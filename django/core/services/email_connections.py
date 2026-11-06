import base64
import hashlib
import hmac
import json
import os
import secrets
import smtplib
import socket
import ssl
import time
from datetime import timedelta
from urllib.parse import urlencode, urlsplit
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from core.common import BusinessError, email, digest
from core.models import EmailConnection, EmailRoute, OAuthAttempt
from .files import encrypt, decrypt
from .network import resolve_host, https_request

PROVIDERS = [
    ("instance", "Configuration du serveur"),
    ("outbox", "Boîte locale, sans envoi"),
    ("smtp", "Serveur SMTP"),
    ("google", "Gmail / Google Workspace"),
    ("microsoft", "Outlook / Microsoft 365"),
]
OAUTH = {
    "google": {
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "profile": "https://openidconnect.googleapis.com/v1/userinfo",
        "scope": "openid email https://www.googleapis.com/auth/gmail.send",
    },
    "microsoft": {
        "authorize": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "profile": "https://graph.microsoft.com/v1.0/me?$select=id,mail,userPrincipalName",
        "scope": "openid profile email offline_access https://graph.microsoft.com/User.Read https://graph.microsoft.com/Mail.Send",
    },
}


def connection_for(company):
    return EmailConnection.objects.get_or_create(company_uid=company.company_uid)[0]


def redirect_uri(provider):
    return settings.APP_URL + "/app/emails/connexion/" + provider + "/retour"


def credentials(connection):
    prefix = "GOOGLE_MAIL" if connection.provider == "google" else "MICROSOFT_MAIL"
    if connection.client_id or connection.client_secret_encrypted:
        client = connection.client_id
        secret = decrypt(connection.client_secret_encrypted)
    else:
        client = os.getenv(prefix + "_CLIENT_ID", "")
        secret = os.getenv(prefix + "_CLIENT_SECRET", "")
    if not client or not secret:
        raise BusinessError(
            "Renseignez l'identifiant et le secret de votre application OAuth dans les paramètres avancés, puis enregistrez."
        )
    return client, secret


def credential_fingerprint(connection):
    client, secret = credentials(connection)
    return digest(client + "\0" + secret)


def oauth_readiness(connection):
    provider = connection.provider
    if provider not in OAUTH:
        return {}
    try:
        credentials(connection)
        configured = True
    except BusinessError:
        configured = False
    return {
        "configured": configured,
        "source": "entreprise"
        if connection.client_id or connection.client_secret_encrypted
        else "serveur",
        "callback": redirect_uri(provider),
        "scope": OAUTH[provider]["scope"],
        "public_https": urlsplit(redirect_uri(provider)).scheme == "https",
    }


