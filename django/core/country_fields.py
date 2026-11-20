"""French country names in forms, ISO alpha-2 codes in records and APIs."""

import unicodedata
from functools import lru_cache

from django import forms
from django.utils import translation
from django_countries import countries


def normalized(value):
    return "".join(
        c for c in unicodedata.normalize("NFKD", str(value)).casefold() if c.isalnum()
    )


@lru_cache(maxsize=1)
def country_choices():
    with translation.override("fr"):
        return tuple((code, str(name)) for code, name in countries)


def country_code(value, existing=""):
    needle = normalized(value)
    for code, name in country_choices():
        if needle in (
            normalized(code),
            normalized(name),
            normalized(f"{name} ({code})"),
        ):
            return code
    if existing and str(value).strip().upper() == existing:
        return existing
    raise forms.ValidationError(
        "Code pays ISO invalide. Choisissez un pays dans les suggestions ou saisissez son code à deux lettres."
    )


class CountryInput(forms.TextInput):
    template_name = "core/country_input.html"

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        widget = context["widget"]
        widget["countries"] = country_choices()
        widget["attrs"].update(
            list=widget["attrs"]["id"] + "_countries",
            autocomplete="country-name",
            maxlength=160,
        )
        names = dict(country_choices())
        if value in names:
            widget["value"] = f"{names[value]} ({value})"
        return context


class CompanyCreationForm(forms.Form):
    legal_name = forms.CharField(label="Raison sociale", max_length=255)
    country_code = forms.CharField(
        label="Pays d’établissement", widget=CountryInput, initial="FR"
    )

    def clean_country_code(self):
        return country_code(self.cleaned_data["country_code"])
