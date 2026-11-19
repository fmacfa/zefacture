from decimal import Decimal
from io import BytesIO

from django.test import Client
from PIL import Image
from pypdf import PdfWriter

from core.common import digest, uid
from core.models import (
    CompanyMember,
    Customer,
    CustomerContact,
    Document,
    DocumentContact,
    DocumentFile,
    DocumentNote,
    Ingestion,
    PdpInvoice,
)
from core.services import files
from .test_application import ApplicationCase


def pdf_bytes(password=None):
    out = BytesIO()
    writer = PdfWriter()
    for width, height in [(210, 300), (300, 210)]:
        writer.add_blank_page(width=width, height=height)
    if password:
        writer.encrypt(password)
    writer.write(out)
    return out.getvalue()


class InvoiceWorkspaceTests(ApplicationCase):
    def invoice(self, **changes):
        values = dict(
            company_uid=self.company.company_uid,
            customer_uid=self.customer.customer_uid,
            direction="incoming",
            type="invoice",
            status="received",
            issue_date="2026-11-06",
            total_excluding_tax=100,
            total_vat=20,
            total_including_tax=120,
            amount_due=45,
        )
        return Document.objects.create(**(values | changes))

    def metadata(self, raw, ext, mime):
        return dict(
            storage_path=files.put(f"fixtures/{uid()}.{ext}", raw),
            mime_type=mime,
            file_size=len(raw),
            sha256=digest(raw),
        )

    def attachment(self, doc, raw, ext="pdf", mime="application/pdf", kind="source"):
        return DocumentFile.objects.create(
            document_uid=doc.document_uid, kind=kind, **self.metadata(raw, ext, mime)
        )

    def preview(self, doc, file):
        return f"/app/documents/{doc.document_uid}/fichiers/{file.file_uid}/apercu"

    def test_pdf_navigation_original_xml_and_private_page_raster(self):
        doc = self.invoice(number="P-1")
        xml = self.attachment(doc, b"<Invoice/>", "xml", "text/xml", "pdp_original")
        raw = pdf_bytes()
        pdf = self.attachment(doc, raw, kind="pdp_readable")
        PdpInvoice.objects.create(
            company_uid=doc.company_uid,
            document_uid=doc.document_uid,
            direction="in",
            remote_id="1",
            remote_company_id="1",
            remote_environment="sandbox",
            original_file_uid=xml.file_uid,
            readable_file_uid=pdf.file_uid,
            invoice_number="P-1",
        )
        url = self.preview(doc, pdf)
        self.assertContains(self.client.get("/app/reception"), url)
        self.assertContains(self.client.get(doc.url), url)
        self.assertContains(self.client.get(url), "Page 1 sur 2")
        self.assertContains(self.client.get(url), "Télécharger le PDF de lecture")
        sizes = []
        for page in (1, 2):
            response = self.client.get(url, {"page": page, "image": "1"})
            self.assertEqual(response["Content-Type"], "image/png")
            self.assertEqual(response["Cache-Control"], "private, no-store")
            with Image.open(BytesIO(response.content)) as image:
                image.load()
                sizes.append(image.size)
                self.assertEqual(max(image.size), 1800)
        self.assertEqual(sizes[0], tuple(reversed(sizes[1])))
        self.assertEqual(self.client.get(url.removesuffix("/apercu")).content, raw)
        for value in ("", "0", "3", "n"):
            self.assertEqual(self.client.get(url, {"page": value}).status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 405)
        self.assertEqual(self.client.get(self.preview(doc, xml)).status_code, 415)
        self.assertRedirects(
            Client().get(url, {"image": 1}), "/connexion", fetch_redirect_response=False
        )

    def test_image_import_multipage_exif_and_linked_amounts(self):
        out = BytesIO()
        first = Image.new("RGB", (50, 80), "red")
        second = Image.new("RGB", (80, 50), "green")
        first.save(out, "TIFF", save_all=True, append_images=[second])
        item = Ingestion.objects.create(
            company_uid=self.company.company_uid,
            original_filename="Ticket.tif",
            source_kind="receipt",
            uploaded_by_user_uid=self.user.user_uid,
            expense_amount=12,
            **self.metadata(out.getvalue(), "tif", None),
        )
        base = f"/app/imports/{item.ingestion_uid}"
        self.assertContains(self.client.get(base), base + "/apercu")
        self.assertContains(self.client.get(base + "/apercu"), "Total TTC enregistré")
        with Image.open(
            BytesIO(self.client.get(base + "/apercu", {"page": 2, "image": 1}).content)
        ) as image:
            self.assertEqual(
                (image.size, image.getpixel((0, 0))), ((80, 50), (0, 128, 0))
            )
        doc = self.invoice()
        item.document_uid = doc.document_uid
        item.save()
        self.assertContains(self.client.get(base + "/apercu"), "Total HT")
        out = BytesIO()
        exif = Image.Exif()
        exif[274] = 6
        first.save(out, "JPEG", exif=exif)
        jpg = self.attachment(doc, out.getvalue(), "jpg", "image/jpeg")
        with Image.open(
            BytesIO(self.client.get(self.preview(doc, jpg), {"image": 1}).content)
        ) as image:
            self.assertEqual(image.size, (80, 50))

    def test_failure_and_permissions_on_every_file_and_amount(self):
        doc = self.invoice()
        miss = self.attachment(doc, pdf_bytes("synthetic-password"))
        url = self.preview(doc, miss)
        self.assertContains(
            self.client.get(url), "Télécharger l’original", status_code=422
        )
        files.resolve(miss.storage_path).write_bytes(b"changed")
        self.assertEqual(self.client.get(url, {"image": 1}).status_code, 409)
        wrong = self.invoice(company_uid=self.other.company_uid)
        otherfile = self.attachment(wrong, pdf_bytes())
        self.assertEqual(
            self.client.get(self.preview(doc, otherfile), {"image": 1}).status_code, 404
        )
        self.assertEqual(
            self.client.get(self.preview(wrong, otherfile)).status_code, 404
        )
        item = Ingestion.objects.create(
            company_uid=doc.company_uid,
            document_uid=doc.document_uid,
            source_kind="expense_note",
            uploaded_by_user_uid=uid(),
        )
        CompanyMember.objects.filter(company_uid=doc.company_uid).update(role="member")
        for query in ({}, {"image": "1"}):
            self.assertEqual(self.client.get(url, query).status_code, 404)
            self.assertEqual(
                self.client.get(
                    f"/app/imports/{item.ingestion_uid}/apercu", query
                ).status_code,
                404,
            )
        self.assertEqual(
            list(self.client.get("/app/reception").context["reception_totals"]), []
        )

    def test_filtered_totals_credits_currencies_full_pagination_and_status_cards(self):
        for i in range(32):
            self.invoice(number=f"Atelier-{i}")
        self.invoice(number="Atelier-avoir", type="credit_note", amount_due=0)
        self.invoice(number="Atelier-GBP", currency_code="GBP")
        self.invoice(number="Atelier-payé", status="paid", amount_due=0)
        self.invoice(number="Atelier-hors-période", issue_date="2026-12-01")
        self.invoice(number="Sans rapport")
        self.invoice(number="Atelier-vente", direction="outgoing")
        self.invoice(number="Atelier-étranger", company_uid=self.other.company_uid)
        params = {
            "from": "2026-11-01",
            "to": "2026-11-30",
            "q": "Atelier",
            "status": "received",
            "page": 2,
        }
        page = self.client.get("/app/reception", params)
        self.assertEqual(page.context["documents"].paginator.count, 34)
        eur, gbp = page.context["reception_totals"]
        self.assertEqual(
            (eur["currency_code"], eur["net"], eur["vat"], eur["gross"], eur["due"]),
            ("EUR", 3100, 620, 3720, 1440),
        )
        self.assertEqual((gbp["currency_code"], gbp["gross"]), ("GBP", Decimal("120")))
        paid = next(c for c in page.context["status_cards"] if c["status"] == "paid")
        self.assertEqual(paid["count"], 1)
        filtered = self.client.get(paid["url"])
        self.assertEqual(filtered.context["documents"].paginator.count, 1)
        self.assertNotIn("page=", paid["url"])
        self.assertContains(
            self.client.get("/app", {"preset": "all"}),
            "Factures fournisseurs par statut",
        )
        empty = self.client.get("/app/reception", params | {"q": "introuvable"})
        self.assertContains(empty, "Aucun montant")
        self.assertNotContains(
            self.client.get("/app/reception", {"from": "invalid"}),
            "Totaux de la sélection",
            status_code=422,
        )

    def test_link_or_create_crm_contact_and_notes_without_changing_issued_invoice(self):
        doc = self.invoice(number="SUIVI", status="paid")
        file = self.attachment(doc, pdf_bytes())
        before = Document.objects.filter(pk=doc.pk).values().get()
        base = f"/app/documents/{doc.document_uid}/suivi/"
        extra = Customer.objects.create(
            company_uid=doc.company_uid, legal_name="Partenaire"
        )
        data = {
            "customer_uid": extra.customer_uid,
            "name": "Marie Exemple",
            "email": "marie@example.test",
        }
        for _ in range(2):
            self.assertRedirects(
                self.client.post(base + "creer-contact", data),
                doc.url + "#contact-crm",
                fetch_redirect_response=False,
            )
        contact = CustomerContact.objects.get(
            company_uid=doc.company_uid, customer_uid=extra.customer_uid
        )
        self.assertEqual(DocumentContact.objects.get(document=doc).contact, contact)
        self.assertContains(
            self.client.get("/app/clients/" + extra.customer_uid), doc.url
        )
        self.client.post(base + "retirer-contact")
        self.assertIsNone(DocumentContact.objects.get(document=doc).contact_id)
        self.assertRedirects(
            self.client.post(
                base + "lier-contact", {"contact_uid": contact.contact_uid}
            ),
            doc.url + "#contact-crm",
            fetch_redirect_response=False,
        )
        key = uid()
        for _ in range(2):
            self.assertEqual(
                self.client.post(
                    base + "note", {"note_uid": key, "body": "Note de suivi"}
                ).status_code,
                302,
            )
        self.assertEqual(DocumentNote.objects.count(), 1)
        self.assertEqual(
            self.client.post(
                base + "note", {"note_uid": key, "body": "Suivi modifié", "edit": "1"}
            ).status_code,
            302,
        )
        self.assertContains(self.client.get(doc.url), "Suivi modifié")
        self.assertEqual(before, Document.objects.filter(pk=doc.pk).values().get())
        self.assertEqual(digest(files.read_verified(file)), file.sha256)
        self.assertEqual(self.client.get(doc.url + "/modifier").status_code, 409)

    def test_contact_isolation_and_read_only_users(self):
        doc = self.invoice(customer_uid=None)
        base = f"/app/documents/{doc.document_uid}/suivi/"
        wrong = CustomerContact.objects.create(
            company_uid=self.other.company_uid, customer_uid=uid(), name="Externe"
        )
        self.assertEqual(
            self.client.post(
                base + "lier-contact", {"contact_uid": wrong.contact_uid}
            ).status_code,
            422,
        )
        for _ in range(2):
            self.assertEqual(
                self.client.post(
                    base + "creer-contact",
                    {"name": "Nouveau tiers", "customer_uid": "new"},
                ).status_code,
                302,
            )
        self.assertEqual(Customer.objects.filter(legal_name="Nouveau tiers").count(), 1)
        self.assertNotContains(self.client.get(doc.url), wrong.contact_uid)
        CompanyMember.objects.filter(company_uid=doc.company_uid).update(role="viewer")
        self.assertEqual(
            self.client.post(
                base + "note", {"body": "Lecture seule", "note_uid": uid()}
            ).status_code,
            403,
        )
        self.assertEqual(self.client.post(base + "retirer-contact").status_code, 403)
        self.assertNotContains(self.client.get(doc.url), "Créer et lier le contact")
