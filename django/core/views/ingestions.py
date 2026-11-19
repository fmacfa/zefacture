import json
import re
from django.db import transaction
from django.shortcuts import render, redirect
from django.http import HttpResponse, JsonResponse
from django.contrib import messages
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from core.common import BusinessError, decimal, stamp, unpack, uid
from core.models import Company, Document, Ingestion
from core.security import hidden_expense_documents, scoped, ROLES
from core.services import extraction_ai, ingestion, files, jobs, invoice_imports
from core.services.account_security import audit
from core.services.document_preview import preview_format
from .preview import preview_response
from core.services.receipt_extraction import (
    EXPENSE_KINDS,
    expense_suggestion,
    expense_values,
)

SOURCE_LABELS = {
    "supplier_invoice": "Facture fournisseur",
    "sales_invoice": "Facture de vente",
    "expense_note": "Note de frais",
    "expense": "Note de frais",
    "expense_receipt": "Ticket de caisse",
    "receipt": "Ticket de caisse",
}
STATUS_LABELS = {
    "uploaded": "Importé",
    "transcribed": "Transcrit",
    "analyzed": "Analysé",
    "converted": "Facture créée",
    "failed": "Traitement à vérifier",
}


@transaction.atomic
def review(request, item, action, query, documents):
    Company.objects.select_for_update().get(pk=request.company.pk)
    item = Ingestion.objects.select_for_update().get(pk=item.pk)
    if item.archived_at:
        raise BusinessError("Restaurez cet import pour le modifier.", 409)
    if item.document_uid:
        raise BusinessError("Cet import a déjà été converti.", 409)
    jobs.require_idle(item)
    if action == "enregistrer-justificatif":
        if item.source_kind not in EXPENSE_KINDS:
            raise BusinessError("Cet import n’est pas un justificatif de frais.")
        fields = expense_values(request.POST, require_complete=True)
        for name, value in fields.items():
            setattr(item, name, value)
        item.updated_at = stamp()
        item.save(update_fields=[*fields, "updated_at"])
        messages.success(
            request, "Les informations du justificatif ont été enregistrées."
        )
        return
    if action == "enregistrer":
        item.transcription_text = request.POST.get("transcription_text", "").replace(
            "\0", ""
        )[:500000]
        fields = ["transcription_text", "updated_at"]
        raw = request.POST.get("extraction_json", "")
        if raw:
            try:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError()
            except (ValueError, RecursionError):
                raise BusinessError("Extraction JSON invalide.")
            invoice_imports.index_extraction(item, data)
            fields.extend(["source_invoice_ref", "source_invoice_match"])
            item.extraction_json = raw
            item.extraction_status = "done"
            fields += ["extraction_json", "extraction_status"]
        if item.transcription_text:
            item.transcription_status = "done"
            fields.append("transcription_status")
        item.updated_at = stamp()
        item.save(update_fields=fields)
        return
    if request.role not in ROLES["finance"]:
        raise BusinessError(
            "La validation des dépenses et factures requiert un responsable ou comptable.",
            403,
        )
    try:
        data = json.loads(
            request.POST.get("extraction_json") or item.extraction_json or "{}"
        )
    except (ValueError, RecursionError):
        raise BusinessError("Extraction JSON invalide.")
    if not isinstance(data, dict):
        raise BusinessError("Extraction JSON invalide.")
    matches, others = invoice_imports.inspect_duplicates(item, query, documents, data)
    if (matches or others) and request.POST.get("distinct") != "1":
        raise BusinessError(
            "Doublon possible. Enregistrez vos corrections, comparez les pièces puis confirmez si les factures sont distinctes.",
            409,
        )
    return ingestion.convert(request.company, request.user, item, data)


