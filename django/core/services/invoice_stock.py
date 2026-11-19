"""Transfer confirmed invoice quantities to the existing stock movement journal."""

from decimal import Decimal
from django.db import transaction
from django.db.models import Case, IntegerField, Q, Sum, Value, When

from core.common import BusinessError, iso_date
from core.models import Company, Document, InventoryItem, StockMovement
from core.security import hidden_expense_documents


STATES = {
    "incoming": ("received", "accepted", "paid"),
    "outgoing": ("sent", "accepted", "paid"),
}


def can_transfer(document):
    return document.type == "invoice" and document.status in STATES.get(
        document.direction, ()
    )


def eligible():
    return Q(type="invoice") & (
        Q(direction="incoming", status__in=STATES["incoming"])
        | Q(direction="outgoing", status__in=STATES["outgoing"])
    )


def invoices(request):
    return Document.objects.filter(
        eligible(), company_uid=request.company.company_uid
    ).exclude(document_uid__in=hidden_expense_documents(request))


def totals_by_item(company_uid, item_uid=None):
    query = StockMovement.objects.filter(company_uid=company_uid)
    if item_uid:
        query = query.filter(item_uid=item_uid)
    rows = query.values("item_uid").annotate(
        entered=Sum("quantity", filter=Q(movement_type="in"), default=Decimal(0)),
        used=Sum("quantity", filter=Q(movement_type="out"), default=Decimal(0)),
        bought=Sum(
            "quantity",
            filter=Q(movement_type="in") & ~Q(source_line_uid=""),
            default=Decimal(0),
        ),
    )
    return {
        row["item_uid"]: {
            "bought": row["bought"],
            "used": row["used"],
            "available": row["entered"] - row["used"],
        }
        for row in rows
    }


EMPTY_TOTALS = {"bought": Decimal(0), "used": Decimal(0), "available": Decimal(0)}


def normalized(value):
    return " ".join((value or "").split()).casefold()


def suggested_item(line, items):
    """Never resolve an ambiguous reference or infer a unit conversion."""
    reference = normalized(line.item_reference)
    if reference:
        matches = [item for item in items if normalized(item.reference) == reference]
    else:
        matches = [
            item
            for item in items
            if normalized(item.name) == normalized(line.description)
        ]
    return matches[0] if len(matches) == 1 else None


