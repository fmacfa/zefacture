from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.db.models import OuterRef, Q, Subquery, Sum
from core.common import BusinessError, today
from core.models import SaleReceipt


def workspace(query, params, start, end, default_currency):
    day = date.fromisoformat(today())
    if start and end and start > end:
        raise BusinessError("La fin de la période précède son début.")
    focus = params.get("suivi", "")
    if focus not in ("", "outstanding", "overdue", "received"):
        raise BusinessError("Vue de suivi des ventes inconnue.")
    currency = params.get("devise", "")
    if currency:
        query = query.filter(currency_code=currency)
    issued = query.filter(status__in=["sent", "paid", "accepted"])
    open_invoices = issued.filter(amount_due__gt=0, issue_date__lte=day.isoformat())
    overdue = Q(due_date__lt=day.isoformat(), due_date__gt="")
    payment_start, payment_end = start, end
    if not start and not end:
        previous_day = min(day.day, monthrange(day.year - 1, day.month)[1])
        payment_start = (
            day.replace(year=day.year - 1, day=previous_day) + timedelta(days=1)
        ).isoformat()
        payment_end = day.isoformat()
    receipts = SaleReceipt.objects.filter(
        document__in=issued, cancelled=False, received_on__isnull=False
    )
    if payment_start:
        receipts = receipts.filter(received_on__gte=payment_start)
    if payment_end:
        receipts = receipts.filter(received_on__lte=payment_end)
    amounts = {
        row["currency_code"]: row
        for row in open_invoices.values("currency_code").annotate(
            outstanding=Sum("amount_due"), overdue=Sum("amount_due", filter=overdue)
        )
    }
    for row in receipts.values("document__currency_code").annotate(
        collected=Sum("amount")
    ):
        amounts.setdefault(row["document__currency_code"], {})["received"] = row[
            "collected"
        ]
    currencies = sorted(
        set(query.order_by().values_list("currency_code", flat=True).distinct())
        | set(amounts)
    )
    keep = {key: params[key] for key in ("q", "from", "to") if params.get(key)}
    groups = []
    for code in currencies or [currency or default_currency]:
        cards = []
        for key, label in (
            ("outstanding", "À encaisser aujourd’hui"),
            ("overdue", "Dont en retard"),
            ("received", "Encaissé sur la période"),
        ):
            cards.append(
                {
                    "label": label,
                    "key": key,
                    "amount": amounts.get(code, {}).get(key) or Decimal(0),
                    "active": focus == key,
                    "url": "?" + urlencode({**keep, "devise": code, "suivi": key}),
                }
            )
        groups.append({"currency": code, "cards": cards})
    if focus == "outstanding":
        visible = open_invoices
    elif focus == "overdue":
        visible = open_invoices.filter(overdue)
    elif focus == "received":
        by_document = (
            receipts.filter(document_id=OuterRef("pk"))
            .values("document_id")
            .annotate(total=Sum("amount"))
        )
        visible = query.filter(pk__in=receipts.values("document_id")).annotate(
            period_collected=Subquery(by_document.values("total"))
        )
    else:
        visible = query
        if start:
            visible = visible.filter(issue_date__gte=start)
        if end:
            visible = visible.filter(issue_date__lte=end)
    return visible, {
        "sales_groups": groups,
        "sales_focus": focus,
        "sales_reset": "?" + urlencode(keep),
        "payment_start": date.fromisoformat(payment_start) if payment_start else None,
        "payment_end": date.fromisoformat(payment_end) if payment_end else None,
        "today": day.isoformat(),
    }
