from io import BytesIO
from decimal import Decimal
from PIL import Image, ImageDraw, ImageFont
from openpyxl import load_workbook
from pypdf import PdfReader
from django.core.files.uploadedfile import SimpleUploadedFile
from weasyprint import HTML

from core.models import BankAccount, BankTransaction, Document, StatementMatch
from core.services import bank_statements, finance, statement_pdf
from .test_application import ApplicationCase

LIST = "/app/finances/transactions"
DAY = "2026-05-12"


def ofx(entries, xml=False, currency="EUR"):
    def leaf(name, value):
        return f"<{name}>{value}" + (f"</{name}>" if xml else "\n")

    rows = "".join(
        "<STMTTRN>"
        + leaf("DTPOSTED", "20260512120000[+1:CET]")
        + leaf("TRNAMT", amount)
        + leaf("NAME", label)
        + leaf("FITID", ref)
        + "</STMTTRN>"
        for ref, label, amount in entries
    )
    return (
        "<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS>"
        + leaf("CURDEF", currency)
        + "<BANKACCTFROM>"
        + leaf("ACCTID", "123")
        + "</BANKACCTFROM><BANKTRANLIST>"
        + rows
        + "</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"
    ).encode()


class StatementTests(ApplicationCase):
    def setUp(self):
        super().setUp()
        self.acc = BankAccount.objects.create(
            company_uid=self.company.company_uid, name="Compte de test"
        )

    def upload(self, name, raw, account=None):
        return self.client.post(
            LIST + "/import",
            {
                "account_uid": (account or self.acc).account_uid,
                "file": SimpleUploadedFile(name, raw),
            },
        )

    def test_import_ofx_qfx_and_csv_repeated_rows_and_scoped_balances(self):
        records = [
            ("ref-a", "Paie", "-100.50"),
            ("ref-b", "Paie", "-100.50"),
            ("ref-c", "Remboursement", "35.00"),
        ]
        for name, data in (
            ("releve.ofx", ofx(records)),
            ("releve.qfx", ofx(records, True)),
            (
                "releve.csv",
                "date;libellé;montant\n12/05/2026;Paie;-100,50\n12/05/2026;Paie;-100,50\n12/05/2026;Remboursement;35,00\n".encode(),
            ),
        ):
            self.assertEqual(self.upload(name, data).status_code, 302)
        self.acc.refresh_from_db()
        self.assertEqual(
            (BankTransaction.objects.count(), self.acc.current_balance),
            (3, Decimal("-166")),
        )
        self.assertEqual(
            self.upload("usd.qfx", ofx(records, True, "USD")).status_code, 422
        )
        self.assertEqual(
            self.upload("multiple.ofx", ofx(records) + ofx(records)).status_code, 422
        )
        alien = BankAccount.objects.create(
            company_uid=self.other.company_uid, name="Compte inaccessible"
        )
        self.assertEqual(
            self.upload("releve.ofx", ofx(records), alien).status_code, 404
        )
        self.assertEqual(BankTransaction.objects.count(), 3)
        self.assertEqual(
            BankTransaction.objects.filter(account_uid=alien.account_uid).count(), 0
        )
        changed = bank_statements.import_file(
            self.company, self.acc.account_uid, "ofx", ofx([("ref-a", "Paie", "-500")])
        )
        self.assertEqual(changed, {"imported": 0, "skipped": 0, "rejected": 1})

    def test_same_bank_reference_in_two_accounts_and_signed_debit_columns(self):
        second = BankAccount.objects.create(
            company_uid=self.company.company_uid, name="Deuxième compte"
        )
        raw = ofx([("101", "Opération", "-42")])
        for acc in (self.acc, second):
            self.assertEqual(self.upload("releve.qfx", raw, acc).status_code, 302)
        csv = "date;libellé;débit;crédit\n2026-05-15;Salaire;-90;\n2026-05-16;Refund;;7\n".encode()
        self.assertEqual(self.upload("col.csv", csv).status_code, 302)
        self.acc.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(
            (self.acc.current_balance, second.current_balance), (-125, -42)
        )

    def test_expense_salary_vat_refund_and_invoice_are_independent(self):
        self.upload(
            "releve.ofx",
            ofx(
                [("w", "Paie", "-1500"), ("t", "Trésor", "-200"), ("r", "Crédit", "45")]
            ),
        )
        tx = BankTransaction.objects.get(label="Paie")
        path = LIST + f"/{tx.transaction_uid}/traiter"
        self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(
            self.client.post(path, {"category": "vat_refund"}).status_code, 422
        )
        self.assertEqual(
            self.client.post(
                path, {"category": "salary", "note": "Paie de mai"}
            ).status_code,
            302,
        )
        invoice = Document.objects.create(
            company_uid=self.company.company_uid,
            type="invoice",
            direction="incoming",
            status="received",
            amount_due=1500,
        )
        self.assertEqual(
            self.client.post(
                LIST + f"/{tx.transaction_uid}/rapprocher",
                {"document_uid": invoice.document_uid},
            ).status_code,
            409,
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.amount_due, 1500)
        for label, cat in (("Trésor", "vat_payment"), ("Crédit", "vat_refund")):
            item = BankTransaction.objects.get(label=label)
            self.assertEqual(
                self.client.post(
                    LIST + f"/{item.transaction_uid}/traiter", {"category": cat}
                ).status_code,
                302,
            )
        self.assertEqual(
            finance.vat_payments(self.company, DAY, DAY),
            [{"currency": "EUR", "vat_payment": 200, "vat_refund": 45}],
        )
        self.assertContains(
            self.client.get("/app/finances/tva", {"from": DAY, "to": DAY}),
            "TVA remboursée",
        )
        self.assertEqual(finance.vat_rows(self.company, DAY, DAY), [])
        export = self.client.get(
            LIST + "/export", {"categorie": "salary", "from": DAY, "to": DAY}
        )
        self.assertIn("Paie de mai", export.content.decode())
        self.assertNotIn("Trésor", export.content.decode())
        params = {"dataset": "transactions", "preset": "all"}
        for fmt in ("csv", "xlsx", "pdf"):
            reply = self.client.get("/app/rapports", {**params, "format": fmt})
            self.assertEqual(reply.status_code, 200)
            if fmt == "csv":
                content = reply.content.decode("utf-8-sig")
            elif fmt == "xlsx":
                sheet = load_workbook(BytesIO(reply.content)).active
                content = " ".join(str(v) for values in sheet.values for v in values)
            else:
                content = " ".join(
                    page.extract_text()
                    for page in PdfReader(BytesIO(reply.content)).pages
                )
            self.assertIn("Salaires", content)
            self.assertIn("Paie de mai", " ".join(content.split()))
        self.assertEqual(self.client.post(path, {"action": "reset"}).status_code, 302)
        self.assertEqual(
            self.client.post(
                LIST + f"/{tx.transaction_uid}/rapprocher",
                {"document_uid": invoice.document_uid},
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(path, {"category": "salary"}).status_code, 409
        )

    def test_manual_reimbursement_and_finance_permissions(self):
        data = {
            "transaction_date": DAY,
            "label": "Déplacement",
            "amount": "24.50",
            "direction": "outgoing",
            "account_uid": self.acc.account_uid,
            "standalone_category": "expense_reimbursement",
            "classification_note": "Remboursement du train",
        }
        self.assertEqual(self.client.post(LIST, data).status_code, 302)
        tx = BankTransaction.objects.get()
        self.assertFalse(tx.document_uid)
        self.assertEqual(
            self.client.get(LIST, {"traitement": "standalone"})
            .context["transactions"]
            .paginator.count,
            1,
        )
        other = BankTransaction.objects.create(company_uid=self.other.company_uid)
        self.assertEqual(
            self.client.post(
                LIST + f"/{other.transaction_uid}/traiter", {"category": "salary"}
            ).status_code,
            404,
        )
        self.acc.refresh_from_db()
        self.assertEqual(self.acc.current_balance, Decimal("-24.50"))

    def test_pdf_review_reads_real_text_atomic_confirmation_and_retry(self):
        pdf = HTML(
            string="<pre>12/05/2026  Paie  -91,25\n12/05/2026  Remboursement  +32,00</pre>"
        ).write_pdf()
        response = self.upload("releve.pdf", pdf)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["rows"][0]["amount"], "91.25")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertFalse(BankTransaction.objects.exists())
        token = response.context["token"]
        confirm = LIST + "/releve/confirmer"
        data = {
            "preview": token,
            "slot": ["0", "1"],
            "selected": ["0", "1"],
            "date_0": DAY,
            "label_0": "Paie vérifiée",
            "amount_0": "91.25",
            "direction_0": "outgoing",
            "date_1": DAY,
            "label_1": "Remboursement",
            "amount_1": "32",
            "direction_1": "",
        }
        self.assertEqual(self.client.post(confirm, data).status_code, 422)
        self.assertFalse(BankTransaction.objects.exists())
        self.assertFalse(StatementMatch.objects.exists())
        data["direction_1"] = "incoming"
        for _ in range(2):
            self.assertEqual(self.client.post(confirm, data).status_code, 302)
        self.assertEqual(
            self.client.post(confirm, data | {"preview": token + "x"}).status_code, 400
        )
        self.acc.refresh_from_db()
        self.assertEqual(
            (BankTransaction.objects.count(), self.acc.current_balance),
            (2, Decimal("-59.25")),
        )
        self.assertEqual(
            self.upload(
                "releve.csv",
                b"date;libelle;montant\n2026-05-12;Paie verifiee;-91.25\n".replace(
                    b"verifiee", "vérifiée".encode()
                ),
            ).status_code,
            302,
        )
        self.assertEqual(BankTransaction.objects.count(), 2)

    def test_scanned_pdf_requires_confirmation_and_unclear_columns_stay_blank(self):
        img = Image.new("RGB", (1400, 420), "white")
        ink = ImageDraw.Draw(img)
        font = ImageFont.load_default(size=37)
        ink.text((45, 50), "RELEVE BANCAIRE", font=font, fill="black")
        ink.text((45, 145), "12/05/2026   COTISATION   -35,00", font=font, fill="black")
        data = BytesIO()
        img.save(data, format="PDF", resolution=140)
        response = self.upload("scan.pdf", data.getvalue())
        self.assertEqual(response.status_code, 200)
        self.assertIn("COTISATION", response.context["transcript"].upper())
        self.assertFalse(BankTransaction.objects.exists())
        ambiguous = list(
            statement_pdf.proposed_rows("12/05/2026 Achat 25,00 2 300,00", 1)
        )[0]
        self.assertEqual((ambiguous["direction"], ambiguous["amount"]), ("", ""))
        self.assertEqual(
            list(statement_pdf.proposed_rows("12/05/2026 Solde précédent 952,00", 1)),
            [],
        )
