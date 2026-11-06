import copy
import io
import json
import os
import time
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import pyotp
from PIL import Image
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.http import HttpResponse
from django.test import Client, RequestFactory, override_settings
from django.utils import timezone
from pypdf import PdfReader
from core.common import BusinessError, digest
from core.middleware import ProxyBoundaryMiddleware
from core.models import (
    CompanyBranding,
    UserSecurity,
    OAuthAttempt,
    EmailConnection,
    EmailEnvelope,
    Email,
)
from core.services import (
    account_security,
    branding,
    email_connections,
    mail,
    network,
    ingestion,
    pdp,
)
from core.services.files import encrypt, decrypt, generate_document, read_verified
from core.views.documents import send
from tests.test_application import ApplicationCase


class OAuthHardeningTests(ApplicationCase):
    def smtp_data(self):
        return dict(
            provider="smtp",
            sender_email="sender@example.test",
            smtp_host="smtp.example.test",
            smtp_port=587,
            smtp_security="starttls",
            smtp_username="sender@example.test",
            smtp_password="SyntheticSecret",
        )

    def test_smtp_saved_secret_is_bound_to_destination_and_account(self):
        data = self.smtp_data()
        connection = email_connections.save_settings(self.company, data)
        for change in (
            {"smtp_host": "other.example.test"},
            {"smtp_port": 465},
            {"smtp_security": "ssl"},
            {"smtp_username": "other@example.test"},
        ):
            with self.subTest(change=change):
                result = self.client.post(
                    "/app/emails/connexion", data | change | {"smtp_password": ""}
                )
                self.assertEqual(result.status_code, 422)
                connection.refresh_from_db()
                self.assertEqual(connection.smtp_host, data["smtp_host"])
                self.assertEqual(connection.smtp_port, 587)
                self.assertEqual(connection.smtp_username, data["smtp_username"])
                self.assertEqual(
                    decrypt(connection.password_encrypted), "SyntheticSecret"
                )
        changed = email_connections.save_settings(
            self.company,
            data
            | {"smtp_host": "other.example.test", "smtp_password": "ReplacementSecret"},
        )
        self.assertEqual(decrypt(changed.password_encrypted), "ReplacementSecret")

    def oauth_request(self, provider="google"):
        connection = email_connections.save_settings(
            self.company,
            {
                "provider": provider,
                "client_id": "test-client",
                "client_secret": "test-secret",
            },
        )
        request = RequestFactory().get("/app/emails/connexion")
        request.user, request.company, request.session = self.user, self.company, {}
        url = email_connections.begin_oauth(request, connection)
        params = parse_qs(urlsplit(url).query)
        request.GET = {"code": "synthetic-code", "state": params["state"][0]}
        return request, connection

    def test_oauth_consumption_is_durable_for_concurrent_session_copies(self):
        request, connection = self.oauth_request()
        stale = copy.copy(request)
        stale.session = copy.deepcopy(request.session)
        with patch(
            "core.services.email_connections.api_json",
            side_effect=[
                {
                    "access_token": "new",
                    "refresh_token": "refresh",
                    "scope": email_connections.OAUTH["google"]["scope"],
                },
                {"sub": "id", "email": "sender@example.test", "email_verified": True},
            ],
        ) as api:
            updated = email_connections.finish_oauth(request, "google")
            self.assertEqual(updated.version, connection.version + 1)
            with self.assertRaises(BusinessError):
                email_connections.finish_oauth(stale, "google")
            self.assertEqual(api.call_count, 2)
        self.assertIsNotNone(OAuthAttempt.objects.get().consumed_at)

    def test_failed_grant_is_consumed_and_cannot_reuse_previous_refresh_token(self):
        request, connection = self.oauth_request()
        connection.refresh_token_encrypted = encrypt("previous-account-refresh")
        connection.save()
        stale = copy.copy(request)
        stale.session = copy.deepcopy(request.session)
        with patch(
            "core.services.email_connections.api_json",
            side_effect=[
                {"access_token": "new"},
                {"sub": "new-id", "email": "new@example.test", "email_verified": True},
            ],
        ):
            with self.assertRaises(BusinessError):
                email_connections.finish_oauth(request, "google")
        with patch(
            "core.services.email_connections.api_json",
            side_effect=AssertionError("No replay"),
        ):
            with self.assertRaises(BusinessError):
                email_connections.finish_oauth(stale, "google")
        self.assertIsNotNone(OAuthAttempt.objects.get().consumed_at)

    def test_client_credentials_are_paired_and_bound_to_pending_grant(self):
        request, connection = self.oauth_request()
        with patch.dict(
            os.environ,
            {
                "GOOGLE_MAIL_CLIENT_ID": "server-id",
                "GOOGLE_MAIL_CLIENT_SECRET": "server-secret",
            },
        ):
            connection.client_secret_encrypted = ""
            with self.assertRaises(BusinessError):
                email_connections.credentials(connection)
            connection.client_id = ""
            self.assertEqual(
                email_connections.credentials(connection),
                ("server-id", "server-secret"),
            )
        EmailConnection.objects.filter(pk=connection.pk).update(
            client_secret_encrypted=encrypt("changed-secret")
        )
        with patch(
            "core.services.email_connections.api_json",
            side_effect=AssertionError("Changed credentials"),
        ):
            with self.assertRaises(BusinessError):
                email_connections.finish_oauth(request, "google")

    def test_token_scope_and_type_are_checked_without_storing_rejected_token(self):
        _, connection = self.oauth_request()
        for payload in (
            {"access_token": "x", "scope": "openid email"},
            {"access_token": "x", "token_type": "mac"},
        ):
            with self.assertRaises(BusinessError):
                email_connections.store_token(connection, payload)
        connection.refresh_from_db()
        self.assertEqual(connection.access_token_encrypted, "")

    def test_revoked_authorization_requires_reconnection_and_redacts_provider_error(
        self,
    ):
        _, connection = self.oauth_request()
        connection.status, connection.refresh_token_encrypted = (
            "connected",
            encrypt("secret-refresh"),
        )
        connection.token_expires_at = timezone.now() - timedelta(minutes=1)
        connection.save()
        with patch(
            "core.services.email_connections.https_request",
            return_value=(
                400,
                b'{"error":"invalid_grant","error_description":"DO-NOT-DISPLAY"}',
            ),
        ):
            with self.assertRaises(BusinessError) as error:
                email_connections.access_token(connection)
        self.assertNotIn("DO-NOT-DISPLAY", str(error.exception))
        connection.refresh_from_db()
        self.assertEqual(
            (connection.status, connection.refresh_token_encrypted), ("reconnect", "")
        )

    def test_callback_is_get_only_and_help_uses_exact_instance_origin(self):
        self.assertEqual(
            self.client.post("/app/emails/connexion/google/retour").status_code, 405
        )
        with override_settings(APP_URL="https://zefacture.com"):
            result = self.client.get("/app/emails/connexion/guide")
            self.assertContains(
                result, "https://zefacture.com/app/emails/connexion/google/retour"
            )
            self.assertContains(
                result, "https://zefacture.com/app/emails/connexion/microsoft/retour"
            )


