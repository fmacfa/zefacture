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


@register.filter
def expense_amount(value):
    """Keep sub-cent values on original receipts while displaying at least two places."""
    if value is None or value == "":
        return ""
    precision = max(2, -Decimal(str(value)).normalize().as_tuple().exponent)
    return display_number(value, f"{precision}g")
