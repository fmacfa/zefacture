from decimal import Decimal
from io import BytesIO

from django.test import override_settings
from lxml import etree
from pypdf import PdfReader

from core.common import BusinessError
from core.models import CompanyBranding, Email, InventoryItem, InvoiceEvent
from core.services import calculations, documents, files, mail, reminders, sale_receipts
from .test_application import ApplicationCase


class CommercialWorkflowTests(ApplicationCase):
    def mixed_lines(self):
        return [
            dict(
                description="Article",
                quantity=2,
                unit_price_including_tax="120",
                vat_rate=20,
                discount_percent=10,
            ),
            dict(
                description="Service",
                quantity=1,
                unit_price_including_tax="110",
                vat_rate=10,
            ),
        ]

    def assert_amounts(self, data, **expected):
        for key, value in expected.items():
            self.assertEqual(Decimal(str(data[key])), Decimal(str(value)), key)

    def embedded(self, doc):
        pdf = PdfReader(BytesIO(files.read_verified(files.generate_document(doc))))
        return etree.fromstring(pdf.attachments["factur-x.xml"][0]), " ".join(
            p.extract_text() for p in pdf.pages
        )

    def test_mixed_prices_allowances_preview_pdf_xml_credit_and_progress(self):
        params = {
            "lines": self.mixed_lines(),
            "global_discount_value": 10,
            "global_discount_type": "percent",
        }
        preview = self.client.post(
            "/app/documents/calculer", params, content_type="application/json"
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assert_amounts(
            preview.json(),
            subtotal=280,
            allowance=28,
            net=252,
            vat="41.40",
            payable="293.40",
        )
        self.assertEqual(
            [Decimal(t["allowance"]) for t in preview.json()["taxes"]], [10, 18]
        )
        quote = self.document("quote", **params)
        documents.change_status(quote, "accept")
        invoice = documents.bill_quote(self.company, self.user, quote)
        xml, text = self.embedded(invoice)
        self.assertIn("Remise globale HT", text)
        self.assertIn("293,40", text)
        totals = xml.xpath(
            "//*[local-name()='SpecifiedTradeSettlementHeaderMonetarySummation']"
        )[0]
        self.assert_amounts(
            {etree.QName(child).localname: child.text for child in totals},
            LineTotalAmount=280,
            AllowanceTotalAmount=28,
            TaxBasisTotalAmount=252,
            TaxTotalAmount="41.4",
            GrandTotalAmount="293.4",
            DuePayableAmount="293.4",
        )
        allowances = xml.xpath(
            "//*[local-name()='ApplicableHeaderTradeSettlement']/*[local-name()='SpecifiedTradeAllowanceCharge']"
        )
        self.assertEqual(
            [
                Decimal(a.xpath("string(./*[local-name()='ActualAmount'])"))
                for a in allowances
            ],
            [10, 18],
        )
        invoice.status = "sent"
        invoice.save(update_fields=["status"])
        credit = self.document(
            "credit_note",
            source_invoice_uid=invoice.document_uid,
            lines=self.mixed_lines(),
            global_discount_type="amount",
            global_discount_value=28,
        )
        root, _ = self.embedded(credit)
        self.assertEqual(root.xpath("string(//*[local-name()='TypeCode'][1])"), "381")
        self.assertEqual(credit.total_including_tax, invoice.total_including_tax)
        quote2 = self.document("quote", **params)
        documents.change_status(quote2, "accept")
        advance = documents.bill_quote(self.company, self.user, quote2, 40)
        balance = documents.bill_quote(self.company, self.user, quote2)
        self.assertEqual(
            advance.total_including_tax + balance.total_including_tax,
            quote2.total_including_tax,
        )
        self.assertEqual(
            advance.total_excluding_tax + balance.total_excluding_tax,
            quote2.total_excluding_tax,
        )
        self.embedded(advance)
        self.embedded(balance)

    def test_cent_rounding_and_invalid_allowances(self):
        data = [
            dict(
                description="Essai",
                quantity=1,
                unit_price_including_tax="9.99",
                vat_rate=20,
            )
        ]
        doc = self.document(lines=data)
        self.assertEqual(doc.rounding_amount, Decimal("-0.01"))
        xml, _ = self.embedded(doc)
        self.assertEqual(
            Decimal(xml.xpath("string(//*[local-name()='RoundingAmount'])")),
            Decimal("-0.01"),
        )
        self.assertEqual(
            Decimal(xml.xpath("string(//*[local-name()='DuePayableAmount'])")),
            Decimal("9.99"),
        )
        for kind, value in [
            ("amount", "8.34"),
            ("amount", "0.001"),
            ("percent", 101),
            ("percent", -1),
            ("other", 2),
        ]:
            with self.subTest(kind=kind, value=value), self.assertRaises(BusinessError):
                calculations.calculate(data, kind, value)
        free = self.document("quote", lines=data, global_discount_value=100)
        documents.change_status(free, "accept")
        self.assertEqual(
            documents.bill_quote(self.company, self.user, free).total_including_tax, 0
        )
        split = calculations.calculate(
            [
                dict(description=c, unit_price="0.05", vat_rate=r)
                for c, r in (("A", 20), ("B", 10), ("C", 5.5))
            ],
            "amount",
            "0.02",
        )
        self.assertEqual(sum(t["allowance"] for t in split["taxes"]), Decimal("0.02"))
        self.assertEqual(sum(t["basis"] for t in split["taxes"]), Decimal("0.13"))

    def test_vat_rates_and_categories_in_structured_invoices(self):
        self.customer.vat_number = "DE123456789"
        self.customer.country_code = "DE"
        self.customer.save()
        for code, rate in [
            ("S", 20),
            ("S", 10),
            ("S", 5.5),
            ("S", 2.1),
            ("S", 7.7),
            ("Z", 0),
            ("E", 0),
            ("AE", 0),
            ("K", 0),
            ("G", 0),
            ("O", 0),
        ]:
            with self.subTest(code=code, rate=rate):
                if code == "O":
                    self.company.vat_number = ""
                    self.company.save()
                    self.customer.vat_number = ""
                    self.customer.save()
                doc = self.document(
                    lines=[
                        dict(
                            description="Produit",
                            unit_price_including_tax=100,
                            vat_category_code=code,
                            vat_rate=rate,
                        )
                    ],
                    global_discount_type="amount",
                    global_discount_value="1.23",
                    delivery_date="2026-10-25",
                    delivery_mode="billing",
                )
                xml, _ = self.embedded(doc)
                self.assertEqual(
                    xml.xpath(
                        "//*[local-name()='ApplicableTradeTax']/*[local-name()='CategoryCode']/text()"
                    ),
                    [code, code],
                )
                self.assertEqual(
                    xml.xpath(
                        "//*[local-name()='CategoryTradeTax']/*[local-name()='CategoryCode']/text()"
                    ),
                    [code],
                )

    def test_quote_line_article_reuses_reference_and_preserves_price_category_and_currency(
        self,
    ):
        payload = {
            "currency_code": "EUR",
            "creation_uid": "1" * 32,
            "name": "Visite",
            "item_type": "service",
            "line": {
                "description": "Visite sur place",
                "item_reference": "VIS",
                "unit_code": "HUR",
                "unit_price_including_tax": "37.50",
                "vat_rate": 0,
                "vat_category_code": "E",
                "discount_percent": 15,
            },
        }

        def post(data):
            return self.client.post(
                "/app/devis/articles", data, content_type="application/json"
            )

        first = post(payload)
        self.assertEqual(first.status_code, 201, first.content)
        for other in [
            payload,
            {
                **payload,
                "creation_uid": "2" * 32,
                "line": {**payload["line"], "item_reference": "vis"},
            },
        ]:
            result = post(other)
            self.assertEqual(result.status_code, 200)
            self.assertFalse(result.json()["created"])
        obj = InventoryItem.objects.get(company_uid=self.company.company_uid)
        self.assertEqual(
            (
                obj.vat_category_code,
                obj.unit_code,
                obj.unit_price_excluding_tax,
                obj.currency_code,
            ),
            ("E", "HUR", Decimal("37.5"), "EUR"),
        )
        self.assertEqual(obj.stock, 0)
        obj.status = "archived"
        obj.save()
        self.assertEqual(post(payload).status_code, 409)

    def late(self, number):
        doc = self.document(
            issue_date="2020-01-01",
            due_date="2020-01-30",
            lines=[dict(description="Conseil", unit_price=1000, vat_rate=20)],
        )
        doc.number, doc.status = number, "sent"
        doc.save()
        return doc

    def test_bulk_reminders_preview_balance_recheck_replay_and_tenant(self):
        first, second = self.late("V-1"), self.late("V-2")
        sale_receipts.declare(first, 400, "2020-02-10", "0" * 32)
        url = "/app/factures/relances"
        listing = self.client.get("/app/factures", {"suivi": "overdue"})
        self.assertContains(listing, "select-reminders")
        pre = self.client.post(
            url, {"facture": [first.document_uid, second.document_uid]}
        )
        self.assertContains(pre, "800,00")
        self.assertContains(pre, "client@example.test")
        self.assertFalse(Email.objects.exists())
        sale_receipts.declare(second, 50, "2020-02-11", "7" * 32)
        data = {"action": "send", "review": pre.context["review"]}
        self.assertContains(self.client.post(url, data), "a changé")
        self.client.post(url, data)
        item = Email.objects.get()
        self.assertEqual(
            (item.document_uid, item.reminder_balance), (first.document_uid, 800)
        )
        self.assertIn("800,00", item.body_text)
        self.assertEqual(
            InvoiceEvent.objects.filter(event_code="local:reminder").count(), 1
        )
        self.assertEqual(self.client.get(url, {"facture": "f" * 32}).status_code, 404)
        first.buyer_snapshot = '{"email":"CLIENT@EXAMPLE.TEST"}'
        first.save(update_fields=["buyer_snapshot"])
        self.assertTrue(reminders.still_current(item))
        item.transport = "smtp"
        sale_receipts.declare(first, 800, "2020-02-12", "8" * 32)
        with override_settings(
            EXTERNAL_SERVICES_DISABLED=False, EMAIL_DELIVERY_DISABLED=False
        ):
            self.assertFalse(mail.deliver(item))
        item.refresh_from_db()
        self.assertEqual(item.status, "cancelled")

    def test_custom_template_keeps_balance_and_same_day_already_sent(self):
        doc = self.late("V-3")
        sale_receipts.declare(doc, 400, "2020-02-10", "9" * 32)
        brand, _ = CompanyBranding.objects.get_or_create(
            company_uid=self.company.company_uid
        )
        brand.email_templates = {
            "invoice_reminder": {
                "subject": "Rappel {document_number}",
                "body": "Facture initiale {total}",
            }
        }
        brand.save()
        item, _ = reminders.preview(doc)
        self.assertIn("800,00", item.body_text)
        queued = reminders.enqueue(self.company.company_uid, doc.document_uid)
        queued.status = "sent"
        queued.save()
        with self.assertRaisesMessage(BusinessError, "aujourd’hui"):
            reminders.prepare(doc)
