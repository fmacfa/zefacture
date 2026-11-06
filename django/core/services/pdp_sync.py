"""Durable SUPERPDP transport and resumable, tenant-bound invoice synchronization.

Provider events are an append-only ledger, not a replacement for bank reconciliation.
No POST is automatically replayed after an ambiguous network result.
"""

import hashlib
import json
import re
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation, localcontext
from urllib.parse import urlencode

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.common import BusinessError, iso_date, json_text, stamp, uid
from core.models import (
    Company,
    Customer,
    CustomerProfile,
    Document,
    DocumentFile,
    DocumentLine,
    InvoiceEvent,
    PdpInvoice,
    PdpOutbox,
    PdpRemoteEvent,
    PdpSyncState,
)
from . import files, pdp
from .documents import event


STATUS_LABELS = {
    "api:uploaded": "Déposé auprès de SUPERPDP",
    "api:invalid": "Validation échouée",
    "api:validated": "Validé",
    "api:sent": "Transmis",
    "api:rejected": "Rejeté",
    "api:received": "Reçu",
    "api:acknowledged": "Accusé de réception",
    "api:accepted": "Accepté",
    "fr:200": "Déposée",
    "fr:201": "Émise",
    "fr:202": "Reçue",
    "fr:203": "Mise à disposition",
    "fr:204": "Prise en charge",
    "fr:205": "Approuvée",
    "fr:206": "Approuvée partiellement",
    "fr:207": "En litige",
    "fr:208": "Suspendue",
    "fr:209": "Complétée",
    "fr:210": "Refusée",
    "fr:211": "Paiement transmis",
    "fr:212": "Encaissement déclaré",
    "fr:213": "Rejetée",
    "fr:501": "Irrecevable",
}
OUTBOX_LABELS = {
    "queued": "En attente",
    "sending": "Transmission en cours",
    "sent": "Dépôt confirmé",
    "failed": "Refus de la requête",
    "uncertain": "Résultat à vérifier avant toute reprise",
}


def state_for(company):
    return PdpSyncState.objects.get_or_create(company_uid=company.company_uid)[0]


def _token(session):
    if not getattr(session, "_verified_token", None):
        company = Company.objects.get(company_uid=session.company_uid, status="active")
        pdp.verify(company, session)
    if session.company_verification_status != "verified":
        raise BusinessError("L'entreprise SUPERPDP doit être vérifiée.", 403)
    return session._verified_token


def _binding(company, session, verify=False):
    if settings.EXTERNAL_SERVICES_DISABLED:
        raise BusinessError(
            "Les services externes sont désactivés sur cette instance.", 503
        )
    if (
        verify
        or session.company_verification_status != "verified"
        or not session.pdp_company_id
    ):
        pdp.verify(company, session)
    if session.company_verification_status != "verified":
        raise BusinessError(
            "SUPERPDP doit vérifier l'autorisation de cette entreprise avant les échanges.",
            403,
        )
    remote_id = pdp.identifier(session.pdp_company_id)
    environment = session.pdp_company_env
    if environment not in {"sandbox", "production"}:
        raise BusinessError("Environnement SUPERPDP non reconnu.", 502)
    with transaction.atomic():
        Company.objects.select_for_update().get(pk=company.pk)
        state = state_for(company)
        if state.remote_company_id and (
            state.remote_company_id != remote_id
            or state.remote_environment != environment
        ):
            raise BusinessError(
                "Le compte ou l'environnement SUPERPDP diffère de la liaison enregistrée.",
                409,
            )
        state.remote_company_id, state.remote_environment = remote_id, environment
        state.save(update_fields=["remote_company_id", "remote_environment"])
    return state


def request_sync(company, *, enabled=None, replay=False):
    with transaction.atomic():
        Company.objects.select_for_update().get(pk=company.pk)
        state = state_for(company)
        state = PdpSyncState.objects.select_for_update().get(pk=state.pk)
        if replay and state.lease_until and state.lease_until > timezone.now():
            raise BusinessError("Une synchronisation est déjà en cours.", 409)
        if enabled is not None:
            state.enabled = enabled
        if replay:
            state.invoice_cursor = state.event_cursor = 0
        state.requested = enabled is not False
        state.next_sync_at = timezone.now()
        fields = ["requested", "next_sync_at"]
        if enabled is not None:
            fields.append("enabled")
        if replay:
            fields.extend(["invoice_cursor", "event_cursor"])
        state.save(update_fields=fields)
        return state


