import csv
from decimal import Decimal
from io import BytesIO, StringIO
from unittest.mock import patch

from django.test import Client
from openpyxl import load_workbook

from core.models import BankAccount, CompanyMember, UserPreferences
from core.numbers import NumberStyle, current_style, number_context
from core.services import finance, reports
from .test_application import ApplicationCase


class DisplayPreferencesTests(ApplicationCase):
    def choose(self, group, decimal):
        return self.client.post(
            "/app/compte/affichage",
            {
                "theme": "system",
                "density": "compact",
                "number_grouping": group,
                "number_decimal": decimal,
            },
        )

    def test_styles_roundtrip_precision_and_user_scoping(self):
        for group, decimal, expected in [
            ("space", "comma", "1 234 567,89"),
            ("dot", "comma", "1.234.567,89"),
            ("comma", "dot", "1,234,567.89"),
            ("none", "comma", "1234567,89"),
            ("none", "dot", "1234567.89"),
            ("space", "dot", "1 234 567.89"),
        ]:
            style = NumberStyle(group, decimal, True)
            self.assertEqual(style.format(Decimal("1234567.89"), 2), expected)
            self.assertEqual(style.parse("-" + expected), "-1234567.89")
        style = NumberStyle("comma", "dot", True)
        large = Decimal("123456789012345678901234.789012")
        self.assertEqual(Decimal(style.parse(style.format(large))), large)
        with self.assertRaises(ValueError):
            style.parse("1,23.78")
        self.assertRedirects(self.choose("dot", "comma"), "/app/compte/affichage")
        self.assertEqual(self.choose("dot", "dot").status_code, 422)
        BankAccount.objects.create(
            company_uid=self.company.company_uid,
            name="Caisse",
            current_balance=1234567.89,
        )
        self.assertContains(self.client.get("/app/finances/comptes"), "1.234.567,89")
        self.assertEqual(
            UserPreferences.objects.get(user_uid=self.user.user_uid).number_grouping,
            "dot",
        )
        self.assertFalse(current_style().customized)

    def test_exports_csv_excel_and_pdf_keep_identifiers_and_values(self):
        style = NumberStyle("dot", "comma", True)
        selected = reports.period({"preset": "all"})
        rows = [["000123456", Decimal("1234.50"), Decimal("-23.04")]]
        with number_context(style):
            text = finance.csv_text(["Code", "Total", "Écart"], rows)
            self.assertEqual(
                list(csv.reader(StringIO(text), delimiter=";"))[1],
                ["000123456", "1.234,50", "-23,04"],
            )
            product = self.document()
            self.assertEqual(
                product.seller["number_display"], {"group": "dot", "decimal": "comma"}
            )
            args = (
                ["Code", "Total", "Écart"],
                rows,
                "Exemple",
                self.company,
                selected,
                None,
            )
            data, _ = reports.export_bytes("xlsx", *args)
            wb = load_workbook(BytesIO(data))
            self.assertEqual(wb.active.title, "Affichage")
            self.assertEqual(wb["Données"]["B2"].value, 1234.5)
            self.assertEqual(wb["Affichage"]["B2"].value, "1.234,50")
            self.assertEqual(wb["Affichage"]["A2"].value, "000123456")
            data, _ = reports.export_bytes("pdf", *args)
            from pypdf import PdfReader

            rendered = " ".join(page.extract_text() for page in PdfReader(BytesIO(data)).pages)
            self.assertIn("1.234,50", rendered)
            account = BankAccount.objects.create(
                company_uid=self.company.company_uid, name="Banque test"
            )
            imported = finance.import_csv(
                self.company,
                account.account_uid,
                "date;libellé;montant\n2026-10-25;Remise;1.234,50\n".encode(),
            )
            self.assertEqual(imported["imported"], 1)
            account.refresh_from_db()
            self.assertEqual(account.current_balance, Decimal("1234.50"))

    def test_navigation_permissions_and_current_page(self):
        owner = self.client.get("/app/finances/transactions")
        self.assertContains(
            owner,
            'href="/app/finances/transactions" class="active" aria-current="page"',
        )
        self.assertContains(owner, "Réglages de l’entreprise")
        CompanyMember.objects.filter(company_uid=self.company.company_uid).update(
            role="viewer"
        )
        reader = self.client.get("/app")
        self.assertNotContains(reader, 'href="/app/finances/comptes"')
        self.assertNotContains(reader, 'href="/app/developpeurs"')
        self.assertContains(reader, 'href="/app/reception"')

    def test_password_change_feedback_and_both_login_limits_expire(self):
        self.assertContains(self.client.get("/app/compte"), "data-password-strength")
        wrong = {"email": self.user.email, "password": "incorrect"}
        correct = wrong | {"password": "OwnerPassword123!"}
        guest = Client()
        from time import time

        now = int(time())
        with patch("core.security.time.time", return_value=now):
            for _ in range(10):
                self.assertEqual(
                    guest.post(
                        "/connexion", wrong, REMOTE_ADDR="192.0.2.10"
                    ).status_code,
                    200,
                )
            limited = guest.post("/connexion", correct, REMOTE_ADDR="192.0.2.10")
            self.assertEqual(
                (limited.status_code, limited["Retry-After"]), (429, "900")
            )
            for i in range(10):
                guest.post("/connexion", wrong, REMOTE_ADDR=f"192.0.2.{30 + i}")
            self.assertEqual(
                guest.post("/connexion", correct, REMOTE_ADDR="192.0.2.51").status_code,
                429,
            )
            self.assertNotIn("_auth_user_id", guest.session)
        with patch("core.security.time.time", return_value=now + 901):
            self.assertEqual(
                guest.post("/connexion", correct, REMOTE_ADDR="192.0.2.10").status_code,
                302,
            )
            self.assertIn("_auth_user_id", guest.session)
