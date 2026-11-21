from django import forms
from .common import BusinessError, email, today
from .models import Company, Customer, InventoryItem, BankAccount, Forecast
from .services.documents import CUSTOMER_FIELDS, COMPANY_FIELDS
from .country_fields import CountryInput, country_code as country_from_name

LABELS = {
    "legal_name": "Nom / raison sociale",
    "local_registration": "Identifiant d’immatriculation local",
    "trade_name": "Nom commercial",
    "type": "Type de client",
    "name": "Nom",
    "siren": "SIREN",
    "siret": "SIRET",
    "vat_number": "Numéro de TVA",
    "address_line1": "Adresse",
    "address_line2": "Complément",
    "postal_code": "Code postal",
    "city": "Ville",
    "country_code": "Pays (code ISO)",
    "billing_email": "Email de facturation",
    "support_email": "Email de contact",
    "phone": "Téléphone",
    "website": "Site internet",
    "bank_iban": "IBAN",
    "bank_bic": "BIC",
    "payment_terms": "Conditions de paiement",
    "invoice_footer": "Mentions de bas de facture",
    "legal_form": "Forme juridique",
    "share_capital": "Capital social",
    "rcs_city": "Ville du greffe RCS",
    "rcs_number": "Numéro de registre",
    "ape_code": "Code APE",
    "vat_regime": "Régime TVA",
    "has_vat_on_debits": "TVA sur les débits",
    "quote_number_prefix": "Préfixe des devis",
    "invoice_number_prefix": "Préfixe des factures",
    "credit_note_number_prefix": "Préfixe des avoirs",
    "electronic_address": "Adresse électronique de facturation",
    "electronic_address_scheme": "Schéma de l'adresse électronique",
    "reference": "Référence",
    "description": "Description",
    "item_type": "Type d'article",
    "stock_tracking": "Suivre le stock",
    "reorder_level": "Seuil d'alerte",
    "unit_code": "Unité",
    "unit_price_excluding_tax": "Prix unitaire HT",
    "vat_rate": "TVA (%)",
    "bank_name": "Banque",
    "iban": "IBAN",
    "currency_code": "Devise",
    "opening_balance": "Solde initial",
    "label": "Libellé",
    "due_date": "Échéance",
    "amount": "Montant",
    "direction": "Sens",
}


class FirstConnectionForm(forms.Form):
    country_code = forms.CharField(label="Pays d’établissement", initial="FR", required=False, widget=CountryInput)
    field_order = ["company_name", "country_code", "name", "email", "password"]

    def clean_country_code(self):
        return country_from_name(self.cleaned_data.get("country_code") or "FR")


    company_name = forms.CharField(
        label="Nom de l’entreprise",
        max_length=254,
        widget=forms.TextInput(
            attrs={"autocomplete": "organization", "placeholder": "Mon entreprise"}
        ),
    )
    name = forms.CharField(
        label="Votre nom",
        max_length=120,
        widget=forms.TextInput(
            attrs={"autocomplete": "name", "placeholder": "Prénom Nom"}
        ),
    )
    email = forms.EmailField(
        label="Adresse e-mail",
        max_length=254,
        widget=forms.EmailInput(
            attrs={"autocomplete": "username", "placeholder": "vous@example.com"}
        ),
    )
    password = forms.CharField(
        label="Mot de passe",
        strip=False,
        min_length=10,
        max_length=1024,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Au moins 10 caractères.",
    )


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.label = LABELS.get(name, name.replace("_", " ").capitalize())
            if isinstance(field.widget, forms.Textarea):
                field.widget = (
                    forms.Textarea(attrs={"rows": 3})
                    if name in {"description", "payment_terms", "invoice_footer"}
                    else forms.TextInput()
                )
            if name in {"email", "billing_email", "support_email"}:
                field.widget = forms.EmailInput()
            if name.endswith("date"):
                field.widget = forms.DateInput(attrs={"type": "date"})
        for name, choices in {
            "type": [("business", "Professionnel"), ("individual", "Particulier")],
            "item_type": [("service", "Service"), ("product", "Produit")],
            "direction": [("incoming", "Encaissement"), ("outgoing", "Décaissement")],
        }.items():
            if name in self.fields:
                self.fields[name].widget = forms.Select(choices=choices)

        for field in self.fields.values():
            field.widget.attrs["aria-label"] = field.label


