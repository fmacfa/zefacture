from decimal import Decimal

from django import template
from django.template.defaultfilters import floatformat

from core.numbers import current_style

register = template.Library()


@register.filter
def display_number(value, precision=None):
    style = current_style()
    if precision is not None:
        if not style.customized:
            return floatformat(value, precision)
        return style.format(value, int(str(precision).rstrip("g")))
    if not isinstance(value, (int, float, Decimal)) or isinstance(value, bool):
        return value
    return style.format(value) if style.customized else value
