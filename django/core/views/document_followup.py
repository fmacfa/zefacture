import re

from django.db import transaction
from django.db.models import OuterRef
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from core.common import BusinessError, uid
from core.models import (
    Company,
    Customer,
    CustomerContact,
    Document,
    DocumentContact,
    DocumentNote,
)
from core.security import ROLES, scoped, scoped_get
from core.services import crm
from core.services.documents import save_customer


def active_customers(company):
    return Customer.objects.filter(company_uid=company.company_uid, status="active")


def followup_context(request, document):
    link = (
        DocumentContact.objects.filter(
            document=document,
            contact__company_uid=document.company_uid,
        )
        .select_related("contact")
        .first()
    )
    notes = document.internal_notes.select_related("author").order_by(
        "-created_at", "-pk"
    )
    context = {"crm_contact": link.contact if link else None, "internal_notes": notes}
    if request.role in ROLES["write"]:
        customers = active_customers(request.company)
        context.update(
            note_uid=uid(),
            crm_customers=customers.order_by("legal_name"),
            crm_contacts=CustomerContact.objects.filter(
                company_uid=document.company_uid,
                customer_uid__in=customers.values("customer_uid"),
            )
            .annotate(
                customer_name=customers.filter(
                    customer_uid=OuterRef("customer_uid")
                ).values("legal_name")[:1],
            )
            .order_by("customer_name", "name"),
        )
    return context


def assign_contact(request, document, action):
    company = request.company
    customers = active_customers(company)
    links = DocumentContact.objects.filter(document=document)
    if action == "retirer-contact":
        links.delete()
        return
    if action == "lier-contact":
        contact = CustomerContact.objects.filter(
            contact_uid=request.POST.get("contact_uid", ""),
            company_uid=company.company_uid,
            customer_uid__in=customers.values("customer_uid"),
        ).first()
        if not contact:
            raise BusinessError("Choisissez un contact actif de cette entreprise.")
    else:
        customer_uid = request.POST.get("customer_uid", "")
        if customer_uid == "new":
            party = (
                document.seller if document.direction == "incoming" else document.buyer
            )
            name = party.get("name") or crm.text(request.POST, "name", required=True)
            customer = customers.filter(customer_uid=document.customer_uid).first()
            if not customer:
                # Only reuse this document's explicitly linked party when the card matches.
                linked_ids = links.filter(
                    contact__company_uid=company.company_uid
                ).values("contact__customer_uid")
                customer = customers.filter(
                    customer_uid__in=linked_ids, legal_name=name
                ).first()
            if not customer:
                customer = save_customer(
                    company,
                    {
                        "legal_name": name,
                        "type": party.get("type") or "business",
                        "country_code": party.get("country_code") or "FR",
                    },
                )
                crm.save_profile(
                    customer,
                    {
                        "relationship": "supplier"
                        if document.direction == "incoming"
                        else "customer"
                    },
                )
        else:
            customer = customers.filter(customer_uid=customer_uid).first()
            if not customer:
                raise BusinessError("Choisissez une fiche CRM de cette entreprise.")
        # A repeat submission reuses the same person on the chosen card.
        contact = (
            CustomerContact.objects.filter(
                company_uid=company.company_uid,
                customer_uid=customer.customer_uid,
                name=crm.text(request.POST, "name", required=True),
                email__iexact=request.POST.get("email", "").strip(),
                phone=crm.text(request.POST, "phone", 80),
                position=crm.text(request.POST, "position"),
            )
            .order_by("pk")
            .first()
        )
        if not contact:
            contact = crm.add_contact(customer, request.POST)
    DocumentContact.objects.update_or_create(
        document=document, defaults={"contact": contact}
    )


@scoped("write")
@require_POST
@transaction.atomic
def followup(request, document_uid, action):
    Company.objects.select_for_update().get(pk=request.company.pk)
    document = scoped_get(Document, request, document_uid=document_uid)
    Document.objects.select_for_update().get(pk=document.pk)
    if action == "note":
        key = request.POST.get("note_uid", "")
        body = request.POST.get("body", "").strip()
        if not re.fullmatch("[0-9a-f]{32}", key) or not body or len(body) > 5000:
            raise BusinessError("Saisissez une note de 1 à 5 000 caractères.")
        if request.POST.get("edit") == "1":
            note = document.internal_notes.filter(note_uid=key).first()
            if not note:
                raise BusinessError("Note introuvable.", 404)
            if note.author_id != request.user.pk and request.role not in ROLES["admin"]:
                raise BusinessError("Modification de cette note non autorisée.", 403)
            if note.body != body:
                note.body = body
                note.save(update_fields=["body", "updated_at"])
        else:
            note, created = DocumentNote.objects.get_or_create(
                note_uid=key,
                defaults={"document": document, "author": request.user, "body": body},
            )
            if not created and (
                note.document_id != document.pk
                or note.author_id != request.user.pk
                or note.body != body
            ):
                raise BusinessError(
                    "Cette note a déjà été enregistrée. Rechargez la fiche.", 409
                )
        return redirect(document.url + "#notes-internes")
    assign_contact(request, document, action)
    return redirect(document.url + "#contact-crm")
