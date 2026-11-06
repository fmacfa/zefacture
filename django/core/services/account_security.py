"""TOTP, single-use recovery codes, and recent-authentication checks."""

import base64
import hmac
import secrets
import time
from io import BytesIO
from urllib.parse import urlencode
import pyotp
import qrcode
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from core.common import BusinessError, digest
from core.models import AuditEvent, UserSecurity
from .files import encrypt, decrypt


def audit(request, action, target_uid=""):
    # No free-form payload, IP, provider response or secret is persisted here.
    return AuditEvent.objects.create(
        company_uid=getattr(getattr(request, "company", None), "company_uid", ""),
        user_uid=request.user.user_uid if request.user.is_authenticated else "",
        action=action,
        target_uid=target_uid,
    )


def enabled(user):
    return UserSecurity.objects.filter(
        user_uid=user.user_uid, enabled_at__isnull=False
    ).exists()


def recent(request):
    age = time.time() - request.session.get("verified_at", 0)
    return 0 <= age < 900


def verification_url(request):
    return "/app/verification?" + urlencode({"next": request.path})


def new_recovery_codes():
    codes = [secrets.token_hex(8) for _ in range(10)]
    return codes, [digest(code) for code in codes]


@transaction.atomic
def verify_code(user, code):
    row = (
        UserSecurity.objects.select_for_update()
        .filter(user_uid=user.user_uid, enabled_at__isnull=False)
        .first()
    )
    if not row:
        return False
    code = str(code or "").strip().replace(" ", "")
    if len(code) == 6 and code.isascii() and code.isdigit():
        totp = pyotp.TOTP(decrypt(row.totp_secret_encrypted))
        counter = int(time.time()) // totp.interval
        for current in (counter, counter - 1, counter + 1):
            if current > row.last_totp_counter and hmac.compare_digest(
                totp.at(current * totp.interval), code
            ):
                row.last_totp_counter = current
                row.save(update_fields=["last_totp_counter"])
                return True
    if len(code) == 16:
        hashed = digest(code.lower())
        for index, value in enumerate(row.recovery_hashes):
            if hmac.compare_digest(value, hashed):
                row.recovery_hashes.pop(index)
                row.save(update_fields=["recovery_hashes"])
                return True
    return False


def prepare_setup(request):
    secret = pyotp.random_base32()
    request.session["totp_setup"] = {
        "secret": encrypt(secret),
        "created": time.time(),
        "user_uid": request.user.user_uid,
    }


def setup_details(request):
    pending = request.session.get("totp_setup", {})
    if (
        pending.get("user_uid") != request.user.user_uid
        or not 0 <= time.time() - pending.get("created", 0) < 600
    ):
        request.session.pop("totp_setup", None)
        return None
    secret = decrypt(pending["secret"])
    uri = pyotp.TOTP(secret).provisioning_uri(
        name=request.user.email, issuer_name="ZeFacture"
    )
    out = BytesIO()
    qrcode.make(uri).save(out, format="PNG")
    return {
        "secret": secret,
        "qr": "data:image/png;base64," + base64.b64encode(out.getvalue()).decode(),
    }


@transaction.atomic
def activate(request, code):
    details = setup_details(request)
    if not details:
        raise BusinessError("Préparation expirée. Recommencez l’activation.")
    row, _ = UserSecurity.objects.get_or_create(user_uid=request.user.user_uid)
    row = UserSecurity.objects.select_for_update().get(pk=row.pk)
    if row.enabled_at:
        raise BusinessError("La double authentification est déjà activée.")
    totp = pyotp.TOTP(details["secret"])
    # Enrollment confirms possession; the code is consumed and cannot log in again.
    counter = int(time.time()) // totp.interval
    accepted = next(
        (
            c
            for c in (counter, counter - 1, counter + 1)
            if hmac.compare_digest(totp.at(c * totp.interval), str(code).strip())
        ),
        None,
    )
    if accepted is None:
        raise BusinessError("Code de l’application d’authentification incorrect.")
    codes, row.recovery_hashes = new_recovery_codes()
    row.totp_secret_encrypted = encrypt(details["secret"])
    row.enabled_at, row.last_totp_counter = timezone.now(), accepted
    row.save()
    request.session.pop("totp_setup", None)
    invalidate_other_sessions(request)
    audit(request, "security.mfa_enabled")
    return codes


def invalidate_other_sessions(request):
    from django.contrib import auth
    from core.models import User

    # The caller holds UserSecurity when changing MFA. SQL avoids lost increments.
    from django.db.models import F

    User.objects.filter(pk=request.user.pk).update(
        session_version=F("session_version") + 1
    )
    request.user.refresh_from_db()
    auth.update_session_auth_hash(request, request.user)
    request.session["session_version"] = request.user.session_version
    request.session["verified_at"] = time.time()


def admin_security_redirect(request, recent_required=True):
    if settings.MFA_REQUIRED_FOR_ADMINS and not enabled(request.user):
        return "/app/compte/securite"
    if recent_required and settings.SENSITIVE_REAUTH_REQUIRED and not recent(request):
        return verification_url(request)
    return ""
