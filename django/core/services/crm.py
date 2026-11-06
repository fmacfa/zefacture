from datetime import date
import re
from django.db import transaction
from django.utils import timezone
from core.common import BusinessError, decimal, email, iso_date, money, today
from core.models import (
    CompanyMember,
    Customer,
    CustomerProfile,
    CustomerContact,
    Opportunity,
    CrmActivity,
)

RELATIONSHIPS = [
    ("prospect", "Prospect"),
    ("customer", "Client"),
    ("supplier", "Fournisseur"),
    ("both", "Client et fournisseur"),
]
CURRENCIES = {"EUR", "USD", "GBP", "CHF", "CAD", "JPY", "SEK", "NOK", "DKK", "PLN"}


def text(data, key, limit=200, required=False):
    value = str(data.get(key) or "").strip()
    if len(value) > limit or (required and not value):
        raise BusinessError(
            f"Le champ {key} est requis et limité à {limit} caractères."
            if required
            else f"Le champ {key} dépasse {limit} caractères."
        )
    return value


def assigned(company_uid, value):
    value = str(value or "")
    if (
        value
        and not CompanyMember.objects.filter(
            company_uid=company_uid, user_uid=value, status="active"
        ).exists()
    ):
        raise BusinessError("Choisissez un membre actif de cette entreprise.")
    return value


def save_profile(customer, data):
    relationship = data.get("relationship", "customer")
    if relationship not in dict(RELATIONSHIPS):
        raise BusinessError("Relation commerciale invalide.")
    return CustomerProfile.objects.update_or_create(
        customer_uid=customer.customer_uid,
        company_uid=customer.company_uid,
        defaults={
            "relationship": relationship,
            "tags": text(data, "tags", 500),
            "notes": text(data, "notes", 20000),
            "assigned_to_user_uid": assigned(
                customer.company_uid, data.get("assigned_to_user_uid")
            ),
        },
    )[0]


def add_contact(customer, data):
    return CustomerContact.objects.create(
        customer_uid=customer.customer_uid,
        company_uid=customer.company_uid,
        name=text(data, "name", required=True),
        position=text(data, "position"),
        email=email(data.get("email"), optional=True),
        phone=text(data, "phone", 80),
    )


def save_opportunity(customer, data, opportunity=None):
    opportunity = opportunity or Opportunity(
        customer_uid=customer.customer_uid, company_uid=customer.company_uid
    )
    if (opportunity.customer_uid, opportunity.company_uid) != (
        customer.customer_uid,
        customer.company_uid,
    ):
        raise BusinessError("Opportunité inaccessible.", 404)
    stage = data.get("stage", "prospect")
    if stage not in dict(Opportunity.STAGES):
        raise BusinessError("Étape commerciale invalide.")
    value = money(decimal(data.get("amount")))
    if value < 0 or data.get("currency_code", "EUR") not in CURRENCIES:
        raise BusinessError("Montant ou devise invalide.")
    opportunity.title = text(data, "title", 240, required=True)
    opportunity.amount = value
    opportunity.currency_code = data.get("currency_code", "EUR")
    opportunity.expected_close = iso_date(data.get("expected_close"), optional=True)
    if stage in {"won", "lost"}:
        if stage != opportunity.stage or not opportunity.closed_on:
            opportunity.closed_on = date.fromisoformat(today())
    else:
        opportunity.closed_on = None
    opportunity.stage = stage
    opportunity.notes = text(data, "notes", 20000)
    opportunity.assigned_to_user_uid = assigned(
        customer.company_uid, data.get("assigned_to_user_uid")
    )
    opportunity.save()
    if stage == "won":
        profile, _ = CustomerProfile.objects.get_or_create(
            company_uid=customer.company_uid, customer_uid=customer.customer_uid
        )
        if profile.relationship == "prospect":
            profile.relationship = "customer"
            profile.save()
    return opportunity


@transaction.atomic
def add_activity(customer, user, data):
    Customer.objects.select_for_update().get(pk=customer.pk)
    nonce = str(data.get("activity_nonce") or "")
    if nonce:
        if not re.fullmatch(r"[a-f0-9]{32}", nonce):
            raise BusinessError("Identifiant d'activité invalide.")
        existing = CrmActivity.objects.filter(activity_uid=nonce).first()
        if existing:
            if (
                existing.company_uid != customer.company_uid
                or existing.customer_uid != customer.customer_uid
            ):
                raise BusinessError("Activité inaccessible.", 404)
            if (existing.kind, existing.title, existing.body) != (
                data.get("kind", "note"),
                str(data.get("title") or "").strip(),
                str(data.get("body") or "").strip(),
            ):
                raise BusinessError(
                    "Ce formulaire a déjà été enregistré avec un autre contenu. Rechargez la fiche avant de créer une nouvelle activité.",
                    409,
                )
            return existing
    kind = data.get("kind", "note")
    if kind not in dict(CrmActivity.KINDS):
        raise BusinessError("Type d'activité invalide.")
    title = text(data, "title", 240, required=True)
    body = text(data, "body", 50000)
    email_uid = ""
    if kind == "email":
        contact_uid = data.get("contact_uid")
        contact = (
            CustomerContact.objects.filter(
                company_uid=customer.company_uid,
                customer_uid=customer.customer_uid,
                contact_uid=contact_uid,
            ).first()
            if contact_uid
            else None
        )
        if contact_uid and not contact:
            raise BusinessError("Contact introuvable.", 404)
        recipient = contact.email if contact else customer.email
        if not recipient or not body:
            raise BusinessError("Une adresse destinataire et un message sont requis.")
        from .mail import queue

        item = queue(customer.company_uid, None, "crm", recipient, title, body)
        email_uid = item.email_uid
    return CrmActivity.objects.create(
        **({"activity_uid": nonce} if nonce else {}),
        customer_uid=customer.customer_uid,
        company_uid=customer.company_uid,
        kind=kind,
        title=title,
        body=body,
        due_on=iso_date(data.get("due_on"), optional=True),
        assigned_to_user_uid=assigned(
            customer.company_uid, data.get("assigned_to_user_uid")
        ),
        created_by_user_uid=user.user_uid,
        email_uid=email_uid,
    )


def complete(activity):
    activity.completed_at = timezone.now()
    activity.save(update_fields=["completed_at"])
