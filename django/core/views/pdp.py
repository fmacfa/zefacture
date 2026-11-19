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
from django.views.decorators.debug import sensitive_post_parameters
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST
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
    PdpSyncState,
)
from core.security import scoped, scoped_get, ROLES
from core.services import pdp as service
from core.services import pdp_sync
from core.services.account_security import audit
from core.services.files import encrypt


def save_pdp_settings(session, sync, data):
    session.api_base = service.base(
        data.get("api_base") or "https://api.superpdp.tech"
    )
    new_client_id = (data.get("client_id") or "").strip() or session.client_id
    if (
        new_client_id != session.client_id
        and session.client_secret_encrypted
        and not data.get("client_secret")
    ):
        raise BusinessError(
            "Ressaisir le secret associé au nouveau Client ID SUPERPDP."
        )
    credentials_changed = bool(
        data.get("client_secret")
        or data.get("access_token")
        or new_client_id != session.client_id
    )
    session.client_id = new_client_id
    if credentials_changed:
        session.token_expires_at = session.last_checked_at = None
        session.access_token_encrypted = session.refresh_token_encrypted = None
        session.company_verification_status = "needs_review"
    for source, destination in (
        ("client_secret", "client_secret_encrypted"),
        ("access_token", "access_token_encrypted"),
    ):
        if data.get(source):
            setattr(session, destination, encrypt(data[source].strip()))
    if data.get("sandbox") == "1":
        if settings.ENVIRONMENT == "production":
            raise BusinessError("Simulation indisponible en production.")
        session.status = "sandbox"
    elif credentials_changed or session.status != "active":
        session.status = "configured"
    session.updated_at = stamp()
    session.save()
    if credentials_changed:
        sync.auth_mode = "technical" if session.client_id else "auto"
        sync.version += 1
        sync.save(update_fields=["auth_mode", "version"])


def verify_connection(company, session):
    service.verify(company, session)
    if session.company_verification_status == "verified":
        now = timezone.now()
        PdpSyncState.objects.filter(
            company_uid=company.company_uid, last_success_at__isnull=True, requested=False
        ).filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now)).update(
            requested=True, next_sync_at=now
        )


@scoped("write")
@require_POST
def sync_reception(request):
    if not service.reception_connected(request.company):
        raise BusinessError(
            "Une connexion SUPERPDP vérifiée est nécessaire pour récupérer les factures.", 409
        )
    pdp_sync.request_sync(request.company)
    audit(request, "pdp.sync_requested")
    return redirect("/app/reception")


@sensitive_post_parameters("client_secret", "access_token")
@require_http_methods(["GET", "HEAD", "POST"])
@scoped("admin")
def pdp(request, action=None):
    session = service.session_for(request.company)
    sync = pdp_sync.state_for(request.company)
    redirect_uri = (
        os.getenv("SUPERPDP_OAUTH_REDIRECT_URI")
        or settings.APP_URL + "/app/entreprise/pdp/callback"
    )
    client_id = os.getenv("SUPERPDP_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("SUPERPDP_OAUTH_CLIENT_SECRET", "").strip()
    oauth_ready = bool(client_id and client_secret) and not settings.EXTERNAL_SERVICES_DISABLED
    if action == "connecter" and request.method == "POST":
        if settings.EXTERNAL_SERVICES_DISABLED:
            raise BusinessError(
                "Les services externes sont désactivés sur cette instance.", 503
            )
        if not oauth_ready:
            messages.error(
                request, "L’application OAuth de cette instance doit être configurée pour utiliser ce bouton."
            )
            return redirect("/app/entreprise/pdp")
        state = secrets.token_urlsafe(32)
        request.session["pdp_oauth"] = {
            "state": state,
            "company_uid": request.company.company_uid,
            "created": time.time(),
            "user_uid": request.user.user_uid,
            "version": sync.version,
            "client": digest(json_text([client_id, client_secret])),
        }
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
            != digest(json_text([client_id, client_secret]))
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
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        session.refresh_token_encrypted = None
        service.save_token(session, data)
        sync.auth_mode = "oauth"
        sync.version += 1
        sync.save(update_fields=["auth_mode", "version"])
        verify_connection(request.company, session)
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
            session.last_checked_at = None
            session.save()
            audit(request, "pdp.disconnected", session.session_uid)
        elif operation in {"verify", "save_and_verify", "", "save"}:
            try:
                with transaction.atomic():
                    company = Company.objects.select_for_update().get(pk=request.company.pk)
                    session = PdpSession.objects.select_for_update().get(pk=session.pk)
                    sync = PdpSyncState.objects.select_for_update().get(pk=sync.pk)
                    if operation != "verify":
                        save_pdp_settings(session, sync, request.POST)
                        audit(request, "pdp.configuration_saved", session.session_uid)
                    if operation in {"verify", "save_and_verify"} and session.status != "sandbox":
                        verify_connection(company, session)
                        audit(request, "pdp.connection_checked", session.session_uid)
            except service.PdpHttpError as exc:
                if exc.http_status in (401, 403):
                    messages.error(
                        request,
                        "SUPERPDP refuse l’accès. Vérifiez les identifiants techniques et leurs droits pour cette entreprise.",
                    )
                else:
                    messages.error(request, str(exc))
            except BusinessError as exc:
                if operation in {"", "save"}:
                    raise
                messages.error(request, str(exc))
            else:
                if session.status == "sandbox":
                    messages.success(request, "Simulation locale enregistrée.")
                elif operation not in {"verify", "save_and_verify"}:
                    messages.success(request, "Paramètres SUPERPDP enregistrés.")
                elif session.company_verification_status == "verified":
                    messages.success(request, "Connexion SUPERPDP confirmée et entreprise vérifiée.")
                elif session.company_verification_status == "failed":
                    messages.warning(request, "SUPERPDP a refusé la vérification de cette entreprise.")
                else:
                    messages.warning(
                        request,
                        "Connexion établie. Consultez l’état de vérification dans SUPERPDP.",
                    )
        else:
            raise BusinessError("Action SUPERPDP inconnue.")
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
            "oauth_ready": oauth_ready,
            "external_services_disabled": settings.EXTERNAL_SERVICES_DISABLED,
            "has_pdp_credentials": bool(
                session.access_token_encrypted
                or session.refresh_token_encrypted
                or (session.client_id and session.client_secret_encrypted)
            ),
            "connection_label": {
                "not_configured": "À configurer",
                "configured": "Paramètres enregistrés",
                "active": "Connexion vérifiée",
                "sandbox": "Simulation locale",
            }.get(session.status, "À vérifier"),
            "verification_label": {
                "verified": "Entreprise vérifiée",
                "needs_review": "En cours d’examen chez SUPERPDP" if session.last_checked_at else "Entreprise à vérifier",
                "failed": "Vérification refusée",
            }.get(session.company_verification_status, "Entreprise à vérifier"),
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