@transaction.atomic
def save_settings(company, data):
    connection = connection_for(company)
    connection = EmailConnection.objects.select_for_update().get(pk=connection.pk)
    provider = data.get("provider", "instance")
    if provider not in dict(PROVIDERS):
        raise BusinessError("Messagerie inconnue.")
    previous = connection.provider
    connection.sender_name = str(data.get("sender_name") or company.legal_name).strip()
    if len(connection.sender_name) > 200 or any(
        c in connection.sender_name for c in "\r\n"
    ):
        raise BusinessError("Nom d'expéditeur invalide.")
    changed_credentials = (
        provider != previous
        or bool(data.get("client_secret"))
        or ("client_id" in data and data["client_id"].strip() != connection.client_id)
    )
    if provider == "smtp":
        host = str(data.get("smtp_host", "")).strip().lower()
        try:
            port = int(data.get("smtp_port", "587"))
        except (ValueError, TypeError):
            raise BusinessError("Port SMTP invalide.")
        security = data.get("smtp_security", "starttls")
        username = str(data.get("smtp_username", "")).strip()
        destination_changed = (host, port, security, username) != (
            connection.smtp_host,
            connection.smtp_port,
            connection.smtp_security,
            connection.smtp_username,
        )
        if destination_changed and not data.get("smtp_password"):
            raise BusinessError(
                "Ressaisissez le mot de passe SMTP lorsque le serveur, le port, le chiffrement ou l’identifiant change."
            )
        if (
            not host
            or len(host) > 253
            or any(c in host for c in "/@:#?\r\n ")
            or port not in {25, 465, 587, 2525}
            or security not in {"ssl", "starttls"}
        ):
            raise BusinessError(
                "Indiquez un hôte SMTP et un port 25, 465, 587 ou 2525 avec TLS."
            )
        connection.smtp_host, connection.smtp_port, connection.smtp_security = (
            host,
            port,
            security,
        )
        connection.smtp_username = username
        if not connection.smtp_username or len(connection.smtp_username) > 254:
            raise BusinessError("Identifiant SMTP requis.")
        if data.get("smtp_password"):
            if len(data["smtp_password"]) > 2000:
                raise BusinessError("Mot de passe SMTP trop long.")
            connection.password_encrypted = encrypt(data["smtp_password"])
        if not connection.password_encrypted:
            raise BusinessError("Mot de passe SMTP requis.")
        connection.sender_email = email(data.get("sender_email"))
        connection.status = "configured"
    elif provider in OAUTH:
        if provider != previous:
            connection.client_id = ""
            connection.client_secret_encrypted = ""
        if "client_id" in data:
            new_client = str(data.get("client_id") or "").strip()
            if new_client != connection.client_id and not data.get("client_secret"):
                raise BusinessError(
                    "Ressaisissez le secret avec le nouvel identifiant OAuth, ou utilisez l’application du serveur."
                )
            connection.client_id = new_client
            if len(connection.client_id) > 500:
                raise BusinessError("Identifiant OAuth trop long.")
        if data.get("client_secret"):
            if len(data["client_secret"]) > 3000:
                raise BusinessError("Secret OAuth trop long.")
            connection.client_secret_encrypted = encrypt(data["client_secret"])
        if bool(connection.client_id) != bool(connection.client_secret_encrypted):
            raise BusinessError(
                "L’identifiant OAuth et son secret doivent être renseignés ensemble."
            )
        if changed_credentials:
            connection.status = "disconnected"
            connection.sender_email = ""
    else:
        connection.status = "configured"
        connection.sender_email = ""
    if changed_credentials:
        connection.access_token_encrypted = ""
        connection.refresh_token_encrypted = ""
        connection.token_expires_at = None
        connection.account_id = ""
    connection.provider = provider
    connection.version += 1
    connection.last_tested_at = None
    connection.save()
    return connection


def begin_oauth(request, connection):
    if settings.EXTERNAL_SERVICES_DISABLED:
        raise BusinessError(
            "Les services externes sont désactivés sur cette instance.", 503
        )
    provider = connection.provider
    if provider not in OAUTH:
        raise BusinessError(
            "Choisissez Google ou Microsoft et enregistrez les paramètres."
        )
    client, _ = credentials(connection)
    callback = urlsplit(redirect_uri(provider))
    if callback.scheme != "https" and callback.hostname not in {
        "localhost",
        "127.0.0.1",
    }:
        raise BusinessError(
            "Configurez l'URL HTTPS de votre instance avant de connecter cette messagerie."
        )
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    OAuthAttempt.objects.filter(expires_at__lt=timezone.now()).delete()
    OAuthAttempt.objects.create(
        state_hash=digest(state),
        company_uid=connection.company_uid,
        user_uid=request.user.user_uid,
        provider=provider,
        version=connection.version,
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    request.session["mail_oauth"] = {
        "state": state,
        "verifier": verifier,
        "created": time.time(),
        "company_uid": connection.company_uid,
        "user_uid": request.user.user_uid,
        "provider": provider,
        "version": connection.version,
        "credentials": credential_fingerprint(connection),
    }
    params = {
        "response_type": "code",
        "client_id": client,
        "redirect_uri": redirect_uri(provider),
        "scope": OAUTH[provider]["scope"],
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        )
        .rstrip(b"=")
        .decode(),
    }
    if provider == "google":
        params.update(
            access_type="offline", prompt="consent", include_granted_scopes="true"
        )
    else:
        params["prompt"] = "select_account"
    return OAUTH[provider]["authorize"] + "?" + urlencode(params)


