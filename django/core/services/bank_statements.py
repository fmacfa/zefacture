"""Read bank statements and merge their rows into the account's existing ledger."""

import csv
import hashlib
import io
import re
import unicodedata
from collections import Counter
from datetime import datetime
from html.parser import HTMLParser

from django.db import transaction
from django.db.models import Q

from core.common import BusinessError, decimal, digest, iso_date, json_text, money
from core.models import BankAccount, BankTransaction, StatementMatch
from core.numbers import current_style

MAX_BYTES = 5_000_000
MAX_ROWS = 1000


def header(value):
    folded = unicodedata.normalize("NFKD", value.lower().strip())
    return re.sub(
        r"[^a-z0-9]+", "_", "".join(c for c in folded if not unicodedata.combining(c))
    ).strip("_")


def bank_date(value):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    raise BusinessError("Date bancaire invalide.")


def bank_number(value):
    value = str(value or "").replace("€", "").strip()
    if value and current_style().customized:
        try:
            value = current_style().parse(value)
        except ValueError:
            if not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", value):
                raise BusinessError("Format numérique invalide.")
    return decimal("".join(value.split()))


def decode(raw):
    try:
        return raw.decode(
            "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        )
    except UnicodeDecodeError:
        try:
            return raw.decode("cp1252")
        except UnicodeDecodeError as exc:
            raise BusinessError("Encodage du relevé illisible.") from exc


def csv_amount(row):
    if row.get("amount"):
        return bank_number(row["amount"])
    debit, credit = bank_number(row.get("debit")), bank_number(row.get("credit"))
    if debit and credit:
        raise BusinessError("Une même ligne contient un débit et un crédit.")
    return -abs(debit) if debit else credit


def csv_rows(raw):
    data = decode(raw)
    try:
        dialect = csv.Sniffer().sniff(data[:4000], delimiters=";,\t")
    except csv.Error as exc:
        raise BusinessError("Le séparateur du relevé CSV est illisible.") from exc
    reader = csv.DictReader(io.StringIO(data), dialect=dialect)
    aliases = {
        "date": "date",
        "date_operation": "date",
        "date_d_operation": "date",
        "date_comptable": "date",
        "transaction_date": "date",
        "libelle": "label",
        "libelle_operation": "label",
        "description": "label",
        "montant": "amount",
        "reference": "external_id",
        "sens": "direction",
    }
    try:
        reader.fieldnames = [
            aliases.get(header(f), header(f)) for f in reader.fieldnames or []
        ]
        if not {"date", "label"} <= set(reader.fieldnames) or not set(
            reader.fieldnames
        ) & {"amount", "debit", "credit"}:
            raise BusinessError(
                "Le CSV doit contenir la date, le libellé et le montant ou les colonnes débit et crédit."
            )
        for row in reader:
            try:
                amount = csv_amount(row)
                direction = header(row.get("direction") or "")
                direction = {
                    "debit": "outgoing",
                    "decaissement": "outgoing",
                    "credit": "incoming",
                    "encaissement": "incoming",
                }.get(direction, direction)
                if amount < 0 and direction == "incoming":
                    raise BusinessError("Le sens contredit le montant du relevé.")
                yield {
                    "transaction_date": bank_date((row.get("date") or "").strip()),
                    "label": (row.get("label") or "").strip(),
                    "amount": abs(amount),
                    "direction": direction
                    or ("incoming" if amount >= 0 else "outgoing"),
                    "external_id": (row.get("external_id") or "").strip(),
                    "category": row.get("category") or row.get("categorie") or "other",
                }
            except BusinessError:
                yield None
    except csv.Error as exc:
        raise BusinessError("Le tableau CSV est illisible.") from exc


class OFXStatement(HTMLParser):
    """Read posted bank and card statements, including OFX 1's unclosed scalar tags."""

    sections = {"stmtrs", "ccstmtrs"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.section = self.in_list = False
        self.count = 0
        self.meta = {}
        self.rows = []
        self.row = self.field = None

    def handle_starttag(self, tag, attrs):
        self.field = None
        if tag in self.sections:
            self.count += 1
            self.section = True
        elif self.section and tag == "banktranlist":
            self.in_list = True
        elif self.in_list and tag == "stmttrn":
            if self.row is not None or len(self.rows) >= MAX_ROWS:
                raise BusinessError(
                    "Liste OFX invalide ou supérieure à 1 000 opérations."
                )
            self.row = {}
        elif self.section and tag in ("curdef", "acctid") and self.row is None:
            self.field = tag
        elif self.row is not None and tag in (
            "dtposted",
            "trnamt",
            "fitid",
            "name",
            "memo",
            "correctfitid",
            "correctaction",
        ):
            self.field = tag

    def handle_endtag(self, tag):
        if tag == "stmttrn" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == "banktranlist":
            self.in_list = False
        elif tag in self.sections:
            self.section = False
        self.field = None

    def handle_data(self, data):
        if self.field:
            target = self.row if self.row is not None else self.meta
            target[self.field] = target.get(self.field, "") + data


def ofx_rows(raw, account):
    parser = OFXStatement()
    parser.feed(decode(raw))
    parser.close()
    if parser.count != 1 or parser.row is not None or parser.section or not parser.rows:
        raise BusinessError(
            "Choisissez un relevé OFX ou QFX complet contenant les opérations d’un seul compte."
        )
    if parser.meta.get("curdef", "").strip().upper() != account.currency_code:
        raise BusinessError("La devise du relevé ne correspond pas au compte.")
    source = "".join(parser.meta.get("acctid", "").upper().split())
    iban = "".join((account.iban or "").upper().split())
    if (
        iban
        and re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", source)
        and source != iban
    ):
        raise BusinessError("L’IBAN du relevé ne correspond pas au compte choisi.")
    for row in parser.rows:
        if "correctfitid" in row or "correctaction" in row:
            raise BusinessError(
                "Ce relevé contient une correction d’écriture OFX. Vérifiez-la auprès de votre banque avant l’import."
            )
        try:
            day = (
                datetime.strptime(row["dtposted"].strip()[:8], "%Y%m%d")
                .date()
                .isoformat()
            )
            amount = decimal(row["trnamt"].strip())
            reference = row["fitid"].strip()
            if not reference:
                raise ValueError()
            name, memo = (row.get(k, "").strip() for k in ("name", "memo"))
            label = name or memo
            if name and memo and memo.casefold() != name.casefold():
                label = f"{name} · {memo}"
            yield {
                "transaction_date": day,
                "amount": abs(amount),
                "direction": "outgoing" if amount < 0 else "incoming",
                "label": label,
                "external_id": reference,
            }
        except (KeyError, ValueError, BusinessError):
            yield None


def row_key(account, data, kind, file_digest, occurrences):
    if kind == "ofx":
        return digest(f"{account.account_uid}:ofx:{data['external_id']}")
    if kind == "pdf":
        slot = data.get("source_key", data["slot"])
        return digest(f"{account.account_uid}:pdf:{file_digest}:{slot}")
    signature = json_text(
        [
            account.account_uid,
            data["transaction_date"],
            data["label"],
            str(money(data["amount"])),
            data["direction"],
            data.get("external_id") or "",
        ]
    )
    occurrences[signature] += 1
    return digest(signature + ":" + str(occurrences[signature]))


def record_row(company, account, data, kind, file_digest, key, used):
    from .finance import create_transaction

    existing = BankTransaction.objects.filter(
        company_uid=company.company_uid, account_uid=account.account_uid
    )
    data["transaction_date"] = iso_date(data["transaction_date"])
    data["amount"] = money(data["amount"])
    if (
        not data["label"]
        or data["amount"] <= 0
        or data["direction"] not in ("incoming", "outgoing")
    ):
        raise BusinessError(
            "Une date, un libellé, un montant positif et un sens sont requis."
        )
    alias = (
        StatementMatch.objects.filter(account=account, row_key=key)
        .select_related("transaction")
        .first()
    )
    matched = alias.transaction if alias else existing.filter(import_hash=key).first()
    if matched:
        if (matched.amount, matched.direction, matched.transaction_date) != (
            data["amount"],
            data["direction"],
            data["transaction_date"],
        ):
            raise BusinessError("La banque a modifié une opération déjà importée.")
        used.add(matched.pk)
        return "skipped"
    candidates = existing.filter(
        transaction_date=data["transaction_date"],
        amount=data["amount"],
        direction=data["direction"],
        status="booked",
    ).exclude(pk__in=used)
    # Equal rows in one file can be distinct bank operations.
    candidates = candidates.exclude(statement_matches__file_digest=file_digest)
    ref = data.get("external_id")
    if ref:
        matched = (
            candidates.filter(Q(external_id=ref) | Q(bank_feed__bank_reference=ref))
            .order_by("pk")
            .first()
        )
    if not matched:
        if kind == "ofx":
            candidates = candidates.exclude(statement_matches__kind="ofx")
        elif kind == "csv":
            # Historical CSV hashes already disambiguate identical lines by occurrence.
            candidates = candidates.exclude(
                import_hash__isnull=False,
                bank_feed__isnull=True,
                statement_matches__isnull=True,
            )
            candidates = candidates.exclude(statement_matches__kind="csv")
        if ref:
            candidates = candidates.filter(
                Q(external_id__isnull=True)
                | Q(external_id="")
                | Q(external_id=ref)
                | Q(bank_feed__isnull=False)
            )
        matched = candidates.filter(label__iexact=data["label"]).order_by("pk").first()
    if matched:
        result = "skipped"
    else:
        matched = create_transaction(
            company, {**data, "account_uid": account.account_uid, "import_hash": key}
        )
        result = "imported"
    StatementMatch.objects.create(
        account=account,
        transaction=matched,
        kind=kind,
        row_key=key,
        file_digest=file_digest,
    )
    used.add(matched.pk)
    return result


@transaction.atomic
def import_rows(company, account_uid, kind, file_digest, rows, issues=None):
    account = (
        BankAccount.objects.select_for_update()
        .filter(
            company_uid=company.company_uid, account_uid=account_uid, status="active"
        )
        .first()
    )
    if not account:
        raise BusinessError("Choisissez un compte bancaire actif de l’entreprise.")
    if kind == "ofx":
        rows = ofx_rows(rows, account)
    result = {"imported": 0, "skipped": 0, "rejected": 0}
    used, occurrences = set(), Counter()
    for index, data in enumerate(rows):
        if index >= MAX_ROWS:
            raise BusinessError("Maximum 1 000 opérations par relevé.")
        try:
            if not data:
                raise BusinessError("Ligne bancaire illisible.")
            key = row_key(account, data, kind, file_digest, occurrences)
            result[
                record_row(company, account, data, kind, file_digest, key, used)
            ] += 1
        except BusinessError as exc:
            if issues is not None and len(issues) < 10:
                issues.append(f"Opération {index + 1} · {exc}")
            if kind == "pdf":
                raise
            result["rejected"] += 1
    return result


def import_file(company, account_uid, kind, raw, issues=None):
    if kind not in ("csv", "ofx") or len(raw) > MAX_BYTES:
        raise BusinessError("Choisissez un relevé CSV, OFX ou QFX de 5 Mo maximum.")
    rows = csv_rows(raw) if kind == "csv" else raw
    return import_rows(
        company, account_uid, kind, hashlib.sha256(raw).hexdigest(), rows, issues
    )
