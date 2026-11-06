import hashlib
import json
import secrets
from datetime import datetime, timezone, date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone as django_timezone


def uid():
    return secrets.token_hex(16)


def stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def today():
    return django_timezone.localdate().isoformat()


def digest(value):
    return hashlib.sha256(
        value.encode() if isinstance(value, str) else value
    ).hexdigest()


def json_text(value):
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def unpack(value):
    try:
        return json.loads(value or "{}")
    except (ValueError, TypeError):
        return {}


class BusinessError(Exception):
    def __init__(self, message, status=422):
        self.status = status
        super().__init__(message)


def decimal(value, default="0"):
    try:
        result = Decimal(
            str(value if value not in (None, "") else default).replace(",", ".")
        )
        if not result.is_finite() or abs(result) > Decimal("100000000"):
            raise ValueError()
        return result.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise BusinessError("Valeur numérique invalide ou trop élevée.")


def money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def iso_date(value, optional=False):
    if not value and optional:
        return None
    try:
        result = date.fromisoformat(str(value))
        if result.isoformat() != str(value):
            raise ValueError()
        return result.isoformat()
    except (ValueError, TypeError):
        raise BusinessError("Date invalide, format attendu : AAAA-MM-JJ.")


def email(value, optional=False):
    result = str(value or "").strip().lower()
    if optional and not result:
        return ""
    try:
        validate_email(result)
    except ValidationError:
        raise BusinessError("Adresse email invalide.")
    return result


def country(value):
    from django_countries_data import COUNTRY_CODES

    result = str(value or "").strip().upper()
    if result not in COUNTRY_CODES:
        raise BusinessError("Code pays ISO invalide.")
    return result
