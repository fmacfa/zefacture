import json
import secrets
from collections import defaultdict
from tempfile import TemporaryFile
from datetime import date, timedelta
from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.shortcuts import render, redirect
from django.db import transaction
from django.db.models import Case, Count, DateTimeField, F, Q, Sum, Value, When
from django.db.models.functions import Cast, NullIf, TruncDate
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.utils.text import slugify
from core.numbers import document_amount
from core.common import BusinessError, today, stamp, digest, iso_date
from core.models import (
    Company,
    Customer,
    Document,
    InventoryItem,
    DocumentFile,
    InvoiceEvent,
    PdpInvoice,
    PdpSyncState,
)
from core.security import scoped, scoped_get, ROLES, hidden_expense_documents
from core.services import documents as service, files, pdp, supplier_archive
from core.services.document_preview import preferred_file, preview_format
from core.services.mail import queue
from core.services.reports import document_status_cards
from .preview import preview_response
from .document_followup import followup_context

SECTIONS = {
    "devis": ("quote", "Devis", "nouveau"),
    "factures": ("invoice", "Factures", "nouvelle"),
    "avoirs": ("credit_note", "Avoirs", "nouveau"),
    "reception": ("invoice", "Factures fournisseurs", "nouvelle"),
}


@scoped("write")
@require_POST
def quick_customer(request):
    data = request.POST.dict()
    data.setdefault("type", "business")
    data.setdefault("country_code", "FR")
    customer = service.save_customer(request.company, data)
    return JsonResponse(
        {"customer_uid": customer.customer_uid, "legal_name": customer.legal_name},
        status=201,
    )


def section_query(company, section, request=None):
    kind = SECTIONS[section][0]
    query = Document.objects.filter(
        company_uid=company.company_uid,
        direction="incoming" if section == "reception" else "outgoing",
    )
    if request is not None:
        query = query.exclude(document_uid__in=hidden_expense_documents(request))
    return query.filter(
        type__in=["invoice", "progress_invoice", "credit_note"]
        if section == "reception"
        else ["invoice", "progress_invoice"]
        if section == "factures"
        else [kind]
    )


def reception_context(company, visible, page):
    links = PdpInvoice.objects.filter(company_uid=company.company_uid, direction="in")
    uids = [doc.document_uid for doc in page]
    by_document = {link.document_uid: link for link in links.filter(document_uid__in=uids)}
    records = defaultdict(list)
    for file in DocumentFile.objects.filter(document_uid__in=uids).order_by("-id"):
        records[file.document_uid].append(file)
    for document in page:
        attached = records[document.document_uid]
        link = by_document.get(document.document_uid)
        if link:
            link.original_mime = next(
                (file.mime_type for file in attached if file.file_uid == link.original_file_uid), ""
            )
        document.pdp_files = link
        document.preview_file = preferred_file(attached, link)
    sync = PdpSyncState.objects.filter(company_uid=company.company_uid).first()
    connected = pdp.reception_connected(company)
    return {
        "pdp_connected": connected,
        "pdp_sync": sync,
        "pdp_count": links.filter(document_uid__in=visible.values("document_uid")).count(),
        "pdp_pending": bool(connected and sync and (
            sync.requested or sync.lease_until and sync.lease_until > timezone.now()
        )),
        "external_services_disabled": settings.EXTERNAL_SERVICES_DISABLED,
    }


def reception_totals(query):
    def net_of_credits(field):
        return Sum(Case(When(type="credit_note", then=-F(field)), default=F(field)))

    return query.values("currency_code").annotate(
        count=Count("pk"),
        net=net_of_credits("total_excluding_tax"),
        vat=net_of_credits("total_vat"),
        gross=net_of_credits("total_including_tax"),
        due=Sum("amount_due"),
    ).order_by("currency_code")


def read_data(request):
    try:
        if request.content_type == "application/json":
            data = json.loads(request.body)
        else:
            data = request.POST.dict()
            data["lines"] = json.loads(data.get("lines_json", "[]"))
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, TypeError):
        raise BusinessError("Données du document invalides.")


def document_download(document, file, default_name, default_mime):
    name = default_name
    if file.kind in {"pdp_original", "pdp_readable"}:
        number = slugify(document.number or "")[:140] or document.document_uid
        name = number + ("-original" if file.kind == "pdp_original" else "-lecture")
    return HttpResponse(
        files.read_verified(file),
        content_type=file.mime_type or default_mime,
        headers={
            "Content-Disposition": content_disposition_header(True, name + files.download_extension(file.mime_type)),
            "Cache-Control": "private, no-store",
        },
    )


