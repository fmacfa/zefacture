import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from io import BytesIO

from django.conf import settings
from django.db import transaction
from django.template.loader import render_to_string
from core.common import BusinessError, uid, stamp, money
from core.models import Document, DocumentFile
from .calculations import calculate


def resolve(storage_path):
    relative = str(storage_path).removeprefix("storage/")
    target = (settings.DATA_DIR / relative).resolve()
    if not target.is_relative_to(settings.DATA_DIR.resolve()):
        raise BusinessError("Chemin de fichier interdit.", 404)
    return target


def read_verified(file):
    target = resolve(file.storage_path)
    if not target.is_file():
        raise BusinessError(
            "Le fichier original est absent. Restaurez-le depuis une sauvegarde.", 409
        )
    data = target.read_bytes()
    if file.sha256 and hashlib.sha256(data).hexdigest() != file.sha256:
        raise BusinessError(
            "L'empreinte du fichier original ne correspond pas. Restaurez la sauvegarde.",
            409,
        )
    return data


def put(relative, data):
    target = resolve(relative)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(target, "xb") as file:
        os.chmod(target, 0o600)
        file.write(data)
    return "storage/" + str(target.relative_to(settings.DATA_DIR.resolve()))


def upload(uploaded, directory):
    if not uploaded or uploaded.size > 15 * 1024 * 1024:
        raise BusinessError("Fichier requis, maximum 15 Mo.")
    raw = uploaded.read()
    if raw.startswith(b"%PDF-"):
        mime, extension = "application/pdf", ".pdf"
    else:
        from PIL import Image, UnidentifiedImageError

        try:
            with Image.open(BytesIO(raw)) as image:
                image.verify()
                extension = {
                    "PNG": ".png",
                    "JPEG": ".jpg",
                    "WEBP": ".webp",
                    "TIFF": ".tiff",
                    "BMP": ".bmp",
                }.get(image.format)
                if not extension:
                    raise ValueError()
                mime = "image/" + ("jpeg" if extension == ".jpg" else extension[1:])
        except (
            ValueError,
            UnidentifiedImageError,
            OSError,
            Image.DecompressionBombError,
        ) as exc:
            raise BusinessError("Formats acceptés : PDF, PNG, JPEG et WebP.") from exc
    relative = f"{directory}/{uid()}{extension}"
    return dict(
        storage_path=put(relative, raw),
        mime_type=mime,
        sha256=hashlib.sha256(raw).hexdigest(),
        file_size=len(raw),
    )


def validate_xml(raw):
    with tempfile.TemporaryDirectory(prefix="zefacture-xml-") as folder:
        target = Path(folder) / "factur-x.xml"
        target.write_bytes(raw)
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("validate_xml.py")),
                    str(target),
                ],
                capture_output=True,
                timeout=45,
            )
            report = json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            raise BusinessError(
                "Le validateur Factur-X local n'a pas abouti ; aucun document n'a été émis.",
                503,
            ) from exc
        if result.returncode or not all(
            report.get(k) is True for k in ("valid", "xsd", "schematron")
        ):
            failures = "; ".join(
                f.get("message", "") for f in report.get("failures", [])[:5]
            )
            raise BusinessError(
                "Factur-X non valide : "
                + (failures or report.get("error", "validation interrompue"))
            )
        return report


def party_xml(party):
    data = {
        "name": party.get("name", ""),
        "country_code": party.get("country_code", "FR"),
    }
    for target, source in {
        "addr_l1": "address_line1",
        "addr_l2": "address_line2",
        "postcode": "postal_code",
        "city": "city",
        "vat_identifier": "vat_number",
    }.items():
        if party.get(source):
            data[target] = party[source]
    if party.get("siren"):
        data.update(legal_identifier=party["siren"], legal_identifier_schemeid="0002")
    if party.get("electronic_address"):
        data.update(
            einvoicing_addr=party["electronic_address"],
            einvoicing_addr_schemeid=party.get("electronic_address_scheme") or "EM",
        )
    return data