def api_json(url, method="GET", form=None, token=None):
    headers = {"Accept": "application/json"}
    raw = None
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        raw = urlencode(form).encode()
    if token:
        headers["Authorization"] = "Bearer " + token
    status, response = https_request(url, method, body=raw, headers=headers)
    if not 200 <= status < 300:
        try:
            error = json.loads(response).get("error")
        except (ValueError, AttributeError):
            error = None
        if isinstance(error, str) and error in {
            "invalid_grant",
            "interaction_required",
            "consent_required",
        }:
            raise AuthorizationExpired(
                "Autorisation expirée ou révoquée. Reconnectez ce compte dans Emails > Connexion.",
                401,
            )
        raise BusinessError(
            f"La messagerie a refusé l'autorisation (HTTP {status}). Vérifiez les paramètres ou reconnectez le compte.",
            502,
        )
    try:
        data = json.loads(response)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, TypeError) as exc:
        raise BusinessError("Réponse de la messagerie invalide.", 502) from exc


class AuthorizationExpired(BusinessError):
    pass


def store_token(connection, data):
    token = data.get("access_token")
    if not isinstance(token, str) or not token or len(token) > 100000:
        raise BusinessError("L'autorisation n'a pas fourni de jeton d'accès.", 502)
    if str(data.get("token_type", "Bearer")).lower() != "bearer":
        raise BusinessError("Type d’autorisation non pris en charge.", 502)
    granted = data.get("scope")
    if granted is not None:
        if not isinstance(granted, str):
            raise BusinessError("Permissions de messagerie invalides.", 502)
        scopes = {s.lower() for s in granted.split()}
        required = (
            "https://www.googleapis.com/auth/gmail.send"
            if connection.provider == "google"
            else "mail.send"
        )
        if (
            required not in scopes
            and ("https://graph.microsoft.com/" + required) not in scopes
        ):
            raise BusinessError(
                "L’autorisation d’envoyer des emails n’a pas été accordée. Reconnectez le compte en acceptant cette permission.",
                502,
            )
    try:
        duration = min(86400, max(60, int(data.get("expires_in", 3600))))
    except (ValueError, TypeError):
        raise BusinessError("Durée d'autorisation invalide.", 502)
    connection.access_token_encrypted = encrypt(token)
    refresh = data.get("refresh_token")
    if refresh:
        if not isinstance(refresh, str) or len(refresh) > 100000:
            raise BusinessError("Jeton de renouvellement invalide.", 502)
        connection.refresh_token_encrypted = encrypt(refresh)
    connection.token_expires_at = timezone.now() + timedelta(seconds=duration - 30)
    connection.save()
    return token


def finish_oauth(request, provider):
    pending = request.session.pop("mail_oauth", None)
    if (
        not pending
        or not hmac.compare_digest(
            str(pending.get("state", "")).encode(),
            str(request.GET.get("state", "")).encode(),
        )
        or not 0 <= time.time() - pending["created"] < 600
        or pending["company_uid"] != request.company.company_uid
        or pending["user_uid"] != request.user.user_uid
        or pending["provider"] != provider
    ):
        raise BusinessError(
            "Cette autorisation est expirée ou ne correspond pas à votre entreprise.",
            403,
        )
    # Commit consumption before token exchange, including failed callbacks. An
    # atomic UPDATE also rejects two simultaneous requests with the same session.
    consumed = OAuthAttempt.objects.filter(
        state_hash=digest(pending["state"]),
        company_uid=pending["company_uid"],
        user_uid=pending["user_uid"],
        provider=provider,
        version=pending["version"],
        consumed_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).update(consumed_at=timezone.now())
    if consumed != 1:
        raise BusinessError("Cette autorisation a déjà été utilisée ou a expiré.", 403)
    with transaction.atomic():
        return complete_oauth(request, provider, pending)


