import json
from decimal import Decimal
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.utils.http import content_disposition_header
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from core.common import BusinessError
from core.models import ApiKey, User, Customer, Document
from core.services import documents, files
from core.services.api_access import api_company


def numbers(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [numbers(v) for v in value]
    return value


def document_for_api(company, resource, document_uid):
    types = ("quote",) if resource == "quotes" else documents.INVOICE_TYPES
    document = Document.objects.filter(
        company_uid=company.company_uid,
        document_uid=document_uid,
        type__in=types,
        direction="outgoing",
    ).first()
    if not document:
        raise BusinessError("Document introuvable.", 404)
    return document


def invoice_pdf(request, company, document_uid):
    invoice = document_for_api(company, "invoices", document_uid)
    content = files.issued_invoice_pdf(invoice)
    name = slugify(invoice.number or "") or f"facture-{invoice.document_uid}"
    return HttpResponse(
        content if request.method == "GET" else b"",
        content_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition_header(True, name + ".pdf"),
            "Content-Length": str(len(content)),
        },
    )


@csrf_exempt
@transaction.atomic
def api(request, resource, document_uid=None, action=None):
    try:
        company = api_company(request, ApiKey.Scope.BUSINESS)
        methods = {"issue": ("POST",), "pdf": ("GET", "HEAD")}.get(action)
        if methods and request.method not in methods:
            return JsonResponse(
                {"error": "Méthode non autorisée."},
                status=405,
                headers={"Allow": ", ".join(methods)},
            )
        if action == "pdf":
            return invoice_pdf(request, company, document_uid)
        if action == "issue":
            document = document_for_api(company, "invoices", document_uid)
            invoice = documents.issue_invoice(document)
            result = documents.payload(invoice)
            result["issued_at"] = invoice.sent_at
            result["pdf_url"] = reverse(
                "api_invoice_pdf", kwargs={"document_uid": invoice.document_uid}
            )
            return JsonResponse({"data": numbers(result)})
        if request.method == "GET" and not document_uid:
            if resource == "customers":
                data = list(
                    Customer.objects.filter(company_uid=company.company_uid).values(
                        *(
                            ["customer_uid", "status", "created_at"]
                            + documents.CUSTOMER_FIELDS
                        )
                    )
                )
            else:
                types = ("quote",) if resource == "quotes" else documents.INVOICE_TYPES
                data = [
                    documents.payload(d)
                    for d in Document.objects.filter(
                        company_uid=company.company_uid,
                        direction="outgoing",
                        type__in=types,
                    ).order_by("id")
                ]
            return JsonResponse({"data": numbers(data)})
        if request.method != "POST":
            raise BusinessError("Méthode non autorisée.", 405)
        try:
            data = json.loads(request.body or b"{}")
        except ValueError:
            raise BusinessError("JSON invalide.", 400)
        if not isinstance(data, dict):
            raise BusinessError("Objet JSON requis.", 400)
        if document_uid:
            document = document_for_api(company, resource, document_uid)
            if action == "accept":
                document = documents.change_status(document, "accept")
            elif action in {"invoice", "progress-invoices"}:
                user = User.objects.get(user_uid=company.owner_user_uid)
                percent = (
                    data.get("percent", data.get("progress_percent"))
                    if action == "progress-invoices"
                    else None
                )
                document = documents.bill_quote(company, user, document, percent)
            elif action == "facturx":
                files.generate_document(document)
            else:
                raise BusinessError("Action inconnue.", 404)
        elif resource == "customers":
            customer = documents.save_customer(company, data)
            return JsonResponse(
                {
                    "data": {
                        k: getattr(customer, k)
                        for k in ["customer_uid", "status", "created_at"]
                        + documents.CUSTOMER_FIELDS
                    }
                },
                status=201,
            )
        else:
            user = User.objects.get(user_uid=company.owner_user_uid)
            document = documents.save_document(
                company, user, "quote" if resource == "quotes" else "invoice", data
            )
            if data.get("generate_facturx") and resource == "invoices":
                files.generate_document(document)
        return JsonResponse(
            {"data": numbers(documents.payload(document))},
            status=200 if action in {"accept", "facturx"} else 201,
        )
    except BusinessError as exc:
        transaction.set_rollback(True)
        return JsonResponse({"error": str(exc)}, status=exc.status)
