import hashlib
import hmac
import json
import time
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from core.common import uid, BusinessError
from core.models import WebhookEndpoint, WebhookDelivery
from .files import decrypt
from .network import https_request

EVENTS = {
    "customer.created": "Client créé",
    "customer.updated": "Client modifié",
    "document.created": "Document créé",
    "document.updated": "Document modifié",
    "document.status_changed": "Statut d'un document modifié",
    "stock.movement": "Mouvement de stock",
    "crm.opportunity.created": "Opportunité créée",
    "crm.opportunity.updated": "Opportunité modifiée",
    "crm.activity.created": "Activité CRM créée",
    "email.sent": "Email accepté par la messagerie",
}


def emit(company_uid, event_type, data, endpoint=None):
    if not company_uid:
        return
    endpoints = (
        [endpoint]
        if endpoint
        else WebhookEndpoint.objects.filter(company_uid=company_uid, enabled=True)
    )
    event_uid = uid()
    payload = {
        "id": event_uid,
        "type": event_type,
        "created_at": timezone.now().isoformat(),
        "company_uid": company_uid,
        "data": data,
    }
    # Materialize only when subscribed; unrelated business saves do not depend on
    # serialization or on an external service. Deliveries share the transaction.
    for target in endpoints:
        if target.company_uid != company_uid or not target.enabled:
            continue
        if not endpoint and event_type not in target.events:
            continue
        WebhookDelivery.objects.create(
            endpoint_uid=target.endpoint_uid,
            company_uid=company_uid,
            event_uid=event_uid,
            event_type=event_type,
            payload=json.loads(json.dumps(payload, default=str)),
        )


def flush(limit=20):
    if settings.EXTERNAL_SERVICES_DISABLED or getattr(
        settings, "WEBHOOK_DELIVERY_DISABLED", False
    ):
        return 0
    # A worker lost after sending may be retried: consumers deduplicate by id.
    WebhookDelivery.objects.filter(
        status="sending", available_at__lt=timezone.now()
    ).update(status="queued")
    count = 0
    for _ in range(limit):
        with transaction.atomic():
            item = (
                WebhookDelivery.objects.select_for_update(skip_locked=True)
                .filter(status="queued", available_at__lte=timezone.now())
                .order_by("id")
                .first()
            )
            if not item:
                break
            target = WebhookEndpoint.objects.filter(
                endpoint_uid=item.endpoint_uid, company_uid=item.company_uid
            ).first()
            if not target or not target.enabled:
                item.status = "paused"
                item.save(update_fields=["status"])
                continue
            item.status = "sending"
            item.attempts += 1
            item.available_at = timezone.now() + timedelta(minutes=10)
            item.save()
            target_url, encrypted_secret = target.url, target.secret_encrypted
        try:
            raw = json.dumps(
                item.payload, ensure_ascii=False, separators=(",", ":")
            ).encode()
            timestamp = str(int(time.time()))
            signature = hmac.new(
                decrypt(encrypted_secret).encode(),
                timestamp.encode() + b"." + raw,
                hashlib.sha256,
            ).hexdigest()
            status, _ = https_request(
                target_url,
                body=raw,
                headers={
                    "Content-Type": "application/json",
                    "X-ZeFacture-Id": item.event_uid,
                    "X-ZeFacture-Event": item.event_type,
                    "X-ZeFacture-Signature": f"t={timestamp},v1={signature}",
                    "User-Agent": "ZeFacture-Webhook/1",
                },
                max_bytes=65536,
            )
            item.last_status_code = status
            if not 200 <= status < 300:
                raise BusinessError(f"Le destinataire a répondu HTTP {status}.")
            item.status, item.last_error, item.delivered_at = "sent", "", timezone.now()
            count += 1
        except Exception as exc:
            item.status = "failed" if item.attempts >= 6 else "queued"
            item.available_at = timezone.now() + timedelta(
                seconds=min(3600, 60 * 2 ** min(item.attempts - 1, 6))
            )
            item.last_error = (
                str(exc)[:500]
                if isinstance(exc, BusinessError)
                else "Échec de livraison. Vérifiez la destination et son certificat."
            )
        item.save()
    return count
