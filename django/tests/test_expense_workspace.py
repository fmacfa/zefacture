from decimal import Decimal

from core.common import json_text, uid
from core.models import (
    BankTransaction,
    CompanyMember,
    CompanyNumberFormat,
    ExpenseReimbursement,
    Ingestion,
    UserPreferences,
)
from core.services import extraction_ai, expense_workspace
from .test_application import ApplicationCase


class ExpenseWorkspaceTests(ApplicationCase):
    def receipt(self, **changes):
        values = {
            "company_uid": self.company.company_uid,
            "uploaded_by_user_uid": self.user.user_uid,
            "source_kind": "receipt",
            "merchant_name": "Atelier exemple",
            "expense_date": "2026-01-10",
            "expense_amount": Decimal("1234.56"),
            "expense_currency": "EUR",
        }
        return Ingestion.objects.create(**(values | changes))

    def test_documented_foreign_payment_advancer_and_reimbursement(self):
        note = self.receipt(expense_currency="", expense_amount=None)
        url = "/app/imports/" + note.ingestion_uid
        values = {"merchant_name": "Café exemple", "expense_date": "2024-05-03",
                  "expense_amount": "98.5", "expense_currency": "CZK", "expense_paid_by": "personal",
                  "expense_payer_name": "Camille Exemple", "expense_paid_eur": "3,92"}
        save = url + "/enregistrer-justificatif"
        self.assertEqual(self.client.post(save, values).status_code, 302)
        note.refresh_from_db()
        self.assertEqual((note.expense_amount, note.expense_currency, note.expense_paid_eur, note.expense_payer_name),
                         (Decimal("98.5"), "CZK", Decimal("3.92"), "Camille Exemple"))
        visible = self.client.get("/app/notes-de-frais", {"q": "Camille Exemple"})
        self.assertEqual(visible.context["paid_eur_total"], Decimal("3.92"))
        self.assertEqual(visible.context["paid_eur_count"], 1)
        self.assertContains(visible, "CZK")
        self.assertContains(self.client.get(url), "Camille Exemple")
        report = self.client.get("/app/rapports", {"dataset": "expenses", "format": "csv", "preset": "all"})
        self.assertIn("Camille Exemple", report.content.decode())
        for change in ({"expense_paid_eur": "-1"}, {"expense_paid_eur": "2.345"}, {"expense_currency": "ZZZ"},
                       {"expense_payer_name": "x" * 181}):
            self.assertEqual(self.client.post(save, values | change).status_code, 422)
        refund = url + "/remboursement"
        self.assertEqual(self.client.post(refund, {"reimbursement_status": "unknown"}).status_code, 302)
        self.assertEqual(self.client.post(refund, {"reimbursement_status": "paid", "paid_on": "2024-05-06"}).status_code, 302)
        self.assertEqual(self.client.post(save, values | {"expense_paid_eur": "4.15"}).status_code, 409)
        self.assertEqual(self.client.post(refund, {"reimbursement_status": "due"}).status_code, 302)
        self.assertEqual(self.client.post(save, values | {"expense_amount": "1.234", "expense_currency": "KWD",
                                                        "expense_paid_eur": ""}).status_code, 302)
        note.refresh_from_db()
        self.assertIsNone(note.expense_paid_eur)
        self.assertEqual(note.expense_amount, Decimal("1.234"))
        self.assertContains(self.client.get(url), "1,234")

    def test_provider_schema_fields_and_legacy_classification(self):
        data = {
            "supplier": {"legal_name": "Atelier exemple"},
            "expense": {
                "category": "Transport",
                "description": "Course en taxi",
                "paid_by": "personal",
                "purpose": "Visite du salon",
            },
        }
        for provider in extraction_ai.PROVIDERS:
            payload = extraction_ai.request_data(
                provider, "", "test-model", "texte", expense=True
            )[2]
            self.assertIn("paid_by", str(payload))
            self.assertNotIn(
                "paid_by",
                str(
                    extraction_ai.request_data(
                        provider, "", "test-model", "texte", sale=True
                    )[2]
                ),
            )
        parsed = extraction_ai.normalize_extraction(data, expense=True)
        self.assertEqual(parsed["expense"], data["expense"])
        self.assertNotIn("expense", extraction_ai.normalize_extraction(data))
        item = self.receipt(
            transcription_text="Ticket de train\nRestaurant",
            extraction_json=json_text(
                {
                    "lines": [{"description": "Sandwich"}],
                    "payment": {"method": "card"},
                }
            ),
        )
        facts = expense_workspace.details(item)
        self.assertEqual(
            (facts["category"], facts["method"], facts["payer"], facts["purpose"]),
            ("Repas", "card", "", ""),
        )
        self.assertEqual(facts["status"], "unknown")
        item.extraction_json = json_text(parsed)
        item.save()
        url = "/app/imports/" + item.ingestion_uid
        detail = self.client.get(url)
        self.assertContains(detail, 'value="Transport"')
        self.assertContains(detail, 'value="personal" selected')
        self.assertContains(detail, "Visite du salon")
        fields = {
            "merchant_name": "Atelier corrigé",
            "expense_date": "2026-01-10",
            "expense_amount": "1234.56",
            "expense_currency": "EUR",
            "expense_category": "Catégorie interne",
            "expense_description": "",
            "expense_paid_by": "",
        }
        self.assertEqual(
            self.client.post(url + "/enregistrer-justificatif", fields).status_code, 302
        )
        item.refresh_from_db()
        corrected = expense_workspace.details(item)
        self.assertEqual(
            (corrected["category"], corrected["purpose"], corrected["payer"]),
            ("Catégorie interne", "", ""),
        )
        self.assertNotIn("Lire le justificatif", self.client.get(url).content.decode())

    def test_reimbursement_dashboard_and_financial_scope(self):
        note = self.receipt(expense_paid_by="personal")
        url = "/app/imports/" + note.ingestion_uid
        reimbursement = url + "/remboursement"
        self.assertEqual(expense_workspace.reimbursement_state(note), "due")
        for invalid in ("", "2099-12-31"):
            self.assertEqual(
                self.client.post(
                    reimbursement, {"reimbursement_status": "paid", "paid_on": invalid}
                ).status_code,
                422,
            )
        self.assertFalse(ExpenseReimbursement.objects.exists())
        for _ in range(2):
            self.assertEqual(
                self.client.post(
                    reimbursement,
                    {"reimbursement_status": "paid", "paid_on": "2026-02-12"},
                ).status_code,
                302,
            )
        self.assertEqual(ExpenseReimbursement.objects.count(), 1)
        self.assertFalse(BankTransaction.objects.exists())
        edit_amount = {
            "merchant_name": "Atelier",
            "expense_date": "2026-01-10",
            "expense_amount": "32",
            "expense_currency": "EUR",
        }
        self.assertEqual(
            self.client.post(
                url + "/enregistrer-justificatif", edit_amount
            ).status_code,
            409,
        )
        for category, amount in [("salary", "450"), ("cfe", "90")]:
            data = {
                "standalone_category": category,
                "label": "Versement exemple",
                "amount": amount,
                "transaction_date": "2026-01-15",
                "direction": "outgoing",
            }
            self.assertEqual(
                self.client.post("/app/finances/transactions", data).status_code, 302
            )
        self.receipt(company_uid=self.other.company_uid, expense_amount=9000)
        self.receipt(
            expense_paid_by="company", expense_currency="USD", expense_amount=7
        )
        january = {"from": "2026-01-01", "to": "2026-01-31"}
        dashboard = self.client.get("/app", january)
        groups = dict(dashboard.context["expense_totals"])
        self.assertEqual(groups["EUR"]["paid"], Decimal("1234.56"))
        self.assertEqual(groups["USD"]["gross"], 7)
        self.assertEqual(
            sum(row["amount"] for row in dashboard.context["independent_outflows"]), 540
        )
        self.assertEqual(
            self.client.get("/app", {"from": "2026-02-01", "to": "2026-02-28"}).context[
                "expense_totals"
            ],
            [],
        )
        self.assertContains(
            self.client.get("/app/finances/transactions?depense=cfe"),
            'value="cfe" selected',
        )
        CompanyMember.objects.filter(
            company_uid=self.company.company_uid, user_uid=self.user.user_uid
        ).update(role="member")
        self.assertEqual(
            self.client.post(
                reimbursement, {"reimbursement_status": "due"}
            ).status_code,
            403,
        )
        self.assertNotContains(self.client.get("/app", january), "Saisir un salaire")
        note.uploaded_by_user_uid = uid()
        note.save()
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertNotIn(
            "EUR", dict(self.client.get("/app", january).context["expense_totals"])
        )

    def test_all_pages_search_and_filtered_progress(self):
        base = self.receipt(
            expense_category="Repas", expense_paid_by="personal", expense_amount=1
        )
        Ingestion.objects.bulk_create(
            [
                Ingestion(
                    company_uid=base.company_uid,
                    uploaded_by_user_uid=base.uploaded_by_user_uid,
                    source_kind="receipt",
                    merchant_name="Atelier exemple",
                    expense_category="Repas",
                    expense_date=base.expense_date,
                    expense_amount=1,
                    expense_currency="EUR",
                    expense_paid_by="personal",
                )
                for _ in range(201)
            ]
        )
        self.receipt(expense_category="Transport")
        params = {
            "q": "ATELIER",
            "categorie": "Repas",
            "from": "2026-01-10",
            "to": "2026-01-10",
            "statut": "due",
            "page": "2",
        }
        page = self.client.get(
            "/app/notes-de-frais", params, headers={"X-Expense-Fragment": "results"}
        )
        self.assertEqual(page.context["page"].paginator.count, 202)
        self.assertEqual(dict(page.context["expense_totals"])["EUR"]["due"], 202)
        self.assertEqual(len(page.context["items"]), 25)
        self.assertContains(page, "page=3")
        self.assertNotContains(page, "<!doctype")

    def test_company_number_page_shared_format_and_switching(self):
        note = self.receipt()
        UserPreferences.objects.create(
            user_uid=self.user.user_uid, number_grouping="none", number_decimal="dot"
        )
        page = "/app/entreprise/reglages/nombres"
        self.assertContains(self.client.get("/app/entreprise/reglages/paiement"), page)

        def save(group, point, company=None):
            return self.client.post(
                page,
                {
                    "number_grouping": group,
                    "number_decimal": point,
                    "company_uid": company or self.company.company_uid,
                },
            )

        self.assertEqual(save("dot", "comma", self.other.company_uid).status_code, 409)
        self.assertEqual(save("dot", "dot").status_code, 422)
        self.assertEqual(save("dot", "comma").status_code, 302)
        self.assertEqual(CompanyNumberFormat.objects.count(), 1)
        self.assertContains(self.client.get("/app/notes-de-frais"), "1.234,56")
        self.assertContains(
            self.client.get("/app/imports/" + note.ingestion_uid),
            'data-number-group="dot" data-number-decimal="comma"',
        )
        self.assertEqual(
            self.document().seller["number_display"],
            {"group": "dot", "decimal": "comma"},
        )
        member = CompanyMember.objects.get(
            company_uid=self.company.company_uid, user_uid=self.user.user_uid
        )
        member.role = "viewer"
        member.save()
        self.assertEqual(save("comma", "dot").status_code, 403)
        self.assertContains(self.client.get("/app/notes-de-frais"), "1.234,56")
        self.assertNotContains(
            self.client.get("/app/imports/" + note.ingestion_uid),
            'name="expense_amount"',
        )
        CompanyMember.objects.create(
            company_uid=self.other.company_uid,
            user_uid=self.user.user_uid,
            role="owner",
        )
        self.client.post(f"/app/entreprises/{self.other.company_uid}/selectionner")
        self.assertContains(
            self.client.get("/app"),
            'data-number-group="none" data-number-decimal="dot"',
        )
