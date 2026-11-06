import json
from decimal import Decimal
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from core.common import BusinessError, digest
from core.models import ApiKey, Company, User, Customer, Document
from core.services import documents, files
from core.security import throttle


def numbers(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [numbers(v) for v in value]
    return value


@csrf_exempt
@transaction.atomic
def api(request, resource, document_uid=None, action=None):
    try:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise BusinessError("Clé API requise.", 401)
        key = ApiKey.objects.filter(key_hash=digest(auth[7:]), status="active").first()
        company = (
            Company.objects.filter(company_uid=key.company_uid, status="active").first()
            if key
            else None
        )
        if not company:
            raise BusinessError("Clé API invalide.", 401)
        user = User.objects.get(user_uid=company.owner_user_uid)
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
                types = (
                    ["quote"]
                    if resource == "quotes"
                    else ["invoice", "progress_invoice"]
                )
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
            types = (
                ["quote"] if resource == "quotes" else ["invoice", "progress_invoice"]
            )
            document = Document.objects.filter(
                company_uid=company.company_uid,
                document_uid=document_uid,
                type__in=types,
                direction="outgoing",
            ).first()
            if not document:
                raise BusinessError("Document introuvable.", 404)
            if action == "accept":
                document = documents.change_status(document, "accept")
            elif action == "invoice":
                document = documents.bill_quote(company, user, document)
            elif action == "progress-invoices":
                document = documents.bill_quote(
                    company,
                    user,
                    document,
                    data.get("percent", data.get("progress_percent")),
                )
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
