import secrets
import time

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from core.common import BusinessError
from core.models import BankFeed
from core.security import scoped
from core.services import bank_sync
from core.services.account_security import audit
from core.services.bridge import Bridge
from core.services.files import encrypt


ACCOUNTS = "/app/finances/comptes"
CALLBACK = ACCOUNTS + "/connexion/retour"


@scoped("admin", verification_return=ACCOUNTS)
@require_POST
@sensitive_post_parameters("client_secret")
@transaction.atomic
def configure(request):
    key = request.POST.get("client_id", "").strip()
    secret = request.POST.get("client_secret", "").strip()
    if (
        not key
        or len(key) > 180
        or len(secret) > 1024
        or any(ord(c) < 33 or ord(c) > 126 for c in key + secret)
    ):
        raise BusinessError("Vérifiez les identifiants de l’application Bridge.")
    feed, created = BankFeed.objects.select_for_update().get_or_create(
        company_uid=request.company.company_uid
    )
    if feed.remote_user and key != feed.client_id:
        raise BusinessError(
            "Cette entreprise est déjà liée à une application Bridge. Utilisez son identifiant.",
            409,
        )
    if not secret and (created or key != feed.client_id or not feed.secret_encrypted):
        raise BusinessError("Le secret de l’application Bridge est requis.")
    feed.client_id = key
    if secret:
        feed.secret_encrypted = encrypt(secret)
        feed.lease, feed.lease_until = "", None
    feed.auto_reconcile = request.POST.get("auto_reconcile") == "1"
    feed.save()
    audit(request, "bank.configured")
    messages.success(request, "Paramètres de connexion bancaire enregistrés.")
    return redirect(ACCOUNTS)


@scoped("admin", verification_return=ACCOUNTS)
@require_POST
@never_cache
def connect(request):
    with transaction.atomic():
        feed = (
            BankFeed.objects.select_for_update()
            .filter(company_uid=request.company.company_uid)
            .first()
        )
        if not feed or not feed.secret_encrypted:
            raise BusinessError("Configurez d’abord l’application Bridge.")
        if feed.owner_uid and feed.owner_uid != request.user.user_uid:
            raise BusinessError(
                "L’administrateur qui a ouvert cette connexion doit autoriser les banques.",
                403,
            )
    client = Bridge(feed)
    remote_user = client.authorize(provision=True)
    nonce = secrets.token_hex(24)
    url = client.management_url(
        request.user.email, settings.APP_URL.rstrip("/") + CALLBACK, nonce
    )
    with transaction.atomic():
        current = BankFeed.objects.select_for_update().get(pk=feed.pk)
        if (current.client_id, current.secret_encrypted) != (
            feed.client_id,
            feed.secret_encrypted,
        ) or current.owner_uid not in ("", request.user.user_uid):
            raise BusinessError("La configuration a changé. Rouvrez la connexion.", 409)
        if current.remote_user and current.remote_user != remote_user:
            raise BusinessError("L’identité bancaire a changé.", 409)
        current.owner_uid, current.remote_user, current.enabled = (
            request.user.user_uid,
            remote_user,
            True,
        )
        current.next_sync_at = timezone.now()
        current.save(
            update_fields=["owner_uid", "remote_user", "enabled", "next_sync_at"]
        )
    request.session["bank_connect"] = {
        "nonce": nonce,
        "company": feed.company_uid,
        "owner": request.user.user_uid,
        "user": remote_user,
        "expires": time.time() + 3600,
    }
    audit(request, "bank.connect_opened")
    return render(
        request,
        "core/bank_consent.html",
        {"title": "Autoriser mes banques", "portal": url},
    )


@scoped("finance")
@require_GET
def callback(request):
    attempt = request.session.get("bank_connect") or {}
    state = request.GET.get("context", "")
    if (
        not state
        or not secrets.compare_digest(
            state.encode(), str(attempt.get("nonce", "")).encode()
        )
        or attempt.get("company") != request.company.company_uid
        or attempt.get("owner") != request.user.user_uid
        or time.time() > attempt.get("expires", 0)
        or request.GET.get("user_uuid", attempt.get("user")) != attempt.get("user")
    ):
        raise BusinessError(
            "Ce retour bancaire a expiré ou appartient à une autre session. Rouvrez la connexion.",
            403,
        )
    request.session.pop("bank_connect", None)
    BankFeed.objects.filter(
        company_uid=request.company.company_uid,
        remote_user=attempt["user"],
        enabled=True,
    ).update(next_sync_at=timezone.now())
    if request.GET.get("success") == "false":
        messages.info(
            request,
            "Le parcours Bridge a été interrompu. Vous pouvez le rouvrir pour terminer l’autorisation.",
        )
    else:
        messages.success(
            request,
            "Retour de Bridge. Les comptes autorisés seront actualisés par le serveur.",
        )
    return redirect(ACCOUNTS)


@scoped("finance")
@require_POST
def refresh(request):
    if settings.EXTERNAL_SERVICES_DISABLED:
        raise BusinessError(
            "Les services externes sont désactivés sur cette instance.", 503
        )
    changed = (
        BankFeed.objects.filter(company_uid=request.company.company_uid, enabled=True)
        .exclude(remote_user="")
        .update(next_sync_at=timezone.now())
    )
    if not changed:
        raise BusinessError("Activez la connexion bancaire avant de synchroniser.", 409)
    messages.success(request, "La synchronisation bancaire est demandée.")
    return redirect(ACCOUNTS)


@scoped("admin", verification_return=ACCOUNTS)
@require_POST
def toggle(request):
    enable = request.POST.get("action")
    if enable not in {"pause", "resume"}:
        raise BusinessError("Action bancaire invalide.")
    active = enable == "resume"
    changed = (
        BankFeed.objects.filter(company_uid=request.company.company_uid)
        .exclude(remote_user="")
        .update(
            enabled=active,
            next_sync_at=timezone.now() if active else None,
            lease="",
            lease_until=None,
        )
    )
    if not changed:
        raise BusinessError("Autorisez d’abord les comptes auprès de la banque.")
    audit(request, "bank.resumed" if active else "bank.paused")
    return redirect(ACCOUNTS)


@scoped("finance")
@require_POST
def resolve(request, entry_id):
    bank_sync.resolve_entry(request.company, entry_id, request.POST.get("choice", ""))
    audit(request, "bank.reviewed")
    return redirect("/app/finances/transactions")
