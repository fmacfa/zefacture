from django.test import override_settings
from django.urls import reverse

from core.forms import CompanyForm
from core.models import CompanyMember
from core.services.documents import COMPANY_FIELDS
from .test_application import ApplicationCase


class CompanySettingsTests(ApplicationCase):
    def section(self, name):
        return reverse("company_settings_section", args=[name])

    def form_values(self, form):
        return {
            field.name: "" if field.value() is None else field.value() for field in form
        }

    def section_data(self, name, **changes):
        form = self.client.get(self.section(name)).context["form"]
        return (
            dict(self.form_values(form), company_uid=self.company.company_uid) | changes
        )

    def test_every_existing_field_has_one_section(self):
        self.assertContains(self.client.get(reverse("company_settings")), "Identité")
        names = []
        for section in (
            "identite",
            "coordonnees",
            "fiscalite",
            "paiement",
            "documents",
        ):
            response = self.client.get(self.section(section))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, f'action="{self.section(section)}"')
            self.assertContains(
                response, f'href="{self.section(section)}" aria-current="page"'
            )
            names.extend(response.context["form"].fields)
        self.assertCountEqual(names, COMPANY_FIELDS)

    def test_saving_a_section_preserves_other_settings_and_number_counters(self):
        payment = self.section_data("paiement", payment_terms="Paiement à 15 jours.")
        identity = self.section_data("identite", trade_name="Atelier Exemple")
        self.company.next_invoice_number = 37
        self.company.save(update_fields=["next_invoice_number"])
        self.assertRedirects(
            self.client.post(self.section("identite"), identity),
            self.section("identite"),
        )
        response = self.client.post(
            self.section("paiement"),
            payment
            | {"trade_name": "Valeur d’un ancien onglet", "next_invoice_number": 1},
            follow=True,
        )
        self.assertEqual(response.redirect_chain, [(self.section("paiement"), 302)])
        self.assertContains(response, "Réglages enregistrés")
        self.company.refresh_from_db()
        self.assertEqual(self.company.payment_terms, "Paiement à 15 jours.")
        self.assertEqual(self.company.trade_name, "Atelier Exemple")
        self.assertEqual(self.company.next_invoice_number, 37)

    def test_validation_keeps_values_on_the_same_page(self):
        response = self.client.post(
            self.section("coordonnees"),
            self.section_data("coordonnees", city="Lille", country_code="ZZ"),
        )
        self.assertContains(response, "Code pays ISO invalide", status_code=422)
        self.assertEqual(response.context["form"]["city"].value(), "Lille")
        self.company.refresh_from_db()
        self.assertEqual(self.company.city, "Paris")

    def test_previous_form_address_still_accepts_a_complete_form(self):
        data = self.form_values(CompanyForm(instance=self.company))
        data.update(city="Lille", country_code="fr", invoice_number_prefix="FAC")
        self.assertRedirects(
            self.client.post(reverse("company_settings"), data),
            reverse("company_settings"),
        )
        self.company.refresh_from_db()
        self.assertEqual(self.company.city, "Lille")
        self.assertEqual(self.company.country_code, "FR")
        self.assertEqual(self.company.invoice_number_prefix, "FAC")

    def test_old_tab_cannot_save_to_a_different_selected_company(self):
        data = self.section_data("identite", legal_name="Autre nom")
        CompanyMember.objects.create(
            company_uid=self.other.company_uid,
            user_uid=self.user.user_uid,
            role="owner",
        )
        self.client.post(f"/app/entreprises/{self.other.company_uid}/selectionner")
        self.assertEqual(
            self.client.post(self.section("identite"), data).status_code, 409
        )
        self.other.refresh_from_db()
        self.company.refresh_from_db()
        self.assertEqual(self.other.legal_name, "Autre entreprise")
        self.assertEqual(self.company.legal_name, "Entreprise Démonstration SAS")

    def test_member_can_read_but_only_admin_can_save(self):
        CompanyMember.objects.filter(company_uid=self.company.company_uid).update(
            role="viewer"
        )
        route = self.section("identite")
        self.assertContains(self.client.get(route), "Lecture seule")
        response = self.client.post(
            route, {"company_uid": self.company.company_uid, "legal_name": "Autre nom"}
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(SENSITIVE_REAUTH_REQUIRED=True)
    def test_password_verification_keeps_the_section_draft(self):
        route = self.section("paiement")
        data = self.section_data("paiement", payment_terms="Paiement à 15 jours.")
        response = self.client.post(route, data)
        self.assertContains(
            response, "Confirmer dans un nouvel onglet", status_code=403
        )
        self.assertEqual(
            response.context["form"]["payment_terms"].value(), data["payment_terms"]
        )
        self.company.refresh_from_db()
        self.assertEqual(self.company.payment_terms, "Paiement à 30 jours.")
        self.assertRedirects(
            self.client.post(
                f"/app/verification?next={route}", {"password": "OwnerPassword123!"}
            ),
            route,
        )
        self.assertRedirects(self.client.post(route, data), route)
        self.company.refresh_from_db()
        self.assertEqual(self.company.payment_terms, data["payment_terms"])
