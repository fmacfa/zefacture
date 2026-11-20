from django import template
from core.country_fields import CountryInput

register = template.Library()


@register.simple_tag
def country_input(field_id, value="FR"):
    return CountryInput().render(
        "country_code", value, attrs={"id": field_id, "required": True}
    )
