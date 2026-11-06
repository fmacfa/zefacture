import hashlib
import io
import json
import os
import tempfile
from pathlib import Path
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, Client, override_settings
from django.template.loader import get_template
from django.conf import settings
from django.contrib.auth import authenticate
from core.common import BusinessError, uid, digest, stamp, json_text
from core.models import *
from core.services.calculations import calculate
from core.services.documents import (
    save_document,
    save_customer,
    bill_quote,
    change_status,
)
from core.services.files import (
    generate_document,
    read_verified,
    put,
    encrypt,
    decrypt,
    validate_xml,
)
from core.services.finance import create_transaction, reconcile, import_csv, csv_text
from core.views.documents import send


class ApplicationCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            "owner@example.test", "OwnerPassword123!", name="Propriétaire"
        )
        cls.company = Company.objects.create(
            owner_user_uid=cls.user.user_uid,
            legal_name="Entreprise Démonstration SAS",
            siren="123456789",
            vat_number="FR32123456789",
            address_line1="1 rue de la Démonstration",
            postal_code="75001",
            city="Paris",
            country_code="FR",
            billing_email=cls.user.email,
            payment_terms="Paiement à 30 jours.",
        )
        CompanyMember.objects.create(
            company_uid=cls.company.company_uid,
            user_uid=cls.user.user_uid,
            role="owner",
        )
        cls.customer = save_customer(
            cls.company,
            dict(
                legal_name="Client Démonstration",
                type="business",
                email="client@example.test",
                address_line1="2 rue des Exemples",
                postal_code="69001",
                city="Lyon",
                country_code="FR",
            ),
        )
        cls.other = Company.objects.create(
            owner_user_uid=cls.user.user_uid, legal_name="Autre entreprise"
        )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.override = override_settings(
            DATA_DIR=Path(self.temp.name),
            STORAGES={
                "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                "staticfiles": {
                    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
                },
            },
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.temp.cleanup)
        self.client.force_login(self.user)

    def data(self, **changes):
        return (
            dict(
                customer_uid=self.customer.customer_uid,
                issue_date="2026-10-25",
                due_date="2026-11-24",
                currency_code="EUR",
                lines=[
                    dict(
                        description="Prestation fictive",
                        quantity="2",
                        unit_price_excluding_tax="50",
                        vat_rate="20",
                    )
                ],
            )
            | changes
        )

    def document(self, kind="invoice", **changes):
        return save_document(self.company, self.user, kind, self.data(**changes))


class CalculationTests(ApplicationCase):
    def test_rounding_at_rate_not_line(self):
        result = calculate(
            [
                dict(
                    description=str(i),
                    qty=1,
                    unit_price_excluding_tax=".03",
                    vat_rate=20,
                )
                for i in range(300)
            ]
        )
        self.assertEqual(result["vat"], Decimal("1.80"))
        self.assertEqual(
            sum(line["total_vat"] for line in result["lines"]), Decimal("1.80")
        )
        self.assertEqual(result["gross"], Decimal("10.80"))

    def test_discounts_fractional_price(self):
        result = calculate(
            [
                dict(
                    description="A",
                    quantity="3",
                    unit_price_excluding_tax=".335",
                    vat_rate="5.5",
                    discount_percent="12.5",
                )
            ]
        )
        self.assertEqual(result["net"], Decimal(".88"))
        self.assertEqual(result["vat"], Decimal(".05"))

    def test_invalid_numbers_and_categories(self):
        for field, value in [
            ("quantity", "0"),
            ("unit_price_excluding_tax", "-1"),
            ("vat_rate", "101"),
            ("discount_percent", "101"),
            ("quantity", "NaN"),
            ("quantity", "Infinity"),
        ]:
            with (
                self.subTest(field=field, value=value),
                self.assertRaises(BusinessError),
            ):
                calculate(
                    [
                        dict(
                            description="X",
                            quantity=1,
                            unit_price_excluding_tax=1,
                            vat_rate=20,
                        )
                        | {field: value}
                    ]
                )
        with self.assertRaises(BusinessError):
            calculate([dict(description="X", vat_rate=20, vat_category_code="E")])

    def test_numbering_rollback(self):
        before = self.company.next_invoice_number
        with self.assertRaises(BusinessError):
            self.document(lines=[])
        self.company.refresh_from_db()
        self.assertEqual(self.company.next_invoice_number, before)
        one, two = self.document(), self.document()
        self.assertNotEqual(one.number, two.number)


