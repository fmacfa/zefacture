from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import render, redirect
from core.common import BusinessError
from core.models import UserPreferences
from core.security import scoped, select_company
from core.services import branding as service
from core.services.account_security import audit


@scoped("admin")
def branding(request):
    if request.method == "POST":
        service.save(request.company, request.POST, request.FILES.get("logo"))
        audit(request, "branding.updated")
        messages.success(
            request,
            "Personnalisation enregistrée pour cette entreprise. Les documents déjà émis conservent leur présentation d’origine.",
        )
        return redirect("/app/entreprise/personnalisation")
    row = service.for_company(request.company)
    return render(
        request,
        "core/branding.html",
        {
            "title": "Personnalisation",
            "branding": row,
            "mail_templates": [
                {"key": key, "label": label, **row.email_templates.get(key, {})}
                for key, label in service.TEMPLATE_NAMES.items()
            ],
        },
    )


@scoped()
def logo(request):
    row = service.for_company(request.company)
    raw = service.logo_bytes(
        request.company.company_uid,
        {"logo_path": row.logo_path, "logo_sha256": row.logo_sha256},
    )
    if not raw:
        return HttpResponse(status=404)
    return HttpResponse(raw, content_type="image/png")


def preferences(request):
    if not request.user.is_authenticated:
        return redirect("/connexion")
    select_company(request)
    if request.method == "POST":
        theme, density = request.POST.get("theme"), request.POST.get("density")
        if theme not in {"system", "light", "dark"} or density not in {
            "comfortable",
            "compact",
        }:
            return render(
                request,
                "core/error.html",
                {"error": "Préférences inconnues."},
                status=422,
            )
        UserPreferences.objects.update_or_create(
            user_uid=request.user.user_uid,
            defaults={"theme": theme, "density": density},
        )
        messages.success(
            request, "Préférences d’affichage enregistrées pour votre compte."
        )
        return redirect("/app/compte/affichage")
    row = (
        UserPreferences.objects.filter(user_uid=request.user.user_uid).first()
        or UserPreferences()
    )
    return render(
        request,
        "core/preferences.html",
        {"title": "Préférences d’affichage", "preferences": row},
    )
