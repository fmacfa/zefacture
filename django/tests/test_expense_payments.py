import csv
from io import BytesIO, StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import load_workbook
from pypdf import PdfReader

from core.common import json_text
from core.models import CompanyMember, Ingestion
from core.services.documents import save_document
from core.services.receipt_extraction import suggested_payment
from .test_application import ApplicationCase


class ExpensePaymentTests(ApplicationCase):
    def receipt(self, **values):
        return Ingestion.objects.create(
            company_uid=self.company.company_uid,
            uploaded_by_user_uid=self.user.user_uid,
            source_kind="receipt",
            original_filename="ticket-test.pdf",
            **values,
        )

    def test_legacy_description_payment_and_all_report_formats(self):
        item = self.receipt(
            expense_description="Visite du chantier",
            expense_date="2026-10-14",
            expense_amount="24.30",
            transcription_text="Mode de paiement : CB\nTOTAL TTC 24,30 EUR",
        )
        url = f"/app/imports/{item.ingestion_uid}"
        self.assertContains(self.client.get(url), "Visite du chantier")
        self.assertContains(
            self.client.get(url), "Le justificatif mentionne « Carte bancaire »."
        )
        self.assertEqual(
            self.client.post(
                url + "/reglement",
                {"expense_payment_method": "card", "expense_paid_by": "personal"},
            ).status_code,
            302,
        )
        item.refresh_from_db()
        self.assertEqual(item.expense_description, "Visite du chantier")
        self.assertEqual(
            self.client.post(
                url + "/reglement",
                {
                    "expense_payment_method": "inconnu",
                    "expense_description": "Non enregistré",
                },
            ).status_code,
            422,
        )
        self.assertContains(self.client.get("/app/notes-de-frais"), "Fonds personnels")
        params = {"dataset": "expenses", "preset": "all"}

        def exported(fmt):
            response = self.client.get("/app/rapports", {**params, "format": fmt})
            self.assertEqual(response.status_code, 200)
            return response.content

        table = list(
            csv.reader(StringIO(exported("csv").decode("utf-8-sig")), delimiter=";")
        )
        self.assertIn("Motif de la dépense", table[0])
        self.assertIn("Visite du chantier", table[1])
        self.assertIn("Carte bancaire", table[1])
        self.assertIn(
            "Fonds personnels",
            next(
                load_workbook(BytesIO(exported("xlsx"))).active.iter_rows(
                    min_row=2, values_only=True
                )
            ),
        )
        pdf = " ".join(
            p.extract_text() or "" for p in PdfReader(BytesIO(exported("pdf"))).pages
        )
        self.assertIn("Visite du chantier", " ".join(pdf.split()))
        self.assertEqual(self.client.post(url + "/archiver").status_code, 302)
        self.assertEqual(
            self.client.post(
                url + "/reglement", {"expense_payment_method": "cash"}
            ).status_code,
            409,
        )
        self.assertContains(self.client.get(url), "Fonds personnels")

    def test_receipt_and_linked_invoice_share_one_followup_without_changing_original(
        self,
    ):
        payload = {
            "source_kind": "supplier_invoice",
            "pipeline": "none",
            "file": SimpleUploadedFile("achat.pdf", b"%PDF-original-test-frais"),
            "expense_payment_method": "card",
            "expense_paid_by": "personal",
            "expense_description": "Visite fournisseur",
        }
        detail = self.client.post("/app/imports", payload)["Location"]
        item = Ingestion.objects.get()
        result = self.client.post(
            detail + "/creer-facture",
            {
                "extraction_json": json_text(
                    {
                        "supplier": {"legal_name": "Atelier exemple"},
                        "invoice": {
                            "number": "TEST-421",
                            "issue_date": "2026-10-14",
                            "currency_code": "EUR",
                        },
                        "lines": self.data()["lines"],
                    }
                )
            },
        )
        self.assertEqual(result.status_code, 302)
        item.refresh_from_db()
        doc_url = result["Location"]
        self.assertContains(self.client.get(doc_url), "Visite fournisseur")
        self.assertEqual(
            self.client.post(
                f"/app/documents/{item.document_uid}/suivi/reglement",
                {
                    "expense_description": "Achats pour le chantier",
                    "expense_payment_method": "transfer",
                },
            ).status_code,
            302,
        )
        self.assertContains(self.client.get(detail), "Achats pour le chantier")
        self.assertEqual(
            self.client.get(detail + "/telecharger").content,
            b"%PDF-original-test-frais",
        )
        self.assertEqual(self.client.post(doc_url + "/accepter").status_code, 302)
        self.assertEqual(
            self.client.post(
                doc_url + "/payer", {"expense_payment_method": "cheque"}
            ).status_code,
            302,
        )
        item.refresh_from_db()
        self.assertEqual(item.expense_payment_method, "cheque")
        self.assertEqual(item.expense_paid_by, "personal")

    def test_settlement_on_native_invoice_preserves_balance_and_planned_method(self):
        document = save_document(
            self.company,
            self.user,
            "invoice",
            self.data(number="FOU-TEST", payment_method_code="30"),
            direction="incoming",
        )
        balance = document.amount_due
        url = f"/app/documents/{document.document_uid}/suivi/reglement"
        self.assertEqual(
            self.client.post(
                url,
                {
                    "expense_payment_method": "cash",
                    "expense_description": "Achat atelier",
                },
            ).status_code,
            302,
        )
        document.refresh_from_db()
        self.assertEqual(
            (
                document.expense_payment_method,
                document.payment_method_code,
                document.amount_due,
            ),
            ("cash", "30", balance),
        )
        CompanyMember.objects.filter(
            company_uid=self.company.company_uid, user_uid=self.user.user_uid
        ).update(role="member")
        self.assertEqual(
            self.client.post(url, {"expense_payment_method": "card"}).status_code, 403
        )
        self.assertEqual(self.client.get(url).status_code, 405)
        private = Ingestion.objects.create(
            company_uid=self.company.company_uid,
            uploaded_by_user_uid="0" * 32,
            source_kind="receipt",
            expense_description="Mission interne",
        )
        self.assertEqual(
            self.client.post(
                f"/app/imports/{private.ingestion_uid}/reglement",
                {"expense_description": "Écrasé"},
            ).status_code,
            404,
        )
        private.refresh_from_db()
        self.assertEqual(private.expense_description, "Mission interne")

    def test_suggestions_do_not_infer_business_purpose_or_payer(self):
        self.assertEqual(suggested_payment("CB : 12,00 EUR"), "Carte bancaire")
        self.assertEqual(suggested_payment("Cartes acceptées : Visa\nCB refusée"), "")
        self.assertEqual(suggested_payment("Carte bancaire\nEspèces"), "")
