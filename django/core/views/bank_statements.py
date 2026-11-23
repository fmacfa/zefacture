import hashlib
from pathlib import Path

from django.contrib import messages
from django.core import signing
from django.db import transaction
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_http_methods

from core.common import BusinessError, decimal, iso_date, uid
from core.models import BankAccount, BankTransaction, BankStatementImport, SaleReceipt, ExpenseClaim
from core.security import scoped, scoped_get
from core.services import (
    bank_allocations,
    bank_categories,
    bank_statements,
    extraction_ai,
    finance,
    statement_batches,
    statement_pdf,
    month_end,
)

TRANSACTIONS = "/app/finances/transactions"
SIGNING_SALT = "core.bank-statement-review.v1"
MAX_SLOTS = 120


def batch_form(request):
    token = signing.dumps(
        [request.user.user_uid, request.company.company_uid, uid()],
        salt="core.bank-batch",
    )
    recent = (
        private_items(request)
        .order_by("-pk")
        .values_list("batch_uid", flat=True)
        .first()
    )
    return {
        "batch_key": token,
        "last_bank_batch": recent,
        "bank_providers": extraction_ai.choices(request.company.company_uid),
        "max_bank_files": statement_batches.MAX_FILES,
    }


def private_items(request):
    return BankStatementImport.objects.filter(
        user_uid=request.user.user_uid,
        account__company_uid=request.company.company_uid,
        created_at__gte=timezone.now() - statement_batches.RETENTION,
    )


def batch_upload(request, account, uploads):
    token = request.POST.get("batch_key")
    batch_uid = uid()
    if token:
        try:
            user, company, batch_uid = signing.loads(
                token, salt="core.bank-batch", max_age=7200
            )
        except signing.BadSignature as exc:
            raise BusinessError(
                "Le formulaire a expiré. Rouvrez l’import de relevés.", 400
            ) from exc
        if (user, company) != (request.user.user_uid, request.company.company_uid):
            raise BusinessError(
                "L’entreprise active a changé. Rouvrez l’import de relevés.", 409
            )
    try:
        offset = int(request.POST.get("offset", "0"))
    except ValueError as exc:
        raise BusinessError("Position de fichier invalide.") from exc
    statement_batches.enqueue(
        request.company,
        request.user,
        account,
        batch_uid,
        offset,
        uploads,
        request.POST.get("provider", ""),
    )
    url = reverse("bank_statement_batch", args=[batch_uid])
    if request.headers.get("Accept") == "application/json":
        return JsonResponse({"url": url})
    return redirect(url)


@never_cache
@scoped("finance")
@require_http_methods(["GET"])
def batch(request, batch_uid):
    items = list(
        private_items(request)
        .filter(batch_uid=batch_uid)
        .defer("contents", "result")
        .select_related("account")
        .annotate(
            added=F("result__imported"),
            skipped=F("result__skipped"),
            rejected=F("result__rejected"),
            page_index=F("result__index"),
        )
        .order_by("position")
    )
    if not items:
        raise BusinessError("Lot introuvable ou expiré.", 404)
    for item in items:
        item.status_label = statement_batches.LABELS.get(item.status, item.status)
    return render(
        request,
        "core/statement_batch.html",
        {
            "title": "Suivi des relevés",
            "items": items,
            "account": items[0].account,
            "busy": any(i.status in statement_batches.BUSY for i in items),
        },
    )


@never_cache
@scoped("finance")
@require_http_methods(["GET", "POST"])
@transaction.atomic
def review_item(request, item_uid):
    items = private_items(request).select_related("account").defer("contents")
    if request.method == "POST":
        items = items.select_for_update()
    item = get_object_or_404(items, item_uid=item_uid)
    batch_url = reverse("bank_statement_batch", args=[item.batch_uid])
    if item.status != "review":
        return redirect(batch_url)
    review = item.result
    rows = [dict(row, slot=i) for i, row in enumerate(review["rows"])]
    rows.append({"slot": len(rows)})
    if request.method == "POST":
        return confirm_selection(request, item.account, "", item.file_digest, item=item)
    return review_page(request, item.account, "", rows, review["transcript"], item=item)


def success(request, result):
    messages.success(
        request,
        f"{result['imported']} opérations importées, {result['skipped']} déjà présentes, {result['rejected']} lignes rejetées.",
    )
    return redirect(TRANSACTIONS)


