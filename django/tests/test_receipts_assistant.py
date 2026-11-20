import io
import json
import os
import shutil
from decimal import Decimal
from secrets import token_urlsafe
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from PIL import Image, ImageDraw, ImageFont

from core.common import BusinessError, digest
from core.models import ApiKey, BackgroundJob, Document, Ingestion
from core.services import extraction_ai, jobs
from core.services.receipt_extraction import local_suggestion
from .test_application import ApplicationCase


OCR_TEXT = (
    "CAFÉ DES EXEMPLES\nTICKET DE CAISSE\n17/11/2026\n"
    "SOUS-TOTAL 35,00 EUR\nTOTAL TVA 4,00 EUR\nTOTAL TTC 39,00 EUR"
)
PROVIDER_FIELDS = {
    "supplier": {"legal_name": "Café des Exemples"},
    "invoice": {"number": "", "issue_date": "2026-11-17", "currency_code": "EUR"},
    "totals": {"total_including_tax": "39,00", "total_vat": "4.00"},
    "lines": [
        {
            "description": "Repas",
            "quantity": 1,
            "unit_price_excluding_tax": "10.00",
            "vat_rate": "10",
        },
        {"description": "Illisible", "quantity": 1, "unit_price_excluding_tax": None},
    ],
    "unknown": "ignore this field",
}


def fake_http(payload, code=200):
    response = Mock(status_code=code)
    response.iter_content.return_value = [json.dumps(payload).encode()]
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    return context


class SuggestionTests(SimpleTestCase):
    def test_receipt_fields_and_explicit_total(self):
        result = local_suggestion(OCR_TEXT)
        self.assertEqual(result["supplier"]["legal_name"], "CAFÉ DES EXEMPLES")
        self.assertEqual(
            result["invoice"], {"issue_date": "2026-11-17", "currency_code": "EUR"}
        )
        self.assertEqual(
            result["totals"], {"total_including_tax": "39", "total_vat": "4"}
        )

    def test_ambiguous_totals_dates_and_currency_remain_empty(self):
        result = local_suggestion(
            "TOTAL TTC 17,00 USD\nNET À PAYER 18,00 EUR\n"
            "2026-11-17\n2026-11-18\nTVA 10 % 1,80"
        )
        self.assertEqual(result["invoice"], {"issue_date": "", "currency_code": ""})
        self.assertEqual(result["totals"], {"total_including_tax": "", "total_vat": ""})
        result = local_suggestion("Facture\nTOTAL HT 12,00\nTOTAL TVA 2,40")
        self.assertEqual(result["totals"]["total_including_tax"], "")
        self.assertEqual(result["totals"]["total_vat"], "2.4")


