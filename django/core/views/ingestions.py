import json
import re
from django.db import transaction
from django.shortcuts import render, redirect
from django.http import HttpResponse, JsonResponse
from django.contrib import messages
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from core.common import BusinessError, decimal, json_text, stamp, unpack, uid
from core.models import Company, Document, Ingestion
from core.security import hidden_expense_documents, scoped, ROLES
from core.services import extraction_ai, ingestion, files, jobs, invoice_imports, expense_payments
from core.services.account_security import audit
from core.services.document_preview import preview_format
from .preview import preview_response
from core.services.receipt_extraction import (
    EXPENSE_KINDS,
    expense_suggestion,
    expense_values,
    suggested_payment_method,
)

SOURCE_LABELS = {
    "supplier_invoice": "Facture fournisseur",
    "sales_invoice": "Facture de vente",
    "expense_note": "Note de frais",
    "expense": "Note de frais",
    "expense_receipt": "Ticket de caisse",
    "receipt": "Ticket de caisse",
}

def fill_list_row(row, query, documents):
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
    row.can_preview = preview_format(row)


def progress_response(request, selection, visible, documents, expenses, archived, detail):
    identifiers = request.GET["suivi"].split(",")
    if len(identifiers) > 200 or not all(re.fullmatch("[a-f0-9]{32}", i) for i in identifiers):
        raise BusinessError("Sélection de fichiers invalide.")
    if detail:
        selection = selection.filter(pk=detail.pk)
    items = list(selection.filter(ingestion_uid__in=identifiers).only(
        "ingestion_uid", "archived_at", "document_uid", "status", "transcription_status", "extraction_status"
    ))
    jobs.set_progress(items, request.company.company_uid)
    states = {i.ingestion_uid: i.progress for i in items}
    if not detail:
        missing_rows = set(request.GET.get("ajouter", "").split(","))
        refresh = [key for key, state in states.items() if not state["busy"] or key in missing_rows]
        for row in selection.filter(ingestion_uid__in=refresh):
            row.progress = states[row.ingestion_uid]
            fill_list_row(row, visible, documents)
            row_html = render_to_string("core/ingestion_row.html", {
                "i": row, "expenses": expenses, "archives": archived,
            }, request=request)
            states[row.ingestion_uid] = {**row.progress, "row": row_html}
    response = JsonResponse({"items": states})
    response["Cache-Control"] = "private, no-store"
    return response


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
            invoice_imports.mark_reviewed(item, data)
            item.extraction_json = json_text(data)
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
    visible_imports = query
    if expenses:
        query = query.filter(source_kind__in=EXPENSE_KINDS)
    following = action is None and request.method == "GET" and "suivi" in request.GET
    lookup = query.only("pk") if following else query
    item = lookup.filter(ingestion_uid=ingestion_uid).first() if ingestion_uid else None
    if ingestion_uid and not item:
        raise BusinessError("Import introuvable.", 404)
    archived = request.GET.get("archives") == "1"
    batch = request.GET.get("lot", "")
    selection = query.filter(archived_at__isnull=not archived)
    if batch:
        if not re.fullmatch("[a-f0-9]{32}", batch):
            raise BusinessError("Lot invalide.")
        selection = selection.filter(batch_uid=batch)
    if following:
        return progress_response(request, query if item else selection, visible_imports, documents, expenses, archived, item)
    if action == "reglement":
        if request.method != "POST":
            return HttpResponse(status=405, headers={"Allow": "POST"})
        if item.document_uid and item.source_kind not in EXPENSE_KINDS and request.role not in ROLES["finance"]:
            raise BusinessError("Le suivi d’une facture validée nécessite un accès financier.", 403)
        expense_payments.save(item, request.POST, purchase=item.source_kind != "sales_invoice")
        messages.success(request, "Suivi du règlement enregistré.")
        return redirect("/app/imports/" + item.ingestion_uid + "#suivi-reglement")
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
            details = expense_values(request.POST) if kind != "sales_invoice" else {}
            uploads = request.FILES.getlist("file") or request.FILES.getlist(
                "source_file"
            )
            if not 1 <= len(uploads) <= 50:
                raise BusinessError("Sélectionnez de 1 à 50 fichiers.")
            batch = request.POST.get("batch_uid") or uid()
            if not re.fullmatch("[a-f0-9]{32}", batch):
                raise BusinessError("Lot invalide.")
            pipeline = request.POST.get("pipeline")
            if pipeline is None:
                providers = extraction_ai.choices(request.company.company_uid)
                pipeline = providers[0][0] if providers else "none"
            if pipeline not in {"local", "none"}:
                extraction_ai.require_provider(pipeline, request.company.company_uid)
            results = []
            for uploaded in uploads:
                try:
                    result = invoice_imports.upload_one(
                        request.company,
                        request.user,
                        visible_imports,
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
                {"title": "Résultat du lot", "results": results, "batch": batch, "expenses": expenses},
            )
        elif action in {"transcrire", "analyser"}:
            jobs.enqueue(
                item,
                "transcribe" if action == "transcrire" else "analyze",
                request.POST.get("provider", "") if action == "analyser" else "",
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
        jobs.set_progress([item], request.company.company_uid)
        base_context = {
            "title": item.original_filename,
            "item": item,
            "can_preview": preview_format(item),
            "is_expense": item.source_kind in EXPENSE_KINDS,
            "source_label": SOURCE_LABELS.get(item.source_kind, "Justificatif"),
        }
        if item.progress["busy"]:
            return render(request, "core/ingestion.html", base_context)
        parsed = invoice_imports.review_data(item)
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
        method = suggested_payment_method(item.transcription_text, parsed)
        return render(
            request,
            "core/ingestion.html",
            {
                **base_context,
                "parsed": parsed,
                "lines": extracted_lines,
                "inventory": [],
                **expense_payments.context(item, purchase=item.source_kind != "sales_invoice"),
                "edit_payment": request.role in ROLES["write"] and (not item.document_uid or is_expense or request.role in ROLES["finance"]),
                "payment_action": f"/app/imports/{item.ingestion_uid}/reglement",
                "payment_suggestion": method.label if method else "",
                "suggested_method": method,
                "is_sale": item.source_kind == "sales_invoice",
                "duplicate_documents": duplicate_documents,
                "duplicate_imports": duplicate_imports,
                "expense": expense,
                "suggested": suggested,
                "ai_choices": extraction_ai.choices(request.company.company_uid),
            },
        )
    rows = list(selection.order_by("-id")[:200])
    jobs.set_progress(rows, request.company.company_uid)
    for row in rows:
        fill_list_row(row, query, documents)
    return render(
        request,
        "core/ingestions.html",
        {
            "title": "Notes de frais" if expenses else "Imports",
            "items": rows,
            "expenses": expenses,
            "archives": archived,
            **expense_payments.context(None),
            "batch": batch,
            "ai_choices": extraction_ai.choices(request.company.company_uid),
        },
    )
