import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.db import transaction
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.xml.functions import iterparse

from core.common import BusinessError, stamp
from core.models import EvidenceFile, ExpenseRegisterLine, ExpenseWorkbook, Ingestion
from . import evidence, expense_claims
from .receipt_extraction import EXPENSE_KINDS, RECEIPT_CURRENCIES, as_date, simplify

FIELDS = {
    "expense_date": ("Date", ("date", "date de depense")),
    "merchant_name": (
        "Commerçant",
        ("merchant", "commerce", "commercant", "fournisseur"),
    ),
    "expense_amount": (
        "TTC en devise d’origine",
        ("amount_total", "ttc", "ttc (€)", "montant ttc"),
    ),
    "expense_currency": ("Devise d’origine", ("currency", "devise")),
    "expense_paid_eur": (
        "Euros débités",
        ("paid_eur", "montant paye eur", "euros debites"),
    ),
    "expense_payer_name": (
        "Payeur identifié sur la pièce",
        ("payeur identifie", "payeur", "payer"),
    ),
    "file": ("Nom du justificatif", ("image_filename", "fichier", "justificatif")),
    "page": ("Page du justificatif", ("page",)),
    "key": ("Identifiant stable de la ligne", ("id", "identifiant")),
}
REQUIRED = ("expense_date", "merchant_name", "expense_amount")
NUMERIC = ("expense_amount", "expense_paid_eur")
MAX_ROWS, MAX_COLS = 2500, 100


def open_book(raw):
    if len(raw) > 16 * 1024 * 1024:
        raise BusinessError("Le classeur dépasse 16 Mo.")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if (
                len(infos) > 1200
                or sum(i.file_size for i in infos) > 32 * 1024 * 1024
                or any(i.filename.lower().endswith("vbaproject.bin") for i in infos)
            ):
                raise ValueError()
            # Reject oversized coordinates even when worksheet dimensions are absent or stale.
            for entry in infos:
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", entry.filename):
                    with archive.open(entry) as xml:
                        for _, node in iterparse(xml, events=("end",)):
                            if (
                                node.tag.rpartition("}")[2] == "row"
                                and int(node.get("r", 0)) > MAX_ROWS
                            ):
                                raise ValueError()
                            if node.tag.rpartition("}")[2] == "c":
                                location = node.get("r", "")
                                if re.match(
                                    r"[A-Z]{3,}|(?:C[W-Z]|[D-Z][A-Z])\d", location
                                ):
                                    raise ValueError()
                            node.clear()
        return load_workbook(
            io.BytesIO(raw), read_only=True, data_only=False, keep_links=False
        )
    except Exception as exc:
        raise BusinessError(
            "Choisissez un .xlsx lisible, sans macro, limité à 2 500 lignes et 100 colonnes par feuille."
        ) from exc


def upload(company, filename, raw):
    book = open_book(raw)
    book.close()
    return ExpenseWorkbook.objects.get_or_create(
        company_uid=company.company_uid,
        digest=hashlib.sha256(raw).hexdigest(),
        defaults={"contents": raw, "filename": Path(filename).name[:255]},
    )[0]


def read_sheet(workbook, sheet, header):
    book = open_book(bytes(workbook.contents))
    try:
        if sheet not in book.sheetnames or not 1 <= header <= MAX_ROWS:
            raise BusinessError(
                "Choisissez une feuille et une ligne d’en-tête valides."
            )
        ws = book[sheet]
        ws.reset_dimensions()
        all_rows = ws.iter_rows(min_row=header, max_col=MAX_COLS)
        first = next(all_rows, ())
        columns = [
            (i, get_column_letter(i), str(c.value or "")[:140])
            for i, c in enumerate(first, 1)
            if c.value is not None
        ]
        rows = []
        for n, cells in enumerate(all_rows, header + 1):
            if n > MAX_ROWS:
                raise BusinessError("La feuille dépasse 2 500 lignes.")
            row = {
                i: (c.value, c.data_type)
                for i, c in enumerate(cells, 1)
                if c.value is not None
            }
            if row:
                rows.append((n, row))
        return columns, rows
    finally:
        book.close()


def mapping_fields(columns, mapping):
    result = []
    for key, (label, aliases) in FIELDS.items():
        suggested = next(
            (i for i, _, title in columns if simplify(title.strip()) in aliases), 0
        )
        result.append((key, label, mapping.get(key, suggested)))
    return result


def normalize(key, value):
    if key == "expense_date":
        result = (
            value.date().isoformat()
            if isinstance(value, datetime)
            else value.isoformat()
            if isinstance(value, date)
            else as_date(str(value).strip())
        )
        if not result:
            raise ValueError("Date illisible")
        return result
    text = str(value).strip()
    if key in NUMERIC:
        try:
            amount = Decimal(
                text.replace("\u202f", "").replace(" ", "").replace(",", ".")
            )
            if (
                not amount.is_finite()
                or amount < 0
                or amount >= 10**16
                or amount
                != amount.quantize(
                    Decimal(".01" if key == "expense_paid_eur" else ".0001")
                )
            ):
                raise InvalidOperation()
        except (ValueError, InvalidOperation):
            raise ValueError("Montant ou précision invalide")
        return format(amount.normalize(), "f")
    if key == "expense_currency":
        text = text.upper()
        if text not in RECEIPT_CURRENCIES:
            raise ValueError("Devise ISO inconnue")
    if key == "page" and (not text.isdigit() or not 1 <= int(text) <= 500):
        raise ValueError("Page invalide")
    if len(text) > (180 if key in ("key", "expense_payer_name") else 255):
        raise ValueError("Cellule trop longue")
    return text


