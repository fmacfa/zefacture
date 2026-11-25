from django import forms
from .common import BusinessError, email, today
from .models import Company, Customer, InventoryItem, BankAccount, Forecast
from .services.documents import CUSTOMER_FIELDS, COMPANY_FIELDS, INVOICE_SELLER_FIELDS
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
    "collection_fee_terms": "Indemnité pour frais de recouvrement",
    "late_payment_terms": "Pénalités de retard",
    "early_payment_terms": "Conditions d’escompte",
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
    "unit_price_including_tax": "Prix unitaire TTC",
    "vat_category_code": "Catégorie de TVA",
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
                    if name in {"description", "payment_terms", "invoice_footer", "collection_fee_terms",
                                "late_payment_terms", "early_payment_terms"}
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
    ("SC", "Société civile (SC)"),
    ("GIE", "Groupement d’intérêt économique (GIE)"),
    ("SELARL", "Société d’exercice libéral à responsabilité limitée (SELARL)"),
    ("SELARLU", "SELARL unipersonnelle (SELARLU)"),
    ("SELAS", "Société d’exercice libéral par actions simplifiée (SELAS)"),
    ("SELASU", "SELAS unipersonnelle (SELASU)"),
    ("SARL de famille", "SARL de famille"),
    ("EIRL", "Entreprise individuelle à responsabilité limitée (EIRL existante)"),
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
                "electronic_address": "Adresse de facturation de l’émetteur inscrite dans l’annuaire de sa plateforme.",
                "electronic_address_scheme": "Code de l’annuaire, par exemple 0225 pour une adresse française.",
                "early_payment_terms": "Indiquez l’escompte applicable ou son absence.",
                "late_payment_terms": "Indiquez les conditions et le taux prévus dans vos conditions de vente.",
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
        if self.is_bound:
            for name in INVOICE_SELLER_FIELDS:
                if name in self.fields and self.add_prefix(name) not in self.data:
                    # Older open forms did not include these fields.
                    self.fields[name].disabled = True
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
        if "electronic_address" in cleaned and "electronic_address_scheme" in cleaned:
            address, scheme = cleaned["electronic_address"], cleaned["electronic_address_scheme"]
            if bool(address) != bool(scheme):
                field = "electronic_address_scheme" if address else "electronic_address"
                self.add_error(field, "Renseignez ensemble l’adresse et le schéma de facturation.")
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
        fields = "reference name description item_type stock_tracking reorder_level unit_code unit_price_excluding_tax unit_price_including_tax currency_code vat_rate vat_category_code".split()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .services.branding import SUPPORTED_CURRENCIES
        self.fields["currency_code"].widget = forms.Select(choices=[("", "Devise du document"), *[(c,c) for c in sorted(SUPPORTED_CURRENCIES)]])
        from .services.calculations import VAT_CATEGORIES
        self.fields["unit_price_excluding_tax"].required = False
        self.fields["unit_price_including_tax"].help_text = "Si vous saisissez le TTC, le prix HT est calculé avec le taux de TVA."
        self.fields["vat_category_code"].widget = forms.Select(choices=[("", "Selon le taux"), *VAT_CATEGORIES])
        self.fields["item_type"].widget = forms.Select(choices=[("product", "Produit"), ("service", "Service")])
        self.fields["stock_tracking"].widget = forms.Select(
            choices=[(0, "Non"), (1, "Oui")]
        )

    def clean(self):
        cleaned = super().clean()
        from .services.calculations import unit_prices, VAT_CATEGORIES
        from .common import BusinessError
        from .services.branding import SUPPORTED_CURRENCIES
        if cleaned.get("currency_code") and cleaned["currency_code"] not in SUPPORTED_CURRENCIES:
            self.add_error("currency_code", "Devise non prise en charge.")
        rate = cleaned.get("vat_rate")
        if rate is None or not 0 <= rate <= 100:
            self.add_error("vat_rate", "Choisissez un taux compris entre 0 et 100 %.")
            return cleaned
        category = cleaned.get("vat_category_code") or ("S" if rate else "Z")
        if category not in dict(VAT_CATEGORIES) or (category == "S") != bool(rate):
            self.add_error("vat_category_code", "La catégorie de TVA doit correspondre au taux.")
        if all(cleaned.get(field) is None for field in ("unit_price_excluding_tax", "unit_price_including_tax")):
            self.add_error("unit_price_excluding_tax", "Saisissez un prix HT ou TTC.")
            return cleaned
        try:
            price, gross = unit_prices(cleaned, rate)
            if price < 0:
                raise BusinessError("Le prix doit être positif ou nul.")
            cleaned.update(unit_price_excluding_tax=price, unit_price_including_tax=gross, vat_category_code=category)
        except BusinessError as exc:
            self.add_error("unit_price_excluding_tax", str(exc))
        return cleaned


class AccountForm(StyledModelForm):
    class Meta:
        model = BankAccount
        fields = "name bank_name iban currency_code opening_balance".split()


class ForecastForm(StyledModelForm):
    class Meta:
        model = Forecast
        fields = "label due_date amount direction".split()