@scoped("write")
def documents(request, section, document_uid=None, action=None):
    kind, title, _ = SECTIONS[section]
    from core.services.branding import for_company

    brand = for_company(request.company)
    document = None
    if document_uid:
        document = (
            section_query(request.company, section, request)
            .filter(document_uid=document_uid)
            .first()
        )
        if not document:
            raise BusinessError("Document introuvable.", 404)
    if action == "telecharger" and document:
        if request.method != "GET":
            return HttpResponse(status=405)
        file = files.generate_document(document)
        return document_download(
            document, file, f"{document.type}-{document.document_uid}", "application/pdf"
        )
    if action == "facture-avancement" and document and request.method == "GET":
        if request.role not in ROLES["write"]:
            raise BusinessError("Modification non autorisée.", 403)
        return render(
            request,
            "core/progress.html",
            {"title": "Facture d'avancement", "document": document},
        )
    if (
        action
        and action not in {"new", "edit", "autosave"}
        and document
        and request.method == "POST"
    ):
        if action in {"accepter", "annuler", "payer", "refuser", "litige"}:
            service.change_status(
                document,
                {
                    "accepter": "accept",
                    "annuler": "cancel",
                    "payer": "pay",
                    "refuser": "refuse",
                    "litige": "dispute",
                }[action],
            )
        elif action in {"facturer", "facture-avancement"}:
            result = service.bill_quote(
                request.company,
                request.user,
                document,
                request.POST.get("percent", request.POST.get("progress_percent"))
                if action == "facture-avancement"
                else None,
            )
            return redirect(result.url)
        elif action == "generer-factur-x":
            files.generate_document(document)
        elif action == "envoyer":
            send(
                document,
                request.POST.get("channel", "email"),
                request.POST.get("processing_rule", ""),
            )
        elif action == "relancer":
            if (
                document.type not in {"invoice", "progress_invoice"}
                or document.direction != "outgoing"
                or document.status != "sent"
                or not document.due_date
                or document.due_date >= today()
                or document.amount_due <= 0
            ):
                raise BusinessError(
                    "Seule une facture échue avec un reste dû peut être relancée."
                )
            queue(
                document.company_uid,
                document.document_uid,
                "invoice_reminder",
                document.buyer.get("email"),
                "Relance facture " + document.number,
                f"Bonjour,\nLa facture {document.number}, échue le {document.due_date}, présente un reste dû de {document_amount(document,document.amount_due)} {document.currency_code}.",
            )
            service.event(document, "reminder", "Relance préparée")
        else:
            raise BusinessError("Action inconnue.", 404)
        messages.success(request, "Action enregistrée.")
        return redirect(document.url)
    if (
        action in {"new", "edit", "autosave"}
        or document
        and request.method == "POST"
        and not action
    ):
        if request.method == "POST":
            data = read_data(request)
            if action == "autosave" and data.get("document_uid"):
                document = (
                    section_query(request.company, section, request)
                    .filter(document_uid=data["document_uid"])
                    .first()
                )
                if not document:
                    raise BusinessError("Brouillon introuvable.", 404)
            saved = service.save_document(
                request.company,
                request.user,
                kind,
                data,
                document,
                direction="incoming" if section == "reception" else "outgoing",
            )
            if action == "autosave":
                return JsonResponse(
                    {
                        "document_uid": saved.document_uid,
                        "url": saved.url,
                        "saved_at": saved.updated_at,
                    }
                )
            messages.success(request, "Document enregistré.")
            return redirect(saved.url)
        if document and document.status != "draft":
            raise BusinessError("Ce document est figé.", 409)
        if request.role not in ROLES["write"]:
            raise BusinessError("Modification non autorisée.", 403)
        return render(
            request,
            "core/document_form.html",
            {
                "title": ("Modifier " if document else "Créer ") + title.lower(),
                "document": document,
                "section": section,
                "is_quote": kind == "quote",
                "selected_customer_uid": document.customer_uid
                if document
                else request.GET.get("customer_uid", ""),
                "is_credit": kind == "credit_note",
                "incoming": section == "reception",
                "customers": Customer.objects.filter(
                    company_uid=request.company.company_uid, status="active"
                ).order_by("legal_name"),
                "invoices": Document.objects.filter(
                    company_uid=request.company.company_uid,
                    direction="outgoing",
                    type__in=["invoice", "progress_invoice"],
                    status__in=["sent", "paid"],
                ),
                "inventory": list(
                    InventoryItem.objects.filter(
                        company_uid=request.company.company_uid, status="active"
                    ).values(
                        "name",
                        "description",
                        "reference",
                        "unit_code",
                        "unit_price_excluding_tax",
                        "vat_rate",
                    )
                ),
                "lines": list(document.lines.values())
                if document
                else [
                    {
                        "description": "",
                        "quantity": 1,
                        "unit_price_excluding_tax": 0,
                        "vat_rate": 20,
                        "discount_percent": 0,
                        "unit_code": "C62",
                        "vat_category_code": "S",
                    }
                ],
                "today": today(),
                "due_date": str(
                    date.fromisoformat(today())
                    + timedelta(
                        days=brand.quote_validity_days
                        if kind == "quote"
                        else brand.payment_due_days
                    )
                ),
                "default_currency": brand.default_currency,
            },
        )
    if document:
        from core.models import PdpOutbox
        from core.services.pdp_sync import STATUS_LABELS, OUTBOX_LABELS

        pdp_link = PdpInvoice.objects.filter(
            company_uid=document.company_uid, document_uid=document.document_uid
        ).first()
        pdp_operations = list(
            PdpOutbox.objects.filter(
                company_uid=document.company_uid, document_uid=document.document_uid
            ).order_by("-id")[:10]
        )
        for operation in pdp_operations:
            operation.status_label = OUTBOX_LABELS.get(
                operation.status, operation.status
            )
        attached = list(document.files)
        for file in attached:
            file.preview_kind = preview_format(file)
        return render(
            request,
            "core/document.html",
            {
                "title": document.number,
                "document": document,
                "lines": document.lines,
                "events": InvoiceEvent.objects.filter(
                    document_uid=document.document_uid
                ).order_by("-id"),
                "files": attached,
                "preview_file": preferred_file(attached, pdp_link),
                "linked": Document.objects.filter(
                    company_uid=document.company_uid,
                    source_quote_uid=document.document_uid,
                ),
                "section": section,
                "pdp_link": pdp_link,
                "pdp_status_label": STATUS_LABELS.get(
                    pdp_link.latest_status, pdp_link.latest_status
                )
                if pdp_link
                else "",
                "pdp_operations": pdp_operations,
                **followup_context(request, document),
            },
        )
    return document_list(request, section)