def logical_values(cells, mapping, currency):
    values = {}
    for key, column in mapping.items():
        if column not in cells:
            continue
        value, kind = cells[column]
        if kind in ("f", "e"):
            if key in REQUIRED or key in ("key", "expense_currency"):
                raise ValueError(
                    "La colonne obligatoire contient une formule ou une erreur"
                )
            continue
        if str(value).strip():
            values[key] = normalize(key, value)
    if any(k not in values for k in REQUIRED):
        raise ValueError("Date, commerçant ou TTC manquant")
    if "expense_currency" not in values:
        if mapping.get("expense_currency") or not currency:
            raise ValueError("Devise manquante")
        values["expense_currency"] = normalize("expense_currency", currency)
    if mapping.get("key") and "key" not in values:
        raise ValueError("Identifiant de ligne manquant")
    if values.get("page") and not values.get("file"):
        raise ValueError("La page nécessite un nom de justificatif")
    return values


def local_value(receipt, key):
    old = getattr(receipt, key, None) if receipt else None
    if old is None or old == "":
        return ""
    return format(old.normalize(), "f") if isinstance(old, Decimal) else str(old)


def preview(company, workbook, register, sheet, header, mapping, currency):
    if not register or len(register) > 150 or any(not mapping.get(k) for k in REQUIRED):
        raise BusinessError(
            "Nommez le registre et associez la date, le commerçant et le TTC."
        )
    if workbook.company_uid != company.company_uid:
        raise BusinessError("Classeur introuvable.", 404)
    _, rows = read_sheet(workbook, sheet, header)
    existing = {
        entry.key: entry
        for entry in ExpenseRegisterLine.objects.filter(
            company_uid=company.company_uid, register=register, sheet=sheet
        ).select_related("receipt")
    }
    pool = list(
        Ingestion.objects.filter(
            company_uid=company.company_uid,
            source_kind__in=EXPENSE_KINDS,
            archived_at__isnull=True,
        )
        .only(
            "ingestion_uid",
            "expense_date",
            "merchant_name",
            "expense_amount",
            "expense_currency",
            "expense_payer_name",
            "expense_paid_eur",
            "original_filename",
            "storage_path",
            "company_uid",
            "document_uid",
            "reimbursement",
            "claim_line",
        )
        .select_related("reimbursement", "claim_line__claim")
    )
    names, facts, by_id = {}, {}, {}
    for item in pool:
        by_id[item.pk] = item
        names.setdefault(Path(item.original_filename).name.casefold(), []).append(item)
        facts.setdefault(
            (
                item.expense_date,
                simplify((item.merchant_name or "").strip()),
                local_value(item, "expense_amount"),
                item.expense_currency,
            ),
            [],
        ).append(item)
    sources = {}
    for uid, fn, pages in EvidenceFile.objects.filter(
        company_uid=company.company_uid
    ).values_list("file_uid", "filename", "pages"):
        sources.setdefault(fn.casefold(), []).append((uid, pages))
    result = []
    for n, cells in rows:
        row = {
            "number": n,
            "changes": [],
            "state": "À vérifier",
            "selectable": False,
            "proof": "",
        }
        result.append(row)
        try:
            vals = logical_values(cells, mapping, currency)
            key = ("id:" + vals["key"]) if vals.get("key") else f"row:{n}"
            if len(key) > 180:
                raise ValueError("Identifiant trop long")
            entry = existing.get(key)
            file = Path(vals.get("file", "")).name.casefold()
            same = facts.get(
                (
                    vals["expense_date"],
                    simplify(vals["merchant_name"]),
                    vals["expense_amount"],
                    vals["expense_currency"],
                ),
                [],
            )
            by_name = names.get(file, []) if file and not vals.get("page") else []
            choices = {r.pk: r for r in (same + by_name)}
            row.update(
                key=key,
                values=vals,
                label=vals["merchant_name"],
                day=vals["expense_date"],
                amount=vals["expense_amount"],
                currency=vals["expense_currency"],
                filename=vals.get("file", ""),
            )
            if entry:
                item = by_id.get(entry.receipt_id)
                if not item:
                    raise ValueError("La pièce associée est archivée")
                if choices and item.pk not in choices:
                    raise ValueError(
                        "Identité de ligne modifiée. Vérifiez l’identifiant stable et la version du registre."
                    )
            else:
                if len(choices) > 1:
                    raise ValueError(
                        "Plusieurs justificatifs possibles. Vérifiez le nom du fichier ou les champs."
                    )
                item = next(iter(choices.values()), None)
            matched = sources.get(file, [])
            if len(matched) > 1 or (
                matched and int(vals.get("page", 0)) > matched[0][1]
            ):
                raise ValueError("Nom de pièce ambigu ou page indisponible")
            if matched:
                row["proof"] = matched[0][0]
            previous = entry.source_values if entry else {}
            for field, value in vals.items():
                if field not in FIELDS or field in ("file", "page", "key"):
                    continue
                old = local_value(item, field)
                if old != value and (not entry or previous.get(field) != value):
                    row["changes"].append((field, FIELDS[field][0], old, value))
            for field in ("file", "page"):
                if entry and previous.get(field, "") != vals.get(field, ""):
                    row["changes"].append(
                        (
                            field,
                            FIELDS[field][0],
                            previous.get(field, ""),
                            vals.get(field, ""),
                        )
                    )
            row.update(item=item, entry=entry)
            proof_new = matched and (
                not item
                or not item.proofs.filter(
                    source__file_uid=row["proof"], page=int(vals.get("page", 0))
                ).exists()
            )
            if (
                item
                and row["changes"]
                and (
                    getattr(item, "document_uid", None)
                    or getattr(getattr(item, "reimbursement", None), "status", None)
                    == "paid"
                    or getattr(getattr(item, "claim_line", None), "claim", None)
                    and item.claim_line.claim.approved
                )
            ):
                raise ValueError(
                    "Justificatif validé ou remboursé. Révisez la fiche avant toute correction."
                )
            if entry and entry.source_values == vals and not proof_new:
                row["state"] = "Inchangée"
            else:
                row.update(
                    state="Mise à jour"
                    if entry
                    else "Relier à la fiche"
                    if item
                    else "Nouvelle dépense",
                    selectable=True,
                )
        except (ValueError, TypeError) as exc:
            row["error"] = str(exc)
    keys = Counter(r["key"] for r in result if r.get("key"))
    targets = Counter(r["item"].pk for r in result if r.get("item") and r["selectable"])
    new_facts = Counter(
        (r["day"], simplify(r["label"]), r["amount"], r["currency"])
        for r in result
        if r["selectable"] and not r.get("item")
    )
    for r in result:
        if (
            keys[r.get("key")] > 1
            or (r.get("item") and targets[r["item"].pk] > 1)
            or (
                r["selectable"]
                and not r.get("item")
                and new_facts[
                    (r["day"], simplify(r["label"]), r["amount"], r["currency"])
                ]
                > 1
            )
        ):
            r.update(
                selectable=False,
                state="À vérifier",
                error="Doublon potentiel dans cette feuille.",
            )
    return result


