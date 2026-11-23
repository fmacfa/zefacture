import io
import zipfile
from datetime import date
from decimal import Decimal

from openpyxl import Workbook
from pypdf import PdfReader, PdfWriter

from core.common import BusinessError
from core.models import (
    BankAccount,
    BankTransaction,
    CompanyMember,
    EvidenceLink,
    ExpenseClaim,
    ExpenseReimbursement,
    ExpenseRegisterLine,
    Ingestion,
    StatementControl,
)
from core.services import (
    bank_allocations,
    evidence,
    expense_claims,
    expense_registers,
    month_end,
)
from .test_application import ApplicationCase


class MonthlyExpenseTests(ApplicationCase):
    def receipt(self, **values):
        return Ingestion.objects.create(
            **(
                {
                    "company_uid": self.company.company_uid,
                    "source_kind": "expense_note",
                    "uploaded_by_user_uid": self.user.user_uid,
                    "expense_paid_by": "personal",
                    "expense_payer_name": "Camille Exemple",
                    "merchant_name": "Maison Exemple",
                    "expense_date": "2025-04-03",
                    "expense_amount": Decimal("10"),
                    "expense_paid_eur": Decimal("10"),
                }
                | values
            )
        )

    def account(self, currency="EUR"):
        return BankAccount.objects.create(
            company_uid=self.company.company_uid,
            name="Compte " + currency,
            currency_code=currency,
        )

    def debit(self, account, amount=15, day="2025-04-23"):
        return BankTransaction.objects.create(
            company_uid=self.company.company_uid,
            account_uid=account.account_uid,
            amount=amount,
            direction="outgoing",
            transaction_date=day,
            label="Virement de test",
        )

    def make_claim(self):
        return expense_claims.make_draft(
            self.company, self.user.user_uid, "Camille Exemple", date(2025, 4, 1)
        )

    def test_group_partial_reversible_and_isolated(self):
        first = self.receipt()
        second = self.receipt(expense_date="2025-04-04")
        ExpenseReimbursement.objects.create(
            receipt=first, status="unknown", updated_by=self.user.user_uid
        )
        self.receipt(expense_payer_name="")
        self.receipt(company_uid=self.other.company_uid)
        claim = self.make_claim()
        self.assertEqual(self.make_claim().lines.count(), 2)
        self.assertEqual(
            ExpenseReimbursement.objects.get(receipt=first).status, "unknown"
        )
        self.assertContains(
            self.client.get("/app/notes-de-frais/groupees/" + claim.claim_uid),
            "Brouillon",
        )
        expense_claims.approve(self.company, claim, self.user.user_uid)
        target = "claim-" + claim.claim_uid
        tx = self.debit(self.account())
        bank_allocations.allocate(
            self.company, tx, [{"target": target, "amount": "-15.00"}]
        )
        self.assertEqual(expense_claims.balances(claim), (20, 15, 5))
        self.assertEqual(ExpenseReimbursement.objects.get(receipt=first).status, "paid")
        self.assertEqual(ExpenseReimbursement.objects.get(receipt=second).status, "due")
        with self.assertRaises(BusinessError):
            bank_allocations.allocate(
                self.company,
                self.debit(self.account()),
                [{"target": target, "amount": "-15.00"}],
            )
        with self.assertRaises(BusinessError):
            bank_allocations.allocate(
                self.company,
                self.debit(self.account("USD"), 5),
                [{"target": target, "amount": "-5"}],
            )
        with self.assertRaises(BusinessError):
            expense_claims.reopen(self.company, claim)
        bank_allocations.reset(self.company, tx, tx.allocations.first().pk)
        expense_claims.reopen(self.company, claim)
        self.assertEqual(
            ExpenseReimbursement.objects.get(receipt=first).status, "unknown"
        )
        self.assertFalse(ExpenseReimbursement.objects.filter(receipt=second).exists())

    def test_group_missing_euros_and_changed_payer(self):
        first = self.receipt(expense_paid_eur=None)
        claim = self.make_claim()
        with self.assertRaises(BusinessError):
            expense_claims.approve(self.company, claim, self.user.user_uid)
        Ingestion.objects.filter(pk=first.pk).update(
            expense_payer_name="Autre personne"
        )
        with self.assertRaises(BusinessError):
            expense_claims.approve(self.company, claim, self.user.user_uid)
        self.assertFalse(ExpenseClaim.objects.get(pk=claim.pk).approved)

    def workbook(self, values, filename="Registre.xlsx"):
        w = Workbook()
        w.active.append(["N°", "Date", "Commerce", "TTC", "Payeur", "Fichier", "Page"])
        for v in values:
            w.active.append(v)
        buf = io.BytesIO()
        w.save(buf)
        return expense_registers.upload(self.company, filename, buf.getvalue())

    def test_register_preview_versions_and_stale_confirmation(self):
        item = self.receipt()
        base = ["R-1", "2025-04-03", "Maison Exemple", 10, None, None, None]
        opts = dict(
            register="Voyages",
            sheet="Sheet",
            header=1,
            mapping={
                "key": 1,
                "expense_date": 2,
                "merchant_name": 3,
                "expense_amount": 4,
                "expense_payer_name": 5,
            },
            currency="EUR",
        )
        book = self.workbook([base])

        def preview(book):
            return expense_registers.preview(self.company, book, **opts)

        def commit(book, rows):
            return expense_registers.commit(
                self.company,
                self.user.user_uid,
                book,
                opts,
                ["2"],
                expense_registers.fingerprint(rows),
            )

        first = preview(book)
        self.assertEqual(first[0]["item"].pk, item.pk)
        self.assertEqual(first[0]["changes"], [])
        commit(book, first)
        self.assertEqual(preview(book)[0]["state"], "Inchangée")
        changed = base[:]
        changed[3] = 12
        revision = self.workbook([changed], "Révision.xlsx")
        new = preview(revision)
        self.assertEqual(
            new[0]["changes"],
            [("expense_amount", "TTC en devise d’origine", "10", "12")],
        )
        Ingestion.objects.filter(pk=item.pk).update(expense_amount=11)
        with self.assertRaises(BusinessError):
            commit(revision, new)
        commit(revision, preview(revision))
        item.refresh_from_db()
        self.assertEqual((item.expense_amount, item.expense_paid_eur), (12, 10))
        self.assertEqual(ExpenseRegisterLine.objects.count(), 1)
        self.assertFalse(ExpenseReimbursement.objects.exists())
        url = "/app/notes-de-frais/registres/" + revision.upload_uid
        self.assertContains(self.client.get(url), "Correspondance des colonnes")

    @staticmethod
    def pdf():
        writer = PdfWriter()
        writer.add_blank_page(100, 100)
        writer.add_blank_page(200, 300)
        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()

    def test_shared_proof_pages_and_cross_company(self):
        raw = self.pdf()
        source = evidence.store(self.company, "Liasse.pdf", raw)
        second = evidence.store(self.company, "Autre nom.pdf", raw)
        first = self.receipt()
        tx = self.debit(self.account())
        self.assertEqual(source.pk, second.pk)
        evidence.attach(first, source, 2)
        evidence.attach(first, source, 2)
        evidence.attach(tx, source)
        self.assertEqual(EvidenceLink.objects.count(), 2)
        response = self.client.get(
            "/app/justificatifs/source/" + source.file_uid + "/2"
        )
        page = PdfReader(io.BytesIO(b"".join(response.streaming_content)))
        self.assertEqual((len(page.pages), page.pages[0].mediabox.width), (1, 200))
        with self.assertRaises(BusinessError):
            evidence.attach(self.receipt(company_uid=self.other.company_uid), source)
        self.assertEqual(
            self.client.get(
                "/app/justificatifs/source/" + source.file_uid + "/3"
            ).status_code,
            404,
        )
        self.assertContains(
            self.client.get("/app/justificatifs/frais/" + first.ingestion_uid), "page 2"
        )
        self.assertContains(
            self.client.get(
                "/app/finances/transactions/" + tx.transaction_uid + "/traiter"
            ),
            "Liasse.pdf",
        )
        with self.assertRaises(BusinessError):
            evidence.store(self.company, "invalide.pdf", b"pas un document")

    def test_register_pages_do_not_collapse_a_bundle(self):
        evidence.store(self.company, "Billets.pdf", self.pdf())
        self.receipt(
            original_filename="Billets.pdf", expense_amount=None, merchant_name=None
        )
        rows = [
            [1, "2025-04-01", "Train fictif", 12, "=A2", "Billets.pdf", 1],
            [2, "2025-04-02", "Thé exemple", 8, None, "Billets.pdf", 2],
        ]
        book = self.workbook(rows)
        opts = dict(
            register="Voyage",
            sheet="Sheet",
            header=1,
            currency="EUR",
            mapping={
                "key": 1,
                "expense_date": 2,
                "merchant_name": 3,
                "expense_amount": 4,
                "expense_payer_name": 5,
                "file": 6,
                "page": 7,
            },
        )
        preview = expense_registers.preview(self.company, book, **opts)
        self.assertTrue(all(r["state"] == "Nouvelle dépense" for r in preview))
        expense_registers.commit(
            self.company,
            self.user.user_uid,
            book,
            opts,
            ["2", "3"],
            expense_registers.fingerprint(preview),
        )
        self.assertEqual(EvidenceLink.objects.count(), 2)
        self.assertFalse(
            ExpenseRegisterLine.objects.exclude(receipt__expense_payer_name="").exists()
        )
        self.assertEqual(
            [r["state"] for r in expense_registers.preview(self.company, book, **opts)],
            ["Inchangée", "Inchangée"],
        )

    def test_controls_printed_and_separate_credit_debit_differences(self):
        account = self.account()
        tx = self.debit(account, 10)
        data = month_end.printed(
            "SOLDE CREDITEUR AU 31/03/2025   100,00\nTOTAL DES MOUVEMENTS  10,00   0,00\nSOLDE CREDITEUR AU 30/04/2025*  90,00"
        )
        control = month_end.save_control(account, data, self.user.user_uid, "Avril")
        self.assertTrue(month_end.ledger(control)["matches"])
        self.assertEqual(
            month_end.save_control(account, data, self.user.user_uid, "Copie").pk,
            control.pk,
        )
        with self.assertRaises(BusinessError):
            month_end.save_control(
                account, {**data, "end": "2025-04-28"}, self.user.user_uid, "Annexe"
            )
        self.debit(account, 1)
        BankTransaction.objects.create(
            company_uid=self.company.company_uid,
            account_uid=account.account_uid,
            transaction_date=tx.transaction_date,
            direction="incoming",
            amount=1,
        )
        result = month_end.ledger(control)
        self.assertEqual(result["delta"], 0)
        self.assertFalse(result["matches"])
        self.assertContains(
            self.client.get("/app/cloture?month=2025-04"), "Écart à vérifier"
        )
        self.assertContains(
            self.client.get("/app/cloture?month=2025-05"), "Relevé à renseigner"
        )
        self.assertEqual(StatementControl.objects.count(), 1)

    def test_chart_currency_link_access_and_nonfinancial_role(self):
        tx = self.debit(self.account("USD"), 17)
        self.debit(self.account(), 21)
        self.receipt()
        response = self.client.get("/app?from=2025-04-01&to=2025-04-30&devise=USD")
        self.assertContains(response, "Flux de trésorerie")
        chart = response.context["bank_chart"]
        self.assertEqual(chart["ticks"][0]["outgoing"], 17)
        detail = self.client.get(chart["ticks"][0]["url"].split("#")[0])
        self.assertEqual([t.pk for t in detail.context["transactions"]], [tx.pk])
        for url in (
            "/app/notes-de-frais/groupees",
            "/app/notes-de-frais/registres",
            "/app/cloture",
        ):
            self.assertEqual(self.client.get(url).status_code, 200)
        CompanyMember.objects.filter(
            company_uid=self.company.company_uid, user_uid=self.user.user_uid
        ).update(role="viewer")
        self.assertNotContains(self.client.get("/app"), "Flux de trésorerie")
        self.assertEqual(self.client.get("/app/cloture").status_code, 403)
        self.assertEqual(
            self.client.get("/app/notes-de-frais/registres").status_code, 403
        )

    def test_monthly_review_reason_and_undated_receipts(self):
        receipt = self.receipt(merchant_name=None, expense_date=None)
        url = "/app/cloture"
        response = self.client.post(
            url,
            {
                "action": "annotate",
                "uid": receipt.ingestion_uid,
                "document_date": "2025-04-02",
                "reason": "Deux montants différents",
                "next_action": "Relire le total imprimé",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertContains(
            self.client.get(url + "?month=2025-04"), "Deux montants différents"
        )
        self.assertNotContains(
            self.client.get(url + "?month=2025-05"), "Deux montants différents"
        )

    def test_register_confirmation_through_http(self):
        book = self.workbook(
            [["A", "2025-04-01", "Exemple Atelier", 12, None, None, None]]
        )
        url = "/app/notes-de-frais/registres/" + book.upload_uid
        preview = self.client.post(
            url,
            {
                "register": "Avril",
                "sheet": "Sheet",
                "header": "1",
                "currency": "EUR",
                "col_expense_date": 2,
                "col_merchant_name": 3,
                "col_expense_amount": 4,
                "col_key": 1,
            },
        )
        self.assertContains(preview, "Nouvelle dépense")
        self.assertEqual(
            self.client.post(
                url, {"apply": preview.context["token"], "row": "2"}
            ).status_code,
            302,
        )
        receipt = ExpenseRegisterLine.objects.get().receipt
        self.assertContains(
            self.client.get("/app/imports/" + receipt.ingestion_uid),
            "Cette dépense provient d’un registre Excel",
        )
        self.assertFalse(receipt.proofs.exists())
        self.assertEqual(
            self.client.get(
                "/app/imports/" + receipt.ingestion_uid + "/telecharger"
            ).status_code,
            302,
        )

    def test_register_can_preserve_a_reviewed_receipt(self):
        item = self.receipt(original_filename="original.pdf")
        book = self.workbook(
            [["A", "2025-04-03", "Maison Exemple", 13, None, "original.pdf", None]]
        )
        opts = dict(
            register="Relecture",
            sheet="Sheet",
            header=1,
            currency="EUR",
            mapping={
                "expense_date": 2,
                "merchant_name": 3,
                "expense_amount": 4,
                "file": 6,
            },
        )
        rows = expense_registers.preview(self.company, book, **opts)
        self.assertEqual(rows[0]["item"].pk, item.pk)
        expense_registers.commit(
            self.company,
            self.user.user_uid,
            book,
            opts,
            ["2"],
            expense_registers.fingerprint(rows),
            preserve_existing=True,
        )
        item.refresh_from_db()
        self.assertEqual(item.expense_amount, 10)
        self.assertEqual(
            item.register_lines.get().source_values["expense_amount"], "13"
        )

    def test_styled_tail_and_persisted_formula_result(self):
        book = Workbook()
        page = book.active
        page.append(["Date", "Commerce", "Total", "Euros payés"])
        page.append(["2025-04-05", "Restaurant exemple", "=10+2", "=10+2"])
        page.cell(1000000, 11).number_format = "0.00"
        origin = io.BytesIO()
        book.save(origin)
        raw = io.BytesIO()
        with zipfile.ZipFile(origin) as src, zipfile.ZipFile(raw, "w") as dst:
            for info in src.infolist():
                data = src.read(info)
                if info.filename == "xl/worksheets/sheet1.xml":
                    data = data.replace(b"<f>10+2</f><v></v>", b"<f>10+2</f><v>12</v>")
                dst.writestr(info, data)
        file = expense_registers.upload(self.company, "Frais.xlsx", raw.getvalue())
        row = expense_registers.preview(
            self.company,
            file,
            register="Voyage",
            sheet="Sheet",
            header=1,
            mapping={
                "expense_date": 1,
                "merchant_name": 2,
                "expense_amount": 3,
                "expense_paid_eur": 4,
            },
            currency="EUR",
        )[0]
        self.assertTrue(row["cached_total"])
        self.assertEqual(row["amount"], "12")
        self.assertNotIn("expense_paid_eur", row["values"])

    def test_currency_from_billing_without_a_bank_account(self):
        from core.services.activity_dashboard import bank_chart
        from core.services.reports import period

        self.debit(self.account(), 5)
        chart = bank_chart(self.company, period({"preset": "all"}), "USD", {"USD"})
        self.assertEqual(chart["currency"], "USD")
        self.assertEqual(chart["currencies"], ["EUR", "USD"])
        self.assertNotIn("ticks", chart)