@scoped("read")
@require_GET
def export_reception(request):
    return document_list(request, "reception", export=True)


def zip_response(query, format_choice, start, end):
    if not start or not end:
        raise BusinessError(
            "Choisissez une date de début et une date de fin pour télécharger le ZIP."
        )
    output = TemporaryFile()
    try:
        supplier_archive.write_archive(output, query, format_choice)
        formats = "pdf-xml" if format_choice == "both" else format_choice
        return FileResponse(
            output,
            as_attachment=True,
            content_type="application/zip",
            filename=f"factures-fournisseurs_{start}_{end}_{formats}.zip",
        )
    except BaseException:
        output.close()
        raise


def document_list(request, section, *, export=False):
    _, title, new = SECTIONS[section]
    visible = section_query(request.company, section, request)
    query = visible
    if request.GET.get("q"):
        q = request.GET["q"][:200]
        query = query.filter(
            Q(number__icontains=q)
            | Q(buyer_snapshot__icontains=q)
            | Q(seller_snapshot__icontains=q)
            | Q(subject__icontains=q)
        )
    error = None
    cards = []
    try:
        date_field = "issue_date"
        if section == "reception":
            basis = request.GET.get("date_field", "issue")
            if basis == "received":
                # Legacy receipt timestamps are stored as UTC text. Filter by
                # calendar day in the same time zone as the interface.
                query = query.annotate(
                    receipt_day=TruncDate(
                        Cast(NullIf("received_at", Value("")), DateTimeField()),
                        tzinfo=timezone.get_current_timezone(),
                    )
                )
                date_field = "receipt_day"
            elif basis != "issue":
                raise BusinessError(
                    "Choisissez la date de facture ou la date de réception dans ZeFacture."
                )
        start = iso_date(request.GET.get("from"), optional=True)
        end = iso_date(request.GET.get("to"), optional=True)
        if start and end and start > end and section == "reception":
            raise BusinessError(
                "La date de fin doit être égale ou postérieure à la date de début."
            )
        if start:
            query = query.filter(**{f"{date_field}__gte": start})
        if end:
            query = query.filter(**{f"{date_field}__lte": end})
        if not export:
            cards = document_status_cards(query, section, request.GET)
        if request.GET.get("status"):
            query = query.filter(status=request.GET["status"])
        if export:
            return zip_response(query, request.GET.get("format", "both"), start, end)
    except BusinessError as exc:
        if section != "reception":
            raise
        error = exc
    from django.core.paginator import Paginator

    page = Paginator(query.order_by("-created_at", "-id"), 30).get_page(
        request.GET.get("page")
    )
    return render(
        request,
        "core/documents.html",
        {
            "title": title,
            "documents": page,
            "section": section,
            "reception_error": str(error) if error else "",
            "status_cards": cards,
            "reception_totals": reception_totals(query) if section == "reception" and not error else (),
            "new_url": f"/app/{section}/{new}",
            **(
                reception_context(request.company, visible, page)
                if section == "reception"
                else {}
            ),
        },
        status=error.status if error else 200,
    )


