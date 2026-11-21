from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from core.common import BusinessError
from core.models import Document
from core.security import ROLES, scoped
from core.services import reminders


@scoped("write")
@require_http_methods(["GET", "POST"])
def prepare(request):
    if request.role not in ROLES["write"]:
        return HttpResponseForbidden()
    confirmed = request.method == "POST" and request.POST.get("action") == "send"
    choices = reminders.reviewed(request) if confirmed else {}
    source = request.GET if request.method == "GET" else request.POST
    ids = list(choices) if confirmed else list(dict.fromkeys(source.getlist("facture")))
    if not ids or len(ids) > 100:
        raise BusinessError("Sélectionnez entre une et cent factures.")
    docs = Document.objects.filter(
        company_uid=request.company.company_uid,
        direction="outgoing",
        type__in=["invoice", "progress_invoice"],
        document_uid__in=ids,
    ).order_by("due_date", "number")
    if len(docs) != len(ids):
        raise BusinessError("Facture introuvable.", 404)
    rows, ready = [], {}
    for doc in docs:
        row = {"doc": doc}
        try:
            if confirmed:
                reminders.enqueue(
                    request.company.company_uid,
                    doc.document_uid,
                    choices[doc.document_uid],
                )
                row["result"] = "Mise en file d’attente"
            else:
                item, proof = reminders.preview(doc)
                ready[doc.document_uid] = proof
                row["item"] = item
        except BusinessError as exc:
            row["error"] = str(exc)
        rows.append(row)
    return render(
        request,
        "core/invoice_reminders.html",
        {
            "title": "Relances des factures en retard",
            "rows": rows,
            "confirmed": confirmed,
            "count": len(ready),
            "review": reminders.token(request, ready) if ready else "",
        },
    )
