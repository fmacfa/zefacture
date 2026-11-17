import re

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from core.common import BusinessError, iso_date, money
from core.models import ApiKey, Document, Ingestion
from core.services import reports
from core.services.api_access import api_company
from core.services.receipt_extraction import EXPENSE_KINDS, expense_suggestion


def amount(value):
    return format(money(value), "f") if value is not None else None


def range_from(params):
    start = iso_date(params.get("date_from"), optional=True)
    end = iso_date(params.get("date_to"), optional=True)
    if start and end and start > end:
        raise BusinessError("La date de début doit précéder la date de fin.", 400)
    return start, end


def filter_dates(query, field, params):
    start, end = range_from(params)
    if start:
        query = query.filter(**{field + "__gte": start})
    if end:
        query = query.filter(**{field + "__lte": end})
    return query


def page(query, uid_field, extract, params):
    try:
        limit = int(params.get("limit", 20))
        if not 1 <= limit <= 100:
            raise ValueError()
    except ValueError as exc:
        raise BusinessError(
            "La limite doit être un entier entre 1 et 100.", 400
        ) from exc
    cursor = params.get("cursor")
    if cursor:
        pivot = None
        if re.fullmatch(r"[a-f0-9]{32}", cursor):
            pivot = (
                query.filter(**{uid_field: cursor}).values_list("id", flat=True).first()
            )
        if pivot is None:
            raise BusinessError("Curseur inconnu pour ces filtres.", 400)
        query = query.filter(id__lt=pivot)
    rows = list(query.order_by("-id")[: limit + 1])
    return {
        "data": [extract(row) for row in rows[:limit]],
        "next_cursor": getattr(rows[limit - 1], uid_field)
        if len(rows) > limit
        else None,
    }


def billing_summary(company, params):
    range_from(params)
    period = reports.period(
        {
            "date_from": params.get("date_from"),
            "date_to": params.get("date_to"),
            "compare": params.get("compare", "none"),
        }
    )
    docs = Document.objects.filter(company_uid=company.company_uid)

    def totals(start, end):
        result = reports.billing_totals(reports.between(docs, "issue_date", start, end))
        return [
            {
                "currency": currency,
                "sales_excluding_tax": amount(values["sales"]),
                "purchases_excluding_tax": amount(values["purchases"]),
            }
            for currency, values in sorted(result.items())
        ]

    data = {
        "date_from": period.start.isoformat(),
        "date_to": period.end.isoformat(),
        "totals": totals(period.start, period.end),
    }
    if period.compare_start:
        data["comparison"] = {
            "date_from": period.compare_start.isoformat(),
            "date_to": period.compare_end.isoformat(),
            "totals": totals(period.compare_start, period.compare_end),
        }
    return {"data": data}


def invoice_data(doc):
    partner = doc.seller if doc.direction == "incoming" else doc.buyer
    return {
        "id": doc.document_uid,
        "number": doc.number,
        "direction": doc.direction,
        "type": doc.type,
        "status": doc.status,
        "issue_date": doc.issue_date,
        "due_date": doc.due_date,
        "currency": doc.currency_code,
        "partner": partner.get("name", "")[:255]
        if isinstance(partner, dict) and isinstance(partner.get("name", ""), str)
        else "",
        "total_excluding_tax": amount(doc.total_excluding_tax),
        "total_including_tax": amount(doc.total_including_tax),
    }


def invoice_page(company, params):
    docs = Document.objects.filter(
        company_uid=company.company_uid,
        type__in=["invoice", "progress_invoice", "credit_note"],
    )
    direction = params.get("direction")
    if direction:
        if direction not in {"incoming", "outgoing"}:
            raise BusinessError("Direction de facture invalide.", 400)
        docs = docs.filter(direction=direction)
    docs = filter_dates(docs, "issue_date", params)
    return page(docs, "document_uid", invoice_data, params)


def receipt_data(item):
    return {
        "id": item.ingestion_uid,
        "type": item.source_kind,
        "merchant": item.merchant_name,
        "date": item.expense_date,
        "category": item.expense_category,
        "amount_including_tax": amount(item.expense_amount),
        "currency": item.expense_currency or None,
        "transcription_status": item.transcription_status,
        "extraction_status": item.extraction_status,
        "expense_status": item.expense_status,
    }


def receipt_result(company, params, receipt_uid):
    receipts = Ingestion.objects.filter(
        company_uid=company.company_uid, source_kind__in=EXPENSE_KINDS
    )
    if receipt_uid:
        receipt = receipts.filter(ingestion_uid=receipt_uid).first()
        if not receipt:
            raise BusinessError("Justificatif introuvable.", 404)
        data = receipt_data(receipt)
        if receipt.transcription_text:
            data["suggestions_to_review"] = expense_suggestion(receipt)
        return {"data": data}
    return page(
        filter_dates(receipts, "expense_date", params),
        "ingestion_uid",
        receipt_data,
        params,
    )


@csrf_exempt
def assistant_api(request, resource, receipt_uid=None):
    try:
        company = api_company(request, ApiKey.Scope.ASSISTANT)
        if request.method != "GET":
            raise BusinessError("Méthode non autorisée.", 405)
        if resource == "summary":
            result = billing_summary(company, request.GET)
        elif resource == "invoices":
            result = invoice_page(company, request.GET)
        else:
            result = receipt_result(company, request.GET, receipt_uid)
        response = JsonResponse(result)
    except BusinessError as exc:
        response = JsonResponse({"error": str(exc)}, status=exc.status)
        if exc.status == 401:
            response["WWW-Authenticate"] = "Bearer"
        if exc.status == 405:
            response["Allow"] = "GET"
    response["Cache-Control"] = "private, no-store"
    return response
