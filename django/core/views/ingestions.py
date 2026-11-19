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
from core.services import expense_workspace
from core.services.expense_classification import CATEGORIES
from core.services.document_preview import preview_format, read_page
from .preview import preview_response
from core.services.receipt_extraction import (
    EXPENSE_KINDS,
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

def analysis_source(item):
    provider = (item.extraction_model or "").split(":", 1)[0]
    if provider == "local":
        return "OCR local"
    return extraction_ai.PROVIDERS[provider][0] if provider in extraction_ai.PROVIDERS else ""


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
    row.analysis_source = analysis_source(row)


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
    if not detail and request.GET.get("etats") != "1":
        missing_rows = set(request.GET.get("ajouter", "").split(","))
        refresh = [key for key, state in states.items() if not state["busy"] or key in missing_rows]
        for row in selection.filter(ingestion_uid__in=refresh):
            row.progress = states[row.ingestion_uid]
            if expenses:
                row.facts = expense_workspace.details(row)
                # The progress endpoint returns operational metadata, never raw OCR fragments.
                data = unpack(row.extraction_json)
                party = data.get("supplier") if isinstance(data, dict) else None
                row.facts["merchant"] = row.merchant_name or (party.get("legal_name", "") if isinstance(party, dict) else "")
            else:
                fill_list_row(row, visible, documents)
            row_html = render_to_string("core/expense_row.html" if expenses else "core/ingestion_row.html", {
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
        if expense_workspace.reimbursement_state(item) == "paid" and (
            fields["expense_amount"] != item.expense_amount or fields["expense_currency"] != item.expense_currency
        ):
            raise BusinessError("Corrigez le suivi du remboursement avant de modifier le montant déjà versé.", 409)
        expense_payments.mark_expense_fields(item, fields)
        if "expense_vat" in request.POST:
            raw_vat = request.POST["expense_vat"].strip()
            vat = decimal(raw_vat) if raw_vat else None
            if vat is not None and not 0 <= vat <= fields["expense_amount"]:
                raise BusinessError("La TVA doit être comprise entre zéro et le montant TTC.")
            data = unpack(fields.get("extraction_json", item.extraction_json))
            if not isinstance(data, dict):
                data = {}
            reviewed = invoice_imports.reviewed_fields(data)
            if not isinstance(data.get("totals"), dict):
                data["totals"] = {}
            data["totals"]["total_vat"] = format(vat, "f") if vat is not None else ""
            data["_reviewed"] = sorted(reviewed | {"totals.total_vat"})
            fields["extraction_json"] = json_text(data)
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
    readonly = request.role not in ROLES["write"]
    if readonly and not expenses and not ingestion_uid:
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
    if expenses or readonly:
        query = expense_workspace.receipts_for(request)
        if readonly:
            visible_imports = query
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
    if request.method == "POST" and not item and request.POST.get("action") == "analyser-lot":
        if not batch or archived:
            raise BusinessError("Choisissez un lot d’imports actifs.")
        count = jobs.enqueue_batch_ai(selection, request.company.company_uid, request.POST.get("provider", ""))
        if count:
            messages.success(request, f"Analyse demandée pour {count} document{'s' if count > 1 else ''}.")
        else:
            messages.info(request, "Toutes les pièces de ce lot sont déjà analysées ou en cours de traitement.")
        return redirect(f"{request.path}?lot={batch}")
    if action == "remboursement":
        if request.method != "POST":
            return HttpResponse(status=405, headers={"Allow": "POST"})
        expense_workspace.record_reimbursement(request, item)
        audit(request, "expense.reimbursement", item.ingestion_uid)
        messages.success(request, "Suivi du remboursement enregistré.")
        return redirect(f"/app/imports/{item.ingestion_uid}#remboursement")
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
        elif action == "relancer":
            if item.source_kind not in EXPENSE_KINDS:
                raise BusinessError("Choisissez une note de frais.")
            choices = extraction_ai.choices(request.company.company_uid)
            provider = choices[0][0] if choices else "local"
            if item.transcription_text and provider != "local":
                jobs.enqueue(item, "analyze", provider)
            else:
                jobs.enqueue(item, "pipeline", provider)
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
        is_expense = base_context["is_expense"]
        template = "core/expense.html" if is_expense else "core/ingestion.html"
        if is_expense:
            request.navigation_route = "/app/notes-de-frais"
            facts = expense_workspace.details(item)
            base_context.update(
                title=facts["merchant"] or "Note de frais", facts=facts, categories=CATEGORIES,
                expense={key: facts[key] for key in ("merchant", "amount", "date", "currency")},
                reimbursement_states=expense_workspace.STATES, today=timezone.localdate(),
                reader_url=f"/app/imports/{item.ingestion_uid}/apercu",
            )
            if base_context["can_preview"]:
                try:
                    base_context["page_count"], _ = read_page(item, 1)
                except BusinessError as exc:
                    base_context["preview_error"] = str(exc)
        if item.progress["busy"]:
            return render(request, template, base_context)
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
        duplicate_documents, duplicate_imports = invoice_imports.inspect_duplicates(
            item, query, documents, parsed
        )
        method = suggested_payment_method(item.transcription_text, parsed)
        return render(
            request,
            template,
            {
                **base_context,
                "parsed": parsed,
                "analysis_source": analysis_source(item),
                "lines": extracted_lines,
                "inventory": [],
                **expense_payments.context(item, purchase=item.source_kind != "sales_invoice"),
                "edit_payment": request.role in ROLES["write"] and (not item.document_uid or is_expense or request.role in ROLES["finance"]),
                "payment_action": f"/app/imports/{item.ingestion_uid}/reglement",
                "payment_suggestion": method.label if method else "",
                "is_sale": item.source_kind == "sales_invoice",
                "duplicate_documents": duplicate_documents,
                "duplicate_imports": duplicate_imports,
                "suggested_method": facts["method"] if is_expense else method,
                "suggested_payer": facts["payer"] if is_expense else "",
                "suggested_purpose": facts["purpose"] if is_expense else "",
                "ai_choices": extraction_ai.choices(request.company.company_uid),
            },
        )
    if expenses:
        list_context = expense_workspace.workspace(selection, request.GET)
        if request.headers.get("X-Expense-Fragment") == "results":
            response = render(request, "core/expense_results.html", list_context)
            response["Cache-Control"] = "private, no-store"
            return response
    else:
        rows = list(selection.order_by("-id")[:200])
        jobs.set_progress(rows, request.company.company_uid)
        for row in rows:
            fill_list_row(row, query, documents)
        list_context = {"items": rows}
    choices = extraction_ai.choices(request.company.company_uid)
    batch_count = 0
    if batch and not archived and choices:
        batch_count = min(50, jobs.awaiting_batch_ai(selection, request.company.company_uid).count())
    return render(
        request,
        "core/expenses.html" if expenses else "core/ingestions.html",
        {
            "title": "Notes de frais" if expenses else "Imports",
            **list_context,
            "latest_batch": selection.order_by("-id").values_list("batch_uid", flat=True).first() if expenses else "",
            "expenses": expenses,
            "archives": archived,
            **expense_payments.context(None),
            "batch": batch,
            "batch_count": batch_count,
            "ai_choices": choices,
        },
    )
