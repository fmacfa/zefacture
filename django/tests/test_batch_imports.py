from io import BytesIO
from unittest.mock import patch
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from core.common import json_text, uid
from core.models import (
    BackgroundJob,
    Customer,
    CustomerProfile,
    Document,
    DocumentFile,
    Ingestion,
)
from core.services import jobs, files, extraction_ai
from .test_application import ApplicationCase


def photo(name, color):
    data = BytesIO()
    Image.new("RGB", (20, 20), color).save(data, format="PNG")
    return SimpleUploadedFile(name, data.getvalue(), content_type="image/png")


class BatchImportTests(ApplicationCase):
    def test_multiple_files_independent_result_retry_and_visible_exact_duplicate(self):
        lot = uid()
        response = self.client.post(
            "/app/imports",
            {
                "source_kind": "supplier_invoice",
                "file": [
                    photo("a.png", "red"),
                    SimpleUploadedFile("invalid.pdf", b"bad"),
                    photo("b.png", "blue"),
                    photo("again.png", "red"),
                ],
                "batch_uid": lot,
                "pipeline": "local",
            },
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["state"] for row in response.json()["files"]],
            ["imported", "failed", "imported", "duplicate"],
        )
        self.assertEqual(Ingestion.objects.filter(batch_uid=lot).count(), 2)
        self.assertEqual(BackgroundJob.objects.count(), 2)
        self.assertContains(
            self.client.get("/app/imports", {"lot": lot}), "Documents de ce lot"
        )

        def transcribe(item):
            item.transcription_text = (
                "Facture ABC-2040\nDate : 10/05/2026\nTOTAL TTC 120,00 EUR"
            )
            item.transcription_status = "done"
            item.save()

        with patch("core.services.ingestion.transcribe", side_effect=transcribe):
            self.assertTrue(jobs.work_one())
            self.assertTrue(jobs.work_one())
        self.assertEqual(
            Ingestion.objects.filter(
                extraction_model="local", source_invoice_ref="abc-2040"
            ).count(),
            2,
        )
        self.assertContains(
            self.client.get("/app/imports", {"lot": lot}), "Doublon possible"
        )
        self.assertContains(
            self.client.get("/app/imports/" + Ingestion.objects.first().ingestion_uid),
            "Comparez la référence",
        )
        replay = self.client.post(
            "/app/imports",
            {
                "source_kind": "supplier_invoice",
                "file": photo("copie.png", "red"),
                "pipeline": "local",
            },
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(replay.json()["files"][0]["state"], "duplicate")
        expense_replay = self.client.post(
            "/app/notes-de-frais",
            {"file": photo("achat.png", "red")},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(expense_replay.json()["files"][0]["state"], "duplicate")
        self.assertEqual(BackgroundJob.objects.count(), 2)

    def test_sale_preserves_buyer_original_number_and_receivable_without_reissue(self):
        link = self.client.post(
            "/app/imports",
            {
                "source_kind": "sales_invoice",
                "file": SimpleUploadedFile("sale.pdf", b"%PDF-synthetic-archived-sale"),
                "pipeline": "none",
            },
        )["Location"]
        self.assertContains(self.client.get(link), "Nom du client")
        invoice = {
            "buyer": {"legal_name": "Acheteur de test", "email": "buyer@example.test"},
            "invoice": {
                "number": "V-2025-ABC",
                "issue_date": "2025-04-12",
                "currency_code": "EUR",
            },
            "totals": {"total_including_tax": "120"},
            "lines": self.data()["lines"],
        }
        before = self.company.next_invoice_number
        result = self.client.post(
            link + "/creer-facture", {"extraction_json": json_text(invoice)}
        )
        self.assertEqual(result.status_code, 302)
        doc = Document.objects.get(number="V-2025-ABC")
        self.assertEqual(
            (doc.direction, doc.status, doc.amount_due, doc.buyer["name"]),
            ("outgoing", "sent", 120, "Acheteur de test"),
        )
        self.assertEqual(doc.crm_link.contact.name, "Acheteur de test")
        self.assertContains(self.client.get(link), f'href="{doc.url}"')
        self.company.refresh_from_db()
        self.assertEqual(before, self.company.next_invoice_number)
        self.assertEqual(files.issued_invoice_pdf(doc), b"%PDF-synthetic-archived-sale")
        self.assertEqual(files.generate_document(doc).kind, "source")
        duplicate = self.client.post(
            "/app/imports",
            {
                "source_kind": "sales_invoice",
                "file": SimpleUploadedFile("scan.pdf", b"%PDF-new-scan"),
            },
        )["Location"]
        self.client.post(
            duplicate + "/enregistrer", {"extraction_json": json_text(invoice)}
        )
        self.assertContains(self.client.get(duplicate), "Doublon possible")
        self.assertEqual(
            self.client.post(
                duplicate + "/creer-facture", {"extraction_json": json_text(invoice)}
            ).status_code,
            409,
        )
        self.assertEqual(
            Document.objects.filter(
                company_uid=self.company.company_uid, number=doc.number
            ).count(),
            1,
        )
        self.assertFalse(
            DocumentFile.objects.filter(
                document_uid=doc.document_uid, kind="facturx"
            ).exists()
        )

    def test_different_scans_match_on_invoice_facts_before_conversion(self):
        lot = uid()
        self.client.post(
            "/app/imports",
            {
                "file": [photo("un.png", "red"), photo("deux.png", "blue")],
                "batch_uid": lot,
                "source_kind": "supplier_invoice",
                "pipeline": "none",
            },
        )
        first, second = Ingestion.objects.order_by("pk")
        for item, name, total in (
            (first, "Atelier  Démo", "120,00"),
            (second, "ATELIER DÉMO", "120.0"),
        ):
            self.client.post(
                "/app/imports/" + item.ingestion_uid + "/enregistrer",
                {
                    "extraction_json": json_text(
                        {
                            "supplier": {"legal_name": name},
                            "invoice": {
                                "issue_date": "2025-04-12",
                                "currency_code": "EUR",
                            },
                            "totals": {"total_including_tax": total},
                        }
                    )
                },
            )
        self.assertContains(
            self.client.get("/app/imports", {"lot": lot}), "Doublon possible", count=2
        )
        self.assertContains(
            self.client.get("/app/imports/" + first.ingestion_uid), second.ingestion_uid
        )
        for bad in (
            {"supplier": ["archive"], "invoice": {"issue_date": []}, "totals": []},
            {"buyer": {}, "invoice": "?"},
        ):
            details = "/app/imports/" + first.ingestion_uid
            self.client.post(
                details + "/enregistrer", {"extraction_json": json_text(bad)}
            )
            self.assertEqual(self.client.get(details).status_code, 200)
            self.assertEqual(
                self.client.post(
                    details + "/creer-facture", {"extraction_json": json_text(bad)}
                ).status_code,
                422,
            )

    def test_shared_number_allowed_for_distinct_sellers_and_blocked_for_same_seller(
        self,
    ):
        def submit(index, kind, party, number, confirmed=False):
            response = self.client.post(
                "/app/imports",
                {
                    "source_kind": kind,
                    "file": SimpleUploadedFile(
                        "scan.pdf", f"%PDF-synthetic-{index}".encode()
                    ),
                    "pipeline": "none",
                },
            )
            return self.client.post(
                response["Location"] + "/creer-facture",
                {
                    "distinct": "1" if confirmed else "",
                    "extraction_json": json_text(
                        {
                            "buyer" if kind == "sales_invoice" else "supplier": {
                                "legal_name": party,
                                "email": "billing@example.test",
                            },
                            "invoice": {
                                "number": number,
                                "issue_date": "2025-04-12",
                                "currency_code": "EUR",
                            },
                            "totals": {"total_including_tax": "120"},
                            "lines": self.data()["lines"],
                        }
                    ),
                },
            )

        self.assertEqual(
            submit(1, "sales_invoice", "Client test", "F-123").status_code, 302
        )
        self.assertEqual(
            submit(2, "supplier_invoice", "Fournisseur A", "F-123").status_code, 302
        )
        self.assertEqual(
            submit(3, "supplier_invoice", "Fournisseur B", "F-123", True).status_code,
            302,
        )
        self.assertEqual(
            submit(4, "supplier_invoice", "Fournisseur A", "F-123", True).status_code,
            409,
        )
        self.assertEqual(
            submit(5, "supplier_invoice", "Fournisseur A", "F-124", True).status_code,
            302,
        )
        self.assertEqual(
            Customer.objects.filter(
                company_uid=self.company.company_uid, legal_name="Fournisseur A"
            ).count(),
            1,
        )
        self.assertEqual(
            CustomerProfile.objects.filter(
                company_uid=self.company.company_uid, relationship="supplier"
            ).count(),
            2,
        )

    def test_selected_ai_runs_after_ocr_with_buyer_schema(self):
        with patch(
            "core.services.extraction_ai.require_provider",
            return_value=("Test", "fake", "model"),
        ):
            self.client.post(
                "/app/imports",
                {
                    "source_kind": "sales_invoice",
                    "pipeline": "openai",
                    "file": photo("c.png", "green"),
                },
            )

        def transcript(item):
            item.transcription_text = "Texte fictif"
            item.transcription_status = "done"
            item.save()

        with (
            patch("core.services.ingestion.transcribe", side_effect=transcript),
            patch(
                "core.services.extraction_ai.extract",
                return_value=(
                    {"buyer": {"legal_name": "Test"}, "invoice": {"number": "T-1"}},
                    "test:model",
                ),
            ) as external,
        ):
            jobs.work_one()
        external.assert_called_once_with("openai", "Texte fictif", sale=True, company_uid=self.company.company_uid)
        self.assertEqual(Ingestion.objects.get().source_invoice_ref, "t-1")
        _, _, payload = extraction_ai.request_data(
            "openai", "fake", "test", "contenu", sale=True
        )
        self.assertIn("buyer", str(payload))
        self.assertNotIn('"supplier":', str(payload))

    def test_same_file_in_another_enterprise_does_not_disclose_or_prevent_upload(self):
        import hashlib

        f = photo("commun.png", "blue")
        digest = hashlib.sha256(f.read()).hexdigest()
        f.seek(0)
        hidden = Ingestion.objects.create(
            company_uid=self.other.company_uid,
            sha256=digest,
            original_filename="autre.png",
        )
        response = self.client.post(
            "/app/imports", {"file": f}, HTTP_ACCEPT="application/json"
        )
        self.assertEqual(response.json()["files"][0]["state"], "imported")
        self.assertNotContains(response, hidden.ingestion_uid)
