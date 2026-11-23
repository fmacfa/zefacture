import re

from django.db import transaction
from django.db.models import Q, Sum

from core.common import BusinessError, decimal, stamp
from core.models import (
    BankAccount,
    BankAllocation,
    BankFeedEntry,
    BankTransaction,
    Document,
    ExpenseClaim,
)
from . import sale_receipts, expense_claims
from .documents import INVOICE_TYPES, event

KINDS = BankAllocation.Kind
COMPONENTS = dict(KINDS.choices)
COMPONENTS.pop(KINDS.DOCUMENT)
COMPONENTS.pop(KINDS.CLAIM)
OPEN = ("sent", "accepted", "received", "disputed")
MAX_LINES = 100


def available_documents(company, currency):
    return (
        Document.objects.filter(
            company_uid=company.company_uid,
            currency_code=currency,
            type__in=(*INVOICE_TYPES, "credit_note"),
        )
        .filter(
            Q(status__in=OPEN)
            | Q(status="paid", type="credit_note")
            | Q(
                status="paid",
                direction="outgoing",
                receipts__transaction__isnull=True,
                receipts__cancelled=False,
            )
        )
        .distinct()
    )


def account_exists(company, tx):
    return BankAccount.objects.filter(company_uid=company.company_uid, account_uid=tx.account_uid).exists()


def signed_amount(tx):
    return tx.amount if tx.direction == "incoming" else -tx.amount


def lock_transaction(company, tx):
    feed = BankFeedEntry.objects.select_for_update().filter(transaction=tx).first()
    tx = BankTransaction.objects.select_for_update().get(
        pk=tx.pk, company_uid=company.company_uid
    )
    if tx.status != "booked" or (feed and feed.review):
        raise BusinessError(
            "Vérifiez le statut de l’opération bancaire avant de la ventiler.", 409
        )
    return tx


def parse_lines(rows):
    if len(rows) > MAX_LINES:
        raise BusinessError(f"Une ventilation est limitée à {MAX_LINES} lignes.")
    parsed, document_uids = [], set()
    for row in rows:
        target = str(row.get("target") or "").strip()
        raw = str(row.get("amount") or "").strip()
        note = str(row.get("note") or "").strip()
        if not (target or raw or note):
            continue
        if not target or not re.fullmatch(r"[+-]?\d+(?:[.,]\d{1,2})?", raw):
            raise BusinessError(
                "Choisissez une affectation et un montant signé au centime pour chaque ligne."
            )
        amount = decimal(raw)
        if not amount or len(note) > 500:
            raise BusinessError(
                "Chaque ligne exige un montant non nul et un motif de 500 caractères au maximum."
            )
        if re.fullmatch(r"claim-[0-9a-f]{32}", target):
            if any(row[0] == KINDS.CLAIM and row[1] == target[6:] for row in parsed):
                raise BusinessError("Regroupez les montants affectés à la même note.")
            parsed.append((KINDS.CLAIM, target[6:], "", amount, note))
            continue
        if target in COMPONENTS:
            parsed.append((target, "", "", amount, note))
            continue
        doc_uid, _, choice = target.partition("/")
        if not re.fullmatch(r"[0-9a-f]{32}", doc_uid) or (
            choice and choice != "new" and not re.fullmatch(r"[0-9a-f]{32}", choice)
        ):
            raise BusinessError("Affectation inconnue.")
        if doc_uid in document_uids:
            raise BusinessError(
                "Regroupez les montants affectés à une même pièce sur une ligne."
            )
        document_uids.add(doc_uid)
        parsed.append((KINDS.DOCUMENT, doc_uid, choice, amount, note))
    if not parsed:
        raise BusinessError("Ajoutez au moins une ligne à la ventilation.")
    return parsed, document_uids


def apply_document(tx, doc, line, choice):
    amount = abs(line.amount)
    credit = doc.type == "credit_note"
    if (line.amount > 0) != ((doc.direction == "outgoing") != credit):
        raise BusinessError(
            f"Le signe du montant ne correspond pas au sens de la pièce {doc.number}."
        )
    if doc.status not in OPEN and not (
        doc.status == "paid" and (credit or sale_receipts.is_sale(doc))
    ):
        raise BusinessError("Cette pièce ne peut plus être rapprochée.", 409)
    line.previous_status = doc.status
    if credit:
        used = doc.bank_allocations.aggregate(total=Sum("amount"))["total"] or 0
        if amount + abs(used) > abs(doc.total_including_tax):
            raise BusinessError(
                f"Le montant dépasse le crédit disponible sur {doc.number}.", 409
            )
    elif sale_receipts.is_sale(doc):
        pending = doc.receipts.select_for_update().filter(
            transaction__isnull=True, cancelled=False, amount=amount
        )
        if choice != "new":
            candidate = (
                pending.filter(receipt_uid=choice).first()
                if choice
                else pending.first()
            )
            if candidate:
                line.previous_receipt_date = candidate.received_on
        sale_receipts.confirm_or_record(doc, tx, choice, False, amount=amount)
    else:
        sale_receipts.apply_amount(doc, amount, tx.transaction_date)
    if choice not in ("", "new") and not sale_receipts.is_sale(doc):
        raise BusinessError("Ce règlement déclaré appartient à une autre facture.", 409)
    event(
        doc,
        "bank_allocation",
        "Montant rapproché par ventilation",
        {
            "transaction_uid": tx.transaction_uid,
            "amount": line.amount,
            "remaining": doc.amount_due,
        },
    )


