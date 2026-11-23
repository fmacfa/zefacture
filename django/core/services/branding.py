"""Tenant-owned presentation, immutable document branding, and plain-text mail."""

import hashlib
import re
import string
from io import BytesIO
from types import SimpleNamespace
from PIL import Image, ImageOps, UnidentifiedImageError
from django.db import transaction
from core.numbers import document_amount, document_style
from core.common import BusinessError, uid, email
from core.models import CompanyBranding, Document, EmailEnvelope
from . import files

DEFAULT_ACCENT = CompanyBranding.DEFAULT_ACCENT

TEMPLATE_NAMES = {
    "quote": "Devis",
    "invoice": "Facture / avoir",
    "invoice_reminder": "Relance de facture",
}
VARIABLES = {
    "company",
    "customer",
    "document_number",
    "total",
    "amount_due",
    "currency",
    "due_date",
    "document_link",
    "original_body",
    "signature",
}
SUPPORTED_CURRENCIES = {
    "EUR",
    "USD",
    "GBP",
    "CHF",
    "CAD",
    "JPY",
    "SEK",
    "NOK",
    "DKK",
    "PLN",
}
DEFAULT_TEMPLATE = {
    "subject": "{document_number}",
    "body": "{original_body}\n\n{signature}",
}


def for_company(company):
    return CompanyBranding.objects.filter(
        company_uid=company.company_uid
    ).first() or CompanyBranding(
        company_uid=company.company_uid
    )


def validate_template(value):
    try:
        for _, field, spec, conversion in string.Formatter().parse(value):
            if field is not None and (field not in VARIABLES or spec or conversion):
                raise ValueError()
    except ValueError:
        raise BusinessError(
            "Variable de modèle inconnue. Utilisez uniquement les variables proposées, sans attribut ni format."
        )


def normalized_logo(uploaded, company_uid):
    if uploaded.size > 2 * 1024 * 1024:
        raise BusinessError("Logo limité à 2 Mo.")
    try:
        with Image.open(BytesIO(uploaded.read())) as source:
            if (
                source.format not in {"PNG", "JPEG", "WEBP"}
                or source.width * source.height > 16_000_000
            ):
                raise ValueError()
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.thumbnail((1024, 1024))
            image.info.clear()
            out = BytesIO()
            image.save(out, format="PNG", optimize=True)
        raw = out.getvalue()
    except (
        ValueError,
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise BusinessError(
            "Choisissez un logo PNG, JPEG ou WebP de moins de 16 mégapixels."
        ) from exc
    return files.put(f"branding/{company_uid}/{uid()}.png", raw), hashlib.sha256(
        raw
    ).hexdigest()


def identity_values(data):
    color = str(data.get("accent_color", DEFAULT_ACCENT))
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise BusinessError(f"Utilisez un code couleur hexadécimal comme {DEFAULT_ACCENT}.")
    name = str(data.get("display_name", "")).strip()
    if len(name) > 120 or any(c in name for c in "\r\n"):
        raise BusinessError("Nom d’affichage limité à 120 caractères, sur une ligne.")
    return {"display_name": name, "accent_color": color.lower()}


def document_values(data):
    layout = data.get("document_layout", "classic")
    if layout not in {"classic", "compact"}:
        raise BusinessError("Modèle de document inconnu.")
    try:
        currency = str(data.get("default_currency", "EUR")).upper()
        if currency not in SUPPORTED_CURRENCIES:
            raise ValueError()
        payment = int(data.get("payment_due_days", 30))
        quote = int(data.get("quote_validity_days", 30))
        if not 0 <= payment <= 365 or not 1 <= quote <= 365:
            raise ValueError()
    except (ValueError, TypeError):
        raise BusinessError(
            "Devise ISO valide et délais de 0 à 365 jours requis (devis : 1 minimum)."
        )
    return {
        "document_layout": layout,
        "default_currency": currency,
        "payment_due_days": payment,
        "quote_validity_days": quote,
    }


def email_values(data):
    reply_to = email(data.get("reply_to"), optional=True)
    signature = str(data.get("email_signature", "")).strip()
    if len(signature) > 4000:
        raise BusinessError("Signature limitée à 4 000 caractères.")
    return {"reply_to": reply_to, "email_signature": signature}


def template_values(data):
    templates = {}
    for key in TEMPLATE_NAMES:
        subject, body = (
            str(data.get(key + "_subject", "")).strip(),
            str(data.get(key + "_body", "")).strip(),
        )
        if not subject and not body:
            continue
        if (
            not subject
            or not body
            or len(subject) > 240
            or len(body) > 10000
            or any(c in subject for c in "\r\n")
        ):
            raise BusinessError(
                "Chaque modèle comporte un objet sur une ligne (240 caractères) et un message (10 000 caractères)."
            )
        validate_template(subject)
        validate_template(body)
        templates[key] = {"subject": subject, "body": body}
    return {"email_templates": templates}


SECTION_VALUES = {
    "identite": identity_values,
    "documents": document_values,
    "emails": email_values,
    "modeles": template_values,
}


@transaction.atomic
def save(company, data, uploaded=None, *, section=None):
    validators = SECTION_VALUES.values() if section is None else (SECTION_VALUES[section],)
    updates = {}
    for validate in validators:
        updates.update(validate(data))
    row, _ = CompanyBranding.objects.get_or_create(company_uid=company.company_uid)
    row = CompanyBranding.objects.select_for_update().get(pk=row.pk)
    if section in (None, "identite"):
        if uploaded:
            updates["logo_path"], updates["logo_sha256"] = normalized_logo(
                uploaded, company.company_uid
            )
        elif data.get("remove_logo") == "1":
            updates["logo_path"], updates["logo_sha256"] = "", ""
    for field, value in updates.items():
        setattr(row, field, value)
    row.save(update_fields=[*updates, "updated_at"])
    return row


def luminance(rgb):
    values = [c / 255 for c in rgb]
    values = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in values
    ]
    return sum(c * w for c, w in zip(values, (0.2126, 0.7152, 0.0722)))


