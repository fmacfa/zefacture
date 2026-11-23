from core.services import expense_claims
from collections import Counter, defaultdict
from decimal import Decimal
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum

from core.common import BusinessError, iso_date, today, unpack
from core.models import ExpenseReimbursement, Ingestion
from core.payment_details import PaidBy, PaymentMethod
from core.security import ROLES
from . import jobs
from .expense_classification import CATEGORIES, classification
from .receipt_extraction import (
    EXPENSE_KINDS,
    expense_suggestion,
    simplify,
    suggested_payment_method,
)


STATES = {
    "due": "À rembourser",
    "paid": "Remboursée",
    "exempt": "Sans remboursement",
    "unknown": "À préciser",
}
PAYER_STATES = {"company": "exempt", "personal": "due"}


def receipts_for(request):
    query = Ingestion.objects.filter(
        company_uid=request.company.company_uid, source_kind__in=EXPENSE_KINDS
    )
    if request.role not in ROLES["finance"]:
        query = query.filter(uploaded_by_user_uid=request.user.user_uid)
    return query


def reimbursement_state(item):
    followup = getattr(item, "reimbursement", None)
    if followup:
        return followup.status
    return PAYER_STATES.get(item.expense_paid_by, "unknown")


@transaction.atomic
def record_reimbursement(request, item):
    if request.role not in ROLES["finance"]:
        raise BusinessError(
            "Un responsable ou un comptable doit confirmer les remboursements.", 403
        )
    item = receipts_for(request).select_for_update().filter(pk=item.pk).first()
    if not item or item.archived_at:
        raise BusinessError("Choisissez une note de frais active.", 409)
    expense_claims.require_editable(item)
    status = request.POST.get("reimbursement_status")
    if status not in {*STATES, "auto"}:
        raise BusinessError("Choisissez un état de remboursement.")
    if status == "auto":
        ExpenseReimbursement.objects.filter(receipt=item).delete()
        return
    paid_on = None
    if status == "paid":
        paid_on = iso_date(request.POST.get("paid_on"))
        if item.expense_amount is None or not item.expense_currency:
            raise BusinessError(
                "Enregistrez le montant et la devise de la note avant de confirmer le remboursement."
            )
        if paid_on > today():
            raise BusinessError(
                "La date de versement doit être aujourd’hui ou une date passée."
            )
    ExpenseReimbursement.objects.update_or_create(
        receipt=item,
        defaults={
            "status": status,
            "paid_on": paid_on,
            "updated_by": request.user.user_uid,
        },
    )


def details(item):
    parsed = unpack(item.extraction_json)
    if not isinstance(parsed, dict):
        parsed = {}
    suggested = expense_suggestion(item, parsed)
    facts = classification(item, parsed)
    reviewed = parsed.get("_reviewed") or []

    def optional(name, suggestion):
        value = getattr(item, name)
        return (value or "") if name in reviewed else (value or suggestion or "")

    method = suggested_payment_method(item.transcription_text, parsed)
    method_code = optional("expense_payment_method", method)
    payer = optional("expense_paid_by", facts.get("paid_by"))
    state = reimbursement_state(item)
    amount = (
        format(item.expense_amount.normalize(), "f")
        if item.expense_amount is not None
        else suggested["amount"]
    )
    return {
        "merchant": item.merchant_name or suggested["merchant"],
        "date": item.expense_date or suggested["date"],
        "amount": amount,
        "currency": item.expense_currency or suggested["currency"],
        "paid_eur": item.expense_paid_eur,
        "payer_name": item.expense_payer_name,
        "vat": suggested["vat"]
        if "totals.total_vat" in reviewed
        or not item.expense_currency
        or not suggested["currency"]
        or item.expense_currency == suggested["currency"]
        else "",
        "category": optional("expense_category", facts.get("category")),
        "description": facts.get("description", ""),
        "purpose": optional("expense_description", facts.get("purpose")),
        "method": method_code,
        "method_label": dict(PaymentMethod.choices).get(method_code, ""),
        "payer": payer,
        "payer_label": dict(PaidBy.choices).get(payer, ""),
        "status": state,
        "status_label": STATES[state],
    }