@never_cache
def upload(request):
    account = scoped_get(
        BankAccount,
        request,
        account_uid=request.POST.get("account_uid"),
        status="active",
    )
    uploads = request.FILES.getlist("file")
    if (
        request.POST.get("batch_key")
        or len(uploads) > 1
        or request.POST.get("provider")
    ):
        return batch_upload(request, account, uploads)
    uploaded = uploads[0] if uploads else None
    if not uploaded or uploaded.size > bank_statements.MAX_BYTES:
        raise BusinessError("Sélectionnez un relevé bancaire de 5 Mo maximum.")
    raw = uploaded.read(bank_statements.MAX_BYTES + 1)
    suffix = Path(uploaded.name).suffix.lower()
    if suffix != ".pdf":
        kind = {".ofx": "ofx", ".qfx": "ofx", ".csv": "csv"}.get(suffix)
        issues = []
        result = bank_statements.import_file(
            request.company, account.account_uid, kind, raw, issues
        )
        if issues:
            messages.warning(request, " · ".join(issues))
        return success(request, result)
    proposed, transcript = statement_pdf.preview(raw)
    rows = [dict(row, slot=i) for i, row in enumerate(proposed)]
    rows.append({"slot": len(rows)})
    token = signing.dumps(
        [
            request.user.user_uid,
            request.company.company_uid,
            account.account_uid,
            hashlib.sha256(raw).hexdigest(),
            month_end.printed(transcript),
            Path(uploaded.name).name[:255],
        ],
        salt=SIGNING_SALT,
    )
    return review_page(request, account, token, rows, transcript)


def review_page(request, account, token, rows, transcript, error="", item=None):
    return render(
        request,
        "core/statement_pdf_review.html",
        {
            "title": "Vérifier le relevé PDF",
            "item": item,
            "batch_url": reverse("bank_statement_batch", args=[item.batch_uid])
            if item
            else "",
            "account": account,
            "token": token,
            "rows": rows,
            "transcript": transcript,
            "printed": month_end.printed(transcript),
            "max_slots": MAX_SLOTS,
            "error": error,
        },
        status=422 if error else 200,
    )


@never_cache
@scoped("finance")
@require_POST
def confirm_pdf(request):
    token = request.POST.get("preview", "")
    try:
        user, company, uid, file_digest, *metadata = signing.loads(
            token, salt=SIGNING_SALT, max_age=7200
        )
    except signing.BadSignature as exc:
        raise BusinessError(
            "L’aperçu a expiré ou est invalide. Rouvrez le relevé PDF.", 400
        ) from exc
    if (user, company) != (request.user.user_uid, request.company.company_uid):
        raise BusinessError("Aperçu indisponible.", 404)
    account = scoped_get(BankAccount, request, account_uid=uid, status="active")
    return confirm_selection(request, account, token, file_digest, metadata=metadata)


def confirm_selection(request, account, token, file_digest, *, item=None, metadata=None):
    slots = request.POST.getlist("slot")
    selected = set(request.POST.getlist("selected"))
    valid = {str(i) for i in range(MAX_SLOTS)}
    if (
        len(slots) > MAX_SLOTS
        or len(slots) != len(set(slots))
        or not selected <= set(slots) <= valid
    ):
        raise BusinessError("Sélection de lignes invalide.", 400)
    rows = [
        {
            field: request.POST.get(f"{field}_{i}", "")
            for field in ("date", "label", "amount", "direction")
        }
        | {"slot": i, "selected": i in selected}
        for i in slots
    ]
    if item:
        originals = item.result["rows"]
        for row in rows:
            index = int(row["slot"])
            if index < len(originals):
                row.update(
                    {
                        key: originals[index][key]
                        for key in ("page", "original", "ai_completed")
                        if key in originals[index]
                    }
                )
    try:
        if not selected:
            raise BusinessError("Cochez les opérations vérifiées à enregistrer.")
        confirmed = [
            {
                "slot": row["slot"],
                "transaction_date": iso_date(row["date"]),
                "label": row["label"].strip(),
                "amount": decimal(row["amount"]),
                "direction": row["direction"],
            }
            for row in rows
            if row["selected"]
        ]
        if item:
            for row in confirmed:
                index = int(row["slot"])
                row["source_key"] = (
                    originals[index]["source_key"]
                    if index < len(originals)
                    else f"manual:{index - len(originals)}"
                )
        with transaction.atomic():
            result = bank_statements.import_rows(
                request.company, account.account_uid, "pdf", file_digest, confirmed
            )
            printed = month_end.printed(item.result["transcript"]) if item else metadata[0] if metadata else {}
            if printed:
                reference = item.filename if item else metadata[1]
                control = month_end.save_control(account, printed, request.user.user_uid, reference)
                if not month_end.ledger(control)["matches"]:
                    raise BusinessError("Les mouvements comptabilisés ne correspondent pas aux soldes ou aux totaux imprimés. Vérifiez les dates et les lignes sélectionnées.")

    except BusinessError as exc:
        return review_page(
            request,
            account,
            token,
            rows,
            item.result["transcript"]
            if item
            else request.POST.get("transcript", "")[: statement_pdf.MAX_TEXT],
            str(exc),
            item,
        )
    if item:
        item.status, item.result = "done", result
        item.save(update_fields=["status", "result"])
        return redirect("bank_statement_batch", batch_uid=item.batch_uid)
    return success(request, result)


