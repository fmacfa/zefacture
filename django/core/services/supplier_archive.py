"""ZIP exports of stored, visible supplier invoices."""

from collections import defaultdict
from io import BytesIO
from itertools import batched
from pathlib import PurePosixPath
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from django.db.models import OuterRef
from django.utils.text import slugify
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from core.common import BusinessError
from core.models import DocumentFile, PdpInvoice
from . import files


FORMATS = {"both": ("pdf", "xml"), "pdf": ("pdf",), "xml": ("xml",)}


def file_format(file):
    mime = (file.mime_type or "").split(";", 1)[0].strip().lower()
    if mime in {"text/xml", "application/xml"}:
        return "xml"
    if mime == "application/pdf":
        return "pdf"
    if mime in {"", "application/octet-stream"}:
        # Some historical records only have a kind or an extension.
        if file.kind in {"pdf", "facturx", "xml"}:
            return "xml" if file.kind == "xml" else "pdf"
        extension = PurePosixPath(file.storage_path).suffix.lower()
        if extension in {".pdf", ".xml"}:
            return extension[1:]
    return ""


def stored_versions(document, records, formats):
    if document.original_uid is not None:
        by_uid = {record.file_uid: record for record in records}
        original = by_uid.get(document.original_uid)
        if not original:
            raise BusinessError(
                "L’original SUPERPDP est introuvable. Vérifiez la fiche de la facture.",
                409,
            )
        versions = {file_format(original): original}
        if "pdf" in formats and document.readable_uid and "pdf" not in versions:
            readable = by_uid.get(document.readable_uid)
            if not readable or file_format(readable) != "pdf":
                raise BusinessError(
                    "Le PDF de lecture SUPERPDP est introuvable. Vérifiez la fiche de la facture.",
                    409,
                )
            versions["pdf"] = readable
        return versions

    # Prefer the recorded invoice source over supplementary attachments. Files
    # arrive newest first, including historical originals without a MIME type.
    originals = [
        file for file in records if file.kind in {"source", "facturx", "pdf", "xml"}
    ]
    if not originals:
        originals = [file for file in records if file.kind == "attachment"][:1]
    versions = {}
    for file in originals:
        versions.setdefault(file_format(file), file)
    return versions


def invoice_xml(raw):
    if files.is_invoice_xml(raw, allow_legacy=True):
        return raw
    raise BusinessError(
        "Le XML conservé est illisible ou ne contient pas une facture.", 409
    )


def embedded_xml(pdf):
    try:
        matches = [
            attachment
            for attachment in PdfReader(BytesIO(pdf)).attachment_list
            if str(attachment.name).casefold()
            in {"factur-x.xml", "zugferd-invoice.xml", "xrechnung.xml"}
        ]
        if len(matches) == 1:
            return invoice_xml(matches[0].content)
    except (PyPdfError, ValueError, KeyError, TypeError, RecursionError):
        pass
    return None


def invoice_contents(document, records, formats):
    versions = stored_versions(document, records, formats)
    contents = {}
    extract_xml = "xml" in formats and "xml" not in versions
    if "pdf" in versions and ("pdf" in formats or extract_xml):
        pdf = files.read_verified(versions["pdf"])
        if not pdf.startswith(b"%PDF-"):
            raise BusinessError(
                "Le PDF conservé est illisible. Vérifiez la fiche de la facture.", 409
            )
        if "pdf" in formats:
            contents["pdf"] = pdf
        if extract_xml:
            contents["xml"] = embedded_xml(pdf)
    if "xml" in formats and "xml" in versions:
        contents["xml"] = invoice_xml(files.read_verified(versions["xml"]))
    return contents


def write_archive(output, documents, format_choice):
    formats = FORMATS.get(format_choice)
    if not formats:
        raise BusinessError("Choisissez PDF, XML ou PDF et XML.")
    links = PdpInvoice.objects.filter(
        company_uid=OuterRef("company_uid"),
        document_uid=OuterRef("document_uid"),
        direction="in",
    )
    rows = (
        documents.only("document_uid", "number", "issue_date")
        .annotate(
            original_uid=links.values("original_file_uid")[:1],
            readable_uid=links.values("readable_file_uid")[:1],
        )
        .order_by("issue_date", "pk")
    )
    missing = []
    document_count = 0
    file_count = 0
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=1) as archive:
        for batch in batched(rows.iterator(chunk_size=100), 100):
            records = defaultdict(list)
            for file in DocumentFile.objects.filter(
                document_uid__in=[doc.document_uid for doc in batch]
            ).order_by("-pk"):
                records[file.document_uid].append(file)
            for document in batch:
                document_count += 1
                number = slugify(document.number or "")[:100] or "facture"
                date = slugify(document.issue_date or "")[:10] or "sans-date"
                stem = f"{date}_{number}_{document.pk}"
                contents = invoice_contents(
                    document, records[document.document_uid], formats
                )
                for fmt in formats:
                    if not contents.get(fmt):
                        missing.append(f"{stem} — {fmt.upper()} indisponible")
                        continue
                    archive.writestr(
                        f"{fmt.upper()}/{stem}.{fmt}",
                        contents[fmt],
                        compress_type=ZIP_STORED if fmt == "pdf" else ZIP_DEFLATED,
                    )
                    file_count += 1
        if not document_count:
            raise BusinessError(
                "Aucune facture fournisseur ne correspond à cette période et à ces filtres."
            )
        if not file_count:
            raise BusinessError(
                "Aucun fichier du format choisi n’est disponible pour cette sélection."
            )
        if missing:
            archive.writestr("fichiers-indisponibles.txt", "\n".join(missing) + "\n")
    output.seek(0)
