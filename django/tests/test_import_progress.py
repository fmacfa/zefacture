from unittest.mock import patch

from core.common import json_text, uid
from core.models import BackgroundJob, CompanyMember, Ingestion
from core.services import jobs, extraction_ai
from .test_application import ApplicationCase


class ImportProgressTests(ApplicationCase):
    def setUp(self):
        super().setUp()
        self.item = Ingestion.objects.create(
            company_uid=self.company.company_uid,
            uploaded_by_user_uid=self.user.user_uid,
            source_kind="receipt",
            original_filename="ticket.png",
            expense_currency="",
        )
        self.url = "/app/imports/" + self.item.ingestion_uid

    def state(self, endpoint="/app/notes-de-frais", **query):
        response = self.client.get(
            endpoint, {"suivi": self.item.ingestion_uid, **query}
        )
        self.assertEqual(response["Cache-Control"], "private, no-store")
        return response.json()["items"][self.item.ingestion_uid]

    def test_lifecycle_updates_without_writing_or_exposing_ocr_in_status(self):
        i = self.item
        self.assertEqual(self.state()["code"], "uploaded")
        job = jobs.enqueue(i, "pipeline", "local")
        self.assertEqual(self.state(self.url)["code"], "queued")
        self.assertContains(self.client.get(self.url), "data-import-wait")
        self.assertNotContains(self.client.get(self.url), 'name="merchant_name"')
        state = self.state(ajouter=i.ingestion_uid)
        self.assertIn("data-pending", state["row"])
        job.status = "running"
        job.save()
        self.assertEqual(self.state()["code"], "ocr")
        i.transcription_status = "done"
        i.transcription_text = "Texte privé du test"
        i.save()
        self.assertEqual(self.state()["code"], "extracting")
        job.provider = "google"
        job.save()
        self.assertEqual(self.state()["code"], "ai")
        i.status, i.extraction_status = "analyzed", "done"
        i.extraction_json = json_text(
            {
                "invoice": {"currency_code": "CHF", "issue_date": "2026-11-10"},
                "totals": {"total_including_tax": "12.50"},
            }
        )
        i.save()
        job.status = "done"
        job.save()
        self.assertEqual(
            self.state(self.url),
            {
                "code": "analyzed",
                "label": "Analyse terminée · à vérifier",
                "busy": False,
            },
        )
        self.assertIn("CHF", self.state()["row"])
        self.assertNotIn(i.transcription_text, str(self.state()))
        self.assertContains(self.client.get(self.url), 'value="12.5"')
        self.assertEqual(BackgroundJob.objects.count(), 1)
        job.status = "failed"
        job.save()
        self.assertEqual(self.state()["code"], "failed")
        self.assertContains(self.client.get(self.url), "Vous pouvez le relancer")
        job.delete()
        i.status, i.transcription_status, i.extraction_status = (
            "uploaded",
            "queued",
            "pending",
        )
        i.save()
        self.assertFalse(self.state()["busy"])

    def test_polling_preserves_company_member_and_batch_limits(self):
        ids = [self.item.ingestion_uid]
        for company, uploader in [
            (self.other.company_uid, self.user.user_uid),
            (self.company.company_uid, uid()),
        ]:
            ids.append(
                Ingestion.objects.create(
                    company_uid=company,
                    uploaded_by_user_uid=uploader,
                    source_kind="receipt",
                ).ingestion_uid
            )
        CompanyMember.objects.filter(
            company_uid=self.company.company_uid, user_uid=self.user.user_uid
        ).update(role="member")
        for page in ["/app/notes-de-frais", "/app/imports"]:
            query = {"suivi": ",".join(ids), "ajouter": ",".join(ids)}
            states = self.client.get(page, query).json()["items"]
            self.assertEqual(list(states), ids[:1])
            self.assertEqual(
                self.client.get(page, {**query, "lot": uid()}).json()["items"], {}
            )
            self.assertEqual(
                self.client.get(page, {"suivi": "invalid"}).status_code, 422
            )
        self.assertEqual(
            self.client.get("/app/imports/" + ids[-1], {"suivi": ids[-1]}).status_code,
            404,
        )

    def test_ai_review_fills_fields_and_keeps_manual_corrections_on_rerun(self):
        i = self.item
        i.transcription_text = "TICKET\nCB\nTOTAL TTC 17,80 CHF"
        i.transcription_status = "done"
        i.merchant_name = "Commerçant vérifié"
        i.save()
        self.client.post(
            self.url + "/enregistrer",
            {
                "transcription_text": i.transcription_text,
                "extraction_json": json_text(
                    {
                        "supplier": {"legal_name": "Nom corrigé"},
                        "invoice": {"reference": "MA-REF"},
                    }
                ),
            },
        )
        parsed = extraction_ai.normalize_extraction(
            {
                "supplier": {
                    "legal_name": "Café des Tests",
                    "email": "cafe@example.test",
                },
                "invoice": {
                    "number": "T-100",
                    "issue_date": "2026-11-10",
                    "currency_code": "CHF",
                },
                "totals": {"total_including_tax": "17.80", "total_vat": "1.2"},
                "payment": {"method": "card"},
                "lines": [
                    {
                        "description": "Repas",
                        "quantity": "1",
                        "unit_price_excluding_tax": "16.60",
                        "vat_rate": "7.2",
                    }
                ],
            }
        )
        with (
            patch.object(extraction_ai, "require_provider"),
            patch.object(
                extraction_ai, "extract", return_value=(parsed, "google:test")
            ) as provider,
        ):
            self.client.post(self.url + "/analyser", {"provider": "google"})
            self.assertTrue(jobs.work_one())
        provider.assert_called_once_with(
            "google", i.transcription_text, company_uid=self.company.company_uid
        )
        page = self.client.get(self.url)
        self.assertEqual(
            page.context["expense"],
            {
                "merchant": "Commerçant vérifié",
                "date": "2026-11-10",
                "amount": "17.8",
                "currency": "CHF",
            },
        )
        facts = page.context["parsed"]
        self.assertEqual(facts["invoice"]["reference"], "MA-REF")
        self.assertEqual(facts["invoice"]["number"], "T-100")
        self.assertEqual(facts["supplier"]["legal_name"], "Nom corrigé")
        self.assertEqual(facts["supplier"]["email"], "cafe@example.test")
        self.assertEqual(facts["totals"]["total_vat"], "1.2")
        self.assertEqual(page.context["lines"][0]["description"], "Repas")
        self.assertContains(page, 'value="card" selected')
        i.refresh_from_db()
        self.assertIsNone(i.expense_amount)
        self.assertFalse(i.document_uid)

    def test_company_default_automates_batch_and_respects_overrides_and_duplicates(
        self,
    ):
        from django.test import override_settings
        from secrets import token_urlsafe
        from .test_batch_imports import photo

        settings_url = "/app/entreprise/ia"
        with override_settings(EXTERNAL_SERVICES_DISABLED=False):
            for provider in ("google", "openai"):
                response = self.client.post(
                    settings_url,
                    {
                        "action": "save",
                        "provider": provider,
                        "api_key": token_urlsafe(24),
                        "model": "test-model",
                    },
                )
                self.assertEqual(response.status_code, 302)
            self.client.post(settings_url, {"action": "default", "provider": "google"})
            self.assertEqual(
                extraction_ai.choices(self.company.company_uid)[0][0], "google"
            )
            for page in ["/app/imports", "/app/notes-de-frais"]:
                self.assertContains(
                    self.client.get(page), 'data-provider="Google Gemini" selected'
                )
            upload = self.client.post(
                "/app/notes-de-frais",
                {
                    "file": [
                        photo("first.png", "red"),
                        photo("second.png", "blue"),
                        photo("copy.png", "red"),
                    ]
                },
                HTTP_ACCEPT="application/json",
            )
            self.assertEqual(
                [i["state"] for i in upload.json()["files"]],
                ["imported", "imported", "duplicate"],
            )
            self.assertEqual(
                list(BackgroundJob.objects.values_list("operation", "provider")),
                [("pipeline", "google")] * 2,
            )

            def read(item):
                item.transcription_text = "Texte OCR du justificatif"
                item.transcription_status = "done"
                item.save(update_fields=["transcription_text", "transcription_status"])

            with (
                patch("core.services.ingestion.transcribe", side_effect=read),
                patch.object(
                    extraction_ai,
                    "extract",
                    side_effect=lambda *a, **kw: (
                        {"supplier": {"legal_name": "Café synthétique"}},
                        "google:test",
                    ),
                ) as provider,
            ):
                self.assertTrue(jobs.work_one())
                self.assertTrue(jobs.work_one())
                self.assertFalse(jobs.work_one())
            self.assertEqual(provider.call_count, 2)
            for result in upload.json()["files"][:2]:
                self.assertContains(
                    self.client.get(result["url"]), 'value="Café synthétique"'
                )
            self.client.post(
                "/app/notes-de-frais",
                {"file": photo("third.png", "green"), "pipeline": "local"},
            )
            self.client.post(
                "/app/notes-de-frais",
                {"file": photo("fourth.png", "purple"), "pipeline": "none"},
            )
            self.assertEqual(BackgroundJob.objects.count(), 3)
            self.assertEqual(BackgroundJob.objects.last().provider, "local")
            self.client.post(settings_url, {"action": "disable", "provider": "google"})
            self.assertEqual(
                extraction_ai.choices(self.company.company_uid)[0][0], "openai"
            )
        with override_settings(EXTERNAL_SERVICES_DISABLED=True):
            self.assertContains(
                self.client.get("/app/notes-de-frais"),
                '<option value="local" selected>',
            )
