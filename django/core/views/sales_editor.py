import re

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from core.common import BusinessError
from core.models import Company, InventoryItem
from core.security import scoped
from core.services.branding import SUPPORTED_CURRENCIES
from core.services.calculations import calculate
from .documents import read_data


@scoped("write")
@require_POST
def preview_prices(request):
    data = read_data(request)
    lines = data.get("lines")
    if not isinstance(lines, list) or any(not isinstance(line, dict) for line in lines):
        raise BusinessError("Lignes invalides.")
    # A price can be previewed while the designation is still being typed.
    rows = [dict(line, description=line.get("description") or "…") for line in lines]
    result = calculate(
        rows,
        data.get("global_discount_type", "percent"),
        data.get("global_discount_value", 0),
    )
    return JsonResponse(
        {
            **{
                key: result[key]
                for key in (
                    "subtotal",
                    "allowance",
                    "net",
                    "vat",
                    "rounding",
                    "payable",
                )
            },
            "lines": [
                line["total_excluding_tax"] + line["global_discount_amount"]
                for line in result["lines"]
            ],
            "taxes": result["taxes"],
        }
    )


@scoped("write")
@require_POST
@transaction.atomic
def quote_article(request):
    data = read_data(request)
    token = str(data.get("creation_uid", ""))
    name = str(data.get("name") or "").strip()
    line = data.get("line")
    kind = data.get("item_type")
    currency = str(data.get("currency_code") or "").upper()
    if currency not in SUPPORTED_CURRENCIES:
        raise BusinessError("Vérifiez la devise du devis.")
    if (
        not re.fullmatch(r"[0-9a-f]{32}", token)
        or not name
        or len(name) > 255
        or kind not in {"product", "service"}
    ):
        raise BusinessError("Précisez le nom et le type de l’article.")
    if not isinstance(line, dict):
        raise BusinessError("Ligne de devis invalide.")
    row = calculate(
        [
            dict(
                line,
                description=line.get("description") or name,
                quantity=1,
                discount_percent=0,
            )
        ]
    )["lines"][0]
    reference = row["item_reference"].strip()
    if len(reference) > 255:
        raise BusinessError("La référence de l’article est limitée à 255 caractères.")
    Company.objects.select_for_update().get(pk=request.company.pk)
    items = InventoryItem.objects.filter(company_uid=request.company.company_uid)
    existing = items.filter(item_uid=token).first()
    if not existing:
        matches = (
            items.filter(reference__iexact=reference)
            if reference
            else items.filter(name__iexact=name)
        )
        candidates = list(matches.order_by("id")[:2])
        if len(candidates) > 1:
            raise BusinessError(
                "Plusieurs articles correspondent. Choisissez-les dans le catalogue ou précisez une référence.",
                409,
            )
        existing = candidates[0] if candidates else None
    fields = (
        "item_uid",
        "currency_code",
        "name",
        "reference",
        "description",
        "unit_code",
        "unit_price_excluding_tax",
        "unit_price_including_tax",
        "vat_rate",
        "vat_category_code",
    )
    if existing:
        if existing.status != "active":
            raise BusinessError(
                "Cet article est archivé. Consultez le catalogue ou utilisez une autre référence.",
                409,
            )
        return JsonResponse(
            {"created": False, "item": {key: getattr(existing, key) for key in fields}}
        )
    # The public identifier supplied for replay protection cannot claim an item in another company.
    if InventoryItem.objects.filter(item_uid=token).exists():
        raise BusinessError("Identifiant de création indisponible.", 409)
    item = items.create(
        company_uid=request.company.company_uid,
        item_uid=token,
        name=name,
        item_type=kind,
        currency_code=currency,
        reference=reference,
        description=row["description"],
        unit_code=row["unit_code"],
        unit_price_excluding_tax=row["unit_price_excluding_tax"],
        unit_price_including_tax=row["unit_price_including_tax"],
        vat_rate=row["vat_rate"],
        vat_category_code=row["vat_category_code"],
    )
    return JsonResponse(
        {"created": True, "item": {key: getattr(item, key) for key in fields}},
        status=201,
    )
