import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as datetime_timezone
from decimal import Decimal
from io import BytesIO
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone
from core.common import BusinessError, iso_date, money, today
from core.models import (
    Customer,
    CustomerProfile,
    Document,
    DocumentLine,
    BankAccount,
    BankTransaction,
    InventoryItem,
    StockMovement,
    Opportunity,
    CrmActivity,
    CustomerContact,
    Ingestion,
    Forecast,
)
from core.security import hidden_expense_documents, ROLES
from .finance import csv_text, vat_rows

DATASETS = {
    "summary": "Synthèse et comparatifs",
    "documents": "Devis, factures et avoirs",
    "lines": "Détail des lignes de facturation",
    "customers": "Clients et prospects",
    "contacts": "Contacts CRM",
    "opportunities": "Opportunités commerciales",
    "activities": "Activités et relances",
    "inventory": "Stock en fin de période",
    "movements": "Mouvements de stock",
    "transactions": "Transactions bancaires",
    "vat": "État de TVA",
    "expenses": "Notes de frais",
    "forecasts": "Prévisions de trésorerie",
}
METRICS = {
    "sales": "Ventes HT (avoirs déduits)",
    "purchases": "Achats HT (avoirs déduits)",
    "balance": "Ventes moins achats HT",
    "incoming": "Encaissements bancaires",
    "outgoing": "Décaissements bancaires",
    "cash_net": "Solde des flux bancaires",
    "won": "Opportunités gagnées",
}
RELATION_LABELS = {
    "customer": "Client",
    "prospect": "Prospect",
    "supplier": "Fournisseur",
    "both": "Client et fournisseur",
}
DIRECTION_LABELS = {"incoming": "Entrant", "outgoing": "Sortant"}
TYPE_LABELS = {
    "invoice": "Facture",
    "progress_invoice": "Facture d'avancement",
    "quote": "Devis",
    "credit_note": "Avoir",
    "product": "Produit",
    "service": "Service",
}
STATUS_LABELS = {
    "active": "Actif",
    "archived": "Archivé",
    "planned": "Prévu",
    "cancelled": "Annulé",
    "uploaded": "Importé",
    "transcribed": "Transcrit",
    "analyzed": "Analysé",
    "converted": "Converti",
    "failed": "Échec",
    "queued": "En attente",
}


@dataclass
class Period:
    start: date
    end: date
    compare_start: date | None
    compare_end: date | None
    comparison: str

    @property
    def label(self):
        if self.start == date.min and self.end == date.max:
            return "Toutes les dates"
        return f"{self.start:%d/%m/%Y} au {self.end:%d/%m/%Y}"

    @property
    def compare_label(self):
        return (
            f"{self.compare_start:%d/%m/%Y} au {self.compare_end:%d/%m/%Y}"
            if self.compare_start
            else "Sans comparaison"
        )


