import secrets
from datetime import date, timedelta
from decimal import Decimal
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import render, redirect
from core.common import (
    BusinessError,
    stamp,
    today,
    email,
    digest,
    decimal,
    iso_date,
    country,
)
from core.models import (
    Company,
    CompanyMember,
    CompanyInvitation,
    User,
    Customer,
    InventoryItem,
    StockMovement,
    ApiKey,
    Email,
    Document,
    BankAccount,
)
from core.forms import CustomerForm, CompanyForm, InventoryForm
from core.security import (
    scoped,
    scoped_get,
    select_company,
    ROLES,
    hidden_expense_documents,
)
from core.services.documents import save_customer
from core.services import files
from core.services.mail import queue
from core.services.account_security import audit, admin_security_redirect


def table(request, title, headers, rows, **context):
    from django.core.paginator import Paginator

    page = Paginator(rows, 40).get_page(request.GET.get("page"))
    return render(
        request,
        "core/table.html",
        dict(title=title, headers=headers, rows=page, **context),
    )


@scoped()
def dashboard(request):
    from core.services import reports

    selected = reports.period(request.GET)
    comparison = reports.summary(request, selected)
    docs = Document.objects.filter(company_uid=request.company.company_uid).exclude(
        document_uid__in=hidden_expense_documents(request)
    )
    docs = reports.between(docs, "issue_date", selected.start, selected.end)
    active = docs.exclude(
        status__in=["draft", "cancelled", "refused", "simulated"]
    ).exclude(type__in=["quote", "credit_note"])
    totals = []
    for currency in sorted(
        set(active.values_list("currency_code", flat=True)) or {"EUR"}
    ):
        subset = active.filter(currency_code=currency)
        totals.append(
            {
                "currency": currency,
                "receivables": subset.filter(direction="outgoing").aggregate(
                    n=Sum("amount_due")
                )["n"]
                or 0,
                "payables": subset.filter(direction="incoming").aggregate(
                    n=Sum("amount_due")
                )["n"]
                or 0,
            }
        )
    return render(
        request,
        "core/dashboard.html",
        {
            "title": "Vue d'ensemble",
            "period": selected,
            "report": comparison,
            "totals": totals,
            "documents": docs.order_by("-id")[:8],
            "quotes": docs.filter(type="quote", status="sent").count(),
            "drafts": docs.filter(status="draft").count(),
            "late": active.filter(
                direction="outgoing", due_date__lt=today(), amount_due__gt=0
            ).count(),
        },
    )


def companies(request, company_uid=None, action=None):
    if not request.user.is_authenticated:
        return redirect("/connexion")
    select_company(request)
    if request.method == "POST":
        if company_uid:
            if not CompanyMember.objects.filter(
                company_uid=company_uid, user_uid=request.user.user_uid, status="active"
            ).exists():
                return render(
                    request,
                    "core/error.html",
                    {"error": "Entreprise inaccessible."},
                    status=403,
                )
            request.session["company_uid"] = company_uid
            return redirect("/app")
        name = request.POST.get("legal_name", "").strip()
        if not name:
            return render(
                request,
                "core/error.html",
                {"error": "Nom d'entreprise requis."},
                status=422,
            )
        with transaction.atomic():
            company = Company.objects.create(
                owner_user_uid=request.user.user_uid,
                legal_name=name,
                billing_email=request.user.email,
            )
            CompanyMember.objects.create(
                company_uid=company.company_uid,
                user_uid=request.user.user_uid,
                role="owner",
                joined_at=stamp(),
            )
        request.session["company_uid"] = company.company_uid
        return redirect("/app/entreprise")
    memberships = CompanyMember.objects.filter(
        user_uid=request.user.user_uid, status="active"
    )
    return render(
        request,
        "core/companies.html",
        {
            "title": "Mes entreprises",
            "companies": Company.objects.filter(
                company_uid__in=memberships.values("company_uid")
            ),
        },
    )


@scoped()
def company(request):
    if request.method == "POST" and request.role not in ROLES["admin"]:
        raise BusinessError(
            "Administration de l'entreprise réservée aux responsables.", 403
        )
    if request.method == "POST":
        destination = admin_security_redirect(request)
        if destination:
            return redirect(destination)
    form = CompanyForm(request.POST or None, instance=request.company)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.country_code = country(obj.country_code)
        obj.billing_email = email(obj.billing_email, optional=True)
        obj.support_email = email(obj.support_email, optional=True)
        if obj.has_vat_on_debits not in (None, 0, 1):
            raise BusinessError("L'option TVA doit valoir 0 ou 1.")
        for name in (
            "quote_number_prefix",
            "invoice_number_prefix",
            "credit_note_number_prefix",
        ):
            value = getattr(obj, name)
            if not value or len(value) > 20 or not value.replace("-", "").isalnum():
                raise BusinessError("Préfixe de numérotation invalide.")
        obj.updated_at = stamp()
        obj.save()
        audit(request, "company.updated")
        messages.success(request, "Entreprise mise à jour.")
        return redirect("/app/entreprise")
    return render(
        request,
        "core/form.html",
        {
            "title": "Mon entreprise",
            "form": form,
            "extra_link": "/app/entreprise/pdp"
            if request.role in ROLES["admin"]
            else "",
            "extra_label": "Connexion SUPERPDP",
            "can_write": request.role in ROLES["admin"],
        },
    )