@transaction.atomic
def queue_invoice(document, file, processing_rule=""):
    company = Company.objects.select_for_update().get(company_uid=document.company_uid)
    document = Document.objects.select_for_update().get(pk=document.pk)
    session = pdp.session_for(company)
    if session.status == "sandbox":
        # Historical local simulation is separate from SUPERPDP's real sandbox API.
        if settings.ENVIRONMENT not in {"local", "testing", "development"}:
            raise BusinessError("Simulation locale indisponible en production.", 409)
        return pdp.send_invoice(document, file, processing_rule)
    state = _binding(company, session)
    if document.direction != "outgoing" or document.type not in {
        "invoice",
        "progress_invoice",
        "credit_note",
    }:
        raise BusinessError("Seule une facture ou un avoir sortant peut être déposé.")
    prior = PdpOutbox.objects.filter(
        company_uid=company.company_uid, request_key="invoice:" + document.document_uid
    ).first()
    if prior:
        return prior
    if document.pdp_invoice_id or document.status not in {"draft", "simulated"}:
        raise BusinessError(
            "Ce document est déjà émis ; vérifier son suivi SUPERPDP.", 409
        )
    if file.document_uid != document.document_uid or file.kind != "facturx":
        raise BusinessError("Original Factur-X du document requis.")
    files.read_verified(file)
    rule = processing_rule or (
        "B2C"
        if document.buyer.get("type") == "individual"
        else "B2BInt"
        if document.buyer.get("country_code", "FR") != "FR"
        else "B2B"
    )
    if rule not in {"B2B", "B2C", "B2BInt"}:
        raise BusinessError("Règle de traitement inconnue.")
    operation = PdpOutbox.objects.create(
        company_uid=company.company_uid,
        document_uid=document.document_uid,
        request_key="invoice:" + document.document_uid,
        operation="invoice",
        remote_company_id=state.remote_company_id,
        remote_environment=state.remote_environment,
        file_uid=file.file_uid,
        payload={"processing_rule": rule, "external_id": document.document_uid},
    )
    # Issuing freezes the existing original, as with the email outbox. The remote
    # delivery state lives in PdpOutbox, and is never inferred from local 'sent'.
    document.status = "simulated" if state.remote_environment == "sandbox" else "sent"
    document.sent_at = None if document.status == "simulated" else stamp()
    document.pdp_external_id = document.document_uid
    document.pdp_processing_rule = rule
    if document.status == "simulated":
        document.amount_due = 0
    document.save()
    event(
        document,
        "pdp_queued",
        "Dépôt SUPERPDP préparé",
        {"operation_uid": operation.operation_uid},
    )
    return operation


@transaction.atomic
def queue_status(document, code, reason="", note=""):
    company = Company.objects.select_for_update().get(company_uid=document.company_uid)
    document = Document.objects.select_for_update().get(pk=document.pk)
    link = PdpInvoice.objects.filter(
        company_uid=company.company_uid, document_uid=document.document_uid
    ).first()
    if not link:
        raise BusinessError(
            "Synchronisez d'abord l'original et les identifiants SUPERPDP.", 409
        )
    state = _binding(company, pdp.session_for(company))
    if (link.remote_company_id, link.remote_environment) != (
        state.remote_company_id,
        state.remote_environment,
    ):
        raise BusinessError("La facture appartient à une autre liaison SUPERPDP.", 409)
    expected = (
        {
            "fr:205": "accepted",
            "fr:207": "disputed",
            "fr:210": "refused",
            "fr:211": "paid",
        }
        if document.direction == "incoming"
        else {"fr:212": "paid"}
    )
    sandbox = link.remote_environment == "sandbox" and document.status == "simulated"
    if code not in expected or not sandbox and document.status != expected[code]:
        raise BusinessError(
            "Le statut local ne permet pas cette déclaration SUPERPDP.", 409
        )
    if code in {"fr:211", "fr:212"} and (
        document.type == "credit_note" or document.amount_due != 0
    ):
        raise BusinessError(
            "Le règlement intégral doit être vérifié avant cette déclaration."
        )
    reason, note = str(reason).strip(), str(note).strip()
    if (
        len(reason) > 100
        or len(note) > 2000
        or code in {"fr:207", "fr:210"}
        and not reason
    ):
        raise BusinessError(
            "Un code motif conforme au référentiel fournisseur est requis pour un litige ou refus."
        )
    payload = {"invoice_id": int(link.remote_id), "status_code": code}
    detail = {}
    if reason:
        detail["reason"] = reason
    if note:
        detail["notes"] = [{"contents": [{"content": note}]}]
    if detail:
        payload["details"] = [detail]
    last = (
        InvoiceEvent.objects.filter(
            company_uid=company.company_uid,
            document_uid=document.document_uid,
            provider="local",
        )
        .order_by("-id")
        .first()
    )
    revision = last.event_uid if last else document.paid_at or document.status
    key = (
        "event:"
        + hashlib.sha256((document.document_uid + code + revision).encode()).hexdigest()
    )
    operation, created = PdpOutbox.objects.get_or_create(
        company_uid=company.company_uid,
        request_key=key,
        defaults=dict(
            document_uid=document.document_uid,
            operation="event",
            remote_company_id=state.remote_company_id,
            remote_environment=state.remote_environment,
            payload=payload,
        ),
    )
    if not created and operation.payload != payload:
        raise BusinessError(
            "Une déclaration existe déjà pour cette action ; vérifier son résultat avant de la modifier.",
            409,
        )
    return operation


def retry_failed(company, operation_uid):
    with transaction.atomic():
        operation = (
            PdpOutbox.objects.select_for_update()
            .filter(company_uid=company.company_uid, operation_uid=operation_uid)
            .first()
        )
        if not operation or operation.status != "failed":
            raise BusinessError(
                "Seules les requêtes explicitement refusées peuvent être reprises ici.",
                409,
            )
        operation.status, operation.last_error = "queued", ""
        operation.save()


