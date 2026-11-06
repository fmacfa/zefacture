import os
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from core.common import email, stamp, BusinessError
from core.models import Email


@transaction.atomic
def queue(company_uid, document_uid, template, recipient, subject, body):
    from .email_connections import route_email

    item = Email(
        company_uid=company_uid,
        document_uid=document_uid,
        template=template,
        recipient_email=email(recipient),
        sender_email=settings.DEFAULT_FROM_EMAIL,
        sender_name=settings.MAIL_FROM_NAME,
        subject=subject,
        body_text=body,
        transport=settings.MAIL_TRANSPORT,
        status="queued",
    )
    if company_uid:
        from .branding import apply_mail

        apply_mail(item)
    route_email(item)
    item.save()
    return item


def deliver(item):
    if settings.EXTERNAL_SERVICES_DISABLED or settings.EMAIL_DELIVERY_DISABLED:
        raise BusinessError("Les envois sont désactivés sur cette instance.")
    if item.transport == "outbox":
        return False
    attachment = None
    if item.document_uid and item.template in {"invoice", "quote"}:
        from core.models import Document
        from .files import read_verified

        document = Document.objects.get(
            document_uid=item.document_uid, company_uid=item.company_uid
        )
        original = document.files.filter(kind__in=["facturx", "pdf"]).first()
        if not original:
            raise BusinessError("PDF original manquant.")
        attachment = (
            document.type + "-" + document.document_uid + ".pdf",
            read_verified(original),
        )
    message = EmailMultiAlternatives(
        subject=item.subject,
        body=item.body_text,
        from_email=f"{item.sender_name} <{item.sender_email}>",
        to=[item.recipient_email],
    )
    from core.models import EmailEnvelope

    envelope = EmailEnvelope.objects.filter(email_uid=item.email_uid).first()
    if envelope and envelope.reply_to:
        message.reply_to = [envelope.reply_to]
    if item.body_html:
        message.attach_alternative(item.body_html, "text/html")
    if attachment:
        message.attach(attachment[0], attachment[1], "application/pdf")
    if item.transport == "company":
        from .email_connections import deliver as deliver_company

        deliver_company(item, message)
    elif item.transport == "smtp":
        if not message.send(fail_silently=False):
            raise BusinessError("Le serveur SMTP n'a pas accepté l'email.")
    elif item.transport == "resend":
        import requests

        key = os.getenv("RESEND_API_KEY", "")
        if not key:
            raise BusinessError("RESEND_API_KEY manquante.")
        payload = {
            "from": f"{item.sender_name} <{item.sender_email}>",
            "to": [item.recipient_email],
            "subject": item.subject,
            "text": item.body_text,
        }
        if message.reply_to:
            payload["reply_to"] = message.reply_to
        if attachment:
            import base64

            payload["attachments"] = [
                {
                    "filename": attachment[0],
                    "content": base64.b64encode(attachment[1]).decode(),
                }
            ]
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": "Bearer " + key,
                    "Idempotency-Key": item.email_uid,
                },
                json=payload,
                timeout=(5, 20),
                allow_redirects=False,
            )
        if response.status_code not in (200, 201):
            raise BusinessError(
                f"Resend a refusé l'email (HTTP {response.status_code})."
            )
        item.provider_message_id = response.json().get("id")
    else:
        raise BusinessError("Transport email inconnu.")
    item.status, item.sent_at, item.last_error = "sent", stamp(), None
    item.save()
    return True


def flush(limit=50):
    if settings.EXTERNAL_SERVICES_DISABLED or getattr(
        settings, "EMAIL_DELIVERY_DISABLED", False
    ):
        return 0
    query = Email.objects.filter(status="queued").exclude(transport="outbox")
    if settings.MAIL_TRANSPORT == "outbox":
        query = query.filter(transport="company")
    count = 0
    for pk in list(query.values_list("pk", flat=True)[:limit]):
        with transaction.atomic():
            item = (
                Email.objects.select_for_update(skip_locked=True)
                .filter(pk=pk, status="queued")
                .first()
            )
            if not item:
                continue
            try:
                if deliver(item):
                    count += 1
            except Exception as exc:
                item.status = "failed"
                item.last_error = (
                    str(exc)[:500]
                    if isinstance(exc, BusinessError)
                    else "L'envoi a échoué. Vérifiez la messagerie et ses identifiants, puis relancez le message."
                )
                item.save()
    return count