@scoped("write")
def customers(request, customer_uid=None, action=None):
    if customer_uid and not action and request.method == "GET":
        from .crm import customer

        return customer(request, customer_uid)
    obj = (
        scoped_get(Customer, request, customer_uid=customer_uid)
        if customer_uid
        else None
    )
    if request.method == "POST" and action == "archiver":
        obj.status = "archived"
        obj.updated_at = stamp()
        obj.save()
        return redirect("/app/clients")
    if action == "annuaire" and obj:
        from core.services.pdp import directory

        result = directory(
            request.company, obj, request.POST.get("lookup", "directory")
        )
        return render(
            request,
            "core/directory.html",
            {"title": "Recherche entreprise", "customer": obj, "result": result},
        )
    if obj or action == "new" or request.method == "POST":
        form = CustomerForm(request.POST or None, instance=obj)
        if request.method == "POST" and form.is_valid():
            from core.services import crm

            relationship = request.POST.get("relationship", "customer")
            if relationship not in dict(crm.RELATIONSHIPS):
                raise BusinessError("Relation commerciale invalide.")
            with transaction.atomic():
                customer = save_customer(request.company, form.cleaned_data, obj)
                if not obj:
                    crm.save_profile(customer, {"relationship": relationship})
            messages.success(request, "Client enregistré.")
            return redirect("/app/clients/" + customer.customer_uid)
        return render(
            request,
            "core/form.html",
            {
                "title": obj.legal_name if obj else "Nouveau client",
                "form": form,
                "new_customer": not obj,
                "archive_url": "/app/clients/" + obj.customer_uid + "/archiver"
                if obj
                else "",
                "directory_url": "/app/clients/" + obj.customer_uid + "/annuaire"
                if obj
                else "",
            },
        )
    query = Customer.objects.filter(
        company_uid=request.company.company_uid,
        status=request.GET.get("status", "active"),
    )
    if request.GET.get("q"):
        query = query.filter(
            Q(legal_name__icontains=request.GET["q"][:200])
            | Q(email__icontains=request.GET["q"][:200])
        )
    from core.models import CustomerProfile

    profiles = CustomerProfile.objects.filter(company_uid=request.company.company_uid)
    if request.GET.get("type") in {"business", "individual"}:
        query = query.filter(type=request.GET["type"])
    if request.GET.get("relationship"):
        selected = request.GET["relationship"]
        condition = Q(
            customer_uid__in=profiles.filter(relationship=selected).values(
                "customer_uid"
            )
        )
        if selected == "customer":
            condition |= ~Q(customer_uid__in=profiles.values("customer_uid"))
        query = query.filter(condition)
    profile_map = {p.customer_uid: p for p in profiles}
    from core.services.crm import RELATIONSHIPS

    return table(
        request,
        "Clients",
        ["Client", "Type", "Relation", "Ville", "Email", "Statut"],
        [
            {
                "url": "/app/clients/" + c.customer_uid,
                "cells": [
                    c.legal_name,
                    "Particulier" if c.type == "individual" else "Professionnel",
                    dict(RELATIONSHIPS).get(
                        getattr(
                            profile_map.get(c.customer_uid), "relationship", "customer"
                        )
                    ),
                    c.city,
                    c.email,
                    c.status,
                ],
            }
            for c in query.order_by("legal_name")
        ],
        new_url="/app/clients/nouveau",
        customer_filters=True,
    )


