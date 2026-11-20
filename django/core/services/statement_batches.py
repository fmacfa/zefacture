"""Short-lived bank statement intake. Only structured files and confirmed PDF rows book money."""

from datetime import timedelta
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from core.common import BusinessError, digest
from core.models import BankAccount, BankStatementImport, Company, CompanyMember, User
from core.numbers import NumberStyle, current_style, number_context
from core.security import ROLES
from . import bank_statements, extraction_ai, statement_ai, statement_pdf

MAX_FILES = 50
MAX_UPLOAD = 15_000_000
RETENTION = timedelta(days=7)
BUSY = ("queued", "reading", "analyzing", "working")
LABELS = {
    "queued": "En attente",
    "reading": "Lecture du relevé",
    "analyzing": "Analyse IA",
    "working": "Analyse IA",
    "review": "À vérifier",
    "done": "Traité",
    "failed": "Échec",
    "duplicate": "Fichier en double",
}


@transaction.atomic
def enqueue(company, user, account, batch_uid, offset, uploads, provider):
    if not 0 <= offset < MAX_FILES or not uploads or offset + len(uploads) > MAX_FILES:
        raise BusinessError(f"Sélectionnez entre 1 et {MAX_FILES} relevés.")
    if (
        any(f.size > bank_statements.MAX_BYTES for f in uploads)
        or sum(f.size for f in uploads) > MAX_UPLOAD
    ):
        raise BusinessError("Chaque relevé est limité à 5 Mo et chaque envoi à 15 Mo.")
    if provider and provider not in dict(extraction_ai.choices(company.company_uid)):
        raise BusinessError(
            "Ce fournisseur IA n’est plus disponible pour cette entreprise."
        )
    Company.objects.select_for_update().get(pk=company.pk)
    batch = BankStatementImport.objects.filter(batch_uid=batch_uid)
    if batch.exclude(
        account=account, user_uid=user.user_uid, provider=provider
    ).exists():
        raise BusinessError(
            "Le compte ou le mode de lecture du lot a changé. Rouvrez le formulaire.",
            409,
        )
    for position, upload in enumerate(uploads, offset):
        raw = upload.read(bank_statements.MAX_BYTES + 1)
        if len(raw) > bank_statements.MAX_BYTES:
            raise BusinessError("Un relevé dépasse 5 Mo.")
        name = Path(upload.name).name[:255]
        kind = {".csv": "csv", ".ofx": "ofx", ".qfx": "ofx", ".pdf": "pdf"}.get(
            Path(name).suffix.lower(), ""
        )
        fingerprint = digest(raw)
        previous = (
            batch.filter(position=position).values("filename", "file_digest").first()
        )
        if previous:
            if previous != {"filename": name, "file_digest": fingerprint}:
                raise BusinessError(
                    "La sélection a changé. Rouvrez le formulaire pour commencer un nouveau lot.",
                    409,
                )
            continue
        status, message = "queued", ""
        if not kind or not raw:
            status, message = (
                "failed",
                "Fichier vide ou format incompatible. Utilisez CSV, OFX, QFX ou PDF.",
            )
        elif (
            prior := batch.filter(kind=kind, file_digest=fingerprint)
            .order_by("position")
            .values_list("filename", flat=True)
            .first()
        ):
            status, message = "duplicate", f"Même contenu que « {prior} » dans ce lot."
        style = current_style()
        BankStatementImport.objects.create(
            batch_uid=batch_uid,
            position=position,
            account=account,
            user_uid=user.user_uid,
            filename=name,
            file_digest=fingerprint,
            kind=kind,
            provider=provider,
            status=status,
            message=message,
            contents=raw if status == "queued" else None,
            result={
                "style": {
                    "grouping": style.grouping,
                    "decimal": style.decimal,
                    "customized": style.customized,
                }
            }
            if kind == "csv"
            else {},
        )


