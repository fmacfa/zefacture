import io
import json
import os
import secrets
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from django.test import TestCase, TransactionTestCase, Client, override_settings
from django.db import connection, connections, close_old_connections
from django.core.management import call_command
from core.common import BusinessError, stamp, digest
from core.models import *
from core.services.documents import save_document, bill_quote
from core.services import pdp, mail, jobs
from core.services.files import encrypt, put
from .test_application import ApplicationCase


class WorkflowTests(ApplicationCase):
    @override_settings(REGISTRATION_OPEN=False)
    def test_invitation_registration_and_replay(self):
        token = secrets.token_hex(32)
        invitation = CompanyInvitation.objects.create(
            company_uid=self.company.company_uid,
            invited_by_user_uid=self.user.user_uid,
            email="invited@example.test",
            role="accountant",
            token=token,
            expires_at="2099-01-01 00:00:00",
        )
        data = dict(
            name="Invitée",
            email=invitation.email,
            password="AnotherPassword123!",
            password_confirmation="AnotherPassword123!",
            terms="1",
            invite=token,
        )
        response = Client().post("/inscription", data)
        self.assertEqual(response.status_code, 302)
        invited = User.objects.get(email=data["email"])
        self.assertEqual(
            CompanyMember.objects.get(user_uid=invited.user_uid).role, "accountant"
        )
        self.assertFalse(
            CompanyMember.objects.filter(
                user_uid=invited.user_uid, company_uid=self.other.company_uid
            ).exists()
        )
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, "accepted")
        data["email"] = "second@example.test"
        self.assertEqual(Client().post("/inscription", data).status_code, 200)
        self.assertFalse(User.objects.filter(email=data["email"]).exists())

    def test_password_reset_token_one_time_and_session_invalidation(self):
        token = secrets.token_hex(32)
        PasswordReset.objects.create(
            user_uid=self.user.user_uid,
            email=self.user.email,
            token_hash=digest(token),
            expires_at="2099-01-01 00:00:00",
        )
        session = self.client.session
        session["session_version"] = self.user.session_version
        session.save()
        response = Client().post(
            "/mot-de-passe-reinitialiser/" + token,
            {
                "password": "ChangedPassword123!",
                "password_confirmation": "ChangedPassword123!",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/app").status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("ChangedPassword123!"))
        response = Client().post(
            "/mot-de-passe-reinitialiser/" + token,
            {
                "password": "OtherPassword123!",
                "password_confirmation": "OtherPassword123!",
            },
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("ChangedPassword123!"))

    def test_owner_cannot_be_removed(self):
        self.assertEqual(
            self.client.post(
                "/app/collaborateurs/" + self.user.user_uid + "/retirer"
            ).status_code,
            422,
        )
        self.assertTrue(
            CompanyMember.objects.filter(
                user_uid=self.user.user_uid, status="active"
            ).exists()
        )

    def test_api_generation_failure_rolls_back_document_and_number(self):
        raw = "test-api-key"
        ApiKey.objects.create(
            company_uid=self.company.company_uid,
            name="API",
            key_hash=digest(raw),
            prefix=raw,
        )
        with patch(
            "core.services.files.generate_document",
            side_effect=BusinessError("invalid XML"),
        ):
            response = Client().post(
                "/api/v1/invoices",
                json.dumps(self.data(generate_facturx=True)),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer " + raw,
            )
        self.assertEqual(response.status_code, 422)
        self.assertFalse(Document.objects.exists())
        self.company.refresh_from_db()
        self.assertEqual(self.company.next_invoice_number, 1)

    @override_settings(MAIL_TRANSPORT="outbox")
    def test_outbox_never_delivers_historical_smtp(self):
        Email.objects.create(
            template="test",
            recipient_email="example@example.test",
            sender_email="sender@example.test",
            sender_name="Test",
            subject="Test",
            body_text="Test",
            transport="smtp",
        )
        with patch(
            "core.services.mail.deliver", side_effect=AssertionError("Must not send")
        ):
            self.assertEqual(mail.flush(), 0)

    @override_settings(
        MAIL_TRANSPORT="smtp",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_smtp_attaches_original_and_only_sends_once(self):
        from django.core import mail as django_mail

        doc = self.document()
        doc.status = "sent"
        doc.save()
        raw = b"%PDF-original"
        DocumentFile.objects.create(
            document_uid=doc.document_uid,
            kind="facturx",
            storage_path=put("test/send.pdf", raw),
            sha256=digest(raw),
        )
        item = mail.queue(
            doc.company_uid,
            doc.document_uid,
            "invoice",
            self.customer.email,
            "Invoice",
            "Hello",
        )
        self.assertEqual(mail.flush(), 1)
        self.assertEqual(mail.flush(), 0)
        self.assertEqual(len(django_mail.outbox), 1)
        self.assertEqual(django_mail.outbox[0].attachments[0].content, raw)

    def test_background_job_duplicate_and_failure_visible(self):
        item = Ingestion.objects.create(
            company_uid=self.company.company_uid,
            uploaded_by_user_uid=self.user.user_uid,
            original_filename="source.pdf",
            storage_path="storage/no.pdf",
        )
        jobs.enqueue(item, "transcribe")
        with self.assertRaises(BusinessError):
            jobs.enqueue(item, "transcribe")
        with patch(
            "core.services.ingestion.transcribe",
            side_effect=BusinessError("OCR unavailable"),
        ):
            self.assertTrue(jobs.work_one())
        item.refresh_from_db()
        self.assertEqual(item.transcription_status, "failed")
        self.assertEqual(BackgroundJob.objects.get().status, "failed")

    def test_path_traversal(self):
        from core.services.files import resolve

        for value in ["storage/../../secret", "/etc/passwd"]:
            with self.assertRaises(BusinessError):
                resolve(value)

    def test_issued_snapshot_not_affected_by_customer_change(self):
        doc = self.document()
        doc.status = "sent"
        doc.save()
        original = doc.buyer_snapshot
        self.customer.legal_name = "Changed"
        self.customer.save()
        doc.refresh_from_db()
        self.assertEqual(doc.buyer_snapshot, original)


class PdpHttpTests(ApplicationCase):
    @override_settings(ENVIRONMENT="testing")
    def test_oauth_profile_directory_and_send_contract(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond()

            def respond(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                received.append((self.command, self.path, dict(self.headers), body))
                if self.path == "/oauth2/token":
                    data = {
                        "access_token": "mock-access",
                        "refresh_token": "mock-refresh",
                        "expires_in": 3600,
                    }
                elif self.path == "/v1.beta/oauth2_sessions/me":
                    data = {
                        "company_id": 101,
                        "user_id": "mock-user",
                        "company_verification_status": "verified",
                    }
                elif self.path == "/v1.beta/companies/me":
                    data = {
                        "id": 101,
                        "number": "123456789",
                        "number_scheme": "fr_siren",
                        "env": "test",
                    }
                elif self.path.startswith("/v1.beta/french_directory"):
                    data = {"data": []}
                elif self.path.startswith("/v1.beta/invoices?"):
                    data = {"id": 201}
                else:
                    self.send_error(404)
                    return
                raw = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = "http://127.0.0.1:" + str(server.server_port)
        try:
            with patch.dict(os.environ, {"SUPERPDP_TEST_BASE_URL": origin}):
                session = PdpSession.objects.create(
                    company_uid=self.company.company_uid,
                    api_base=origin,
                    client_id="mock-client",
                    client_secret_encrypted=encrypt("mock-secret"),
                )
                profile = pdp.verify(self.company, session)
                self.assertEqual(profile["id"], 101)
                self.assertEqual(
                    pdp.directory(self.company, self.customer, "directory")[
                        "companies"
                    ],
                    {"data": []},
                )
                doc = self.document()
                file = DocumentFile.objects.create(
                    document_uid=doc.document_uid,
                    kind="facturx",
                    storage_path=put("pdp/test.pdf", b"%PDF-mock"),
                    sha256=digest(b"%PDF-mock"),
                )
                pdp.send_invoice(doc, file)
                self.assertEqual(doc.status, "sent")
                self.assertEqual(doc.pdp_invoice_id, "201")
                count = len(received)
                pdp.send_invoice(doc, file)
                self.assertEqual(len(received), count)
                self.assertIn(b"grant_type=client_credentials", received[0][3])
                self.assertTrue(
                    any(
                        "processing_rule=B2B" in entry[1] and entry[3] == b"%PDF-mock"
                        for entry in received
                    )
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row locks")
        self.user = User.objects.create_user(
            "parallel@example.test", "Password123!", name="Parallel"
        )
        self.company = Company.objects.create(
            owner_user_uid=self.user.user_uid, legal_name="Concurrent"
        )
        self.customer = Customer.objects.create(
            company_uid=self.company.company_uid, legal_name="Client"
        )
        self.data = dict(
            customer_uid=self.customer.customer_uid,
            lines=[
                dict(
                    description="Service",
                    quantity=1,
                    unit_price_excluding_tax=100,
                    vat_rate=20,
                )
            ],
        )

    def parallel(self, fn):
        barrier = threading.Barrier(2)

        def run():
            close_old_connections()
            barrier.wait()
            try:
                return fn()
            except BusinessError:
                return "rejected"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(lambda _: run(), range(2)))

    def test_concurrent_numbering(self):
        ids = self.parallel(
            lambda: save_document(self.company, self.user, "invoice", self.data).number
        )
        self.assertEqual(len(set(ids)), 2)
        self.assertNotIn("rejected", ids)

    def test_concurrent_progress_prevents_overbilling(self):
        quote = save_document(self.company, self.user, "quote", self.data)
        quote.status = "accepted"
        quote.save()
        result = self.parallel(
            lambda: bill_quote(self.company, self.user, quote, 60).document_uid
        )
        self.assertEqual(result.count("rejected"), 1)
        self.assertEqual(
            Document.objects.filter(source_quote_uid=quote.document_uid).count(), 1
        )
