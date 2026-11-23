import hashlib
import io
from pathlib import Path

from django.http import FileResponse
from PIL import Image
from pypdf import PdfReader, PdfWriter

from core.common import BusinessError
from core.models import EvidenceFile, EvidenceLink


def read_upload(upload, limit=16 * 1024 * 1024):
    if not upload or upload.size <= 0 or upload.size > limit:
        raise BusinessError("Choisissez un fichier non vide de 16 Mo au maximum.")
    return upload.read(limit + 1)


def store(company, filename, raw):
    if not 0 < len(raw) <= 16 * 1024 * 1024:
        raise BusinessError("La pièce doit peser entre un octet et 16 Mo.")
    if raw[:5] == b"%PDF-":
        try:
            reader = PdfReader(io.BytesIO(raw))
            pages = len(reader.pages)
            if reader.is_encrypted or not 1 <= pages <= 500:
                raise ValueError()
        except Exception as exc:
            raise BusinessError(
                "Choisissez un PDF lisible, non chiffré, de 500 pages au maximum."
            ) from exc
        mime = "application/pdf"
    else:
        try:
            with Image.open(io.BytesIO(raw)) as image:
                mime = {"JPEG": "image/jpeg", "PNG": "image/png"}[image.format]
                image.verify()
        except Exception as exc:
            raise BusinessError(
                "Choisissez un justificatif PDF, JPEG ou PNG lisible."
            ) from exc
        pages = 1
    source, _ = EvidenceFile.objects.get_or_create(
        company_uid=company.company_uid,
        digest=hashlib.sha256(raw).hexdigest(),
        defaults={
            "contents": raw,
            "pages": pages,
            "filename": Path(filename).name[:255],
            "mime_type": mime,
        },
    )
    return source


def attach(target, source, page=0):
    if source.company_uid != target.company_uid:
        raise BusinessError("Cette pièce est indisponible.", 404)
    try:
        page = int(page or 0)
    except (TypeError, ValueError):
        raise BusinessError("Numéro de page invalide.")
    if page < 0 or page > source.pages:
        raise BusinessError("Le numéro de page dépasse les limites de la pièce.")
    field = "receipt" if hasattr(target, "ingestion_uid") else "transaction"
    return EvidenceLink.objects.get_or_create(
        source=source, page=page, **{field: target}
    )[0]


def stream(source, page=0):
    raw, name = io.BytesIO(bytes(source.contents)), source.filename
    if page:
        if not 1 <= page <= source.pages:
            raise BusinessError("Page introuvable.", 404)
        if source.mime_type == "application/pdf":
            writer = PdfWriter()
            writer.add_page(PdfReader(raw).pages[page - 1])
            raw = io.BytesIO()
            writer.write(raw)
            raw.seek(0)
            name = f"{Path(name).stem} - page {page}.pdf"
    response = FileResponse(raw, content_type=source.mime_type, filename=name)
    response["Cache-Control"] = "private, no-store"
    return response