class DocumentTests(ApplicationCase):
    def test_client_is_scoped(self):
        customer = Customer.objects.create(
            company_uid=self.other.company_uid, legal_name="Privé"
        )
        with self.assertRaises(BusinessError):
            self.document(customer_uid=customer.customer_uid)

    def test_quote_without_client(self):
        quote = self.document("quote", customer_uid="")
        with self.assertRaises(BusinessError):
            change_status(quote, "accept")
        self.assertIsNone(quote.customer_uid)

    def test_quote_progress_and_remainder(self):
        quote = self.document(
            "quote",
            lines=[
                dict(
                    description="Petite prestation",
                    quantity=1,
                    unit_price_excluding_tax=".03",
                    vat_rate=20,
                )
            ]
            * 3,
        )
        quote.status = "accepted"
        quote.accepted_at = stamp()
        quote.save()
        one = bill_quote(self.company, self.user, quote, "33.3333")
        two = bill_quote(self.company, self.user, quote, "33.3333")
        last = bill_quote(self.company, self.user, quote)
        self.assertEqual(
            sum(d.total_including_tax for d in (one, two, last)),
            quote.total_including_tax,
        )
        self.assertEqual(bill_quote(self.company, self.user, quote).pk, last.pk)
        with self.assertRaises(BusinessError):
            bill_quote(self.company, self.user, quote, 1)
        with self.assertRaises(BusinessError):
            change_status(quote, "cancel")

    def test_overbilling_rejected(self):
        quote = self.document("quote")
        quote.status = "accepted"
        quote.save()
        bill_quote(self.company, self.user, quote, 70)
        with self.assertRaises(BusinessError):
            bill_quote(self.company, self.user, quote, 31)

    def test_sent_immutable(self):
        doc = self.document()
        doc.status = "sent"
        doc.save()
        with self.assertRaises(BusinessError):
            save_document(self.company, self.user, "invoice", self.data(), doc)
        with self.assertRaises(BusinessError):
            change_status(doc, "cancel")
        with self.assertRaises(BusinessError):
            generate_document(doc)

    def test_credit_note_customer(self):
        original = self.document()
        original.status = "sent"
        original.save()
        credit = self.document("credit_note", source_invoice_uid=original.document_uid)
        self.assertEqual(credit.amount_due, 0)
        self.assertEqual(credit.source_invoice_uid, original.document_uid)

    def test_sent_file_integrity(self):
        doc = self.document()
        raw = b"immutable original"
        file = DocumentFile.objects.create(
            document_uid=doc.document_uid,
            kind="facturx",
            storage_path=put("test/original.pdf", raw),
            sha256=digest(raw),
        )
        doc.status = "sent"
        doc.save()
        self.assertEqual(generate_document(doc).pk, file.pk)
        Path(settings.DATA_DIR / "test/original.pdf").write_bytes(b"altered")
        with self.assertRaises(BusinessError):
            generate_document(doc)

    def test_public_quote_expiry_and_acceptance(self):
        quote = self.document("quote")
        token = "a" * 64
        quote.status = "sent"
        quote.public_token_hash = digest(token)
        quote.public_token_expires_at = "2099-01-01 00:00:00"
        quote.save()
        DocumentFile.objects.create(
            document_uid=quote.document_uid,
            kind="pdf",
            storage_path=put("test/quote.pdf", b"%PDF-example"),
            sha256=digest(b"%PDF-example"),
        )
        public = Client()
        self.assertEqual(public.get("/devis/" + token).status_code, 200)
        self.assertEqual(
            public.post("/devis/" + token, {"accept": "1"}).status_code, 200
        )
        quote.refresh_from_db()
        self.assertEqual(quote.status, "accepted")
        quote.public_token_expires_at = "2000-01-01 00:00:00"
        quote.save()
        self.assertEqual(public.get("/devis/" + token).status_code, 404)