def retry_uncertain(company, operation_uid, confirmed=False):
    if not confirmed:
        raise BusinessError(
            "Vérifier l'absence du dépôt chez SUPERPDP et confirmer cette vérification."
        )
    with transaction.atomic():
        operation = (
            PdpOutbox.objects.select_for_update()
            .filter(
                company_uid=company.company_uid,
                operation_uid=operation_uid,
                status="uncertain",
            )
            .first()
        )
        state = state_for(company)
        if (
            not operation
            or not operation.started_at
            or not state.last_success_at
            or state.last_success_at < operation.started_at
            or state.last_error
            or state.requested
            or state.lease
        ):
            raise BusinessError(
                "Effectuer d'abord une synchronisation complète réussie après la transmission incertaine.",
                409,
            )
        if (operation.remote_company_id, operation.remote_environment) != (
            state.remote_company_id,
            state.remote_environment,
        ):
            raise BusinessError("La liaison SUPERPDP a changé.", 409)
        if (
            operation.remote_id
            or operation.operation == "invoice"
            and (
                PdpInvoice.objects.filter(
                    company_uid=company.company_uid, document_uid=operation.document_uid
                ).exists()
                or Document.objects.filter(
                    company_uid=company.company_uid, document_uid=operation.document_uid
                )
                .exclude(pdp_invoice_id__isnull=True)
                .exclude(pdp_invoice_id="")
                .exists()
            )
        ):
            raise BusinessError(
                "Un identifiant distant existe déjà ; ne pas réémettre cette requête.",
                409,
            )
        operation.status, operation.last_error = "queued", ""
        operation.save()


def _error(exc):
    return (
        str(exc)[:500]
        if isinstance(exc, BusinessError)
        else "Échec SUPERPDP. Vérifier la connexion et reprendre la synchronisation."
    )


def flush_outbox(limit=5):
    if settings.EXTERNAL_SERVICES_DISABLED:
        return 0
    PdpOutbox.objects.filter(
        status="sending", started_at__lt=timezone.now() - timedelta(minutes=5)
    ).update(
        status="uncertain",
        last_error="Traitement interrompu : synchroniser avant toute nouvelle transmission.",
    )
    completed = 0
    for _ in range(limit):
        with transaction.atomic():
            operation = (
                PdpOutbox.objects.select_for_update(skip_locked=True)
                .filter(status="queued")
                .order_by("id")
                .first()
            )
            if not operation:
                break
            operation.status = "sending"
            operation.started_at = timezone.now()
            operation.attempts += 1
            operation.save()
        sent = False
        try:
            company = Company.objects.get(
                company_uid=operation.company_uid, status="active"
            )
            session = pdp.session_for(company)
            state = _binding(company, session, verify=True)
            if (state.remote_company_id, state.remote_environment) != (
                operation.remote_company_id,
                operation.remote_environment,
            ):
                raise BusinessError(
                    "Le compte SUPERPDP a changé depuis la préparation.", 409
                )
            token = _token(session)
            document = Document.objects.get(
                company_uid=company.company_uid, document_uid=operation.document_uid
            )
            if operation.operation == "invoice":
                file = DocumentFile.objects.get(
                    document_uid=document.document_uid, file_uid=operation.file_uid
                )
                raw = files.read_verified(file)
                if document.status == "draft" or document.direction != "outgoing":
                    raise BusinessError("Le document préparé n'est plus émis.", 409)
                path = "/v1.beta/invoices?" + urlencode(operation.payload)
                sent = True
                data = pdp.request(
                    session,
                    "POST",
                    path,
                    token,
                    body=raw,
                    headers={"Content-Type": "application/pdf"},
                )
            else:
                sent = True
                data = pdp.request(
                    session,
                    "POST",
                    "/v1.beta/invoice_events",
                    token,
                    body=json_text(operation.payload).encode(),
                    headers={"Content-Type": "application/json"},
                )
            if not isinstance(data, dict):
                raise BusinessError("Réponse de dépôt SUPERPDP invalide.", 502)
            remote_id = pdp.identifier(data.get("id"))
            if operation.operation == "invoice":
                if (
                    pdp.identifier(data.get("company_id"))
                    != operation.remote_company_id
                    or data.get("direction") != "out"
                    or data.get("external_id", document.document_uid)
                    != document.document_uid
                ):
                    raise BusinessError(
                        "Identité ou direction de la facture déposée incohérente.", 409
                    )
            elif (
                pdp.identifier(data.get("invoice_id"))
                != str(operation.payload["invoice_id"])
                or data.get("status_code") != operation.payload["status_code"]
            ):
                raise BusinessError("Événement retourné par SUPERPDP incohérent.", 409)
            with transaction.atomic():
                if operation.operation == "invoice":
                    document = Document.objects.select_for_update().get(pk=document.pk)
                    if document.pdp_invoice_id and document.pdp_invoice_id != remote_id:
                        raise BusinessError(
                            "Une autre facture SUPERPDP est déjà liée.", 409
                        )
                    document.pdp_invoice_id = remote_id
                    document.save(update_fields=["pdp_invoice_id"])
                    event(
                        document,
                        "pdp_uploaded",
                        "Dépôt accepté par SUPERPDP ; transmission en cours",
                        {"pdp_invoice_id": remote_id},
                    )
                operation.status, operation.remote_id, operation.last_error = (
                    "sent",
                    remote_id,
                    "",
                )
                operation.save()
                PdpSyncState.objects.filter(pk=state.pk).update(requested=True)
            completed += 1
        except Exception as exc:
            definite_rejection = (
                isinstance(exc, pdp.PdpHttpError)
                and 400 <= exc.http_status < 500
                and exc.http_status not in {408, 429}
            )
            operation.status = (
                "failed" if not sent or definite_rejection else "uncertain"
            )
            operation.last_error = _error(exc)
            operation.save()
            if sent and operation.operation == "invoice":
                PdpSyncState.objects.filter(company_uid=operation.company_uid).update(
                    requested=True
                )
    return completed


