from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core.common import BusinessError, iso_date, today
from core.models import StockMovement
from core.security import ROLES, scoped
from core.services import invoice_stock


@require_http_methods(["GET", "POST"])
@scoped("write")
def invoice_lines(request, document_uid=None):
    if request.role not in ROLES["write"]:
        raise BusinessError("Accès à l’inventaire requis.", 403)
    query = invoice_stock.invoices(request)
    if document_uid:
        doc = query.filter(document_uid=document_uid).first()
        if not doc:
            raise BusinessError("Facture indisponible pour le stock.", 404)
        if request.method == "POST":
            choices = {
                key[5:]: value
                for key, value in request.POST.items()
                if key.startswith("line_") and value
            }
            new, attached, already = invoice_stock.post_lines(
                request, doc.document_uid, choices, request.POST.get("date")
            )
            messages.success(
                request,
                f"Mouvements créés : {new}. Rattachés à l’historique : {attached}. Lignes déjà traitées : {already}.",
            )
            return redirect(request.get_full_path())
        page = Paginator(doc.lines, 30).get_page(request.GET.get("page"))
        lines = invoice_stock.choices_for_lines(doc.company_uid, doc, page.object_list)
        return render(
            request,
            "core/stock_invoice.html",
            {
                "title": f"Stock · {doc.number}",
                "doc": doc,
                "lines": lines,
                "pending": any(not line.recorded for line in lines),
                "page": page,
                "effective_date": doc.issue_date or today(),
            },
        )
    if request.method != "GET":
        return HttpResponse(status=405, headers={"Allow": "GET"})
    outgoing = request.GET.get("sens") == "ventes"
    query = query.filter(direction="outgoing" if outgoing else "incoming")
    term = request.GET.get("q", "").strip()[:160]
    if term:
        party_field = "buyer_snapshot" if outgoing else "seller_snapshot"
        query = query.filter(
            Q(number__icontains=term) | Q(**{party_field + "__icontains": term})
        )
    for parameter, lookup in (("du", "issue_date__gte"), ("au", "issue_date__lte")):
        if request.GET.get(parameter):
            query = query.filter(**{lookup: iso_date(request.GET[parameter])})
    page = Paginator(query.order_by("-issue_date", "-pk"), 30).get_page(
        request.GET.get("page")
    )
    docs = list(page.object_list)
    counts = dict(
        StockMovement.objects.filter(
            company_uid=request.company.company_uid,
            source_document_uid__in=[doc.document_uid for doc in docs],
        )
        .values_list("source_document_uid")
        .annotate(total=Count("pk"))
    )
    for doc in docs:
        doc.recorded_count = counts.get(doc.document_uid, 0)
    return render(
        request,
        "core/stock_invoices.html",
        {
            "title": "Stock depuis les factures",
            "docs": docs,
            "page": page,
            "outgoing": outgoing,
        },
    )
