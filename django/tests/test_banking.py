from json import dumps
from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4

import requests
from django.core.management import call_command
from django.test import Client, override_settings
from django.utils import timezone

from core.common import BusinessError, json_text
from core.models import (
    BankAccount,
    BankFeed,
    BankFeedAccount,
    BankFeedEntry,
    BankTransaction,
    CompanyMember,
    Customer,
    CustomerContact,
    Document,
    DocumentContact,
    InvoiceEvent,
    PdpInvoice,
)
from core.services import bank_sync, finance
from core.services.bridge import Bridge
from core.services.files import decrypt
from core.services.invoice_contacts import assign_automatically
from .test_application import ApplicationCase


ACCOUNTS = "/app/finances/comptes"
CONNECTION = ACCOUNTS + "/connexion/"
TRANSACTIONS = "/app/finances/transactions"
DAY = "2026-05-12"


def account(remote_id, **changes):
    return {
        "id": remote_id,
        "name": f"Compte test {remote_id}",
        "currency_code": "EUR",
        "balance": 800,
        "accounting_balance": 720,
        "data_access": "enabled",
    } | changes


def movement(
    remote_id, account_id=10, amount=-120, label="Virement FOU-0042", **changes
):
    return {
        "id": remote_id,
        "account_id": account_id,
        "amount": amount,
        "provider_description": label,
        "booking_date": DAY,
        "updated_at": "2026-05-13T02:30:01.123Z",
        "currency_code": "EUR",
        "future": False,
        "deleted": False,
        "provider_external_id": "test-" + str(remote_id),
    } | changes


class BridgeFixture:
    """Serve the documented aggregation API at the HTTP boundary using synthetic accounts."""

    def __init__(self):
        self.user = str(uuid4())
        self.external = None
        self.portal = "https://connect.bridgeapi.io/user/" + str(uuid4())
        self.accounts = [account(10), account(20, currency_code="GBP")]
        self.rows = {10: [], 20: []}
        self.session = {}
        self.calls = []
        self.transaction_error = None

    def __call__(self, method, url, *, headers, json=None, params=None, **options):
        request_url = urlsplit(url)
        assert request_url.netloc == "api.bridgeapi.io"
        assert (
            options["allow_redirects"] is False
            and headers["Bridge-Version"] == "2025-01-15"
        )
        endpoint = request_url.path.removeprefix("/v3/aggregation/")
        self.calls.append((method, endpoint, params))
        code = 200
        if endpoint == "users" and method == "POST":
            self.external = json["external_user_id"]
            code, data = 201, {"uuid": self.user, "external_user_id": self.external}
        elif endpoint == "authorization/token" and method == "POST":
            assert json == {"external_user_id": self.external}
            data = {
                "access_token": "synthetic-bearer",
                "user": {"uuid": self.user, "external_user_id": self.external},
            }
        else:
            assert headers.get("Authorization") == "Bearer synthetic-bearer"
            if endpoint == "user-management-sessions" and method == "POST":
                self.session = json
                data = {"url": self.portal}
            elif endpoint == "accounts" and method == "GET":
                data = {"resources": self.accounts, "pagination": {}}
            elif endpoint == "transactions" and method == "GET":
                pk = int(params["account_id"])
                rows = self.rows[pk]
                second = params.get("after") == "second"
                if second and self.transaction_error:
                    code, data = self.transaction_error, {}
                else:
                    next_uri = (
                        "/v3/aggregation/transactions?after=second&limit=1"
                        if rows[1:] and not second
                        else None
                    )
                    data = {
                        "resources": rows[1:] if second else rows[:1],
                        "pagination": {"next_uri": next_uri},
                    }
            else:
                raise AssertionError((method, endpoint))
        response = requests.Response()
        response.status_code = code
        response._content = dumps(data).encode()
        response._content_consumed = True
        return response


