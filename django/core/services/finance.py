import csv
import io
import unicodedata
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from django.db import transaction
from django.db.models import Q
from core.common import (
    BusinessError,
    money,
    decimal,
    iso_date,
    today,
    stamp,
    digest,
    json_text,
)
from core.models import (
    BankAccount,
    BankFeedEntry,
    BankTransaction,
    Document,
    DocumentLine,
    Forecast,
)
from .documents import event


@transaction.atomic
def reconcile(company, bank_transaction, document_uid, *, exact=False):
    feed_entry = (
        BankFeedEntry.objects.select_for_update()
        .filter(transaction=bank_transaction)
        .first()
    )
    if feed_entry and feed_entry.review:
        raise BusinessError("Vérifiez d’abord la correction bancaire.", 409)
    bank_transaction = BankTransaction.objects.select_for_update().get(
        pk=bank_transaction.pk, company_uid=company.company_uid
    )
    document = (
        Document.objects.select_for_update()
        .filter(company_uid=company.company_uid, document_uid=document_uid)
        .first()
    )
    if bank_transaction.status != "booked":
        raise BusinessError(
            "Seule une opération comptabilisée peut être rapprochée.", 409
        )
    if bank_transaction.document_uid:
        raise BusinessError("Transaction déjà rapprochée.", 409)
    expected = (
        "incoming" if document and document.direction == "outgoing" else "outgoing"
    )
    if (
        not document
        or document.type not in {"invoice", "progress_invoice"}
        or document.status in {"draft", "cancelled", "paid", "refused", "simulated"}
    ):
        raise BusinessError("Facture non rapprochable.", 409)
    if (
        bank_transaction.direction != expected
        or not 0 < bank_transaction.amount <= document.amount_due
        or exact
        and (
            bank_transaction.amount != document.amount_due
            or document.status not in {"sent", "received", "accepted"}
        )
    ):
        raise BusinessError("Sens incompatible ou paiement supérieur au solde.", 409)
    account = BankAccount.objects.filter(
        account_uid=bank_transaction.account_uid, company_uid=company.company_uid
    ).first()
    if document.currency_code != (account.currency_code if account else "EUR"):
        raise BusinessError("Les devises du compte et de la facture diffèrent.")
    if feed_entry:
        feed_entry.previous_invoice_status = document.status
        feed_entry.save(update_fields=["previous_invoice_status"])
    document.amount_due = money(document.amount_due - bank_transaction.amount)
    if not document.amount_due:
        document.status, document.paid_at = "paid", stamp()
    document.updated_at = stamp()
    document.save()
    bank_transaction.document_uid = document_uid
    bank_transaction.updated_at = stamp()
    bank_transaction.save()
    event(
        document,
        "bank_payment",
        "Paiement rapproché",
        {
            "amount": bank_transaction.amount,
            "remaining": document.amount_due,
            "transaction_uid": bank_transaction.transaction_uid,
        },
    )


@transaction.atomic
def create_transaction(company, data):
    amount = money(decimal(data.get("amount")))
    direction = data.get("direction")
    label = str(data.get("label", "")).strip()
    if amount <= 0 or direction not in {"incoming", "outgoing"} or not label:
        raise BusinessError("Montant positif, libellé et sens requis.")
    account = None
    if data.get("account_uid"):
        account = (
            BankAccount.objects.select_for_update()
            .filter(
                company_uid=company.company_uid,
                account_uid=data["account_uid"],
                status="active",
            )
            .first()
        )
        if not account:
            raise BusinessError("Compte bancaire introuvable.")
    item = BankTransaction.objects.create(
        company_uid=company.company_uid,
        account_uid=account.account_uid if account else None,
        transaction_date=iso_date(data.get("transaction_date") or today()),
        label=label,
        amount=amount,
        direction=direction,
        category=data.get("category") or "other",
        external_id=data.get("external_id") or None,
        import_hash=data.get("import_hash"),
        imported_at=stamp() if data.get("import_hash") else None,
    )
    if account and not hasattr(account, "bank_feed"):
        account.current_balance += amount if direction == "incoming" else -amount
        account.updated_at = stamp()
        account.save()
    if data.get("document_uid"):
        reconcile(company, item, data["document_uid"])
    return item


def header(value):
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", value.lower().strip())
        if not unicodedata.combining(c)
    ).replace(" ", "_")