class AccountSecurityTests(ApplicationCase):
    def enable(self, code="0123456789abcdef"):
        secret = pyotp.random_base32()
        row = UserSecurity.objects.create(
            user_uid=self.user.user_uid,
            totp_secret_encrypted=encrypt(secret),
            enabled_at=timezone.now(),
            recovery_hashes=[digest(code)],
        )
        return row, secret

    def test_totp_is_required_before_session_login_and_replay_is_rejected(self):
        row, secret = self.enable()
        self.client.logout()
        result = self.client.post(
            "/connexion", {"email": self.user.email, "password": "OwnerPassword123!"}
        )
        self.assertEqual(result["Location"], "/connexion/verification")
        self.assertNotIn("_auth_user_id", self.client.session)
        code = pyotp.TOTP(secret).now()
        self.assertEqual(
            self.client.post("/connexion/verification", {"code": code})["Location"],
            "/app",
        )
        self.client.logout()
        self.client.post(
            "/connexion", {"email": self.user.email, "password": "OwnerPassword123!"}
        )
        self.assertEqual(
            self.client.post("/connexion/verification", {"code": code}).status_code, 200
        )
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(
            self.client.post("/connexion/verification", {"code": "0123456789abcdef"})[
                "Location"
            ],
            "/app",
        )
        self.assertFalse(account_security.verify_code(self.user, "0123456789abcdef"))

    def test_enrollment_encrypts_secret_and_shows_single_use_recovery_codes(self):
        result = self.client.post(
            "/app/compte/securite",
            {"action": "prepare", "password": "OwnerPassword123!"},
        )
        self.assertEqual(result.status_code, 200)
        secret = result.context["setup"]["secret"]
        result = self.client.post(
            "/app/compte/securite",
            {"action": "activate", "code": pyotp.TOTP(secret).now()},
        )
        codes = result.context["recovery_codes"]
        row = UserSecurity.objects.get(user_uid=self.user.user_uid)
        self.assertEqual(len(codes), 10)
        self.assertNotIn(secret, row.totp_secret_encrypted)
        self.assertNotIn(codes[0], json.dumps(row.recovery_hashes))
        self.assertTrue(account_security.verify_code(self.user, codes[0]))
        self.assertFalse(account_security.verify_code(self.user, codes[0]))
        self.assertEqual(
            self.client.get("/app/compte/securite").context["recovery_codes"], []
        )
        self.assertNotIn("totp_setup", self.client.session)

    @override_settings(MFA_REQUIRED_FOR_ADMINS=True)
    def test_admin_mfa_policy_blocks_business_until_setup_and_prevents_disabling(self):
        self.assertEqual(self.client.get("/app")["Location"], "/app/compte/securite")
        self.assertEqual(self.client.get("/app/compte/securite").status_code, 200)
        self.enable()
        result = self.client.post(
            "/app/compte/securite",
            {
                "action": "disable",
                "password": "OwnerPassword123!",
                "code": "0123456789abcdef",
            },
        )
        self.assertContains(result, "obligatoire")
        self.assertTrue(account_security.enabled(self.user))

    @override_settings(SENSITIVE_REAUTH_REQUIRED=True)
    def test_sensitive_configuration_requires_recent_password_verification(self):
        self.assertTrue(
            self.client.get("/app/emails/connexion")["Location"].startswith(
                "/app/verification"
            )
        )
        result = self.client.post(
            "/app/verification?next=/app/emails/connexion",
            {"password": "OwnerPassword123!"},
        )
        self.assertEqual(result["Location"], "/app/emails/connexion")
        self.assertEqual(self.client.get("/app/emails/connexion").status_code, 200)
        session = self.client.session
        session["verified_at"] = time.time() - 901
        session.save()
        self.assertEqual(self.client.get("/app/emails/connexion").status_code, 302)

    def test_operator_recovery_invalidates_other_sessions_and_password_reset_does_not_remove_mfa(
        self,
    ):
        self.enable()
        self.client.logout()
        self.client.post("/mot-de-passe-oublie", {"email": self.user.email})
        reset_email = Email.objects.get(template="password_reset")
        reset_path = urlsplit(reset_email.body_text).path
        self.client.post(
            reset_path,
            {
                "password": "ReplacementPassword456!",
                "password_confirmation": "ReplacementPassword456!",
            },
        )
        self.assertTrue(account_security.enabled(self.user))
        with self.assertRaises(Exception):
            call_command("reset_mfa", email=self.user.email, stdout=io.StringIO())
        self.user.refresh_from_db()
        old_version = self.user.session_version
        call_command(
            "reset_mfa",
            email=self.user.email,
            confirm_identity_checked=True,
            stdout=io.StringIO(),
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.session_version, old_version + 1)
        self.assertFalse(account_security.enabled(self.user))

    def test_mfa_attempt_limit_is_shared_across_client_addresses(self):
        row, secret = self.enable()
        self.client.logout()
        self.client.post(
            "/connexion", {"email": self.user.email, "password": "OwnerPassword123!"}
        )
        for index in range(5):
            self.client.post(
                "/connexion/verification",
                {"code": "wrong-code"},
                REMOTE_ADDR=f"192.0.2.{index + 1}",
            )
        result = self.client.post(
            "/connexion/verification",
            {"code": pyotp.TOTP(secret).now()},
            REMOTE_ADDR="198.51.100.1",
        )
        self.assertContains(result, "Trop de tentatives")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_security_headers_and_proxy_client_addresses_cannot_be_spoofed(self):
        response = self.client.get("/app")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", response["Content-Security-Policy"])
        observed = []
        middleware = ProxyBoundaryMiddleware(
            lambda request: (
                observed.append(
                    (
                        request.is_secure(),
                        request.META["REMOTE_ADDR"],
                        "HTTP_X_ZEFACTURE_PROXY_KEY" in request.META,
                    )
                )
                or HttpResponse()
            )
        )
        with override_settings(
            TRUST_PROXY=True,
            PROXY_SHARED_SECRET="p" * 48,
            SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        ):
            request = RequestFactory().get(
                "/",
                HTTP_X_FORWARDED_PROTO="https",
                HTTP_X_ZEFACTURE_CLIENT_IP="1.1.1.1",
            )
            middleware(request)
            request = RequestFactory().get(
                "/",
                HTTP_X_FORWARDED_PROTO="https",
                HTTP_X_ZEFACTURE_CLIENT_IP="1.1.1.1",
                HTTP_X_ZEFACTURE_PROXY_KEY="p" * 48,
            )
            middleware(request)
        self.assertEqual(
            observed, [(False, "127.0.0.1", False), (True, "1.1.1.1", False)]
        )

    def test_absolute_session_expiry_is_enforced_even_for_an_active_browser(self):
        session = self.client.session
        session["session_started_at"] = time.time() - settings.SESSION_COOKIE_AGE - 1
        session.save()
        self.assertEqual(self.client.get("/app")["Location"], "/connexion")

    @override_settings(EXTERNAL_SERVICES_DISABLED=True)
    def test_recovery_lock_blocks_all_external_services_before_network_access(self):
        with patch("socket.getaddrinfo", side_effect=AssertionError("No DNS")):
            with self.assertRaises(BusinessError):
                network.https_request("https://example.com")
            with self.assertRaises(BusinessError):
                ingestion.analyze(None)
            with self.assertRaises(BusinessError):
                pdp.directory(self.company, self.customer, "vies")
            with self.assertRaises(BusinessError):
                email_connections.smtp_client(None)
        self.assertEqual(mail.flush(), 0)


