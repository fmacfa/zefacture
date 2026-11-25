from io import StringIO
from uuid import uuid4

from django.core.management import call_command

from core.common import json_text
from core.models import (
    BankTransaction,
    CompanyMember,
    Customer,
    CustomerContact,
    Document,
    DocumentContact,
    PdpInvoice,
)
from core.services import finance
from core.services.invoice_contacts import assign_automatically
from .test_application import ApplicationCase


TRANSACTIONS = "/app/finances/transactions"
DAY = "2026-05-12"


class BankingTests(ApplicationCase):
    def invoice(self, number, **changes):
        return Document.objects.create(
            **(
                {
                    "company_uid": self.company.company_uid,
                    "direction": "incoming",
                    "type": "invoice",
                    "number": number,
                    "status": "received",
                    "currency_code": "EUR",
                    "total_including_tax": 120,
                    "amount_due": 120,
                }
                | changes
            )
        )

    def test_matching_a_declared_payment_confirms_400_on_1200_once_and_shows_the_remaining_800(self):
        invoice = self.invoice("VENTE-1200", direction="outgoing", status="sent", issue_date="2026-04-03",
                               due_date="2026-05-01", buyer_snapshot='{"email":"client@example.test"}', total_including_tax=1200, amount_due=1200)
        period = {"from": "2026-05-01", "to": "2026-05-31"}
        payment = {"amount": "400,00", "received_on": DAY, "receipt_uid": uuid4().hex}
        for _ in range(2):
            self.assertEqual(self.client.post(invoice.url + "/encaisser", payment).status_code, 302)
        def verify_balances():
            invoice.refresh_from_db()
            self.assertEqual((invoice.amount_due, invoice.status, invoice.receipts.count()), (800, "sent", 1))
            page = self.client.get("/app/factures", {**period, "suivi": "received", "devise": "EUR"})
            self.assertContains(page, invoice.number)
            self.assertEqual([c["amount"] for c in page.context["sales_groups"][0]["cards"]], [800, 800, 400])
        verify_balances()
        tx = finance.create_transaction(
            self.company, {"direction": "incoming", "amount": "400", "transaction_date": DAY, "label": invoice.number}
        )
        response = self.client.post(
            TRANSACTIONS + "/" + tx.transaction_uid + "/rapprocher",
            {"document_uid": invoice.document_uid + "/" + payment["receipt_uid"]},
        )
        self.assertEqual(response.status_code, 302)
        verify_balances()
        review = self.client.get("/app/factures/relances", {"facture": invoice.document_uid})
        self.assertEqual(review.status_code, 200)
        self.assertRegex(review.context["rows"][0]["item"].body_text, r"800[,.]00 EUR")
        receipt = invoice.receipts.get()
        self.assertEqual(receipt.transaction.document_uid, invoice.document_uid)
        self.assertContains(self.client.get(invoice.url), receipt.transaction.transaction_uid + "/traiter")
        self.assertEqual(self.client.get("/app/factures", {**period, "suivi":"overdue"}).context["documents"].paginator.count, 1)

    def test_fully_declared_invoice_and_ambiguous_declarations_can_be_matched(self):
        doc = self.invoice("VENTE-PAI-81", direction="outgoing", status="sent", amount_due=800, total_including_tax=800)
        for _ in range(2):
            self.client.post(doc.url + "/encaisser", {"amount":400, "received_on":DAY, "receipt_uid":uuid4().hex})
        tx = finance.create_transaction(
            self.company, {"direction": "incoming", "amount": "400", "transaction_date": DAY, "label": doc.number}
        )
        treatment = self.client.get(TRANSACTIONS + "/" + tx.transaction_uid + "/traiter")
        receipt = doc.receipts.first()
        choice = doc.document_uid + "/" + receipt.receipt_uid
        self.assertContains(treatment, choice)
        self.assertIsNone(tx.document_uid)
        self.assertEqual(self.client.post(TRANSACTIONS + "/" + tx.transaction_uid + "/rapprocher",
                                         {"document_uid": choice}).status_code, 302)
        doc.refresh_from_db()
        self.assertEqual((doc.amount_due, doc.status, doc.receipts.filter(transaction__isnull=False).count()), (0, "paid", 1))

    def test_receipt_history_is_additive_and_unknown_dates_do_not_enter_cash_totals(self):
        from django.apps import apps
        from django.db import connection
        from core.receipt_history import fill_receipt_history
        from core.services.sales_workspace import workspace
        paid = self.invoice("VENTE-OLD-01", direction="outgoing", status="paid", issue_date=DAY,
                            due_date=DAY, total_including_tax=1200, amount_due=0, paid_at=DAY + " 12:00:00")
        unknown = self.invoice("VENTE-OLD-02", direction="outgoing", status="sent", issue_date=DAY,
                               due_date=DAY, total_including_tax=1200, amount_due=800)
        tx = BankTransaction.objects.create(company_uid=self.company.company_uid, document_uid=paid.document_uid,
                                           direction="incoming", amount=400, transaction_date=DAY, status="booked")
        for _ in range(2):
            fill_receipt_history(apps, connection.alias)
        self.assertEqual(paid.receipts.count(), 2)
        self.assertEqual(paid.receipts.get(transaction=tx).amount, 400)
        self.assertEqual(paid.receipts.get(origin="legacy").amount, 800)
        self.assertIsNone(unknown.receipts.get().received_on)
        visible = Document.objects.filter(pk__in=[paid.pk, unknown.pk])
        _, summary = workspace(visible, {}, DAY, DAY, "EUR")
        self.assertEqual(summary["sales_groups"][0]["cards"][2]["amount"], 1200)