@override_settings(EXTERNAL_SERVICES_DISABLED=False)
class ProviderTests(SimpleTestCase):
    def test_text_only_payload_and_structured_response_for_each_provider(self):
        structured = json.dumps(PROVIDER_FIELDS)
        replies = {
            "openrouter": {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": structured}}
                ]
            },
            "openai": {
                "status": "completed",
                "output": [
                    {"type": "reasoning"},
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": structured}],
                    },
                ],
            },
            "google": {
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "parts": [
                                {"text": "private reasoning", "thought": True},
                                {"text": structured},
                            ]
                        },
                    }
                ],
            },
            "anthropic": {
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": structured}],
            },
        }
        urls = {
            "openrouter": "https://openrouter.ai/api/v1/chat/completions",
            "openai": "https://api.openai.com/v1/responses",
            "google": "https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent",
            "anthropic": "https://api.anthropic.com/v1/messages",
        }
        for provider, reply in replies.items():
            prefix = extraction_ai.PROVIDERS[provider][1]
            key = token_urlsafe(32)
            with (
                self.subTest(provider=provider),
                patch.dict(
                    os.environ,
                    {prefix + "_API_KEY": key, prefix + "_MODEL": "test-model"},
                ),
                patch("requests.Session.post", return_value=fake_http(reply)) as post,
            ):
                data, model = extraction_ai.extract(provider, OCR_TEXT)
                self.assertEqual(
                    data["totals"], {"total_including_tax": "39", "total_vat": "4"}
                )
                self.assertEqual(len(data["lines"]), 1)
                self.assertNotIn("number", data["invoice"])
                self.assertNotIn("unknown", data)
                self.assertEqual(model, provider + ":test-model")
                post.assert_called_once()
                url, options = post.call_args.args[0], post.call_args.kwargs
                self.assertEqual(url, urls[provider])
                self.assertFalse(options["allow_redirects"])
                self.assertNotIn(key, json.dumps(options["json"]))
                payload = options["json"]
                if provider == "openai":
                    self.assertEqual(payload["input"], OCR_TEXT)
                    self.assertFalse(payload["store"])
                    self.assertTrue(payload["text"]["format"]["strict"])
                elif provider == "google":
                    self.assertEqual(
                        payload["contents"][0]["parts"][0]["text"], OCR_TEXT
                    )
                    self.assertEqual(key, options["headers"]["x-goog-api-key"])
                else:
                    self.assertEqual(payload["messages"][-1]["content"], OCR_TEXT)

    def test_provider_failure_does_not_leak_response_or_fall_back(self):
        key = token_urlsafe(32)
        with patch.dict(
            os.environ, {"OPENAI_API_KEY": key, "OPENAI_MODEL": "test-model"}
        ):
            with patch(
                "requests.Session.post",
                return_value=fake_http({"error": key}, code=429),
            ) as post:
                with self.assertRaises(BusinessError) as error:
                    extraction_ai.extract("openai", OCR_TEXT)
                post.assert_called_once()
                self.assertIn("HTTP 429", str(error.exception))
                self.assertNotIn(key, str(error.exception))
            with (
                override_settings(EXTERNAL_SERVICES_DISABLED=True),
                patch("requests.Session.post") as post,
            ):
                with self.assertRaisesRegex(BusinessError, "désactivée"):
                    extraction_ai.extract("openai", OCR_TEXT)
                post.assert_not_called()


