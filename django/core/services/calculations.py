from collections import defaultdict
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP, localcontext
from core.common import BusinessError, decimal, money


VAT_CATEGORIES = (
    ("S", "Taxée"),
    ("Z", "Taux zéro"),
    ("E", "Exonérée"),
    ("AE", "Autoliquidation"),
    ("K", "Livraison intra-UE"),
    ("G", "Export hors UE"),
    ("O", "Hors champ"),
)
VAT_LABELS = dict(VAT_CATEGORIES)


def allocate(amount, weights):
    """Split an amount in cents proportionally without losing rounding remainders."""
    cents = int(amount * 100)
    if not cents:
        return [Decimal(0)] * len(weights)
    units = [int(weight * 100) for weight in weights]
    total = sum(units)
    shares = [divmod(cents * weight, total) for weight in units]
    extra = cents - sum(q for q, _ in shares)
    order = sorted(range(len(shares)), key=lambda i: -shares[i][1])
    bonus = set(order[:extra])
    return [Decimal(q + (i in bonus)) / 100 for i, (q, _) in enumerate(shares)]


def unit_prices(item, rate):
    gross = item.get("unit_price_including_tax")
    if gross not in (None, ""):
        gross = decimal(gross)
        net = (gross * 100 / (100 + rate)).quantize(
            Decimal("0.00000001"), rounding=ROUND_HALF_UP
        )
        return net, gross
    return decimal(
        item.get("unit_price_excluding_tax", item.get("unit_price", 0)),
        quantum=Decimal("0.00000001"),
    ), None


def calculate(source, discount_type="percent", discount_value=0):
    with localcontext() as ctx:
        ctx.prec = 42
        return _calculate(source, discount_type, discount_value)


def _calculate(source, discount_type, discount_value):
    if not isinstance(source, list) or not 1 <= len(source) <= 1000:
        raise BusinessError("Ajoutez entre 1 et 1 000 lignes.")
    lines, groups = [], defaultdict(list)
    for index, item in enumerate(source):
        if not isinstance(item, dict):
            raise BusinessError("Ligne invalide.")
        qty = decimal(item.get("quantity", item.get("qty", 1)))
        rate = decimal(item.get("vat_rate", 20))
        if not 0 <= rate <= 100:
            raise BusinessError("Le taux de TVA doit être compris entre 0 et 100 %.")
        price, gross_price = unit_prices(item, rate)
        discount = decimal(item.get("discount_percent", 0))
        description = str(item.get("description", "")).strip()
        category = str(item.get("vat_category_code") or ("S" if rate else "Z"))
        if not description or len(description) > 10000:
            raise BusinessError(
                "Description de ligne requise (10 000 caractères maximum)."
            )
        if qty <= 0 or price < 0 or not 0 <= rate <= 100 or not 0 <= discount <= 100:
            raise BusinessError("Quantité, prix, TVA ou remise invalide.")
        if category not in VAT_LABELS or (category == "S") != bool(rate):
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
            item_reference=str(
                item.get("item_reference") or item.get("reference") or ""
            ),
            description=description,
            quantity=qty,
            unit_price_excluding_tax=price,
            unit_price_including_tax=gross_price,
            unit_code=unit,
            discount_percent=discount,
            vat_rate=rate,
            vat_category_code=category,
            total_excluding_tax=net,
        )
        lines.append(record)
        groups[(category, rate)].append(record)
    buckets = sorted(groups.items())
    bases = [sum(i["total_excluding_tax"] for i in items) for _, items in buckets]
    subtotal = sum(bases)
    value = decimal(discount_value)
    if discount_type not in {"percent", "amount"} or value < 0:
        raise BusinessError("Remise globale invalide.")
    if discount_type == "percent":
        if value > 100:
            raise BusinessError("La remise globale ne peut pas dépasser 100 %.")
        allowance = money(subtotal * value / 100)
    else:
        if value != money(value) or value > subtotal:
            raise BusinessError(
                "La remise fixe HT doit être au centime et ne peut pas dépasser le sous-total HT."
            )
        allowance = money(value)

    taxes, rounding = [], Decimal(0)
    for ((category, rate), items), before, share in zip(
        buckets, bases, allocate(allowance, bases)
    ):
        basis = before - share
        tax = money(basis * rate / 100)
        weights = [i["total_excluding_tax"] for i in items]
        gross_target = gross_basis = gross_remaining = net_remaining = Decimal(0)
        for item, original, part in zip(items, weights, allocate(share, weights)):
            item["global_discount_amount"] = part
            item["total_excluding_tax"] -= part
            if item["unit_price_including_tax"] is None:
                net_remaining += original - part
            else:
                entered = money(item["quantity"] * item["unit_price_including_tax"])
                gross_target += entered - money(
                    entered * item["discount_percent"] / 100
                )
                gross_basis += original
                gross_remaining += original - part
        if any(i["unit_price_including_tax"] is not None for i in items):
            if discount_type == "percent":
                gross_target -= money(gross_target * value / 100)
            elif gross_basis:
                gross_target = money(gross_target * gross_remaining / gross_basis)
            rounding += (
                gross_target
                + net_remaining
                + money(net_remaining * rate / 100)
                - basis
                - tax
            )
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
        taxes.append(
            dict(
                category=category,
                label=VAT_LABELS[category],
                rate=rate,
                basis=basis,
                tax=tax,
                allowance=share,
            )
        )
    net = sum((i["total_excluding_tax"] for i in lines), Decimal(0))
    vat = sum((t["tax"] for t in taxes), Decimal(0))
    return dict(
        lines=lines,
        taxes=taxes,
        subtotal=subtotal,
        allowance=allowance,
        net=net,
        vat=vat,
        gross=net + vat,
        rounding=rounding,
        payable=net + vat + rounding,
    )