@transaction.atomic
def send(document, channel="email", processing_rule=""):
    Company.objects.select_for_update().get(company_uid=document.company_uid)
    document = Document.objects.select_for_update().get(pk=document.pk)
    if document.direction != "outgoing":
        raise BusinessError("Ce document n'est pas une émission.")
    if document.status in {"sent", "paid", "accepted"}:
        return document
    if document.status not in {"draft", "simulated"} or not document.customer_uid:
        raise BusinessError("Complétez le brouillon avant l'envoi.", 409)
    if document.status == "simulated":
        document.amount_due = (
            0
            if document.type in {"quote", "credit_note"}
            else document.total_including_tax
        )
        document.save(update_fields=["amount_due"])
    file = files.generate_document(document)
    if channel == "superpdp" and document.type != "quote":
        from core.services.pdp_sync import queue_invoice

        queue_invoice(document, file, processing_rule)
    elif channel == "email":
        body = f"Bonjour,\nVotre {'devis' if document.type == 'quote' else 'facture'} {document.number} d'un montant de {document_amount(document,document.total_including_tax)} {document.currency_code} est disponible."
        if document.type == "quote":
            raw = secrets.token_hex(32)
            document.public_token_hash = digest(raw)
            document.public_token_expires_at = (
                str(date.fromisoformat(today()) + timedelta(days=30)) + " 23:59:59"
            )
            body += "\nConsulter et accepter : " + settings.APP_URL + "/devis/" + raw
        queue(
            document.company_uid,
            document.document_uid,
            "quote" if document.type == "quote" else "invoice",
            document.buyer.get("email"),
            document.number,
            body,
        )
        service.record_issuance(document, file, "Document émis ; email préparé")
    else:
        raise BusinessError("Canal d'envoi inconnu.")
    return document


def public_quote(request, token):
    document = Document.objects.filter(
        public_token_hash=digest(token),
        type="quote",
        direction="outgoing",
        status__in=["sent", "accepted"],
        public_token_expires_at__gt=stamp(),
    ).first()
    if not document:
        return render(
            request,
            "core/error.html",
            {"error": "Ce lien de devis est expiré ou indisponible."},
            status=404,
        )
    if request.method == "POST":
        if request.POST.get("accept") != "1":
            return render(
                request,
                "core/error.html",
                {"error": "Confirmez l'acceptation du devis."},
                status=422,
            )
        try:
            document = service.change_status(document, "accept")
        except BusinessError as exc:
            return render(
                request, "core/error.html", {"error": str(exc)}, status=exc.status
            )
    from core.services.branding import logo_bytes, palette
    import base64

    presentation = document.seller.get("branding", {})
    logo = logo_bytes(document.company_uid, presentation)
    accent = presentation.get("accent_color")
    from core.numbers import document_style, number_context
    with number_context(document_style(document)):
        return render(
            request,
            "core/public_quote.html",
            {
                "title": document.number,
                "document": document,
                "lines": document.lines,
                "public_brand": presentation,
                "palette": palette(accent) if accent else None,
                "public_logo": "data:image/png;base64," + base64.b64encode(logo).decode()
                if logo
                else "",
            },
        )


@scoped("write")
def attachment(request, document_uid, file_uid=None, preview=False):
    document = scoped_get(Document, request, document_uid=document_uid)
    if request.method == "POST" and not file_uid:
        metadata = files.upload(
            request.FILES.get("file"),
            f"attachments/{document.company_uid}/{document.document_uid}",
        )
        DocumentFile.objects.create(
            document_uid=document_uid, kind="attachment", **metadata
        )
        return redirect(document.url)
    if request.method == "GET" and file_uid:
        file = document.files.filter(file_uid=file_uid).first()
        if not file:
            raise BusinessError("Fichier introuvable.", 404)
        if preview:
            return preview_response(
                request, file, title=document.number or "Document", back_url=document.url,
                download_url=f"/app/documents/{document_uid}/fichiers/{file_uid}",
                document=document,
            )
        return document_download(document, file, file.file_uid, "application/octet-stream")
    return HttpResponse(status=405)
