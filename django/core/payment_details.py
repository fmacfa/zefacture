from django.db.models import TextChoices

from .common import BusinessError


class PaymentMethod(TextChoices):
    CARD = "card", "Carte bancaire"
    CASH = "cash", "Espèces"
    TRANSFER = "transfer", "Virement"
    DIRECT_DEBIT = "direct_debit", "Prélèvement"
    CHEQUE = "cheque", "Chèque"
    WALLET = "wallet", "Portefeuille électronique"
    MIXED = "mixed", "Plusieurs moyens"
    OTHER = "other", "Autre"


class PaidBy(TextChoices):
    COMPANY = "company", "Entreprise"
    PERSONAL = "personal", "Fonds personnels"
    THIRD_PARTY = "third_party", "Tiers"


def payment_fields(data, *, purchase=True):
    """Only update values supplied by the caller, including an explicit blank."""
    fields = {}
    for name, choices in (
        ("expense_payment_method", PaymentMethod),
        ("expense_paid_by", PaidBy),
    ):
        if name not in data or (not purchase and name == "expense_paid_by"):
            continue
        value = data[name]
        if value != "" and value not in choices.values:
            raise BusinessError(
                "Choisissez un moyen de paiement et une origine des fonds proposés."
            )
        fields[name] = value
    if purchase and "expense_description" in data:
        purpose = data["expense_description"].replace("\0", "").strip()
        if len(purpose) > 2000:
            raise BusinessError("Le motif est limité à 2 000 caractères.")
        fields["expense_description"] = purpose
    return fields
