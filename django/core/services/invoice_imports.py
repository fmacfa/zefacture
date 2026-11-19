"""Upload a private original once and identify invoices that warrant comparison."""

import hashlib
import re

from django.db import transaction
from django.db.models import Q

from core.common import BusinessError, decimal, unpack
from core.models import Company, DocumentFile, Ingestion, PdpInvoice
from . import files
from .invoice_match import invoice_keys_v1, name_key
from .receipt_extraction import local_suggestion


def index_extraction(item, data):
    item.source_invoice_ref, item.source_invoice_match = invoice_keys_v1(
        item.source_kind, data
    )


def local_invoice(item):
    data = local_suggestion(item.transcription_text)
    raw = item.transcription_text or ""
    numbers = {
        m.strip(" .:")
        for m in re.findall(
            r"(?im)^\s*(?:facture|invoice)\s*(?:n[°ºo.]\s*|numéro\s*|number\s*|#\s*|:\s*|\s+)([A-Z0-9][A-Z0-9./_-]{1,99})\s*$",
            raw,
        )
        if any(c.isdigit() for c in m)
    }
    if len(numbers) == 1:
        data.setdefault("invoice", {})["number"] = numbers.pop()
    if item.source_kind == "sales_invoice":
        data.pop("supplier", None)
        buyers = {
            m.strip()
            for m in re.findall(
                r"(?im)^\s*(?:client|destinataire|factur[ée] à|bill to)\s*:\s*([^\r\n]{2,200})$",
                raw,
            )
        }
        data["buyer"] = {"legal_name": buyers.pop()} if len(buyers) == 1 else {}
    return data


def _field_values(data):
    """Editable scalar facts and the complete set of invoice lines."""
    if not isinstance(data, dict):
        return {}
    facts = {}
    for group in ("supplier", "buyer", "invoice", "totals"):
        fields = data.get(group)
        if isinstance(fields, dict):
            for name, value in fields.items():
                if (
                    isinstance(name, str)
                    and "." not in name
                    and isinstance(value, (str, int, float))
                ):
                    facts[f"{group}.{name}"] = value
    lines = data.get("lines")
    if isinstance(lines, list) and any(
        isinstance(row, dict) and row.get("description") for row in lines
    ):
        facts["lines"] = lines
    return facts


def reviewed_fields(data):
    if not isinstance(data, dict):
        return set()
    if isinstance(data.get("_reviewed"), list):
        return {name for name in data["_reviewed"] if isinstance(name, str)}
    # Older saved review forms contain the editor's reference field, even when empty.
    invoice = data.get("invoice")
    if isinstance(invoice, dict) and "reference" in invoice:
        return {name for name, value in _field_values(data).items() if value != ""}
    return set()


def mark_reviewed(item, data):
    before = _field_values(review_data(item))
    fields = _field_values(data)
    confirmed = {
        name
        for name, value in fields.items()
        if value != "" or before.get(name) not in ("", None)
    }
    if "lines" in before and "lines" in data:
        confirmed.add("lines")
    data["_reviewed"] = sorted(
        confirmed | reviewed_fields(unpack(item.extraction_json))
    )


def keep_reviewed(item, suggestions):
    previous = unpack(getattr(item, "extraction_json", None))
    confirmed = reviewed_fields(previous)
    for name in confirmed:
        if name == "lines":
            suggestions["lines"] = previous.get("lines", [])
        elif "." in name:
            group, key = name.split(".", 1)
            source = previous.get(group)
            if isinstance(source, dict) and group in {
                "supplier",
                "buyer",
                "invoice",
                "totals",
            }:
                suggestions.setdefault(group, {})[key] = source.get(key, "")
    suggestions["_reviewed"] = sorted(confirmed)
    return suggestions


def review_data(item):
    """Fill missing structured fields from local OCR, preserving explicit corrections."""
    parsed = unpack(item.extraction_json)
    if not isinstance(parsed, dict):
        parsed = {}
    confirmed = reviewed_fields(parsed)
    for group, fallback in local_invoice(item).items():
        fields = parsed.get(group)
        if not isinstance(fields, dict):
            parsed[group] = fields = {}
        for key, value in fallback.items():
            if fields.get(key) in (None, "") and f"{group}.{key}" not in confirmed:
                fields[key] = value
    return parsed


