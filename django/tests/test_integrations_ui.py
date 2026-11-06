import base64
import hashlib
import hmac
import json
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import urllib3
from django.conf import settings
from django.db import transaction
from django.test import override_settings
from django.utils import timezone
from core.common import BusinessError, digest
from core.models import (
    Email,
    EmailConnection,
    EmailRoute,
    WebhookEndpoint,
    WebhookDelivery,
    Customer,
    CompanyMember,
    DocumentFile,
)
from core.services import (
    email_connections as connection_service,
    mail,
    webhooks,
    network,
)
from core.services.files import encrypt, decrypt, put
from tests.test_application import ApplicationCase
from tests.tls_services import TlsServices


class EmailConnectionTests(ApplicationCase):
    def smtp(self):
        return connection_service.save_settings(
            self.company,
            {
                "provider": "smtp",
                "sender_name": "Test Démonstration",
                "sender_email": "sender@example.test",
                "smtp_host": "smtp.example.test",
                "smtp_username": "sender@example.test",
                "smtp_password": "SecretTestOnly",
                "smtp_port": "587",
                "smtp_security": "starttls",
            },
        )

    def test_smtp_interface_encrypts_secret_and_preserves_blank_password(self):
        connection = self.smtp()
        self.assertEqual(decrypt(connection.password_encrypted), "SecretTestOnly")
        self.assertNotIn("SecretTestOnly", connection.password_encrypted)
        response = self.client.get("/app/emails/connexion")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "SecretTestOnly")
        data = {
            "provider": "smtp",
            "sender_email": connection.sender_email,
            "smtp_host": connection.smtp_host,
            "smtp_username": connection.smtp_username,
            "smtp_port": 587,
            "smtp_security": "starttls",
            "smtp_password": "",
        }
        self.assertEqual(
            self.client.post("/app/emails/connexion", data).status_code, 302
        )
        connection.refresh_from_db()
        self.assertEqual(decrypt(connection.password_encrypted), "SecretTestOnly")
        self.assertEqual(connection.smtp_port, 587)

    def test_company_route_and_changed_configuration_need_explicit_retry(self):
        connection = self.smtp()
        item = mail.queue(
            self.company.company_uid,
            None,
            "crm",
            self.customer.email,
            "Test",
            "Message",
        )
        other = mail.queue(
            self.other.company_uid, None, "crm", self.customer.email, "Other", "Message"
        )
        self.assertEqual((item.transport, other.transport), ("company", "outbox"))
        self.assertEqual(
            EmailRoute.objects.get().connection_uid, connection.connection_uid
        )
        connection.version += 1
        connection.save()
        with patch(
            "core.services.email_connections.smtp_client",
            side_effect=AssertionError("Must not send"),
        ):
            self.assertEqual(mail.flush(), 0)
        item.refresh_from_db()
        self.assertEqual(item.status, "failed")
        self.assertEqual(
            self.client.post("/app/emails/" + item.email_uid + "/relancer").status_code,
            302,
        )
        self.assertEqual(
            EmailRoute.objects.get().connection_version, connection.version
        )

    @override_settings(OUTBOUND_ALLOWED_HOSTS=["localhost"], MAIL_TRANSPORT="outbox")
    def test_smtp_actual_tls_delivery_with_original_pdf_and_no_duplicate(self):
        fixture = TlsServices()
        self.addCleanup(fixture.close)
        from core.models import CompanyBranding

        CompanyBranding.objects.create(
            company_uid=self.company.company_uid, reply_to="reply@example.test"
        )
        port = fixture.smtp()
        connection = self.smtp()
        connection.smtp_host, connection.smtp_port = "localhost", port
        connection.save()
        document = self.document()
        raw = b"%PDF-fictitious-original"
        DocumentFile.objects.create(
            document_uid=document.document_uid,
            kind="facturx",
            storage_path=put("test/mail-original.pdf", raw),
            sha256=digest(raw),
        )
        item = mail.queue(
            self.company.company_uid,
            document.document_uid,
            "invoice",
            self.customer.email,
            "Facture exemple",
            "Bonjour.",
        )
        with patch(
            "core.services.email_connections.ssl.create_default_context",
            return_value=fixture.trust,
        ):
            connection_service.check_connection(connection)
            self.assertEqual(mail.flush(), 1)
            self.assertEqual(mail.flush(), 0)
        self.assertEqual(len(fixture.smtp_messages), 1)
        message = BytesParser(policy=policy.default).parsebytes(
            fixture.smtp_messages[0]
        )
        self.assertEqual(message["To"], self.customer.email)
        self.assertEqual(message["Reply-To"], "reply@example.test")
        self.assertEqual(
            list(message.iter_attachments())[0].get_payload(decode=True), raw
        )
        item.refresh_from_db()
        self.assertEqual(item.status, "sent")

    def oauth(self, provider):
        connection_service.save_settings(
            self.company,
            {
                "provider": provider,
                "client_id": "test-client-id",
                "client_secret": "test-client-secret",
            },
        )
        result = self.client.post("/app/emails/connexion", {"action": "connect"})
        self.assertEqual(result.status_code, 302)
        params = parse_qs(urlsplit(result["Location"]).query)
        verifier = self.client.session["mail_oauth"]["verifier"]
        self.assertEqual(
            params["code_challenge"][0],
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode(),
        )
        profile = (
            {
                "sub": "account-google",
                "email": "sender@example.test",
                "email_verified": True,
            }
            if provider == "google"
            else {"id": "account-ms", "mail": "sender@example.test"}
        )
        with patch(
            "core.services.email_connections.api_json",
            side_effect=[
                {
                    "access_token": "test-access-token",
                    "refresh_token": "test-refresh-token",
                    "expires_in": 3600,
                },
                profile,
            ],
        ) as api:
            callback = "/app/emails/connexion/" + provider + "/retour"
            result = self.client.get(
                callback, {"code": "test-code", "state": params["state"][0]}
            )
            self.assertEqual(result.status_code, 302, result.content)
            self.assertEqual(
                api.call_args_list[0].kwargs["form"]["code_verifier"], verifier
            )
        connection = EmailConnection.objects.get(company_uid=self.company.company_uid)
        self.assertEqual(connection.status, "connected")
        self.assertEqual(connection.sender_email, "sender@example.test")
        self.assertEqual(
            decrypt(connection.refresh_token_encrypted), "test-refresh-token"
        )
        self.assertEqual(
            self.client.get(
                callback, {"code": "test-code", "state": params["state"][0]}
            ).status_code,
            403,
        )
        return connection

    def test_google_oauth_pkce_refresh_and_gmail_send(self):
        connection = self.oauth("google")
        connection.token_expires_at = timezone.now() - timedelta(minutes=1)
        connection.save()
        with patch(
            "core.services.email_connections.api_json",
            return_value={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 1800,
            },
        ) as refresh:
            self.assertEqual(connection_service.access_token(connection), "new-access")
            self.assertEqual(
                refresh.call_args.kwargs["form"]["grant_type"], "refresh_token"
            )
        connection.refresh_from_db()
        self.assertEqual(decrypt(connection.refresh_token_encrypted), "new-refresh")
        mail.queue(
            self.company.company_uid,
            None,
            "crm",
            self.customer.email,
            "Google test",
            "Message test",
        )
        with patch(
            "core.services.email_connections.https_request",
            return_value=(200, b'{"id":"google-message"}'),
        ) as http:
            self.assertEqual(mail.flush(), 1)
            self.assertEqual(
                http.call_args.args[0],
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            )
            raw = base64.urlsafe_b64decode(
                json.loads(http.call_args.kwargs["body"])["raw"]
            )
            self.assertIn(b"Message test", raw)
            self.assertNotIn(b"new-refresh", raw)

    def test_microsoft_oauth_and_mime_send_accepts_empty_202(self):
        self.oauth("microsoft")
        mail.queue(
            self.company.company_uid,
            None,
            "crm",
            self.customer.email,
            "Outlook test",
            "Message Outlook",
        )
        with patch(
            "core.services.email_connections.https_request", return_value=(202, b"")
        ) as http:
            self.assertEqual(mail.flush(), 1)
            self.assertEqual(
                http.call_args.args[0], "https://graph.microsoft.com/v1.0/me/sendMail"
            )
            self.assertIn(
                b"Message Outlook", base64.b64decode(http.call_args.kwargs["body"])
            )

    def test_oauth_rejects_wrong_state_company_and_configuration_change(self):
        connection_service.save_settings(
            self.company,
            {"provider": "google", "client_id": "test", "client_secret": "test-secret"},
        )
        self.client.post("/app/emails/connexion", {"action": "connect"})
        with patch(
            "core.services.email_connections.api_json",
            side_effect=AssertionError("Must not exchange"),
        ):
            self.assertEqual(
                self.client.get(
                    "/app/emails/connexion/google/retour",
                    {"state": "forged", "code": "x"},
                ).status_code,
                403,
            )
        result = self.client.post("/app/emails/connexion", {"action": "connect"})
        state = parse_qs(urlsplit(result["Location"]).query)["state"][0]
        EmailConnection.objects.filter(company_uid=self.company.company_uid).update(
            version=99
        )
        with patch(
            "core.services.email_connections.api_json",
            side_effect=AssertionError("Must not exchange"),
        ):
            self.assertEqual(
                self.client.get(
                    "/app/emails/connexion/google/retour", {"state": state, "code": "x"}
                ).status_code,
                409,
            )

    @override_settings(EMAIL_DELIVERY_DISABLED=True)
    def test_recovery_switch_blocks_even_company_connections(self):
        self.smtp()
        mail.queue(
            self.company.company_uid, None, "crm", self.customer.email, "Test", "Test"
        )
        with patch(
            "core.services.mail.deliver", side_effect=AssertionError("Must not send")
        ):
            self.assertEqual(mail.flush(), 0)

    def test_mail_settings_require_admin_and_disconnect_keeps_messages(self):
        self.smtp()
        item = mail.queue(
            self.company.company_uid, None, "crm", self.customer.email, "Test", "Test"
        )
        self.assertEqual(
            self.client.post(
                "/app/emails/connexion", {"action": "disconnect"}
            ).status_code,
            302,
        )
        self.assertTrue(Email.objects.filter(pk=item.pk).exists())
        self.assertEqual(EmailConnection.objects.get().provider, "outbox")
        member = CompanyMember.objects.get(
            company_uid=self.company.company_uid, user_uid=self.user.user_uid
        )
        member.role = "member"
        member.save()
        self.assertEqual(self.client.get("/app/emails/connexion").status_code, 403)


