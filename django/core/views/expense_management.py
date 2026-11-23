from datetime import date
from pathlib import Path

from django.contrib import messages
from django.core import signing
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from core.common import BusinessError
from core.models import (
    BankAccount,
    BankTransaction,
    ClosingReviewNote,
    EvidenceFile,
    ExpenseClaim,
    ExpenseWorkbook,
    Ingestion,
    StatementControl,
)
from core.security import scoped
from core.services import (
    bank_allocations,
    evidence,
    expense_claims,
    expense_registers,
    month_end,
    statement_pdf,
)
from core.services.receipt_extraction import EXPENSE_KINDS

GROUPS = "/app/notes-de-frais/groupees"
REGISTERS = "/app/notes-de-frais/registres"


@scoped("finance")
@require_http_methods(["GET", "POST"])
def claims(request, uid=None):
    company, actor = request.company, request.user.user_uid
    claims = ExpenseClaim.objects.filter(company_uid=company.company_uid)
    claim = get_object_or_404(claims, claim_uid=uid) if uid else None
    request.navigation_route = GROUPS
    action = request.POST.get("action")
    if request.method == "POST":
        if not claim and action == "draft":
            claim = expense_claims.make_draft(
                company,
                actor,
                request.POST.get("payer", "").strip(),
                expense_claims.month_start(request.POST.get("month", "")),
            )
        elif claim and action == "approve":
            expense_claims.approve(company, claim, actor)
        elif claim and action == "reopen":
            expense_claims.reopen(company, claim)
        elif claim and action == "remove":
            with transaction.atomic():
                claim = claims.select_for_update().get(pk=claim.pk)
                if claim.approved:
                    raise BusinessError(
                        "Rouvrez la note avant de retirer une pièce.", 409
                    )
                get_object_or_404(
                    claim.lines, receipt__ingestion_uid=request.POST.get("receipt")
                ).delete()
        elif claim and action == "pay":
            tx = get_object_or_404(
                BankTransaction,
                company_uid=company.company_uid,
                transaction_uid=request.POST.get("transfer"),
            )
            bank_allocations.allocate(
                company,
                tx,
                [{"target": "claim-" + uid, "amount": format(-tx.amount, ".2f")}],
            )
        else:
            raise BusinessError("Action de note groupée inconnue.")
        return redirect(GROUPS + "/" + claim.claim_uid)
    if not claim:
        payers = (
            Ingestion.objects.filter(
                company_uid=company.company_uid,
                archived_at__isnull=True,
                source_kind__in=EXPENSE_KINDS,
                expense_paid_by="personal",
            )
            .exclude(expense_payer_name="")
            .values_list("expense_payer_name", flat=True)
            .distinct()
            .order_by("expense_payer_name")
        )
        return render(
            request,
            "core/claims.html",
            {
                "title": "Notes groupées",
                "claims": claims.annotate(count=Count("lines")).order_by(
                    "-month", "payer"
                ),
                "payers": payers,
            },
        )
    total, paid, left = expense_claims.balances(claim)
    lines = list(
        claim.lines.select_related("receipt").order_by(
            "receipt__expense_date", "receipt_id"
        )
    )
    if not claim.approved:
        total = sum(l.receipt.expense_paid_eur or 0 for l in lines)
    transfers = (
        BankTransaction.objects.filter(
            company_uid=company.company_uid,
            direction="outgoing",
            status="booked",
            account_uid__in=BankAccount.objects.filter(
                company_uid=company.company_uid, currency_code="EUR"
            ).values("account_uid"),
            amount__lte=left,
            standalone_category="",
            allocations__isnull=True,
        )
        .filter(Q(document_uid__isnull=True) | Q(document_uid=""))
        .exclude(bank_feed__review__gt="")
        .order_by("-transaction_date", "pk")
        if claim.approved and left > 0
        else []
    )
    return render(
        request,
        "core/claim.html",
        {
            "title": "Note groupée",
            "claim": claim,
            "lines": lines,
            "missing": sum(l.receipt.expense_paid_eur is None for l in lines),
            "total": total,
            "paid": paid,
            "left": left,
            "transfers": transfers,
            "payments": claim.allocations.select_related("transaction").order_by(
                "transaction__transaction_date"
            ),
        },
    )