def complete_oauth(request, provider, pending):
    connection = EmailConnection.objects.select_for_update().get(
        company_uid=request.company.company_uid
    )
    if connection.provider != provider or connection.version != pending["version"]:
        raise BusinessError("Les paramètres ont changé. Recommencez la connexion.", 409)
    if not hmac.compare_digest(
        credential_fingerprint(connection), pending.get("credentials", "")
    ):
        raise BusinessError(
            "L’application OAuth a changé. Recommencez la connexion.", 409
        )
    if request.GET.get("error") or not request.GET.get("code"):
        raise BusinessError("L'autorisation a été refusée ou annulée.")
    client, secret = credentials(connection)
    data = api_json(
        OAUTH[provider]["token"],
        "POST",
        form={
            "grant_type": "authorization_code",
            "code": request.GET["code"],
            "client_id": client,
            "client_secret": secret,
            "redirect_uri": redirect_uri(provider),
            "code_verifier": pending["verifier"],
        },
    )
    # A fresh grant must supply its own durable authorization; never retain the
    # refresh token belonging to a previous account when reconnecting.
    connection.refresh_token_encrypted = ""
    token = store_token(connection, data)
    profile = api_json(OAUTH[provider]["profile"], token=token)
    if provider == "google":
        if profile.get("email_verified") is not True:
            raise BusinessError("Google n'a pas confirmé l'adresse du compte.", 502)
        address, identifier = profile.get("email"), profile.get("sub")
    else:
        address, identifier = (
            profile.get("mail") or profile.get("userPrincipalName"),
            profile.get("id"),
        )
    if not identifier or not connection.refresh_token_encrypted:
        raise BusinessError(
            "Autorisation incomplète. Accordez l'accès durable à la messagerie puis reconnectez le compte.",
            502,
        )
    connection.sender_email, connection.account_id = (
        email(address),
        str(identifier)[:300],
    )
    connection.status, connection.last_tested_at = "connected", timezone.now()
    connection.version += 1
    connection.save()
    return connection


def access_token(connection):
    try:
        with transaction.atomic():
            return locked_access_token(connection)
    except AuthorizationExpired:
        mark_reconnect(connection)
        raise


def mark_reconnect(connection):
    EmailConnection.objects.filter(pk=connection.pk, version=connection.version).update(
        status="reconnect",
        access_token_encrypted="",
        refresh_token_encrypted="",
        token_expires_at=None,
    )


def locked_access_token(connection):
    current = EmailConnection.objects.select_for_update().get(pk=connection.pk)
    if (
        current.version,
        current.provider,
        current.sender_email,
        current.account_id,
    ) != (
        connection.version,
        connection.provider,
        connection.sender_email,
        connection.account_id,
    ):
        raise BusinessError("La messagerie a changé. Recommencez l'opération.", 409)
    connection = current
    if connection.status != "connected":
        raise BusinessError("Reconnectez la messagerie de l'entreprise.")
    if (
        connection.token_expires_at
        and connection.token_expires_at > timezone.now() + timedelta(seconds=30)
    ):
        return decrypt(connection.access_token_encrypted)
    client, secret = credentials(connection)
    if not connection.refresh_token_encrypted:
        raise BusinessError(
            "Reconnectez la messagerie pour renouveler son autorisation."
        )
    data = api_json(
        OAUTH[connection.provider]["token"],
        "POST",
        form={
            "grant_type": "refresh_token",
            "refresh_token": decrypt(connection.refresh_token_encrypted),
            "client_id": client,
            "client_secret": secret,
        },
    )
    return store_token(connection, data)


def smtp_client(connection):
    if settings.EXTERNAL_SERVICES_DISABLED:
        raise BusinessError(
            "Les services externes sont désactivés sur cette instance.", 503
        )
    address = resolve_host(connection.smtp_host, connection.smtp_port)
    context = ssl.create_default_context()
    base = smtplib.SMTP_SSL if connection.smtp_security == "ssl" else smtplib.SMTP

    class PinnedSMTP(base):
        def _get_socket(self, host, port, timeout):
            sock = socket.create_connection((address, port), timeout)
            if connection.smtp_security == "ssl":
                return context.wrap_socket(sock, server_hostname=host)
            return sock

    server = None
    try:
        server = PinnedSMTP(connection.smtp_host, connection.smtp_port, timeout=15)
        server.ehlo()
        if connection.smtp_security == "starttls":
            server.starttls(context=context)
            server.ehlo()
        server.login(connection.smtp_username, decrypt(connection.password_encrypted))
        return server
    except Exception:
        if server:
            server.close()
        raise BusinessError(
            "Connexion SMTP refusée. Vérifiez le serveur, le chiffrement et les identifiants.",
            502,
        )