def palette(color):
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(color)):
        color = DEFAULT_ACCENT
    rgb = tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))
    light, dark = rgb, rgb
    while (1.05 / (luminance(light) + 0.05)) < 4.5:
        light = tuple(max(0, int(c * 0.90)) for c in light)
    while ((luminance(dark) + 0.05) / (0.02 + 0.05)) < 4.5:
        dark = tuple(min(255, c + max(1, int((255 - c) * 0.12))) for c in dark)
    hexa = lambda values: "#" + "".join(f"{c:02x}" for c in values)
    return {
        "is_custom": color.lower() != DEFAULT_ACCENT,
        "primary": hexa(light),
        "hover": hexa(tuple(int(c * 0.8) for c in light)),
        "dark_link": hexa(dark),
    }


def snapshot(company):
    row = for_company(company)
    return {
        "display_name": row.display_name,
        "accent_color": row.accent_color,
        "document_layout": row.document_layout,
        "logo_path": row.logo_path,
        "logo_sha256": row.logo_sha256,
    }


def logo_bytes(company_uid, branding):
    path = branding.get("logo_path", "")
    if not path:
        return None
    if (
        not path.startswith(f"storage/branding/{company_uid}/")
        or not path.endswith(".png")
        or not re.fullmatch(r"[a-f0-9]{64}", branding.get("logo_sha256", ""))
    ):
        raise BusinessError("Le logo du document est invalide.", 409)
    return files.read_verified(
        SimpleNamespace(storage_path=path, sha256=branding["logo_sha256"])
    )


def apply_mail(item, *, save_envelope=True):
    from core.models import Company

    company = Company.objects.get(company_uid=item.company_uid)
    brand = for_company(company)
    document = (
        Document.objects.filter(
            company_uid=item.company_uid, document_uid=item.document_uid
        ).first()
        if item.document_uid
        else None
    )
    if document and item.template in TEMPLATE_NAMES:
        template = brand.email_templates.get(item.template)
        if template:
            original = item.body_text
            marker = "\nConsulter et accepter : "
            link = (
                original.split(marker, 1)[1]
                if item.template == "quote" and marker in original
                else ""
            )
            variables = {
                "company": company.legal_name,
                "customer": document.buyer.get("name", ""),
                "document_number": document.number,
                "total": document_amount(document,document.total_including_tax),
                "amount_due": document_style(document).format(document.amount_due, 2),
                "currency": document.currency_code,
                "due_date": document.due_date or "",
                "document_link": link,
                "original_body": original,
                "signature": brand.email_signature,
            }
            validate_template(template["subject"])
            validate_template(template["body"])
            item.subject = template["subject"].format_map(variables)
            item.body_text = template["body"].format_map(variables)
            if item.template == "invoice_reminder" and "{amount_due}" not in template["body"] and "{original_body}" not in template["body"]:
                item.body_text += "\n\nSolde restant à régler : " + variables["amount_due"] + " " + document.currency_code
            if link and link not in item.body_text:
                item.body_text += marker + link
            if brand.email_signature and "{signature}" not in template["body"]:
                item.body_text += "\n\n" + brand.email_signature
        elif brand.email_signature:
            item.body_text += "\n\n" + brand.email_signature
    elif brand.email_signature:
        item.body_text += "\n\n" + brand.email_signature
    if len(item.subject) > 998 or any(c in item.subject for c in "\r\n"):
        raise BusinessError("Objet d’email invalide après personnalisation.")
    if brand.reply_to and save_envelope:
        EmailEnvelope.objects.update_or_create(
            email_uid=item.email_uid, defaults={"reply_to": brand.reply_to}
        )