def finish_review(pages):
    rows = [row for page in pages for row in page["rows"]]
    if len(rows) > statement_pdf.MAX_LINES:
        raise BusinessError(
            "Le relevé dépasse 100 opérations proposées. Scindez le PDF."
        )
    return {"rows": rows, "transcript": statement_pdf.transcript_for(pages)}


def process(item, step):
    account = item.account
    company = Company.objects.filter(
        company_uid=account.company_uid, status="active"
    ).first()
    if (
        not company
        or account.status != "active"
        or not User.objects.filter(user_uid=item.user_uid, is_active=True).exists()
        or not CompanyMember.objects.filter(
            user_uid=item.user_uid,
            company_uid=account.company_uid,
            status="active",
            role__in=ROLES["finance"],
        ).exists()
    ):
        raise BusinessError("Le compte ou l’accès financier n’est plus actif.")
    if item.kind != "pdf":
        issues = []
        with number_context(NumberStyle(**item.result.get("style", {}))):
            counts = bank_statements.import_file(
                company, account.account_uid, item.kind, bytes(item.contents), issues
            )
        return "done", counts, " · ".join(issues)
    if step == "queued":
        pages = statement_pdf.read_pages(bytes(item.contents))
        # Retain the slot identities already used for PDFs read by earlier releases.
        for slot, row in enumerate(row for page in pages for row in page["rows"]):
            row["source_key"] = slot
        if not item.provider:
            return "review", finish_review(pages), ""
        return "analyzing", {"pages": pages, "index": 0}, ""
    pages, index = item.result["pages"], item.result["index"]
    original_rows = [row.copy() for row in pages[index]["rows"]]
    try:
        statement_ai.complete_page(pages[index], item.provider, company.company_uid)
        if sum(len(page["rows"]) for page in pages) > statement_pdf.MAX_LINES:
            raise BusinessError("L’IA a proposé trop d’opérations.")
    except BusinessError as exc:
        # Keep previous pages and the local reading of the unfinished page.
        pages[index]["rows"] = original_rows
        return (
            "review",
            finish_review(pages),
            f"Analyse IA interrompue à la page {index + 1}. {exc}",
        )
    if index + 1 < len(pages):
        return "analyzing", {"pages": pages, "index": index + 1}, ""
    return "review", finish_review(pages), ""


def work_one():
    now = timezone.now()
    BankStatementImport.objects.filter(created_at__lt=now - RETENTION).delete()
    BankStatementImport.objects.filter(
        status__in=("reading", "working"), started_at__lt=now - timedelta(minutes=15)
    ).update(
        status="failed",
        contents=None,
        result={},
        message="Lecture interrompue. Réimportez ce fichier.",
    )
    with transaction.atomic():
        item = (
            BankStatementImport.objects.select_for_update(skip_locked=True)
            .filter(status__in=("queued", "analyzing"))
            .order_by("pk")
            .first()
        )
        if not item:
            return False
        step = item.status
        item.status = "reading" if step == "queued" else "working"
        item.started_at = now
        item.save(update_fields=["status", "started_at"])
    claimed = BankStatementImport.objects.filter(
        pk=item.pk, status=item.status, started_at=now
    )
    try:
        # Structured ledger postings and their result must commit together.
        if item.kind != "pdf":
            with transaction.atomic():
                if claimed.select_for_update().exists():
                    state, result, message = process(item, step)
                    claimed.update(
                        status=state, result=result, message=message, contents=None
                    )
            return True
        state, result, message = process(item, step)
    except BusinessError as exc:
        state, result, message = "failed", {}, str(exc)
    except Exception:
        import logging

        logging.getLogger(__name__).error(
            "Échec du traitement bancaire pour la tâche %s", item.pk
        )
        state, result, message = (
            "failed",
            {},
            "Traitement bancaire indisponible. Réimportez le fichier.",
        )
    claimed.update(status=state, result=result, message=message, contents=None)
    return True