def inspect_duplicates(item, visible_items, documents, data=None):
    if item.document_uid or item.source_kind not in {
        "sales_invoice",
        "supplier_invoice",
    }:
        return [], []
    extracted = data if isinstance(data, dict) else unpack(item.extraction_json)
    if not isinstance(extracted, dict) or not extracted:
        extracted = local_invoice(item)
    ref, fingerprint = invoice_keys_v1(item.source_kind, extracted)
    if not ref and not fingerprint:
        return [], []
    sale = item.source_kind == "sales_invoice"
    documents = documents.filter(
        direction="outgoing" if sale else "incoming",
        type__in=["invoice", "progress_invoice"],
    )
    pending_condition = Q()
    candidates = []
    if ref:
        pending_condition = Q(source_invoice_ref=ref)
        pdp = PdpInvoice.objects.filter(
            company_uid=item.company_uid, invoice_number__iexact=ref
        ).values("document_uid")
        candidates = list(
            documents.filter(Q(number__iexact=ref) | Q(document_uid__in=pdp)).order_by(
                "pk"
            )[:5]
        )
    if fingerprint:
        pending_condition |= Q(source_invoice_match=fingerprint)
        if not candidates:
            party = (
                (extracted.get("buyer") or extracted.get("customer"))
                if sale
                else (extracted.get("supplier") or extracted.get("seller"))
            )
            name = name_key(party.get("legal_name") or party.get("name"))
            invoice = extracted["invoice"]
            for doc in documents.filter(
                issue_date=invoice["issue_date"],
                currency_code=invoice["currency_code"].strip().upper(),
                total_including_tax=decimal(extracted["totals"]["total_including_tax"]),
            ).iterator():
                peer = doc.buyer if sale else doc.seller
                if isinstance(peer, dict) and name_key(peer.get("name")) == name:
                    candidates.append(doc)
                    if len(candidates) == 5:
                        break
    others = list(
        visible_items.filter(
            pending_condition, source_kind=item.source_kind, document_uid__isnull=True
        )
        .exclude(pk=item.pk)
        .order_by("pk")[:5]
    )
    return candidates, others


def upload_one(
    company, user, visible_items, documents, uploaded, kind, batch, details, pipeline
):
    from .jobs import enqueue

    if not uploaded or not 0 < uploaded.size <= 15 * 1024 * 1024:
        raise BusinessError("Chaque fichier doit contenir entre 1 octet et 15 Mo.")
    h = hashlib.sha256()
    for part in uploaded.chunks():
        h.update(part)
    uploaded.seek(0)
    sha = h.hexdigest()
    metadata = None
    try:
        with transaction.atomic():
            Company.objects.select_for_update().get(pk=company.pk)
            existing = visible_items.filter(sha256=sha).order_by("pk").first()
            if existing:
                return {
                    "state": "duplicate",
                    "label": "Doublon exact · original déjà importé",
                    "url": "/app/imports/" + existing.ingestion_uid,
                }
            doc = documents.filter(
                document_uid__in=DocumentFile.objects.filter(sha256=sha).values(
                    "document_uid"
                )
            ).first()
            if doc:
                return {
                    "state": "duplicate",
                    "label": "Doublon exact · facture déjà enregistrée",
                    "url": doc.url,
                }
            metadata = files.upload(uploaded, "uploads/" + company.company_uid)
            item = Ingestion.objects.create(
                company_uid=company.company_uid,
                uploaded_by_user_uid=user.user_uid,
                original_filename=uploaded.name[:255],
                source_kind=kind,
                batch_uid=batch,
                **metadata,
                **details,
            )
            if pipeline != "none":
                enqueue(item, "pipeline", pipeline)
            return {
                "state": "imported",
                "ingestion_uid": item.ingestion_uid,
                "queued": pipeline != "none",
                "label": "Analyse en file" if pipeline != "none" else "Importé",
                "url": "/app/imports/" + item.ingestion_uid,
            }
    except Exception:
        if metadata:
            files.resolve(metadata["storage_path"]).unlink(missing_ok=True)
        raise
