import json
import os
import secrets
from unittest.mock import patch
from django.test import override_settings
from core.common import BusinessError
from core.models import CompanyMember
from core.services import extraction_ai
from core.views.ai_settings import TEST_RECEIPT
from .test_application import ApplicationCase
from .test_receipts_assistant import fake_http


@override_settings(EXTERNAL_SERVICES_DISABLED=False, SENSITIVE_REAUTH_REQUIRED=False)
class CompanyAITests(ApplicationCase):
    url = "/app/entreprise/ia"

    def test_inline_provider_result_and_native_download_signal(self):
        payload = {"provider": "google", "action": "test"}
        with patch("core.services.extraction_ai.extract") as probe:
            response = self.client.post(
                self.url, payload, HTTP_ACCEPT="application/json"
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("Connexion réussie", response.json()["message"])
            probe.side_effect = BusinessError(
                "Fournisseur temporairement indisponible.", 502
            )
            failure = self.client.post(
                self.url, payload, HTTP_ACCEPT="application/json"
            )
            self.assertEqual(failure.status_code, 502)
            self.assertIn("indisponible", failure.json()["error"])
        token = secrets.token_hex(16)
        for path, value, expected in (
            ("/app/finances/transactions/export", token, True),
            ("/app/finances/transactions/export", "invalide", False),
            (self.url, token, False),
        ):
            reply = self.client.get(path, {"_zf_download": value})
            self.assertEqual("zf_download_" + value in reply.cookies, expected)
            if expected:
                self.assertEqual(
                    reply.cookies["zf_download_" + value]["samesite"], "Strict"
                )

    def test_store_replace_disable_model_and_company_isolation(self):
        key = secrets.token_urlsafe(18)
        data = {
            "provider": "google",
            "model": "gemma-4-26b-a4b-it",
            "api_key": key,
            "action": "save",
        }
        with patch.dict(os.environ, {"GEMINI_API_KEY": "", "GEMINI_MODEL": ""}):
            self.assertEqual(self.client.post(self.url, data).status_code, 302)
            self.company.refresh_from_db()
            self.assertNotIn(key, json.dumps(self.company.ingestion_ai))
            page = self.client.get(self.url)
            self.assertNotContains(page, key)
            self.assertNotContains(page, self.company.ingestion_ai["google"]["secret"])
            self.assertContains(page, "Clé de l’entreprise")
            self.assertEqual(
                extraction_ai.require_provider("google", self.company.company_uid)[1],
                key,
            )
            self.assertNotIn(
                "google", dict(extraction_ai.choices(self.other.company_uid))
            )
            self.assertContains(
                self.client.get("/app/imports"), "OCR puis analyse avec Google Gemini"
            )
            self.client.post(
                self.url, {**data, "api_key": "", "model": "gemma-4-31b-it"}
            )
            self.assertEqual(
                extraction_ai.require_provider("google", self.company.company_uid)[1:],
                (key, "gemma-4-31b-it"),
            )
            with patch("core.services.extraction_ai.extract") as probe:
                self.assertEqual(
                    self.client.post(
                        self.url, {"provider": "google", "action": "test"}
                    ).status_code,
                    302,
                )
                probe.assert_called_once_with(
                    "google", TEST_RECEIPT, company_uid=self.company.company_uid
                )
            self.client.post(self.url, {"action": "disable", "provider": "google"})
            self.assertNotIn(
                "google", dict(extraction_ai.choices(self.company.company_uid))
            )
            self.company.refresh_from_db()
            self.assertNotIn("secret", self.company.ingestion_ai["google"])

    def test_admin_only_and_plain_json_for_gemma(self):
        key = secrets.token_urlsafe(18)
        self.client.post(
            self.url,
            {
                "provider": "google",
                "action": "save",
                "model": "gemma-4-31b-it",
                "api_key": key,
            },
        )
        payload = {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "parts": [
                            {
                                "text": '```json\n{"supplier":{"legal_name":"Test"},"invoice":{},"totals":{},"lines":[]}\n```'
                            }
                        ]
                    },
                }
            ]
        }
        with patch("requests.Session.post", return_value=fake_http(payload)) as post:
            result, _ = extraction_ai.extract(
                "google", "Ticket fictif", company_uid=self.company.company_uid
            )
            self.assertEqual(result["supplier"]["legal_name"], "Test")
            self.assertEqual(post.call_args.kwargs["headers"], {"x-goog-api-key": key})
            self.assertNotIn(
                "responseJsonSchema", post.call_args.kwargs["json"]["generationConfig"]
            )
        CompanyMember.objects.filter(
            company_uid=self.company.company_uid, user_uid=self.user.user_uid
        ).update(role="member")
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(
            self.client.post(
                self.url, {"provider": "google", "action": "disable"}
            ).status_code,
            403,
        )
