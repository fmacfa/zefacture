from decimal import Decimal
from core.models import InventoryItem, StockMovement
from core.services.documents import save_document
from .test_application import ApplicationCase


class InvoiceStockTests(ApplicationCase):
    def invoice(self, ref, quantity="3", kind="incoming", number="ACH-12", unit="C62"):
        lines = [
            dict(
                description="Rouleau",
                quantity=quantity,
                unit_code=unit,
                item_reference=ref,
                unit_price="10",
                vat_rate="20",
            )
        ]
        return save_document(
            self.company,
            self.user,
            "invoice",
            self.data(number=number, lines=lines),
            direction=kind,
            imported_sale=kind == "outgoing",
        )

    def post_line(self, doc, choice, date="2026-10-25"):
        return self.client.post(
            "/app/inventaire/factures/" + doc.document_uid,
            {"line_" + doc.lines.get().line_uid: choice, "date": date},
        )

    def test_receipt_then_consumption_and_sale_idempotently(self):
        purchase = self.invoice("R-1", "12")
        self.assertContains(
            self.client.get(purchase.url), "Récupérer les achats dans l’inventaire"
        )
        self.assertContains(
            self.client.get("/app/inventaire/factures"), purchase.number
        )
        self.assertEqual(self.post_line(purchase, "new").status_code, 302)
        item = InventoryItem.objects.get(reference="R-1")
        self.assertEqual(item.stock, 12)
        self.assertEqual(
            self.post_line(purchase, "item:" + item.item_uid).status_code, 302
        )
        self.assertEqual(StockMovement.objects.count(), 1)
        item_url = "/app/inventaire/" + item.item_uid
        self.assertEqual(
            self.client.post(
                item_url + "/mouvements",
                {
                    "movement_type": "out",
                    "quantity": "2.5",
                    "occurred_on": "2026-10-25",
                    "reason": "Atelier",
                },
            ).status_code,
            302,
        )
        sale = self.invoice("R-1", "3", kind="outgoing", number="V-811")
        self.assertContains(
            self.client.get("/app/inventaire/factures/" + sale.document_uid),
            f'value="item:{item.item_uid}" selected',
        )
        self.assertEqual(self.post_line(sale, "item:" + item.item_uid).status_code, 302)
        self.assertEqual(item.stock, Decimal("6.5"))
        page = self.client.get(item_url)
        self.assertEqual(
            page.context["totals"],
            {"bought": 12, "used": Decimal("5.5"), "available": Decimal("6.5")},
        )
        self.assertContains(page, purchase.number)
        report = self.client.get(
            "/app/rapports", {"dataset": "inventory", "preset": "all", "format": "csv"}
        )
        self.assertIn("Acheté sur facture pendant la période", report.content.decode())
        self.assertEqual(
            self.post_line(
                self.invoice("R-1", "2", kind="outgoing", number="V-812"),
                "item:" + item.item_uid,
                "2026-09-01",
            ).status_code,
            409,
        )
        self.assertEqual(item.stock, Decimal("6.5"))

    def test_attach_existing_movement_and_reject_foreign_items_and_units(self):
        item = InventoryItem.objects.create(
            company_uid=self.company.company_uid,
            reference="R",
            name="Rouleau",
            stock_tracking=1,
        )
        old = StockMovement.objects.create(
            company_uid=self.company.company_uid,
            item_uid=item.item_uid,
            movement_type="in",
            quantity="3",
            occurred_on="2026-10-25",
            reason="Saisie existante",
        )
        purchase = self.invoice("R")
        self.assertEqual(
            self.post_line(purchase, "move:" + old.movement_uid).status_code, 302
        )
        self.assertEqual(item.stock, 3)
        self.assertEqual(StockMovement.objects.get().reason, "Saisie existante")
        wrong = self.invoice("R", number="ACH-13", unit="KGM")
        self.assertEqual(
            self.post_line(wrong, "item:" + item.item_uid).status_code, 422
        )
        foreign = InventoryItem.objects.create(
            company_uid=self.other.company_uid,
            reference="XX",
            stock_tracking=1,
            unit_code="KGM",
        )
        self.assertEqual(
            self.post_line(wrong, "item:" + foreign.item_uid).status_code, 422
        )
        self.assertEqual(
            self.post_line(wrong, "move:" + old.movement_uid).status_code, 409
        )
        self.assertEqual(StockMovement.objects.count(), 1)
        purchase.status = "refused"
        purchase.save(update_fields=["status"])
        self.assertEqual(self.post_line(purchase, "new").status_code, 404)
