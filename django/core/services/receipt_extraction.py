"""Conservative suggestions from OCR. Saving a receipt remains a user action."""

import re
import unicodedata
from datetime import date
from decimal import Decimal
from iso4217 import Currency

from core.common import BusinessError, decimal, iso_date, unpack
from core.payment_details import PaymentMethod, payment_fields

EXPENSE_KINDS = {"expense_note", "expense", "expense_receipt", "receipt"}
CURRENCIES = {"EUR", "USD", "GBP", "CHF", "CAD", "JPY", "SEK", "NOK", "DKK", "PLN"}
RECEIPT_CURRENCIES = frozenset(Currency.__members__) - {"XXX", "XTS"}

DATE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}[./-]\d{4})(?!\d)")
AMOUNT = r"\d{1,3}(?: \d{3})+[,.]\d{2}|\d{1,9}[,.]\d{2}"
CURRENCY_UNIT = r"(?:€|£|euros?|" + "|".join(sorted(CURRENCIES)) + ")"
RECEIPT_UNIT = r"(?:€|£|euros?|k[cč]|" + "|".join(sorted(RECEIPT_CURRENCIES)) + ")"
RECEIPT_AMOUNT = re.compile(
    rf"\s*(?:{RECEIPT_UNIT}\s*)?(\d+(?: \d{{3}})*(?:[.,]\d{{1,4}})?)(?:\s*{RECEIPT_UNIT})?\s*$", re.I
)
AMOUNT_ONLY = re.compile(
    rf"\s*(?:{CURRENCY_UNIT}\s*)?({AMOUNT})(?:\s*{CURRENCY_UNIT})?\s*$",
    re.I,
)
TOTAL = re.compile(
    r"^(total\s+(?:t\.?t\.?c\.?|toutes taxes comprises|a payer)|net\s+(?:a payer|paye)"
    r"|montant\s+(?:total|paye)|total)(?!\w)\s*[:=]?\s*(.*)$",
    re.I,
)
VAT = re.compile(r"^(?:total tva|tva totale|montant tva)\s*[:=]?\s*(.*)$", re.I)
HEADING = re.compile(
    r"^(?:ticket|recu|note|facture|date|total|tva|siret|siren|rcs|carte|caisse|"
    r"paiement|montant|merci|bienvenue|tel|adresse|www|https?)(?!\w)"
)


def simplify(text):
    return "".join(
        c
        for c in unicodedata.normalize("NFD", text.lower())
        if not unicodedata.combining(c)
    )


def as_date(value):
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value).isoformat()
        day, month, year = map(int, re.split(r"[./-]", value))
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def only(values):
    values = {value for value in values if value}
    return values.pop() if len(values) == 1 else ""


def amount_string(value):
    try:
        amount = decimal(value)
    except BusinessError:
        return ""
    return (
        format(amount.normalize(), "f")
        if value not in ("", None) and amount >= 0
        else ""
    )


def explicit_amount(tail, expense=False):
    match = (RECEIPT_AMOUNT if expense else AMOUNT_ONLY).fullmatch(tail)
    return amount_string(match[1].replace(" ", "")) if match else ""


def local_suggestion(text, *, expense=False):
    # Most relevant data is in the header and footer. Bound the work for long PDFs.
    text = text or ""
    if len(text) > 36000:
        text = text[:18000] + "\n" + text[-18000:]
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    labels = [simplify(line) for line in lines]
    dates, labelled_dates = [], []
    firm_totals, generic_totals, taxes = [], [], []
    for index, line in enumerate(labels):
        date_values = [as_date(match[1]) for match in DATE.finditer(line)]
        dates.extend(date_values)
        if re.match(r"^date\s+(?:d['’]emission|de facture|d['’]achat)\b", line):
            labelled_dates.extend(date_values)
        match = TOTAL.match(line)
        if match:
            label, tail = match.groups()
            if not tail and index + 1 < len(labels):
                tail = labels[index + 1]
            (generic_totals if label == "total" else firm_totals).append(
                explicit_amount(tail, expense)
            )
        match = VAT.match(line)
        if match:
            taxes.append(explicit_amount(match[1], expense))

    merchant = next(
        (
            line
            for line, label in zip(lines[:6], labels[:6])
            if len(line) <= 120
            and re.match(r"^[^\W\d_]", line)
            and sum(char.isalpha() for char in line) >= 3
            and not HEADING.match(label)
            and not DATE.search(line)
            and not re.search(r"\d{5}|[@%]", line)
        ),
        "",
    )
    upper = text.upper()
    currencies = {code for code in CURRENCIES if re.search(rf"\b{code}\b", upper)}
    if expense:
        # Common words such as TRY are also ISO codes. Require a nearby price.
        for before, after in re.findall(r"\b([A-Z]{3})[ \t]*\d|\d[ \t]*([A-Z]{3})\b", upper):
            if (code := before or after) in RECEIPT_CURRENCIES:
                currencies.add(code)
        if re.search(r"\bKČ\b", upper):
            currencies.add("CZK")
    if "€" in text or re.search(r"\bEUROS?\b", upper):
        currencies.add("EUR")
    if "£" in text:
        currencies.add("GBP")
    total = only(firm_totals) if any(firm_totals) else only(generic_totals)
    return {
        "supplier": {"legal_name": merchant},
        "invoice": {
            "issue_date": only(labelled_dates) if any(labelled_dates) else only(dates),
            "currency_code": only(currencies),
        },
        "totals": {
            "total_including_tax": total,
            "total_vat": only(taxes),
        },
    }