class FinanceTests(ApplicationCase):
    def payable(self):
        doc = self.document()
        doc.status = "sent"
        doc.save()
        return doc

    def test_partial_full_payment_and_duplicate(self):
        doc = self.payable()
        account = BankAccount.objects.create(
            company_uid=self.company.company_uid,
            name="Banque",
            opening_balance=50,
            current_balance=50,
        )
        t = create_transaction(
            self.company,
            dict(
                account_uid=account.account_uid,
                label="Premier paiement",
                amount=30,
                direction="incoming",
            ),
        )
        reconcile(self.company, t, doc.document_uid)
        doc.refresh_from_db()
        self.assertEqual(doc.amount_due, 90)
        with self.assertRaises(BusinessError):
            reconcile(self.company, t, doc.document_uid)
        create_transaction(
            self.company,
            dict(
                account_uid=account.account_uid,
                label="Solde",
                amount=90,
                direction="incoming",
                document_uid=doc.document_uid,
            ),
        )
        doc.refresh_from_db()
        account.refresh_from_db()
        self.assertEqual(doc.status, "paid")
        self.assertEqual(account.current_balance, 170)

    def test_overpayment_rolls_back_account_and_transaction(self):
        doc = self.payable()
        account = BankAccount.objects.create(
            company_uid=self.company.company_uid, name="Banque"
        )
        with self.assertRaises(BusinessError):
            create_transaction(
                self.company,
                dict(
                    account_uid=account.account_uid,
                    label="Trop",
                    amount=121,
                    direction="incoming",
                    document_uid=doc.document_uid,
                ),
            )
        self.assertFalse(BankTransaction.objects.exists())
        account.refresh_from_db()
        self.assertEqual(account.current_balance, 0)

    def test_cross_company_and_currency(self):
        doc = self.payable()
        account = BankAccount.objects.create(
            company_uid=self.other.company_uid, name="Interdit"
        )
        with self.assertRaises(BusinessError):
            create_transaction(
                self.company,
                dict(
                    account_uid=account.account_uid,
                    label="X",
                    amount=1,
                    direction="incoming",
                ),
            )
        account = BankAccount.objects.create(
            company_uid=self.company.company_uid, name="USD", currency_code="USD"
        )
        t = create_transaction(
            self.company,
            dict(
                account_uid=account.account_uid,
                label="X",
                amount=1,
                direction="incoming",
            ),
        )
        with self.assertRaises(BusinessError):
            reconcile(self.company, t, doc.document_uid)

    def test_csv_dedup_preserves_repeated_identical_entries(self):
        account = BankAccount.objects.create(
            company_uid=self.company.company_uid, name="Banque"
        )
        raw = "date;libellé;montant\n25/10/2026;Vente;12,50\n25/10/2026;Vente;12,50\n25/10/2026;Achat;-4,00\n".encode()
        self.assertEqual(
            import_csv(self.company, account.account_uid, raw),
            dict(imported=3, skipped=0, rejected=0),
        )
        self.assertEqual(
            import_csv(self.company, account.account_uid, raw),
            dict(imported=0, skipped=3, rejected=0),
        )
        account.refresh_from_db()
        self.assertEqual(account.current_balance, 21)

    def test_csv_formula_escaping(self):
        result = csv_text(["label", "amount"], [["=HYPERLINK(1)", Decimal("-4.00")]])
        self.assertIn("'=HYPERLINK", result)
        self.assertIn("-4.00", result)


