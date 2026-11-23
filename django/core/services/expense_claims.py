from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from core.common import BusinessError
from core.models import ExpenseClaim, ExpenseClaimLine, ExpenseReimbursement, Ingestion
from .receipt_extraction import EXPENSE_KINDS


def month_start(value):
    try:
        return date.fromisoformat(value + "-01")
    except (TypeError, ValueError):
        raise BusinessError("Choisissez un mois valide.")


def eligible(company, payer, month):
    return Ingestion.objects.filter(
        company_uid=company.company_uid,
        source_kind__in=EXPENSE_KINDS,
        expense_paid_by="personal",
        expense_payer_name=payer,
        archived_at__isnull=True,
        expense_date__startswith=month.strftime("%Y-%m-"),
    ).exclude(reimbursement__status__in=["paid", "exempt"])


@transaction.atomic
def make_draft(company, actor, payer, month):
    if not payer or len(payer) > 180:
        raise BusinessError("Choisissez un payeur identifié sur un justificatif.")
    # Also serializes two simultaneous creations for this payer and month.
    type(company).objects.select_for_update().get(pk=company.pk)
    claim, _ = ExpenseClaim.objects.get_or_create(
        company_uid=company.company_uid,
        payer=payer,
        month=month,
        defaults={"created_by": actor},
    )
    if claim.approved:
        raise BusinessError("Cette note groupée est déjà validée.", 409)
    items = (
        eligible(company, payer, month)
        .select_for_update(of=("self",))
        .filter(claim_line__isnull=True)
        .order_by("pk")
    )
    ExpenseClaimLine.objects.bulk_create(
        [
            ExpenseClaimLine(claim=claim, receipt=r, amount=r.expense_paid_eur)
            for r in items
        ]
    )
    if not claim.lines.exists():
        raise BusinessError(
            "Aucun justificatif actif et disponible pour ce payeur et ce mois."
        )
    return claim


def require_editable(item):
    if ExpenseClaimLine.objects.filter(receipt=item, claim__approved=True).exists():
        raise BusinessError(
            "Cette pièce appartient à une note de frais validée. Rouvrez la note groupée pour la modifier.",
            409,
        )


@transaction.atomic
def approve(company, claim, actor):
    claim = ExpenseClaim.objects.select_for_update().get(
        pk=claim.pk, company_uid=company.company_uid
    )
    if claim.approved:
        return
    lines = list(claim.lines.order_by("receipt_id"))
    items = {
        r.pk: r
        for r in eligible(company, claim.payer, claim.month)
        .select_for_update(of=("self",))
        .filter(pk__in=[l.receipt_id for l in lines])
        .order_by("pk")
    }
    if not lines or len(items) != len(lines):
        raise BusinessError(
            "Une pièce a changé de payeur, de période ou de statut. Révisez les lignes de la note.",
            409,
        )
    for line in lines:
        item = items[line.receipt_id]
        from .jobs import require_idle

        require_idle(item)
        if item.expense_paid_eur is None or item.expense_paid_eur <= 0:
            raise BusinessError(
                "Renseignez le montant effectivement avancé en euros sur chaque pièce."
            )
        line.amount = item.expense_paid_eur
        previous = getattr(item, "reimbursement", None)
        line.previous_status = previous.status if previous else ""
        line.save(update_fields=["amount", "previous_status"])
    claim.approved, claim.approved_by = True, actor
    claim.save(update_fields=["approved", "approved_by"])
    update_reimbursements(claim)


def balances(claim):
    total = claim.lines.aggregate(n=Sum("amount"))["n"] or Decimal(0)
    paid = -(claim.allocations.aggregate(n=Sum("amount"))["n"] or Decimal(0))
    return total, paid, total - paid


def update_reimbursements(claim):
    # Apply transfers by booking date to the oldest receipts first.
    payments = list(
        claim.allocations.order_by("transaction__transaction_date", "pk").values_list(
            "transaction__transaction_date", "amount"
        )
    )
    through = Decimal(0)
    idx = 0
    funded = Decimal(0)
    payment_date = None
    for line in claim.lines.order_by("receipt__expense_date", "receipt_id"):
        through += line.amount
        while funded < through and idx < len(payments):
            payment_date, signed = payments[idx]
            funded -= signed
            idx += 1
        paid = funded >= through
        ExpenseReimbursement.objects.update_or_create(
            receipt_id=line.receipt_id,
            defaults={
                "status": "paid" if paid else "due",
                "paid_on": payment_date if paid else None,
                "updated_by": claim.approved_by,
            },
        )


@transaction.atomic
def reopen(company, claim):
    claim = ExpenseClaim.objects.select_for_update().get(
        pk=claim.pk, company_uid=company.company_uid
    )
    if not claim.approved:
        return
    if claim.allocations.exists():
        raise BusinessError(
            "Annulez d’abord les ventilations des virements rattachés.", 409
        )
    for line in claim.lines.all():
        if line.previous_status:
            ExpenseReimbursement.objects.filter(receipt_id=line.receipt_id).update(
                status=line.previous_status, paid_on=None
            )
        else:
            ExpenseReimbursement.objects.filter(receipt_id=line.receipt_id).delete()
    claim.approved = False
    claim.save(update_fields=["approved"])
