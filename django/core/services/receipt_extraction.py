"""Conservative suggestions from OCR. Saving a receipt remains a user action."""

import re
import unicodedata
from datetime import date
from decimal import Decimal

from core.common import BusinessError, decimal, iso_date, unpack
from core.payment_details import PaymentMethod, payment_fields

EXPENSE_KINDS = {"expense_note", "expense", "expense_receipt", "receipt"}
CURRENCIES = {"EUR", "USD", "GBP", "CHF", "CAD", "JPY", "SEK", "NOK", "DKK", "PLN"}

DATE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}[./-]\d{4})(?!\d)")
AMOUNT = r"\d{1,3}(?: \d{3})+[,.]\d{2}|\d{1,9}[,.]\d{2}"
CURRENCY_UNIT = r"(?:€|£|euros?|" + "|".join(sorted(CURRENCIES)) + ")"
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


def explicit_amount(tail):
    match = AMOUNT_ONLY.fullmatch(tail)
    return amount_string(match[1].replace(" ", "")) if match else ""


def local_suggestion(text):
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
                explicit_amount(tail)
            )
        match = VAT.match(line)
        if match:
            taxes.append(explicit_amount(match[1]))

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


def expense_suggestion(item):
    local = local_suggestion(item.transcription_text)
    parsed = unpack(item.extraction_json)
    if not isinstance(parsed, dict):
        parsed = {}

    def facts(group):
        supplied = parsed.get(group)
        return {
            field: (supplied.get(field) if isinstance(supplied, dict) else "") or value
            for field, value in local[group].items()
        }

    supplier, invoice, totals = (
        facts(group) for group in ("supplier", "invoice", "totals")
    )
    currency = invoice["currency_code"]
    return {
        "merchant": str(supplier["legal_name"] or "")[:255],
        "date": as_date(str(invoice["issue_date"] or "")),
        "currency": currency
        if isinstance(currency, str) and currency in CURRENCIES
        else "",
        "amount": amount_string(totals["total_including_tax"]),
        "vat": amount_string(totals["total_vat"]),
    }


def suggested_payment(text):
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
    return next(iter(found)).label if len(found) == 1 else ""


def expense_values(data, require_complete=False):
    def text(name, limit):
        return data.get(name, "").replace("\0", "").strip()[:limit]

    merchant = text("merchant_name", 255)
    when = iso_date(data.get("expense_date"), optional=True)
    raw_amount = data.get("expense_amount")
    amount = decimal(raw_amount) if raw_amount not in (None, "") else None
    currency = data.get("expense_currency", "").strip().upper()
    if amount is not None and amount < Decimal(0):
        raise BusinessError("Le montant du justificatif doit être positif ou nul.")
    if currency and currency not in CURRENCIES:
        raise BusinessError("Devise non prise en charge.")
    if require_complete and (
        not merchant or not when or amount is None or not currency
    ):
        raise BusinessError(
            "Renseignez le commerçant, la date, le montant TTC et la devise."
        )
    return {
        "merchant_name": merchant,
        "expense_date": when,
        "expense_amount": amount,
        "expense_currency": currency,
        "expense_category": text("expense_category", 255),
        **payment_fields(data),
    }
