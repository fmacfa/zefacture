from django.db import transaction

from core.common import BusinessError
from core.models import Document, Ingestion
from core.payment_details import PaidBy, PaymentMethod, payment_fields


def for_document(document, *, lock=False):
    # The original import owns the expense details, including its legacy description.
    sources = Ingestion.objects.filter(
        company_uid=document.company_uid, document_uid=document.document_uid
    )
    if lock:
        sources = sources.select_for_update()
    return sources.order_by("pk").first() or document


def context(record, *, purchase=True):
    return {
        "payment_record": record,
        "payment_methods": PaymentMethod.choices,
        "payment_payers": PaidBy.choices,
        "is_purchase": purchase,
    }


@transaction.atomic
def save(record, data, *, purchase=True):
    record = type(record).objects.select_for_update().get(pk=record.pk)
    if isinstance(record, Document):
        if record.type not in {"invoice", "progress_invoice"}:
            raise BusinessError("Le suivi du règlement est réservé aux factures.")
        record = for_document(record, lock=True)
    if getattr(record, "archived_at", None):
        raise BusinessError(
            "Restaurez le justificatif pour modifier son règlement.", 409
        )
    changes = payment_fields(data, purchase=purchase)
    for field, value in changes.items():
        setattr(record, field, value)
    if changes:
        record.save(update_fields=list(changes))