def fingerprint(rows):
    snapshot = [
        (
            r["number"],
            r["state"],
            r["selectable"],
            r.get("values"),
            r.get("changes"),
            r.get("proof"),
            r["item"].pk if r.get("item") else None,
        )
        for r in rows
    ]
    return hashlib.sha256(json.dumps(snapshot, ensure_ascii=False).encode()).hexdigest()


@transaction.atomic
def commit(company, actor, workbook, options, selected, expected):
    type(company).objects.select_for_update().get(pk=company.pk)
    # Lock existing receipts before recomputing differences against the signed preview.
    list(
        Ingestion.objects.filter(
            company_uid=company.company_uid,
            source_kind__in=EXPENSE_KINDS,
            archived_at__isnull=True,
        )
        .order_by("pk")
        .select_for_update()
        .values_list("pk", flat=True)
    )
    rows = preview(company, workbook, **options)
    if expected != fingerprint(rows):
        raise BusinessError(
            "Les données ont changé depuis l’aperçu. Actualisez les différences.", 409
        )
    allowed = {str(r["number"]) for r in rows if r["selectable"]}
    if not selected or not set(selected) <= allowed:
        raise BusinessError("Cochez au moins une ligne disponible dans cet aperçu.")
    for r in rows:
        if str(r["number"]) not in selected:
            continue
        vals = r["values"]
        item = r.get("item")
        if not item:
            item = Ingestion(
                company_uid=company.company_uid,
                uploaded_by_user_uid=actor,
                source_kind="expense_note",
                original_filename=vals.get("file")
                or f"{workbook.filename} · {options['sheet']} · {r['number']}",
                status="reviewed",
                expense_status="recorded",
                transcription_status="skipped",
                extraction_status="skipped",
            )
        if r["changes"] and item.pk:
            expense_claims.require_editable(item)
        for field, _, _, value in r["changes"]:
            if field not in ("file", "page"):
                setattr(item, field, value)
        if not item.pk or r["changes"]:
            item.updated_at = stamp()
            item.save()
        ExpenseRegisterLine.objects.update_or_create(
            company_uid=company.company_uid,
            register=options["register"],
            sheet=options["sheet"],
            key=r["key"],
            defaults={
                "workbook": workbook,
                "receipt": item,
                "row_number": r["number"],
                "source_values": vals,
            },
        )
        if r["proof"]:
            evidence.attach(
                item,
                EvidenceFile.objects.get(
                    company_uid=company.company_uid, file_uid=r["proof"]
                ),
                vals.get("page", 0),
            )
    return len(selected)
