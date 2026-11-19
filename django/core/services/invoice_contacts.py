from django.db import transaction
from django.db.models import Q

from core.common import BusinessError, email
from core.models import (
    Company,
    Customer,
    CustomerContact,
    CustomerProfile,
    Document,
    DocumentContact,
    PdpInvoice,
)


def contact_info(document, party):
    side = "seller" if document.direction == "incoming" else "buyer"
    pdp = (
        PdpInvoice.objects.filter(
            company_uid=document.company_uid,
            document_uid=document.document_uid,
        )
        .values_list("payload", flat=True)
        .first()
    )
    details = pdp
    for field in ("en_invoice", side, "contact"):
        details = details.get(field) if isinstance(details, dict) else None
    if not isinstance(details, dict):
        details = {}
    name = str(details.get("contact_point") or party.get("contact_name") or "").strip()
    address = str(
        details.get("email_address")
        or party.get("contact_email")
        or party.get("email")
        or ""
    ).strip()
    try:
        address = email(address, optional=True)
    except BusinessError:
        address = ""
    phone = str(
        details.get("phone_number")
        or party.get("contact_phone")
        or party.get("phone")
        or ""
    ).strip()[:80]
    return name[:200], address if len(address) <= 254 else "", phone


def unique_match(query):
    matches = list(query.order_by("pk")[:2])
    return (matches[0] if len(matches) == 1 else None), bool(matches)


def historical_customer(document, party, address):
    """Identify older invoices without a CRM id, without editing their legal snapshots."""
    name = str(party.get("name") or "").strip()
    if not name:
        return None
    cards = Customer.objects.filter(company_uid=document.company_uid)
    identifiers = {
        k: party[k]
        for k in ("siren", "siret", "vat_number", "electronic_address")
        if party.get(k)
    }
    if identifiers:
        condition = Q()
        for key, value in identifiers.items():
            condition |= Q(**{key + "__iexact": value})
        card, found = unique_match(cards.filter(condition))
        if found:
            if (
                card
                and card.status == "active"
                and all(
                    not getattr(card, k)
                    or getattr(card, k).casefold() == str(v).casefold()
                    for k, v in identifiers.items()
                )
            ):
                return card
            return None
    same_name = cards.filter(legal_name__iexact=name)
    if same_name.exists():
        evidence = Q()
        if address:
            evidence |= Q(email__iexact=address)
        if party.get("address_line1") and party.get("postal_code"):
            evidence |= Q(
                address_line1__iexact=party["address_line1"],
                postal_code=party["postal_code"],
            )
        if evidence:
            card, _ = unique_match(same_name.filter(evidence))
            if (
                card
                and card.status == "active"
                and all(
                    not getattr(card, k)
                    or getattr(card, k).casefold() == str(v).casefold()
                    for k, v in identifiers.items()
                )
            ):
                return card
        return None
    if not (
        identifiers
        or address
        or (party.get("address_line1") and party.get("postal_code"))
    ):
        return None
    card = cards.create(
        company_uid=document.company_uid,
        legal_name=name,
        type="individual" if party.get("type") == "individual" else "business",
        email=address,
        country_code=party.get("country_code") or "FR",
        **{
            k: party[k]
            for k in ("address_line1", "postal_code", "city", *identifiers)
            if party.get(k)
        },
    )
    CustomerProfile.objects.create(
        company_uid=document.company_uid,
        customer_uid=card.customer_uid,
        relationship="supplier" if document.direction == "incoming" else "customer",
    )
    return card


@transaction.atomic
def assign_automatically(document):
    """Resolve the counterparty of a new or historical invoice within its company."""
    if document.type not in {"invoice", "progress_invoice", "credit_note"}:
        return False
    Company.objects.select_for_update().get(company_uid=document.company_uid)
    document = Document.objects.select_for_update().get(pk=document.pk)
    link = (
        DocumentContact.objects.filter(document=document)
        .select_related("contact")
        .first()
    )
    if link and not link.automatic:
        return False
    party = document.seller if document.direction == "incoming" else document.buyer
    name, address, phone = contact_info(document, party)
    customer = Customer.objects.filter(
        company_uid=document.company_uid,
        customer_uid=document.customer_uid,
        status="active",
    ).first()
    if not document.customer_uid:
        customer = historical_customer(document, party, address)
    if not customer:
        if link:
            link.delete()
        return False
    contacts = CustomerContact.objects.filter(
        company_uid=document.company_uid, customer_uid=customer.customer_uid
    )
    contact = None
    ambiguous = False
    if address:
        contact, ambiguous = unique_match(contacts.filter(email__iexact=address))
        if name and contact and contact.name.casefold() != name.casefold():
            contact, ambiguous = unique_match(
                contacts.filter(email__iexact=address, name__iexact=name)
            )
            ambiguous = not contact
    if not contact and name and not ambiguous:
        contact, ambiguous = unique_match(contacts.filter(name__iexact=name))
        if (
            contact
            and address
            and contact.email
            and contact.email.casefold() != address.casefold()
        ):
            contact, ambiguous = None, True
    if not name and not address:
        contact, ambiguous = unique_match(contacts)
    if not contact and not ambiguous:
        # A legal entity can have a billing contact in its own name.
        label = name or party.get("name") or customer.legal_name
        if label:
            contact = contacts.create(
                company_uid=document.company_uid,
                customer_uid=customer.customer_uid,
                name=str(label)[:200],
                email=address,
                phone=phone,
                position="" if name or customer.type == "individual" else "Facturation",
            )
    if contact:
        if link and link.contact_id == contact.pk:
            return False
        DocumentContact.objects.update_or_create(
            document=document, defaults={"contact": contact, "automatic": True}
        )
        return True
    if link:
        link.delete()
    return False
