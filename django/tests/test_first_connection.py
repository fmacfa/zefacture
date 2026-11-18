import io
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from core.common import BusinessError, digest
from core.models import Company, CompanyMember, InstanceSetup, User
from core.services.instance_setup import create_first_owner, issue_setup_code


INSTALLATION_URL = "/connexion/installation"


@override_settings(REGISTRATION_OPEN=False, MFA_REQUIRED_FOR_ADMINS=True)
class FirstConnectionTests(TestCase):
    def setUp(self):
        self.password = secrets.token_urlsafe(24)
        self.data = {
            "email": "Proprietaire@Example.TEST",
            "name": "Premier propriétaire",
            "company_name": "Entreprise de test",
            "password": self.password,
            "password_confirmation": self.password,
            "terms": "1",
        }

    def issue_code(self):
        output = io.StringIO()
        call_command("setup_code", stdout=output)
        return output.getvalue().strip()

    def test_wizard_requires_a_code_and_an_empty_instance(self):
        self.assertContains(self.client.get("/connexion"), "Se connecter")
        self.assertRedirects(self.client.get(INSTALLATION_URL), "/connexion")
        code = self.issue_code()
        state = InstanceSetup.objects.get()
        self.assertEqual(state.code_hash, digest(code))

        response = self.client.get("/connexion", follow=True)
        self.assertEqual(response.redirect_chain, [(INSTALLATION_URL, 302)])
        self.assertContains(response, "Créer le premier compte")
        self.assertNotContains(response, code)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        self.assertFalse(User.objects.exists())

        Company.objects.create(legal_name="Entreprise préexistante")
        self.assertRedirects(
            self.client.post(INSTALLATION_URL, self.data), "/connexion"
        )
        with self.assertRaises(CommandError):
            self.issue_code()

    def test_creates_first_owner_with_csrf_and_requires_mfa(self):
        code = self.issue_code()
        browser = Client(enforce_csrf_checks=True)
        self.assertEqual(browser.get(INSTALLATION_URL).status_code, 200)
        form = {**self.data, "setup_code": code}
        self.assertEqual(browser.post(INSTALLATION_URL, form).status_code, 403)
        self.assertFalse(User.objects.exists())
        form["csrfmiddlewaretoken"] = browser.cookies["csrftoken"].value

        response = browser.post(INSTALLATION_URL, form)
        self.assertRedirects(response, "/app/compte/securite")
        user = User.objects.get()
        company = Company.objects.get()
        member = CompanyMember.objects.get()
        self.assertEqual(user.email, "proprietaire@example.test")
        self.assertEqual(user.name, self.data["name"])
        self.assertTrue(user.is_staff and user.is_superuser)
        self.assertTrue(user.check_password(self.password))
        self.assertTrue(user.terms_accepted_at)
        self.assertEqual(company.legal_name, self.data["company_name"])
        self.assertEqual(company.billing_email, user.email)
        self.assertEqual(company.owner_user_uid, user.user_uid)
        self.assertEqual(member.user_uid, user.user_uid)
        self.assertEqual(member.company_uid, company.company_uid)
        self.assertEqual(member.role, "owner")
        self.assertEqual(browser.session["session_version"], user.session_version)
        self.assertRedirects(browser.get("/app"), "/app/compte/securite")

        setup = InstanceSetup.objects.get()
        self.assertEqual(setup.code_hash, "")
        self.assertIsNone(setup.expires_at)
        self.assertIsNotNone(setup.completed_at)
        other_client = Client()
        self.assertRedirects(other_client.post(INSTALLATION_URL, form), "/connexion")
        self.assertEqual(User.objects.count(), 1)
        self.assertContains(other_client.get("/connexion"), "Se connecter")
        self.assertRedirects(
            other_client.post(
                "/connexion", {"email": user.email, "password": self.password}
            ),
            "/app",
            target_status_code=302,
        )
        with self.assertRaises(CommandError):
            self.issue_code()

    def test_wrong_replaced_and_expired_codes_cannot_create_a_user(self):
        old = self.issue_code()
        current = self.issue_code()
        for code in (old, secrets.token_urlsafe(32)):
            response = self.client.post(
                INSTALLATION_URL, {**self.data, "setup_code": code}
            )
            self.assertContains(
                response, "Code d’installation invalide ou expiré", status_code=403
            )
            self.assertNotContains(response, code, status_code=403)
        InstanceSetup.objects.update(expires_at=timezone.now())
        self.assertRedirects(
            self.client.post(INSTALLATION_URL, {**self.data, "setup_code": current}),
            "/connexion",
        )
        self.assertFalse(User.objects.exists())
        self.assertFalse(Company.objects.exists())

    def test_bad_form_and_failed_creation_leave_the_code_usable(self):
        code = self.issue_code()
        form = {**self.data, "setup_code": code}
        bad_form = {**form, "password_confirmation": "Un autre mot de passe"}
        response = self.client.post(INSTALLATION_URL, bad_form)
        self.assertContains(response, "Les mots de passe diffèrent.", status_code=422)
        self.assertNotContains(response, self.password, status_code=422)
        self.assertNotContains(response, code, status_code=422)
        with (
            patch(
                "core.services.instance_setup.CompanyMember.objects.create",
                side_effect=IntegrityError,
            ),
            self.assertRaises(IntegrityError),
        ):
            create_first_owner(
                address=form["email"],
                name=form["name"],
                company_name=form["company_name"],
                password=form["password"],
                setup_code=code,
            )
        self.assertFalse(User.objects.exists())
        self.assertFalse(Company.objects.exists())
        self.assertEqual(InstanceSetup.objects.get().code_hash, digest(code))
        self.assertRedirects(
            self.client.post(INSTALLATION_URL, form), "/app/compte/securite"
        )

    def test_cli_bootstrap_disables_the_wizard(self):
        code = self.issue_code()
        with patch.dict(os.environ, {"BOOTSTRAP_PASSWORD": self.password}):
            call_command("bootstrap", email=self.data["email"], stdout=io.StringIO())
        self.assertTrue(User.objects.get().is_superuser)
        self.assertEqual(CompanyMember.objects.get().role, "owner")
        self.assertRedirects(
            self.client.post(INSTALLATION_URL, {**self.data, "setup_code": code}),
            "/connexion",
        )

    def test_setup_does_not_open_public_registration(self):
        self.issue_code()
        response = self.client.post("/inscription", self.data)
        self.assertContains(response, "Les inscriptions publiques sont fermées.")
        self.assertFalse(User.objects.exists())


@skipUnless(connection.vendor == "postgresql", "Vérifie le verrouillage PostgreSQL.")
class ConcurrentFirstConnectionTests(TransactionTestCase):
    def test_one_owner_is_created_when_two_requests_submit_the_same_code(self):
        code = issue_setup_code()
        start = Barrier(2, timeout=10)

        def attempt(number):
            try:
                start.wait()
                create_first_owner(
                    address=f"owner-{number}@example.test",
                    name="Propriétaire",
                    company_name="Entreprise de test",
                    password=secrets.token_urlsafe(24),
                    setup_code=code,
                )
                return True
            except BusinessError as exc:
                if exc.status != 403:
                    raise
                return False
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(pool.map(attempt, [1, 2]), [True, False])
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(Company.objects.count(), 1)
        self.assertEqual(CompanyMember.objects.count(), 1)
