import json
from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.contrib import messages
from core.common import BusinessError, stamp, today, iso_date, decimal, unpack
from core.models import Ingestion
from core.security import scoped, ROLES
from core.services import ingestion, files


@scoped("write")
def ingestions(request, ingestion_uid=None, action=None, expenses=False):
    if not expenses and request.role not in ROLES["write"]:
        raise BusinessError("Votre rôle ne permet pas d'accéder aux imports.", 403)
    query = Ingestion.objects.filter(company_uid=request.company.company_uid)
    if request.role not in ROLES["finance"]:
        from django.db.models import Q

        query = query.filter(
            Q(uploaded_by_user_uid=request.user.user_uid)
            | ~Q(
                source_kind__in=[
                    "expense_note",
                    "expense",
                    "expense_receipt",
                    "receipt",
                ]
            )
        )
    if expenses:
        query = query.filter(
            source_kind__in=["expense_note", "expense", "expense_receipt", "receipt"]
        )
    item = query.filter(ingestion_uid=ingestion_uid).first() if ingestion_uid else None
    if ingestion_uid and not item:
        raise BusinessError("Import introuvable.", 404)
    if action == "telecharger" and request.method == "GET":
        response = HttpResponse(
            files.read_verified(item),
            content_type=item.mime_type or "application/octet-stream",
        )
        response["Content-Disposition"] = (
            'attachment; filename="'
            + item.ingestion_uid
            + files.download_extension(item.mime_type)
            + '"'
        )
        response["Cache-Control"] = "private, no-store"
        return response
    if request.method == "POST":
        if not item:
            kind = request.POST.get(
                "source_kind", "expense_note" if expenses else "supplier_invoice"
            )
            if kind not in {
                "supplier_invoice",
                "expense_note",
                "expense",
                "expense_receipt",
                "receipt",
            }:
                raise BusinessError("Type d'import invalide.")
            uploaded = request.FILES.get("file") or request.FILES.get("source_file")
            metadata = files.upload(uploaded, "uploads/" + request.company.company_uid)
            item = Ingestion.objects.create(
                company_uid=request.company.company_uid,
                uploaded_by_user_uid=request.user.user_uid,
                original_filename=uploaded.name[:255],
                source_kind=kind,
                **metadata,
                expense_date=iso_date(request.POST.get("expense_date") or today()),
                expense_category=request.POST.get("expense_category"),
                merchant_name=request.POST.get("merchant_name"),
                expense_description=request.POST.get("expense_description"),
                expense_amount=decimal(request.POST.get("expense_amount"))
                if request.POST.get("expense_amount")
                else None,
                expense_currency=request.POST.get("expense_currency") or "EUR",
            )
        else:
            if item.document_uid:
                raise BusinessError("Cet import a déjà été converti.", 409)
            if action in {"transcrire", "analyser"}:
                from core.services.jobs import enqueue

                enqueue(item, "transcribe" if action == "transcrire" else "analyze")
                messages.success(
                    request,
                    "Traitement ajouté à la file. Actualisez cette page pour consulter le résultat.",
                )
            elif action in {"valider-facture", "creer-facture"}:
                if request.role not in ROLES["finance"]:
                    raise BusinessError(
                        "La validation des dépenses et factures requiert un responsable ou comptable.",
                        403,
                    )
                try:
                    data = json.loads(
                        request.POST.get("extraction_json")
                        or item.extraction_json
                        or "{}"
                    )
                except ValueError:
                    raise BusinessError("Extraction JSON invalide.")
                document = ingestion.convert(request.company, request.user, item, data)
                return redirect(document.url)
            elif action == "enregistrer":
                item.transcription_text = request.POST.get("transcription_text", "")[
                    :500000
                ]
                raw = request.POST.get("extraction_json", "")
                if raw:
                    try:
                        data = json.loads(raw)
                        if not isinstance(data, dict):
                            raise ValueError()
                    except ValueError:
                        raise BusinessError("Extraction JSON invalide.")
                    item.extraction_json = raw
                    item.extraction_status = "done"
                if item.transcription_text:
                    item.transcription_status = "done"
                item.updated_at = stamp()
                item.save()
        return redirect("/app/imports/" + item.ingestion_uid)
    if item:
        example = {
            "supplier": {"legal_name": "", "country_code": "FR"},
            "invoice": {
                "number": "",
                "issue_date": today(),
                "due_date": today(),
                "currency_code": "EUR",
            },
            "lines": [
                {
                    "description": "",
                    "quantity": 1,
                    "unit_price_excluding_tax": 0,
                    "vat_rate": 20,
                }
            ],
        }
        parsed = unpack(item.extraction_json) or example
        extracted_lines = (
            parsed.get("lines")
            or parsed.get("line_items")
            or parsed.get("items")
            or example["lines"]
        )
        return render(
            request,
            "core/ingestion.html",
            {
                "title": item.original_filename,
                "item": item,
                "parsed": parsed,
                "lines": extracted_lines,
                "inventory": [],
            },
        )
    return render(
        request,
        "core/ingestions.html",
        {
            "title": "Notes de frais" if expenses else "Imports",
            "items": query.order_by("-id")[:200],
            "expenses": expenses,
            "today": today(),
        },
    )
