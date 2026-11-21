from django import template
from core.country_fields import CountryInput

register = template.Library()


@register.simple_tag
def country_input(field_id, value="FR", name="country_code", required=True):
    return CountryInput().render(
        name, value, attrs={"id": field_id, "required": required}
    )