class CustomerForm(StyledModelForm):
    type = forms.ChoiceField(
        choices=[("business", "Professionnel"), ("individual", "Particulier")],
        label="Type de client",
    )

    class Meta:
        model = Customer
        fields = CUSTOMER_FIELDS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, autocomplete in {
            "email": "email",
            "phone": "tel",
            "address_line1": "address-line1",
            "address_line2": "address-line2",
            "postal_code": "postal-code",
            "city": "address-level2",
            "country_code": "country",
        }.items():
            self.fields[name].widget.attrs["autocomplete"] = autocomplete
        self.fields["country_code"].widget = CountryInput()
        self.fields["country_code"].label = "Pays"
        self.fields["country_code"].help_text = "Recherchez par nom ou par code ISO."
        self.fields["phone"].widget.attrs["inputmode"] = "tel"
        for name, length in (("siren", 9), ("siret", 14)):
            self.fields[name].widget.attrs["inputmode"] = "numeric"
            self.fields[name].help_text = f"{length} chiffres pour un client français."

    def clean_siren(self):
        return self._identifier("siren", 9)

    def clean_siret(self):
        return self._identifier("siret", 14)

    def _identifier(self, field, length):
        value = "".join((self.cleaned_data.get(field) or "").split())
        if value and (len(value) != length or not value.isascii() or not value.isdigit()):
            raise forms.ValidationError(f"Utilisez {length} chiffres.")
        return value

    def clean_country_code(self):
        return country_from_name(self.cleaned_data["country_code"], self.instance.country_code if self.instance.pk else "")


FRENCH_LEGAL_FORMS = (
    ("EI", "Entreprise individuelle (EI, y compris micro-entreprise)"),
    ("EURL", "Entreprise unipersonnelle à responsabilité limitée (EURL)"),
    ("SARL", "Société à responsabilité limitée (SARL)"),
    ("SASU", "Société par actions simplifiée unipersonnelle (SASU)"),
    ("SAS", "Société par actions simplifiée (SAS)"),
    ("SA", "Société anonyme (SA)"),
    ("SNC", "Société en nom collectif (SNC)"),
    ("SCI", "Société civile immobilière (SCI)"),
    ("SCS", "Société en commandite simple (SCS)"),
    ("SCA", "Société en commandite par actions (SCA)"),
    ("Association", "Association"),
)
FRENCH_VAT_REGIMES = (
    ("Franchise en base", "Franchise en base de TVA"),
    ("Réel normal", "Régime réel normal (CA3)"),
)
SIMPLIFIED_VAT = ("Réel simplifié", "Régime réel simplifié (CA12, jusqu’en 2026)")


class SuggestedValueInput(forms.MultiWidget):
    use_fieldset = False
    custom = "__other__"

    def __init__(self, label, choices):
        self.predefined = {value for value, _ in choices}
        super().__init__(
            {
                "": forms.Select(
                    choices=[("", "Non renseigné"), *choices, (self.custom, "Autre (préciser)")],
                    attrs={"data-suggested-value": self.custom},
                ),
                "other": forms.TextInput(
                    attrs={
                        "aria-label": f"{label} — autre valeur",
                        "placeholder": "Précisez si vous choisissez Autre",
                        "data-other-value": "",
                    }
                ),
            }
        )

    def id_for_label(self, id_):
        return f"{id_}_0"

    def decompress(self, value):
        if not value or value in self.predefined:
            return [value or "", ""]
        return [self.custom, value]

    def value_from_datadict(self, data, files, name):
        selection, custom = super().value_from_datadict(data, files, name)
        return custom if selection == self.custom else selection