@scoped("write")
def inventory(request, item_uid=None, action=None):
    if request.role not in ROLES["write"]:
        raise BusinessError("Votre rôle ne permet pas d'accéder à l'inventaire.", 403)
    item = scoped_get(InventoryItem, request, item_uid=item_uid) if item_uid else None
    if action == "image" and item and request.method == "GET":
        if not item.image_path:
            raise BusinessError("Image absente.", 404)
        target = files.resolve(item.image_path)
        if not target.is_file():
            raise BusinessError("Image absente.", 404)
        response = HttpResponse(
            target.read_bytes(), content_type=item.image_mime_type or "image/png"
        )
        response["Cache-Control"] = "private, no-store"
        return response
    if request.method == "POST" and action == "archiver":
        item.status = "archived"
        item.save()
        return redirect("/app/inventaire")
    if request.method == "POST" and action == "mouvements":
        with transaction.atomic():
            item = InventoryItem.objects.select_for_update().get(pk=item.pk)
            qty = decimal(request.POST.get("quantity"))
            direction = request.POST.get("movement_type")
            if not item.stock_tracking or qty <= 0 or direction not in {"in", "out"}:
                raise BusinessError("Mouvement de stock invalide.")
            if direction == "out" and qty > item.stock:
                raise BusinessError("Stock insuffisant.")
            StockMovement.objects.create(
                company_uid=item.company_uid,
                item_uid=item.item_uid,
                movement_type=direction,
                quantity=qty,
                occurred_on=iso_date(request.POST.get("occurred_on") or today()),
                reason=request.POST.get("reason"),
                reference=request.POST.get("movement_reference"),
                created_by_user_uid=request.user.user_uid,
            )
        return redirect("/app/inventaire/" + item.item_uid)
    if item or action == "new" or request.method == "POST":
        form = InventoryForm(request.POST or None, instance=item)
        if request.method == "POST" and form.is_valid():
            obj = form.save(commit=False)
            if (
                obj.unit_price_excluding_tax < 0
                or not 0 <= obj.vat_rate <= 100
                or obj.stock_tracking not in (0, 1)
            ):
                raise BusinessError("Prix, TVA ou suivi de stock invalide.")
            obj.company_uid = request.company.company_uid
            if request.FILES.get("image"):
                metadata = files.upload(
                    request.FILES["image"],
                    f"inventory/{obj.company_uid}/{obj.item_uid}",
                )
                if not metadata["mime_type"].startswith("image/"):
                    raise BusinessError("Choisissez une image.")
                obj.image_path, obj.image_mime_type = (
                    metadata["storage_path"],
                    metadata["mime_type"],
                )
            obj.updated_at = stamp()
            obj.save()
            return redirect("/app/inventaire/" + obj.item_uid)
        return render(
            request,
            "core/inventory.html",
            {
                "title": item.name if item else "Nouvel article",
                "form": form,
                "item": item,
                "movements": StockMovement.objects.filter(item_uid=item_uid).order_by(
                    "-id"
                )
                if item
                else [],
                "today": today(),
            },
        )
    query = InventoryItem.objects.filter(
        company_uid=request.company.company_uid,
        status=request.GET.get("status", "active"),
    )
    if request.GET.get("q"):
        query = query.filter(
            Q(name__icontains=request.GET["q"][:200])
            | Q(reference__icontains=request.GET["q"][:200])
        )
    return table(
        request,
        "Inventaire",
        ["Article", "Référence", "Type", "Prix HT", "TVA %", "Stock"],
        [
            {
                "url": "/app/inventaire/" + i.item_uid,
                "cells": [
                    i.name,
                    i.reference,
                    i.item_type,
                    f"{i.unit_price_excluding_tax:.2f}",
                    f"{i.vat_rate:g}",
                    str(i.stock) if i.stock_tracking else "—",
                ],
            }
            for i in query.order_by("name")
        ],
        new_url="/app/inventaire/nouveau",
    )


