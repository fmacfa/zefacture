import secrets
from django.contrib import messages
from django.db import transaction
from django.shortcuts import render, redirect
from django.utils import timezone
from core.common import BusinessError
from core.models import WebhookEndpoint, WebhookDelivery, EmailConnection
from core.security import scoped, scoped_get, throttle
from core.services import email_connections as mail_service, webhooks as hooks
from core.services.files import encrypt
from core.services.network import checked_url, resolve_host
from core.services.account_security import audit


@scoped("admin")
def email_connection(request, provider=None, action=None):
    if action == "retour":
        if request.method != "GET":
            raise BusinessError("Méthode non autorisée.", 405)
        mail_service.finish_oauth(request, provider)
        audit(request, "email.oauth_connected")
        messages.success(
            request, "Messagerie connectée. Les nouveaux emails utiliseront ce compte."
        )
        return redirect("/app/emails/connexion")
    connection = mail_service.connection_for(request.company)
    if request.method == "POST":
        throttle(
            request,
            "mail-settings:" + request.company.company_uid,
            limit=30,
            seconds=900,
        )
        selected = request.POST.get("action", "save")
        if selected == "save":
            mail_service.save_settings(request.company, request.POST)
            audit(request, "email.settings_updated")
            messages.success(
                request,
                "Paramètres enregistrés. Les anciens emails ne sont pas envoyés automatiquement.",
            )
        elif selected == "connect":
            audit(request, "email.oauth_started")
            return redirect(mail_service.begin_oauth(request, connection))
        elif selected == "use_server":
            with transaction.atomic():
                connection = EmailConnection.objects.select_for_update().get(
                    pk=connection.pk
                )
                connection.client_id = connection.client_secret_encrypted = ""
                connection.access_token_encrypted = (
                    connection.refresh_token_encrypted
                ) = ""
                (
                    connection.token_expires_at,
                    connection.account_id,
                    connection.sender_email,
                ) = None, "", ""
                connection.status = "disconnected"
                connection.version += 1
                connection.save()
            audit(request, "email.oauth_use_server")
            messages.success(
                request,
                "Application du serveur sélectionnée. Reconnectez maintenant votre compte.",
            )
        elif selected == "test":
            mail_service.check_connection(connection)
            audit(request, "email.connection_checked")
            messages.success(
                request,
                "La messagerie a confirmé la connexion. Aucun email n'a été envoyé.",
            )
        elif selected == "send_test":
            from core.services.mail import queue

            queue(
                request.company.company_uid,
                None,
                "connection_test",
                request.user.email,
                "Test de messagerie ZeFacture",
                "La connexion email de "
                + request.company.legal_name
                + " est configurée.",
            )
            messages.success(
                request,
                "Email de test en file d'attente à votre adresse. Son résultat apparaîtra dans Emails.",
            )
        elif selected == "disconnect":
            with transaction.atomic():
                connection = EmailConnection.objects.select_for_update().get(
                    pk=connection.pk
                )
                connection.access_token_encrypted = (
                    connection.refresh_token_encrypted
                ) = ""
                connection.token_expires_at = None
                connection.account_id = ""
                connection.sender_email = ""
                connection.password_encrypted = ""
                connection.smtp_username = ""
                connection.status = "disconnected"
                connection.provider = "outbox"
                connection.version += 1
                connection.save()
            audit(request, "email.disconnected")
            messages.success(
                request,
                "Compte déconnecté de ZeFacture. Vous pouvez aussi révoquer l'autorisation dans votre messagerie.",
            )
        else:
            raise BusinessError("Action inconnue.")
        return redirect("/app/emails/connexion")
    if action:
        raise BusinessError("Méthode non autorisée.", 405)
    return render(
        request,
        "core/email_connection.html",
        {
            "title": "Connexion email",
            "connection": connection,
            "providers": mail_service.PROVIDERS,
            "oauth_provider": connection.provider in mail_service.OAUTH,
            "redirect_uri": mail_service.redirect_uri(connection.provider)
            if connection.provider in mail_service.OAUTH
            else "",
            "password_saved": bool(connection.password_encrypted),
            "client_secret_saved": bool(connection.client_secret_encrypted),
            "readiness": mail_service.oauth_readiness(connection),
        },
    )


