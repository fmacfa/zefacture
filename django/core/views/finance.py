from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.db.models import Q, Count, Sum
from django.core.paginator import Paginator
from django.conf import settings
from core.common import BusinessError, today, iso_date, money
from core.models import (
    BankAccount,
    BankFeed,
    BankFeedEntry,
    BankTransaction,
    Document,
    Forecast,
)
from core.services import bank_categories
from . import bank_statements
from core.services.bank_sync import candidates
from core.forms import AccountForm, ForecastForm
from core.security import scoped, scoped_get
from core.services import finance as service


def download_csv(name, headers, rows):
    response = HttpResponse(
        service.csv_text(headers, rows), content_type="text/csv; charset=utf-8"
    )
    response["Content-Disposition"] = f'attachment; filename="{name}.csv"'
    return response


@scoped("finance")
def finance(request, section="overview", item_uid=None, action=None):
    company = request.company
    if section == "accounts":
        if request.method == "POST" and item_uid:
            account = scoped_get(BankAccount, request, account_uid=item_uid)
            account.status = "archived"
            account.save()
            return redirect("/app/finances/comptes")
        form = AccountForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            account = form.save(commit=False)
            account.company_uid = company.company_uid
            account.current_balance = account.opening_balance
            if account.currency_code not in {
                "EUR",
                "USD",
                "GBP",
                "CHF",
                "CAD",
                "JPY",
                "SEK",
                "NOK",
                "DKK",
                "PLN",
            }:
                raise BusinessError("Devise invalide.")
            account.save()
            return redirect("/app/finances/comptes")
        return render(
            request,
            "core/bank_accounts.html",
            {
                "title": "Comptes bancaires",
                "form": form,
                "accounts": BankAccount.objects.filter(
                    company_uid=company.company_uid
                ).select_related("bank_feed"),
                "feed": BankFeed.objects.filter(
                    company_uid=company.company_uid
                ).first(),
                "callback_url": settings.APP_URL.rstrip("/")
                + "/app/finances/comptes/connexion/retour",
                "external_disabled": settings.EXTERNAL_SERVICES_DISABLED,
            },
        )
    if section == "transactions":
        if request.method == "POST":
            if item_uid:
                service.reconcile(
                    company,
                    scoped_get(BankTransaction, request, transaction_uid=item_uid),
                    request.POST.get("document_uid"),
                )
            elif action == "import":
                return bank_statements.upload(request)
            else:
                service.create_transaction(company, request.POST)
            return redirect("/app/finances/transactions")
        rows = BankTransaction.objects.filter(company_uid=company.company_uid).order_by(
            "-transaction_date", "-id"
        )
        if request.GET.get("q"):
            rows = rows.filter(label__icontains=request.GET["q"][:200])
        if request.GET.get("compte"):
            rows = rows.filter(account_uid=request.GET["compte"])
        category = request.GET.get("categorie", "")
        if category:
            rows = rows.filter(standalone_category=category)
        treatment = request.GET.get("traitement", "")
        unlinked = Q(document_uid__isnull=True) | Q(document_uid="")
        treatment_filters = {"unlinked": unlinked, "invoice": ~unlinked,
                             "pending": unlinked & Q(status="booked", standalone_category="") & ~Q(bank_feed__review__gt=""),
                             "standalone": unlinked & Q(status="booked") & ~Q(standalone_category="")}
        start = iso_date(request.GET.get("from"), optional=True)
        end = iso_date(request.GET.get("to"), optional=True)
        if start and end and start > end:
            raise BusinessError("Période invalide.")
        if start:
            rows = rows.filter(transaction_date__gte=start)
        if end:
            rows = rows.filter(transaction_date__lte=end)
        accounts = list(
            BankAccount.objects.filter(company_uid=company.company_uid).order_by("name")
        )
        by_uid = {a.account_uid: a for a in accounts}
        base_rows = rows
        if treatment in treatment_filters:
            rows = rows.filter(treatment_filters[treatment])
        if action == "export":
            return download_csv(
                "transactions",
                [
                    "date",
                    "libellé",
                    "montant",
                    "sens",
                    "compte",
                    "document",
                    "statut",
                    "catégorie",
                    "motif",
                    "devise",
                    "nom du compte",
                ],
                [
                    [
                        t.transaction_date,
                        t.label,
                        money(t.amount),
                        t.direction,
                        t.account_uid,
                        t.document_uid,
                        t.status,
                        bank_categories.category_label(t),
                        t.classification_note,
                        by_uid[t.account_uid].currency_code
                        if t.account_uid in by_uid
                        else "EUR",
                        by_uid[t.account_uid].name if t.account_uid in by_uid else "",
                    ]
                    for t in rows
                ],
            )
        counts = base_rows.aggregate(all=Count("pk"), **{key: Count("pk", filter=condition) for key, condition in treatment_filters.items()})
        totals = {}
        for group in rows.filter(status="booked").order_by().values("account_uid", "direction").annotate(amount=Sum("amount")):
            account = by_uid.get(group["account_uid"])
            currency = account.currency_code if account else ""
            balance = totals.setdefault(currency, {"incoming": Decimal(0), "outgoing": Decimal(0)})
            balance[group["direction"]] += group["amount"]
        for balance in totals.values():
            balance["net"] = balance["incoming"] - balance["outgoing"]
        reviews = (
            BankFeedEntry.objects.filter(
                feed_account__feed__company_uid=company.company_uid
            )
            .exclude(review="")
            .select_related(
                "feed_account__account", "feed_account__feed", "transaction"
            )
        )
        if request.GET.get("compte"):
            reviews = reviews.filter(
                feed_account__account__account_uid=request.GET["compte"]
            )
        if start:
            reviews = reviews.filter(day__gte=start)
        if end:
            reviews = reviews.filter(day__lte=end)
        if request.GET.get("q"):
            reviews = reviews.filter(label__icontains=request.GET["q"][:200])
        review_page = Paginator(reviews.order_by("pk"), 15).get_page(
            request.GET.get("verification")
        )
        for entry in review_page:
            if entry.review == "overlap":
                entry.choices = candidates(entry).order_by("pk")
        page = Paginator(rows.select_related("bank_feed"), 50).get_page(
            request.GET.get("page")
        )
        linked = {d.document_uid: d for d in Document.objects.filter(company_uid=company.company_uid,
                  document_uid__in=[t.document_uid for t in page if t.document_uid])}
        for row in page:
            row.document = linked.get(row.document_uid)
            row.account = by_uid.get(row.account_uid)
            row.category_label = bank_categories.category_label(row)
        return render(
            request,
            "core/transactions.html",
            {
                "title": "Transactions",
                "bank_counts": counts, "bank_totals": sorted(totals.items()),
                **bank_statements.batch_form(request),
                "categories": bank_categories.CATEGORIES,
                "new_expense": request.GET.get("depense", ""),
                "outgoing_categories": bank_categories.OUTGOING,
                "incoming_categories": bank_categories.INCOMING,
                "transactions": page,
                "review_page": review_page,
                "all_accounts": accounts,
                "today": today(),
                "accounts": BankAccount.objects.filter(
                    company_uid=company.company_uid, status="active"
                ),
            },
        )
    start = iso_date(
        request.GET.get("from")
        or request.GET.get("date_from")
        or today()[:4] + "-01-01"
    )
    end = iso_date(request.GET.get("to") or request.GET.get("date_to") or today())
    if start > end:
        raise BusinessError("Période invalide.")
    if section == "accounting":
        headers = [
            "Date",
            "Numéro",
            "Sens",
            "Type",
            "Client/fournisseur",
            "Devise",
            "HT",
            "TVA",
            "TTC",
            "Reste dû",
            "Statut",
        ]
        rows = [
            [
                d.issue_date,
                d.number,
                d.direction,
                d.type,
                (d.buyer if d.direction == "outgoing" else d.seller).get("name", ""),
                d.currency_code,
                money(d.total_excluding_tax * d.journal_sign),
                money(d.total_vat * d.journal_sign),
                money(d.total_including_tax * d.journal_sign),
                money(d.amount_due),
                d.status,
            ]
            for d in service.effective_documents(company)
            .filter(issue_date__gte=start, issue_date__lte=end)
            .order_by("issue_date", "number")
        ]
        return download_csv("export-comptable", headers, rows)
    if section == "vat":
        rows = service.vat_rows(company, start, end)
        headers = ["Date", "Facture", "Sens", "Devise", "Taux %", "Base HT", "TVA"]
        if action == "export":
            return download_csv("tva", headers, rows)
        totals = defaultdict(lambda: [Decimal(0), Decimal(0)])
        for row in rows:
            totals[row[3]][0 if row[2] == "outgoing" else 1] += row[6]
        return render(
            request,
            "core/vat.html",
            {
                "title": "État de TVA",
                "bank_vat": service.vat_payments(company, start, end),
                "headers": headers,
                "rows": rows,
                "start": start,
                "end": end,
                "totals": [
                    {
                        "currency": key,
                        "collected": value[0],
                        "deductible": value[1],
                        "net": value[0] - value[1],
                    }
                    for key, value in totals.items()
                ],
            },
        )
    if section == "cashflow":
        if request.method == "POST" and item_uid:
            forecast = scoped_get(Forecast, request, forecast_uid=item_uid)
            forecast.status = "cancelled"
            forecast.save()
            return redirect("/app/finances/tresorerie")
        form = ForecastForm(
            request.POST or None, initial={"due_date": today(), "direction": "incoming"}
        )
        if request.method == "POST" and form.is_valid():
            obj = form.save(commit=False)
            obj.company_uid = company.company_uid
            iso_date(obj.due_date)
            if obj.amount <= 0:
                raise BusinessError("Montant positif requis.")
            obj.save()
            return redirect("/app/finances/tresorerie")
        forecasts = Forecast.objects.filter(
            company_uid=company.company_uid, status="planned"
        ).order_by("due_date")
        horizon = str(date.fromisoformat(today()) + timedelta(days=90))
        items = [
            {
                "date": f.due_date,
                "label": f.label,
                "amount": f.amount if f.direction == "incoming" else -f.amount,
                "currency": "EUR",
            }
            for f in forecasts.filter(due_date__lte=horizon)
        ]
        items += [
            {
                "date": d.due_date or today(),
                "label": d.number,
                "amount": d.amount_due if d.direction == "outgoing" else -d.amount_due,
                "currency": d.currency_code,
            }
            for d in service.effective_documents(company)
            .exclude(type="credit_note")
            .filter(amount_due__gt=0, due_date__lte=horizon)
        ]
        balances = defaultdict(Decimal)
        for account in BankAccount.objects.exclude(bank_feed__shared=False).filter(
            company_uid=company.company_uid, status="active"
        ):
            balances[account.currency_code] += account.current_balance
        for item in sorted(items, key=lambda i: i["date"]):
            balances[item["currency"]] += item["amount"]
            item["balance"] = balances[item["currency"]]
        return render(
            request,
            "core/cashflow.html",
            {
                "title": "Trésorerie à 90 jours",
                "form": form,
                "forecasts": forecasts,
                "items": sorted(items, key=lambda i: i["date"]),
                "balances": dict(balances),
            },
        )
    return render(
        request,
        "core/finance.html",
        {
            "title": "Finances",
            "start": start,
            "end": end,
            "accounts": BankAccount.objects.filter(
                company_uid=company.company_uid, status="active"
            ),
        },
    )
