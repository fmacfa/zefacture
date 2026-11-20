from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from weasyprint import HTML

from core.common import BusinessError
from core.models import (
    BankAccount,
    BankStatementImport,
    BankTransaction,
    Company,
    Customer,
)
from core.services import extraction_ai, statement_ai, statement_batches
from .test_application import ApplicationCase
from .test_bank_statements import ofx


class BankWorkspaceTests(ApplicationCase):
    url = "/app/finances/transactions"

    def setUp(self):
        super().setUp()
        self.account = BankAccount.objects.create(
            company_uid=self.company.company_uid, name="Compte courant"
        )
        self.key = self.client.get(self.url).context["batch_key"]

    def upload(self, files, **extra):
        return self.client.post(
            self.url + "/import",
            {
                "account_uid": self.account.account_uid,
                "batch_key": self.key,
                "file": [SimpleUploadedFile(n, b) for n, b in files],
                **extra,
            },
        )

    def drain(self):
        for _ in range(20):
            if not statement_batches.work_one():
                return
        self.fail("La file de relevés n’est pas arrivée à son terme.")

    def test_mixed_files_retry_duplicates_and_review(self):
        csv = b"date;libelle;montant\n2026-05-12;Salaire;-50\n"
        pdf = HTML(string="<pre>13/05/2026  Train  -12,50</pre>").write_pdf()
        files = [
            ("mai.csv", csv),
            ("juin.qfx", ofx([("id-fictif", "Loyer", "-75")])),
            ("page.pdf", pdf),
            ("copie.csv", csv),
            ("vide.csv", b""),
            ("mauvais.pdf", b"illisible"),
        ]
        first = self.upload(files)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(self.upload(files).url, first.url)
        self.assertEqual(BankStatementImport.objects.count(), 6)
        self.drain()
        batch = self.client.get(first.url)
        self.assertContains(batch, "Fichier en double")
        self.assertContains(batch, "Fichier vide ou format incompatible")
        self.assertContains(batch, "Vérifier les opérations")
        self.assertEqual(BankTransaction.objects.count(), 2)
        item = BankStatementImport.objects.get(filename="page.pdf")
        url = reverse("bank_statement_review", args=[item.item_uid])
        self.assertEqual(self.client.get(url).context["rows"][0]["label"], "Train")
        form = {
            "slot": "0",
            "selected": "0",
            "date_0": "2026-05-13",
            "label_0": "Train",
            "amount_0": "12.50",
            "direction_0": "outgoing",
        }
        for _ in range(2):
            self.assertRedirects(self.client.post(url, form), first.url)
        self.account.refresh_from_db()
        self.assertEqual(self.account.current_balance, Decimal("-137.50"))
        self.assertFalse(BankStatementImport.objects.exclude(contents=None).exists())
        self.assertFalse(
            BankStatementImport.objects.get(pk=item.pk).result.get("transcript")
        )
        self.assertEqual(self.upload([("changement.csv", csv)]).status_code, 409)
        foreign = BankAccount.objects.create(company_uid=self.other.company_uid)
        self.assertEqual(
            self.upload(files, account_uid=foreign.account_uid).status_code, 404
        )

    def test_two_pdfs_reviewed_independently_in_reverse_order(self):
        files = [
            (
                "janvier.pdf",
                HTML(string="<pre>01/01/2026 Titre  -12,50</pre>").write_pdf(),
            ),
            (
                "février.pdf",
                HTML(string="<pre>01/02/2026 Titre  -13,50</pre>").write_pdf(),
            ),
        ]
        self.upload(files)
        self.drain()
        self.assertFalse(BankTransaction.objects.exists())
        self.assertEqual(BankStatementImport.objects.filter(status="review").count(), 2)
        for item in BankStatementImport.objects.order_by("-position"):
            url = reverse("bank_statement_review", args=[item.item_uid])
            row = self.client.get(url).context["rows"][0]
            self.assertEqual(
                self.client.post(
                    url,
                    {
                        "slot": "0",
                        "selected": "0",
                        "date_0": row["date"],
                        "label_0": row["label"],
                        "amount_0": row["amount"],
                        "direction_0": row["direction"],
                    },
                ).status_code,
                302,
            )
        self.account.refresh_from_db()
        self.assertEqual(self.account.current_balance, Decimal("-26"))

    def test_parts_company_and_expiry(self):
        form = [("mai.ofx", ofx([("un", "Mai", "42")]))]
        first = self.upload(form)
        self.assertEqual(
            self.upload(
                [("juin.csv", b"date;libelle;montant\n2026-06-01;Juin;2")], offset=1
            ).url,
            first.url,
        )
        self.assertEqual(self.upload(form, offset=50).status_code, 422)
        item = BankStatementImport.objects.first()
        BankStatementImport.objects.update(user_uid="autre-utilisateur")
        self.assertEqual(self.client.get(first.url).status_code, 404)
        self.assertEqual(
            self.client.get(
                reverse("bank_statement_review", args=[item.item_uid])
            ).status_code,
            404,
        )
        self.drain()
        self.assertFalse(BankTransaction.objects.exists())
        BankStatementImport.objects.update(
            created_at=timezone.now() - timedelta(days=8)
        )
        self.assertFalse(statement_batches.work_one())
        self.assertFalse(BankStatementImport.objects.exists())

    def test_ai_processes_each_page_and_falls_back(self):
        pages = [
            {
                "number": 1,
                "text": "Date Débit Crédit\n01/05/2026 Train 15,00 200,00",
                "rows": [
                    {
                        "page": 1,
                        "source_line": 2,
                        "original": "01/05/2026 Train 15,00 200,00",
                        "date": "2026-05-01",
                        "label": "Train",
                        "amount": "",
                        "direction": "",
                    }
                ],
            },
            {
                "number": 2,
                "text": "02/05/2026 Paie -51,00",
                "rows": [
                    {
                        "page": 2,
                        "source_line": 1,
                        "original": "Paie",
                        "date": "2026-05-02",
                        "label": "Paie",
                        "amount": "51.00",
                        "direction": "outgoing",
                    }
                ],
            },
        ]
        data = [
            {
                "line": 2,
                "date": "2026-05-01",
                "label": "Train",
                "amount": "15.00",
                "direction": "outgoing",
            }
        ]
        with (
            patch.object(
                extraction_ai, "choices", return_value=[("google", "Google Gemini")]
            ),
            patch("core.services.statement_pdf.read_pages", return_value=pages),
            patch.object(
                extraction_ai,
                "extract",
                side_effect=[
                    (data, "google:modele-test"),
                    BusinessError("Service indisponible."),
                ],
            ) as api,
        ):
            self.upload([("ocr.pdf", b"%PDF-exemple")], provider="google")
            self.drain()
        self.assertEqual(api.call_count, 2)
        self.assertTrue(
            all(
                call.kwargs
                == {"statement": True, "company_uid": self.company.company_uid}
                for call in api.call_args_list
            )
        )
        item = BankStatementImport.objects.get()
        self.assertEqual(item.status, "review")
        self.assertIn("page 2", item.message)
        self.assertEqual(
            [(r["amount"], r["source_key"]) for r in item.result["rows"]],
            [("15.00", 0), ("51.00", 1)],
        )
        self.assertTrue(item.result["rows"][0]["ai_completed"])
        self.assertFalse(BankTransaction.objects.exists())

    def test_ai_only_suggests_supported_source_rows(self):
        page = {
            "number": 1,
            "text": "Solde au 01/05/2026 777,00\nPaiement Démo 2026-05-02 19,00\nTexte sans opération",
            "rows": [],
        }
        row = {
            "line": 2,
            "date": "2026-05-02",
            "label": "Paiement Démo",
            "amount": "19.00",
            "direction": "outgoing",
        }
        with patch.object(
            extraction_ai,
            "extract",
            return_value=(
                [row | {"line": 1}, row | {"amount": "9000"}, row | {"line": 3}, row],
                "model",
            ),
        ):
            statement_ai.complete_page(page, "google", self.company.company_uid)
        self.assertEqual(len(page["rows"]), 1)
        self.assertEqual(page["rows"][0]["source_key"], "ocr:1:2")

    def test_manual_entry_without_account_keeps_its_euro_amount(self):
        BankTransaction.objects.create(
            company_uid=self.company.company_uid,
            account_uid=None,
            transaction_date="2026-05-02",
            direction="incoming",
            amount=1234,
            label="Dépôt manuel",
        )
        response = self.client.get(self.url)
        self.assertEqual(
            dict(response.context["bank_totals"])["EUR"]["incoming"], Decimal("1234")
        )
        self.assertContains(response, "EUR</td>")

    def test_overview_filters_all_pages_and_separates_currencies(self):
        usd = BankAccount.objects.create(
            company_uid=self.company.company_uid, currency_code="USD"
        )

        def tx(account, amount, **kwargs):
            return BankTransaction.objects.create(
                company_uid=account.company_uid,
                account_uid=account.account_uid,
                transaction_date="2026-05-02",
                direction="outgoing",
                amount=amount,
                label="Test",
                **kwargs,
            )

        from core.models import Document

        invoice = Document.objects.create(
            company_uid=self.company.company_uid,
            number="ACHAT-TEST",
            direction="incoming",
        )
        tx(self.account, 10, document_uid=invoice.document_uid)
        tx(self.account, 5, standalone_category="salary")
        tx(self.account, 7)
        tx(usd, 100)
        tx(usd, 200, status="pending")
        foreign = BankAccount.objects.create(company_uid=self.other.company_uid)
        tx(foreign, 999)
        reply = self.client.get(
            self.url,
            {"traitement": "unlinked", "from": "2026-05-01", "to": "2026-05-31"},
        )
        self.assertEqual(reply.context["transactions"].paginator.count, 4)
        self.assertEqual(
            reply.context["bank_counts"],
            {"all": 5, "unlinked": 4, "invoice": 1, "standalone": 1, "pending": 2},
        )
        totals = dict(reply.context["bank_totals"])
        self.assertEqual((totals["EUR"]["net"], totals["USD"]["net"]), (-12, -100))
        self.assertContains(
            self.client.get(self.url, {"traitement": "invoice"}), invoice.url
        )
        self.assertEqual(
            self.client.get(
                self.url, {"compte": usd.account_uid, "to": "2026-04-30"}
            ).context["bank_counts"]["all"],
            0,
        )


