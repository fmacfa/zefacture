from core.common import BusinessError, country, iso_date
from core.models import SaleOperation

ADDRESS_FIELDS = ("line1", "line2", "postal_code", "city", "country_code")


def short_text(value, limit, label):
    if value is not None and not isinstance(value, str):
        raise BusinessError(f"{label} invalide.")
    value = (value or "").strip()
    if len(value) > limit:
        raise BusinessError(f"{label} dépasse {limit} caractères.")
    return value


def address_from(data):
    address = data.get(
        "delivery_address", {key: data.get("delivery_" + key) for key in ADDRESS_FIELDS}
    )
    if not isinstance(address, dict):
        raise BusinessError("L’adresse de livraison doit être un objet.")
    address = {
        key: short_text(address.get(key), 180, "L’adresse de livraison")
        for key in ADDRESS_FIELDS
    }
    if not any(address.values()):
        return {}
    if not address["line1"] or not address["city"]:
        raise BusinessError("Complétez la rue et la ville de livraison.")
    if "delivery_country_code" in data:
        from django.core.exceptions import ValidationError
        from core.country_fields import country_code

        try:
            address["country_code"] = country_code(address["country_code"])
        except ValidationError as exc:
            raise BusinessError("Choisissez un pays de livraison valide.") from exc
    else:
        address["country_code"] = country(address["country_code"])
    return address


def update_details(document, data, customer):
    if "delivery_date" in data:
        document.delivery_date = iso_date(data["delivery_date"], optional=True)
    if "purchase_order" in data:
        document.purchase_order = short_text(
            data["purchase_order"], 100, "Le numéro de commande"
        )
    if "operation_category" in data:
        value = data["operation_category"] or ""
        if value and value not in SaleOperation.values:
            raise BusinessError("Choisissez biens, services ou les deux.")
        document.operation_category = value
    mode = data.get("delivery_mode", "")
    if mode not in ("", "customer", "billing", "custom"):
        raise BusinessError("Mode de livraison invalide.")
    if mode == "billing":
        document.delivery_address = {}
    elif mode == "customer":
        document.delivery_address = customer.delivery_address if customer else {}
    elif "delivery_address" in data or any(
        "delivery_" + key in data for key in ADDRESS_FIELDS
    ):
        document.delivery_address = address_from(data)
    elif not document.pk and document.direction == "outgoing" and customer:
        document.delivery_address = customer.delivery_address