def _number(value, default=None, *, rounded=False):
    if value is None and default is not None:
        value = default
    try:
        if isinstance(value, (bool, float)):
            raise ValueError()
        result = Decimal(str(value))
        if not result.is_finite() or abs(result) >= Decimal("1e22"):
            raise ValueError()
        with localcontext() as context:
            context.prec = 60
            projected = result.quantize(Decimal("0.00000001"))
        if not rounded and result != projected:
            raise ValueError()
        return projected
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise BusinessError(
            "Montant SUPERPDP absent, invalide ou hors précision de stockage.", 502
        ) from exc


def _party(value):
    if not isinstance(value, dict) or not value.get("name"):
        raise BusinessError("Identité de l'émetteur ou du destinataire absente.", 502)
    address, contact = value.get("postal_address") or {}, value.get("contact") or {}
    registration, electronic = (
        value.get("legal_registration_identifier") or {},
        value.get("electronic_address") or {},
    )
    siren = (
        str(registration.get("value") or "")
        if registration.get("scheme") in {"0002", "fr_siren"}
        else ""
    )
    return {
        "name": str(value["name"]),
        "type": "business",
        "siren": siren,
        "vat_number": str(value.get("vat_identifier") or ""),
        "address_line1": str(address.get("address_line1") or ""),
        "address_line2": str(address.get("address_line2") or ""),
        "postal_code": str(address.get("post_code") or ""),
        "city": str(address.get("city") or ""),
        "country_code": str(address.get("country_code") or ""),
        "email": str(contact.get("email_address") or ""),
        "phone": str(contact.get("phone_number") or ""),
        "electronic_address": str(electronic.get("value") or ""),
        "electronic_address_scheme": str(electronic.get("scheme") or ""),
    }


def _projection(payload):
    with localcontext() as context:
        context.prec = 60
        return _project_invoice(payload)


