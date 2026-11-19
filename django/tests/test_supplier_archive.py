from io import BytesIO
from zipfile import ZipFile

from django.test import Client
from django.utils import timezone
from pypdf import PdfWriter

from core.common import digest, uid
from core.models import CompanyMember, Document, DocumentFile, Ingestion, PdpInvoice
from core.services import files
from .test_application import ApplicationCase


XML = b'<?xml version="1.0"?><Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"/>'
CII = b'<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"/>'


def make_pdf(xml=None):
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    if xml:
        writer.add_attachment("factur-x.xml", xml)
    writer.write(output)
    return output.getvalue()


class SupplierArchiveTests(ApplicationCase):
    url = "/app/reception/export.zip"
    dates = {"from": "2026-11-01", "to": "2026-11-30"}

    def invoice(self, number, company=None, issue="2026-11-05", **fields):
        return Document.objects.create(
            company_uid=(company or self.company).company_uid,
            number=number,
            issue_date=issue,
            **(
                {"direction": "incoming", "type": "invoice", "status": "received"}
                | fields
            ),
        )

    def file(self, document, kind, raw, mime):
        ext = "pdf" if raw.startswith(b"%PDF-") else "xml"
        return DocumentFile.objects.create(
            document_uid=document.document_uid,
            kind=kind,
            mime_type=mime,
            sha256=digest(raw),
            file_size=len(raw),
            storage_path=files.put(f"test/{uid()}.{ext}", raw),
        )

    def link(self, document, original, readable=None):
        return PdpInvoice.objects.create(
            company_uid=document.company_uid,
            document_uid=document.document_uid,
            direction="in",
            remote_id=str(document.pk),
            remote_company_id="1",
            remote_environment="production",
            invoice_number=document.number,
            original_file_uid=original.file_uid,
            readable_file_uid=readable.file_uid if readable else "",
        )

    def download(self, **params):
        response = self.client.get(self.url, self.dates | params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertIn("attachment;", response["Content-Disposition"])
        with ZipFile(BytesIO(b"".join(response.streaming_content))) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(len(archive.namelist()), len(set(archive.namelist())))
            return {name: archive.read(name) for name in archive.namelist()}

    def test_xml_original_pdf_reading_copy_and_embedded_xml_at_period_boundaries(self):
        pdf, hybrid = make_pdf(), make_pdf(CII)
        first = self.invoice("../Fourn/A", issue=self.dates["from"])
        last = self.invoice("FOURN/A", issue=self.dates["to"], type="credit_note")
        self.link(
            first,
            self.file(first, "pdp_original", XML, "text/xml"),
            self.file(first, "pdp_readable", pdf, "application/pdf"),
        )
        self.link(last, self.file(last, "pdp_original", hybrid, "application/pdf"))
        self.invoice("Hors période", issue="2026-10-31")
        self.invoice("Vente", direction="outgoing")
        self.invoice("Devis", type="quote")
        self.invoice("Autre entreprise", company=self.other)
        expected_pdf = {
            f"PDF/2026-11-01_fourna_{first.pk}.pdf": pdf,
            f"PDF/2026-11-30_fourna_{last.pk}.pdf": hybrid,
        }
        expected_xml = {
            f"XML/2026-11-01_fourna_{first.pk}.xml": XML,
            f"XML/2026-11-30_fourna_{last.pk}.xml": CII,
        }
        self.assertEqual(self.download(format="pdf"), expected_pdf)
        self.assertEqual(self.download(format="xml"), expected_xml)
        self.assertEqual(self.download(format="both"), expected_pdf | expected_xml)
        self.assertEqual(DocumentFile.objects.count(), 3)

    def test_native_source_filters_scope_and_all_pages(self):
        for i in range(31):
            doc = self.invoice(f"Atelier {i}")
            self.file(doc, "source", XML, None)
        self.file(self.invoice("Autre fournisseur"), "source", XML, "application/xml")
        self.file(
            self.invoice("Atelier payé", status="paid"),
            "source",
            XML,
            "application/xml",
        )
        hidden = self.invoice("Atelier note privée")
        self.file(hidden, "source", XML, "application/xml")
        Ingestion.objects.create(
            company_uid=self.company.company_uid,
            document_uid=hidden.document_uid,
            source_kind="expense_note",
            uploaded_by_user_uid=uid(),
        )
        CompanyMember.objects.filter(company_uid=self.company.company_uid).update(
            role="viewer"
        )
        selected = self.download(format="xml", q="Atelier", status="received", page="2")
        self.assertEqual(len(selected), 31)
        self.assertTrue(
            all(name.startswith("XML/2026-11-05_atelier-") for name in selected)
        )
        self.assertFalse(any("privee" in name for name in selected))
        self.assertRedirects(
            Client().get(self.url, self.dates),
            "/connexion",
            fetch_redirect_response=False,
        )
        self.assertEqual(self.client.post(self.url).status_code, 405)

    def test_missing_formats_are_identified_and_additional_attachments_are_excluded(
        self,
    ):
        doc = self.invoice("Photo fournisseur")
        self.file(doc, "source", b"image fictive", "image/png")
        self.file(doc, "attachment", make_pdf(), "application/pdf")
        plain = self.invoice("Papier")
        pdf = make_pdf()
        self.file(plain, "attachment", pdf, "application/pdf")
        archive = self.download()
        report = archive.pop("fichiers-indisponibles.txt").decode()
        self.assertEqual(list(archive.values()), [pdf])
        self.assertIn("photo-fournisseur", report)
        self.assertIn("papier", report)
        self.assertEqual(report.count("PDF indisponible"), 1)
        self.assertEqual(report.count("XML indisponible"), 2)
        self.assertContains(
            self.client.get(self.url, self.dates | {"format": "xml"}),
            "Aucun fichier du format choisi",
            status_code=422,
        )

    def test_receipt_day_uses_local_midnight_and_the_same_filter_as_the_list(self):
        with timezone.override("Europe/Paris"):
            pdf = make_pdf()
            for i, stamp in enumerate(
                (
                    "2026-10-31 22:59:59",
                    "2026-10-31 23:00:00",
                    "2026-11-01 22:59:59",
                    "2026-11-01 23:00:00",
                )
            ):
                doc = self.invoice(f"Reçu {i}", issue="2026-08-05", received_at=stamp)
                self.file(doc, "pdf", pdf, "application/pdf")
            period = {
                "date_field": "received",
                "from": "2026-11-01",
                "to": "2026-11-01",
                "format": "pdf",
            }
            selected = self.download(**period)
            self.assertEqual(len(selected), 2)
            page = self.client.get("/app/reception", period)
            self.assertContains(page, "Reçu 1")
            self.assertContains(page, "Reçu 2")
            self.assertNotContains(page, "Reçu 0")
            self.assertNotContains(page, "Reçu 3")

    def test_rejects_invalid_and_empty_periods(self):
        for params in (
            {},
            {"from": "2026-11-02"},
            {"from": "2026-11-31", "to": "2026-12-01"},
            {"from": "2026-11-30", "to": "2026-11-01"},
            self.dates | {"format": "unknown"},
            self.dates | {"date_field": "unknown"},
            self.dates,
        ):
            with self.subTest(params=params):
                response = self.client.get(self.url, params)
                self.assertContains(response, 'role="alert"', status_code=422)
                self.assertNotIn("Content-Disposition", response)
                self.assertContains(response, "Télécharger le ZIP", status_code=422)

    def test_unreadable_or_cross_document_reference_fails_before_sending_a_zip(self):
        doc = self.invoice("Facture test")
        original = self.file(doc, "pdp_original", XML, "application/xml")
        other = self.invoice("Hors entreprise", company=self.other)
        foreign_pdf = self.file(other, "pdp_readable", make_pdf(), "application/pdf")
        self.link(doc, original, foreign_pdf)
        self.assertEqual(list(self.download(format="xml").values()), [XML])
        self.assertEqual(
            self.client.get(self.url, self.dates | {"format": "pdf"}).status_code, 409
        )
        files.resolve(original.storage_path).write_bytes(XML + b" ")
        response = self.client.get(self.url, self.dates | {"format": "xml"})
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("Content-Disposition", response)
