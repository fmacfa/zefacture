import copy
import json
import os
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse, urlencode

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from core.common import BusinessError, digest
from core.models import (
    CompanyMember,
    Customer,
    Document,
    DocumentFile,
    InvoiceEvent,
    OAuthAttempt,
    PdpInvoice,
    PdpOutbox,
    PdpRemoteEvent,
    PdpSession,
    PdpSyncState,
)
from core.services import pdp, pdp_sync
from core.services.files import encrypt, generate_document, put, read_verified
from core.services.documents import save_document
from core.views.documents import send
from tests.test_application import ApplicationCase
from tests.pdp_provider import (
    Provider,
    invoice,
    event,
    from_document,
    pdf_bytes,
    COMPANY_ID,
    FIRST_INVOICE_ID,
    FIRST_EVENT_ID,
)


@override_settings(ENVIRONMENT="testing")
class SuperpdpTests(ApplicationCase):
    def setUp(self):
        super().setUp()
        self.provider = Provider()
        self.addCleanup(self.provider.close)
        self.environment = patch.dict(
            os.environ,
            {
                "SUPERPDP_TEST_BASE_URL": self.provider.origin,
                "SUPERPDP_OAUTH_CLIENT_ID": "synthetic-client",
                "SUPERPDP_OAUTH_CLIENT_SECRET": "synthetic-secret",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.session = PdpSession.objects.create(
            company_uid=self.company.company_uid,
            api_base=self.provider.origin,
            client_id="synthetic-client",
            client_secret_encrypted=encrypt("synthetic-secret"),
        )

    def sync(self):
        return pdp_sync.sync_company(self.company.company_uid, force=True)

    def original(self, document, raw=None):
        raw = raw or pdf_bytes()
        return DocumentFile.objects.create(
            document_uid=document.document_uid,
            kind="facturx",
            storage_path=put("test/" + document.document_uid + ".pdf", raw),
            sha256=digest(raw),
            file_size=len(raw),
            mime_type="application/pdf",
        )

    def test_receive_paginated_invoices_originals_events_and_repeat_without_duplicates(
        self,
    ):
        for i in range(3):
            self.provider.add(invoice(FIRST_INVOICE_ID + i, number=f"FOURN-{i}"))
            value = event(FIRST_INVOICE_ID + i, FIRST_EVENT_ID + i)
            self.provider.events[value["id"]] = value
        self.assertTrue(self.sync())
        self.assertEqual(PdpInvoice.objects.count(), 3)
        self.assertEqual(PdpRemoteEvent.objects.count(), 3)
        self.assertEqual(Document.objects.filter(direction="incoming").count(), 3)
        state = PdpSyncState.objects.get()
        self.assertEqual(state.invoice_cursor, FIRST_INVOICE_ID + 2)
        self.assertEqual(state.event_cursor, FIRST_EVENT_ID + 2)
        original_hashes = list(DocumentFile.objects.values_list("sha256", flat=True))
        self.assertTrue(self.sync())
        pdp_sync.request_sync(self.company, replay=True)
        self.assertTrue(self.sync())
        self.assertEqual(Document.objects.count(), 3)
        self.assertEqual(PdpRemoteEvent.objects.count(), 3)
        self.assertEqual(
            list(DocumentFile.objects.values_list("sha256", flat=True)), original_hashes
        )
        document = Document.objects.first()
        self.assertEqual(document.amount_due, Decimal("120"))
        self.assertEqual(document.total_vat, Decimal("20"))
        self.assertEqual(
            read_verified(generate_document(document)),
            self.provider.originals[FIRST_INVOICE_ID][0],
        )
        self.assertContains(self.client.get("/app/reception"), "FOURN-0")
        self.assertContains(self.client.get(document.url), "Suivi SUPERPDP")
        self.assertContains(self.client.get(document.url), "Reçue")

    def test_resume_after_original_download_failure_keeps_committed_cursor(self):
        for i in range(3):
            self.provider.add(invoice(FIRST_INVOICE_ID + i, number=f"RESUME-{i}"))
        path = f"/v1.beta/invoices/{FIRST_INVOICE_ID + 1}?format=original"
        self.provider.failures[("GET", path)] = 503
        with self.assertRaises(BusinessError):
            self.sync()
        state = PdpSyncState.objects.get()
        self.assertEqual(state.invoice_cursor, FIRST_INVOICE_ID)
        self.assertEqual(Document.objects.count(), 1)
        self.assertIn("503", state.last_error)
        self.assertNotIn("synthetic-provider-secret", state.last_error)
        self.provider.failures.clear()
        self.assertTrue(self.sync())
        self.assertEqual(Document.objects.count(), 3)

    def test_xml_original_is_preserved_and_readable_pdf_is_separate(self):
        raw = b'<?xml version="1.0"?><Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"/>'
        self.provider.add(raw=raw, mime="application/xml")
        self.sync()
        link = PdpInvoice.objects.get()
        document = Document.objects.get(document_uid=link.document_uid)
        self.assertEqual(read_verified(generate_document(document)), raw)
        self.assertEqual(
            DocumentFile.objects.get(file_uid=link.original_file_uid).mime_type,
            "application/xml",
        )
        self.assertEqual(
            DocumentFile.objects.get(file_uid=link.readable_file_uid).mime_type,
            "application/pdf",
        )
        response = self.client.get(document.url + "/telecharger")
        self.assertEqual(response.content, raw)
        self.assertEqual(response["Content-Type"], "application/xml")

    def test_original_larger_than_json_limit_is_downloaded_without_truncation(self):
        raw = pdf_bytes() + b"\n%" + b"x" * 2_100_000
        self.provider.add(raw=raw)
        self.sync()
        file = DocumentFile.objects.get(kind="pdp_original")
        self.assertEqual(read_verified(file), raw)
        self.assertEqual(file.file_size, len(raw))

    def test_original_size_type_and_xml_entity_fail_closed(self):
        for raw, mime in [
            (b"<html>error</html>", "application/pdf"),
            (
                b'<!DOCTYPE Invoice [<!ENTITY x SYSTEM "file:///etc/passwd">]><Invoice>&x;</Invoice>',
                "application/xml",
            ),
        ]:
            self.provider.add(raw=raw, mime=mime)
            with self.assertRaises(BusinessError):
                self.sync()
            self.assertFalse(Document.objects.exists())
            self.assertEqual(PdpSyncState.objects.get().invoice_cursor, 0)
        self.provider.add(raw=pdf_bytes())
        with override_settings(SUPERPDP_MAX_ORIGINAL_BYTES=20):
            with self.assertRaises(BusinessError):
                self.sync()
        self.assertFalse(Document.objects.exists())

    def test_existing_original_is_never_silently_replaced(self):
        self.provider.add()
        self.sync()
        file = DocumentFile.objects.get(kind="pdp_original")
        from core.services.files import resolve

        resolve(file.storage_path).write_bytes(b"corrupted")
        pdp_sync.request_sync(self.company, replay=True)
        with self.assertRaises(BusinessError):
            self.sync()
        self.assertEqual(resolve(file.storage_path).read_bytes(), b"corrupted")
        self.assertEqual(Document.objects.count(), 1)

    def test_company_and_environment_boundaries_are_enforced_before_import(self):
        value = invoice()
        value["company_id"] = COMPANY_ID + 1
        self.provider.add(value)
        with self.assertRaises(BusinessError):
            self.sync()
        self.assertFalse(Document.objects.exists())
        self.provider.invoices.clear()
        self.provider.environment = "sandbox"
        with self.assertRaises(BusinessError):
            self.sync()
        self.assertFalse(Document.objects.exists())

    def test_unverified_company_and_mismatched_siren_cannot_send_or_receive(self):
        self.provider.verification = "needs_review"
        self.provider.add()
        with self.assertRaises(BusinessError):
            self.sync()
        self.assertFalse(Document.objects.exists())
        self.provider.verification = "verified"
        self.provider.number = "999999999"
        with self.assertRaises(BusinessError):
            self.sync()
        self.assertFalse(Document.objects.exists())

    def test_remote_events_are_retained_without_rewriting_payment_or_amounts(self):
        self.provider.add()
        self.sync()
        document = Document.objects.get()
        document.status, document.amount_due = "accepted", Decimal("70")
        document.save()
        before = Document.objects.values().get()
        for i, code in enumerate(
            ["fr:205", "fr:207", "fr:212", "fr:220", "future:unknown"]
        ):
            value = event(identifier=FIRST_EVENT_ID + i, code=code)
            self.provider.events[value["id"]] = value
        self.sync()
        self.assertEqual(Document.objects.values().get(), before)
        link = PdpInvoice.objects.get()
        self.assertEqual(link.latest_status, "future:unknown")
        self.assertEqual(len(link.status_codes), 5)
        self.assertEqual(InvoiceEvent.objects.filter(provider="superpdp").count(), 5)
        self.assertTrue(
            PdpRemoteEvent.objects.filter(remote_id=FIRST_EVENT_ID).exists()
        )

    def test_out_of_order_event_import_does_not_regress_latest_and_changes_are_rejected(
        self,
    ):
        self.provider.add()
        self.sync()
        state = PdpSyncState.objects.get()
        self.session.refresh_from_db()
        latest = event(identifier=FIRST_EVENT_ID + 1, code="fr:205")
        older = event(code="fr:202")
        pdp_sync.ingest_event(self.company, self.session, state, latest)
        pdp_sync.ingest_event(self.company, self.session, state, older)
        self.assertEqual(PdpInvoice.objects.get().latest_status, "fr:205")
        with self.assertRaises(BusinessError):
            pdp_sync.ingest_event(
                self.company, self.session, state, latest | {"status_code": "fr:210"}
            )
        self.assertEqual(PdpRemoteEvent.objects.count(), 2)

    def test_event_can_discover_invoice_and_resume_independently_of_invoice_cursor(
        self,
    ):
        self.provider.add()
        self.provider.events[FIRST_EVENT_ID] = event()
        state = pdp_sync.state_for(self.company)
        state.invoice_cursor = FIRST_INVOICE_ID + 10
        state.save()
        self.sync()
        self.assertEqual(PdpInvoice.objects.count(), 1)
        self.assertEqual(PdpRemoteEvent.objects.count(), 1)

    def test_incoming_credit_note_and_supplier_number_collision_preserve_existing_record(
        self,
    ):
        existing = self.document()
        before = Document.objects.values().get(pk=existing.pk)
        value = invoice(number=existing.number)
        value["en_invoice"]["type_code"] = 381
        self.provider.add(value)
        self.sync()
        self.assertEqual(Document.objects.values().get(pk=existing.pk), before)
        document = Document.objects.get(direction="incoming")
        self.assertEqual(document.type, "credit_note")
        self.assertEqual(document.amount_due, 0)
        self.assertNotEqual(document.number, existing.number)
        self.assertEqual(PdpInvoice.objects.get().invoice_number, existing.number)
        self.assertContains(self.client.get("/app/reception"), document.number)

    def test_totals_allowances_and_vat_are_preserved_without_recalculating_unit_prices(
        self,
    ):
        value = invoice()
        data = value["en_invoice"]
        data["totals"].update(
            total_without_vat="90.00",
            total_vat_amount={"value": "18.00"},
            total_with_vat="108.00",
            amount_due_for_payment="108.00",
            sum_allowances_amount="10.00",
        )
        data["vat_break_down"][0].update(
            vat_category_taxable_amount="90.00", vat_category_tax_amount="18.00"
        )
        data["document_level_allowances"] = [{"amount": "10.00"}]
        data["lines"][0]["price_details"]["item_net_price"] = "50.000000000123"
        self.provider.add(value)
        self.sync()
        document = Document.objects.get()
        self.assertEqual(document.total_including_tax, Decimal("108"))
        self.assertEqual(
            sum(line.total_excluding_tax for line in document.lines), Decimal("90")
        )
        self.assertEqual(sum(line.total_vat for line in document.lines), Decimal("18"))
        self.assertEqual(PdpInvoice.objects.get().payload, value)
        self.assertEqual(document.lines.count(), 2)

    def test_invalid_totals_do_not_advance_cursor_or_create_partial_customer(self):
        value = invoice()
        value["en_invoice"]["totals"]["total_with_vat"] = "999.00"
        self.provider.add(value)
        customers = Customer.objects.count()
        with self.assertRaises(BusinessError):
            self.sync()
        self.assertFalse(Document.objects.exists())
        self.assertEqual(Customer.objects.count(), customers)
        self.assertEqual(PdpSyncState.objects.get().invoice_cursor, 0)

    def test_send_facturx_is_durable_uses_original_and_never_replays_success(self):
        document = self.document()
        original = generate_document(document)
        self.provider.posted_invoice = from_document(document)
        send(document, "superpdp")
        operation = PdpOutbox.objects.get()
        self.assertEqual(operation.status, "queued")
        self.assertFalse(
            any(
                r[0] == "POST" and r[1].startswith("/v1.beta/invoices?")
                for r in self.provider.requests
            )
        )
        self.assertEqual(pdp_sync.flush_outbox(), 1)
        self.assertEqual(pdp_sync.flush_outbox(), 0)
        self.sync()
        document.refresh_from_db()
        self.assertEqual(document.pdp_invoice_id, str(FIRST_INVOICE_ID))
        self.assertEqual(Document.objects.count(), 1)
        self.assertFalse(PdpInvoice.objects.get().imported)
        post = next(
            r
            for r in self.provider.requests
            if r[0] == "POST" and r[1].startswith("/v1.beta/invoices?")
        )
        frozen = DocumentFile.objects.get(file_uid=operation.file_uid)
        self.assertEqual(post[3], read_verified(frozen))
        self.assertEqual(digest(read_verified(original)), original.sha256)
        self.assertEqual(
            parse_qs(urlparse(post[1]).query)["external_id"], [document.document_uid]
        )

    def test_uncertain_deposit_is_found_by_external_id_without_resending_or_losing_payment(
        self,
    ):
        document = self.document()
        original = self.original(document)
        self.provider.posted_invoice = from_document(document)
        self.provider.disconnect_after_accept = True
        pdp_sync.queue_invoice(document, original)
        document.refresh_from_db()
        document.amount_due, document.status = Decimal("70"), "sent"
        document.save()
        self.assertEqual(pdp_sync.flush_outbox(), 0)
        self.assertEqual(PdpOutbox.objects.get().status, "uncertain")
        self.assertEqual(pdp_sync.flush_outbox(), 0)
        self.sync()
        self.assertEqual(PdpOutbox.objects.get().status, "sent")
        self.assertEqual(Document.objects.count(), 1)
        document.refresh_from_db()
        self.assertEqual(document.amount_due, Decimal("70"))
        self.assertEqual(
            sum(
                r[0] == "POST" and r[1].startswith("/v1.beta/invoices?")
                for r in self.provider.requests
            ),
            1,
        )

    def test_failed_or_interrupted_post_is_not_automatically_retried(self):
        document = self.document()
        pdp_sync.queue_invoice(document, self.original(document))
        operation = PdpOutbox.objects.get()
        path = "/v1.beta/invoices?" + urlencode(operation.payload)
        self.provider.failures[("POST", path)] = 400
        self.assertEqual(pdp_sync.flush_outbox(), 0)
        operation.refresh_from_db()
        self.assertEqual(operation.status, "failed")
        self.assertNotIn("synthetic-provider-secret", operation.last_error)
        self.provider.failures.clear()
        self.assertEqual(pdp_sync.flush_outbox(), 0)
        pdp_sync.retry_failed(self.company, operation.operation_uid)
        self.assertEqual(pdp_sync.flush_outbox(), 1)
        operation.refresh_from_db()
        operation.status, operation.started_at = (
            "sending",
            timezone.now() - timedelta(minutes=6),
        )
        operation.save()
        self.assertEqual(pdp_sync.flush_outbox(), 0)
        operation.refresh_from_db()
        self.assertEqual(operation.status, "uncertain")
        with self.assertRaises(BusinessError):
            pdp_sync.retry_failed(self.company, operation.operation_uid)

    def test_lifecycle_declaration_is_explicit_and_deduplicated(self):
        self.provider.add()
        self.sync()
        document = Document.objects.get()
        document.status = "disputed"
        document.save()
        with self.assertRaises(BusinessError):
            pdp_sync.queue_status(document, "fr:207")
        first = pdp_sync.queue_status(document, "fr:207", "SYNTHETIC", "Motif de test")
        self.assertEqual(
            pdp_sync.queue_status(document, "fr:207", "SYNTHETIC", "Motif de test").pk,
            first.pk,
        )
        self.assertEqual(pdp_sync.flush_outbox(), 1)
        self.sync()
        self.assertEqual(PdpRemoteEvent.objects.get().status_code, "fr:207")
        request = next(
            r
            for r in self.provider.requests
            if r[0] == "POST" and r[1] == "/v1.beta/invoice_events"
        )
        body = json.loads(request[3])
        contract = json.loads(
            (Path(__file__).parent / "fixtures/superpdp-contract.json").read_text()
        )
        schema = contract["operations"]["POST /v1.beta/invoice_events"][
            "request_schema"
        ]
        self.assertLessEqual(set(schema["required"]), set(body))
        self.assertLessEqual(set(body), set(schema["properties"]))
        self.assertIn(body["status_code"], contract["status_code_create"])
        self.assertEqual(body["invoice_id"], FIRST_INVOICE_ID)
        self.assertEqual(body["details"][0]["reason"], "SYNTHETIC")
        document.refresh_from_db()
        self.assertEqual(document.amount_due, Decimal("120"))

    def test_sandbox_documents_never_enter_live_financial_totals(self):
        self.provider.environment = "sandbox"
        self.provider.add()
        self.sync()
        document = Document.objects.get()
        self.assertEqual(document.status, "simulated")
        self.assertEqual(document.amount_due, 0)
        response = self.client.get("/app/rapports?preset=all")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            any(item["value"] for item in response.context["report"]["metrics"])
        )

    def test_controls_require_roles_and_cross_tenant_document_is_inaccessible(self):
        self.provider.add()
        self.sync()
        document = Document.objects.get()
        self.assertContains(
            self.client.get("/app/entreprise/pdp"), "Synchroniser maintenant"
        )
        membership = CompanyMember.objects.get(
            company_uid=self.company.company_uid, user_uid=self.user.user_uid
        )
        membership.role = "viewer"
        membership.save()
        self.assertEqual(
            self.client.post("/app/entreprise/pdp", {"action": "sync"}).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                f"/app/documents/{document.document_uid}/pdp", {"action": "sync"}
            ).status_code,
            403,
        )
        membership.role = "member"
        membership.save()
        document.status, document.amount_due = "paid", 0
        document.save()
        self.assertEqual(
            self.client.post(
                f"/app/documents/{document.document_uid}/pdp", {"status_code": "fr:211"}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(document.url + "/telecharger").status_code, 200
        )
        session = self.client.session
        session["company_uid"] = self.other.company_uid
        session.save()
        CompanyMember.objects.create(
            company_uid=self.other.company_uid,
            user_uid=self.user.user_uid,
            role="owner",
        )
        self.assertEqual(self.client.get(document.url).status_code, 404)
        self.assertEqual(
            self.client.post(
                f"/app/documents/{document.document_uid}/pdp", {"action": "sync"}
            ).status_code,
            404,
        )

    def test_paused_synchronization_lease_and_external_recovery_lock(self):
        state = pdp_sync.state_for(self.company)
        self.assertFalse(state.enabled)
        self.assertFalse(pdp_sync.sync_company(self.company.company_uid))
        state.lease, state.lease_until = (
            "synthetic-lease",
            timezone.now() + timedelta(minutes=5),
        )
        state.save()
        self.assertFalse(self.sync())
        with self.assertRaises(BusinessError):
            pdp_sync.request_sync(self.company, replay=True)
        state.lease, state.lease_until = "", None
        state.save()
        pdp_sync.request_sync(self.company, enabled=False)
        with override_settings(EXTERNAL_SERVICES_DISABLED=True):
            self.assertEqual(pdp_sync.sync_due(), 0)
            self.assertEqual(pdp_sync.flush_outbox(), 0)
            with self.assertRaises(BusinessError):
                self.sync()
        self.assertFalse(self.provider.requests)

    def test_oauth_state_is_durable_one_use_and_credentials_are_not_rebound(self):
        response = self.client.post("/app/entreprise/pdp/connecter")
        self.assertEqual(response.status_code, 302)
        state = parse_qs(urlparse(response.url).query)["state"][0]
        pending = copy.deepcopy(self.client.session["pdp_oauth"])
        url = "/app/entreprise/pdp/callback?state=" + state + "&code=synthetic-code"
        self.assertEqual(self.client.get(url).status_code, 302)
        self.assertIsNotNone(OAuthAttempt.objects.get().consumed_at)
        session = self.client.session
        session["pdp_oauth"] = pending
        session.save()
        self.assertEqual(self.client.get(url).status_code, 403)
        old_secret = PdpSession.objects.get().client_secret_encrypted
        self.assertEqual(
            self.client.post(
                "/app/entreprise/pdp", {"client_id": "changed-client"}
            ).status_code,
            422,
        )
        self.assertEqual(PdpSession.objects.get().client_secret_encrypted, old_secret)

    def test_command_only_synchronizes_and_does_not_emit_invoices(self):
        self.provider.add()
        import io

        call_command(
            "sync_superpdp", company=self.company.company_uid, stdout=io.StringIO()
        )
        self.assertEqual(Document.objects.count(), 1)
        self.assertFalse(
            any(
                row[0] == "POST" and row[1].startswith("/v1.beta/invoices")
                for row in self.provider.requests
            )
        )

    def test_existing_manual_invoice_links_by_exact_original_without_rewriting_legacy_fields(
        self,
    ):
        data = self.data(
            number="FOURN-2026-001", issue_date="2026-11-06", due_date="2026-12-06"
        )
        document = save_document(
            self.company, self.user, "invoice", data, direction="incoming"
        )
        raw = pdf_bytes()
        original = DocumentFile.objects.create(
            document_uid=document.document_uid,
            kind="source",
            storage_path=put("manual/source.pdf", raw),
            sha256=digest(raw),
            file_size=len(raw),
            mime_type="application/pdf",
        )
        before = Document.objects.values().get()
        value = invoice()
        value["en_invoice"]["seller"]["name"] = self.customer.legal_name
        self.provider.add(value, raw)
        self.sync()
        self.assertEqual(Document.objects.count(), 1)
        self.assertEqual(Document.objects.values().get(), before)
        self.assertEqual(read_verified(original), raw)
        self.assertFalse(PdpInvoice.objects.get().imported)

    def test_remote_outgoing_invoice_keeps_original_and_does_not_consume_numbering(
        self,
    ):
        value = invoice(direction="out", number="EXTERNE-0042")
        self.provider.add(value)
        counter = self.company.next_invoice_number
        self.sync()
        document = Document.objects.get()
        self.assertEqual(document.number, "EXTERNE-0042")
        self.assertEqual(document.direction, "outgoing")
        self.assertEqual(
            read_verified(generate_document(document)),
            self.provider.originals[FIRST_INVOICE_ID][0],
        )
        self.company.refresh_from_db()
        self.assertEqual(self.company.next_invoice_number, counter)

    def test_recovery_after_database_failure_reuses_verified_blob_without_duplicates(
        self,
    ):
        self.provider.add()
        with patch.object(
            PdpInvoice.objects,
            "create",
            side_effect=BusinessError("synthetic interruption"),
        ):
            with self.assertRaises(BusinessError):
                self.sync()
        self.assertFalse(Document.objects.exists())
        self.assertFalse(DocumentFile.objects.exists())
        self.assertEqual(PdpSyncState.objects.get().invoice_cursor, 0)
        self.assertTrue(self.sync())
        self.assertEqual(Document.objects.count(), 1)
        self.assertEqual(DocumentFile.objects.filter(kind="pdp_original").count(), 1)

    def test_worker_send_confirmation_does_not_reset_a_payment_made_while_queued(self):
        document = self.document()
        self.provider.posted_invoice = from_document(document)
        pdp_sync.queue_invoice(document, self.original(document))
        document.refresh_from_db()
        document.amount_due, document.status, document.paid_at = (
            0,
            "paid",
            "2026-11-06 11:00:00",
        )
        document.save()
        self.assertEqual(pdp_sync.flush_outbox(), 1)
        self.sync()
        document.refresh_from_db()
        self.assertEqual(document.amount_due, 0)
        self.assertEqual(document.status, "paid")
        self.assertEqual(document.paid_at, "2026-11-06 11:00:00")

    def test_expired_technical_token_refresh_uses_its_own_client_pair(self):
        self.sync()
        self.session.refresh_from_db()
        self.session.token_expires_at = "2000-01-01 00:00:00"
        self.session.save()
        with patch.dict(
            os.environ,
            {
                "SUPERPDP_OAUTH_CLIENT_ID": "other-global-app",
                "SUPERPDP_OAUTH_CLIENT_SECRET": "other-global-secret",
            },
        ):
            pdp.access_token(self.session)
        token_calls = [
            row
            for row in self.provider.requests
            if row[0] == "POST" and row[1] == "/oauth2/token"
        ]
        form = parse_qs(token_calls[-1][3].decode())
        self.assertEqual(form["grant_type"], ["refresh_token"])
        self.assertEqual(form["client_id"], ["synthetic-client"])

    def test_failed_oauth_callback_consumes_state_before_exchange(self):
        response = self.client.post("/app/entreprise/pdp/connecter")
        state = parse_qs(urlparse(response.url).query)["state"][0]
        pending = copy.deepcopy(self.client.session["pdp_oauth"])
        self.provider.failures[("POST", "/oauth2/token")] = 400
        url = "/app/entreprise/pdp/callback?state=" + state + "&code=synthetic-code"
        self.assertEqual(self.client.get(url).status_code, 502)
        self.assertIsNotNone(OAuthAttempt.objects.get().consumed_at)
        session = self.client.session
        session["pdp_oauth"] = pending
        session.save()
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            sum(row[1] == "/oauth2/token" for row in self.provider.requests), 1
        )

    def test_uncertain_post_requires_a_fresh_sync_and_explicit_absence_confirmation(
        self,
    ):
        document = self.document()
        self.provider.posted_invoice = from_document(document)
        pdp_sync.queue_invoice(document, self.original(document))
        operation = PdpOutbox.objects.get()
        path = "/v1.beta/invoices?" + urlencode(operation.payload)
        self.provider.failures[("POST", path)] = 500
        pdp_sync.flush_outbox()
        self.assertEqual(PdpOutbox.objects.get().status, "uncertain")
        with self.assertRaises(BusinessError):
            pdp_sync.retry_uncertain(
                self.company, operation.operation_uid, confirmed=True
            )
        self.provider.failures.clear()
        self.sync()
        with self.assertRaises(BusinessError):
            pdp_sync.retry_uncertain(self.company, operation.operation_uid)
        pdp_sync.retry_uncertain(self.company, operation.operation_uid, confirmed=True)
        self.assertEqual(pdp_sync.flush_outbox(), 1)
        with self.assertRaises(BusinessError):
            pdp_sync.retry_uncertain(
                self.company, operation.operation_uid, confirmed=True
            )

    def test_sandbox_lifecycle_declaration_keeps_document_simulated(self):
        self.provider.environment = "sandbox"
        self.provider.add()
        self.sync()
        document = Document.objects.get()
        response = self.client.post(
            f"/app/documents/{document.document_uid}/pdp", {"status_code": "fr:205"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(pdp_sync.flush_outbox(), 1)
        self.sync()
        document.refresh_from_db()
        self.assertEqual(document.status, "simulated")
        self.assertEqual(document.amount_due, 0)
        self.assertEqual(PdpInvoice.objects.get().latest_status, "fr:205")

    def test_optional_vat_total_uses_the_exact_provider_breakdown(self):
        value = invoice()
        del value["en_invoice"]["totals"]["total_vat_amount"]
        self.provider.add(value)
        self.sync()
        self.assertEqual(Document.objects.get().total_vat, Decimal("20"))

    def test_legacy_simulation_cannot_become_a_real_post_in_production(self):
        self.session.status = "sandbox"
        self.session.save()
        document = self.document()
        with override_settings(ENVIRONMENT="production"):
            with self.assertRaises(BusinessError):
                pdp_sync.queue_invoice(document, self.original(document))
        self.assertFalse(self.provider.requests)

    def test_upload_keeps_the_token_verified_before_a_concurrent_connection_edit(self):
        document = self.document()
        self.provider.posted_invoice = from_document(document)
        pdp_sync.queue_invoice(document, self.original(document))
        original_binding = pdp_sync._binding

        def concurrent_edit(*args, **kwargs):
            state = original_binding(*args, **kwargs)
            PdpSession.objects.filter(pk=self.session.pk).update(
                access_token_encrypted=encrypt("different-account-token")
            )
            return state

        with patch.object(pdp_sync, "_binding", side_effect=concurrent_edit):
            self.assertEqual(pdp_sync.flush_outbox(), 1)
        post = next(
            row
            for row in self.provider.requests
            if row[0] == "POST" and row[1].startswith("/v1.beta/invoices?")
        )
        self.assertEqual(post[2]["Authorization"], "Bearer synthetic-access")

    def test_downloads_keep_the_same_verified_token_for_the_whole_sync(self):
        self.provider.add()
        original_binding = pdp_sync._binding

        def concurrent_edit(*args, **kwargs):
            state = original_binding(*args, **kwargs)
            PdpSession.objects.filter(pk=self.session.pk).update(
                access_token_encrypted=encrypt("different-account-token")
            )
            return state

        with patch.object(pdp_sync, "_binding", side_effect=concurrent_edit):
            self.sync()
        reads = [
            row
            for row in self.provider.requests
            if row[1].startswith("/v1.beta/invoices")
        ]
        self.assertTrue(reads)
        self.assertTrue(
            all(row[2]["Authorization"] == "Bearer synthetic-access" for row in reads)
        )