def _project_invoice(payload):
    data = payload.get("en_invoice")
    if not isinstance(data, dict):
        raise BusinessError("Données structurées EN16931 absentes.", 502)
    number = str(data.get("number") or "").strip()
    currency = str(data.get("currency_code") or "")
    if not number or not re.fullmatch(r"[A-Z]{3}", currency):
        raise BusinessError("Numéro ou devise de facture SUPERPDP invalide.", 502)
    type_code = str(data.get("type_code"))
    if type_code in {"381", "261", "396"}:
        kind = "credit_note"
    elif type_code in {"380", "384", "386", "389", "875", "876", "877"}:
        kind = "invoice"
    else:
        raise BusinessError(
            "Type de facture SUPERPDP non pris en charge : traitement manuel requis.",
            502,
        )
    totals = data.get("totals") or {}
    net, gross = (
        _number(totals.get("total_without_vat")),
        _number(totals.get("total_with_vat")),
    )
    breakdown = data.get("vat_break_down")
    if not isinstance(breakdown, list) or not breakdown:
        raise BusinessError("Ventilation TVA SUPERPDP absente.", 502)
    tax_value = totals.get("total_vat_amount")
    vat = (
        sum(
            (_number(bucket.get("vat_category_tax_amount")) for bucket in breakdown),
            Decimal(0),
        )
        if tax_value is None
        else _number(
            tax_value.get("value") if isinstance(tax_value, dict) else tax_value
        )
    )
    due = _number(totals.get("amount_due_for_payment"))
    rounding = _number(totals.get("rounding_amount"), "0")
    paid = _number(totals.get("paid_amount"), "0")
    if (
        net + vat != gross
        or gross - paid + rounding != due
        or min(net, gross, due, paid) < 0
    ):
        raise BusinessError(
            "Totaux de facture SUPERPDP incohérents ; aucune valeur n'a été recalculée.",
            502,
        )
    groups = defaultdict(list)
    line_rows = data.get("lines")
    if not isinstance(line_rows, list) or not line_rows or len(line_rows) > 10000:
        raise BusinessError(
            "Lignes de facture SUPERPDP absentes ou trop nombreuses.", 502
        )
    for line in line_rows:
        if not isinstance(line, dict):
            raise BusinessError("Ligne de facture SUPERPDP invalide.", 502)
        if line.get("subtype") in {"GROUP", "INFORMATION"}:
            continue
        information, prices = (
            line.get("item_information") or {},
            line.get("price_details") or {},
        )
        tax = line.get("vat_information") or {}
        rate = _number(tax.get("invoiced_item_vat_rate"), "0")
        category = str(tax.get("invoiced_item_vat_category_code") or "O")
        row = dict(
            description=str(
                information.get("name")
                or information.get("description")
                or "Ligne fournisseur"
            ),
            item_reference=str(
                information.get("seller_identifier") or line.get("identifier") or ""
            ),
            quantity=_number(line.get("invoiced_quantity"), "1", rounded=True),
            unit_code=str(line.get("invoiced_quantity_code") or "C62"),
            unit_price_excluding_tax=_number(
                prices.get("item_net_price"), "0", rounded=True
            ),
            vat_category_code=category,
            vat_rate=rate,
            total_excluding_tax=_number(line.get("net_amount")),
            total_vat=_number(line["line_vat_amount"])
            if line.get("line_vat_amount") is not None
            else None,
        )
        groups[(category, rate)].append(row)
    bases = taxes = Decimal(0)
    seen = set()
    for bucket in breakdown:
        key = (
            str(bucket.get("vat_category_code") or "O"),
            _number(bucket.get("vat_category_rate"), "0"),
        )
        if key in seen:
            raise BusinessError("Ventilation TVA dupliquée.", 502)
        seen.add(key)
        basis, tax = (
            _number(bucket.get("vat_category_taxable_amount")),
            _number(bucket.get("vat_category_tax_amount")),
        )
        bases += basis
        taxes += tax
        rows = groups[key]
        adjustment = basis - sum(
            (row["total_excluding_tax"] for row in rows), Decimal(0)
        )
        if adjustment or not rows:
            rows.append(
                dict(
                    description="Ajustement du document (remises / charges)",
                    item_reference="",
                    quantity=Decimal(1),
                    unit_code="C62",
                    unit_price_excluding_tax=adjustment,
                    vat_category_code=key[0],
                    vat_rate=key[1],
                    total_excluding_tax=adjustment,
                    total_vat=None,
                )
            )
        missing = [row for row in rows if row["total_vat"] is None]
        remaining = tax - sum(
            (row["total_vat"] or Decimal(0) for row in rows), Decimal(0)
        )
        if missing:
            denominator = sum(
                (row["total_excluding_tax"] for row in missing), Decimal(0)
            )
            allocated = Decimal(0)
            for row in missing[:-1]:
                with localcontext() as context:
                    context.prec = 60
                    row["total_vat"] = (
                        (remaining * row["total_excluding_tax"] / denominator).quantize(
                            Decimal(".01")
                        )
                        if denominator
                        else Decimal(0)
                    )
                allocated += row["total_vat"]
            missing[-1]["total_vat"] = remaining - allocated
        elif remaining:
            raise BusinessError(
                "TVA des lignes différente de la ventilation du document.", 502
            )
    if bases != net or taxes != vat or set(groups) - seen:
        raise BusinessError("Ventilation TVA différente des totaux SUPERPDP.", 502)
    rows = []
    for group in groups.values():
        for row in group:
            row["position"] = len(rows) + 1
            row["total_including_tax"] = row["total_excluding_tax"] + row["total_vat"]
            rows.append(row)
    return dict(
        number=number,
        type=kind,
        issue_date=iso_date(data.get("issue_date")),
        due_date=iso_date(data.get("payment_due_date"), optional=True),
        currency_code=currency,
        total_excluding_tax=net,
        total_vat=vat,
        total_including_tax=gross,
        amount_due=0 if kind == "credit_note" else due,
        rounding_amount=rounding,
        seller_snapshot=json_text(_party(data.get("seller"))),
        buyer_snapshot=json_text(_party(data.get("buyer"))),
        buyer_reference=str(data.get("buyer_reference") or ""),
        reference=str(data.get("purchase_order_reference") or ""),
        payment_terms=str(data.get("payment_terms") or ""),
    ), rows


def _binary(session, remote_id, format="original"):
    raw, mime = pdp.request(
        session,
        "GET",
        f"/v1.beta/invoices/{remote_id}?" + urlencode({"format": format}),
        _token(session),
        binary=True,
    )
    if raw.startswith(b"%PDF-") and mime == "application/pdf":
        return raw, mime, ".pdf"
    if mime in {"application/xml", "text/xml"} and format == "original":
        from lxml import etree

        try:
            tree = etree.fromstring(
                raw,
                parser=etree.XMLParser(
                    resolve_entities=False,
                    no_network=True,
                    load_dtd=False,
                    huge_tree=False,
                ),
            )
            if tree.getroottree().docinfo.doctype or etree.QName(
                tree
            ).localname not in {"Invoice", "CreditNote", "CrossIndustryInvoice"}:
                raise ValueError()
        except (etree.LxmlError, ValueError) as exc:
            raise BusinessError("Original XML SUPERPDP invalide.", 502) from exc
        return raw, "application/xml", ".xml"
    raise BusinessError("Le fichier reçu n'est pas un original PDF/XML reconnu.", 502)


def _store(document, remote_id, environment, raw, mime, extension, kind):
    sha = hashlib.sha256(raw).hexdigest()
    existing = DocumentFile.objects.filter(
        document_uid=document.document_uid, kind=kind
    ).first()
    if existing:
        if existing.sha256 != sha:
            raise BusinessError(
                "L'original SUPERPDP diffère de celui déjà conservé.", 409
            )
        files.read_verified(existing)
        return existing
    path = (
        f"pdp/{document.company_uid}/{environment}/{remote_id}/{kind}-{sha}{extension}"
    )
    try:
        stored = files.put(path, raw)
    except FileExistsError:
        if hashlib.sha256(files.resolve(path).read_bytes()).hexdigest() != sha:
            raise BusinessError(
                "Fichier SUPERPDP présent avec une empreinte différente.", 409
            )
        stored = "storage/" + path
    return DocumentFile.objects.create(
        document_uid=document.document_uid,
        kind=kind,
        storage_path=stored,
        mime_type=mime,
        sha256=sha,
        file_size=len(raw),
    )