class AccessTests(ApplicationCase):
    def test_all_pages_render(self):
        routes = [
            "/",
            "/source",
            "/app",
            "/app/compte",
            "/app/entreprises",
            "/app/entreprise",
            "/app/entreprise/pdp",
            "/app/collaborateurs",
            "/app/emails",
            "/app/developpeurs",
            "/app/clients",
            "/app/clients/nouveau",
            "/app/inventaire",
            "/app/inventaire/nouveau",
            "/app/imports",
            "/app/notes-de-frais",
            "/app/finances",
            "/app/finances/comptes",
            "/app/finances/transactions",
            "/app/finances/tresorerie",
            "/app/finances/tva",
            "/app/devis",
            "/app/devis/nouveau",
            "/app/factures",
            "/app/factures/nouvelle",
            "/app/avoirs/nouveau",
            "/app/reception/nouvelle",
        ]
        for route in routes:
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 200)
        doc = self.document()
        self.assertEqual(self.client.get(doc.url).status_code, 200)
        self.assertEqual(self.client.get(doc.url + "/modifier").status_code, 200)

    def test_document_isolation(self):
        doc = self.document()
        doc.company_uid = self.other.company_uid
        doc.save()
        for suffix in ["", "/modifier", "/telecharger"]:
            self.assertEqual(self.client.get(doc.url + suffix).status_code, 404)

    def test_roles_and_expense_visibility(self):
        member = CompanyMember.objects.get(
            company_uid=self.company.company_uid, user_uid=self.user.user_uid
        )
        member.role = "viewer"
        member.save()
        self.assertEqual(self.client.get("/app/finances").status_code, 403)
        self.assertEqual(self.client.post("/app/clients", {}).status_code, 403)
        self.assertEqual(self.client.get("/app/devis/nouveau").status_code, 403)
        member.role = "member"
        member.save()
        item = Ingestion.objects.create(
            company_uid=self.company.company_uid,
            uploaded_by_user_uid=uid(),
            source_kind="expense_note",
            original_filename="private",
            storage_path="storage/private.pdf",
        )
        self.assertEqual(
            self.client.get("/app/imports/" + item.ingestion_uid).status_code, 404
        )
        self.assertEqual(self.client.get("/app/entreprise/pdp").status_code, 403)

    def test_csrf_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post("/app/clients", {}).status_code, 403)
        self.assertEqual(client.get("/deconnexion").status_code, 405)

    def test_converted_expense_document_remains_private(self):
        document = self.document(subject="Justificatif confidentiel")
        document.direction = "incoming"
        document.status = "received"
        document.save()
        original = b"original private receipt"
        file = DocumentFile.objects.create(
            document_uid=document.document_uid,
            kind="source",
            storage_path=put("private/receipt.png", original),
            mime_type="image/png",
            sha256=hashlib.sha256(original).hexdigest(),
        )
        item = Ingestion.objects.create(
            company_uid=self.company.company_uid,
            uploaded_by_user_uid=uid(),
            document_uid=document.document_uid,
            source_kind="expense_note",
            original_filename="receipt.png",
            storage_path=file.storage_path,
        )
        member = CompanyMember.objects.get(
            company_uid=self.company.company_uid, user_uid=self.user.user_uid
        )
        attachment = f"/app/documents/{document.document_uid}/fichiers/{file.file_uid}"
        for role in ("member", "viewer"):
            member.role = role
            member.save()
            for route in (document.url, document.url + "/telecharger", attachment):
                self.assertEqual(self.client.get(route).status_code, 404)
            for route in ("/app", "/app/reception"):
                response = self.client.get(route)
                self.assertNotIn(document, response.context["documents"])
            self.assertEqual(self.client.get("/app/entreprise").status_code, 200)
        self.assertEqual(self.client.get("/app/imports").status_code, 403)
        self.assertEqual(self.client.get("/app/inventaire").status_code, 403)
        member.role = "accountant"
        member.save()
        self.assertEqual(self.client.get(document.url).status_code, 200)
        response = self.client.get(attachment)
        self.assertEqual(response.content, original)
        self.assertTrue(response["Content-Disposition"].endswith('.png"'))
        member.role = "member"
        member.save()
        item.uploaded_by_user_uid = self.user.user_uid
        item.save()
        self.assertEqual(self.client.get(document.url).status_code, 200)

    def test_first_secret_key_is_consistent_between_workers(self):
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(encrypt, ["shared secret"] * 16))
        self.assertEqual([decrypt(value) for value in values], ["shared secret"] * 16)
        self.assertEqual((settings.DATA_DIR / "app.key").stat().st_mode & 0o777, 0o600)

    def test_php_bcrypt_upgrade(self):
        import bcrypt

        user = User.objects.create(
            email="legacy@example.test",
            name="Legacy",
            password=bcrypt.hashpw(b"Password123!", bcrypt.gensalt())
            .decode()
            .replace("$2b$", "$2y$"),
        )
        self.assertIsNotNone(authenticate(username=user.email, password="Password123!"))
        user.refresh_from_db()
        self.assertFalse(user.password.startswith("$"))
        self.assertIsNone(authenticate(username=user.email, password="incorrect"))

    def test_sodium_secretbox_compatibility(self):
        from nacl.secret import SecretBox
        import base64

        key = b"x" * 32
        (settings.DATA_DIR / "app.key").write_bytes(base64.b64encode(key))
        php = base64.b64encode(
            SecretBox(key).encrypt(b"legacy-token", b"n" * 24)
        ).decode()
        self.assertEqual(decrypt(php), "legacy-token")
        self.assertEqual(
            SecretBox(key).decrypt(base64.b64decode(encrypt("new-token"))), b"new-token"
        )

    def test_api_scoping_and_create(self):
        raw = "zf_live_" + ("a" * 48)
        ApiKey.objects.create(
            company_uid=self.company.company_uid,
            name="test",
            key_hash=digest(raw),
            prefix=raw[:16],
        )
        response = Client().post(
            "/api/v1/invoices",
            json.dumps(self.data()),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + raw,
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["data"]["total_including_tax"], 120)
        self.assertEqual(Client().get("/api/v1/invoices").status_code, 401)
        key = ApiKey.objects.get()
        key.status = "revoked"
        key.save()
        self.assertEqual(
            Client()
            .get("/api/v1/invoices", HTTP_AUTHORIZATION="Bearer " + raw)
            .status_code,
            401,
        )