def check_connection(connection):
    if connection.provider == "smtp":
        with smtp_client(connection) as client:
            if client.noop()[0] != 250:
                raise BusinessError(
                    "Le serveur SMTP n'a pas confirmé la connexion.", 502
                )
    elif connection.provider in OAUTH:
        profile = api_json(
            OAUTH[connection.provider]["profile"], token=access_token(connection)
        )
        identifier = (
            profile.get("sub") if connection.provider == "google" else profile.get("id")
        )
        if str(identifier) != connection.account_id:
            raise BusinessError(
                "Le compte connecté ne correspond plus à l'expéditeur.", 409
            )
    else:
        raise BusinessError("Choisissez une messagerie à tester.")
    connection.last_tested_at = timezone.now()
    connection.save(update_fields=["last_tested_at"])


def deliver(item, message):
    route = EmailRoute.objects.filter(email_uid=item.email_uid).first()
    connection = (
        EmailConnection.objects.filter(
            company_uid=item.company_uid, connection_uid=route.connection_uid
        ).first()
        if route
        else None
    )
    if (
        not connection
        or connection.version != route.connection_version
        or connection.sender_email != item.sender_email
    ):
        raise BusinessError(
            "La messagerie a changé. Relancez explicitement cet email depuis l'interface.",
            409,
        )
    if connection.provider == "smtp":
        with smtp_client(connection) as client:
            refused = client.sendmail(
                item.sender_email, [item.recipient_email], message.message().as_bytes()
            )
            if refused:
                raise BusinessError("Le serveur SMTP a refusé le destinataire.", 502)
        return
    if connection.provider not in OAUTH:
        raise BusinessError("Reconnectez la messagerie de l'entreprise.")
    raw = message.message().as_bytes()
    token = access_token(connection)
    if connection.provider == "google":
        body = json.dumps({"raw": base64.urlsafe_b64encode(raw).decode()}).encode()
        url, mime = (
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            "application/json",
        )
    else:
        body = base64.b64encode(raw)
        if len(body) > 4_000_000:
            raise BusinessError(
                "Cet email dépasse la taille prise en charge par la connexion Outlook. Utilisez SMTP ou réduisez les pièces jointes."
            )
        url, mime = "https://graph.microsoft.com/v1.0/me/sendMail", "text/plain"
    status, response = https_request(
        url,
        body=body,
        headers={"Authorization": "Bearer " + token, "Content-Type": mime},
    )
    if not 200 <= status < 300:
        if status == 401:
            mark_reconnect(connection)
            raise BusinessError(
                "L’autorisation n’est plus valable. Reconnectez le compte dans Emails > Connexion.",
                401,
            )
        raise BusinessError(f"La messagerie a refusé l'envoi (HTTP {status}).", 502)
    if connection.provider == "google":
        try:
            item.provider_message_id = str(json.loads(response).get("id", ""))[:190]
        except (ValueError, AttributeError):
            pass  # Acknowledged by provider: never retry automatically.


def route_email(item):
    connection = (
        EmailConnection.objects.filter(company_uid=item.company_uid).first()
        if item.company_uid
        else None
    )
    if not connection or connection.provider == "instance":
        item.transport = settings.MAIL_TRANSPORT
        item.sender_email = settings.DEFAULT_FROM_EMAIL
        item.sender_name = settings.MAIL_FROM_NAME
        EmailRoute.objects.filter(email_uid=item.email_uid).delete()
    elif connection.provider == "outbox":
        item.transport = "outbox"
        item.sender_email = connection.sender_email or settings.DEFAULT_FROM_EMAIL
        item.sender_name = connection.sender_name or settings.MAIL_FROM_NAME
        EmailRoute.objects.filter(email_uid=item.email_uid).delete()
    else:
        if not connection.sender_email or connection.status not in {
            "configured",
            "connected",
        }:
            raise BusinessError(
                "Connectez d'abord la messagerie dans Emails > Connexion."
            )
        item.transport, item.sender_email, item.sender_name = (
            "company",
            connection.sender_email,
            connection.sender_name,
        )
        EmailRoute.objects.update_or_create(
            email_uid=item.email_uid,
            defaults={
                "connection_uid": connection.connection_uid,
                "connection_version": connection.version,
            },
        )
    return item