class ReceiptFlowTests(ApplicationCase):
    def test_archiving_and_restoring_preserves_the_original_and_expense(self):
        raw = b"%PDF-original-fictif"
        from core.services.files import put

        item = Ingestion.objects.create(
            company_uid=self.company.company_uid,
            uploaded_by_user_uid=self.user.user_uid,
            source_kind="receipt",
            original_filename="ticket-archive-fictif.pdf",
            storage_path=put("tests/ticket-archive-fictif.pdf", raw),
            sha256=digest(raw),
            mime_type="application/pdf",
            expense_amount=Decimal("16.50"),
        )
        url = f"/app/imports/{item.ingestion_uid}"
        self.assertEqual(self.client.get(url + "/archiver").status_code, 405)
        self.assertRedirects(self.client.post(url + "/archiver"), "/app/notes-de-frais")
        self.assertNotContains(self.client.get("/app/notes-de-frais"), item.original_filename)
        self.assertContains(self.client.get("/app/notes-de-frais?archives=1"), item.original_filename)
        self.assertEqual(self.client.post(url + "/transcrire").status_code, 409)
        self.assertEqual(self.client.get(url + "/telecharger").content, raw)
        self.assertRedirects(self.client.post(url + "/restaurer"), url)
        self.assertContains(self.client.get("/app/notes-de-frais"), item.original_filename)
        item.refresh_from_db()
        self.assertIsNone(item.archived_at)
        self.assertEqual(item.expense_amount, Decimal("16.50"))

    @override_settings(EXTERNAL_SERVICES_DISABLED=True)
    def test_ticket_upload_and_ocr_suggest_fields_before_review(self):
        if not shutil.which("tesseract"):
            self.skipTest("Tesseract indisponible")
        image = Image.new("RGB", (950, 640), "white")
        font = ImageFont.load_default(size=38)
        ImageDraw.Draw(image).multiline_text(
            (50, 70),
            OCR_TEXT.replace("É", "E"),
            font=font,
            fill="black",
            spacing=22,
        )
        source = io.BytesIO()
        image.save(source, "PNG")
        raw = source.getvalue()
        upload = self.client.post(
            "/app/notes-de-frais",
            {
                "company_uid": self.company.company_uid,
                "source_kind": "receipt",
                "file": SimpleUploadedFile("ticket.png", raw, "image/png"),
            },
        )
        self.assertEqual(upload.status_code, 302)
        with patch.dict(os.environ, {"OCR_LANGUAGES": "eng"}):
            self.assertEqual(
                self.client.post(upload.url + "/transcrire").status_code, 302
            )
            self.assertTrue(jobs.work_one())
        item = Ingestion.objects.get()
        self.assertEqual(item.transcription_status, "done", item.last_error)
        self.assertIsNone(item.expense_amount)
        self.assertIsNone(item.expense_date)
        review = self.client.get(upload.url)
        self.assertEqual(review.context["expense"]["amount"], "39")
        self.assertEqual(review.context["expense"]["date"], "2026-11-17")
        self.assertContains(review, 'value="CAFE DES EXEMPLES"')
        self.assertNotContains(review, 'name="provider"')
        saved = self.client.post(
            upload.url + "/enregistrer-justificatif",
            {
                "merchant_name": "Café des Exemples",
                "expense_date": "2026-11-17",
                "expense_currency": "EUR",
                "expense_amount": "39.00",
                "expense_category": "Repas",
            },
        )
        self.assertEqual(saved.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.expense_amount, Decimal("39.00"))
        self.assertEqual(item.merchant_name, "Café des Exemples")
        self.assertEqual(item.expense_category, "Repas")
        self.assertFalse(Document.objects.exists())
        self.assertEqual(self.client.get(upload.url + "/telecharger").content, raw)

    @override_settings(EXTERNAL_SERVICES_DISABLED=False)
    def test_worker_uses_selected_provider_and_keeps_reviewed_details(self):
        item = Ingestion.objects.create(
            company_uid=self.company.company_uid,
            uploaded_by_user_uid=self.user.user_uid,
            source_kind="receipt",
            transcription_text=OCR_TEXT,
            merchant_name="Nom vérifié",
            expense_amount=Decimal("40.00"),
        )
        url = f"/app/imports/{item.ingestion_uid}"
        key = token_urlsafe(32)
        with patch.dict(
            os.environ,
            {
                "INGESTION_AI_PROVIDER": "openrouter",
                "GEMINI_API_KEY": key,
                "GEMINI_MODEL": "test-model",
            },
        ):
            self.assertContains(self.client.get(url), "Google Gemini")
            self.assertNotContains(self.client.get(url), key)
            self.assertEqual(
                self.client.post(url + "/analyser", {"provider": "google"}).status_code,
                302,
            )
            self.assertEqual(BackgroundJob.objects.get().provider, "google")
            self.assertEqual(
                self.client.post(url + "/enregistrer-justificatif", {}).status_code, 409
            )
            with patch(
                "core.services.extraction_ai.extract",
                return_value=(
                    PROVIDER_FIELDS,
                    "google:test-model",
                ),
            ) as extract:
                self.assertTrue(jobs.work_one())
                extract.assert_called_once_with("google", OCR_TEXT, company_uid=self.company.company_uid, expense=True)
        item.refresh_from_db()
        self.assertEqual(item.extraction_model, "google:test-model")
        self.assertEqual(item.merchant_name, "Nom vérifié")
        self.assertEqual(item.expense_amount, Decimal("40.00"))
        self.assertEqual(self.client.get(url).context["expense"]["amount"], "40")


