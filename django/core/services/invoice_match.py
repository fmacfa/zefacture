"""Stable comparison keys used for invoice imports and the initial index migration."""

import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation


def name_key(value):
    return " ".join(value.split()).casefold() if isinstance(value, str) else ""


def invoice_keys_v1(kind, data):
    """Return the original reference and, with complete evidence, a second comparison key."""
    if not isinstance(data, dict):
        return "", ""
    invoice = data.get("invoice")
    if not isinstance(invoice, dict):
        return "", ""
    number = invoice.get("number")
    reference = (
        str(number).strip().casefold()[:140] if isinstance(number, (str, int)) else ""
    )
    party = (
        data.get("buyer") or data.get("customer")
        if kind == "sales_invoice"
        else data.get("supplier") or data.get("seller")
    )
    totals = data.get("totals")
    if not isinstance(party, dict) or not isinstance(totals, dict):
        return reference, ""
    name = name_key(party.get("legal_name") or party.get("name"))
    issued = invoice.get("issue_date")
    currency = invoice.get("currency_code")
    try:
        if (
            not name
            or not isinstance(issued, str)
            or date.fromisoformat(issued).isoformat() != issued
        ):
            return reference, ""
        if (
            not isinstance(currency, str)
            or len(currency.strip()) != 3
            or not currency.strip().isascii()
            or not currency.strip().isalpha()
        ):
            return reference, ""
        amount = Decimal(str(totals.get("total_including_tax")).replace(",", "."))
        if (
            not amount.is_finite()
            or abs(amount) > 100_000_000
            or amount != amount.quantize(Decimal("0.01"))
        ):
            return reference, ""
    except (InvalidOperation, ValueError):
        return reference, ""
    fields = [
        name,
        issued,
        currency.strip().upper(),
        format(amount or Decimal(0), ".2f"),
    ]
    return reference, hashlib.sha256(
        json.dumps(fields, ensure_ascii=False).encode()
    ).hexdigest()
