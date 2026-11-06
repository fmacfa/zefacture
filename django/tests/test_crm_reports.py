import io
from datetime import date
from decimal import Decimal
from openpyxl import load_workbook
from pypdf import PdfReader
from django.test import Client
from core.common import uid
from core.models import (
    Customer,
    CustomerProfile,
    CustomerContact,
    Opportunity,
    CrmActivity,
    CompanyMember,
    BankAccount,
    BankTransaction,
    InventoryItem,
    StockMovement,
    Document,
)
from core.services import crm, reports
from tests.test_application import ApplicationCase


class CrmTests(ApplicationCase):
    def test_double_activity_submission_queues_only_one_email(self):
        from core.models import Email

        data = {
            "activity_nonce": uid(),
            "kind": "email",
            "title": "Message unique",
            "body": "Un seul envoi",
        }
        first = crm.add_activity(self.customer, self.user, data)
        second = crm.add_activity(self.customer, self.user, data)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Email.objects.count(), 1)
        from core.common import BusinessError

        with self.assertRaises(BusinessError):
            crm.add_activity(self.customer, self.user, data | {"body": "Autre message"})

    def post(self, action, data, suffix=""):
        return self.client.post(
            f"/app/clients/{self.customer.customer_uid}/crm/{action}" + suffix, data
        )

    def test_prospect_contact_opportunity_task_and_customer_history(self):
        self.assertEqual(
            self.post(
                "profile",
                {
                    "relationship": "prospect",
                    "tags": "prioritaire",
                    "notes": "Projet de rénovation",
                    "assigned_to_user_uid": self.user.user_uid,
                },
            ).status_code,
            302,
        )
        self.assertEqual(
            self.post(
                "contact",
                {
                    "name": "Contact Exemple",
                    "email": "contact@example.test",
                    "position": "Achats",
                },
            ).status_code,
            302,
        )
        self.assertEqual(
            self.post(
                "opportunity",
                {
                    "title": "Projet exemple",
                    "stage": "proposal",
                    "amount": "1250.50",
                    "currency_code": "EUR",
                    "expected_close": "2026-12-31",
                },
            ).status_code,
            302,
        )
        opportunity = Opportunity.objects.get()
        self.assertEqual(opportunity.amount, Decimal("1250.50"))
        self.assertEqual(
            self.post(
                "opportunity",
                {
                    "title": opportunity.title,
                    "stage": "won",
                    "amount": opportunity.amount,
                    "currency_code": "EUR",
                },
                "/" + opportunity.opportunity_uid,
            ).status_code,
            302,
        )
        opportunity.refresh_from_db()
        self.assertIsNotNone(opportunity.closed_on)
        self.assertEqual(CustomerProfile.objects.get().relationship, "customer")
        self.assertEqual(
            self.post(
                "activity",
                {
                    "kind": "task",
                    "title": "Rappeler le contact",
                    "due_on": "2026-11-30",
                    "assigned_to_user_uid": self.user.user_uid,
                },
            ).status_code,
            302,
        )
        activity = CrmActivity.objects.get()
        self.assertEqual(
            self.post("complete", {}, "/" + activity.activity_uid).status_code, 302
        )
        activity.refresh_from_db()
        self.assertIsNotNone(activity.completed_at)
        self.document()
        response = self.client.get("/app/clients/" + self.customer.customer_uid)
        self.assertContains(response, "Contact Exemple")
        self.assertContains(response, "Projet exemple")
        self.assertContains(response, "Rappeler le contact")
        self.assertEqual(len(response.context["documents"]), 1)
        self.assertEqual(self.client.get("/app/crm").status_code, 200)

    def test_crm_scopes_links_members_and_read_only_permissions(self):
        other_contact = Customer.objects.create(
            company_uid=self.other.company_uid, legal_name="Invisible"
        )
        opportunity = crm.save_opportunity(
            other_contact, {"title": "Privée", "amount": 20, "stage": "qualified"}
        )
        self.assertEqual(
            self.post(
                "opportunity",
                {"title": "Vol", "stage": "won", "amount": 999},
                "/" + opportunity.opportunity_uid,
            ).status_code,
            404,
        )
        self.assertEqual(
            self.post(
                "profile", {"relationship": "prospect", "assigned_to_user_uid": uid()}
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.get("/app/clients/" + other_contact.customer_uid).status_code,
            404,
        )
        member = CompanyMember.objects.get(
            company_uid=self.company.company_uid, user_uid=self.user.user_uid
        )
        member.role = "viewer"
        member.save()
        self.assertEqual(self.client.get("/app/crm").status_code, 200)
        self.assertEqual(self.post("contact", {"name": "Forbidden"}).status_code, 403)
        self.assertFalse(CustomerContact.objects.exists())

    def test_crm_email_tracks_message_and_enforces_contact_scope(self):
        from core.models import Email

        contact = CustomerContact.objects.create(
            company_uid=self.other.company_uid,
            customer_uid=self.customer.customer_uid,
            name="Autre",
            email="other@example.test",
        )
        self.assertEqual(
            self.post(
                "activity",
                {
                    "kind": "email",
                    "title": "Bonjour",
                    "body": "Votre projet",
                    "contact_uid": contact.contact_uid,
                },
            ).status_code,
            404,
        )
        self.assertFalse(Email.objects.exists())
        response = self.post(
            "activity",
            {
                "kind": "email",
                "title": "Suivi de projet",
                "body": "Votre projet avance.",
            },
        )
        self.assertEqual(response.status_code, 302)
        activity, message = CrmActivity.objects.get(), Email.objects.get()
        self.assertEqual(activity.email_uid, message.email_uid)
        self.assertEqual(message.recipient_email, self.customer.email)
        self.assertEqual(message.transport, "outbox")


class ReportingTests(ApplicationCase):
    def report_request(self, params):
        response = self.client.get("/app/rapports", params)
        self.assertEqual(response.status_code, 200, response.content[:500])
        return response

    def issued(self, day, price, currency="EUR", kind="invoice", direction="outgoing"):
        document = self.document(
            kind,
            issue_date=day,
            due_date=day,
            currency_code=currency,
            lines=[
                {
                    "description": "Facturation exemple",
                    "quantity": "1",
                    "unit_price_excluding_tax": str(price),
                    "vat_rate": "20",
                }
            ],
        )
        document.direction, document.status = (
            direction,
            "sent" if direction == "outgoing" else "received",
        )
        document.save()
        return document

    def test_comparison_deducts_credits_and_separates_currencies_and_payments(self):
        self.issued("2026-10-01", 100)
        invoice = self.issued("2026-11-01", 200)
        credit = self.document(
            "credit_note",
            source_invoice_uid=invoice.document_uid,
            issue_date="2026-11-02",
            lines=[
                {
                    "description": "Avoir",
                    "quantity": 1,
                    "unit_price_excluding_tax": 20,
                    "vat_rate": 20,
                }
            ],
        )
        credit.status = "sent"
        credit.save()
        self.issued("2026-11-01", 40, direction="incoming")
        self.issued("2026-11-03", 80, currency="USD")
        self.document(issue_date="2026-11-01")  # draft excluded
        excluded = self.issued("2026-11-02", 500)
        excluded.status = "simulated"
        excluded.save()
        account = BankAccount.objects.create(
            company_uid=self.company.company_uid, name="CHF", currency_code="CHF"
        )
        BankTransaction.objects.create(
            company_uid=self.company.company_uid,
            account_uid=account.account_uid,
            amount=33,
            direction="incoming",
            transaction_date="2026-11-03",
        )
        response = self.report_request(
            {
                "from": "2026-11-01",
                "to": "2026-11-30",
                "compare": "custom",
                "compare_from": "2026-10-01",
                "compare_to": "2026-10-31",
            }
        )
        metrics = {
            (m["key"], m["currency"]): m for m in response.context["report"]["metrics"]
        }
        sales = metrics[("sales", "EUR")]
        self.assertEqual(
            (sales["value"], sales["before"], sales["difference"], sales["percentage"]),
            (180, 100, 80, 80),
        )
        self.assertEqual(metrics[("balance", "EUR")]["value"], 140)
        self.assertEqual(metrics[("sales", "USD")]["value"], 80)
        self.assertEqual(metrics[("incoming", "CHF")]["value"], 33)
        self.assertEqual(metrics[("incoming", "EUR")]["value"], 0)

    def test_period_leap_year_previous_custom_invalid_and_all_dates(self):
        selected = reports.period(
            {"from": "2024-02-29", "to": "2024-02-29", "compare": "year"}
        )
        self.assertEqual(selected.compare_start, date(2023, 2, 28))
        selected = reports.period({"from": "2026-03-01", "to": "2026-03-31"})
        self.assertEqual((selected.compare_end - selected.compare_start).days, 30)
        self.assertEqual(reports.period({"preset": "all"}).comparison, "none")
        for params in (
            {"from": "2026-12-01", "to": "2026-01-01"},
            {"from": "2026-02-30"},
            {"compare": "custom"},
            {"compare": "bad"},
        ):
            self.assertEqual(self.client.get("/app/rapports", params).status_code, 422)

    def test_end_date_includes_customer_created_at_2359_and_no_other_company(self):
        Customer.objects.filter(pk=self.customer.pk).update(
            created_at="2026-11-03 22:59:59"
        )
        Customer.objects.create(
            company_uid=self.company.company_uid,
            legal_name="Début du lendemain à Paris",
            created_at="2026-11-03 23:00:00",
        )
        Customer.objects.create(
            company_uid=self.other.company_uid,
            legal_name="HIDDEN OTHER COMPANY",
            created_at="2026-11-03 12:00:00",
        )
        response = self.report_request(
            {
                "dataset": "customers",
                "from": "2026-11-03",
                "to": "2026-11-03",
                "compare": "none",
            }
        )
        self.assertEqual(response.context["rows"].paginator.count, 1)
        self.assertNotContains(response, "HIDDEN OTHER COMPANY")

    def test_stock_reconstructed_at_period_end_and_zero_stock_is_numeric(self):
        item = InventoryItem.objects.create(
            company_uid=self.company.company_uid, name="Article", stock_tracking=1
        )
        for day, direction, quantity in (
            ("2026-10-01", "in", 10),
            ("2026-10-25", "out", 3),
            ("2026-11-01", "out", 2),
        ):
            StockMovement.objects.create(
                company_uid=self.company.company_uid,
                item_uid=item.item_uid,
                occurred_on=day,
                movement_type=direction,
                quantity=quantity,
            )
        response = self.report_request(
            {"dataset": "inventory", "from": "2026-10-01", "to": "2026-10-31"}
        )
        self.assertEqual(response.context["rows"][0][3], 7)
        self.assertEqual(item.stock, 5)

    def test_exports_excel_are_complete_literal_and_preserve_identifiers(self):
        Customer.objects.filter(pk=self.customer.pk).update(
            legal_name='=HYPERLINK("https://example.test")', postal_code="00123"
        )
        CustomerContact.objects.bulk_create(
            [
                CustomerContact(
                    company_uid=self.company.company_uid,
                    customer_uid=self.customer.customer_uid,
                    name=f"Contact {i:03}",
                )
                for i in range(75)
            ]
        )
        result = self.report_request(
            {"dataset": "contacts", "preset": "all", "format": "xlsx"}
        )
        workbook = load_workbook(io.BytesIO(result.content))
        self.assertEqual(workbook["Données"].max_row, 76)
        result = self.report_request(
            {"dataset": "customers", "preset": "all", "format": "xlsx"}
        )
        sheet = load_workbook(io.BytesIO(result.content))["Données"]
        self.assertEqual(sheet["B2"].data_type, "s")
        self.assertTrue(sheet["B2"].value.startswith("="))
        self.assertEqual(sheet["H2"].value, "00123")
        csv = self.report_request(
            {"dataset": "customers", "preset": "all", "format": "csv"}
        ).content.decode("utf-8-sig")
        self.assertIn("'=HYPERLINK", csv)

    def test_every_dataset_renders_and_has_downloadable_excel(self):
        self.document()
        for name in reports.DATASETS:
            with self.subTest(dataset=name):
                self.report_request({"dataset": name, "preset": "all"})
                response = self.report_request(
                    {"dataset": name, "preset": "all", "format": "xlsx"}
                )
                self.assertIn(
                    "Données", load_workbook(io.BytesIO(response.content)).sheetnames
                )

    def test_excel_long_activity_keeps_the_entire_message(self):
        body = "Texte long et intégral. " * 2000
        CrmActivity.objects.create(
            company_uid=self.company.company_uid,
            customer_uid=self.customer.customer_uid,
            created_by_user_uid=self.user.user_uid,
            kind="note",
            title="Texte long",
            body=body,
        )
        response = self.report_request(
            {"dataset": "activities", "preset": "all", "format": "xlsx"}
        )
        book = load_workbook(io.BytesIO(response.content))
        self.assertIn("Textes longs", book.sheetnames)
        reconstructed = "".join(
            row[3].value for row in list(book["Textes longs"].rows)[1:]
        )
        self.assertEqual(reconstructed, body)

    def test_new_client_can_be_registered_as_individual_prospect(self):
        response = self.client.post(
            "/app/clients/nouveau",
            {
                "type": "individual",
                "legal_name": "Prospect particulier",
                "country_code": "FR",
                "relationship": "prospect",
            },
        )
        self.assertEqual(response.status_code, 302)
        prospect = Customer.objects.get(legal_name="Prospect particulier")
        self.assertEqual(
            CustomerProfile.objects.get(
                customer_uid=prospect.customer_uid
            ).relationship,
            "prospect",
        )
        response = self.client.get(
            "/app/clients", {"relationship": "prospect", "type": "individual"}
        )
        self.assertContains(response, "Prospect particulier")
        self.assertNotContains(response, self.customer.legal_name)

    def test_pdf_report_contains_period_comparison_and_data(self):
        self.issued("2026-11-03", 987)
        response = self.report_request(
            {
                "dataset": "summary",
                "from": "2026-11-01",
                "to": "2026-11-30",
                "compare": "year",
                "format": "pdf",
            }
        )
        pdf = PdfReader(io.BytesIO(response.content))
        text = "\n".join(page.extract_text() for page in pdf.pages)
        self.assertIn("987", text)
        self.assertIn("01/11/2025", text)
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_report_and_exports_require_financial_permission_and_scope(self):
        member = CompanyMember.objects.get(
            company_uid=self.company.company_uid, user_uid=self.user.user_uid
        )
        member.role = "viewer"
        member.save()
        self.assertEqual(
            self.client.get("/app/rapports", {"format": "xlsx"}).status_code, 403
        )
        self.assertEqual(self.client.get("/app").status_code, 200)
        self.assertEqual(Client().get("/app/rapports").status_code, 302)