def _peer(company, party, direction):
    query = Customer.objects.filter(company_uid=company.company_uid, status="active")
    match = None
    for key in ("siren", "vat_number", "electronic_address"):
        if party.get(key):
            candidates = list(query.filter(**{key: party[key]})[:2])
            if len(candidates) == 1:
                match = candidates[0]
                break
    if match:
        return match
    data = {
        key: value
        for key, value in party.items()
        if key
        in {
            "siren",
            "vat_number",
            "address_line1",
            "address_line2",
            "postal_code",
            "city",
            "country_code",
            "email",
            "phone",
            "electronic_address",
            "electronic_address_scheme",
            "type",
        }
    }
    customer = Customer.objects.create(
        company_uid=company.company_uid, legal_name=party["name"], **data
    )
    CustomerProfile.objects.create(
        company_uid=company.company_uid,
        customer_uid=customer.customer_uid,
        relationship="supplier" if direction == "incoming" else "customer",
    )
    return customer


def ingest_invoice(company, session, state, overview):
    if not isinstance(overview, dict):
        raise BusinessError("Facture SUPERPDP invalide.", 502)
    remote_id = pdp.identifier(overview.get("id"))
    if pdp.identifier(
        overview.get("company_id")
    ) != state.remote_company_id or overview.get("direction") not in {"in", "out"}:
        raise BusinessError(
            "Une facture reçue concerne une autre entreprise ou une direction inconnue.",
            409,
        )
    known = PdpInvoice.objects.filter(
        company_uid=company.company_uid, remote_id=remote_id
    ).first()
    if known:
        if (known.remote_company_id, known.remote_environment, known.direction) != (
            state.remote_company_id,
            state.remote_environment,
            overview["direction"],
        ):
            raise BusinessError("La liaison de facture SUPERPDP a changé.", 409)
        original = DocumentFile.objects.get(
            document_uid=known.document_uid, file_uid=known.original_file_uid
        )
        files.read_verified(original)
        return known
    payload = pdp.request(
        session, "GET", f"/v1.beta/invoices/{remote_id}", _token(session)
    )
    if (
        not isinstance(payload, dict)
        or pdp.identifier(payload.get("id")) != remote_id
        or pdp.identifier(payload.get("company_id")) != state.remote_company_id
        or payload.get("direction") != overview["direction"]
    ):
        raise BusinessError("Identité de la facture SUPERPDP incohérente.", 409)
    values, lines = _projection(payload)
    direction = "incoming" if payload["direction"] == "in" else "outgoing"
    raw, mime, extension = _binary(session, remote_id)
    sha = hashlib.sha256(raw).hexdigest()
    readable = None
    if mime == "application/xml":
        try:
            readable = _binary(session, remote_id, "factur-x")
        except BusinessError:
            pass  # The verified original remains usable if conversion is unavailable.
    with transaction.atomic():
        Company.objects.select_for_update().get(pk=company.pk)
        known = PdpInvoice.objects.filter(
            company_uid=company.company_uid, remote_id=remote_id
        ).first()
        if known:
            return known
        document = Document.objects.filter(
            company_uid=company.company_uid,
            direction=direction,
            pdp_invoice_id=remote_id,
        ).first()
        external = str(payload.get("external_id") or "")
        operation = (
            PdpOutbox.objects.filter(
                company_uid=company.company_uid,
                document_uid=external,
                operation="invoice",
                remote_company_id=state.remote_company_id,
                remote_environment=state.remote_environment,
            ).first()
            if direction == "outgoing" and external
            else None
        )
        if not document and operation:
            document = Document.objects.filter(
                company_uid=company.company_uid,
                direction=direction,
                document_uid=operation.document_uid,
            ).first()
        # A manual import is linked only with the exact same original AND invoice
        # metadata. A supplier number alone never links two documents.
        if not document:
            candidates = Document.objects.filter(
                company_uid=company.company_uid,
                direction=direction,
                number=values["number"],
                issue_date=values["issue_date"],
                currency_code=values["currency_code"],
                total_including_tax=values["total_including_tax"],
                document_uid__in=DocumentFile.objects.filter(sha256=sha).values(
                    "document_uid"
                ),
            )
            matching = [
                item
                for item in candidates
                if item.seller.get("name")
                == json.loads(values["seller_snapshot"])["name"]
                and not PdpInvoice.objects.filter(
                    document_uid=item.document_uid
                ).exists()
            ]
            document = matching[0] if len(matching) == 1 else None
        imported = document is None
        if document:
            if (
                document.status == "draft"
                or document.currency_code != values["currency_code"]
                or document.total_excluding_tax != values["total_excluding_tax"]
                or document.total_vat != values["total_vat"]
                or document.total_including_tax != values["total_including_tax"]
            ):
                raise BusinessError(
                    "La facture locale et celle de SUPERPDP diffèrent ; rapprochement manuel requis.",
                    409,
                )
            if direction == "outgoing":
                originals = document.files.filter(kind="facturx")
                if not originals.filter(sha256=sha).exists():
                    raise BusinessError(
                        "L'original émis localement diffère de l'original SUPERPDP.",
                        409,
                    )
        else:
            party = json.loads(
                values["seller_snapshot"]
                if direction == "incoming"
                else values["buyer_snapshot"]
            )
            if direction == "outgoing" and payload.get("processing_rule") == "B2C":
                party["type"] = "individual"
            customer = _peer(company, party, direction)
            local_number = values["number"]
            if Document.objects.filter(
                company_uid=company.company_uid, number=local_number
            ).exists():
                local_number = f"{local_number} [SUPERPDP {remote_id}]"
            if Document.objects.filter(
                company_uid=company.company_uid, number=local_number
            ).exists():
                local_number = f"SUPERPDP-{remote_id}-{uid()[:8]}"
            values["number"] = local_number
            status = "received" if direction == "incoming" else "sent"
            if state.remote_environment == "sandbox":
                status, values["amount_due"] = "simulated", 0
            document = Document.objects.create(
                company_uid=company.company_uid,
                customer_uid=customer.customer_uid,
                direction=direction,
                status=status,
                created_by_user_uid=company.owner_user_uid,
                pdp_invoice_id=remote_id,
                pdp_external_id=external or None,
                pdp_processing_rule=payload.get("processing_rule"),
                received_at=stamp() if direction == "incoming" else None,
                sent_at=stamp()
                if direction == "outgoing" and status != "simulated"
                else None,
                **values,
            )
            DocumentLine.objects.bulk_create(
                [
                    DocumentLine(document_uid=document.document_uid, **row)
                    for row in lines
                ]
            )
            event(
                document,
                "pdp_imported",
                "Facture et original récupérés depuis SUPERPDP",
            )
        original = _store(
            document,
            remote_id,
            state.remote_environment,
            raw,
            mime,
            extension,
            "pdp_original",
        )
        readable_file = (
            _store(
                document, remote_id, state.remote_environment, *readable, "pdp_readable"
            )
            if readable
            else None
        )
        link = PdpInvoice.objects.create(
            company_uid=company.company_uid,
            remote_id=remote_id,
            remote_company_id=state.remote_company_id,
            remote_environment=state.remote_environment,
            document_uid=document.document_uid,
            direction=payload["direction"],
            invoice_number=str(payload["en_invoice"]["number"]),
            imported=imported,
            original_file_uid=original.file_uid,
            readable_file_uid=readable_file.file_uid if readable_file else "",
            payload=payload,
        )
        if operation and operation.status in {"uncertain", "sending", "queued"}:
            operation.status, operation.remote_id, operation.last_error = (
                "sent",
                remote_id,
                "",
            )
            operation.save()
            # Never reset existing amounts, bank payments, snapshots or originals.
            if not document.pdp_invoice_id:
                document.pdp_invoice_id = remote_id
                document.save(update_fields=["pdp_invoice_id"])
        return link