class ForeignCompanyTests(ApplicationCase):
    def test_country_names_codes_and_foreign_company_creation(self):
        page = self.client.get("/app/clients/nouveau")
        self.assertContains(page, "Allemagne (DE)")
        self.assertContains(page, "Brésil (BR)")
        data = {
            "legal_name": "Société de démonstration",
            "type": "business",
            "country_code": "Allemagne (DE)",
            "vat_number": "DE000000000",
        }
        res = self.client.post("/app/clients/nouveau", data)
        self.assertEqual(res.status_code, 302)
        customer = Customer.objects.get(legal_name=data["legal_name"])
        self.assertEqual((customer.country_code, customer.siren or ""), ("DE", ""))
        for name in ("bresil", "BR"):
            self.assertEqual(
                self.client.post(
                    f"/app/clients/{customer.customer_uid}/modifier",
                    data | {"country_code": name},
                ).status_code,
                302,
            )
            customer.refresh_from_db()
            self.assertEqual(customer.country_code, "BR")
        invalid = self.client.post(
            "/app/clients/nouveau", data | {"country_code": "Allemagne (FR)"}
        )
        self.assertContains(invalid, "Choisissez un pays")
        self.assertEqual(
            self.client.post(
                "/app/entreprises/creer",
                {"legal_name": "Exemple GmbH", "country_code": "Allemagne"},
            ).status_code,
            302,
        )
        self.assertEqual(
            Company.objects.get(legal_name="Exemple GmbH").country_code, "DE"
        )

    @override_settings(EXTERNAL_SERVICES_DISABLED=False)
    def test_vies_result_is_readable_and_never_overwrites(self):
        self.customer.country_code = "DE"
        self.customer.vat_number = "DE000000000"
        self.customer.save()
        url = f"/app/clients/{self.customer.customer_uid}/annuaire"
        with patch("requests.Session.post") as post:
            response = post.return_value
            response.status_code = 200
            response.json.return_value = {
                "countryCode": "DE",
                "vatNumber": "000000000",
                "valid": True,
                "requestDate": "2026-05-12T10:00:00Z",
                "name": "---",
                "address": "---",
                "traderNameMatch": "NOT_PROCESSED",
            }
            reply = self.client.post(url, {"lookup": "vies"})
            self.assertContains(reply, "Numéro de TVA valide")
            self.assertContains(reply, "ni raison sociale ni adresse")
            self.assertNotContains(reply, "NOT_PROCESSED")
            self.assertNotContains(reply, "---")
            self.assertContains(
                reply, f"/app/clients/{self.customer.customer_uid}/modifier"
            )
            response.json.return_value = {"valid": False}
            self.assertContains(
                self.client.post(url, {"lookup": "vies"}), "Numéro de TVA non valide"
            )
            response.json.return_value = {"userErrors": [{"error": "MS_UNAVAILABLE"}]}
            self.assertEqual(self.client.post(url, {"lookup": "vies"}).status_code, 502)
        self.customer.refresh_from_db()
        self.assertNotEqual(self.customer.legal_name, "---")