@transaction.atomic
def allocate(company, tx, rows):
    parsed, document_uids = parse_lines(rows)
    tx = lock_transaction(company, tx)
    if tx.document_uid or tx.standalone_category or tx.allocations.exists():
        raise BusinessError("Cette opération a déjà été traitée.", 409)
    delta = signed_amount(tx) - sum(row[3] for row in parsed)
    if delta:
        raise BusinessError(
            f"La somme signée doit égaler le mouvement bancaire. Écart à répartir {delta:+.2f}."
        )
    currency = (
        BankAccount.objects.filter(
            company_uid=company.company_uid, account_uid=tx.account_uid
        )
        .values_list("currency_code", flat=True)
        .first()
        or "EUR"
    )
    docs = {
        d.document_uid: d
        for d in Document.objects.select_for_update()
        .filter(
            company_uid=company.company_uid,
            currency_code=currency,
            document_uid__in=document_uids,
            type__in=(*INVOICE_TYPES, "credit_note"),
        )
        .order_by("pk")
    }
    if docs.keys() != document_uids:
        raise BusinessError(
            "Une pièce est indisponible pour cette entreprise ou cette devise.", 409
        )
    claim_ids = {r[1] for r in parsed if r[0] == KINDS.CLAIM}
    claims = {c.claim_uid: c for c in ExpenseClaim.objects.select_for_update().filter(
        claim_uid__in=claim_ids, company_uid=company.company_uid, approved=True).order_by("pk")}
    if claims.keys() != claim_ids:
        raise BusinessError("Une note groupée est indisponible ou encore en brouillon.", 409)
    lines = []
    for kind, doc_uid, choice, amount, note in parsed:
        doc = docs.get(doc_uid) if kind == KINDS.DOCUMENT else None
        claim = claims.get(doc_uid) if kind == KINDS.CLAIM else None
        if claim and (currency != "EUR" or not account_exists(company, tx) or tx.direction != "outgoing"
                      or amount >= 0 or -amount > expense_claims.balances(claim)[2]):
            raise BusinessError("Le remboursement doit provenir d’un débit en euros et respecter le solde de la note.", 409)
        line = BankAllocation(
            transaction=tx, document=doc, claim=claim, kind=kind, amount=amount, note=note
        )
        if doc:
            apply_document(tx, doc, line, choice)
        lines.append(line)
    BankAllocation.objects.bulk_create(lines)
    for claim in claims.values():
        expense_claims.update_reimbursements(claim)
    tx.updated_at = stamp()
    tx.save(update_fields=["updated_at"])


def undo_locked(tx, *, version=None):
    """Called in an atomic transaction with the feed entry and bank transaction locked."""
    lines = list(tx.allocations.all())
    if not lines or (version is not None and str(lines[0].pk) != str(version)):
        raise BusinessError("La ventilation a changé. Rouvrez l’opération.", 409)
    docs = {
        d.pk: d
        for d in Document.objects.select_for_update()
        .filter(
            company_uid=tx.company_uid,
            pk__in=[a.document_id for a in lines if a.document_id],
        )
        .order_by("pk")
    }
    claims = list(ExpenseClaim.objects.select_for_update().filter(
        company_uid=tx.company_uid, pk__in=[line.claim_id for line in lines if line.claim_id]).order_by("pk"))
    for line in lines:
        if not line.document_id:
            continue
        doc = docs[line.document_id]
        receipt = (
            doc.receipts.select_for_update()
            .filter(transaction=tx, cancelled=False)
            .first()
        )
        restore_balance = doc.type != "credit_note" and (
            not receipt or receipt.origin == "bank"
        )
        if receipt:
            receipt.transaction = None
            if receipt.origin == "bank":
                receipt.cancelled = True
            else:
                receipt.received_on = line.previous_receipt_date
            receipt.save(update_fields=["transaction", "cancelled", "received_on"])
        if restore_balance:
            doc.amount_due += abs(line.amount)
            if doc.amount_due > doc.total_including_tax or doc.status not in (
                *OPEN,
                "paid",
            ):
                raise BusinessError(
                    "Le solde de la facture a changé. Vérifiez ses règlements.", 409
                )
            if doc.status == "paid":
                doc.status, doc.paid_at = line.previous_status, None
            doc.updated_at = stamp()
            doc.save(update_fields=["amount_due", "status", "paid_at", "updated_at"])
        event(
            doc,
            "bank_allocation_reset",
            "Ventilation bancaire annulée",
            {
                "transaction_uid": tx.transaction_uid,
                "amount": line.amount,
                "remaining": doc.amount_due,
            },
        )
    tx.allocations.all().delete()
    for claim in claims:
        expense_claims.update_reimbursements(claim)
    tx.updated_at = stamp()
    tx.save(update_fields=["updated_at"])


@transaction.atomic
def reset(company, tx, version):
    undo_locked(lock_transaction(company, tx), version=version)