def xml_data(document, computed):
    net, vat = computed["net"], computed["vat"]
    gross = net + vat
    payable = gross + document.rounding_amount
    paid = (
        0 if document.type == "credit_note" else max(0, payable - document.amount_due)
    )
    data = {
        "BT-1": document.number,
        "BT-2": document.issue_date,
        "BT-3": "381" if document.type == "credit_note" else "380",
        "BT-5": document.currency_code,
        "BT-9": document.due_date,
        "BT-20": document.payment_terms or "Paiement à l'échéance",
        "BG-4": party_xml(document.seller),
        "BG-7": party_xml(document.buyer),
        "BT-106": str(net),
        "BT-109": str(net),
        "BT-110": str(vat),
        "BT-112": str(gross),
        "BT-113": str(money(paid)),
        "BT-114": str(money(document.rounding_amount)),
        "BT-115": str(money(payable - paid)),
        "BG-23": [],
        "BG-25": [],
    }
    if document.buyer_reference:
        data["BT-10"] = document.buyer_reference
    if document.seller.get("bank_iban"):
        data.update(
            {
                "BT-81": document.payment_method_code or "30",
                "BT-84": document.seller["bank_iban"].replace(" ", ""),
            }
        )
        if document.seller.get("bank_bic"):
            data["BT-86"] = document.seller["bank_bic"]
    notes = [document.notes, document.seller.get("invoice_footer")]
    if any(notes):
        data["BG-1"] = [{"BT-22": str(n)} for n in notes if n]
    if document.source_invoice_uid:
        source = Document.objects.get(
            document_uid=document.source_invoice_uid, company_uid=document.company_uid
        )
        data["BG-3"] = [{"BT-25": source.number, "BT-26": source.issue_date}]
    reasons = {
        "E": "Exonération de TVA",
        "AE": "Autoliquidation de TVA",
        "K": "Livraison intracommunautaire",
        "G": "Exportation hors Union européenne",
        "O": "Hors champ de la TVA",
    }
    for tax in computed["taxes"]:
        item = {
            "BT-116": str(tax["basis"]),
            "BT-117": str(tax["tax"]),
            "BT-118": tax["category"],
            "BT-119": str(tax["rate"]),
        }
        if tax["category"] in reasons:
            item["BT-120"] = document.notes or reasons[tax["category"]]
        if tax["category"] == "O":
            item.pop("BT-119")
        data["BG-23"].append(item)
    for line in computed["lines"]:
        item = {
            "BT-126": str(line["position"]),
            "BT-129": str(line["quantity"]),
            "BT-130": line["unit_code"],
            "BT-131": str(line["total_excluding_tax"]),
            "BT-146": str(line["unit_price_excluding_tax"]),
            "BT-149": "1",
            "BT-151": line["vat_category_code"],
            "BT-152": str(line["vat_rate"]),
            "BT-153": line["description"],
        }
        if line["vat_category_code"] == "O":
            item.pop("BT-152")
        base = money(line["quantity"] * line["unit_price_excluding_tax"])
        allowance = base - line["total_excluding_tax"]
        if allowance:
            item["BG-27"] = [
                {
                    "amount": str(allowance),
                    "base_amount": str(base),
                    "rate": str(line["discount_percent"]),
                    "reason": "Remise commerciale",
                }
            ]
        data["BG-25"].append(item)
    return data


def no_resources(url, *args, **kwargs):
    raise ValueError(
        "Les ressources externes et les fichiers arbitraires sont interdits dans les PDF."
    )


