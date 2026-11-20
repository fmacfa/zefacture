import json
from decimal import Decimal
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.utils.http import content_disposition_header
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from django.db.models import Q
from core.common import BusinessError, digest
from core.models import ApiKey, Company, User, Customer, Document, InvoiceApiCreation
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


def create_invoice(company, user, data, key, *, quote=None):
    """Request ledger and draft commit together under the company numbering lock."""
    reference = str(data.get("reference") or "").strip()
    if not key and not reference:
        raise BusinessError("Une référence de vente ou l’en-tête Idempotency-Key est requis pour créer une facture.", 400)
    if key is not None and (not key.strip() or len(key) > 200 or not key.isascii() or not key.isprintable()):
        raise BusinessError("La clé d’idempotence doit contenir entre 1 et 200 caractères ASCII imprimables.", 400)
    company = Company.objects.select_for_update().get(pk=company.pk)
    key_hash = digest(key) if key else None
    ref_hash = digest(reference) if reference else None
    payload = [quote.document_uid if quote else None, data]
    fingerprint = digest(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    match = Q()
    if key_hash:
        match |= Q(request_key=key_hash)
    if ref_hash:
        match |= Q(reference_hash=ref_hash)
    records = list(InvoiceApiCreation.objects.select_related("document").filter(
        match, company_uid=company.company_uid,
    )[:2])
    if records:
        prior = records[0]
        if len(records) != 1 or prior.payload_hash != fingerprint or (
            key_hash and prior.request_key and prior.request_key != key_hash
        ):
            raise BusinessError("Cette clé ou référence de vente correspond à une autre demande.", 409)
        if key_hash and not prior.request_key:
            prior.request_key = key_hash
            prior.save(update_fields=["request_key"])
        return prior.document, False
    if not quote and reference and Document.objects.filter(
        company_uid=company.company_uid, type__in=documents.INVOICE_TYPES,
        direction="outgoing", reference=reference,
    ).exists():
        raise BusinessError("Une facture existante porte déjà cette référence. Retrouvez-la avant de poursuivre.", 409)
    if quote:
        percent = data.get("percent", data.get("progress_percent"))
        if percent is None:
            raise BusinessError("Le pourcentage d’avancement est requis.")
        document = documents.bill_quote(company, user, quote, percent)
    else:
        document = documents.save_document(company, user, "invoice", data)
        if data.get("generate_facturx"):
            files.generate_document(document)
    InvoiceApiCreation.objects.create(
        company_uid=company.company_uid, document=document,
        request_key=key_hash, reference_hash=ref_hash, payload_hash=fingerprint,
    )
    return document, True


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
                if action == "progress-invoices":
                    document, created = create_invoice(
                        company, user, data, request.headers.get("Idempotency-Key"), quote=document,
                    )
                    return JsonResponse({"data": numbers(documents.payload(document))}, status=201 if created else 200)
                document = documents.bill_quote(company, user, document)
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
            if resource == "invoices":
                document, created = create_invoice(company, user, data, request.headers.get("Idempotency-Key"))
                return JsonResponse({"data": numbers(documents.payload(document))}, status=201 if created else 200)
            document = documents.save_document(company, user, "quote", data)
        return JsonResponse(
            {"data": numbers(documents.payload(document))},
            status=200 if action in {"accept", "facturx"} else 201,
        )
    except BusinessError as exc:
        transaction.set_rollback(True)
        return JsonResponse({"error": str(exc)}, status=exc.status)