@scoped("admin")
def oauth_guide(request):
    return render(
        request,
        "core/oauth_guide.html",
        {
            "title": "Configurer Google et Microsoft",
            "google_callback": mail_service.redirect_uri("google"),
            "microsoft_callback": mail_service.redirect_uri("microsoft"),
            "google_scope": mail_service.OAUTH["google"]["scope"],
            "microsoft_scope": mail_service.OAUTH["microsoft"]["scope"],
        },
    )


@scoped("admin")
@transaction.atomic
def webhooks(request, endpoint_uid=None, delivery_uid=None):
    endpoint = (
        scoped_get(WebhookEndpoint, request, endpoint_uid=endpoint_uid)
        if endpoint_uid
        else None
    )
    secret = ""
    if request.method == "POST":
        action = request.POST.get("action", "save")
        if action in {"save", "toggle", "rotate"}:
            audit(request, "webhook." + action, endpoint_uid or "")
        if delivery_uid:
            delivery = scoped_get(
                WebhookDelivery,
                request,
                delivery_uid=delivery_uid,
                endpoint_uid=endpoint_uid,
            )
            if delivery.status not in {"failed", "paused"}:
                raise BusinessError(
                    "Cette livraison est déjà envoyée ou en cours.", 409
                )
            if not endpoint.enabled:
                raise BusinessError("Réactivez d'abord ce webhook.")
            (
                delivery.status,
                delivery.attempts,
                delivery.last_error,
                delivery.available_at,
            ) = "queued", 0, "", timezone.now()
            delivery.save()
        elif action == "toggle" and endpoint:
            endpoint.enabled = not endpoint.enabled
            endpoint.save(update_fields=["enabled"])
        elif action == "rotate" and endpoint:
            secret = secrets.token_urlsafe(32)
            endpoint.secret_encrypted = encrypt(secret)
            endpoint.save(update_fields=["secret_encrypted"])
        elif action == "test" and endpoint:
            if not endpoint.enabled:
                raise BusinessError("Réactivez d'abord ce webhook.")
            throttle(
                request, "webhook-test:" + endpoint.endpoint_uid, limit=10, seconds=900
            )
            hooks.emit(
                request.company.company_uid,
                "webhook.test",
                {"message": "Test ZeFacture, sans données métier"},
                endpoint,
            )
            messages.success(request, "Événement de test en file d'attente.")
        elif action == "save":
            endpoint = endpoint or WebhookEndpoint(
                company_uid=request.company.company_uid
            )
            name, url = (
                request.POST.get("name", "").strip(),
                request.POST.get("url", "").strip(),
            )
            if not name or len(name) > 160:
                raise BusinessError("Nom du webhook requis, maximum 160 caractères.")
            parsed = checked_url(url)
            resolve_host(parsed.hostname, parsed.port or 443)
            events = request.POST.getlist("events")
            if not events or any(event not in hooks.EVENTS for event in events):
                raise BusinessError(
                    "Sélectionnez au moins un événement pris en charge."
                )
            if not endpoint.pk:
                secret = secrets.token_urlsafe(32)
                endpoint.secret_encrypted = encrypt(secret)
            endpoint.name, endpoint.url, endpoint.events = (
                name,
                url,
                sorted(set(events)),
            )
            endpoint.save()
        else:
            raise BusinessError("Action inconnue.")
        if not secret:
            return redirect("/app/developpeurs/webhooks/" + endpoint.endpoint_uid)
    return render(
        request,
        "core/webhooks.html",
        {
            "title": "Webhooks sortants",
            "endpoint": endpoint,
            "endpoints": WebhookEndpoint.objects.filter(
                company_uid=request.company.company_uid
            ).order_by("name"),
            "deliveries": WebhookDelivery.objects.filter(
                company_uid=request.company.company_uid,
                **({"endpoint_uid": endpoint.endpoint_uid} if endpoint else {}),
            ).order_by("-id")[:100],
            "events": hooks.EVENTS,
            "secret": secret,
        },
    )
