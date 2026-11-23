import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.db.models import Sum
from django.db.models.functions import Substr

from core.security import ROLES
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


def bank_chart(company, period, requested):
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
    currencies = sorted(set(totals) | set(accounts.values()) - {""}) or ["EUR"]
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
    return {
        "currency": currency,
        "currencies": currencies,
        "ticks": ticks,
        "scale": scale,
        "half": scale / 2,
        "url": transaction_url(start, end, currency),
        "paths": {
            direction: " ".join(f"{t['x']},{t['y_' + direction]}" for t in ticks)
            for direction in ("incoming", "outgoing")
        },
        "yearly": yearly,
    }


def context(request, period, report, expenses):
    finance = request.role in ROLES["finance"]
    chart = (
        bank_chart(request.company, period, request.GET.get("devise"))
        if finance
        else None
    )
    currency = (
        chart["currency"]
        if chart
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
        "headline_metrics": cards,
        "expense_segments": slices,
        "chart_expense_count": total,
    }