def workspace(selection, params, *, dashboard=False):
    start = iso_date(params.get("from"), optional=True)
    end = iso_date(params.get("to"), optional=True)
    if start and end and start > end:
        raise BusinessError("La date de fin précède la date de début.")
    state = params.get("statut", "")
    if state and state not in STATES:
        raise BusinessError("État de remboursement inconnu.")
    category = params.get("categorie", "")
    search = simplify(params.get("q", "").strip()[:200])
    categories = set(CATEGORIES)
    counts = Counter()
    amounts = defaultdict(
        lambda: {
            "gross": Decimal(0),
            "due": Decimal(0),
            "paid": Decimal(0),
            "unknown": Decimal(0),
        }
    )
    paid_eur_count, paid_eur_total = 0, Decimal(0)
    missing = 0
    results = []
    selection = selection.select_related("reimbursement").defer("transcription_json")
    for lookup, limit in (("expense_date__gte", start), ("expense_date__lte", end)):
        if limit:
            selection = selection.filter(
                Q(**{lookup: limit}) | Q(expense_date__isnull=True) | Q(expense_date="")
            )
    if dashboard:
        confirmed = Q(
            expense_date__gt="", expense_currency__gt="", expense_amount__isnull=False
        )
        summaries = (
            selection.filter(confirmed)
            .values("expense_currency", "reimbursement__status", "expense_paid_by")
            .annotate(
                count=Count("pk"), amount=Sum("expense_amount"),
                eur_count=Count("expense_paid_eur"), eur_total=Sum("expense_paid_eur"),
            )
            .order_by()
        )
        for summary in summaries:
            current = summary["reimbursement__status"] or PAYER_STATES.get(
                summary["expense_paid_by"], "unknown"
            )
            counts[current] += summary["count"]
            paid_eur_count += summary["eur_count"]
            paid_eur_total += summary["eur_total"] or 0
            group = amounts[summary["expense_currency"]]
            group["gross"] += summary["amount"]
            if current != "exempt":
                group[current] += summary["amount"]
        selection = selection.exclude(confirmed)
    for item in selection.order_by("-id").iterator(chunk_size=150):
        facts = details(item)
        if facts["category"]:
            categories.add(facts["category"])
        when = facts["date"]
        if (start and (not when or when < start)) or (end and (not when or when > end)):
            continue
        if category and category != facts["category"]:
            continue
        if search and search not in simplify(
            " ".join(
                (
                    facts["merchant"],
                    facts["category"],
                    facts["description"],
                    facts["purpose"],
                    facts["payer_name"],
                    item.original_filename,
                )
            )
        ):
            continue
        current = facts["status"]
        counts[current] += 1
        if state and state != current:
            continue
        if item.expense_paid_eur is not None:
            paid_eur_count += 1
            paid_eur_total += item.expense_paid_eur
        if facts["currency"] and facts["amount"] != "":
            group = amounts[facts["currency"]]
            amount = Decimal(facts["amount"])
            group["gross"] += amount
            if current in {"due", "paid", "unknown"}:
                group[current] += amount
        else:
            missing += 1
        if not dashboard:
            item.facts = facts
            item.transcription_text = item.extraction_json = None
            results.append(item)
    base = {
        k: params[k]
        for k in ("lot", "archives", "q", "from", "to", "categorie")
        if params.get(k)
    }

    def link(status):
        query = urlencode({**base, **({"statut": status} if status else {})})
        return "/app/notes-de-frais" + ("?" + query if query else "")

    cards = [
        {
            "status": code,
            "label": label,
            "count": counts[code] if code else counts.total(),
            "active": state == code,
            "url": link(code),
        }
        for code, label in [("", "Toutes"), *{**STATES, "paid": "Remboursées"}.items()]
    ]
    context = {
        "expense_cards": cards,
        "expense_totals": sorted(amounts.items()),
        "paid_eur_count": paid_eur_count,
        "paid_eur_total": paid_eur_total,
        "missing_amounts": missing,
        "categories": sorted(categories),
        "states": STATES,
        "expense_url": link(""),
    }
    if not dashboard:
        page = Paginator(results, 25).get_page(params.get("page"))
        if page.object_list:
            jobs.set_progress(page.object_list, page.object_list[0].company_uid)
        context.update(items=page.object_list, page=page)
    return context
