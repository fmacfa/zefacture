import hmac
import json
import os
import secrets
import time
from datetime import timedelta
from urllib.parse import urlencode
from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.http import require_POST
from core.common import BusinessError, stamp, digest, json_text
from core.models import (
    Company,
    Document,
    InvoiceEvent,
    WebhookReceipt,
    OAuthAttempt,
    PdpInvoice,
    PdpOutbox,
    PdpSession,
)
from core.security import scoped, scoped_get, ROLES
from core.services import pdp as service
from core.services import pdp_sync
from core.services.account_security import audit
from core.services.files import encrypt


@scoped("admin")
def pdp(request, action=None):
    session = service.session_for(request.company)
    sync = pdp_sync.state_for(request.company)
    redirect_uri = (
        os.getenv("SUPERPDP_OAUTH_REDIRECT_URI")
        or settings.APP_URL + "/app/entreprise/pdp/callback"
    )
    if action == "connecter" and request.method == "POST":
        if settings.EXTERNAL_SERVICES_DISABLED:
            raise BusinessError(
                "Les services externes sont désactivés sur cette instance.", 503
            )
        client_id = os.getenv("SUPERPDP_OAUTH_CLIENT_ID", "")
        if not client_id:
            raise BusinessError(
                "Configurez l'application OAuth SUPERPDP sur le serveur."
            )
        state = secrets.token_urlsafe(32)
        request.session["pdp_oauth"] = {
            "state": state,
            "company_uid": request.company.company_uid,
            "created": time.time(),
            "user_uid": request.user.user_uid,
            "version": sync.version,
            "client": digest(
                json_text([client_id, os.getenv("SUPERPDP_OAUTH_CLIENT_SECRET", "")])
            ),
        }
        if not os.getenv("SUPERPDP_OAUTH_CLIENT_SECRET", ""):
            raise BusinessError(
                "Paire Client ID / secret SUPERPDP requise sur le serveur."
            )
        OAuthAttempt.objects.create(
            state_hash=digest(state),
            company_uid=request.company.company_uid,
            user_uid=request.user.user_uid,
            provider="superpdp",
            version=sync.version,
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        query = {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "state": state,
            "login_hint": request.user.email,
        }
        if request.company.siren:
            query.update(
                superpdp_company_number=request.company.siren,
                superpdp_company_number_scheme="fr_siren",
            )
        return redirect(
            service.base(session.api_base) + "/oauth2/authorize?" + urlencode(query)
        )
    if action == "callback" and request.method == "GET":
        state = request.session.pop("pdp_oauth", {})
        if (
            not state
            or not hmac.compare_digest(
                state.get("state", ""), request.GET.get("state", "")
            )
            or state.get("company_uid") != request.company.company_uid
            or state.get("user_uid") != request.user.user_uid
            or state.get("version") != sync.version
            or state.get("client")
            != digest(
                json_text(
                    [
                        os.getenv("SUPERPDP_OAUTH_CLIENT_ID", ""),
                        os.getenv("SUPERPDP_OAUTH_CLIENT_SECRET", ""),
                    ]
                )
            )
            or not 0 <= time.time() - state.get("created", 0) < 600
        ):
            raise BusinessError("Session OAuth expirée ou invalide.", 403)
        if (
            OAuthAttempt.objects.filter(
                state_hash=digest(state["state"]),
                company_uid=request.company.company_uid,
                user_uid=request.user.user_uid,
                provider="superpdp",
                version=sync.version,
                expires_at__gt=timezone.now(),
                consumed_at__isnull=True,
            ).update(consumed_at=timezone.now())
            != 1
        ):
            raise BusinessError(
                "Cette autorisation SUPERPDP a déjà été consommée.", 403
            )
        if not request.GET.get("code"):
            raise BusinessError("Autorisation OAuth refusée.")
        data = service.request(
            session,
            "POST",
            "/oauth2/token",
            form={
                "grant_type": "authorization_code",
                "code": request.GET["code"],
                "redirect_uri": redirect_uri,
                "client_id": os.getenv("SUPERPDP_OAUTH_CLIENT_ID", ""),
                "client_secret": os.getenv("SUPERPDP_OAUTH_CLIENT_SECRET", ""),
            },
        )
        session.refresh_token_encrypted = None
        service.save_token(session, data)
        sync.auth_mode = "oauth"
        sync.version += 1
        sync.save(update_fields=["auth_mode", "version"])
        service.verify(request.company, session)
        audit(request, "pdp.connected", session.session_uid)
        messages.success(request, "Compte SUPERPDP connecté.")
        return redirect("/app/entreprise/pdp")
    if request.method == "POST":
        operation = request.POST.get("action", "")
        if operation == "sync":
            pdp_sync.request_sync(
                request.company, replay=request.POST.get("replay") == "1"
            )
            audit(request, "pdp.sync_requested")
            messages.success(
                request,
                "Synchronisation demandée ; le worker récupérera les factures, originaux et statuts.",
            )
        elif operation == "automatic":
            if request.POST.get("enabled") == "1":
                pdp_sync._binding(request.company, session, verify=True)
            pdp_sync.request_sync(
                request.company, enabled=request.POST.get("enabled") == "1"
            )
            audit(request, "pdp.sync_policy_changed")
        elif operation == "retry":
            pdp_sync.retry_failed(
                request.company, request.POST.get("operation_uid", "")
            )
            audit(request, "pdp.retry_requested", request.POST.get("operation_uid", ""))
        elif operation == "retry_uncertain":
            pdp_sync.retry_uncertain(
                request.company,
                request.POST.get("operation_uid", ""),
                confirmed=request.POST.get("confirmed_absent") == "1",
            )
            audit(
                request,
                "pdp.uncertain_retry_authorized",
                request.POST.get("operation_uid", ""),
            )
        elif operation == "disconnect":
            sync.enabled, sync.requested, sync.version = False, False, sync.version + 1
            sync.save(update_fields=["enabled", "requested", "version"])
            session.access_token_encrypted = session.refresh_token_encrypted = (
                session.client_secret_encrypted
            ) = None
            session.status = "not_configured"
            session.save()
            audit(request, "pdp.disconnected", session.session_uid)
        elif operation == "verify":
            service.verify(request.company, session)
        else:
            session.api_base = service.base(
                request.POST.get("api_base") or "https://api.superpdp.tech"
            )
            new_client_id = request.POST.get("client_id") or session.client_id
            if (
                new_client_id != session.client_id
                and session.client_secret_encrypted
                and not request.POST.get("client_secret")
            ):
                raise BusinessError(
                    "Ressaisir le secret associé au nouveau Client ID SUPERPDP."
                )
            credentials_changed = bool(
                request.POST.get("client_secret")
                or request.POST.get("access_token")
                or new_client_id != session.client_id
            )
            session.client_id = new_client_id
            if credentials_changed:
                session.access_token_encrypted = session.refresh_token_encrypted = None
                session.company_verification_status = "needs_review"
            for source, destination in (
                ("client_secret", "client_secret_encrypted"),
                ("access_token", "access_token_encrypted"),
            ):
                if request.POST.get(source):
                    setattr(session, destination, encrypt(request.POST[source]))
            if request.POST.get("sandbox") == "1":
                if settings.ENVIRONMENT == "production":
                    raise BusinessError("Simulation indisponible en production.")
                session.status = "sandbox"
            else:
                session.status = "configured"
            session.updated_at = stamp()
            session.save()
            sync.auth_mode = "technical" if session.client_id else "auto"
            sync.version += 1
            sync.save(update_fields=["auth_mode", "version"])
            audit(request, "pdp.configuration_saved", session.session_uid)
        return redirect("/app/entreprise/pdp")
    operations = list(
        PdpOutbox.objects.filter(company_uid=request.company.company_uid).order_by(
            "-id"
        )[:50]
    )
    for operation in operations:
        operation.status_label = pdp_sync.OUTBOX_LABELS.get(
            operation.status, operation.status
        )
    return render(
        request,
        "core/pdp.html",
        {
            "title": "Connexion SUPERPDP",
            "pdp": session,
            "redirect_uri": redirect_uri,
            "webhook_url": settings.APP_URL + "/webhooks/superpdp",
            "allow_sandbox": settings.ENVIRONMENT != "production",
            "sync": sync,
            "sync_interval": settings.SUPERPDP_SYNC_INTERVAL,
            "operations": operations,
            "invoice_count": PdpInvoice.objects.filter(
                company_uid=request.company.company_uid
            ).count(),
        },
    )


@scoped("write")
@require_POST
def document_pdp(request, document_uid):
    document = scoped_get(Document, request, document_uid=document_uid)
    if request.POST.get("action") == "sync":
        pdp_sync.request_sync(request.company)
        messages.success(request, "Actualisation SUPERPDP demandée.")
        audit(request, "pdp.sync_requested", document_uid)
    else:
        code = request.POST.get("status_code", "")
        if code in {"fr:211", "fr:212"} and request.role not in ROLES["finance"]:
            raise BusinessError(
                "Un rôle financier est requis pour déclarer un règlement.", 403
            )
        operation = pdp_sync.queue_status(
            document, code, request.POST.get("reason", ""), request.POST.get("note", "")
        )
        messages.success(
            request,
            "Déclaration SUPERPDP préparée ; son résultat figure dans le suivi.",
        )
        audit(request, "pdp.lifecycle_queued", operation.operation_uid)
    return redirect(document.url)


@csrf_exempt
def webhook(request):
    if request.method != "POST":
        return HttpResponse(status=405)
    secret = os.getenv("SUPERPDP_WEBHOOK_SECRET", "")
    if not secret:
        return JsonResponse({"error": "webhook_not_configured"}, status=503)
    if not hmac.compare_digest(
        secret, request.headers.get("X-ZeFacture-Webhook-Secret", "")
    ):
        return JsonResponse({"error": "unauthorized"}, status=401)
    try:
        data = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "JSON invalide"}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"error": "Objet JSON requis"}, status=400)

    def pick(keys, node=None):
        node = data if node is None else node
        if not isinstance(node, dict):
            return ""
        for key in keys:
            if isinstance(node.get(key), (str, int)):
                return str(node[key])
        for child in node.values():
            value = pick(keys, child) if isinstance(child, dict) else ""
            if value:
                return value
        return ""

    invoice_id = pick(["pdp_invoice_id", "invoice_id", "invoiceId"])
    external = pick(["external_id", "externalId"])
    document_uid = pick(["document_uid", "documentUid"])
    query = (
        Document.objects.filter(pdp_invoice_id=invoice_id)
        if invoice_id
        else Document.objects.none()
    )
    document = query.first()
    if not document and external:
        document = Document.objects.filter(
            Q(pdp_external_id=external) | Q(document_uid=external)
        ).first()
    if not document and document_uid:
        document = Document.objects.filter(document_uid=document_uid).first()
    if not document:
        remote_company = pick(["company_id", "pdp_company_id"])
        companies = (
            list(
                PdpSession.objects.filter(
                    provider="superpdp", pdp_company_id=remote_company, status="active"
                ).values_list("company_uid", flat=True)
            )
            if remote_company
            else []
        )
        if len(companies) == 1:
            pdp_sync.request_sync(Company.objects.get(company_uid=companies[0]))
        return JsonResponse({"ok": True, "stored": False}, status=202)
    # If both identifiers are supplied they must agree; never relink an invoice.
    if (
        invoice_id
        and document.pdp_invoice_id
        and invoice_id != document.pdp_invoice_id
        or external
        and external not in {document.document_uid, document.pdp_external_id}
    ):
        return JsonResponse({"error": "Identifiants incohérents"}, status=409)
    pdp_sync.request_sync(Company.objects.get(company_uid=document.company_uid))
    event_id = pick(["pdp_event_id", "invoice_event_id", "event_id", "eventId"])
    receipt = digest(
        "superpdp:" + document.document_uid + ":" + (event_id or json_text(data))
    )
    with transaction.atomic():
        Document.objects.select_for_update().get(pk=document.pk)
        if (
            WebhookReceipt.objects.filter(pk=receipt).exists()
            or event_id
            and InvoiceEvent.objects.filter(
                company_uid=document.company_uid,
                document_uid=document.document_uid,
                provider="superpdp",
                pdp_event_id=event_id,
            ).exists()
        ):
            return JsonResponse({"ok": True, "stored": False})
        WebhookReceipt.objects.create(
            receipt_key=receipt, document_uid=document.document_uid, created_at=stamp()
        )
        InvoiceEvent.objects.create(
            company_uid=document.company_uid,
            document_uid=document.document_uid,
            provider="superpdp",
            pdp_invoice_id=invoice_id or document.pdp_invoice_id,
            pdp_event_id=event_id or None,
            event_code=pick(["event_code", "eventCode", "type", "status", "name"])
            or "superpdp:event",
            event_label=pick(["event_label", "eventLabel", "label", "message"])
            or "Événement SUPERPDP reçu",
            direction=document.direction,
            payload=json_text(data),
            occurred_at=stamp(),
        )
    return JsonResponse(
        {"ok": True, "stored": True, "document_uid": document.document_uid}
    )
