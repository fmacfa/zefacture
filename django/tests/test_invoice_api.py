from io import BytesIO
from pathlib import Path
import secrets
from unittest.mock import patch

from django.conf import settings
from django.test import Client
from django.urls import reverse
from pypdf import PdfReader

from core.common import BusinessError, digest
from core.models import (
    ApiKey,
    Document,
    DocumentFile,
    Email,
    InvoiceEvent,
    PdpInvoice,
    PdpOutbox,
    WebhookDelivery,
    WebhookEndpoint,
)
from core.services.documents import change_status
from core.services.files import generate_document, put
from .test_application import ApplicationCase


class InvoiceApiTests(ApplicationCase):
    def setUp(self):
        super().setUp()
        self.api = Client(enforce_csrf_checks=True)
        self.key, self.auth = self.credentials(self.company)

    def credentials(self, company, scope=ApiKey.Scope.BUSINESS):
        token = secrets.token_urlsafe(24)
        row = ApiKey.objects.create(
            company_uid=company.company_uid, key_hash=digest(token), scope=scope
        )
        return row, {"HTTP_AUTHORIZATION": "Bearer " + token}

    def route(self, document, action):
        return reverse(f"api_invoice_{action}", args=[document.document_uid])

    def test_mixed_net_and_gross_prices_with_discount_and_idempotent_retry(self):
        params = self.data(
            reference="ORDER-TTC-TEST",
            global_discount_type="percent",
            global_discount_value="10",
            lines=[
                {"description": "Produit", "quantity": "1", "unit_price_including_tax": "120", "vat_rate": "20"},
                {"description": "Service", "quantity": "1", "unit_price_excluding_tax": "100", "vat_rate": "10"},
            ],
        )
        for status in (201, 200):
            response = self.api.post("/api/v1/invoices", params, content_type="application/json", **self.auth)
            self.assertEqual(response.status_code, status, response.content)
            saved = response.json()["data"]
            self.assertEqual([saved[key] for key in ("global_discount_amount", "total_excluding_tax", "total_vat", "amount_due")], [20, 180, 27, 207])
            self.assertEqual(saved["lines"][0]["unit_price_including_tax"], 120)
        self.assertEqual(Document.objects.filter(company_uid=self.company.company_uid, reference=params["reference"]).count(), 1)

    def test_commercial_details_roundtrip_in_pdf_and_structured_invoice(self):
        from lxml import etree
        from core.services.documents import save_customer
        self.company.has_vat_on_debits = 1
        self.company.save(update_fields=["has_vat_on_debits"])
        buyer = save_customer(self.company, {"legal_name":"Beispiel GmbH", "country_code":"DE", "local_registration":"HRB 4711",
                                             "address_line1":"Testweg 1", "city":"Berlin", "postal_code":"10115"})
        data = self.data(customer_uid=buyer.customer_uid, reference="ORDER-DETAILS", purchase_order="BC-124",
                         delivery_date="2026-03-03", operation_category="M1",
                         delivery_address={"line1":"Quai des Essais", "city":"Lyon", "postal_code":"69001", "country_code":"FR"})
        draft = self.api.post("/api/v1/invoices", data, content_type="application/json", **self.auth)
        self.assertEqual(draft.status_code, 201, draft.content)
        doc = Document.objects.get(document_uid=draft.json()["data"]["document_uid"])
        issue = self.api.post(self.route(doc, "issue"), {}, content_type="application/json", **self.auth)
        self.assertEqual(issue.status_code, 200, issue.content)
        for _ in range(2):
            self.assertEqual(self.api.post(self.route(doc, "delivery"), {}, content_type="application/json", **self.auth).status_code, 200)
        self.assertEqual(InvoiceEvent.objects.filter(document_uid=doc.document_uid, event_code="local:customer_handover").count(), 1)
        pdf_response = self.api.get(self.route(doc, "pdf"), **self.auth)
        reader = PdfReader(BytesIO(b"".join(pdf_response.streaming_content) if pdf_response.streaming else pdf_response.content))
        text = " ".join(page.extract_text() for page in reader.pages)
        for label in ("BC-124", "03/03/2026", "Quai des Essais", "Biens et services", "débits", "HRB 4711"):
            self.assertIn(label, text)
        root = etree.fromstring(reader.attachments["factur-x.xml"][0])
        for path, expected in (("BuyerOrderReferencedDocument/IssuerAssignedID", "BC-124"),
                               ("BusinessProcessSpecifiedDocumentContextParameter/ID", "M1"),
                               ("OccurrenceDateTime/DateTimeString", "20260303"),
                               ("ShipToTradeParty/PostalTradeAddress/LineOne", "Quai des Essais"),
                               ("ApplicableTradeTax/DueDateTypeCode", "5")):
            xpath = "//*" + "/*".join("[local-name()='" + part + "']" for part in path.split("/"))
            self.assertEqual(root.xpath(xpath + "/text()"), [expected])

    def test_customer_shipping_default_snapshot_and_explicit_billing_address(self):
        from core.services.documents import save_customer
        addr = {"line1":"Testweg 2", "city":"Berlin", "country_code":"DE"}
        save_customer(self.company, {"delivery_address":addr}, self.customer)
        self.customer.refresh_from_db()
        for idx, (changes, street) in enumerate([({}, "Testweg 2"), ({"delivery_address":{}}, None)]):
            result = self.api.post("/api/v1/invoices", self.data(reference=f"ORDER-SHIP-{idx}", **changes),
                                   content_type="application/json", **self.auth)
            self.assertEqual(result.status_code, 201)
            self.assertEqual(result.json()["data"]["delivery_address"].get("line1"), street)
        result = self.api.post("/api/v1/invoices", self.data(reference="ORDER-NO-COUNTRY",
                               delivery_address={"line1":"Rue Essai", "city":"Lyon"}),
                               content_type="application/json", **self.auth)
        self.assertEqual(result.status_code, 422)

    def test_issue_once_and_download_the_stored_facturx(self):
        WebhookEndpoint.objects.create(
            company_uid=self.company.company_uid,
            url="https://api.example.test/notifications",
            events=["document.status_changed"],
        )
        created = self.api.post(
            "/api/v1/invoices", self.data(reference="ORDER-EXAMPLE-42"),
            content_type="application/json", **self.auth,
        )
        self.assertEqual(created.status_code, 201)
        invoice = Document.objects.get(document_uid=created.json()["data"]["document_uid"])
        self.company.refresh_from_db()
        next_number = self.company.next_invoice_number

        issue_url = self.route(invoice, "issue")
        issued = self.api.post(issue_url, **self.auth)
        self.assertEqual(issued.status_code, 200, issued.content)
        data = issued.json()["data"]
        self.assertEqual(data["status"], "sent")
        self.assertEqual(invoice.number, invoice.draft_reference)
        invoice.refresh_from_db()
        self.assertEqual(data["number"], invoice.number)
        self.assertNotEqual(invoice.number, invoice.draft_reference)
        self.assertEqual(data["reference"], "ORDER-EXAMPLE-42")
        self.assertEqual(data["pdf_url"], self.route(invoice, "pdf"))
        self.assertTrue(data["issued_at"])

        with patch("core.services.files.generate_document", side_effect=AssertionError("Déjà émise")):
            repeated = self.api.post(issue_url, **self.auth)
            self.assertEqual(repeated.status_code, 200)
            self.assertEqual(repeated.json()["data"], data)
            downloaded = self.api.get(data["pdf_url"], **self.auth)
            self.assertEqual(downloaded.status_code, 200)
            head = self.api.head(data["pdf_url"], **self.auth)
            self.assertEqual(head.status_code, 200)
            self.assertFalse(head.content)
            self.assertEqual(head["Content-Length"], str(len(downloaded.content)))

        self.assertEqual(downloaded["Content-Type"], "application/pdf")
        self.assertEqual(downloaded["Cache-Control"], "private, no-store")
        self.assertTrue(downloaded["Content-Disposition"].startswith("attachment; "))
        original = invoice.files.get(kind="facturx")
        self.assertEqual(digest(downloaded.content), original.sha256)
        self.assertIn("factur-x.xml", PdfReader(BytesIO(downloaded.content)).attachments)
        self.assertEqual(invoice.files.count(), 3)
        self.assertEqual(InvoiceEvent.objects.filter(
            document_uid=invoice.document_uid, event_code="local:sent"
        ).count(), 1)
        event = WebhookDelivery.objects.get(event_type="document.status_changed")
        self.assertEqual(event.payload["data"]["status"], "sent")
        self.assertFalse(Email.objects.exists())
        self.assertFalse(PdpOutbox.objects.exists())
        self.company.refresh_from_db()
        self.assertEqual(self.company.next_invoice_number, next_number + 1)

        invoice.refresh_from_db()
        change_status(invoice, "pay")
        paid = self.api.post(issue_url, **self.auth).json()["data"]
        self.assertEqual((paid["status"], paid["issued_at"]), ("paid", data["issued_at"]))
        self.assertEqual(self.api.get(data["pdf_url"], **self.auth).content, downloaded.content)

    def test_access_checks_scope_tenant_direction_type_and_methods(self):
        invoice = self.document()
        other_invoice = Document.objects.create(company_uid=self.other.company_uid, type="invoice")
        received = Document.objects.create(
            company_uid=self.company.company_uid, direction="incoming", type="invoice"
        )
        quote = self.document(kind="quote")
        _, readonly = self.credentials(self.company, ApiKey.Scope.ASSISTANT)
        _, other_key = self.credentials(self.other)
        for method, action in ((self.api.post, "issue"), (self.api.get, "pdf")):
            with self.subTest(action=action):
                url = self.route(invoice, action)
                self.assertEqual(method(url).status_code, 401)
                self.assertEqual(method(url, **readonly).status_code, 403)
                self.assertEqual(method(url, **other_key).status_code, 404)
                for inaccessible in (other_invoice, received, quote):
                    self.assertEqual(method(self.route(inaccessible, action), **self.auth).status_code, 404)
        self.assertEqual(self.api.get(self.route(invoice, "issue"), **self.auth)["Allow"], "POST")
        self.assertEqual(self.api.post(self.route(invoice, "pdf"), **self.auth)["Allow"], "GET, HEAD")
        self.key.status = "revoked"
        self.key.save(update_fields=["status"])
        self.assertEqual(self.api.get(self.route(invoice, "pdf"), **self.auth).status_code, 401)
        self.assertFalse(DocumentFile.objects.exists())

    def test_drafts_remain_private_and_failed_issuance_preserves_the_draft(self):
        invoice = self.document(kind="progress_invoice")
        preview = generate_document(invoice)
        self.assertEqual(self.api.get(self.route(invoice, "pdf"), **self.auth).status_code, 409)
        with patch("core.services.files.validate_xml", side_effect=BusinessError("Factur-X non valide.")):
            rejected = self.api.post(self.route(invoice, "issue"), **self.auth)
        self.assertEqual(rejected.status_code, 422)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "draft")
        self.assertIsNone(invoice.sent_at)
        self.assertEqual(invoice.files.get(kind="facturx").pk, preview.pk)
        for status in ("cancelled", "simulated"):
            Document.objects.filter(pk=invoice.pk).update(status=status)
            self.assertEqual(self.api.post(self.route(invoice, "issue"), **self.auth).status_code, 409)

    def test_legacy_outgoing_pdf_is_preserved_and_corruption_is_rejected(self):
        invoice = self.document(kind="progress_invoice")
        invoice.status = "sent"
        invoice.save()
        url = self.route(invoice, "pdf")
        self.assertEqual(self.api.get(url, **self.auth).status_code, 409)
        content = b"%PDF-1.7\n% synthetic legacy invoice"
        stored = DocumentFile.objects.create(
            document_uid=invoice.document_uid,
            kind="pdf",
            storage_path=put("legacy/issued-example.pdf", content),
            sha256=digest(content),
        )
        self.assertEqual(self.api.get(url, **self.auth).content, content)
        self.assertEqual(self.api.post(self.route(invoice, "issue"), **self.auth).status_code, 200)
        Path(settings.DATA_DIR, stored.storage_path.removeprefix("storage/")).write_bytes(b"altered")
        self.assertEqual(self.api.get(url, **self.auth).status_code, 409)
        self.assertEqual(self.api.post(self.route(invoice, "issue"), **self.auth).status_code, 409)

    def test_outgoing_superpdp_xml_can_use_its_existing_readable_pdf(self):
        invoice = self.document()
        invoice.status = "sent"
        invoice.save()
        xml = DocumentFile.objects.create(
            document_uid=invoice.document_uid, kind="pdp_original", mime_type="application/xml",
            storage_path=put("legacy/issued-example.xml", b"<invoice/>"),
        )
        content = b"%PDF-1.7\n% synthetic readable invoice"
        readback = DocumentFile.objects.create(
            document_uid=invoice.document_uid, kind="pdp_readable", mime_type="application/pdf",
            storage_path=put("legacy/readable-example.pdf", content), sha256=digest(content),
        )
        link = PdpInvoice.objects.create(
            company_uid=invoice.company_uid, remote_id="102", remote_company_id="3",
            remote_environment="production", document_uid=invoice.document_uid,
            direction="outgoing", invoice_number=invoice.number, imported=True,
            original_file_uid=xml.file_uid, readable_file_uid=readback.file_uid,
        )
        with patch("core.services.files.generate_document", side_effect=AssertionError("Déjà importée")):
            url = self.route(invoice, "pdf")
            self.assertEqual(self.api.get(url, **self.auth).content, content)
            link.readable_file_uid = ""
            link.save(update_fields=["readable_file_uid"])
            self.assertEqual(self.api.get(url, **self.auth).status_code, 409)
