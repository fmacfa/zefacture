from decimal import Decimal

from core.common import BusinessError, uid
from core.models import BankTransaction, Document, SaleReceipt
from core.services import bank_allocations, finance, sale_receipts
from .test_application import ApplicationCase

LIST = "/app/finances/transactions"


class BankAllocationTests(ApplicationCase):
    def invoice(self, number, amount, direction="outgoing", **changes):
        return Document.objects.create(
            **{
                "company_uid": self.company.company_uid,
                "number": number,
                "type": "invoice",
                "customer_uid": self.customer.customer_uid,
                "currency_code": "EUR",
                "direction": direction,
                "status": "sent" if direction == "outgoing" else "received",
                "amount_due": amount,
                "total_including_tax": amount,
            }
            | changes
        )

    def movement(self, amount):
        return BankTransaction.objects.create(
            company_uid=self.company.company_uid,
            transaction_date="2024-05-06",
            label="Versement de démonstration",
            direction="incoming" if amount > 0 else "outgoing",
            amount=abs(amount),
        )

    def split(self, tx, *pairs):
        return self.client.post(
            f"{LIST}/{tx.transaction_uid}/traiter",
            {
                "action": "split",
                "allocation_target": [key for key, value in pairs],
                "allocation_amount": [value for key, value in pairs],
                "allocation_note": [""] * len(pairs),
            },
        )

    def test_net_deposit_multi_invoice_and_fee_through_ui_reports_and_undo(self):
        a, b = self.invoice("VENTE-A", 75), self.invoice("VENTE-B", 45)
        tx = self.movement(Decimal("116.40"))
        url = f"{LIST}/{tx.transaction_uid}/traiter"
        self.assertContains(self.client.get(url), a.document_uid + "/new")
        pairs = [
            (a.document_uid + "/new", "75,00"),
            (b.document_uid + "/new", "+45"),
            ("fee", "-3.60"),
        ]
        result = self.split(tx, *pairs)
        self.assertRedirects(result, url)
        self.assertContains(self.client.get(url), "Commission ou frais")
        self.assertEqual(self.split(tx, *pairs).status_code, 409)
        for invoice in (a, b):
            invoice.refresh_from_db()
            self.assertEqual((invoice.status, invoice.amount_due), ("paid", 0))
        self.assertEqual(
            SaleReceipt.objects.filter(transaction=tx, cancelled=False).count(), 2
        )
        with self.assertRaises(BusinessError):
            finance.classify(self.company, tx, "other_income", "")
        with self.assertRaises(BusinessError):
            finance.reconcile(self.company, tx, a.document_uid)
        listing = self.client.get(LIST, {"traitement": "split"})
        self.assertEqual(
            listing.context["bank_counts"],
            {
                "all": 1,
                "unlinked": 0,
                "invoice": 1,
                "standalone": 0,
                "pending": 0,
                "split": 1,
            },
        )
        self.assertEqual(listing.context["bank_totals"][0][1]["net"], Decimal("116.40"))
        report = self.client.get(
            "/app/rapports",
            {"dataset": "allocations", "format": "csv", "preset": "all"},
        ).content.decode()
        self.assertIn("VENTE-A", report)
        self.assertIn("Commission ou frais", report)
        self.assertEqual(len(report.splitlines()), 4)
        version = tx.allocations.first().pk
        self.assertEqual(
            self.client.post(url, {"action": "undo_split", "version": "0"}).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(
                url, {"action": "undo_split", "version": version}
            ).status_code,
            302,
        )
        self.assertFalse(tx.allocations.exists())
        self.assertFalse(SaleReceipt.objects.filter(cancelled=False).exists())
        for invoice, original in ((a, 75), (b, 45)):
            invoice.refresh_from_db()
            self.assertEqual((invoice.status, invoice.amount_due), ("sent", original))
        self.assertEqual(BankTransaction.objects.get().amount, Decimal("116.40"))

    def test_supplier_credit_and_partial_rent(self):
        inv = self.invoice("ACHAT-A", 60, "incoming")
        credit = self.invoice(
            "AVOIR-A", 80, "incoming", type="credit_note", amount_due=0
        )
        tx = self.movement(20)
        self.assertEqual(
            self.split(
                tx, (inv.document_uid, "-60"), (credit.document_uid, "80")
            ).status_code,
            302,
        )
        inv.refresh_from_db()
        self.assertEqual((inv.status, inv.amount_due), ("paid", 0))
        self.assertEqual(
            self.split(self.movement(1), (credit.document_uid, "1")).status_code, 409
        )
        rent = self.movement(-80)
        self.assertEqual(
            self.split(rent, ("rent", "-70"), ("carry", "-10")).status_code, 302
        )
        listing = self.client.get(LIST, {"traitement": "standalone"})
        self.assertEqual([r.pk for r in listing.context["transactions"]], [rent.pk])
        self.assertEqual(listing.context["bank_totals"][0][1]["net"], -80)

    def test_rejects_unbalanced_foreign_currency_wrong_sign_and_bad_precision_atomically(
        self,
    ):
        tx = self.movement(30)
        a = self.invoice("VENTE-A", 20)
        other = self.invoice("VENTE-B", 30, company_uid=self.other.company_uid)
        usd = self.invoice("VENTE-C", 10, currency_code="USD")
        supplier = self.invoice("ACHAT-A", 10, "incoming")
        for pairs in (
            [(a.document_uid, "20")],
            [(other.document_uid, "30")],
            [(a.document_uid, "20"), (usd.document_uid, "10")],
            [(a.document_uid, "20"), (supplier.document_uid, "10")],
            [(a.document_uid, "15"), (a.document_uid, "15")],
            [(a.document_uid, "30")],
            [("other", "30.001")],
        ):
            with self.subTest(pairs=pairs):
                self.assertGreaterEqual(self.split(tx, *pairs).status_code, 400)
                self.assertFalse(tx.allocations.exists())
                self.assertFalse(SaleReceipt.objects.exists())
                a.refresh_from_db()
                self.assertEqual(a.amount_due, 20)
        self.assertNotContains(
            self.client.get(f"{LIST}/{tx.transaction_uid}/traiter"), other.document_uid
        )

    def test_confirmed_manual_receipt_survives_reset(self):
        doc = self.invoice("VENTE-M", 90)
        receipt = sale_receipts.declare(doc, "90", "2024-05-04", uid())
        tx = self.movement(88)
        self.assertEqual(
            self.split(
                tx, (doc.document_uid + "/" + receipt.receipt_uid, "90"), ("fee", "-2")
            ).status_code,
            302,
        )
        doc.refresh_from_db()
        self.assertEqual(
            (doc.amount_due, doc.status, doc.receipts.count()), (0, "paid", 1)
        )
        bank_allocations.reset(self.company, tx, tx.allocations.first().pk)
        receipt.refresh_from_db()
        doc.refresh_from_db()
        self.assertEqual(
            (receipt.cancelled, receipt.transaction_id, str(receipt.received_on)),
            (False, None, "2024-05-04"),
        )
        self.assertEqual((doc.amount_due, doc.status), (0, "paid"))
