from core.common import BusinessError


OUTGOING = {
    "salary": "Salaires",
    "social": "Cotisations sociales",
    "expense_reimbursement": "Remboursement de frais versé",
    "vat_payment": "Paiement de TVA",
    "cfe": "Cotisation foncière des entreprises (CFE)",
    "tax_payment": "Autres impôts et taxes",
    "bank_fee": "Frais bancaires",
    "refund_sent": "Autre remboursement versé",
    "other_expense": "Autre dépense",
}
INCOMING = {
    "vat_refund": "Remboursement de TVA reçu",
    "refund_received": "Autre remboursement reçu",
    "other_income": "Autre encaissement",
}
CATEGORIES = OUTGOING | INCOMING


def choices(direction):
    return OUTGOING if direction == "outgoing" else INCOMING


def checked(category, note, direction):
    category = (category or "").strip()
    note = (note or "").strip()
    if category and category not in choices(direction):
        raise BusinessError(
            "Choisissez une catégorie correspondant au sens de l’opération."
        )
    if len(note) > 500:
        raise BusinessError("Le motif est limité à 500 caractères.")
    return category, note


def category_label(item):
    if item.standalone_category:
        return CATEGORIES.get(item.standalone_category, item.standalone_category)
    return "" if item.category in (None, "", "other") else item.category
