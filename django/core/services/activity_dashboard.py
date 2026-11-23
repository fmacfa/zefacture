import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from itertools import groupby
from urllib.parse import urlencode

from django.db.models import Sum
from django.db.models.functions import Substr

from core.models import StatementControl
from core.security import ROLES
from . import month_end
from . import reports

MONTHS = (
    "Janv.",
    "Févr.",
    "Mars",
    "Avr.",
    "Mai",
    "Juin",
    "Juil.",
    "Août",
    "Sept.",
    "Oct.",
    "Nov.",
    "Déc.",
)


def transaction_url(start, end, currency, direction=""):
    params = {"from": start, "to": end, "devise": currency, "statut": "booked"}
    if direction:
        params["sens"] = direction
    return "/app/finances/transactions?" + urlencode(params) + "#operations"


def statement_covers(controls, account_uids, start, end, verified):
    """A zero without booked rows requires coverage for each account in the currency."""
    if not account_uids:
        return False
    for uid in account_uids:
        cursor = start
        for control in controls.get(uid, ()):
            if control.end < cursor or control.start > end:
                continue
            if control.start > cursor:
                return False
            if control.pk not in verified:
                verified[control.pk] = month_end.ledger(control)["matches"]
            if not verified[control.pk]:
                return False
            if control.end >= end:
                break
            cursor = control.end + timedelta(days=1)
        else:
            return False
    return True


def bank_chart(company, period, requested, billing_currencies):
    queryset, accounts = reports.transactions_for(company)
    queryset = reports.between(queryset, "transaction_date", period.start, period.end)
    totals = defaultdict(lambda: defaultdict(Decimal))
    for row in (
        queryset.annotate(month=Substr("transaction_date", 1, 7))
        .values("account_uid", "month", "direction")
        .annotate(total=Sum("amount"))
        .order_by()
    ):
        currency = accounts.get(row["account_uid"]) or "?"
        totals[currency][(row["month"], row["direction"])] += row["total"]
    currencies = sorted(
        (set(totals) | set(accounts.values()) | billing_currencies) - {""}
    ) or ["EUR"]
    currency = (
        requested
        if requested in currencies
        else "EUR"
        if "EUR" in currencies
        else currencies[0]
    )
    data = totals[currency]
    months = sorted({m for m, direction in data if len(m) == 7})
    if not months:
        return {"currency": currency, "currencies": currencies}
    start = (
        date.fromisoformat(months[0] + "-01")
        if period.start == date.min
        else period.start
    )
    end = (
        date.fromisoformat(months[-1] + "-01") if period.end == date.max else period.end
    )
    if period.end == date.max:
        end = end.replace(day=calendar.monthrange(end.year, end.month)[1])
    # Extend to the last day only inside the selected range.
    count = (end.year - start.year) * 12 + end.month - start.month + 1
    yearly = count > 18
    cursor = date(start.year, 1 if yearly else start.month, 1)
    step = max(1, (end.year - start.year + 12) // 12) if yearly else 1
    account_uids = [uid for uid, code in accounts.items() if code == currency]
    controls = defaultdict(list)
    for control in (
        StatementControl.objects.filter(
            account__company_uid=company.company_uid,
            account__account_uid__in=account_uids,
            start__lte=end,
            end__gte=start,
        )
        .select_related("account")
        .order_by("start")
    ):
        controls[control.account.account_uid].append(control)
    verified = {}
    ticks = []
    while cursor <= end:
        if yearly:
            last = min(end, date(min(9999, cursor.year + step - 1), 12, 31))
            label = str(cursor.year) if step == 1 else f"{cursor.year}–{last.year}"
        else:
            last = min(
                end,
                cursor.replace(day=calendar.monthrange(cursor.year, cursor.month)[1]),
            )
            label = MONTHS[cursor.month - 1]
        left = max(start, cursor)
        groups = [
            (m, d, v)
            for (m, d), v in data.items()
            if left.strftime("%Y-%m") <= m <= last.strftime("%Y-%m")
        ]
        ticks.append(
            dict(
                label=label,
                gap=not groups
                and not statement_covers(controls, account_uids, left, last, verified),
                full=f"{left:%d/%m/%Y} – {last:%d/%m/%Y}",
                start=left,
                end=last,
                incoming=sum((v for m, d, v in groups if d == "incoming"), Decimal(0)),
                outgoing=sum((v for m, d, v in groups if d == "outgoing"), Decimal(0)),
                url=transaction_url(left, last, currency),
            )
        )
        if last == end:
            break
        cursor = last + timedelta(days=1)
    maximum = max(max(t["incoming"], t["outgoing"]) for t in ticks)
    magnitude = Decimal(10) ** maximum.adjusted() if maximum else Decimal(1)
    scale = next(
        (n * magnitude for n in (1, 2, 3, 4, 5, 6, 8, 10) if n * magnitude >= maximum),
        magnitude,
    )
    for i, tick in enumerate(ticks):
        tick["x"] = (
            round(32 + 536 * i / max(1, len(ticks) - 1), 2) if len(ticks) > 1 else 300
        )
        for direction in ("incoming", "outgoing"):
            tick["y_" + direction] = round(
                154 - 125 * float(tick[direction] / scale), 2
            )
    segments = []
    for gap, group in groupby(ticks, key=lambda tick: tick["gap"]):
        if gap:
            continue
        points = list(group)
        segments.append(
            {
                direction: " ".join(f"{t['x']},{t['y_' + direction]}" for t in points)
                for direction in ("incoming", "outgoing")
            }
            | {"start": points[0]["x"], "end": points[-1]["x"]}
        )
    return {
        "currency": currency,
        "currencies": currencies,
        "ticks": ticks,
        "scale": scale,
        "half": scale / 2,
        "url": transaction_url(start, end, currency),
        "segments": segments,
        "gaps": [t for t in ticks if t["gap"]],
        "yearly": yearly,
    }


def context(request, period, report, expenses):
    finance = request.role in ROLES["finance"]
    requested = request.GET.get("devise")
    billing_currencies = {m["currency"] for m in report["metrics"] if m["currency"]}
    chart = (
        bank_chart(request.company, period, requested, billing_currencies)
        if finance
        else None
    )
    currency = (
        chart["currency"]
        if chart
        else requested
        if requested in billing_currencies
        else next((m["currency"] for m in report["metrics"] if m["value"]), "EUR")
    )
    cards = []
    keys = {"sales", "purchases", "incoming", "outgoing"}
    for metric in report["metrics"]:
        if metric["currency"] != currency or metric["key"] not in keys:
            continue
        key = metric["key"]
        if key in ("incoming", "outgoing") and chart:
            url = transaction_url(period.start, period.end, currency, key)
        else:
            base = "/app/factures" if key == "sales" else "/app/reception"
            url = (
                base
                + "?"
                + urlencode(
                    {
                        "from": period.start,
                        "to": period.end,
                        "devise": currency,
                        "comptabilise": "1",
                    }
                )
            )
        cards.append({**metric, "url": url})
    total = expenses[0]["count"]
    offset = 0
    slices = []
    for card in expenses[1:]:
        share = round(100 * card["count"] / total, 4) if total else 0
        slices.append({**card, "share": share, "offset": -offset})
        offset += share
    return {
        "bank_chart": chart,
        "display_currencies": chart["currencies"]
        if chart
        else sorted(billing_currencies),
        "display_currency": currency,
        "headline_metrics": cards,
        "expense_segments": slices,
        "chart_expense_count": total,
    }