class InvoiceContactsTests(ApplicationCase):
    def test_automatic_contacts_creation_reuse_manual_choice_and_historical_links(self):
        first = self.document()
        second = self.document()
        self.assertTrue(first.crm_link.automatic)
        self.assertEqual(first.crm_link.contact_id, second.crm_link.contact_id)
        self.assertEqual(first.crm_link.contact.name, self.customer.legal_name)
        self.assertEqual(CustomerContact.objects.count(), 1)
        specified = self.document(
            contact_name="Lou Exemple", contact_email="lou@example.test"
        )
        self.assertEqual(specified.crm_link.contact.email, "lou@example.test")
        self.assertContains(self.client.get(specified.url), "automatiquement")
        self.client.post(f"/app/documents/{first.document_uid}/suivi/retirer-contact")
        self.assertFalse(assign_automatically(first))
        self.assertIsNone(DocumentContact.objects.get(document=first).contact_id)
        named = specified.crm_link.contact
        self.client.post(
            f"/app/documents/{second.document_uid}/suivi/lier-contact",
            {"contact_uid": named.contact_uid},
        )
        self.assertFalse(assign_automatically(second))
        self.assertFalse(DocumentContact.objects.get(document=second).automatic)
        doc = Document.objects.create(
            company_uid=self.company.company_uid,
            direction="incoming",
            type="invoice",
            seller_snapshot=json_text(
                {
                    "name": "Atelier de test",
                    "siren": "710000001",
                    "email": "achat@example.test",
                }
            ),
        )
        previous = Document.objects.filter(pk=doc.pk).values().get()
        log = StringIO()
        call_command("link_invoice_contacts", stdout=log)
        self.assertIn("1 facture", log.getvalue())
        self.assertEqual(doc.crm_link.contact.name, "Atelier de test")
        self.assertEqual(previous, Document.objects.filter(pk=doc.pk).values().get())
        log = StringIO()
        call_command("link_invoice_contacts", stdout=log)
        self.assertIn("0 facture", log.getvalue())

    def test_structured_supplier_contact_both_directions_and_ambiguity(self):
        target = self.document()
        peer = Customer.objects.create(
            company_uid=self.other.company_uid, legal_name="Tiers"
        )
        CustomerContact.objects.create(
            company_uid=self.other.company_uid,
            customer_uid=peer.customer_uid,
            name="Lou",
            email="lou@example.test",
        )
        for direction, side in (("incoming", "seller"), ("outgoing", "buyer")):
            doc = Document.objects.create(
                company_uid=self.company.company_uid,
                customer_uid=self.customer.customer_uid,
                direction=direction,
                type="invoice",
                seller_snapshot="{}",
                buyer_snapshot="{}",
            )
            PdpInvoice.objects.create(
                company_uid=self.company.company_uid,
                remote_id=str(doc.pk),
                document_uid=doc.document_uid,
                payload={
                    "en_invoice": {
                        side: {
                            "contact": {
                                "contact_point": "Lou",
                                "email_address": "lou@example.test",
                            }
                        }
                    }
                },
            )
            self.assertTrue(assign_automatically(doc))
            self.assertEqual(doc.crm_link.contact.name, "Lou")
            self.assertEqual(doc.crm_link.contact.company_uid, self.company.company_uid)
        self.assertEqual(
            CustomerContact.objects.filter(
                customer_uid=self.customer.customer_uid, email="lou@example.test"
            ).count(),
            1,
        )
        target.crm_link.delete()
        Document.objects.filter(pk=target.pk).update(buyer_snapshot="{}")
        self.assertFalse(assign_automatically(target))
        self.assertFalse(DocumentContact.objects.filter(document=target).exists())

    def test_edit_prefilled_contact_and_create_another_from_supplier_invoice(self):
        doc = self.invoice_for_supplier("F-2026-10")
        other = self.invoice_for_supplier("F-2026-11")
        original = Document.objects.filter(pk=doc.pk).values().get()
        contact = doc.crm_link.contact
        page = self.client.get(doc.url, {"contact": "modifier"})
        self.assertContains(page, "Enregistrer les coordonnées")
        self.assertContains(page, contact.email)
        destination = f"/app/documents/{doc.document_uid}/suivi/"
        data = {
            "contact_uid": contact.contact_uid,
            "version": page.context["contact_version"],
            "name": "Lou Facturation",
            "email": "lou.facturation@example.test",
            "position": "Comptabilité",
            "phone": "0100000000",
        }
        self.assertEqual(
            self.client.post(
                destination + "modifier-contact", data | {"contact_uid": "unrelated"}
            ).status_code,
            409,
        )
        self.assertRedirects(
            self.client.post(destination + "modifier-contact", data),
            doc.url + "#contact-crm",
            fetch_redirect_response=False,
        )
        self.assertFalse(DocumentContact.objects.get(document=doc).automatic)
        self.assertContains(self.client.get(other.url), "Lou Facturation")
        self.assertEqual(
            self.client.post(
                destination + "modifier-contact", data | {"name": "Page ancienne"}
            ).status_code,
            409,
        )
        self.assertEqual(
            self.invoice_for_supplier("F-2026-12").crm_link.contact_id, contact.pk
        )
        create = self.client.get(doc.url, {"contact": "nouveau"})
        self.assertContains(create, "Créer et lier le contact")
        for _ in range(2):
            self.client.post(
                destination + "creer-contact",
                {
                    "customer_uid": self.customer.customer_uid,
                    "name": "Autre personne",
                    "email": "autre@example.test",
                },
            )
        self.assertEqual(
            DocumentContact.objects.get(document=doc).contact.name, "Autre personne"
        )
        self.assertEqual(
            CustomerContact.objects.filter(
                customer_uid=self.customer.customer_uid
            ).count(),
            2,
        )
        self.assertEqual(original, Document.objects.filter(pk=doc.pk).values().get())
        CompanyMember.objects.filter(company_uid=self.company.company_uid).update(
            role="viewer"
        )
        self.assertNotContains(
            self.client.get(doc.url, {"contact": "modifier"}),
            "Enregistrer les coordonnées",
        )
        self.assertEqual(
            self.client.post(destination + "modifier-contact", data).status_code, 403
        )

    def invoice_for_supplier(self, number):
        from core.services.documents import save_document

        return save_document(
            self.company,
            self.user,
            "invoice",
            self.data(number=number),
            direction="incoming",
        )
