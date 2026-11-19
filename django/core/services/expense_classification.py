"""Expense fields that can be established from an itemised receipt."""

import re

from core.payment_details import PaidBy
from .receipt_extraction import simplify


CATEGORIES = {
    "Repas": r"repas|restaurant|restauration|brasserie|bistrot?|bordrestaurant|cafe|sandwich|pizza|burger|coca[ -]?cola|speisen|lunch|dinner",
    "Transport": r"taxi|vtc|uber|trajet|billet de train|fahrkarte|fahrschein|train ticket|boarding pass|billet d.avion|ride fare",
    "Hébergement": r"hotel|hebergement|nuitee?s?|ubernachtung|overnight stay|room night",
    "Carburant et recharge": r"carburant|gazole|diesel|essence sans plomb|super e10|recharge electrique|kraftstoff",
    "Stationnement et péages": r"parking|stationnement|peage|parkhaus|parkgebuhr|toll fee",
    "Fournitures": r"papeterie|fournitures de bureau|office supplies|buromaterial",
    "Télécommunications": r"forfait mobile|abonnement internet|telecommunications|mobile subscription",
    "Logiciels et abonnements": r"licence logicielle|abonnement logiciel|software subscription",
    "Formation": r"frais de formation|formation professionnelle|training course",
}
CLASSIFIERS = {
    label: re.compile(r"\b(?:" + pattern + r")\b")
    for label, pattern in CATEGORIES.items()
}


def clean_classification(raw):
    if not isinstance(raw, dict):
        return {}
    result = {}
    if isinstance(raw.get("category"), str) and raw["category"] in CATEGORIES:
        result["category"] = raw["category"]
    if raw.get("paid_by") in PaidBy.values:
        result["paid_by"] = raw["paid_by"]
    for key in ("description", "purpose"):
        value = raw.get(key)
        if isinstance(value, str):
            value = " ".join(value.split())
            if 0 < len(value) <= 500 and value.isprintable():
                result[key] = value
    return result


def classification(item, parsed):
    result = clean_classification(parsed.get("expense"))
    if not result.get("category"):
        lines = parsed.get("lines")
        descriptions = (
            [
                str(line.get("description", ""))[:500]
                for line in lines[:100]
                if isinstance(line, dict)
            ]
            if isinstance(lines, list)
            else []
        )
        # Item descriptions distinguish e.g. a sandwich bought on a train from a train ticket.
        supplier = parsed.get("supplier")
        merchant = item.merchant_name or (
            supplier.get("legal_name") if isinstance(supplier, dict) else ""
        )
        for source in (
            "\n".join(descriptions),
            (item.transcription_text or "")[:12000],
            str(merchant or ""),
        ):
            text = simplify(source)
            matches = [
                label for label, pattern in CLASSIFIERS.items() if pattern.search(text)
            ]
            if matches:
                if len(matches) == 1:
                    result["category"] = matches[0]
                break
    return result
