from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from core.common import BusinessError, today
from core.models import UserPreferences
from core.security import ROLES, scoped, select_company
from core.services import branding as service
from core.services.account_security import admin_security_redirect, audit


SECTIONS = {
    "identite": ("Identité visuelle", ("display_name", "accent_color")),
    "documents": (
        "Documents",
        ("document_layout", "default_currency", "payment_due_days", "quote_validity_days"),
    ),
    "emails": ("Adresse et signature", ("reply_to", "email_signature")),
    "modeles": ("Modèles d’emails", ()),
}


def invoice_example(company, brand, values, section):
    issue = date.fromisoformat(today())
    try:
        delay = max(0, min(365, int(values.get("payment_due_days", brand.payment_due_days))))
    except (ValueError, TypeError):
        delay = brand.payment_due_days
    color = values.get("accent_color", brand.accent_color)
    layout = values.get("document_layout", brand.document_layout)
    currency = values.get("default_currency", brand.default_currency)
    remove_logo = section == "identite" and values.get("remove_logo")
    logo_url = reverse("company_logo") if brand.logo_path else ""
    return {
        "document": {
            "number": "FA-EXEMPLE",
            "seller": {"name": company.legal_name},
            "buyer": {"name": "Client exemple"},
            "issue_date": issue.strftime("%d/%m/%Y"),
            "due_date": (issue + timedelta(days=delay)).strftime("%d/%m/%Y"),
            "currency_code": currency if currency in service.SUPPORTED_CURRENCIES else "EUR",
            "total_excluding_tax": Decimal("100"),
            "total_including_tax": Decimal("120"),
            "amount_due": Decimal("120"),
        },
        "lines": [
            {
                "description": "Prestation de démonstration",
                "quantity": Decimal("1"),
                "unit_price_excluding_tax": Decimal("100"),
                "discount_percent": Decimal("0"),
                "vat_rate": Decimal("20"),
                "total_excluding_tax": Decimal("100"),
            },
        ],
        "taxes": [{"rate": Decimal("20"), "basis": Decimal("100"), "tax": Decimal("20")}],
        "branding": {"display_name": values.get("display_name", brand.display_name)},
        "brand_color": service.palette(color)["primary"],
        "layout": "compact" if layout == "compact" else "classic",
        "logo_uri": "" if remove_logo else logo_url,
        "saved_logo_uri": logo_url,
        "issue_iso": issue.isoformat(),
    }


@require_http_methods(["GET", "HEAD", "POST"])
@scoped()
def branding(request, section=None):
    if request.role not in ROLES["admin"]:
        raise BusinessError("Votre rôle ne permet pas cette action.", 403)
    current = section or "identite"
    if current not in SECTIONS:
        raise Http404

    heading, fields = SECTIONS[current]
    section_url = reverse("branding_settings_section", args=[current])
    verification = admin_security_redirect(request)
    posting = request.method == "POST"
    legacy_post = posting and section is None
    error = ""
    if posting:
        if legacy_post and verification:
            return redirect(verification)
        if not legacy_post and request.POST.get("company_uid") != request.company.company_uid:
            raise BusinessError(
                "L’entreprise sélectionnée a changé. Rouvrez la rubrique à modifier.",
                409,
            )
        if not verification:
            try:
                with transaction.atomic():
                    service.save(
                        request.company, request.POST, request.FILES.get("logo"), section=section
                    )
                    audit(request, "branding.updated")
            except BusinessError as exc:
                if legacy_post:
                    raise
                error = str(exc)
            else:
                if legacy_post:
                    messages.success(request, "Personnalisation enregistrée pour cette entreprise.")
                    return redirect("branding_settings")
                messages.success(request, f"Réglages enregistrés dans la rubrique « {heading} ».")
                return redirect(section_url)

    row = service.for_company(request.company)
    values = {field: request.POST.get(field, getattr(row, field)) for field in fields}
    values["remove_logo"] = request.POST.get("remove_logo") == "1"
    mail_templates = []
    for key, label in service.TEMPLATE_NAMES.items():
        saved = row.email_templates.get(key, {})
        mail_templates.append(
            {
                "key": key,
                "label": label,
                "subject": request.POST.get(key + "_subject", saved.get("subject", "")),
                "body": request.POST.get(key + "_body", saved.get("body", "")),
            }
        )
    currencies = sorted(service.SUPPORTED_CURRENCIES)
    if current == "documents":
        values["default_currency"] = values["default_currency"].upper()
        if values["default_currency"] not in currencies:
            currencies.insert(0, values["default_currency"])

    return render(
        request,
        "core/branding.html",
        {
            "title": f"Personnalisation · {heading}" if section else "Personnalisation",
            "heading": heading,
            "section": current,
            "section_url": section_url,
            "sections": [
                (label, reverse("branding_settings_section", args=[key]), key == current)
                for key, (label, _) in SECTIONS.items()
            ],
            "values": values,
            "has_logo": bool(row.logo_path),
            "mail_templates": mail_templates,
            "mail_variables": sorted(service.VARIABLES),
            "currencies": currencies,
            "verification": verification,
            "attempted_save": posting,
            "form_error": error,
            "select_logo_again": posting and current == "identite" and bool(request.FILES.get("logo")),
            "invoice_preview": (
                invoice_example(request.company, row, values, current)
                if current in {"identite", "documents"} else None
            ),
        },
        status=403 if posting and verification else 422 if error else 200,
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
