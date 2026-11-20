import csv
import io
import json
import logging
import os
import secrets
from decimal import Decimal
from unittest.mock import patch
from zipfile import ZipFile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from pypdf import PdfReader

from config.request_logging import RequestOutcomeFormatter
from core.common import BusinessError, digest
from core.models import (
    ApiKey,
    CompanyInvitation,
    CompanyMember,
    Document,
    DocumentFile,
    Ingestion,
    InvoiceApiCreation,
    InvoiceEvent,
    PasswordReset,
    PdpInvoice,
    PdpSyncState,
    User,
)
from core.services import files
from core.services.documents import change_status, issue_invoice
from core.views.documents import send
from .test_application import ApplicationCase
from .test_supplier_archive import make_pdf


class InvoiceIntegrityTests(ApplicationCase):
    def test_four_signs_in_accounting_journal(self):
        for number, direction, kind, net in [
            ("V", "outgoing", "invoice", 100),
            ("AV", "outgoing", "credit_note", 20),
            ("A", "incoming", "invoice", 100),
            ("AA", "incoming", "credit_note", 20),
        ]:
            Document.objects.create(
                company_uid=self.company.company_uid,
                type=kind,
                number=number,
                direction=direction,
                status="sent" if direction == "outgoing" else "received",
                issue_date="2026-01-05",
                currency_code="EUR",
                total_excluding_tax=net,
                total_vat=net // 5,
                total_including_tax=net + net // 5,
            )
        response = self.client.get(
            "/app/finances/export-comptable", {"from": "2026-01-01", "to": "2026-01-31"}
        )
        self.assertEqual(response.status_code, 200)
        journal = {
            r["Numéro"]: tuple(Decimal(r[k]) for k in ("HT", "TVA", "TTC"))
            for r in csv.DictReader(
                io.StringIO(response.content.decode("utf-8-sig")), delimiter=";"
            )
        }
        self.assertEqual(
            journal,
            {
                "V": (100, 20, 120),
                "AV": (-20, -4, -24),
                "A": (-100, -20, -120),
                "AA": (20, 4, 24),
            },
        )

    def test_api_retry_rotation_conflict_and_legacy_reference(self):
        def credentials():
            token = secrets.token_urlsafe(24)
            ApiKey.objects.create(
                company_uid=self.company.company_uid, key_hash=digest(token)
            )
            return {"HTTP_AUTHORIZATION": "Bearer " + token}

        first_key, replacement_key = credentials(), credentials()
        url = "/api/v1/invoices"
        payload = self.data(reference="VENTE-EXEMPLE-21")

        def create(data, auth, key=None):
            headers = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
            return self.client.post(
                url, data, content_type="application/json", **auth, **headers
            )

        one = create(payload, first_key)
        self.assertEqual(one.status_code, 201)
        identifier = one.json()["data"]["document_uid"]
        for auth, key in (
            (replacement_key, None),
            (replacement_key, "boutique-21"),
            (first_key, "boutique-21"),
        ):
            repeated = create(dict(reversed(list(payload.items()))), auth, key)
            self.assertEqual(repeated.status_code, 200)
            self.assertEqual(repeated.json()["data"]["document_uid"], identifier)
        self.assertEqual(
            create(payload | {"subject": "Contenu différent"}, first_key).status_code,
            409,
        )
        self.assertEqual(create(payload, first_key, "autre-commande").status_code, 409)
        self.assertEqual(
            create(self.data(reference="AUTRE"), first_key, "boutique-21").status_code,
            409,
        )
        self.document(reference="VENTE-HISTORIQUE")
        self.assertEqual(
            create(self.data(reference="VENTE-HISTORIQUE"), first_key).status_code, 409
        )
        self.assertEqual(InvoiceApiCreation.objects.count(), 1)
        self.assertEqual(Document.objects.count(), 2)
        self.assertEqual(
            create(self.data(), first_key, "sans-reference").status_code, 201
        )
        self.assertEqual(
            create(self.data(), first_key, "sans-reference").status_code, 200
        )
        self.assertEqual(create(self.data(), first_key).status_code, 400)

        quote = change_status(self.document("quote"), "accept")
        path = "/api/v1/quotes/" + quote.document_uid + "/progress-invoices"
        for key, status in [("etape-un", 201), ("etape-un", 200), ("etape-deux", 201)]:
            result = self.client.post(
                path,
                {"percent": "20"},
                content_type="application/json",
                HTTP_IDEMPOTENCY_KEY=key,
                **first_key,
            )
            self.assertEqual(result.status_code, status)
        self.assertEqual(
            Document.objects.filter(source_quote_uid=quote.document_uid).count(), 2
        )

    def test_number_only_on_issuance_across_channels_and_rollback(self):
        cancelled, sent, api = [self.document() for _ in range(3)]
        preview = files.generate_document(sent)
        preview_raw = files.read_verified(preview)
        self.assertIn(
            sent.draft_reference,
            PdfReader(io.BytesIO(preview_raw))
            .pages[0]
            .extract_text()
            .replace("\n", ""),
        )
        change_status(cancelled, "cancel")
        self.company.refresh_from_db()
        self.assertEqual(self.company.next_invoice_number, 1)
        with patch(
            "core.services.files.generate_document",
            side_effect=BusinessError("PDF indisponible"),
        ):
            with self.assertRaises(BusinessError):
                issue_invoice(api)
        api.refresh_from_db()
        self.company.refresh_from_db()
        self.assertEqual(
            (api.number, self.company.next_invoice_number), (api.draft_reference, 1)
        )
        issued = issue_invoice(api)
        emailed = send(sent)
        self.assertEqual(
            (issued.number, emailed.number), ("FA-2026-0001", "FA-2026-0002")
        )
        final_pdf = files.issued_invoice_pdf(emailed)
        self.assertIn(
            emailed.number, PdfReader(io.BytesIO(final_pdf)).pages[0].extract_text()
        )
        self.assertNotEqual(preview_raw, final_pdf)
        self.assertEqual(files.read_verified(preview), preview_raw)
        earlier = self.document(issue_date="2026-01-01")
        with self.assertRaises(BusinessError):
            issue_invoice(earlier)
        january = self.document(issue_date="2027-01-04", due_date="2027-01-20")
        self.assertEqual(issue_invoice(january).number, "FA-2027-0003")

    def test_historical_reserved_number_and_quote_acceptance(self):
        old = self.document()
        Document.objects.filter(pk=old.pk).update(number="FA-HISTORIQUE")
        self.assertEqual(issue_invoice(old).number, "FA-HISTORIQUE")
        quote = self.document("quote")
        self.assertEqual(quote.number, quote.draft_reference)
        self.assertEqual(change_status(quote, "accept").number, "DE-2026-0001")
        self.company.refresh_from_db()
        self.assertEqual(self.company.next_invoice_number, 1)

    def test_one_supplier_original_for_download_preview_and_archive(self):
        doc = Document.objects.create(
            company_uid=self.company.company_uid,
            type="invoice",
            direction="incoming",
            number="ACHAT-1",
            issue_date="2026-01-20",
            status="received",
        )
        original = make_pdf()
        self.client.post(
            f"/app/documents/{doc.document_uid}/fichiers",
            {
                "file": SimpleUploadedFile(
                    "facture.pdf", original, content_type="application/pdf"
                )
            },
        )
        self.client.post(
            f"/app/documents/{doc.document_uid}/fichiers",
            {
                "file": SimpleUploadedFile(
                    "annexe.pdf", make_pdf(b"<test/>"), content_type="application/pdf"
                )
            },
        )
        source = doc.files.get(kind="source")
        self.assertEqual(self.client.get(doc.url).context["preview_file"], source)
        self.assertEqual(self.client.get(doc.url + "/telecharger").content, original)
        archive = self.client.get(
            "/app/reception/export.zip",
            {"from": "2026-01-01", "to": "2026-01-31", "format": "pdf"},
        )
        with ZipFile(io.BytesIO(b"".join(archive.streaming_content))) as z:
            self.assertEqual(z.read(z.namelist()[0]), original)
        DocumentFile.objects.filter(pk=source.pk).update(kind="attachment")
        self.assertEqual(self.client.get(doc.url + "/telecharger").status_code, 409)
        self.assertContains(
            self.client.get(doc.url), "Désigner comme facture originale"
        )
        select = f"/app/documents/{doc.document_uid}/fichiers/{source.file_uid}"
        self.assertEqual(
            self.client.post(select, {"action": "original"}).status_code, 302
        )
        self.assertEqual(self.client.get(doc.url + "/telecharger").content, original)
        self.assertEqual(
            self.client.post(select, {"action": "original"}).status_code, 409
        )