@scoped("admin")
def collaborators(request, user_uid=None, invitation_uid=None, action=None):
    if request.method == "POST":
        with transaction.atomic():
            Company.objects.select_for_update().get(pk=request.company.pk)
            if invitation_uid:
                invitation = scoped_get(
                    CompanyInvitation, request, invitation_uid=invitation_uid
                )
                if invitation.status != "pending":
                    raise BusinessError("Invitation déjà traitée.")
                invitation.status = "cancelled"
                invitation.save()
            elif user_uid:
                member = CompanyMember.objects.filter(
                    company_uid=request.company.company_uid,
                    user_uid=user_uid,
                    status="active",
                ).first()
                if not member:
                    raise BusinessError("Collaborateur introuvable.", 404)
                if member.role == "owner" or member.user_uid == request.user.user_uid:
                    raise BusinessError(
                        "Le propriétaire et votre propre accès sont protégés."
                    )
                role = request.POST.get("role")
                if action == "retirer":
                    member.status = "removed"
                else:
                    if role not in {"admin", "accountant", "member", "viewer"}:
                        raise BusinessError("Rôle invalide.")
                    member.role = role
                member.save()
                audit(
                    request,
                    "team.member_removed"
                    if action == "retirer"
                    else "team.role_changed",
                    user_uid,
                )
            else:
                address = email(request.POST.get("email"))
                role = request.POST.get("role", "member")
                if role not in {"admin", "accountant", "member", "viewer"}:
                    raise BusinessError("Rôle invalide.")
                invitation = CompanyInvitation.objects.create(
                    company_uid=request.company.company_uid,
                    invited_by_user_uid=request.user.user_uid,
                    email=address,
                    role=role,
                    token=secrets.token_hex(32),
                    expires_at=str(date.fromisoformat(today()) + timedelta(days=7))
                    + " 23:59:59",
                )
                queue(
                    request.company.company_uid,
                    None,
                    "invitation",
                    address,
                    "Invitation à " + request.company.legal_name,
                    settings.APP_URL + "/invitation/" + invitation.token,
                )
                audit(request, "team.invitation_created", invitation.invitation_uid)
        return redirect("/app/collaborateurs")
    members = []
    for member in CompanyMember.objects.filter(
        company_uid=request.company.company_uid, status="active"
    ):
        member.user = User.objects.filter(user_uid=member.user_uid).first()
        members.append(member)
    invitations = CompanyInvitation.objects.filter(
        company_uid=request.company.company_uid, status="pending"
    ).order_by("-id")
    return render(
        request,
        "core/collaborators.html",
        {
            "title": "Collaborateurs",
            "members": members,
            "invitations": invitations,
            "app_url": settings.APP_URL,
        },
    )


def accept_invitation(request, token):
    invitation = CompanyInvitation.objects.filter(
        token=token, status="pending", expires_at__gt=stamp()
    ).first()
    if not invitation:
        return render(
            request,
            "core/error.html",
            {"error": "Invitation expirée ou révoquée."},
            status=404,
        )
    if not request.user.is_authenticated:
        return redirect("/inscription?invite=" + token)
    if request.user.email.lower() != invitation.email.lower():
        return render(
            request,
            "core/error.html",
            {"error": "Connectez-vous avec l'adresse invitée."},
            status=403,
        )
    if request.method == "POST":
        with transaction.atomic():
            invitation = CompanyInvitation.objects.select_for_update().get(
                pk=invitation.pk
            )
            if invitation.status == "pending":
                CompanyMember.objects.update_or_create(
                    company_uid=invitation.company_uid,
                    user_uid=request.user.user_uid,
                    defaults={
                        "role": invitation.role,
                        "status": "active",
                        "joined_at": stamp(),
                    },
                )
                invitation.status, invitation.accepted_at = "accepted", stamp()
                invitation.save()
        request.session["company_uid"] = invitation.company_uid
        return redirect("/app")
    return render(
        request,
        "core/invitation.html",
        {"title": "Accepter l'invitation", "invitation": invitation},
    )


@scoped("admin")
def developers(request, key_uid=None, action=None):
    secret = ""
    if request.method == "POST":
        if key_uid:
            key = scoped_get(ApiKey, request, key_uid=key_uid)
            key.status, key.revoked_at = "revoked", stamp()
            key.save()
            audit(request, "api.key_revoked", key.key_uid)
            return redirect("/app/developpeurs")
        name = request.POST.get("name", "").strip()
        if not name:
            raise BusinessError("Nom de clé requis.")
        secret = "zf_live_" + secrets.token_hex(24)
        key = ApiKey.objects.create(
            company_uid=request.company.company_uid,
            name=name,
            key_hash=digest(secret),
            prefix=secret[:16],
        )
        audit(request, "api.key_created", key.key_uid)
    return render(
        request,
        "core/developers.html",
        {
            "title": "API et intégrations",
            "keys": ApiKey.objects.filter(
                company_uid=request.company.company_uid
            ).order_by("-id"),
            "secret": secret,
        },
    )


@scoped("admin")
def emails(request, email_uid=None):
    if request.method == "POST" and email_uid:
        item = scoped_get(Email, request, email_uid=email_uid)
        if item.status == "sent":
            raise BusinessError("Cet email a déjà été envoyé.")
        from core.services.email_connections import route_email

        with transaction.atomic():
            route_email(item)
            item.status, item.last_error = "queued", None
            item.save()
        messages.success(request, "Email remis en file d'attente.")
        return redirect("/app/emails")
    from core.models import EmailConnection

    connection = EmailConnection.objects.filter(
        company_uid=request.company.company_uid
    ).first()
    return render(
        request,
        "core/emails.html",
        {
            "title": "Emails",
            "emails": Email.objects.filter(
                company_uid=request.company.company_uid
            ).order_by("-id")[:100],
            "transport": connection.provider
            if connection and connection.provider != "instance"
            else settings.MAIL_TRANSPORT,
        },
    )