@scoped("finance")
@require_http_methods(["GET", "POST"])
def treatment(request, item_uid):
    item = scoped_get(BankTransaction, request, transaction_uid=item_uid)
    form_rows = [{}, {}]
    error = ""
    response_status = 200
    if request.method == "POST":
        action = request.POST.get("action", "classify")
        if action == "split":
            columns = [request.POST.getlist("allocation_" + key) for key in ("target", "amount", "note")]
            if len(set(map(len, columns))) != 1 or len(columns[0]) > bank_allocations.MAX_LINES:
                raise BusinessError("Lignes de ventilation invalides.")
            form_rows = [dict(zip(("target", "amount", "note"), values)) for values in zip(*columns)]
            try:
                bank_allocations.allocate(request.company, item, form_rows)
            except BusinessError as exc:
                error, response_status = str(exc), exc.status
            else:
                messages.success(request, "Ventilation enregistrée.")
                return redirect("bank_transaction_treatment", item_uid=item_uid)
        elif action == "undo_split":
            bank_allocations.reset(request.company, item, request.POST.get("version", ""))
            messages.success(request, "Ventilation annulée. L’opération est de nouveau à traiter.")
            return redirect("bank_transaction_treatment", item_uid=item_uid)
        elif action in {"classify", "reset"}:
            category = request.POST.get("category", "") if action == "classify" else ""
            if action == "classify" and not category:
                raise BusinessError("Choisissez une catégorie pour classer l’opération.")
            note = request.POST.get("note", "") if category else item.classification_note
            finance.classify(request.company, item, category, note)
            messages.success(request, "Classement enregistré." if category else "L’opération est de nouveau à traiter.")
            return redirect(TRANSACTIONS)
        else:
            raise BusinessError("Action inconnue.")
    account = BankAccount.objects.filter(
        company_uid=request.company.company_uid, account_uid=item.account_uid
    ).first()
    currency = account.currency_code if account else "EUR"
    from core.services.sale_receipts import eligible
    documents = eligible(request.company, "outgoing" if item.direction == "incoming" else "incoming",
                         currency, item.amount).order_by("-issue_date", "number")
    pending = SaleReceipt.objects.filter(document__in=documents, transaction__isnull=True,
                                         cancelled=False, amount=item.amount).select_related("document")
    split_documents = bank_allocations.available_documents(request.company, currency)
    split_receipts = SaleReceipt.objects.filter(
        document__in=split_documents, transaction__isnull=True, cancelled=False,
    ).select_related("document").order_by("document__number", "received_on", "pk")
    return render(
        request,
        "core/bank_treatment.html",
        {
            "title": "Traiter une opération",
            "item": item,
            "account": account,
            "currency": currency,
            "categories": bank_categories.choices(item.direction),
            "documents": documents,
            "pending_receipts": pending,
            "allocations": list(item.allocations.select_related("document","claim")),
            "allocation_claims": ExpenseClaim.objects.filter(company_uid=request.company.company_uid, approved=True).order_by("-month","payer") if currency == "EUR" and item.direction == "outgoing" else [],
            "allocation_docs": split_documents.order_by("-issue_date", "number"),
            "allocation_receipts": split_receipts,
            "allocation_categories": bank_allocations.COMPONENTS,
            "allocation_rows": form_rows or [{}, {}],
            "allocation_error": error,
            "signed_amount": bank_allocations.signed_amount(item),
        },
        status=response_status,
    )