class WebhookTests(ApplicationCase):
    def endpoint(self, company=None, url="https://example.test/hook"):
        return WebhookEndpoint.objects.create(
            company_uid=(company or self.company).company_uid,
            name="Test",
            url=url,
            secret_encrypted=encrypt("test-signing-secret"),
            events=["customer.updated"],
        )

    def test_events_are_scoped_durable_and_rolled_back_with_business_operation(self):
        target = self.endpoint()
        self.endpoint(self.other)
        with self.assertRaises(ValueError):
            with transaction.atomic():
                self.customer.legal_name = "Rolled back"
                self.customer.save()
                raise ValueError("rollback")
        self.assertFalse(WebhookDelivery.objects.exists())
        self.customer.refresh_from_db()
        self.customer.phone = "0102030405"
        self.customer.save()
        delivery = WebhookDelivery.objects.get()
        self.assertEqual(delivery.endpoint_uid, target.endpoint_uid)
        self.assertEqual(
            delivery.payload["data"]["customer_uid"], self.customer.customer_uid
        )

    @override_settings(OUTBOUND_ALLOWED_HOSTS=["localhost"])
    def test_real_https_signed_delivery_retries_and_pause(self):
        fixture = TlsServices()
        self.addCleanup(fixture.close)
        port = fixture.https()
        target = self.endpoint(url=f"https://localhost:{port}/receiver")
        webhooks.emit(
            self.company.company_uid,
            "customer.updated",
            {"customer_uid": self.customer.customer_uid},
        )
        original_pool = urllib3.HTTPSConnectionPool

        def pool(*args, **kwargs):
            return original_pool(*args, **kwargs, ssl_context=fixture.trust)

        fixture.http_status = 503
        with patch(
            "core.services.network.urllib3.HTTPSConnectionPool", side_effect=pool
        ):
            self.assertEqual(webhooks.flush(), 0)
            delivery = WebhookDelivery.objects.get()
            self.assertEqual((delivery.status, delivery.attempts), ("queued", 1))
            self.assertEqual(webhooks.flush(), 0)  # backoff respected
            WebhookDelivery.objects.update(
                available_at=timezone.now() - timedelta(seconds=1)
            )
            fixture.http_status = 200
            self.assertEqual(webhooks.flush(), 1)
        self.assertEqual(len(fixture.http_requests), 2)
        _, headers, body = fixture.http_requests[-1]
        parts = dict(
            value.split("=", 1) for value in headers["X-ZeFacture-Signature"].split(",")
        )
        self.assertTrue(
            hmac.compare_digest(
                parts["v1"],
                hmac.new(
                    b"test-signing-secret",
                    parts["t"].encode() + b"." + body,
                    hashlib.sha256,
                ).hexdigest(),
            )
        )
        self.assertEqual(json.loads(body)["id"], delivery.event_uid)
        self.assertEqual(
            fixture.http_requests[0][1]["X-ZeFacture-Id"], headers["X-ZeFacture-Id"]
        )
        target.enabled = False
        target.save()
        webhooks.emit(self.company.company_uid, "webhook.test", {}, target)
        self.assertEqual(WebhookDelivery.objects.count(), 1)

    def test_ssrf_mixed_dns_redirect_and_url_validation(self):
        with patch(
            "core.services.network.socket.getaddrinfo",
            return_value=[
                (2, 1, 6, "", (address, 443))
                for address in ("2606:4700::1111", "93.184.216.34")
            ],
        ):
            self.assertEqual(network.resolve_host("example.test", 443), "93.184.216.34")
        for url in (
            "http://example.test/hook",
            "https://user:pass@example.test",
            "https://example.test/#fragment",
        ):
            with self.assertRaises(BusinessError):
                network.checked_url(url)
        for addresses in (
            ("127.0.0.1",),
            ("169.254.169.254",),
            ("93.184.216.34", "10.0.0.1"),
            ("::1",),
        ):
            with patch(
                "core.services.network.socket.getaddrinfo",
                return_value=[(2, 1, 6, "", (address, 443)) for address in addresses],
            ):
                with self.assertRaises(BusinessError):
                    network.resolve_host("example.test", 443)
        target = self.endpoint()
        webhooks.emit(self.company.company_uid, "customer.updated", {})
        with patch("core.services.webhooks.https_request", return_value=(302, b"")):
            webhooks.flush()
        self.assertEqual(WebhookDelivery.objects.get().last_status_code, 302)
        self.assertEqual(WebhookDelivery.objects.get().status, "queued")

    def test_webhook_interface_secret_once_rotation_and_admin_scope(self):
        with patch(
            "core.views.integrations.resolve_host", return_value="93.184.216.34"
        ):
            response = self.client.post(
                "/app/developpeurs/webhooks",
                {
                    "name": "Mon outil",
                    "url": "https://example.test/hook",
                    "events": ["customer.created"],
                },
            )
        self.assertEqual(response.status_code, 200)
        target = WebhookEndpoint.objects.get()
        secret = decrypt(target.secret_encrypted)
        self.assertContains(response, secret)
        self.assertNotContains(
            self.client.get("/app/developpeurs/webhooks/" + target.endpoint_uid), secret
        )
        other = self.endpoint(self.other)
        self.assertEqual(
            self.client.post(
                "/app/developpeurs/webhooks/" + other.endpoint_uid, {"action": "toggle"}
            ).status_code,
            404,
        )
        response = self.client.post(
            "/app/developpeurs/webhooks/" + target.endpoint_uid, {"action": "rotate"}
        )
        self.assertEqual(response.status_code, 200)
        target.refresh_from_db()
        self.assertNotEqual(decrypt(target.secret_encrypted), secret)

    @override_settings(WEBHOOK_DELIVERY_DISABLED=True)
    def test_recovery_disables_webhooks(self):
        self.endpoint()
        webhooks.emit(self.company.company_uid, "customer.updated", {})
        with patch(
            "core.services.webhooks.https_request",
            side_effect=AssertionError("Must not send"),
        ):
            self.assertEqual(webhooks.flush(), 0)