def ingest_event(company, session, state, data):
    if not isinstance(data, dict):
        raise BusinessError("Événement SUPERPDP invalide.", 502)
    remote_id, invoice_id = (
        int(pdp.identifier(data.get("id"))),
        pdp.identifier(data.get("invoice_id")),
    )
    code = data.get("status_code")
    occurred = parse_datetime(str(data.get("created_at") or ""))
    if (
        not isinstance(code, str)
        or not code
        or len(code) > 80
        or not occurred
        or timezone.is_naive(occurred)
    ):
        raise BusinessError("Code ou date de l'événement SUPERPDP invalide.", 502)
    link = PdpInvoice.objects.filter(
        company_uid=company.company_uid, remote_id=invoice_id
    ).first()
    if not link:
        overview = pdp.request(
            session, "GET", f"/v1.beta/invoices/{invoice_id}", _token(session)
        )
        link = ingest_invoice(company, session, state, overview)
    if (link.remote_company_id, link.remote_environment) != (
        state.remote_company_id,
        state.remote_environment,
    ):
        raise BusinessError("L'événement concerne une autre liaison SUPERPDP.", 409)
    with transaction.atomic():
        Company.objects.select_for_update().get(pk=company.pk)
        previous = PdpRemoteEvent.objects.filter(
            company_uid=company.company_uid, remote_id=remote_id
        ).first()
        if previous:
            if previous.payload != data:
                raise BusinessError(
                    "Un événement SUPERPDP déjà conservé a changé.", 409
                )
            return previous
        remote = PdpRemoteEvent.objects.create(
            company_uid=company.company_uid,
            remote_id=remote_id,
            remote_invoice_id=invoice_id,
            document_uid=link.document_uid,
            status_code=code,
            payload=data,
            occurred_at=occurred,
        )
        if not InvoiceEvent.objects.filter(
            company_uid=company.company_uid,
            document_uid=link.document_uid,
            provider="superpdp",
            pdp_event_id=str(remote_id),
        ).exists():
            InvoiceEvent.objects.create(
                company_uid=company.company_uid,
                document_uid=link.document_uid,
                provider="superpdp",
                pdp_invoice_id=invoice_id,
                pdp_event_id=str(remote_id),
                event_code=code,
                event_label=str(
                    data.get("status_text") or STATUS_LABELS.get(code, code)
                ),
                direction="incoming" if link.direction == "in" else "outgoing",
                payload=json_text(data),
                occurred_at=occurred.astimezone(
                    timezone.get_default_timezone()
                ).isoformat(),
            )
        link = PdpInvoice.objects.select_for_update().get(pk=link.pk)
        if code not in link.status_codes:
            link.status_codes = link.status_codes + [code]
        if remote_id > link.latest_event_id:
            link.latest_event_id, link.latest_status = remote_id, code
        link.synced_at = timezone.now()
        link.save(
            update_fields=[
                "status_codes",
                "latest_event_id",
                "latest_status",
                "synced_at",
            ]
        )
        # The response ID is exact for accepted lifecycle POSTs; uncertain POSTs
        # have no idempotency key and require explicit provider-side verification.
        PdpOutbox.objects.filter(
            company_uid=company.company_uid,
            operation="event",
            remote_id=str(remote_id),
            status="sent",
        ).update(last_error="")
        return remote