class IntegrationTests(ApplicationCase):
    @override_settings(ENVIRONMENT="testing")
    def test_pdp_allowlist(self):
        from core.services.pdp import base

        for url in [
            "http://api.superpdp.tech",
            "https://evil.test",
            "https://api.superpdp.tech@127.0.0.1",
            "https://api.superpdp.tech/x",
        ]:
            with self.assertRaises(BusinessError):
                base(url)
        self.assertEqual(base("https://api.superpdp.tech"), "https://api.superpdp.tech")

    @patch.dict(os.environ, {"SUPERPDP_WEBHOOK_SECRET": "test-secret"})
    def test_webhook_auth_scope_and_idempotence(self):
        doc = self.document()
        doc.status = "sent"
        doc.pdp_invoice_id = "provider-123"
        doc.pdp_external_id = doc.document_uid
        doc.save()
        data = dict(
            invoice_id="provider-123",
            external_id=doc.document_uid,
            event_id="e1",
            status="delivered",
        )
        path = "/webhooks/superpdp"
        self.assertEqual(
            self.client.post(
                path, json.dumps(data), content_type="application/json"
            ).status_code,
            401,
        )
        response = self.client.post(
            path,
            json.dumps(data),
            content_type="application/json",
            HTTP_X_ZEFACTURE_WEBHOOK_SECRET="test-secret",
        )
        self.assertTrue(response.json()["stored"])
        response = self.client.post(
            path,
            json.dumps(data),
            content_type="application/json",
            HTTP_X_ZEFACTURE_WEBHOOK_SECRET="test-secret",
        )
        self.assertFalse(response.json()["stored"])
        self.assertEqual(WebhookReceipt.objects.count(), 1)
        data["external_id"] = uid()
        self.assertEqual(
            self.client.post(
                path,
                json.dumps(data),
                content_type="application/json",
                HTTP_X_ZEFACTURE_WEBHOOK_SECRET="test-secret",
            ).status_code,
            409,
        )

    def test_manual_ingestion_conversion_idempotent(self):
        from core.services.ingestion import convert

        raw = b"%PDF-example"
        item = Ingestion.objects.create(
            company_uid=self.company.company_uid,
            uploaded_by_user_uid=self.user.user_uid,
            original_filename="invoice.pdf",
            storage_path=put("imports/test.pdf", raw),
            sha256=digest(raw),
            mime_type="application/pdf",
        )
        data = {
            "supplier": {"legal_name": "Fournisseur fictif", "country_code": "FR"},
            "invoice": {
                "number": "SUP-1",
                "issue_date": "2026-10-25",
                "due_date": "2026-11-25",
            },
            "lines": self.data()["lines"],
        }
        document = convert(self.company, self.user, item, data)
        self.assertEqual(document.direction, "incoming")
        self.assertEqual(convert(self.company, self.user, item, data).pk, document.pk)
        self.assertEqual(read_verified(generate_document(document)), raw)


class PdfTests(ApplicationCase):
    def test_facturx_real_generation_offline(self):
        from pypdf import PdfReader

        doc = self.document()
        with patch(
            "socket.create_connection", side_effect=AssertionError("Network forbidden")
        ):
            file = generate_document(doc)
        raw = read_verified(file)
        reader = PdfReader(io.BytesIO(raw))
        self.assertIn("factur-x.xml", reader.attachments)
        self.assertTrue(validate_xml(reader.attachments["factur-x.xml"][0])["valid"])
        self.assertIn(doc.number, reader.pages[0].extract_text())

    def test_zero_vat_credit_and_discount(self):
        original = self.document()
        original.status = "sent"
        original.save()
        for kind, changes in [
            (
                "invoice",
                dict(
                    lines=[
                        dict(
                            description="Exonérée",
                            quantity=1,
                            unit_price_excluding_tax=50,
                            vat_rate=0,
                            vat_category_code="E",
                        )
                    ],
                    notes="TVA non applicable, article 293 B du CGI",
                ),
            ),
            ("credit_note", dict(source_invoice_uid=original.document_uid)),
            (
                "invoice",
                dict(
                    lines=[
                        dict(
                            description="Remise",
                            quantity=3,
                            unit_price_excluding_tax=".335",
                            discount_percent="12.5",
                            vat_rate="5.5",
                        )
                    ]
                ),
            ),
        ]:
            with self.subTest(kind=kind, changes=changes):
                file = generate_document(self.document(kind, **changes))
                self.assertGreater(file.file_size, 1000)

    def test_validator_unavailable_fails_closed(self):
        with patch("subprocess.run", side_effect=OSError("unavailable")):
            with self.assertRaises(BusinessError):
                generate_document(self.document())
        self.assertFalse(DocumentFile.objects.exists())

    def test_no_pdf_on_invalid_seller(self):
        self.company.vat_number = ""
        self.company.siren = ""
        self.company.save()
        with self.assertRaises(BusinessError):
            generate_document(self.document())
