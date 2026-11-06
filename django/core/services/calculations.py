from collections import defaultdict
from decimal import Decimal, ROUND_FLOOR
from core.common import BusinessError, decimal, money


def calculate(source):
    if not isinstance(source, list) or not 1 <= len(source) <= 1000:
        raise BusinessError("Ajoutez entre 1 et 1 000 lignes.")
    lines, groups = [], defaultdict(list)
    for index, item in enumerate(source):
        if not isinstance(item, dict):
            raise BusinessError("Ligne invalide.")
        qty = decimal(item.get("quantity", item.get("qty", 1)))
        price = decimal(item.get("unit_price_excluding_tax", item.get("unit_price", 0)))
        rate = decimal(item.get("vat_rate", 20))
        discount = decimal(item.get("discount_percent", 0))
        description = str(item.get("description", "")).strip()
        category = str(item.get("vat_category_code") or ("S" if rate else "Z"))
        if not description or len(description) > 10000:
            raise BusinessError(
                "Description de ligne requise (10 000 caractères maximum)."
            )
        if qty <= 0 or price < 0 or not 0 <= rate <= 100 or not 0 <= discount <= 100:
            raise BusinessError("Quantité, prix, TVA ou remise invalide.")
        if category not in {"S", "Z", "E", "AE", "K", "G", "O"} or (
            category == "S"
        ) != bool(rate):
            raise BusinessError("La catégorie TVA ne correspond pas au taux.")
        unit = str(item.get("unit_code") or "C62")
        if unit not in {
            "C62",
            "HUR",
            "DAY",
            "MON",
            "ANN",
            "KGM",
            "LTR",
            "MTR",
            "MTK",
            "MTQ",
            "KWH",
            "MIN",
            "LS",
            "EA",
        }:
            raise BusinessError("Unité inconnue.")
        base = money(qty * price)
        net = base - money(base * discount / 100)
        record = dict(
            position=index + 1,
            item_reference=str(item.get("item_reference", item.get("reference", ""))),
            description=description,
            quantity=qty,
            unit_price_excluding_tax=price,
            unit_code=unit,
            discount_percent=discount,
            vat_rate=rate,
            vat_category_code=category,
            total_excluding_tax=net,
        )
        lines.append(record)
        groups[(category, rate)].append(record)
    taxes = []
    for (category, rate), items in sorted(groups.items()):
        basis = sum((i["total_excluding_tax"] for i in items), Decimal(0))
        tax = money(basis * rate / 100)
        for item in items:
            exact = item["total_excluding_tax"] * rate
            cents = exact.to_integral_value(rounding=ROUND_FLOOR)
            item["total_vat"] = cents / 100
            item["_remainder"] = exact - cents
        extra = int((tax - sum(i["total_vat"] for i in items)) * 100)
        for item in sorted(items, key=lambda i: (-i["_remainder"], i["position"]))[
            :extra
        ]:
            item["total_vat"] += Decimal("0.01")
        for item in items:
            item.pop("_remainder")
            item["total_including_tax"] = (
                item["total_excluding_tax"] + item["total_vat"]
            )
        taxes.append(dict(category=category, rate=rate, basis=basis, tax=tax))
    net = sum((i["total_excluding_tax"] for i in lines), Decimal(0))
    vat = sum((t["tax"] for t in taxes), Decimal(0))
    return dict(lines=lines, taxes=taxes, net=net, vat=vat, gross=net + vat)
