from datetime import datetime, time, timezone

from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.utils import timezone as localtime

from core.common import BusinessError, digest, email, today
from core.models import Document, Email
from core.numbers import document_style
from . import mail
from .documents import INVOICE_TYPES, event


def overdue(doc):
    return (
        doc.direction == "outgoing"
        and doc.type in INVOICE_TYPES
        and doc.status in {"sent", "accepted"}
        and doc.amount_due > 0
        and doc.due_date
        and doc.due_date < today()
        and doc.issue_date
        and doc.issue_date <= today()
    )


def still_current(item):
    doc = Document.objects.filter(
        company_uid=item.company_uid, document_uid=item.document_uid
    ).first()
    return bool(
        doc
        and overdue(doc)
        and doc.amount_due == item.reminder_balance
        and (doc.buyer.get("email") or "").strip().lower() == item.recipient_email
    )


def prepare(doc):
    if not overdue(doc):
        raise BusinessError("La facture est soldée ou ne présente plus de retard.")
    recipient = doc.buyer.get("email")
    if not recipient:
        raise BusinessError("Adresse e-mail de facturation absente.")
    email(recipient)
    start = localtime.make_aware(datetime.combine(localtime.localdate(), time.min))
    utc_start = start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    previous = Email.objects.filter(
        company_uid=doc.company_uid,
        document_uid=doc.document_uid,
        template="invoice_reminder",
    )
    if (
        previous.filter(status="queued")
        .filter(Q(reminder_balance=doc.amount_due) | Q(reminder_balance__isnull=True))
        .exists()
    ):
        raise BusinessError("Une relance est déjà en file d’attente.")
    if (
        previous.filter(status="sent")
        .filter(Q(sent_at__gte=utc_start) | Q(created_at__gte=utc_start))
        .exists()
    ):
        raise BusinessError("Une relance a déjà été envoyée aujourd’hui.")
    subject = "Relance facture " + doc.number
    balance = f"{document_style(doc).format(doc.amount_due, 2)} {doc.currency_code}"
    body = f"Bonjour,\n\nLa facture {doc.number}, échue le {doc.due_date}, présente un solde restant à régler de {balance}."
    return (
        doc.company_uid,
        doc.document_uid,
        "invoice_reminder",
        recipient,
        subject,
        body,
    )


def fingerprint(doc, item):
    return digest(
        "\0".join(
            (
                doc.document_uid,
                str(doc.amount_due),
                doc.currency_code,
                item.recipient_email,
                item.subject,
                item.body_text,
            )
        )
    )


def preview(doc):
    args = prepare(doc)
    item = mail.compose(*args, reminder_balance=doc.amount_due)
    return item, fingerprint(doc, item)


@transaction.atomic
def enqueue(company_uid, document_uid, expected=None):
    doc = Document.objects.select_for_update().get(
        company_uid=company_uid, document_uid=document_uid
    )
    args = prepare(doc)
    item = mail.queue(*args, reminder_balance=doc.amount_due)
    if expected is not None and fingerprint(doc, item) != expected:
        raise BusinessError(
            "Le solde, le destinataire ou le message a changé. Vérifiez le nouvel aperçu.",
            409,
        )
    event(
        doc,
        "reminder",
        "Relance mise en file d’attente",
        {"email_uid": item.email_uid, "amount_due": str(doc.amount_due)},
    )
    return item


def token(request, choices):
    return signing.dumps(
        [request.company.company_uid, request.user.user_uid, today(), choices],
        salt="invoice-reminder-review",
        compress=True,
    )


def reviewed(request):
    try:
        company, user, day, choices = signing.loads(
            request.POST.get("review", ""), salt="invoice-reminder-review", max_age=1800
        )
    except signing.BadSignature as exc:
        raise BusinessError(
            "L’aperçu a expiré. Sélectionnez à nouveau les factures.", 409
        ) from exc
    if [company, user, day] != [
        request.company.company_uid,
        request.user.user_uid,
        today(),
    ]:
        raise BusinessError("L’aperçu ne correspond plus à la session.", 409)
    return choices