def period(params):
    now = date.fromisoformat(today())
    preset = params.get("preset", "")
    start, end = date(now.year, 1, 1), now
    if preset == "all":
        return Period(date.min, date.max, None, None, "none")
    if preset == "month":
        start = now.replace(day=1)
    elif preset == "quarter":
        start = date(now.year, ((now.month - 1) // 3) * 3 + 1, 1)
    elif preset not in {"", "year"}:
        raise BusinessError("Raccourci de période invalide.")
    if not preset:
        start = date.fromisoformat(
            iso_date(params.get("from") or params.get("date_from") or start.isoformat())
        )
        end = date.fromisoformat(
            iso_date(params.get("to") or params.get("date_to") or end.isoformat())
        )
    if start > end:
        raise BusinessError("La date de début doit précéder la date de fin.")
    comparison = params.get("compare", "previous")
    try:
        if comparison == "previous":
            compare_end = start - timedelta(days=1)
            compare_start = compare_end - (end - start)
        elif comparison == "year":

            def before(value):
                return value.replace(
                    year=value.year - 1,
                    day=min(
                        value.day, calendar.monthrange(value.year - 1, value.month)[1]
                    ),
                )

            compare_start, compare_end = before(start), before(end)
        elif comparison == "custom":
            compare_start = date.fromisoformat(iso_date(params.get("compare_from")))
            compare_end = date.fromisoformat(iso_date(params.get("compare_to")))
            if compare_start > compare_end:
                raise ValueError()
        elif comparison == "none":
            compare_start = compare_end = None
        else:
            raise ValueError()
    except (ValueError, OverflowError) as exc:
        raise BusinessError("Période de comparaison invalide.") from exc
    return Period(start, end, compare_start, compare_end, comparison)


def between(query, field, start, end, timestamp=False):
    if timestamp is True:
        return query.filter(
            **{field + "__date__gte": start, field + "__date__lte": end}
        )
    if timestamp == "legacy":
        if start != date.min:
            lower = datetime.combine(
                start, time.min, tzinfo=timezone.get_current_timezone()
            ).astimezone(datetime_timezone.utc)
            query = query.filter(
                **{field + "__gte": lower.strftime("%Y-%m-%d %H:%M:%S")}
            )
        if end != date.max:
            upper = datetime.combine(
                end + timedelta(days=1),
                time.min,
                tzinfo=timezone.get_current_timezone(),
            ).astimezone(datetime_timezone.utc)
            query = query.filter(
                **{field + "__lt": upper.strftime("%Y-%m-%d %H:%M:%S")}
            )
        return query
    return query.filter(**{field + "__gte": str(start), field + "__lte": str(end)})


def documents_for(request):
    query = Document.objects.filter(company_uid=request.company.company_uid).exclude(
        document_uid__in=hidden_expense_documents(request)
    )
    customer_uid = request.GET.get("customer_uid", "")
    if customer_uid:
        if not Customer.objects.filter(
            company_uid=request.company.company_uid, customer_uid=customer_uid
        ).exists():
            raise BusinessError("Client inaccessible.", 404)
        query = query.filter(customer_uid=customer_uid)
    return query


def transactions_for(company):
    accounts = dict(
        BankAccount.objects.filter(company_uid=company.company_uid).values_list(
            "account_uid", "currency_code"
        )
    )
    return BankTransaction.objects.filter(
        company_uid=company.company_uid, status="booked"
    ), accounts


def totals(request, start, end):
    result = defaultdict(lambda: defaultdict(Decimal))
    query = between(documents_for(request), "issue_date", start, end)
    effective = query.exclude(type="quote").exclude(
        status__in=["draft", "cancelled", "refused", "simulated"]
    )
    for doc in effective:
        value = money(doc.total_excluding_tax) * (
            -1 if doc.type == "credit_note" else 1
        )
        result[doc.currency_code][
            "sales" if doc.direction == "outgoing" else "purchases"
        ] += value
    if request.role in ROLES["finance"]:
        transactions, accounts = transactions_for(request.company)
        for item in between(transactions, "transaction_date", start, end):
            result[accounts.get(item.account_uid, "EUR")][
                "incoming" if item.direction == "incoming" else "outgoing"
            ] += money(item.amount)
    for item in between(
        Opportunity.objects.filter(
            company_uid=request.company.company_uid, stage="won"
        ),
        "closed_on",
        start,
        end,
    ):
        result[item.currency_code]["won"] += item.amount
    for values in result.values():
        values["balance"] = values["sales"] - values["purchases"]
        values["cash_net"] = values["incoming"] - values["outgoing"]
    return result


def summary(request, selected):
    current = totals(request, selected.start, selected.end)
    old = (
        totals(request, selected.compare_start, selected.compare_end)
        if selected.compare_start
        else {}
    )
    metrics = []
    for currency in sorted(set(current) | set(old) or {"EUR"}):
        for key, label in METRICS.items():
            if (
                key in {"incoming", "outgoing", "cash_net"}
                and request.role not in ROLES["finance"]
            ):
                continue
            value, before = (
                current.get(currency, {}).get(key, Decimal(0)),
                old.get(currency, {}).get(key, Decimal(0)),
            )
            difference = value - before
            percentage = money(difference / abs(before) * 100) if before else None
            metrics.append(
                {
                    "key": key,
                    "label": label,
                    "currency": currency,
                    "value": value,
                    "before": before,
                    "difference": difference,
                    "percentage": percentage,
                }
            )
    docs = between(documents_for(request), "issue_date", selected.start, selected.end)
    monthly = defaultdict(lambda: [Decimal(0), Decimal(0)])
    for doc in docs.exclude(type="quote").exclude(
        status__in=["draft", "cancelled", "refused", "simulated"]
    ):
        monthly[(doc.issue_date[:7], doc.currency_code)][
            0 if doc.direction == "outgoing" else 1
        ] += money(doc.total_excluding_tax) * (-1 if doc.type == "credit_note" else 1)
    counts = {
        "documents": docs.count(),
        "quotes": docs.filter(type="quote").count(),
        "customers": between(
            Customer.objects.filter(company_uid=request.company.company_uid),
            "created_at",
            selected.start,
            selected.end,
            "legacy",
        ).count(),
        "tasks": CrmActivity.objects.filter(
            company_uid=request.company.company_uid,
            kind="task",
            completed_at__isnull=True,
            due_on__lte=selected.end,
        ).count(),
    }

    def count_period(start, end):
        docs = between(documents_for(request), "issue_date", start, end)
        return {
            "documents": docs.count(),
            "quotes": docs.filter(type="quote").count(),
            "customers": between(
                Customer.objects.filter(company_uid=request.company.company_uid),
                "created_at",
                start,
                end,
                "legacy",
            ).count(),
            "activities": between(
                CrmActivity.objects.filter(company_uid=request.company.company_uid),
                "created_at",
                start,
                end,
                True,
            ).count(),
            "opportunities": between(
                Opportunity.objects.filter(company_uid=request.company.company_uid),
                "created_at",
                start,
                end,
                True,
            ).count(),
        }

    period_counts = count_period(selected.start, selected.end)
    prior_counts = (
        count_period(selected.compare_start, selected.compare_end)
        if selected.compare_start
        else {}
    )
    count_metrics = [
        {
            "label": label,
            "value": period_counts[key],
            "before": prior_counts.get(key, 0),
            "difference": period_counts[key] - prior_counts.get(key, 0),
        }
        for key, label in {
            "documents": "Documents",
            "quotes": "Devis",
            "customers": "Nouveaux clients / prospects",
            "activities": "Activités CRM créées",
            "opportunities": "Opportunités créées",
        }.items()
    ]
    return {
        "metrics": metrics,
        "counts": counts,
        "count_metrics": count_metrics,
        "monthly": [
            {
                "month": key[0],
                "currency": key[1],
                "sales": values[0],
                "purchases": values[1],
            }
            for key, values in sorted(monthly.items())
        ],
    }


def dataset(request, name, selected, report=None):
    company_uid = request.company.company_uid
    start, end = selected.start, selected.end
    if name == "summary":
        report = report or summary(request, selected)
        headers = [
            "Indicateur",
            "Devise / unité",
            "Période",
            "Comparaison",
            "Écart",
            "Évolution %",
        ]
        rows = [
            [
                m["label"],
                m["currency"],
                money(m["value"]),
                money(m["before"]) if selected.compare_start else None,
                money(m["difference"]) if selected.compare_start else None,
                m["percentage"] if selected.compare_start else None,
            ]
            for m in report["metrics"]
        ]
        rows += [
            [
                m["label"],
                "Nombre",
                m["value"],
                m["before"] if selected.compare_start else None,
                m["difference"] if selected.compare_start else None,
                None,
            ]
            for m in report["count_metrics"]
        ]
        return headers, rows
    if name == "documents":
        query = between(documents_for(request), "issue_date", start, end).order_by(
            "issue_date", "id"
        )
        if request.GET.get("status"):
            query = query.filter(status=request.GET["status"])
        if request.GET.get("direction") in {"incoming", "outgoing"}:
            query = query.filter(direction=request.GET["direction"])
        return [
            "Date",
            "Numéro",
            "Type",
            "Sens",
            "Client/fournisseur",
            "Devise",
            "HT",
            "TVA",
            "TTC",
            "Reste dû",
            "Statut",
        ], [
            [
                d.issue_date,
                d.number,
                TYPE_LABELS.get(d.type, d.type),
                DIRECTION_LABELS.get(d.direction, d.direction),
                (d.buyer if d.direction == "outgoing" else d.seller).get("name", ""),
                d.currency_code,
                money(d.total_excluding_tax),
                money(d.total_vat),
                money(d.total_including_tax),
                money(d.amount_due),
                d.get_status_label,
            ]
            for d in query
        ]
    if name == "customers":
        profiles = {
            p.customer_uid: p
            for p in CustomerProfile.objects.filter(company_uid=company_uid)
        }
        query = between(
            Customer.objects.filter(company_uid=company_uid),
            "created_at",
            start,
            end,
            "legacy",
        ).order_by("legal_name")
        return [
            "Identifiant",
            "Nom",
            "Type",
            "Relation",
            "Email",
            "Téléphone",
            "Adresse",
            "Code postal",
            "Ville",
            "Pays",
            "SIREN",
            "TVA",
            "Statut",
            "Créé le",
            "Étiquettes CRM",
            "Notes CRM",
            "Responsable CRM",
            "SIRET",
            "Complément d'adresse",
        ], [
            [
                c.customer_uid,
                c.legal_name,
                "Particulier" if c.type == "individual" else "Professionnel",
                RELATION_LABELS.get(
                    getattr(profiles.get(c.customer_uid), "relationship", "customer"),
                    "Client",
                ),
                c.email,
                c.phone,
                c.address_line1,
                c.postal_code,
                c.city,
                c.country_code,
                c.siren,
                c.vat_number,
                STATUS_LABELS.get(c.status, c.status),
                c.created_at,
                getattr(profiles.get(c.customer_uid), "tags", ""),
                getattr(profiles.get(c.customer_uid), "notes", ""),
                getattr(profiles.get(c.customer_uid), "assigned_to_user_uid", ""),
                c.siret,
                c.address_line2,
            ]
            for c in query
        ]
    if name == "lines":
        documents = {
            d.document_uid: d
            for d in between(documents_for(request), "issue_date", start, end)
        }
        query = DocumentLine.objects.filter(document_uid__in=documents).order_by(
            "document_uid", "id"
        )
        return [
            "Date",
            "Document",
            "Désignation",
            "Quantité",
            "Unité",
            "PU HT",
            "Remise %",
            "TVA %",
            "HT",
            "TVA",
            "TTC",
            "Devise",
        ], [
            [
                documents[line.document_uid].issue_date,
                documents[line.document_uid].number,
                line.description,
                line.quantity,
                line.unit_code,
                line.unit_price_excluding_tax,
                line.discount_percent,
                line.vat_rate,
                line.total_excluding_tax,
                line.total_vat,
                line.total_including_tax,
                documents[line.document_uid].currency_code,
            ]
            for line in query
        ]
    if name in {"contacts", "opportunities", "activities"}:
        names = dict(
            Customer.objects.filter(company_uid=company_uid).values_list(
                "customer_uid", "legal_name"
            )
        )
        if name == "contacts":
            return ["Client", "Contact", "Fonction", "Email", "Téléphone", "Créé le"], [
                [
                    names.get(c.customer_uid, ""),
                    c.name,
                    c.position,
                    c.email,
                    c.phone,
                    c.created_at.date(),
                ]
                for c in between(
                    CustomerContact.objects.filter(company_uid=company_uid),
                    "created_at",
                    start,
                    end,
                    True,
                ).order_by("id")
            ]
        if name == "opportunities":
            return [
                "Client",
                "Opportunité",
                "Étape",
                "Montant",
                "Devise",
                "Clôture prévue",
                "Clôturée le",
                "Responsable",
                "Créée le",
            ], [
                [
                    names.get(o.customer_uid, ""),
                    o.title,
                    o.get_stage_display(),
                    o.amount,
                    o.currency_code,
                    o.expected_close,
                    o.closed_on,
                    o.assigned_to_user_uid,
                    o.created_at.date(),
                ]
                for o in between(
                    Opportunity.objects.filter(company_uid=company_uid),
                    "created_at",
                    start,
                    end,
                    True,
                ).order_by("id")
            ]
        return [
            "Client",
            "Type",
            "Objet",
            "Contenu",
            "Échéance",
            "Terminée le",
            "Créée le",
        ], [
            [
                names.get(a.customer_uid, ""),
                a.get_kind_display(),
                a.title,
                a.body,
                a.due_on,
                a.completed_at.date() if a.completed_at else None,
                a.created_at.date(),
            ]
            for a in between(
                CrmActivity.objects.filter(company_uid=company_uid),
                "created_at",
                start,
                end,
                True,
            ).order_by("id")
        ]
    if name == "inventory":
        movements = StockMovement.objects.filter(
            company_uid=company_uid, occurred_on__lte=str(end)
        )
        stocks = defaultdict(Decimal)
        prior_stocks = defaultdict(Decimal)
        for movement in movements:
            stocks[movement.item_uid] += (
                movement.quantity
                if movement.movement_type == "in"
                else -movement.quantity
            )
            if selected.compare_end and movement.occurred_on <= str(
                selected.compare_end
            ):
                prior_stocks[movement.item_uid] += (
                    movement.quantity
                    if movement.movement_type == "in"
                    else -movement.quantity
                )
        if selected.compare_end and selected.compare_end > end:
            for movement in StockMovement.objects.filter(
                company_uid=company_uid,
                occurred_on__gt=str(end),
                occurred_on__lte=str(selected.compare_end),
            ):
                prior_stocks[movement.item_uid] += (
                    movement.quantity
                    if movement.movement_type == "in"
                    else -movement.quantity
                )
        return [
            "Article",
            "Référence",
            "Type",
            "Stock à la fin",
            "Unité",
            "Prix HT actuel",
            "TVA %",
            "Seuil",
            "Statut",
            "Stock comparé",
            "Écart de stock",
        ], [
            [
                item.name,
                item.reference,
                TYPE_LABELS.get(item.item_type, item.item_type),
                stocks[item.item_uid] if item.stock_tracking else None,
                item.unit_code,
                item.unit_price_excluding_tax,
                item.vat_rate,
                item.reorder_level,
                STATUS_LABELS.get(item.status, item.status),
                prior_stocks[item.item_uid]
                if selected.compare_end and item.stock_tracking
                else None,
                stocks[item.item_uid] - prior_stocks[item.item_uid]
                if selected.compare_end and item.stock_tracking
                else None,
            ]
            for item in InventoryItem.objects.filter(company_uid=company_uid).order_by(
                "name"
            )
        ]
    if name == "movements":
        names = dict(
            InventoryItem.objects.filter(company_uid=company_uid).values_list(
                "item_uid", "name"
            )
        )
        return ["Date", "Article", "Sens", "Quantité", "Motif", "Référence"], [
            [
                m.occurred_on,
                names.get(m.item_uid, ""),
                "Entrée" if m.movement_type == "in" else "Sortie",
                m.quantity,
                m.reason,
                m.reference,
            ]
            for m in between(
                StockMovement.objects.filter(company_uid=company_uid),
                "occurred_on",
                start,
                end,
            ).order_by("occurred_on", "id")
        ]
    if name == "transactions":
        query, accounts = transactions_for(request.company)
        return [
            "Date",
            "Libellé",
            "Montant",
            "Devise",
            "Sens",
            "Compte",
            "Document",
            "Catégorie",
        ], [
            [
                t.transaction_date,
                t.label,
                money(t.amount),
                accounts.get(t.account_uid, "EUR"),
                DIRECTION_LABELS.get(t.direction, t.direction),
                t.account_uid,
                t.document_uid,
                t.category,
            ]
            for t in between(query, "transaction_date", start, end).order_by(
                "transaction_date", "id"
            )
        ]
    if name == "vat":
        return [
            "Date",
            "Facture",
            "Sens",
            "Devise",
            "Taux %",
            "Base HT",
            "TVA",
        ], vat_rows(request.company, str(start), str(end))
    if name == "expenses":
        query = Ingestion.objects.filter(
            company_uid=company_uid,
            source_kind__in=["expense_note", "expense", "expense_receipt", "receipt"],
        )
        return [
            "Date",
            "Commerçant",
            "Description",
            "Catégorie",
            "Montant",
            "Devise",
            "Déposé par",
            "Statut",
        ], [
            [
                i.expense_date,
                i.merchant_name,
                i.expense_description,
                i.expense_category,
                i.expense_amount,
                i.expense_currency,
                i.uploaded_by_user_uid,
                STATUS_LABELS.get(i.status, i.status),
            ]
            for i in between(query, "expense_date", start, end).order_by(
                "expense_date", "id"
            )
        ]
    if name == "forecasts":
        return ["Échéance", "Libellé", "Montant", "Devise", "Sens", "Statut"], [
            [
                f.due_date,
                f.label,
                f.amount,
                "EUR",
                DIRECTION_LABELS.get(f.direction, f.direction),
                STATUS_LABELS.get(f.status, f.status),
            ]
            for f in between(
                Forecast.objects.filter(company_uid=company_uid), "due_date", start, end
            ).order_by("due_date", "id")
        ]
    raise BusinessError("Jeu de données inconnu.", 404)


def export_bytes(format, headers, rows, title, company, selected, report):
    if format == "csv":
        return csv_text(headers, rows).encode(), "text/csv; charset=utf-8"
    if format == "xlsx":
        from openpyxl import Workbook
        from openpyxl.cell import WriteOnlyCell
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils.cell import get_column_letter
        from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

        workbook = Workbook(write_only=True)
        long_texts = []

        def sheet(name, sheet_headers, sheet_rows):
            ws = workbook.create_sheet(name)
            ws.freeze_panes = "A2"
            for i in range(1, len(sheet_headers) + 1):
                ws.column_dimensions[get_column_letter(i)].width = 24 if i < 5 else 19
            for index, row in enumerate([sheet_headers] + sheet_rows):
                cells = []
                for column, value in enumerate(row, 1):
                    if isinstance(value, str):
                        value = ILLEGAL_CHARACTERS_RE.sub("", value)
                        if len(value) > 32767:
                            for part, offset in enumerate(
                                range(0, len(value), 32000), 1
                            ):
                                long_texts.append(
                                    [
                                        index + 1,
                                        sheet_headers[column - 1],
                                        part,
                                        value[offset : offset + 32000],
                                    ]
                                )
                            value = (
                                value[:100]
                                + f"… Texte complet dans l'onglet Textes longs (ligne {index + 1})."
                            )
                    if (
                        isinstance(value, Decimal)
                        and len(value.normalize().as_tuple().digits) > 15
                    ):
                        value = str(
                            value
                        )  # Excel has only 15 significant numeric digits.
                    cell = WriteOnlyCell(ws, value=value)
                    if isinstance(value, str):
                        cell.data_type = "s"  # Never execute user-supplied formulas.
                    if isinstance(value, Decimal):
                        cell.number_format = "#,##0.00######"
                    if isinstance(value, date):
                        cell.number_format = "dd/mm/yyyy"
                    cell.alignment = Alignment(vertical="top", wrap_text=True)
                    if index == 0:
                        cell.font = Font(bold=True, color="FFFFFF")
                        cell.fill = PatternFill("solid", fgColor="176F60")
                    cells.append(cell)
                ws.append(cells)
            ws.auto_filter.ref = (
                f"A1:{get_column_letter(len(sheet_headers))}{len(sheet_rows) + 1}"
            )

        sheet("Données", headers, rows)
        if long_texts:
            sheet(
                "Textes longs",
                [
                    "Ligne dans Données",
                    "Colonne",
                    "Partie",
                    "Texte complet à concaténer",
                ],
                long_texts,
            )
        sheet(
            "Périodes",
            ["Paramètre", "Valeur"],
            [
                ["Entreprise", company.legal_name],
                ["Rapport", title],
                ["Période", selected.label],
                ["Comparaison", selected.compare_label],
                [
                    "Dates des clients et CRM",
                    "Date de création ; stock en fin de période au prix HT actuel",
                ],
            ],
        )
        output = BytesIO()
        workbook.save(output)
        return (
            output.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    if format == "pdf":
        if len(rows) > 10000:
            raise BusinessError(
                "Ce PDF dépasse 10 000 lignes. Réduisez la période ou choisissez Excel/CSV pour l'export complet."
            )
        from weasyprint import HTML

        # Tables cannot split a very tall cell across pages. Continue long cells
        # explicitly, without repeating amounts or dropping any text.
        pdf_rows = []
        for row in rows:
            pieces = []
            for value in row:
                if not isinstance(value, str):
                    pieces.append([value])
                    continue
                chunks, chunk = [], ""
                for character in value:
                    chunk += character
                    if (
                        len(chunk) >= 180 and (character.isspace() or len(chunk) >= 220)
                    ) or chunk.count("\n") >= 8:
                        chunks.append(chunk)
                        chunk = ""
                if chunk or not chunks:
                    chunks.append(chunk)
                pieces.append(chunks)
            for index in range(max(map(len, pieces), default=1)):
                continuation = [
                    parts[index] if index < len(parts) else "" for parts in pieces
                ]
                if index and not continuation[0]:
                    continuation[0] = "Suite"
                pdf_rows.append(continuation)
        source = render_to_string(
            "core/report_pdf.html",
            {
                "title": title,
                "company": company,
                "period": selected,
                "headers": headers,
                "rows": pdf_rows,
                "record_count": len(rows),
                "report": report,
                "generated_on": today(),
            },
        )
        return HTML(string=source).write_pdf(), "application/pdf"
    raise BusinessError("Format d'export invalide.")
