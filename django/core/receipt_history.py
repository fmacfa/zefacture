"""Compatibility with databases and archives that predate the sale receipt ledger."""

from datetime import date


def known_day(value):
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def fill_receipt_history(apps, alias):
    """Project existing sale balances into the receipt ledger, preserving original rows and files."""
    Document = apps.get_model("core", "Document")
    Receipt = apps.get_model("core", "SaleReceipt")
    banks = apps.get_model("core", "BankTransaction").objects.using(alias)
    docs = Document.objects.using(alias).filter(
        direction="outgoing",
        type__in=["invoice", "progress_invoice"],
        status__in=["sent", "accepted", "paid"],
        receipts__isnull=True,
    )
    receipts = Receipt.objects.using(alias)
    for doc in docs.iterator():
        recorded = 0
        for tx in banks.filter(
            company_uid=doc.company_uid,
            document_uid=doc.document_uid,
            direction="incoming",
            status="booked",
            amount__gt=0,
        ).iterator():
            receipts.create(
                document_id=doc.pk,
                amount=tx.amount,
                received_on=known_day(tx.transaction_date),
                origin="bank",
                transaction_id=tx.pk,
            )
            recorded += tx.amount
        remaining = doc.total_including_tax - doc.amount_due - recorded
        if remaining > 0:
            day = known_day(doc.paid_at)
            receipts.create(
                document_id=doc.pk, amount=remaining, received_on=day, origin="legacy"
            )