def _checkpoint(state, lease, **updates):
    updates["lease_until"] = timezone.now() + timedelta(minutes=5)
    if PdpSyncState.objects.filter(pk=state.pk, lease=lease).update(**updates) != 1:
        raise BusinessError("Une autre synchronisation a repris le traitement.", 409)


def sync_company(company_uid, *, force=False):
    if settings.EXTERNAL_SERVICES_DISABLED:
        raise BusinessError(
            "Les services externes sont désactivés sur cette instance.", 503
        )
    company = Company.objects.get(company_uid=company_uid, status="active")
    with transaction.atomic():
        state = state_for(company)
        state = PdpSyncState.objects.select_for_update().get(pk=state.pk)
        if state.lease_until and state.lease_until > timezone.now():
            return False
        if (
            not force
            and not state.requested
            and (not state.enabled or state.next_sync_at > timezone.now())
        ):
            return False
        lease = uid()
        state.lease, state.lease_until, state.requested = (
            lease,
            timezone.now() + timedelta(minutes=5),
            False,
        )
        state.save(update_fields=["lease", "lease_until", "requested"])
    pending = False
    try:
        session = pdp.session_for(company)
        bound = _binding(company, session, verify=True)
        state.remote_company_id, state.remote_environment = (
            bound.remote_company_id,
            bound.remote_environment,
        )
        for kind in ("invoices", "invoice_events"):
            cursor_field = "invoice_cursor" if kind == "invoices" else "event_cursor"
            cursor = getattr(state, cursor_field)
            for page in range(settings.SUPERPDP_MAX_SYNC_PAGES):
                _checkpoint(state, lease)
                query = {"starting_after_id": cursor, "limit": 50}
                if kind == "invoices":
                    query["order"] = "asc"
                data = pdp.request(
                    session,
                    "GET",
                    "/v1.beta/" + kind + "?" + urlencode(query),
                    _token(session),
                )
                if (
                    not isinstance(data, dict)
                    or not isinstance(data.get("data"), list)
                    or not isinstance(data.get("has_after"), bool)
                    or len(data["data"]) > 50
                    or data["has_after"]
                    and not data["data"]
                ):
                    raise BusinessError("Pagination SUPERPDP invalide.", 502)
                for item in data["data"]:
                    item_id = int(pdp.identifier(item.get("id")))
                    if item_id <= cursor:
                        raise BusinessError(
                            "Le curseur SUPERPDP n'avance pas strictement.", 502
                        )
                    _checkpoint(state, lease)
                    if kind == "invoices":
                        ingest_invoice(company, session, state, item)
                    else:
                        ingest_event(company, session, state, item)
                    cursor = item_id
                    _checkpoint(state, lease, **{cursor_field: cursor})
                if not data["has_after"]:
                    break
                if page + 1 == settings.SUPERPDP_MAX_SYNC_PAGES:
                    pending = True
        updates = dict(
            last_success_at=timezone.now(),
            last_error="",
            next_sync_at=timezone.now()
            + timedelta(seconds=3 if pending else settings.SUPERPDP_SYNC_INTERVAL),
            lease="",
            lease_until=None,
        )
        if pending:
            updates["requested"] = True
        PdpSyncState.objects.filter(pk=state.pk, lease=lease).update(**updates)
        return True
    except Exception as exc:
        PdpSyncState.objects.filter(pk=state.pk, lease=lease).update(
            last_error=_error(exc),
            next_sync_at=timezone.now()
            + timedelta(seconds=settings.SUPERPDP_SYNC_INTERVAL),
            lease="",
            lease_until=None,
        )
        raise


def sync_due(limit=1):
    if settings.EXTERNAL_SERVICES_DISABLED:
        return 0
    states = (
        PdpSyncState.objects.filter(
            Q(requested=True) | Q(enabled=True, next_sync_at__lte=timezone.now())
        )
        .filter(Q(lease_until__isnull=True) | Q(lease_until__lt=timezone.now()))
        .filter(
            company_uid__in=Company.objects.filter(status="active").values(
                "company_uid"
            )
        )
        .order_by("next_sync_at")[:limit]
    )
    count = 0
    for state in states:
        try:
            count += bool(sync_company(state.company_uid))
        except Exception:
            # Isolate one provider/company failure from mail, OCR and other tenants.
            continue
    return count
