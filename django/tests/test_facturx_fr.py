from decimal import Decimal
from io import BytesIO

from lxml import etree
from pypdf import PdfReader

from core.common import BusinessError, json_text
from core.services import files
from core.services.documents import issue_invoice
from tests.test_application import ApplicationCase


class FrenchFacturXTests(ApplicationCase):
    def test_embedded_identification_terms_address_and_decimals(self):
        invoice = self.document(
            operation_category="B1", delivery_date="2026-10-25",
            lines=[{"description": "Article", "quantity": "3", "unit_price_including_tax": "1.00",
                    "vat_rate": "20.0000"}],
        )
        pdf = PdfReader(BytesIO(files.read_verified(files.generate_document(invoice))))
        root = etree.fromstring(pdf.attachments["factur-x.xml"][0])
        ns = root.nsmap

        def values(path):
            return root.xpath(path, namespaces=ns)

        number = values("//rsm:ExchangedDocument/ram:ID/text()")[0]
        self.assertEqual(number, invoice.number)
        self.assertLessEqual(len(number), 35)
        notes = {n.findtext("ram:SubjectCode", namespaces=ns): n.findtext("ram:Content", namespaces=ns)
                 for n in values("//rsm:ExchangedDocument/ram:IncludedNote[ram:SubjectCode]")}
        self.assertEqual(notes, {"PMT": self.company.collection_fee_terms, "PMD": self.company.late_payment_terms,
                                 "AAB": self.company.early_payment_terms})
        address = values("//ram:SellerTradeParty/ram:URIUniversalCommunication/ram:URIID")[0]
        self.assertEqual((address.get("schemeID"), address.text), ("0225", self.company.electronic_address))
        self.assertEqual(values("//ram:ApplicableTradeTax/ram:RateApplicablePercent/text()"), ["20", "20"])
        price = values("//ram:NetPriceProductTradePrice/ram:ChargeAmount/text()")[0]
        basis = values("//ram:NetPriceProductTradePrice/ram:BasisQuantity/text()")[0]
        self.assertLessEqual(len(price.partition(".")[2]), 6)
        self.assertEqual(Decimal(price) / Decimal(basis), Decimal("0.83333333"))
        self.assertEqual(files.xml_unit_price(Decimal("10.00000000")), ("10", "1"))
        for note in notes.values():
            self.assertIn(note, " ".join(pdf.pages[0].extract_text().split()))

    def test_legacy_draft_preserves_identity_and_can_get_a_final_number(self):
        invoice = self.document()
        seller = invoice.seller
        for field in ("electronic_address", "electronic_address_scheme", "early_payment_terms",
                      "late_payment_terms", "collection_fee_terms"):
            seller.pop(field)
        seller["name"] = "Nom conservé"
        invoice.seller_snapshot = json_text(seller)
        invoice.number = "BROUILLON-" + invoice.document_uid
        invoice.save(update_fields=["number", "seller_snapshot"])
        files.generate_document(invoice)
        invoice.refresh_from_db()
        self.assertEqual(invoice.number, invoice.draft_reference)
        self.assertEqual(invoice.seller["name"], "Nom conservé")
        self.assertEqual(invoice.seller["late_payment_terms"], self.company.late_payment_terms)
        self.assertEqual(issue_invoice(invoice).number, "FA-2026-0001")

    def test_b2c_excludes_professional_notes_and_french_rate_is_never_rounded(self):
        invoice = self.document()
        invoice.buyer_snapshot = json_text(invoice.buyer | {"type": "individual"})
        self.assertEqual(invoice.payment_notes, [])
        invoice = self.document(lines=[{"description": "Article", "unit_price": 20, "vat_rate": "5.555"}])
        with self.assertRaisesMessage(BusinessError, "deux décimales"):
            files.generate_document(invoice)
