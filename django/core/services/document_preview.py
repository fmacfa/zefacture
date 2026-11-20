"""Render one page of a stored document, without exposing the original inline."""

import os
import re
import subprocess
from io import BytesIO
from pathlib import PurePosixPath

from PIL import Image, ImageOps

from core.common import BusinessError
from . import files


IMAGE_FORMATS = {
    "png": "PNG",
    "jpeg": "JPEG",
    "webp": "WEBP",
    "tiff": "TIFF",
    "bmp": "BMP",
}
MAX_EDGE = 1800
MAX_IMAGE_PIXELS = 40_000_000


def preview_format(file):
    mime = (file.mime_type or "").partition(";")[0].strip().lower()
    if mime in {"", "application/octet-stream"}:
        extension = PurePosixPath(file.storage_path or "").suffix[1:].lower()
        kind = {"jpg": "jpeg", "tif": "tiff"}.get(extension, extension)
        if getattr(file, "kind", "") in {"pdf", "facturx", "pdp_readable"}:
            kind = "pdf"
        return kind if kind in IMAGE_FORMATS or kind == "pdf" else ""
    elif mime == "application/pdf":
        return "pdf"
    else:
        kind = mime.removeprefix("image/") if mime.startswith("image/") else ""
    return kind if kind in IMAGE_FORMATS else ""


def preferred_file(records, link=None, *, supplier=True):
    """Prefer the supplier original, or its reading copy when it is XML."""
    if link:
        by_uid = {file.file_uid: file for file in records}
        candidates = [
            by_uid.get(link.original_file_uid),
            by_uid.get(link.readable_file_uid),
        ]
    elif supplier:
        candidates = files.supplier_originals(records)
    else:
        candidates = [
            file for file in records if file.kind in {"source", "pdf", "facturx", "xml"}
        ]
        if not candidates:
            candidates = [file for file in records if file.kind == "attachment"]
    return next((file for file in candidates if file and preview_format(file)), None)


def poppler(args, raw):
    try:
        result = subprocess.run(
            args,
            input=raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
            env={"PATH": os.environ.get("PATH", os.defpath), "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BusinessError(
            "Le rendu du PDF est temporairement indisponible.", 503
        ) from exc
    if result.returncode:
        raise BusinessError("Ce PDF est protégé ou illisible dans le lecteur.")
    return result.stdout


def check_page(page, count):
    if not 1 <= page <= count:
        raise BusinessError("Cette page n’existe pas dans le document.", 404)


def read_page(file, page, *, render_image=False):
    """Return the page count and, on demand, the requested page as PNG bytes."""
    kind = preview_format(file)
    if not kind:
        raise BusinessError("Ce format ne dispose pas d’un aperçu.", 415)
    raw = files.read_verified(file)
    if kind == "pdf":
        if not raw.startswith(b"%PDF-"):
            raise BusinessError("Le fichier conservé n’est pas un PDF lisible.")
        info = poppler(["pdfinfo", "-"], raw)
        matches = re.findall(rb"^Pages:\s+(\d+)\s*$", info, re.MULTILINE)
        if not matches or not 0 < int(matches[-1]):
            raise BusinessError("Le nombre de pages de ce PDF est illisible.")
        count = int(matches[-1])
        check_page(page, count)
        image = None
        if render_image:
            image = poppler(
                [
                    "pdftoppm",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-singlefile",
                    "-scale-to",
                    str(MAX_EDGE),
                    "-png",
                    "-",
                ],
                raw,
            )
            if not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise BusinessError("Le rendu de cette page a échoué.")
        return count, image
    try:
        with Image.open(BytesIO(raw), formats=[IMAGE_FORMATS[kind]]) as source:
            count = getattr(source, "n_frames", 1)
            check_page(page, count)
            source.seek(page - 1)
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise BusinessError(
                    "Cette image dépasse la limite d’aperçu de 40 mégapixels."
                )
            if not render_image:
                return count, None
            source.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
            image = ImageOps.exif_transpose(source).convert("RGBA")
            paper = Image.new("RGB", image.size, "white")
            paper.paste(image, mask=image.getchannel("A"))
            output = BytesIO()
            paper.save(output, format="PNG")
            return count, output.getvalue()
    except (
        Image.DecompressionBombError,
        OSError,
        ValueError,
        SyntaxError,
        EOFError,
    ) as exc:
        raise BusinessError(
            "Cette image est trop volumineuse ou illisible dans le lecteur."
        ) from exc
