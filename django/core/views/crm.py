from collections import defaultdict
from decimal import Decimal
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import render, redirect
from core.common import BusinessError, today, uid
from core.models import (
    Customer,
    CustomerProfile,
    CustomerContact,
    Opportunity,
    CrmActivity,
    CompanyMember,
    User,
    Document,
    Email,
)
from core.security import scoped, scoped_get, hidden_expense_documents
from core.services import crm as service


def members(company):
    ids = CompanyMember.objects.filter(
        company_uid=company.company_uid, status="active"
    ).values("user_uid")
    return User.objects.filter(user_uid__in=ids).order_by("name")


@scoped("write")
def overview(request):
    query = Opportunity.objects.filter(company_uid=request.company.company_uid)
    stage = request.GET.get("stage", "")
    owner = request.GET.get("owner", "")
    search = request.GET.get("q", "")[:200]
    if stage:
        query = query.filter(stage=stage)
    if owner:
        query = query.filter(assigned_to_user_uid=owner)
    if search:
        customers = Customer.objects.filter(
            company_uid=request.company.company_uid, legal_name__icontains=search
        ).values("customer_uid")
        query = query.filter(Q(title__icontains=search) | Q(customer_uid__in=customers))
    totals = defaultdict(Decimal)
    for item in query.exclude(stage__in=["won", "lost"]):
        totals[item.currency_code] += item.amount
    opportunities = Paginator(query.order_by("expected_close", "-id"), 40).get_page(
        request.GET.get("page")
    )
    names = dict(
        Customer.objects.filter(company_uid=request.company.company_uid).values_list(
            "customer_uid", "legal_name"
        )
    )
    for item in opportunities:
        item.customer_name = names.get(item.customer_uid, "")
    tasks = CrmActivity.objects.filter(
        company_uid=request.company.company_uid, kind="task", completed_at__isnull=True
    )
    if owner:
        tasks = tasks.filter(assigned_to_user_uid=owner)
    tasks = Paginator(tasks.order_by("due_on", "id"), 40).get_page(
        request.GET.get("tasks_page")
    )
    for task in tasks:
        task.customer_name = names.get(task.customer_uid, "")
    return render(
        request,
        "core/crm.html",
        {
            "title": "CRM",
            "opportunities": opportunities,
            "tasks": tasks,
            "stages": Opportunity.STAGES,
            "members": members(request.company),
            "pipeline": dict(totals),
            "today": today(),
            "q": search,
            "stage": stage,
            "owner": owner,
        },
    )


@scoped("write")
@transaction.atomic
def customer(request, customer_uid, action=None, item_uid=None):
    obj = scoped_get(Customer, request, customer_uid=customer_uid)
    if request.method == "POST":
        if action == "profile":
            service.save_profile(obj, request.POST)
        elif action == "contact":
            service.add_contact(obj, request.POST)
        elif action == "opportunity":
            opportunity = (
                scoped_get(
                    Opportunity,
                    request,
                    opportunity_uid=item_uid,
                    customer_uid=customer_uid,
                )
                if item_uid
                else None
            )
            service.save_opportunity(obj, request.POST, opportunity)
        elif action == "activity":
            service.add_activity(obj, request.user, request.POST)
        elif action == "complete":
            service.complete(
                scoped_get(
                    CrmActivity,
                    request,
                    activity_uid=item_uid,
                    customer_uid=customer_uid,
                )
            )
        else:
            raise BusinessError("Action CRM inconnue.", 404)
        return redirect("/app/clients/" + customer_uid)
    if action:
        raise BusinessError("Méthode non autorisée.", 405)
    profile = CustomerProfile.objects.filter(
        company_uid=obj.company_uid, customer_uid=customer_uid
    ).first() or CustomerProfile(relationship="customer")
    docs = (
        Document.objects.filter(company_uid=obj.company_uid, customer_uid=customer_uid)
        .exclude(document_uid__in=hidden_expense_documents(request))
        .order_by("-id")
    )
    activities = CrmActivity.objects.filter(
        company_uid=obj.company_uid, customer_uid=customer_uid
    ).order_by("-created_at")
    email_ids = activities.exclude(email_uid="").values("email_uid")
    emails = (
        Email.objects.filter(company_uid=obj.company_uid)
        .filter(
            Q(email_uid__in=email_ids) | Q(document_uid__in=docs.values("document_uid"))
        )
        .order_by("-id")
    )
    return render(
        request,
        "core/customer.html",
        {
            "title": obj.legal_name,
            "activity_nonce": uid(),
            "customer": obj,
            "profile": profile,
            "relationships": service.RELATIONSHIPS,
            "contacts": CustomerContact.objects.filter(
                company_uid=obj.company_uid, customer_uid=customer_uid
            ),
            "opportunities": Opportunity.objects.filter(
                company_uid=obj.company_uid, customer_uid=customer_uid
            ).order_by("-id"),
            "activities": Paginator(activities, 30).get_page(
                request.GET.get("activity_page")
            ),
            "documents": Paginator(docs, 20).get_page(request.GET.get("document_page")),
            "emails": Paginator(emails, 20).get_page(request.GET.get("email_page")),
            "members": members(request.company),
            "stages": Opportunity.STAGES,
            "kinds": CrmActivity.KINDS,
            "today": today(),
        },
    )
