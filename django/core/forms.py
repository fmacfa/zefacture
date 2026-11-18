from django import forms
from .common import BusinessError, country, email
from .models import Company, Customer, InventoryItem, BankAccount, Forecast
from .services.documents import CUSTOMER_FIELDS, COMPANY_FIELDS

LABELS = {
    "legal_name": "Nom / raison sociale",
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
    "rcs_city": "Ville RCS",
    "rcs_number": "Numéro RCS",
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
    "stock_tracking": "Suivre le stock (0 ou 1)",
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
    class Meta:
        model = Customer
        fields = CUSTOMER_FIELDS


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
        if field_names is not None:
            self.fields = {name: self.fields[name] for name in field_names}
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
        return self._normalize("country_code", country)

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


class AccountForm(StyledModelForm):
    class Meta:
        model = BankAccount
        fields = "name bank_name iban currency_code opening_balance".split()


class ForecastForm(StyledModelForm):
    class Meta:
        model = Forecast
        fields = "label due_date amount direction".split()