class CompanyForm(StyledModelForm):
    class Meta:
        model = Company
        fields = COMPANY_FIELDS
        widgets = {
            "has_vat_on_debits": forms.Select(
                choices=[("", "Non renseignée"), (0, "Non"), (1, "Oui")]
            )
        }

    def __init__(self, *args, field_names=None, **kwargs):
        super().__init__(*args, **kwargs)
        if "country_code" in self.fields:
            self.fields["country_code"].widget = CountryInput()
            self.fields["country_code"].label = "Pays d’établissement"
        if field_names is not None:
            self.fields = {name: self.fields[name] for name in field_names}
            is_french = (self.instance.country_code or "").upper() == "FR"
            vat_choices = FRENCH_VAT_REGIMES
            if today()[:4] <= "2026" or self.instance.vat_regime == SIMPLIFIED_VAT[0]:
                vat_choices = (vat_choices[0], SIMPLIFIED_VAT, vat_choices[1])
            if is_french:
                for name, choices in (("legal_form", FRENCH_LEGAL_FORMS), ("vat_regime", vat_choices)):
                    if name in self.fields:
                        field = self.fields[name]
                        field.widget = SuggestedValueInput(field.label, choices)
            help_texts = {
                "legal_form": (
                    "Choisissez la forme indiquée sur votre justificatif d’immatriculation."
                ),
                "vat_regime": (
                    "En France, consultez « Déclarer la TVA » dans votre espace professionnel sur impots.gouv.fr. "
                    "Vous pouvez laisser ce champ vide et le compléter plus tard."
                    if is_french else "Indiquez le régime applicable dans votre pays."
                ),
                "has_vat_on_debits": "Choisissez Oui si vous avez opté pour le paiement de la TVA d’après les débits.",
                "rcs_city": "Indiquez la ville du greffe si vous êtes immatriculé au RCS.",
            }
            if is_french:
                help_texts.update(
                    siren="Identifiant de l’entreprise en 9 chiffres. Il sert aussi à composer la mention RCS.",
                    siret="Identifiant de l’établissement en 14 chiffres.",
                    rcs_city="Si vous êtes immatriculé au RCS, la mention combine la ville du greffe et le SIREN.",
                )
                for name in ("siren", "siret"):
                    if name in self.fields:
                        self.fields[name].widget.attrs["inputmode"] = "numeric"
            for name, hint in help_texts.items():
                if name in self.fields:
                    self.fields[name].help_text = hint
        for name, autocomplete in {
            "legal_name": "organization",
            "address_line1": "address-line1",
            "address_line2": "address-line2",
            "city": "address-level2",
            "postal_code": "postal-code",
            "country_code": "country",
            "billing_email": "email",
            "support_email": "email",
            "phone": "tel",
            "website": "url",
        }.items():
            if name in self.fields:
                self.fields[name].widget.attrs["autocomplete"] = autocomplete

    def _normalize(self, field_name, validator, **options):
        try:
            return validator(self.cleaned_data[field_name], **options)
        except BusinessError as exc:
            raise forms.ValidationError(str(exc)) from exc

    def clean_country_code(self):
        return country_from_name(self.cleaned_data["country_code"], self.instance.country_code if self.instance.pk else "")

    def clean_billing_email(self):
        return self._normalize("billing_email", email, optional=True)

    def clean_support_email(self):
        return self._normalize("support_email", email, optional=True)

    def clean_has_vat_on_debits(self):
        value = self.cleaned_data["has_vat_on_debits"]
        if value not in (None, 0, 1):
            raise forms.ValidationError("Choisissez Oui ou Non.")
        return value

    def clean(self):
        cleaned = super().clean()
        for name, field in self.fields.items():
            if (
                isinstance(field.widget, SuggestedValueInput)
                and self.data.get(self.add_prefix(name)) == SuggestedValueInput.custom
                and not cleaned.get(name)
            ):
                self.add_error(name, "Précisez la valeur ou choisissez « Non renseigné ».")
        for name in (
            "quote_number_prefix",
            "invoice_number_prefix",
            "credit_note_number_prefix",
        ):
            if name in cleaned:
                value = cleaned[name]
                if len(value) > 20 or not value.replace("-", "").isalnum():
                    self.add_error(name, "Utilisez jusqu’à 20 lettres, chiffres ou tirets.")
        return cleaned


class InventoryForm(StyledModelForm):
    class Meta:
        model = InventoryItem
        fields = "reference name description item_type stock_tracking reorder_level unit_code unit_price_excluding_tax vat_rate".split()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stock_tracking"].widget = forms.Select(
            choices=[(0, "Non"), (1, "Oui")]
        )


class AccountForm(StyledModelForm):
    class Meta:
        model = BankAccount
        fields = "name bank_name iban currency_code opening_balance".split()


class ForecastForm(StyledModelForm):
    class Meta:
        model = Forecast
        fields = "label due_date amount direction".split()