def expense_suggestion(item, parsed=None):
    local = local_suggestion(item.transcription_text, expense=True)
    if parsed is None:
        parsed = unpack(item.extraction_json)
    if not isinstance(parsed, dict):
        parsed = {}

    def facts(group):
        supplied = parsed.get(group)
        return {
            field: (supplied.get(field) if isinstance(supplied, dict) else "")
            or ("" if f"{group}.{field}" in reviewed else value)
            for field, value in local[group].items()
        }

    reviewed = parsed.get("_reviewed") or []
    supplier, invoice, totals = (
        facts(group) for group in ("supplier", "invoice", "totals")
    )
    currency = invoice["currency_code"]
    return {
        "merchant": str(supplier["legal_name"] or "")[:255],
        "date": as_date(str(invoice["issue_date"] or "")),
        "currency": currency
        if isinstance(currency, str) and currency in RECEIPT_CURRENCIES
        else "",
        "amount": amount_string(totals["total_including_tax"]),
        "vat": amount_string(totals["total_vat"]),
    }


def suggested_payment_method(text, extracted=None):
    """Only suggest an explicitly stated, unambiguous form of settlement."""
    methods = {
        "carte bancaire": PaymentMethod.CARD,
        "carte": PaymentMethod.CARD,
        "cb": PaymentMethod.CARD,
        "visa": PaymentMethod.CARD,
        "mastercard": PaymentMethod.CARD,
        "credit card": PaymentMethod.CARD,
        "especes": PaymentMethod.CASH,
        "cash": PaymentMethod.CASH,
        "virement": PaymentMethod.TRANSFER,
        "virement bancaire": PaymentMethod.TRANSFER,
        "prelevement": PaymentMethod.DIRECT_DEBIT,
        "prelevement sepa": PaymentMethod.DIRECT_DEBIT,
        "cheque": PaymentMethod.CHEQUE,
        "paypal": PaymentMethod.WALLET,
    }
    found = set()
    for line in (text or "").splitlines():
        if len(line) > 200:
            continue
        line = " ".join(simplify(line).split())
        match = re.fullmatch(
            r"(?:(?:mode de paiement|moyen de paiement|reglement|paiement|paye(?:e)? (?:par|en)|regle(?:e)? (?:par|en)|payment method)\s*[:=-]?\s*)?([a-z ]+?)(?:\s*[:=-]?\s*\d+(?:[,.]\d{2})?\s*(?:eur|€)?)?",
            line,
        )
        if match and (method := methods.get(match[1].strip())):
            found.add(method)
    payment = (extracted or {}).get("payment")
    method = payment.get("method") if isinstance(payment, dict) else None
    if method in PaymentMethod.values:
        found.add(PaymentMethod(method))
    return next(iter(found)) if len(found) == 1 else None


def suggested_payment(text):
    method = suggested_payment_method(text)
    return method.label if method else ""


def expense_values(data, require_complete=False):
    def text(name, limit):
        return data.get(name, "").replace("\0", "").strip()[:limit]

    merchant = text("merchant_name", 255)
    when = iso_date(data.get("expense_date"), optional=True)
    raw_amount = data.get("expense_amount")
    if raw_amount not in (None, "") and not re.fullmatch(r"-?\d+(?:[.,]\d{1,4})?", str(raw_amount).strip()):
        raise BusinessError("Indiquez le montant du justificatif avec au plus quatre décimales.")
    amount = decimal(raw_amount) if raw_amount not in (None, "") else None
    currency = data.get("expense_currency", "").strip().upper()
    if amount is not None and amount < Decimal(0):
        raise BusinessError("Le montant du justificatif doit être positif ou nul.")
    if currency and currency not in RECEIPT_CURRENCIES:
        raise BusinessError("Devise non prise en charge.")
    if require_complete and (
        not merchant or not when or amount is None or not currency
    ):
        raise BusinessError(
            "Renseignez le commerçant, la date, le montant TTC et la devise."
        )
    extra = {}
    if "expense_paid_eur" in data:
        value = str(data["expense_paid_eur"]).strip() if data["expense_paid_eur"] is not None else ""
        if value and not re.fullmatch(r"\d+(?:[.,]\d{1,2})?", value):
            raise BusinessError("Indiquez un montant payé en euros positif ou nul, au centime.")
        extra["expense_paid_eur"] = decimal(value) if value else None
    if "expense_payer_name" in data:
        payer = (data.get("expense_payer_name") or "").replace("\0", "").strip()
        if len(payer) > 180:
            raise BusinessError("Le nom de la personne qui a avancé les fonds est limité à 180 caractères.")
        extra["expense_payer_name"] = payer
    return {
        **extra,
        "merchant_name": merchant,
        "expense_date": when,
        "expense_amount": amount,
        "expense_currency": currency,
        **({"expense_category": text("expense_category", 255)} if "expense_category" in data else {}),
        **payment_fields(data),
    }