@transaction.atomic
def generate_document(document):
    from facturx import generate_xml, generate_from_binary
    from lxml import etree
    from weasyprint import HTML
    from pypdf import PdfReader

    document = Document.objects.select_for_update().get(pk=document.pk)
    from core.models import PdpInvoice

    imported = PdpInvoice.objects.filter(
        document_uid=document.document_uid, imported=True
    ).first()
    if imported:
        original = DocumentFile.objects.get(
            document_uid=document.document_uid, file_uid=imported.original_file_uid
        )
        read_verified(original)
        return original
    kind = "pdf" if document.type == "quote" else "facturx"
    existing = document.files.filter(kind=kind).first()
    if document.direction == "incoming":
        original = document.files.filter(
            kind__in=["facturx", "pdf", "source", "attachment"]
        ).first()
        if not original:
            raise BusinessError("Ajoutez le document fournisseur original.", 409)
        read_verified(original)
        return original
    if document.status not in {"draft", "simulated"}:
        if not existing:
            raise BusinessError(
                "Le document émis n'a plus de fichier original. Restaurez une sauvegarde.",
                409,
            )
        read_verified(existing)
        return existing
    computed = calculate(list(document.lines.values()))
    if (
        computed["net"],
        computed["vat"],
        computed["gross"] + document.rounding_amount,
    ) != (
        document.total_excluding_tax,
        document.total_vat,
        document.total_including_tax,
    ):
        raise BusinessError(
            "Les totaux historiques ne correspondent pas aux lignes. Vérifiez le brouillon.",
            409,
        )
    xml = None
    validation = None
    if kind == "facturx":
        try:
            xml = generate_xml(
                xml_data(document, computed),
                level="en16931",
                check_xsd=False,
                check_schematron=False,
            )
            # factur-x 7.7 emits xsi:nil on an empty optional delivery group. CII
            # requires an empty group, not a fabricated delivery date or nil marker.
            root = etree.fromstring(
                xml, etree.XMLParser(resolve_entities=False, no_network=True)
            )
            for node in root.iter():
                node.attrib.pop("{http://www.w3.org/2001/XMLSchema-instance}nil", None)
            xml = etree.tostring(root, encoding="UTF-8", xml_declaration=True)
            validation = validate_xml(xml)
        except (ValueError, KeyError, TypeError) as exc:
            raise BusinessError(
                "Données insuffisantes pour Factur-X : " + str(exc)
            ) from exc
    source_number = (
        Document.objects.filter(
            company_uid=document.company_uid, document_uid=document.source_invoice_uid
        )
        .values_list("number", flat=True)
        .first()
        if document.source_invoice_uid
        else ""
    )
    from .branding import logo_bytes, palette

    branding = document.seller.get("branding", {})
    logo = logo_bytes(document.company_uid, branding)
    logo_uri = "zefacture-logo:" + hashlib.sha256(logo).hexdigest() if logo else ""

    def document_resources(url, *args, **kwargs):
        if logo and url == logo_uri:
            from email.message import Message
            from weasyprint.urls import URLFetcherResponse

            headers = Message()
            headers["Content-Type"] = "image/png"
            return URLFetcherResponse(url, BytesIO(logo), headers, 200)
        return no_resources(url, *args, **kwargs)

    html = render_to_string(
        "core/document_pdf.html",
        {
            "document": document,
            "lines": computed["lines"],
            "taxes": computed["taxes"],
            "is_quote": kind == "pdf",
            "source_invoice_number": source_number,
            "branding": branding,
            "brand_color": palette(branding.get("accent_color", "#176568"))["primary"],
            "logo_uri": logo_uri,
        },
    )
    pdf = HTML(string=html, url_fetcher=document_resources).write_pdf(
        pdf_variant="pdf/a-3b"
    )
    if xml:
        pdf = generate_from_binary(
            pdf,
            xml,
            flavor="factur-x",
            level="en16931",
            check_xsd=True,
            check_schematron=False,
            afrelationship="alternative",
            lang="fr-FR",
        )
        if PdfReader(BytesIO(pdf)).attachments.get("factur-x.xml") != [xml]:
            raise BusinessError("Contrôle XML embarqué échoué.", 500)
    folder = f"invoices/{document.company_uid}/{document.document_uid}/{uid()}"
    file = DocumentFile.objects.create(
        document_uid=document.document_uid,
        kind=kind,
        storage_path=put(folder + "/document.pdf", pdf),
        mime_type="application/pdf",
        sha256=hashlib.sha256(pdf).hexdigest(),
        file_size=len(pdf),
        generated_at=stamp(),
    )
    if xml:
        DocumentFile.objects.create(
            document_uid=document.document_uid,
            kind="xml",
            storage_path=put(folder + "/factur-x.xml", xml),
            mime_type="application/xml",
            sha256=hashlib.sha256(xml).hexdigest(),
            file_size=len(xml),
            generated_at=stamp(),
        )
        report = json.dumps(validation, ensure_ascii=False, indent=2).encode()
        DocumentFile.objects.create(
            document_uid=document.document_uid,
            kind="validation",
            storage_path=put(folder + "/validation.json", report),
            mime_type="application/json",
            sha256=hashlib.sha256(report).hexdigest(),
            file_size=len(report),
            generated_at=stamp(),
        )
    return file


def download_extension(mime_type):
    return {
        "application/pdf": ".pdf",
        "application/xml": ".xml",
        "text/xml": ".xml",
        "application/json": ".json",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/tiff": ".tiff",
        "image/bmp": ".bmp",
    }.get(mime_type, ".bin")


def secret_box():
    from nacl.secret import SecretBox

    target = settings.DATA_DIR / "app.key"
    if not target.exists():
        from nacl.utils import random

        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Publish the complete key atomically; concurrent workers must never read
        # an empty key or replace the winner's existing (possibly imported) key.
        with tempfile.NamedTemporaryFile(dir=target.parent) as file:
            file.write(base64.b64encode(random(32)))
            file.flush()
            os.fsync(file.fileno())
            try:
                os.link(file.name, target)
            except FileExistsError:
                pass
    return SecretBox(base64.b64decode(target.read_bytes().strip(), validate=True))


def encrypt(value):
    return base64.b64encode(secret_box().encrypt(value.encode())).decode()


def decrypt(value):
    if not value:
        return ""
    try:
        return secret_box().decrypt(base64.b64decode(value, validate=True)).decode()
    except Exception as exc:
        raise BusinessError(
            "Secret illisible : restaurez la clé app.key d'origine.", 409
        ) from exc
