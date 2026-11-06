from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.db.models import Sum
from core.common import BusinessError, today, stamp, decimal, iso_date, money
from core.models import BankAccount, BankTransaction, Document, Forecast
from core.forms import AccountForm, ForecastForm
from core.security import scoped, scoped_get
from core.services import finance as service
from .management import table


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
                "accounts": BankAccount.objects.filter(company_uid=company.company_uid),
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
                uploaded = request.FILES.get("file")
                if not uploaded:
                    raise BusinessError("CSV requis.")
                result = service.import_csv(
                    company, request.POST.get("account_uid"), uploaded.read()
                )
                messages.success(
                    request,
                    f"{result['imported']} transactions importées, {result['skipped']} doublons, {result['rejected']} lignes rejetées.",
                )
            else:
                service.create_transaction(company, request.POST)
            return redirect("/app/finances/transactions")
        rows = BankTransaction.objects.filter(company_uid=company.company_uid).order_by(
            "-transaction_date", "-id"
        )
        if request.GET.get("q"):
            rows = rows.filter(label__icontains=request.GET["q"][:200])
        if action == "export":
            return download_csv(
                "transactions",
                ["date", "libellé", "montant", "sens", "compte", "document"],
                [
                    [
                        t.transaction_date,
                        t.label,
                        money(t.amount),
                        t.direction,
                        t.account_uid,
                        t.document_uid,
                    ]
                    for t in rows
                ],
            )
        return render(
            request,
            "core/transactions.html",
            {
                "title": "Transactions",
                "transactions": rows[:300],
                "today": today(),
                "accounts": BankAccount.objects.filter(
                    company_uid=company.company_uid, status="active"
                ),
                "documents": service.effective_documents(company)
                .exclude(type="credit_note")
                .filter(amount_due__gt=0),
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
                money(
                    d.total_excluding_tax
                    * (
                        -1
                        if d.direction == "incoming" or d.type == "credit_note"
                        else 1
                    )
                ),
                money(
                    d.total_vat
                    * (
                        -1
                        if d.direction == "incoming" or d.type == "credit_note"
                        else 1
                    )
                ),
                money(
                    d.total_including_tax
                    * (
                        -1
                        if d.direction == "incoming" or d.type == "credit_note"
                        else 1
                    )
                ),
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
        for account in BankAccount.objects.filter(
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
