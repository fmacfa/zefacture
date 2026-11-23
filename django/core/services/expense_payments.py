from core.services import expense_claims
from django.db import transaction

from core.common import BusinessError, json_text, unpack
from core.models import Document, Ingestion
from core.payment_details import PaidBy, PaymentMethod, payment_fields
from .receipt_extraction import EXPENSE_KINDS


def mark_expense_fields(record, changes):
    """Remember explicit blanks as well as corrections across future analyses."""
    if not isinstance(record, Ingestion) or record.source_kind not in EXPENSE_KINDS:
        return
    fields = changes.keys() & {"expense_category", "expense_payment_method", "expense_paid_by", "expense_description"}
    if fields:
        from .invoice_imports import reviewed_fields

        data = unpack(record.extraction_json)
        if not isinstance(data, dict):
            data = {}
        data["_reviewed"] = sorted(reviewed_fields(data) | fields)
        changes["extraction_json"] = json_text(data)


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
    if changes and isinstance(record, Ingestion) and record.source_kind in EXPENSE_KINDS:
        from .jobs import require_idle

        require_idle(record)
        expense_claims.require_editable(record)
    mark_expense_fields(record, changes)
    for field, value in changes.items():
        setattr(record, field, value)
    if changes:
        record.save(update_fields=list(changes))