def choices_for_lines(company_uid, doc, lines):
    catalog = list(
        InventoryItem.objects.filter(
            company_uid=company_uid, status="active", stock_tracking=1
        ).order_by("name", "pk")
    )
    by_uid = {item.item_uid: item for item in catalog}
    lines = list(lines)
    existing = {
        m.source_line_uid: m
        for m in StockMovement.objects.filter(
            company_uid=company_uid,
            source_line_uid__in=[line.line_uid for line in lines],
        )
    }
    used_items = {
        i.item_uid: i
        for i in InventoryItem.objects.filter(
            company_uid=company_uid,
            item_uid__in=[m.item_uid for m in existing.values()],
        )
    }
    prior = {}
    for quantity, unit in {
        (line.quantity, line.unit_code)
        for line in lines
        if line.line_uid not in existing
    }:
        compatible = [item.item_uid for item in catalog if item.unit_code == unit]
        if not compatible:
            continue
        candidates = StockMovement.objects.filter(
            company_uid=company_uid,
            source_line_uid="",
            quantity=quantity,
            item_uid__in=compatible,
            movement_type="in" if doc.direction == "incoming" else "out",
        ).order_by(
            Case(
                When(reference__iexact=doc.number, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
            "-occurred_on",
            "-pk",
        )
        group = list(candidates[:20])
        for move in group:
            move.article = by_uid[move.item_uid]
        prior[(quantity, unit)] = group
    for line in lines:
        line.recorded = existing.get(line.line_uid)
        if line.recorded:
            line.stock_item = used_items.get(line.recorded.item_uid)
        else:
            line.articles = [
                item for item in catalog if item.unit_code == line.unit_code
            ]
            line.suggested = suggested_item(line, line.articles)
            line.prior = prior.get((line.quantity, line.unit_code), [])
            line.possible_prior = any(
                move.reference and normalized(move.reference) == normalized(doc.number)
                for move in line.prior
            )
            if line.possible_prior:
                line.suggested = None
    return lines


def available_onwards(item, when):
    movements = (
        StockMovement.objects.filter(
            company_uid=item.company_uid, item_uid=item.item_uid
        )
        .values("occurred_on")
        .annotate(
            entered=Sum("quantity", filter=Q(movement_type="in"), default=Decimal(0)),
            used=Sum("quantity", filter=Q(movement_type="out"), default=Decimal(0)),
        )
        .order_by("occurred_on")
    )
    balance = Decimal(0)
    minimum = None
    for day in movements:
        if day["occurred_on"] > when and minimum is None:
            minimum = balance
        balance += day["entered"] - day["used"]
        if day["occurred_on"] >= when:
            minimum = balance if minimum is None else min(minimum, balance)
    return balance if minimum is None else minimum


@transaction.atomic
def post_lines(request, document_uid, choices, date):
    company_uid = request.company.company_uid
    Company.objects.select_for_update().get(pk=request.company.pk)
    doc = (
        invoices(request).select_for_update().filter(document_uid=document_uid).first()
    )
    if not doc:
        raise BusinessError("Facture indisponible pour le stock.", 404)
    when = iso_date(date)
    if not 1 <= len(choices) <= 40:
        raise BusinessError("Sélectionnez au moins une ligne de la page.")
    lines = list(doc.lines.filter(line_uid__in=choices))
    if len(lines) != len(choices):
        raise BusinessError("Une ligne ne fait pas partie de cette facture.", 404)
    direction = "in" if doc.direction == "incoming" else "out"
    moves = StockMovement.objects.filter(company_uid=company_uid)
    items = InventoryItem.objects.filter(
        company_uid=company_uid, status="active", stock_tracking=1
    )
    new = attached = already = 0
    for line in lines:
        if moves.filter(source_line_uid=line.line_uid).exists():
            already += 1
            continue
        if line.quantity <= 0:
            raise BusinessError("La quantité facturée doit être positive.")
        choice = choices[line.line_uid]
        if choice.startswith("move:"):
            move = (
                moves.select_for_update()
                .filter(
                    movement_uid=choice[5:],
                    source_line_uid="",
                    movement_type=direction,
                    quantity=line.quantity,
                )
                .first()
            )
            if (
                not move
                or not items.select_for_update()
                .filter(item_uid=move.item_uid, unit_code=line.unit_code)
                .exists()
            ):
                raise BusinessError(
                    "Le mouvement choisi est indisponible ou son unité, son sens ou sa quantité diffère.",
                    409,
                )
            move.source_document_uid, move.source_line_uid = (
                doc.document_uid,
                line.line_uid,
            )
            move.save(update_fields=["source_document_uid", "source_line_uid"])
            attached += 1
            continue
        compatible = items.select_for_update().filter(unit_code=line.unit_code)
        if choice == "new" and direction == "in":
            matching = list(
                compatible.filter(reference__iexact=line.item_reference.strip())
                if (line.item_reference or "").strip()
                else compatible.filter(name__iexact=line.description.strip())
            )
            if len(matching) > 1:
                raise BusinessError(
                    "Plusieurs articles correspondent. Sélectionnez l’article à créditer."
                )
            item = matching[0] if matching else None
            if not item:
                item = InventoryItem.objects.create(
                    company_uid=company_uid,
                    item_type="product",
                    stock_tracking=1,
                    name=line.description.strip(),
                    reference=(line.item_reference or "").strip(),
                    unit_code=line.unit_code,
                    vat_rate=line.vat_rate,
                )
        elif choice.startswith("item:"):
            item = compatible.filter(item_uid=choice[5:]).first()
            if not item:
                raise BusinessError(
                    "Choisissez un article actif suivi dans la même unité."
                )
        else:
            raise BusinessError("Sélection de stock invalide.")
        if direction == "out" and available_onwards(item, when) < line.quantity:
            raise BusinessError(
                f"Stock insuffisant pour « {item.name} ». Aucun mouvement de cette sélection n’a été enregistré.",
                409,
            )
        moves.create(
            company_uid=company_uid,
            item_uid=item.item_uid,
            movement_type=direction,
            quantity=line.quantity,
            occurred_on=when,
            reason="Achat sur facture" if direction == "in" else "Vente sur facture",
            reference=doc.number,
            source_document_uid=doc.document_uid,
            source_line_uid=line.line_uid,
            created_by_user_uid=request.user.user_uid,
        )
        new += 1
    return new, attached, already