class AccessIntegrityTests(ApplicationCase):
    def test_import_bound_to_displayed_company_even_for_two_authorized_memberships(
        self,
    ):
        CompanyMember.objects.create(
            company_uid=self.other.company_uid,
            user_uid=self.user.user_uid,
            role="owner",
        )
        self.assertContains(
            self.client.get("/app/imports"), f'value="{self.company.company_uid}"'
        )
        session = self.client.session
        session["company_uid"] = self.other.company_uid
        session.save()
        for page in ["/app/imports", "/app/notes-de-frais"]:
            for company in ("", self.company.company_uid):
                result = self.client.post(
                    page,
                    {
                        "company_uid": company,
                        "source_kind": "receipt",
                        "file": SimpleUploadedFile("justificatif.pdf", make_pdf()),
                    },
                    HTTP_ACCEPT="application/json",
                )
                self.assertEqual(result.status_code, 409)
        self.assertFalse(Ingestion.objects.exists())
        response = self.client.post(
            "/app/notes-de-frais?lot=" + "a" * 32,
            {
                "company_uid": self.company.company_uid,
                "action": "analyser-lot",
                "provider": "google",
            },
        )
        self.assertEqual(response.status_code, 409)

    def test_invitation_replacement_removal_and_fresh_reinvitation(self):
        user = User.objects.create_user(
            "equipe@example.test", "MemberPassword123!", name="Équipe"
        )
        base = "/app/collaborateurs"

        def invite(role):
            self.assertEqual(
                self.client.post(base, {"role": role, "email": user.email}).status_code,
                302,
            )
            return CompanyInvitation.objects.get(
                company_uid=self.company.company_uid, status="pending"
            )

        old = invite("admin")
        new = invite("member")
        member = Client()
        member.force_login(user)
        self.assertEqual(member.post("/invitation/" + old.token).status_code, 404)
        self.assertEqual(member.post("/invitation/" + new.token).status_code, 302)
        other = CompanyInvitation.objects.create(
            company_uid=self.company.company_uid,
            email=user.email,
            role="admin",
            token=secrets.token_hex(32),
            expires_at="2099-01-01 00:00:00",
        )
        self.assertEqual(
            self.client.post(base + "/" + user.user_uid + "/retirer").status_code, 302
        )
        self.assertEqual(member.post("/invitation/" + other.token).status_code, 404)
        rejoin = invite("viewer")
        self.assertEqual(member.post("/invitation/" + rejoin.token).status_code, 302)
        self.assertEqual(
            CompanyMember.objects.get(
                company_uid=self.company.company_uid, user_uid=user.user_uid
            ).role,
            "viewer",
        )

    def test_password_recovery_follows_current_identity(self):
        visitor = Client()
        visitor.post("/mot-de-passe-oublie", {"email": self.user.email})
        reset = PasswordReset.objects.get(user_uid=self.user.user_uid)
        self.assertEqual(reset.account_version, self.user.session_version)
        self.assertEqual(
            self.client.post(
                "/app/compte",
                {
                    "name": self.user.name,
                    "email": "nouvelle@example.test",
                    "current_password": "OwnerPassword123!",
                },
            ).status_code,
            302,
        )
        reset.refresh_from_db()
        self.assertEqual(reset.status, "cancelled")
        self.user.refresh_from_db()
        for version in (None, self.user.session_version - 1):
            raw = secrets.token_hex(32)
            PasswordReset.objects.create(
                user_uid=self.user.user_uid,
                email=self.user.email,
                account_version=version,
                token_hash=digest(raw),
                expires_at="2099-01-01 00:00:00",
            )
            visitor.post(
                "/mot-de-passe-reinitialiser/" + raw,
                {
                    "password": "ChangeAfterReview123!",
                    "password_confirmation": "ChangeAfterReview123!",
                },
            )
            self.user.refresh_from_db()
            self.assertTrue(self.user.check_password("OwnerPassword123!"))

    def test_request_logs_only_keep_http_outcome(self):
        from django.test import RequestFactory

        request = RequestFactory().get("/invitation/" + secrets.token_hex(32))
        record = logging.LogRecord(
            "django.request",
            logging.WARNING,
            __file__,
            1,
            "Not Found: %s",
            (request.path,),
            None,
        )
        record.request = request
        record.status_code = 404
        line = RequestOutcomeFormatter().format(record)
        self.assertIn("404", line)
        self.assertNotIn(request.path, line)

    def test_relay_scopes_event_to_provider_company_and_environment(self):
        for company, remote in [(self.company, "123"), (self.other, "456")]:
            PdpSyncState.objects.create(
                company_uid=company.company_uid,
                remote_company_id=remote,
                remote_environment="production",
            )
            doc = Document.objects.create(
                company_uid=company.company_uid,
                type="invoice",
                direction="incoming",
                number="F-1",
                status="received",
            )
            PdpInvoice.objects.create(
                company_uid=company.company_uid,
                document_uid=doc.document_uid,
                direction="in",
                remote_company_id=remote,
                remote_environment="production",
                remote_id="98",
            )
        event = {"event_id": "1", "invoice_id": "98"}
        token = secrets.token_urlsafe(32)

        def relay(payload):
            with patch.dict(os.environ, {"SUPERPDP_WEBHOOK_SECRET": token}):
                return self.client.post(
                    "/webhooks/superpdp",
                    json.dumps(payload),
                    content_type="application/json",
                    HTTP_X_ZEFACTURE_WEBHOOK_SECRET=token,
                )

        self.assertEqual(relay(event).status_code, 202)
        self.assertEqual(relay(event | {"company_id": "123"}).status_code, 202)
        self.assertFalse(InvoiceEvent.objects.exists())
        qualified = event | {"company_id": "456", "environment": "production"}
        self.assertTrue(relay(qualified).json()["stored"])
        self.assertEqual(InvoiceEvent.objects.get().company_uid, self.other.company_uid)
        self.assertFalse(relay(qualified).json()["stored"])
        conflict = qualified | {
            "document_uid": Document.objects.get(
                company_uid=self.company.company_uid
            ).document_uid
        }
        self.assertEqual(relay(conflict).status_code, 409)
        self.assertEqual(InvoiceEvent.objects.count(), 1)

    def test_history_retains_current_grants_and_explicit_reinvitations(self):
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps
        from django.db import connection
        from core.models import AuditEvent

        retire = import_module(
            "core.migrations.0017_invoice_creation_and_account_recovery"
        ).retire_superseded_invitations

        def invite(email, status="pending"):
            return CompanyInvitation.objects.create(
                company_uid=self.company.company_uid,
                email=email,
                role="member",
                status=status,
                token=secrets.token_hex(32),
                expires_at="2099-01-01 00:00:00",
            )

        def record(action, target):
            AuditEvent.objects.create(
                company_uid=self.company.company_uid, action=action, target_uid=target
            )

        obsolete = invite("retire@example.test")
        invite(obsolete.email, "cancelled")
        valid = invite("nouveau@example.test")
        original = invite(self.user.email)
        former = User.objects.create_user(
            "retour@example.test", "FormerMember123!", name="Retour"
        )
        CompanyMember.objects.create(
            company_uid=self.company.company_uid,
            user_uid=former.user_uid,
            role="member",
            status="removed",
        )
        record("team.member_removed", former.user_uid)
        fresh = invite(former.email)
        record("team.invitation_created", fresh.invitation_uid)
        for _ in range(2):
            retire(apps, SimpleNamespace(connection=connection))
        for row in (obsolete, original, valid, fresh):
            row.refresh_from_db()
        self.assertEqual(
            [row.status for row in (obsolete, original, valid, fresh)],
            ["cancelled", "cancelled", "pending", "pending"],
        )
