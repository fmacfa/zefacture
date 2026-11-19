"""Human-readable numbers. Monetary calculations and exchange formats use Decimal."""

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

GROUPS = {"none": "", "space": "\u202f", "dot": ".", "comma": ","}
DECIMALS = {"comma": ",", "dot": "."}


def numeric_columns(rows):
    """Identify columns containing quantities or amounts without interpreting text identifiers."""
    return {index for row in rows for index, value in enumerate(row) if type(value) in (Decimal, int, float)}


@dataclass(frozen=True)
class NumberStyle:
    grouping: str = "space"
    decimal: str = "comma"
    customized: bool = False

    @classmethod
    def for_preferences(cls, preferences):
        group = getattr(preferences, "number_grouping", "")
        decimal = getattr(preferences, "number_decimal", "")
        if group in GROUPS and decimal in DECIMALS and group != decimal:
            return cls(group, decimal, True)
        return cls()

    def format(self, value, precision=None):
        try:
            number = Decimal(str(value))
        except (ValueError, InvalidOperation):
            return ""
        if not number.is_finite() or abs(number.adjusted()) > 1000:
            return ""
        if precision is not None:
            places = abs(int(precision))
            if precision < 0 and number == number.to_integral_value():
                places = 0
            with localcontext() as ctx:
                ctx.prec = (
                    max(len(number.as_tuple().digits), number.adjusted() + 1, 1)
                    + places
                    + 2
                )
                number = number.quantize(
                    Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP
                )
        whole, _, fraction = format(number.copy_abs(), "f").partition(".")
        grouping = GROUPS[self.grouping]
        if grouping:
            whole = re.sub(r"(?<=\d)(?=(\d{3})+$)", grouping, whole)
        return (
            ("-" if number < 0 else "")
            + whole
            + (DECIMALS[self.decimal] + fraction if fraction else "")
        )

    def parse(self, value):
        """Return an exact canonical value for a user-formatted CSV cell."""
        raw = str(value).strip()
        group, point = GROUPS[self.grouping], DECIMALS[self.decimal]
        if self.grouping == "space":
            raw = re.sub(r"[ \u00a0\u202f]", group, raw)
        grouped = rf"|[0-9]{{1,3}}(?:{re.escape(group)}[0-9]{{3}})+" if group else ""
        if not re.fullmatch(
            rf"[+-]?(?:[0-9]+{grouped})(?:{re.escape(point)}[0-9]+)?", raw
        ):
            raise ValueError("Format numérique invalide.")
        return (raw.replace(group, "") if group else raw).replace(point, ".")


_default = NumberStyle()
_current = ContextVar("zefacture_number_style", default=_default)


def current_style():
    return _current.get()


def company_style(company, fallback):
    shared = getattr(company, "number_format", None)
    return NumberStyle.for_preferences(shared) if shared else fallback


@contextmanager
def number_context(style):
    token = _current.set(style)
    try:
        yield
    finally:
        _current.reset(token)


class NumberPreferencesMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith("/app") or not request.user.is_authenticated:
            return self.get_response(request)
        from core.models import UserPreferences
        from core.security import select_company

        request.display_preferences = UserPreferences.objects.filter(
            user_uid=request.user.user_uid
        ).first()
        active = select_company(request)
        style = company_style(active, NumberStyle.for_preferences(request.display_preferences))
        with number_context(style):
            return self.get_response(request)


def document_style(document):
    party = document.seller if document.direction == "outgoing" else document.buyer
    stored = party.get("number_display") if isinstance(party, dict) else None
    if (
        isinstance(stored, dict)
        and stored.get("group") in GROUPS
        and stored.get("decimal") in DECIMALS
        and stored["group"] != stored["decimal"]
    ):
        return NumberStyle(stored["group"], stored["decimal"], True)
    return current_style()


def document_amount(document, value):
    style = document_style(document)
    return style.format(value, 2) if style.customized else format(value, ".2f")
