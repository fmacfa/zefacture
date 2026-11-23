import csv
import io
from collections import defaultdict
from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from core.numbers import current_style
from core.common import (
    BusinessError,
    money,
    decimal,
    iso_date,
    today,
    stamp,
)
from core.models import (
    BankAccount,
    BankFeedEntry,
    BankTransaction,
    Document,
)
from .documents import event
from . import bank_categories, sale_receipts


@transaction.atomic
def reconcile(company, bank_transaction, document_uid, *, exact=False, receipt_choice=""):
    feed_entry = (
        BankFeedEntry.objects.select_for_update()
        .filter(transaction=bank_transaction)
        .first()
    )
    if feed_entry and feed_entry.review:
        raise BusinessError("Vérifiez d’abord la correction bancaire.", 409)
    bank_transaction = BankTransaction.objects.select_for_update().get(
        pk=bank_transaction.pk, company_uid=company.company_uid
    )
    document = (
        Document.objects.select_for_update()
        .filter(company_uid=company.company_uid, document_uid=document_uid)
        .first()
    )
    if bank_transaction.status != "booked":
        raise BusinessError(
            "Seule une opération comptabilisée peut être rapprochée.", 409
        )
    if bank_transaction.standalone_category:
        raise BusinessError(
            "Remettez l’opération à traiter avant de la rapprocher d’une facture.", 409
        )
    if bank_transaction.document_uid or bank_transaction.allocations.exists():
        raise BusinessError("Transaction déjà rapprochée.", 409)
    expected = (
        "incoming" if document and document.direction == "outgoing" else "outgoing"
    )
    if (
        not document
        or document.type not in {"invoice", "progress_invoice"}
        or document.status in {"draft", "cancelled", "refused", "simulated"}
        or document.status == "paid" and not sale_receipts.is_sale(document)
    ):
        raise BusinessError("Facture non rapprochable.", 409)
    sale = sale_receipts.is_sale(document)
    if (
        bank_transaction.direction != expected or bank_transaction.amount <= 0
        or not sale and bank_transaction.amount > document.amount_due
        or not sale and exact and (bank_transaction.amount != document.amount_due
                                   or document.status not in {"sent", "received", "accepted"})
    ):
        raise BusinessError("Sens incompatible ou paiement supérieur au solde.", 409)
    account = BankAccount.objects.filter(
        account_uid=bank_transaction.account_uid, company_uid=company.company_uid
    ).first()
    if document.currency_code != (account.currency_code if account else "EUR"):
        raise BusinessError("Les devises du compte et de la facture diffèrent.")
    if feed_entry:
        feed_entry.previous_invoice_status = document.status
        feed_entry.save(update_fields=["previous_invoice_status"])
    if sale:
        sale_receipts.confirm_or_record(document, bank_transaction, receipt_choice, exact)
    else:
        document.amount_due = money(document.amount_due - bank_transaction.amount)
        if not document.amount_due:
            document.status, document.paid_at = "paid", stamp()
        document.updated_at = stamp()
        document.save()
    bank_transaction.document_uid = document_uid
    bank_transaction.updated_at = stamp()
    bank_transaction.save()
    event(
        document,
        "bank_payment",
        "Paiement rapproché",
        {
            "amount": bank_transaction.amount,
            "remaining": document.amount_due,
            "transaction_uid": bank_transaction.transaction_uid,
        },
    )


@transaction.atomic
def classify(company, item, category, note):
    feed_entry = (
        BankFeedEntry.objects.select_for_update().filter(transaction=item).first()
    )
    item = BankTransaction.objects.select_for_update().get(
        pk=item.pk, company_uid=company.company_uid
    )
    if (
        item.document_uid
        or item.allocations.exists()
        or item.status != "booked"
        or (feed_entry and feed_entry.review)
    ):
        raise BusinessError(
            "Seule une opération comptabilisée, disponible et sans facture peut être classée.",
            409,
        )
    item.standalone_category, item.classification_note = bank_categories.checked(
        category, note, item.direction
    )
    item.updated_at = stamp()
    item.save(
        update_fields=["standalone_category", "classification_note", "updated_at"]
    )


