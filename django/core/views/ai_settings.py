import re
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.shortcuts import render, redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from core.common import BusinessError
from core.models import Company
from core.security import scoped
from core.services import extraction_ai
from core.services.account_security import audit
from core.services.files import encrypt

URL = "/app/entreprise/ia"
TEST_RECEIPT = "Atelier Démonstration\nTicket fictif de connexion\nDate : 12/04/2026\nTOTAL TTC 12,00 EUR"


@transaction.atomic
def update(request, provider, action):
    company = Company.objects.select_for_update().get(pk=request.company.pk)
    profiles = dict(company.ingestion_ai)
    if action == "save":
        model = request.POST.get("model", "").strip()
        if provider == "google":
            model = model.removeprefix("models/")
        pattern = (
            r"[A-Za-z0-9._-]{1,120}"
            if provider == "google"
            else r"[A-Za-z0-9._/:-]{1,160}"
        )
        if not re.fullmatch(pattern, model):
            raise BusinessError(
                "Renseignez l’identifiant du modèle proposé par le fournisseur."
            )
        secret = request.POST.get("api_key", "").strip()
        if len(secret) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in secret):
            raise BusinessError(
                "La clé API contient un caractère invalide ou dépasse la longueur autorisée."
            )
        cipher = (
            encrypt(secret) if secret else profiles.get(provider, {}).get("secret", "")
        )
        if not cipher:
            raise BusinessError("Saisissez une clé API pour cette entreprise.")
        profiles[provider] = {"model": model, "secret": cipher}
    elif action == "disable":
        profiles[provider] = {"disabled": True}
    elif action == "instance":
        profiles.pop(provider, None)
    else:
        raise BusinessError("Action inconnue.")
    company.ingestion_ai = profiles
    company.save(update_fields=["ingestion_ai"])
    audit(request, "ai_provider." + action, provider)


@never_cache
@sensitive_post_parameters("api_key")
@scoped("admin", verification_return=URL)
@require_http_methods(["GET", "POST"])
def providers(request):
    if request.method == "POST":
        provider = request.POST.get("provider")
        if provider not in extraction_ai.PROVIDERS:
            raise BusinessError("Fournisseur inconnu.")
        action = request.POST.get("action")
        if action == "test":
            extraction_ai.extract(
                provider, TEST_RECEIPT, company_uid=request.company.company_uid
            )
            messages.success(
                request,
                "Connexion réussie. Le fournisseur a répondu au test sur le ticket fictif.",
            )
        else:
            update(request, provider, action)
            messages.success(request, "Réglage du fournisseur enregistré.")
        return redirect(URL + "#" + provider)
    cards = []
    profiles = request.company.ingestion_ai
    for provider in ("google", "openai", "anthropic", "openrouter"):
        label, configured, model = extraction_ai.configuration(
            provider, profiles, reveal=False
        )
        entry = profiles.get(provider, {})
        cards.append(
            {
                "id": provider,
                "label": label,
                "active": bool(configured and model),
                "custom": bool(entry.get("secret")),
                "disabled": bool(entry.get("disabled")),
                "has_profile": provider in profiles,
                "model": model
                or ("gemma-4-26b-a4b-it" if provider == "google" else ""),
            }
        )
    return render(
        request,
        "core/ai_settings.html",
        {
            "title": "Intelligence artificielle",
            "providers": cards,
            "services_disabled": settings.EXTERNAL_SERVICES_DISABLED,
        },
    )