@scoped("finance")
@require_http_methods(["GET", "POST"])
def proofs(request, kind, uid):
    is_expense = kind == "frais"
    target = (
        get_object_or_404(
            Ingestion,
            company_uid=request.company.company_uid,
            ingestion_uid=uid,
            source_kind__in=EXPENSE_KINDS,
        )
        if is_expense
        else get_object_or_404(
            BankTransaction,
            company_uid=request.company.company_uid,
            transaction_uid=uid,
        )
    )
    sources = EvidenceFile.objects.filter(
        company_uid=request.company.company_uid
    ).defer("contents")
    if request.method == "POST":
        with transaction.atomic():
            if request.POST.get("unlink"):
                get_object_or_404(
                    target.proofs, pk=int_field(request.POST["unlink"])
                ).delete()
            else:
                uploaded = request.FILES.get("source")
                if uploaded and request.POST.get("existing"):
                    raise BusinessError(
                        "Choisissez un fichier ou une pièce déjà déposée."
                    )
                source = (
                    evidence.store(
                        request.company, uploaded.name, evidence.read_upload(uploaded)
                    )
                    if uploaded
                    else get_object_or_404(
                        sources, file_uid=request.POST.get("existing")
                    )
                )
                evidence.attach(target, source, request.POST.get("page"))
        return redirect(request.path)
    return render(
        request,
        "core/evidence.html",
        {
            "title": "Pièces justificatives",
            "target": target.original_filename if is_expense else target.label,
            "back": "/app/imports/" + uid
            if is_expense
            else "/app/finances/transactions/" + uid + "/traiter",
            "sources": sources.order_by("filename"),
            "links": target.proofs.select_related("source")
            .defer("source__contents")
            .order_by("source__filename", "page"),
        },
    )


@scoped("finance")
@require_http_methods(["GET"])
def original(request, uid, page=0):
    return evidence.stream(
        get_object_or_404(
            EvidenceFile, company_uid=request.company.company_uid, file_uid=uid
        ),
        page,
    )


