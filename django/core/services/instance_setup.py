import secrets
from datetime import timedelta

from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from core.common import BusinessError, digest, email, stamp
from core.models import Company, CompanyMember, InstanceSetup, User


SETUP_CODE_LIFETIME = timedelta(days=7)
INSTANCE_ALREADY_CONFIGURED = (
    "Cette instance contient déjà un compte ou une entreprise."
)


def instance_is_empty():
    return not (User.objects.exists() or Company.objects.exists())


def pending_setup():
    return InstanceSetup.objects.filter(pk=1, completed_at__isnull=True)


def setup_available(setup_hash=None):
    pending = pending_setup().filter(expires_at__gt=timezone.now())
    if setup_hash is not None:
        pending = pending.filter(code_hash=setup_hash)
    return pending.exists() and instance_is_empty()


@sensitive_variables("code")
def issue_setup_code():
    if not instance_is_empty():
        raise BusinessError(INSTANCE_ALREADY_CONFIGURED, 409)
    InstanceSetup.objects.get_or_create(pk=1)
    code = secrets.token_urlsafe(32)
    with transaction.atomic():
        updated = pending_setup().update(
            code_hash=digest(code), expires_at=timezone.now() + SETUP_CODE_LIFETIME
        )
        if not updated or not instance_is_empty():
            raise BusinessError(INSTANCE_ALREADY_CONFIGURED, 409)
    return code


@sensitive_variables("password", "setup_hash")
def create_first_owner(*, address, name, company_name, password, setup_hash=None):
    if setup_hash is None:
        InstanceSetup.objects.get_or_create(pk=1)
    now = timezone.now()
    pending = pending_setup()
    if setup_hash is not None:
        pending = pending.filter(code_hash=setup_hash, expires_at__gt=now)
    with transaction.atomic():
        # Update first to serialize provisioning in both PostgreSQL and SQLite.
        # If validation or creation fails, the link remains usable.
        if not pending.update(code_hash="", expires_at=None, completed_at=now):
            if setup_hash is not None:
                raise BusinessError("Lien d’installation invalide ou expiré.", 403)
            raise BusinessError(INSTANCE_ALREADY_CONFIGURED, 409)
        if not instance_is_empty():
            raise BusinessError(INSTANCE_ALREADY_CONFIGURED, 409)
        user = User(
            email=email(address),
            name=name,
            is_staff=True,
            is_superuser=True,
        )
        validate_password(password, user)
        user.set_password(password)
        user.save()
        company = Company.objects.create(
            owner_user_uid=user.user_uid,
            legal_name=company_name,
            billing_email=user.email,
        )
        CompanyMember.objects.create(
            company_uid=company.company_uid,
            user_uid=user.user_uid,
            role="owner",
            joined_at=stamp(),
        )
    return user