class BrandingTests(ApplicationCase):
    def data_brand(self, **changes):
        return (
            dict(
                display_name="Studio Exemple",
                accent_color="#9b156b",
                document_layout="compact",
                default_currency="CHF",
                payment_due_days="45",
                quote_validity_days="15",
                reply_to="contact@example.test",
                email_signature="Merci,\nStudio Exemple",
            )
            | changes
        )

    def test_logo_is_normalized_and_tenant_branding_is_snapshotted_and_immutable_after_issue(
        self,
    ):
        out = io.BytesIO()
        exif = Image.Exif()
        exif[315] = "Synthetic private metadata"
        Image.new("RGB", (150, 70), "#9b156b").save(
            out, format="JPEG", exif=exif, icc_profile=b"synthetic-private-profile"
        )
        branding.save(
            self.company,
            self.data_brand(),
            SimpleUploadedFile("logo.jpg", out.getvalue(), content_type="image/jpeg"),
        )
        document = self.document(kind="quote")
        self.assertEqual(document.seller["branding"]["display_name"], "Studio Exemple")
        self.assertTrue(
            branding.logo_bytes(
                self.company.company_uid, document.seller["branding"]
            ).startswith(b"\x89PNG")
        )
        with Image.open(
            io.BytesIO(
                branding.logo_bytes(
                    self.company.company_uid, document.seller["branding"]
                )
            )
        ) as normalized:
            self.assertNotIn("icc_profile", normalized.info)
            self.assertNotIn("exif", normalized.info)
        original = generate_document(document)
        raw = read_verified(original)
        self.assertIn(
            "Studio Exemple",
            "".join(page.extract_text() for page in PdfReader(io.BytesIO(raw)).pages),
        )
        send(document)
        document.refresh_from_db()
        branding.save(
            self.company,
            self.data_brand(
                display_name="Nouvelle présentation",
                accent_color="#006699",
                remove_logo="1",
            ),
        )
        self.assertEqual(read_verified(generate_document(document)), raw)
        self.assertFalse(branding.for_company(self.other).logo_path)
        self.assertEqual(document.seller["branding"]["display_name"], "Studio Exemple")

    def test_safe_templates_keep_quote_acceptance_link_and_freeze_reply_to(self):
        branding.save(
            self.company,
            self.data_brand(
                quote_subject="Devis {document_number} — {company}",
                quote_body="Bonjour {customer}, votre devis de {total} {currency}.",
            ),
        )
        document = self.document(kind="quote")
        send(document)
        item = Email.objects.get(document_uid=document.document_uid)
        self.assertIn("Studio Exemple", item.body_text)
        self.assertIn(settings.APP_URL + "/devis/", item.body_text)
        self.assertEqual(
            EmailEnvelope.objects.get(email_uid=item.email_uid).reply_to,
            "contact@example.test",
        )
        branding.save(self.company, self.data_brand(reply_to="changed@example.test"))
        self.assertEqual(
            EmailEnvelope.objects.get(email_uid=item.email_uid).reply_to,
            "contact@example.test",
        )
        for value in (
            "{company.__class__}",
            "{total!r}",
            "{customer:>10000000}",
            "{not_allowed}",
        ):
            with self.assertRaises(BusinessError):
                branding.save(
                    self.company,
                    self.data_brand(invoice_subject=value, invoice_body="texte"),
                )

    def test_branding_rejects_active_content_bad_colors_and_wrong_company_logo(self):
        with self.assertRaises(BusinessError):
            branding.save(
                self.company, self.data_brand(accent_color="red;position:fixed")
            )
        with self.assertRaises(BusinessError):
            branding.save(
                self.company,
                self.data_brand(),
                SimpleUploadedFile("logo.svg", b'<svg onload="alert(1)"/>'),
            )
        with self.assertRaises(BusinessError):
            branding.logo_bytes(
                self.company.company_uid,
                {
                    "logo_path": "storage/branding/another/logo.png",
                    "logo_sha256": "0" * 64,
                },
            )
        for color in ("#ffffff", "#ffff00", "#000000", "#176f60"):
            adjusted = branding.palette(color)["primary"]
            rgb = [int(adjusted[i : i + 2], 16) for i in (1, 3, 5)]
            self.assertGreaterEqual(1.05 / (branding.luminance(rgb) + 0.05), 4.5)

    def test_branded_facturx_keeps_embedded_xml_and_public_quote_uses_snapshot(self):
        branding.save(self.company, self.data_brand())
        invoice = self.document()
        data = read_verified(generate_document(invoice))
        pdf = PdfReader(io.BytesIO(data))
        self.assertIn("factur-x.xml", pdf.attachments)
        self.assertIn(
            "Studio Exemple", "".join(page.extract_text() for page in pdf.pages)
        )
        quote = self.document(kind="quote")
        send(quote)
        queued = Email.objects.get(document_uid=quote.document_uid)
        link = queued.body_text.split("Consulter et accepter : ", 1)[1].splitlines()[0]
        branding.save(self.company, self.data_brand(display_name="Nouvelle identité"))
        self.client.logout()
        result = self.client.get(urlsplit(link).path)
        self.assertContains(result, "Studio Exemple")
        self.assertNotContains(result, "Nouvelle identité")

    def test_branding_defaults_display_preferences_and_admin_permissions(self):
        branding.save(self.company, self.data_brand())
        response = self.client.get("/app/devis/nouveau")
        self.assertContains(response, 'value="CHF"')
        self.assertEqual(response.context["default_currency"], "CHF")
        self.client.post(
            "/app/compte/affichage", {"theme": "dark", "density": "compact"}
        )
        self.assertContains(
            self.client.get("/app"), 'data-theme="dark" data-density="compact"'
        )
        from core.models import CompanyMember

        CompanyMember.objects.filter(user_uid=self.user.user_uid).update(role="viewer")
        self.assertEqual(
            self.client.get("/app/entreprise/personnalisation").status_code, 403
        )
        self.assertEqual(
            self.client.post(
                "/app/entreprise/personnalisation", self.data_brand()
            ).status_code,
            403,
        )