@transaction.atomic
def create_transaction(company, data):
    amount = money(decimal(data.get("amount")))
    direction = data.get("direction")
    label = str(data.get("label", "")).strip()
    if amount <= 0 or direction not in {"incoming", "outgoing"} or not label:
        raise BusinessError("Montant positif, libellé et sens requis.")
    category, note = bank_categories.checked(
        data.get("standalone_category"), data.get("classification_note"), direction
    )
    if category and data.get("document_uid"):
        raise BusinessError(
            "Choisissez une catégorie sans facture ou une facture à rapprocher."
        )
    account = None
    if data.get("account_uid"):
        account = (
            BankAccount.objects.select_for_update()
            .filter(
                company_uid=company.company_uid,
                account_uid=data["account_uid"],
                status="active",
            )
            .first()
        )
        if not account:
            raise BusinessError("Compte bancaire introuvable.")
    item = BankTransaction.objects.create(
        company_uid=company.company_uid,
        account_uid=account.account_uid if account else None,
        transaction_date=iso_date(data.get("transaction_date") or today()),
        label=label,
        amount=amount,
        direction=direction,
        category=data.get("category") or "other",
        standalone_category=category,
        classification_note=note,
        external_id=data.get("external_id") or None,
        import_hash=data.get("import_hash"),
        imported_at=stamp() if data.get("import_hash") else None,
    )
    if account and not hasattr(account, "bank_feed"):
        account.current_balance += amount if direction == "incoming" else -amount
        account.updated_at = stamp()
        account.save()
    if data.get("document_uid"):
        reconcile(company, item, data["document_uid"])
    return item


def import_csv(company, account_uid, raw):
    from .bank_statements import import_file

    return import_file(company, account_uid, "csv", raw)


def csv_text(headers, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter=";", lineterminator="\r\n")

    def safe(value):
        if current_style().customized and type(value) in (Decimal, int, float):
            return current_style().format(value)
        text = str(value if value is not None else "")
        return (
            "'" + text
            if isinstance(value, str)
            and text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r"))
            else text
        )

    writer.writerow(headers)
    for row in rows:
        writer.writerow([safe(value) for value in row])
    return "\ufeff" + stream.getvalue()


def effective_documents(company):
    return (
        Document.objects.filter(company_uid=company.company_uid)
        .exclude(type="quote")
        .exclude(status__in=["draft", "cancelled", "refused", "simulated"])
    )


def vat_rows(company, start, end):
    rows = []
    for document in effective_documents(company).filter(
        issue_date__gte=start, issue_date__lte=end
    ):
        sign = Decimal(-1) if document.type == "credit_note" else Decimal(1)
        # Preserve the legacy management statement: period of invoice issuance.
        # It is not an automatic tax return or a cash-basis VAT calculation.
        ratio = Decimal(1)
        groups = defaultdict(lambda: [Decimal(0), Decimal(0)])
        for line in document.lines:
            groups[line.vat_rate][0] += line.total_excluding_tax
            groups[line.vat_rate][1] += line.total_vat
        for rate, (net, vat) in groups.items():
            rows.append(
                [
                    document.issue_date,
                    document.number,
                    document.direction,
                    document.currency_code,
                    rate,
                    money(net * sign * ratio),
                    money(vat * sign * ratio),
                ]
            )
    return rows


def vat_payments(company, start, end):
    currencies = dict(
        BankAccount.objects.filter(company_uid=company.company_uid).values_list(
            "account_uid", "currency_code"
        )
    )
    rows = (
        BankTransaction.objects.filter(
            company_uid=company.company_uid,
            status="booked",
            transaction_date__range=(start, end),
            standalone_category__in=("vat_payment", "vat_refund"),
        )
        .values("account_uid", "standalone_category")
        .annotate(total=Sum("amount"))
    )
    totals = defaultdict(lambda: {"vat_payment": Decimal(0), "vat_refund": Decimal(0)})
    for row in rows:
        totals[currencies.get(row["account_uid"], "EUR")][
            row["standalone_category"]
        ] += row["total"]
    return [
        {"currency": currency, **amounts}
        for currency, amounts in sorted(totals.items())
    ]
