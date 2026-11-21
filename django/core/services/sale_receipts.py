from datetime import date
from decimal import Decimal
import re

from django.db import transaction
from django.db.models import Q

from core.common import BusinessError, decimal, iso_date, money, stamp, today, uid
from core.models import Document, SaleReceipt
from .documents import INVOICE_TYPES, event


def is_sale(document):
    return document.direction == "outgoing" and document.type in INVOICE_TYPES


def eligible(company, direction, currency, amount):
    docs = Document.objects.filter(
        company_uid=company.company_uid,
        direction=direction,
        currency_code=currency,
        type__in=INVOICE_TYPES,
    )
    open_docs = Q(
        status__in=["sent", "accepted", "received", "disputed"], amount_due__gte=amount
    )
    if direction == "outgoing":
        pending = SaleReceipt.objects.filter(
            document__company_uid=company.company_uid,
            cancelled=False,
            transaction__isnull=True,
            amount=amount,
        )
        open_docs |= Q(
            status__in=["sent", "paid", "accepted"],
            pk__in=pending.values("document_id"),
        )
    return docs.filter(open_docs)


def apply_amount(document, amount, day):
    if amount <= 0 or amount > document.amount_due:
        raise BusinessError(
            "Le montant doit être positif et inférieur ou égal au reste dû.", 409
        )
    document.amount_due = money(document.amount_due - amount)
    if not document.amount_due:
        document.status = "paid"
        document.paid_at = str(day) + " 00:00:00"
    document.updated_at = stamp()
    document.save(update_fields=["amount_due", "paid_at", "status", "updated_at"])


@transaction.atomic
def declare(document, amount, day, nonce):
    document = Document.objects.select_for_update().get(pk=document.pk)
    if not is_sale(document) or document.status not in ("sent", "accepted", "paid"):
        raise BusinessError(
            "Seule une facture client émise peut recevoir un règlement.", 409
        )
    day = iso_date(day or today())
    if day > today():
        raise BusinessError("La date d’encaissement ne peut pas être future.")
    if not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{32}", nonce):
        raise BusinessError("Rouvrez la fiche pour enregistrer ce règlement.")
    value = decimal(amount)
    if Decimal(str(amount)) != money(value) or value <= 0:
        raise BusinessError("Indiquez un montant positif avec au plus deux décimales.")
    previous = SaleReceipt.objects.filter(receipt_uid=nonce).first()
    if previous:
        if (
            previous.document_id != document.pk
            or previous.amount != value
            or str(previous.received_on) != day
        ):
            raise BusinessError("Cette saisie de règlement a déjà été utilisée.", 409)
        return previous
    apply_amount(document, value, day)
    receipt = SaleReceipt.objects.create(
        document=document,
        amount=value,
        received_on=day,
        receipt_uid=nonce,
        origin="manual",
    )
    event(
        document,
        "receipt_declared",
        "Encaissement déclaré",
        {"receipt_uid": nonce, "amount": value, "date": day},
    )
    return receipt


def confirm_or_record(document, tx, choice, automatic):
    pending = document.receipts.select_for_update().filter(
        transaction__isnull=True, cancelled=False, amount=tx.amount
    )
    if choice == "new":
        receipt = None
    elif choice:
        receipt = pending.filter(receipt_uid=choice).first()
        if not receipt:
            raise BusinessError(
                "Ce règlement n’est plus disponible pour cette facture.", 409
            )
    else:
        matches = list(pending[:2])
        if len(matches) > 1:
            raise BusinessError(
                "Plusieurs règlements correspondent. Sélectionnez le versement à confirmer.",
                409,
            )
        receipt = matches[0] if matches else None
        if (
            receipt
            and automatic
            and (
                not receipt.received_on
                or abs(
                    (date.fromisoformat(tx.transaction_date) - receipt.received_on).days
                )
                > 7
            )
        ):
            raise BusinessError(
                "Vérifiez la date du versement avant de le rapprocher.", 409
            )
    if receipt:
        receipt.transaction = tx
        receipt.received_on = receipt.received_on or tx.transaction_date
        receipt.save(update_fields=["transaction", "received_on"])
    else:
        apply_amount(document, tx.amount, tx.transaction_date)
        receipt = SaleReceipt.objects.create(
            document=document,
            transaction=tx,
            amount=tx.amount,
            received_on=tx.transaction_date,
            origin="bank",
        )
    return receipt


@transaction.atomic
def cancel_declaration(document, receipt_uid):
    document = Document.objects.select_for_update().get(pk=document.pk)
    receipt = (
        document.receipts.select_for_update()
        .filter(receipt_uid=receipt_uid, origin="manual")
        .first()
    )
    if not receipt or receipt.transaction_id:
        raise BusinessError(
            "Seul un règlement déclaré et encore à confirmer peut être annulé.", 409
        )
    if receipt.cancelled:
        return
    if document.amount_due + receipt.amount > document.total_including_tax:
        raise BusinessError("Le solde a changé. Vérifiez les autres règlements.", 409)
    receipt.cancelled = True
    receipt.save(update_fields=["cancelled"])
    document.amount_due += receipt.amount
    if document.status == "paid":
        document.status, document.paid_at = "sent", None
    document.updated_at = stamp()
    document.save(update_fields=["amount_due", "status", "paid_at", "updated_at"])
    event(
        document,
        "receipt_cancelled",
        "Déclaration d’encaissement annulée",
        {"receipt_uid": receipt_uid},
    )


def mark_balance_paid(document):
    if document.amount_due:
        declare(document, document.amount_due, today(), uid())
    document.refresh_from_db()