@override_settings(EXTERNAL_SERVICES_DISABLED=False)
class BankingTests(ApplicationCase):
    def setUp(self):
        super().setUp()
        self.api = BridgeFixture()
        mock = patch("core.services.bridge.requests.request", side_effect=self.api)
        self.http = mock.start()
        self.addCleanup(mock.stop)

    def configure(self, automatic=True):
        self.assertEqual(
            self.client.post(
                CONNECTION + "configurer",
                {
                    "client_id": "demo-application",
                    "client_secret": "synthetic-credential",
                    "auto_reconcile": "1" if automatic else "",
                },
            ).status_code,
            302,
        )
        return BankFeed.objects.get(company_uid=self.company.company_uid)

    def connect(self, automatic=True):
        feed = self.configure(automatic)
        response = self.client.post(CONNECTION + "autoriser")
        self.assertContains(response, self.api.portal)
        self.assertIn("no-store", response["Cache-Control"])
        feed.refresh_from_db()
        return feed

    def sync(self, feed, *, success=True):
        BankFeed.objects.filter(pk=feed.pk).update(next_sync_at=timezone.now())
        bank_sync.sync_due()
        feed.refresh_from_db()
        if success:
            self.assertEqual(feed.last_error, "")
            self.assertIsNotNone(feed.last_success_at)

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

    def test_consent_settings_permissions_nonce_pause_and_resume(self):
        self.assertContains(self.client.get(ACCOUNTS), "Configurer Bridge")
        feed = self.connect()
        self.assertEqual(decrypt(feed.secret_encrypted), "synthetic-credential")
        self.assertNotEqual(feed.secret_encrypted, "synthetic-credential")
        self.assertNotContains(self.client.get(ACCOUNTS), "synthetic-credential")
        self.assertEqual(self.api.session["user_email"], self.user.email)
        self.assertEqual(self.api.session["account_types"], "payment")
        self.assertEqual(
            urlsplit(self.api.session["callback_url"]).path, CONNECTION + "retour"
        )
        self.assertEqual(feed.remote_user, self.api.user)
        valid = {"context": self.api.session["context"], "user_uuid": self.api.user}
        for data in (
            {},
            valid | {"user_uuid": str(uuid4())},
            valid | {"context": "wrong"},
        ):
            self.assertEqual(
                self.client.get(CONNECTION + "retour", data).status_code, 403
            )
        self.assertRedirects(self.client.get(CONNECTION + "retour", valid), ACCOUNTS)
        self.assertEqual(self.client.get(CONNECTION + "retour", valid).status_code, 403)
        self.assertEqual(
            self.client.post(
                CONNECTION + "configurer",
                {"client_id": "different", "client_secret": "test"},
            ).status_code,
            409,
        )
        self.client.post(CONNECTION + "configurer", {"client_id": feed.client_id})
        feed.refresh_from_db()
        self.assertEqual(decrypt(feed.secret_encrypted), "synthetic-credential")
        self.assertFalse(feed.auto_reconcile)
        self.client.post(CONNECTION + "activation", {"action": "pause"})
        with self.assertNumQueries(1):
            bank_sync.sync_due()
        self.assertEqual(self.client.post(CONNECTION + "synchroniser").status_code, 409)
        self.assertRedirects(
            self.client.post(CONNECTION + "activation", {"action": "resume"}), ACCOUNTS
        )
        self.assertRedirects(self.client.post(CONNECTION + "synchroniser"), ACCOUNTS)
        with override_settings(SENSITIVE_REAUTH_REQUIRED=True):
            response = self.client.post(CONNECTION + "autoriser")
            self.assertIn("next=%2Fapp%2Ffinances%2Fcomptes", response["Location"])
        strict_client = Client(enforce_csrf_checks=True)
        strict_client.force_login(self.user)
        self.assertEqual(strict_client.post(CONNECTION + "autoriser").status_code, 403)
        CompanyMember.objects.filter(company_uid=self.company.company_uid).update(
            role="accountant"
        )
        for endpoint in ("autoriser", "configurer", "activation"):
            self.assertEqual(self.client.post(CONNECTION + endpoint).status_code, 403)
        self.assertNotContains(
            self.client.get(ACCOUNTS), "Modifier les paramètres Bridge"
        )
        CompanyMember.objects.filter(company_uid=self.company.company_uid).update(
            role="viewer"
        )
        self.assertEqual(self.client.get(TRANSACTIONS).status_code, 403)
        self.assertEqual(self.client.post(CONNECTION + "synchroniser").status_code, 403)

    def test_management_portal_returns_to_its_callback_url_verbatim(self):
        self.connect()
        callback = self.api.session["callback_url"]
        other_browser = Client()
        other_browser.force_login(self.user)
        self.assertEqual(other_browser.get(callback).status_code, 403)
        self.assertRedirects(self.client.get(callback), ACCOUNTS)
        self.assertEqual(self.client.get(callback).status_code, 403)

    def test_standalone_statement_entry_survives_bridge_matching_and_correction(self):
        feed = self.connect()
        self.api.rows[10] = [movement(320, label="PAIE-0012")]
        self.sync(feed)
        entry = BankFeedEntry.objects.get(remote_id="320")
        tx = entry.transaction
        finance.classify(self.company, tx, "salary", "Paie du mois")
        invoice = self.invoice("PAIE-0012")
        self.sync(feed)
        tx.refresh_from_db()
        invoice.refresh_from_db()
        self.assertIsNone(tx.document_uid)
        self.assertEqual((tx.standalone_category, invoice.amount_due), ("salary", 120))
        self.api.rows[10] = [movement(320, amount=120, label="PAIE-0012", updated_at="2026-05-15T08:00:00Z")]
        self.sync(feed)
        entry.refresh_from_db()
        self.assertEqual(entry.review, "classification")
        self.assertEqual(self.client.post(TRANSACTIONS + f"/verification/{entry.pk}", {"choice":"correct"}).status_code, 302)
        tx.refresh_from_db()
        self.assertEqual((tx.direction, tx.standalone_category, tx.classification_note), ("incoming", "", "Paie du mois"))

    def test_multiple_accounts_cursor_reference_reconciliation_and_financial_correction(
        self,
    ):
        feed = self.connect()
        invoice = self.invoice("FOU-0042")
        other = self.invoice("FOU-0042", company_uid=self.other.company_uid)
        usd = self.invoice("USD-0002", currency_code="USD")
        self.api.rows[10] = [
            movement(110),
            movement(112, amount=30, label="Sans référence"),
            movement(113, future=True),
        ]
        self.api.rows[20] = [movement(120, 20, label="USD-0002", currency_code="GBP")]
        self.sync(feed)
        self.assertEqual(BankFeedAccount.objects.filter(feed=feed).count(), 2)
        self.assertEqual(BankTransaction.objects.count(), 3)
        invoice.refresh_from_db()
        other.refresh_from_db()
        usd.refresh_from_db()
        self.assertEqual((invoice.status, invoice.amount_due), ("paid", 0))
        self.assertEqual((other.amount_due, usd.amount_due), (120, 120))
        self.assertEqual(
            InvoiceEvent.objects.filter(
                document_uid=invoice.document_uid, event_code="local:bank_payment"
            ).count(),
            1,
        )
        link = BankFeedAccount.objects.get(feed=feed, remote_id="10")
        self.assertEqual(link.account.current_balance, 720)
        self.assertIsNotNone(link.cursor)
        self.sync(feed)
        self.assertTrue(
            any(
                args and "since" in args
                for _, op, args in self.api.calls
                if op == "transactions"
            )
        )
        self.assertEqual(BankTransaction.objects.count(), 3)
        entry = BankFeedEntry.objects.get(feed_account=link, remote_id="110")
        self.api.rows[10] = [
            movement(110, deleted=True, updated_at="2026-05-14T08:00:00Z")
        ]
        self.sync(feed)
        entry.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(
            (entry.review, entry.transaction.status, invoice.status),
            ("correction", "booked", "paid"),
        )
        self.assertContains(self.client.get(TRANSACTIONS), "Annuler le rapprochement")
        with self.assertRaises(BusinessError):
            bank_sync.resolve_entry(self.other, entry.pk, "correct")
        self.assertEqual(
            self.client.post(
                TRANSACTIONS + f"/verification/{entry.pk}", {"choice": "correct"}
            ).status_code,
            302,
        )
        invoice.refresh_from_db()
        entry.refresh_from_db()
        self.assertEqual(
            (invoice.amount_due, invoice.status, entry.transaction.status),
            (120, "received", "cancelled"),
        )
        self.assertIsNone(entry.transaction.document_uid)
        self.assertEqual(
            self.client.post(
                TRANSACTIONS + f"/verification/{entry.pk}", {"choice": "correct"}
            ).status_code,
            404,
        )
        self.api.accounts = [
            account(10, data_access="disabled"),
            account(20, currency_code="GBP"),
        ]
        self.sync(feed)
        self.assertFalse(BankFeedAccount.objects.get(pk=link.pk).shared)
        self.assertEqual(BankTransaction.objects.count(), 3)

    def test_reconsent_keeps_the_same_ledger_and_invoice_payment(self):
        feed = self.connect()
        invoice = self.invoice("FOU-0042")
        self.api.accounts = [account(10, iban="FRTEST")]
        self.api.rows[10] = [movement(1)]
        self.sync(feed)
        original = BankFeedEntry.objects.get().transaction
        self.api.accounts = [account(20, iban="FRTEST")]
        self.api.rows[20] = [movement(2, 20, provider_external_id="test-1")]
        self.sync(feed)
        self.assertEqual(
            (BankAccount.objects.count(), BankTransaction.objects.count()), (1, 1)
        )
        self.assertEqual(
            BankFeedEntry.objects.get(remote_id="2").transaction_id, original.pk
        )
        self.api.rows[20] = [
            movement(
                2,
                20,
                provider_external_id="test-1",
                deleted=True,
                updated_at="2026-05-14T08:00:00Z",
            )
        ]
        self.sync(feed)
        entry = BankFeedEntry.objects.get(remote_id="2")
        self.assertEqual(entry.review, "correction")
        bank_sync.resolve_entry(self.company, entry.pk, "correct")
        invoice.refresh_from_db()
        self.assertEqual((invoice.status, invoice.amount_due), ("received", 120))

    def test_partial_page_retry_csv_overlap_distinct_payments_and_manual_review(self):
        feed = self.connect(False)
        scratch = BankAccount.objects.create(
            company_uid=self.company.company_uid,
            name="Historique",
            iban="FR TEST",
            currency_code="EUR",
        )
        self.api.accounts = [account(10, iban="FRTEST")]
        old = finance.create_transaction(
            self.company,
            {
                "label": "CSV existant",
                "amount": 5,
                "direction": "outgoing",
                "transaction_date": DAY,
                "account_uid": scratch.account_uid,
                "external_id": "test-1",
            },
        )
        self.api.rows[10] = [
            movement(1, amount=-5, label="Opération carte"),
            movement(2),
            movement(3, amount=-5, label="Autre carte"),
        ]
        self.api.transaction_error = 503
        self.sync(feed, success=False)
        self.assertTrue(feed.last_error)
        link = BankFeedAccount.objects.get(feed=feed)
        self.assertIsNone(link.cursor)
        self.assertEqual(link.account_id, scratch.pk)
        self.assertEqual(BankFeedEntry.objects.get().transaction_id, old.pk)
        self.api.transaction_error = None
        self.sync(feed)
        self.assertEqual(
            (BankAccount.objects.count(), BankTransaction.objects.count()), (1, 3)
        )
        bill = self.invoice("FOU-0042")
        self.sync(feed)
        bill.refresh_from_db()
        self.assertEqual(bill.status, "received")
        self.assertContains(
            self.client.get(TRANSACTIONS, {"compte": scratch.account_uid, "q": "FOU"}),
            "Virement FOU-0042",
        )
        csv = f"date;libellé;montant\n{DAY};Virement FOU-0042;-120\n".encode()
        for _ in range(2):
            self.assertEqual(
                finance.import_csv(self.company, scratch.account_uid, csv)["skipped"], 1
            )
        local = finance.create_transaction(
            self.company,
            {
                "account_uid": scratch.account_uid,
                "label": "À affecter",
                "amount": 8,
                "direction": "outgoing",
                "transaction_date": DAY,
            },
        )
        self.api.rows[10] = [movement(4, amount=-8, label="Autre libellé")]
        self.sync(feed)
        review = BankFeedEntry.objects.get(remote_id="4")
        self.assertEqual(review.review, "overlap")
        self.assertContains(
            self.client.get(TRANSACTIONS), "Créer une opération distincte"
        )
        with self.assertRaises(BusinessError):
            bank_sync.resolve_entry(self.company, review.pk, old.transaction_uid)
        bank_sync.resolve_entry(self.company, review.pk, local.transaction_uid)
        review.refresh_from_db()
        self.assertEqual(review.transaction_id, local.pk)
        self.assertEqual(BankAccount.objects.get(pk=scratch.pk).current_balance, 720)

    def test_ambiguous_reference_partial_payment_no_false_positive_or_repeat(self):
        feed = self.connect()
        first = self.invoice("REF-0021")
        second = self.invoice("REF-0022")
        self.api.rows[10] = [
            movement(51, label="VIR REF-00211"),
            movement(52, label="REF-0021 et REF-0022"),
            movement(53, amount=-20, label="REF-0022"),
            movement(54, amount=120, label="REF-0021"),
        ]
        self.sync(feed)
        self.assertFalse(BankTransaction.objects.exclude(document_uid=None).exists())
        self.assertEqual(
            Document.objects.filter(
                pk__in=[first.pk, second.pk], amount_due=120
            ).count(),
            2,
        )
        self.client.post(
            TRANSACTIONS
            + "/"
            + BankFeedEntry.objects.get(remote_id="53").transaction.transaction_uid
            + "/rapprocher",
            {"document_uid": second.document_uid},
        )
        second.refresh_from_db()
        self.assertEqual(second.amount_due, 100)
        self.api.rows[10] = [movement(55, amount=-40, label="Dépôt INV-0023")]
        self.sync(feed)
        third = self.invoice("INV-0023", amount_due=40, total_including_tax=40)
        self.sync(feed)
        third.refresh_from_db()
        self.assertEqual(third.status, "paid")

    def test_out_of_scope_provider_response_and_inflight_request(self):
        feed = self.connect()
        client = Bridge(feed)
        with patch.object(
            client,
            "call",
            return_value={
                "resources": [],
                "pagination": {"next_uri": "https://unrelated.example/next"},
            },
        ) as call:
            with self.assertRaises(BusinessError):
                list(client.pages("accounts"))
            self.assertEqual(call.call_count, 1)
        self.api.rows[10] = [movement(1, account_id=20)]
        self.sync(feed, success=False)
        self.assertTrue(feed.last_error)
        self.assertFalse(BankFeedEntry.objects.exists())
        self.api.rows[10] = []
        original = bank_sync.update_accounts

        def refresh_while_running(feed_id, token, rows):
            links = original(feed_id, token, rows)
            self.client.post(CONNECTION + "synchroniser")
            return links

        with patch.object(
            bank_sync, "update_accounts", side_effect=refresh_while_running
        ):
            self.sync(feed)
        self.assertLessEqual(feed.next_sync_at, timezone.now())
        BankFeed.objects.filter(pk=feed.pk).update(
            lease="existing", lease_until=timezone.now() + timedelta(minutes=4)
        )
        with patch("core.services.bank_sync.Bridge") as skipped:
            bank_sync.sync_due()
            skipped.assert_not_called()


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