class AssistantApiTests(ApplicationCase):
    def setUp(self):
        super().setUp()
        base = "/app/developpeurs/cles-api/creer"
        created = self.client.post(
            base, {"name": "Assistant", "scope": ApiKey.Scope.ASSISTANT}
        )
        self.assertEqual(created.status_code, 200)
        self.assertIn("no-store", created["Cache-Control"])
        self.assertContains(created, "Codex · lecture seule")
        self.key = ApiKey.objects.get()
        self.secret = created.context["secret"]
        self.auth = {"HTTP_AUTHORIZATION": "Bearer " + self.secret}
        self.base = "/api/v1/assistant"

    def test_scope_revocation_and_tenant_isolation(self):
        receipt = Ingestion.objects.create(
            company_uid=self.other.company_uid, source_kind="receipt"
        )
        self.assertEqual(self.client.get(self.base + "/receipts").status_code, 401)
        self.assertEqual(
            self.client.get("/api/v1/invoices", **self.auth).status_code, 403
        )
        self.assertEqual(
            self.client.post(self.base + "/receipts", **self.auth).status_code, 405
        )
        self.assertEqual(
            self.client.get(
                self.base + "/receipts/" + receipt.ingestion_uid, **self.auth
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(self.base + "/receipts", **self.auth).json()["data"], []
        )
        business = token_urlsafe(32)
        ApiKey.objects.create(
            company_uid=self.company.company_uid, key_hash=digest(business)
        )
        self.assertEqual(
            self.client.get(
                self.base + "/summary", HTTP_AUTHORIZATION="Bearer " + business
            ).status_code,
            403,
        )
        response = self.client.post(
            f"/app/developpeurs/cles-api/{self.key.key_uid}/revoquer"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.client.get(self.base + "/receipts", **self.auth).status_code, 401
        )

    def test_receipt_detail_omits_raw_sources_and_compares_recorded_amount_with_suggestion(
        self,
    ):
        item = Ingestion.objects.create(
            company_uid=self.company.company_uid,
            source_kind="receipt",
            expense_date="2026-11-17",
            expense_amount=Decimal("42.00"),
            expense_currency="EUR",
            merchant_name="Café confirmé",
            storage_path="storage/private/original.pdf",
            original_filename="confidential.pdf",
            transcription_text=OCR_TEXT + "\nADRESSE CONFIDENTIELLE",
        )
        result = self.client.get(
            self.base + "/receipts/" + item.ingestion_uid, **self.auth
        )
        self.assertEqual(result.json()["data"]["amount_including_tax"], "42.00")
        self.assertEqual(result.json()["data"]["suggestions_to_review"]["amount"], "39")
        self.assertNotContains(result, "ADRESSE CONFIDENTIELLE")
        self.assertNotContains(result, "confidential.pdf")
        self.assertNotContains(result, item.storage_path)
        self.assertIn("no-store", result["Cache-Control"])

    def test_period_totals_use_confirmed_invoices_and_safe_pagination(self):
        older = self.document(issue_date="2026-10-25")
        older.status = "paid"
        older.save(update_fields=["status"])
        latest = self.document(issue_date="2026-11-17")
        response = self.client.get(
            self.base + "/summary",
            {
                "date_from": "2026-10-01",
                "date_to": "2026-11-30",
                "compare": "year",
            },
            **self.auth,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(
            data["totals"],
            [
                {
                    "currency": "EUR",
                    "sales_excluding_tax": "100.00",
                    "purchases_excluding_tax": "0.00",
                }
            ],
        )
        self.assertEqual(data["comparison"]["totals"], [])
        first = self.client.get(
            self.base + "/invoices", {"limit": 1}, **self.auth
        ).json()
        self.assertEqual(first["data"][0]["id"], latest.document_uid)
        second = self.client.get(
            self.base + "/invoices", {"cursor": first["next_cursor"]}, **self.auth
        ).json()
        self.assertEqual(second["data"][0]["id"], older.document_uid)
        self.assertIsNone(second["next_cursor"])