@transaction.atomic
def import_csv(company, account_uid, raw):
    if len(raw) > 5_000_000:
        raise BusinessError("CSV limité à 5 Mo.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    try:
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=";,\t")
    except csv.Error:
        raise BusinessError("CSV illisible.")
    rows = csv.DictReader(io.StringIO(text), dialect=dialect)
    aliases = {
        "date": "transaction_date",
        "date_operation": "transaction_date",
        "libelle": "label",
        "description": "label",
        "montant": "amount",
        "credit": "credit",
        "debit": "debit",
        "reference": "external_id",
        "sens": "direction",
    }
    fields = {aliases.get(header(f), header(f)) for f in rows.fieldnames or []}
    if not {"transaction_date", "label"}.issubset(fields) or not fields.intersection(
        {"amount", "credit", "debit"}
    ):
        raise BusinessError(
            "Colonnes requises : date, libellé, montant (ou débit/crédit)."
        )
    result = {"imported": 0, "skipped": 0, "rejected": 0}
    occurrences = defaultdict(int)
    linked_rows_seen = set()
    # Account lock serializes concurrent imports before deduplication.
    if (
        not BankAccount.objects.select_for_update()
        .filter(
            company_uid=company.company_uid, account_uid=account_uid, status="active"
        )
        .exists()
    ):
        raise BusinessError("Compte bancaire requis.")
    for index, row in enumerate(rows):
        if index >= 1000:
            raise BusinessError("Maximum 1 000 lignes par import.")
        data = {
            aliases.get(header(k), header(k)): v.strip()
            for k, v in row.items()
            if k and v is not None
        }
        try:
            value = data.get("transaction_date", "")
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                try:
                    data["transaction_date"] = (
                        datetime.strptime(value, fmt).date().isoformat()
                    )
                    break
                except ValueError:
                    pass
            iso_date(data["transaction_date"])

            def num(key):
                return decimal(
                    data.get(key, "")
                    .replace(" ", "")
                    .replace("\u00a0", "")
                    .replace("€", "")
                )

            amount = (
                num("amount") if data.get("amount") else num("credit") - num("debit")
            )
            direction = data.get("direction") or (
                "incoming" if amount >= 0 else "outgoing"
            )
            data.update(
                amount=abs(amount), direction=direction, account_uid=account_uid
            )
            signature = json_text(
                [
                    account_uid,
                    data["transaction_date"],
                    data.get("label"),
                    str(money(abs(amount))),
                    direction,
                    data.get("external_id") or "",
                ]
            )
            occurrences[signature] += 1
            data["import_hash"] = digest(signature + ":" + str(occurrences[signature]))
            existing_pk = (
                BankTransaction.objects.filter(
                    company_uid=company.company_uid, import_hash=data["import_hash"]
                )
                .values_list("pk", flat=True)
                .first()
            )
            if existing_pk:
                linked_rows_seen.add(existing_pk)
                result["skipped"] += 1
                continue
            matching = Q(label__iexact=data["label"])
            if data.get("external_id"):
                matching |= Q(bank_feed__bank_reference=data["external_id"])
            linked = (
                BankTransaction.objects.filter(
                    matching,
                    company_uid=company.company_uid,
                    account_uid=account_uid,
                    bank_feed__isnull=False,
                    transaction_date=data["transaction_date"],
                    amount=abs(amount),
                    direction=direction,
                    status="booked",
                )
                .exclude(pk__in=linked_rows_seen)
                .order_by("pk")
                .first()
            )
            if linked:
                linked_rows_seen.add(linked.pk)
                if not linked.import_hash:
                    linked.import_hash = data["import_hash"]
                    linked.save(update_fields=["import_hash"])
                result["skipped"] += 1
                continue
            create_transaction(company, data)
            result["imported"] += 1
        except BusinessError:
            result["rejected"] += 1
    return result


def csv_text(headers, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter=";", lineterminator="\r\n")

    def safe(value):
        text = str(value if value is not None else "")
        return (
            "'" + text
            if isinstance(value, str)
            and text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r"))
            else text
        )

    writer.writerow(headers)
    for row in rows:
        writer.writerow([safe(value) for value in row])
    return "\ufeff" + stream.getvalue()


def effective_documents(company):
    return (
        Document.objects.filter(company_uid=company.company_uid)
        .exclude(type="quote")
        .exclude(status__in=["draft", "cancelled", "refused", "simulated"])
    )


def vat_rows(company, start, end):
    rows = []
    for document in effective_documents(company).filter(
        issue_date__gte=start, issue_date__lte=end
    ):
        sign = Decimal(-1) if document.type == "credit_note" else Decimal(1)
        # Preserve the legacy management statement: period of invoice issuance.
        # It is not an automatic tax return or a cash-basis VAT calculation.
        ratio = Decimal(1)
        groups = defaultdict(lambda: [Decimal(0), Decimal(0)])
        for line in document.lines:
            groups[line.vat_rate][0] += line.total_excluding_tax
            groups[line.vat_rate][1] += line.total_vat
        for rate, (net, vat) in groups.items():
            rows.append(
                [
                    document.issue_date,
                    document.number,
                    document.direction,
                    document.currency_code,
                    rate,
                    money(net * sign * ratio),
                    money(vat * sign * ratio),
                ]
            )
    return rows
