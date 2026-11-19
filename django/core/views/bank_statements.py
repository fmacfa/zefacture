import hashlib
from pathlib import Path

from django.contrib import messages
from django.core import signing
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_http_methods

from core.common import BusinessError, decimal, iso_date
from core.models import BankAccount, BankTransaction
from core.security import scoped, scoped_get
from core.services import bank_categories, bank_statements, finance, statement_pdf

TRANSACTIONS = "/app/finances/transactions"
SIGNING_SALT = "core.bank-statement-review.v1"
MAX_SLOTS = 120


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
    uploaded = request.FILES.get("file")
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
        ],
        salt=SIGNING_SALT,
    )
    return review_page(request, account, token, rows, transcript)


def review_page(request, account, token, rows, transcript, error=""):
    return render(
        request,
        "core/statement_pdf_review.html",
        {
            "title": "Vérifier le relevé PDF",
            "account": account,
            "token": token,
            "rows": rows,
            "transcript": transcript,
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
        user, company, uid, file_digest = signing.loads(
            token, salt=SIGNING_SALT, max_age=7200
        )
    except signing.BadSignature as exc:
        raise BusinessError(
            "L’aperçu a expiré ou est invalide. Rouvrez le relevé PDF.", 400
        ) from exc
    if (user, company) != (request.user.user_uid, request.company.company_uid):
        raise BusinessError("Aperçu indisponible.", 404)
    account = scoped_get(BankAccount, request, account_uid=uid, status="active")
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
        result = bank_statements.import_rows(
            request.company, uid, "pdf", file_digest, confirmed
        )
    except BusinessError as exc:
        return review_page(
            request,
            account,
            token,
            rows,
            request.POST.get("transcript", "")[: statement_pdf.MAX_TEXT],
            str(exc),
        )
    return success(request, result)


@scoped("finance")
@require_http_methods(["GET", "POST"])
def treatment(request, item_uid):
    item = scoped_get(BankTransaction, request, transaction_uid=item_uid)
    if request.method == "POST":
        action = request.POST.get("action", "classify")
        if action not in {"classify", "reset"}:
            raise BusinessError("Action inconnue.")
        category = request.POST.get("category", "") if action == "classify" else ""
        if action == "classify" and not category:
            raise BusinessError("Choisissez une catégorie pour classer l’opération.")
        note = (
            request.POST.get("note", "")
            if action == "classify"
            else item.classification_note
        )
        finance.classify(request.company, item, category, note)
        messages.success(
            request,
            "Classement enregistré."
            if category
            else "L’opération est de nouveau à traiter.",
        )
        return redirect(TRANSACTIONS)
    account = BankAccount.objects.filter(
        company_uid=request.company.company_uid, account_uid=item.account_uid
    ).first()
    currency = account.currency_code if account else "EUR"
    documents = (
        finance.effective_documents(request.company)
        .filter(
            direction="outgoing" if item.direction == "incoming" else "incoming",
            type__in=["invoice", "progress_invoice"],
            currency_code=currency,
            amount_due__gte=item.amount,
        )
        .exclude(status="paid")
        .order_by("-issue_date", "number")
    )
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
        },
    )
