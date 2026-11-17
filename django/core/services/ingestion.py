import os
import subprocess
import tempfile
from pathlib import Path
from django.db import transaction
from core.common import BusinessError, json_text, stamp, decimal, money
from core.models import Ingestion, Customer, Document, DocumentFile
from .files import resolve, read_verified
from .documents import save_customer, save_document
from . import extraction_ai
from .receipt_extraction import EXPENSE_KINDS


def transcribe(item):
    target = resolve(item.storage_path)
    read_verified(item)
    try:
        with tempfile.TemporaryDirectory(prefix="zefacture-ocr-") as directory:
            if item.mime_type == "application/pdf":
                from pypdf import PdfReader

                reader = PdfReader(target)
                if len(reader.pages) > 100:
                    raise BusinessError("Transcription limitée à 100 pages.")
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
                if len(text.strip()) < 30:
                    run = subprocess.run(
                        [
                            "pdftoppm",
                            "-f",
                            "1",
                            "-l",
                            "100",
                            "-scale-to",
                            "2000",
                            "-png",
                            str(target),
                            str(Path(directory) / "page"),
                        ],
                        capture_output=True,
                        timeout=90,
                    )
                    if run.returncode:
                        raise BusinessError("Rendu OCR impossible.")
                    images = sorted(Path(directory).glob("page-*.png"))
                else:
                    images = []
            else:
                text = ""
                images = [target]
            for image in images:
                run = subprocess.run(
                    [
                        "tesseract",
                        str(image),
                        "stdout",
                        "-l",
                        os.getenv("OCR_LANGUAGES", "fra+eng"),
                    ],
                    capture_output=True,
                    timeout=45,
                )
                if run.returncode:
                    raise BusinessError(
                        "OCR local impossible ; vérifiez Tesseract et les langues installées."
                    )
                text += "\n" + run.stdout.decode("utf-8", "replace")
        if not text.strip():
            raise BusinessError(
                "Aucun texte détecté. Vous pouvez le saisir manuellement."
            )
        item.transcription_text = text.replace("\0", "")[:500000]
        item.transcription_json = json_text(
            {"text": item.transcription_text, "engine": "local"}
        )
        item.transcription_status, item.status, item.last_error = (
            "done",
            "transcribed",
            None,
        )
        item.updated_at = stamp()
        item.save(
            update_fields=[
                "transcription_text",
                "transcription_json",
                "transcription_status",
                "status",
                "last_error",
                "updated_at",
            ]
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BusinessError(
            "Transcription locale indisponible ou délai dépassé.", 503
        ) from exc


def analyze(item, provider="openrouter"):
    data, model = extraction_ai.extract(
        provider, item.transcription_text if item is not None else None
    )
    item.extraction_json = json_text(data)
    item.extraction_model = model
    item.extraction_status, item.status, item.last_error = "done", "analyzed", None
    item.updated_at = stamp()
    item.save(
        update_fields=[
            "extraction_json",
            "extraction_model",
            "extraction_status",
            "status",
            "last_error",
            "updated_at",
        ]
    )


@transaction.atomic
def convert(company, user, item, data):
    item = Ingestion.objects.select_for_update().get(
        pk=item.pk, company_uid=company.company_uid
    )
    if item.document_uid:
        return Document.objects.get(
            document_uid=item.document_uid, company_uid=company.company_uid
        )
    if not isinstance(data, dict):
        raise BusinessError("Extraction JSON invalide.")
    supplier = data.get("supplier") or data.get("seller") or {}
    supplier = dict(supplier)
    supplier["legal_name"] = supplier.get("legal_name") or supplier.get("name")
    if not supplier["legal_name"]:
        raise BusinessError("Nom du fournisseur requis.")
    supplier.setdefault("country_code", "FR")
    supplier.setdefault("type", "business")
    customer = (
        Customer.objects.filter(
            company_uid=company.company_uid, siren=supplier["siren"], status="active"
        ).first()
        if supplier.get("siren")
        else None
    )
    if not customer:
        customer = save_customer(company, supplier)
    invoice = dict(data.get("invoice") or data)
    invoice.update(
        customer_uid=customer.customer_uid,
        lines=data.get("lines") or data.get("line_items") or data.get("items") or [],
    )
    for line in invoice["lines"]:
        if "unit_price" in line and "unit_price_excluding_tax" not in line:
            line["unit_price_excluding_tax"] = line["unit_price"]
    document = save_document(company, user, "invoice", invoice, direction="incoming")
    source_total = (data.get("totals") or {}).get("total_including_tax")
    if (
        source_total not in (None, "")
        and money(decimal(source_total)) != document.total_including_tax
    ):
        raise BusinessError(
            "Le total des lignes diffère du total extrait. Corrigez avant de valider."
        )
    DocumentFile.objects.create(
        document_uid=document.document_uid,
        kind="source",
        storage_path=item.storage_path,
        mime_type=item.mime_type,
        sha256=item.sha256,
        file_size=item.file_size,
    )
    item.document_uid = document.document_uid
    item.extraction_json = json_text(data)
    item.extraction_status = "done"
    item.status = "converted"
    if item.source_kind in EXPENSE_KINDS:
        item.expense_status = "validated"
        item.expense_amount = document.total_including_tax
        item.expense_currency = document.currency_code
        item.expense_date = document.issue_date
        item.merchant_name = document.seller.get("name", "")
    item.updated_at = stamp()
    item.save()
    return document