@require_http_methods(["GET", "HEAD", "POST"])
@scoped("write")
def ingestions(request, ingestion_uid=None, action=None, expenses=False):
    if not expenses and request.role not in ROLES["write"]:
        raise BusinessError("Votre rôle ne permet pas d'accéder aux imports.", 403)
    query = Ingestion.objects.filter(company_uid=request.company.company_uid)
    if request.role not in ROLES["finance"]:
        from django.db.models import Q

        query = query.filter(
            Q(uploaded_by_user_uid=request.user.user_uid)
            | ~Q(source_kind__in=EXPENSE_KINDS)
        )
    documents = Document.objects.filter(
        company_uid=request.company.company_uid
    ).exclude(document_uid__in=hidden_expense_documents(request))
    if expenses:
        query = query.filter(source_kind__in=EXPENSE_KINDS)
    item = query.filter(ingestion_uid=ingestion_uid).first() if ingestion_uid else None
    if ingestion_uid and not item:
        raise BusinessError("Import introuvable.", 404)
    if action == "apercu":
        if request.method != "GET":
            return HttpResponse(status=405, headers={"Allow": "GET"})
        base = f"/app/imports/{ingestion_uid}"
        document = None
        if item.document_uid:
            document = (
                Document.objects.filter(
                    company_uid=request.company.company_uid,
                    document_uid=item.document_uid,
                )
                .exclude(document_uid__in=hidden_expense_documents(request))
                .first()
            )
        return preview_response(
            request,
            item,
            title=item.original_filename or "Justificatif",
            back_url=base,
            download_url=base + "/telecharger",
            document=document,
            expense=item,
        )
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
    if action in {"archiver", "restaurer"}:
        if request.method != "POST":
            return HttpResponse(status=405, headers={"Allow": "POST"})
        with transaction.atomic():
            item = Ingestion.objects.select_for_update().get(pk=item.pk)
            jobs.require_idle(item)
            archived = action == "archiver"
            if bool(item.archived_at) != archived:
                item.archived_at = timezone.now() if archived else None
                item.updated_at = stamp()
                item.save(update_fields=["archived_at", "updated_at"])
                audit(
                    request,
                    "ingestion.archived" if archived else "ingestion.restored",
                    item.ingestion_uid,
                )
        if archived:
            messages.success(
                request, "Le justificatif est disponible dans les archives."
            )
            return redirect(
                "/app/notes-de-frais"
                if item.source_kind in EXPENSE_KINDS
                else "/app/imports"
            )
        messages.success(request, "Justificatif restauré.")
        return redirect("/app/imports/" + item.ingestion_uid)
    if request.method == "POST":
        if not item:
            kind = request.POST.get(
                "source_kind", "expense_note" if expenses else "supplier_invoice"
            )
            if kind not in EXPENSE_KINDS | {"supplier_invoice", "sales_invoice"}:
                raise BusinessError("Type d'import invalide.")
            if expenses and kind not in EXPENSE_KINDS:
                raise BusinessError("Choisissez un type de justificatif de frais.")
            details = expense_values(request.POST)
            uploads = request.FILES.getlist("file") or request.FILES.getlist(
                "source_file"
            )
            if not 1 <= len(uploads) <= 50:
                raise BusinessError("Sélectionnez de 1 à 50 fichiers.")
            batch = request.POST.get("batch_uid") or uid()
            if not re.fullmatch("[a-f0-9]{32}", batch):
                raise BusinessError("Lot invalide.")
            pipeline = request.POST.get("pipeline", "none")
            if pipeline not in {"local", "none"}:
                extraction_ai.require_provider(pipeline)
            results = []
            for uploaded in uploads:
                try:
                    result = invoice_imports.upload_one(
                        request.company,
                        request.user,
                        query,
                        documents,
                        uploaded,
                        kind,
                        batch,
                        details,
                        pipeline,
                    )
                except BusinessError as exc:
                    if len(uploads) == 1:
                        raise
                    result = {"state": "failed", "label": str(exc), "url": ""}
                results.append({"filename": uploaded.name[:255], **result})
            if request.headers.get("Accept") == "application/json":
                return JsonResponse({"files": results, "batch": batch})
            if len(results) == 1:
                if results[0]["state"] == "duplicate":
                    messages.warning(request, results[0]["label"])
                return redirect(results[0]["url"])
            return render(
                request,
                "core/import_results.html",
                {"title": "Résultat du lot", "results": results, "batch": batch},
            )
        elif action in {"transcrire", "analyser"}:
            jobs.enqueue(
                item,
                "transcribe" if action == "transcrire" else "analyze",
                request.POST.get("provider", "") if action == "analyser" else "",
            )
            messages.success(
                request,
                "Traitement ajouté à la file. Actualisez cette page pour consulter le résultat.",
            )
        elif action in {
            "valider-facture",
            "creer-facture",
            "enregistrer",
            "enregistrer-justificatif",
        }:
            document = review(request, item, action, query, documents)
            if document:
                return redirect(document.url)
        return redirect("/app/imports/" + item.ingestion_uid)
    if item:
        parsed = unpack(item.extraction_json)
        if not isinstance(parsed, dict) or not parsed:
            parsed = invoice_imports.local_invoice(item)
        extracted_lines = (
            parsed.get("lines")
            or parsed.get("line_items")
            or parsed.get("items")
            or [{"description": "", "quantity": 1, "vat_rate": 20}]
        )
        if not isinstance(extracted_lines, list) or not all(
            isinstance(line, dict) for line in extracted_lines
        ):
            extracted_lines = [{"description": "", "quantity": 1, "vat_rate": 20}]
        is_expense = item.source_kind in EXPENSE_KINDS
        expense = {}
        suggested = {}
        if is_expense:
            suggested = expense_suggestion(item)
            expense = {
                "merchant": item.merchant_name or suggested["merchant"],
                "date": item.expense_date or suggested["date"],
                "currency": item.expense_currency or suggested["currency"],
                "amount": (
                    format(item.expense_amount.normalize(), "f")
                    if item.expense_amount is not None
                    else suggested["amount"]
                ),
            }
        duplicate_documents, duplicate_imports = invoice_imports.inspect_duplicates(
            item, query, documents, parsed
        )
        return render(
            request,
            "core/ingestion.html",
            {
                "title": item.original_filename,
                "item": item,
                "can_preview": preview_format(item),
                "parsed": parsed,
                "lines": extracted_lines,
                "inventory": [],
                "is_expense": is_expense,
                "is_sale": item.source_kind == "sales_invoice",
                "duplicate_documents": duplicate_documents,
                "duplicate_imports": duplicate_imports,
                "expense": expense,
                "suggested": suggested,
                "ai_choices": extraction_ai.choices(),
                "source_label": SOURCE_LABELS.get(item.source_kind, "Justificatif"),
                "state_label": STATUS_LABELS.get(item.status, "À vérifier"),
            },
        )
    archived = request.GET.get("archives") == "1"
    batch = request.GET.get("lot", "")
    selection = query.filter(archived_at__isnull=not archived)
    if batch:
        if not re.fullmatch("[a-f0-9]{32}", batch):
            raise BusinessError("Lot invalide.")
        selection = selection.filter(batch_uid=batch)
    rows = list(selection.order_by("-id")[:200])
    for row in rows:
        extracted = unpack(row.extraction_json)
        if not isinstance(extracted, dict) or not extracted:
            extracted = (
                invoice_imports.local_invoice(row) if row.transcription_text else {}
            )
        row.duplicate = (
            any(invoice_imports.inspect_duplicates(row, query, documents, extracted))
            if extracted
            else False
        )
        invoice = extracted.get("invoice")
        totals = extracted.get("totals")
        row.list_date, row.list_amount, row.list_currency = (
            row.expense_date,
            row.expense_amount,
            row.expense_currency,
        )
        if isinstance(invoice, dict):
            row.list_date = row.list_date or invoice.get("issue_date")
            if row.source_kind not in EXPENSE_KINDS or not row.list_currency:
                row.list_currency = invoice.get("currency_code")
        if (
            row.list_amount is None
            and isinstance(totals, dict)
            and totals.get("total_including_tax") not in (None, "")
        ):
            try:
                row.list_amount = decimal(totals["total_including_tax"])
            except BusinessError:
                pass
        row.source_label = SOURCE_LABELS.get(row.source_kind, "Justificatif")
        row.state_label = STATUS_LABELS.get(row.status, "À vérifier")
        row.can_preview = preview_format(row)
    return render(
        request,
        "core/ingestions.html",
        {
            "title": "Notes de frais" if expenses else "Imports",
            "items": rows,
            "expenses": expenses,
            "archives": archived,
            "batch": batch,
            "ai_choices": extraction_ai.choices(),
        },
    )