def int_field(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        raise BusinessError("Choisissez un numéro valide.")


@scoped("finance")
@require_http_methods(["GET", "POST"])
def registers(request, uid=None):
    company, actor = request.company, request.user.user_uid
    request.navigation_route = REGISTERS
    books = ExpenseWorkbook.objects.filter(company_uid=company.company_uid)
    if not uid:
        if request.method == "POST":
            uploaded = request.FILES.get("workbook")
            raw = evidence.read_upload(uploaded)
            with transaction.atomic():
                book = expense_registers.upload(company, uploaded.name, raw)
                for file in request.FILES.getlist("proofs"):
                    evidence.store(company, file.name, evidence.read_upload(file))
            return redirect(REGISTERS + "/" + book.upload_uid)
        return render(
            request,
            "core/registers.html",
            {
                "title": "Registres de frais",
                "books": books.defer("contents").order_by("-pk"),
            },
        )
    book = get_object_or_404(books, upload_uid=uid)
    if request.method == "POST" and request.POST.get("apply"):
        try:
            owner, tenant, source, options, checksum = signing.loads(
                request.POST["apply"], salt="expense-register", max_age=7200
            )
        except signing.BadSignature:
            raise BusinessError("L’aperçu a expiré. Rouvrez le registre.", 409)
        if (owner, tenant, source) != (actor, company.company_uid, uid):
            raise BusinessError("Aperçu indisponible.", 404)
        n = expense_registers.commit(
            company,
            actor,
            book,
            options,
            request.POST.getlist("row"),
            checksum,
            preserve_existing=request.POST.get("preserve_existing") == "1",
        )
        messages.success(request, f"{n} ligne(s) du registre enregistrée(s).")
        from urllib.parse import urlencode

        return redirect(
            request.path
            + "?"
            + urlencode({k: options[k] for k in ("sheet", "register", "header")})
        )
    params = request.POST if request.method == "POST" else request.GET
    opened = expense_registers.open_book(bytes(book.contents))
    try:
        sheets = opened.sheetnames
    finally:
        opened.close()
    sheet = params.get("sheet", sheets[0])
    header = int_field(params.get("header") or 1)
    columns, _ = expense_registers.read_sheet(book, sheet, header)
    register = params.get("register", Path(book.filename).stem).strip()
    mapping = {
        key: int_field(params["col_" + key])
        for key in expense_registers.FIELDS
        if params.get("col_" + key)
    }
    rows, token = None, ""
    currency = params.get("currency", "").strip().upper()
    if request.method == "POST":
        options = dict(
            register=register,
            sheet=sheet,
            header=header,
            mapping=mapping,
            currency=currency,
        )
        rows = expense_registers.preview(company, book, **options)
        token = signing.dumps(
            [
                actor,
                company.company_uid,
                uid,
                options,
                expense_registers.fingerprint(rows),
            ],
            salt="expense-register",
            compress=True,
        )
    return render(
        request,
        "core/register.html",
        {
            "title": "Aperçu du registre",
            "book": book,
            "sheets": sheets,
            "sheet": sheet,
            "columns": columns,
            "header": header,
            "register": register,
            "fields": expense_registers.mapping_fields(columns, mapping),
            "currency": currency,
            "previewed": rows is not None,
            "rows": rows or [],
            "token": token,
        },
    )


@scoped("finance")
@require_http_methods(["GET", "POST"])
def close_month(request):
    company = request.company
    accounts = BankAccount.objects.filter(company_uid=company.company_uid).order_by(
        "name"
    )
    controls = (
        StatementControl.objects.filter(account__in=accounts)
        .select_related("account", "source")
        .defer("source__contents")
        .order_by("-end", "account__name")
    )
    month = (
        expense_claims.month_start(request.GET["month"])
        if request.GET.get("month")
        else None
    )
    editing = (
        get_object_or_404(controls, pk=int_field(request.GET["edit"]))
        if request.GET.get("edit")
        else None
    )
    data, source, account, previewed = {}, None, None, False
    if editing:
        account = editing.account
        data = {
            k: getattr(editing, k)
            for k in ("debits", "credits", "opening", "closing", "reference")
        }
        data.update(
            start=editing.start.isoformat(),
            end=editing.end.isoformat(),
            version=editing.updated_at.isoformat(),
        )
        source = editing.source
    if request.method == "POST" and request.POST.get("action") == "annotate":
        receipt = get_object_or_404(
            Ingestion,
            company_uid=company.company_uid,
            ingestion_uid=request.POST.get("uid"),
            archived_at__isnull=True,
        )
        reason = request.POST.get("reason", "").strip()
        next_step = request.POST.get("next_action", "").strip()
        if not reason or len(reason) > 750 or not next_step or len(next_step) > 500:
            raise BusinessError("Renseignez le motif et l’action de résolution.")
        try:
            document_date = (
                date.fromisoformat(request.POST["document_date"])
                if request.POST.get("document_date")
                else None
            )
        except ValueError:
            raise BusinessError("Vérifiez la date de la pièce.")
        ClosingReviewNote.objects.update_or_create(
            receipt=receipt,
            defaults={
                "document_date": document_date,
                "reason": reason,
                "next_action": next_step,
                "updated_by": request.user.user_uid,
            },
        )
        return redirect(request.get_full_path())
    if request.method == "POST":
        account = get_object_or_404(accounts, account_uid=request.POST.get("account"))
        data = request.POST.dict()
        if request.POST.get("action") == "read":
            file = request.FILES.get("source")
            raw = evidence.read_upload(file)
            if not raw.startswith(b"%PDF-"):
                raise BusinessError("Sélectionnez le relevé au format PDF.")
            source = evidence.store(company, file.name, raw)
            _, transcript = statement_pdf.preview(raw)
            data.update(month_end.printed(transcript), reference=source.filename)
            previewed = True
        else:
            if request.FILES.get("source"):
                file = request.FILES["source"]
                source = evidence.store(company, file.name, evidence.read_upload(file))
            elif data.get("source_uid"):
                source = get_object_or_404(
                    EvidenceFile,
                    company_uid=company.company_uid,
                    file_uid=data["source_uid"],
                )
            control = month_end.save_control(
                account,
                data,
                request.user.user_uid,
                data.get("reference", ""),
                source,
                data.get("version", ""),
            )
            return redirect("/app/cloture?month=" + control.end.strftime("%Y-%m"))
    if month:
        start, end = month_end.period_bounds(month)
        controls = controls.filter(end__gte=start, end__lte=end)
    entries = [(c, month_end.ledger(c)) for c in controls]
    covered = {c.account_id for c, _ in entries}
    issues = month_end.review_queue(company, month)
    return render(
        request,
        "core/month_end.html",
        {
            "title": "Clôture mensuelle",
            "accounts": accounts,
            "account": account,
            "data": data,
            "source": source,
            "previewed": previewed,
            "editing": editing,
            "entries": entries,
            "month": month.strftime("%Y-%m") if month else "",
            "missing_accounts": [a for a in accounts if a.pk not in covered],
            "page": Paginator(issues, 25).get_page(request.GET.get("page")),
            "expense_count": sum(i["expense"] for i in issues),
            "invoice_count": sum(not i["expense"] for i in issues),
        },
    )
