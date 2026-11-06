import functools
import json
import time
from hashlib import sha256

from django.contrib import auth, messages
from django.contrib.auth.backends import ModelBackend
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.middleware.csrf import CsrfViewMiddleware
from django.db import transaction

from .common import BusinessError
from .models import Company, CompanyMember, RateLimit, User


class LegacyPasswordBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        email = str(username or kwargs.get("email", "")).strip().lower()
        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            User().set_password(password or "")
            return None
        encoded = user.password
        valid = False
        try:
            if encoded.startswith(("$2y$", "$2b$", "$2a$")):
                import bcrypt

                valid = bcrypt.checkpw((password or "").encode()[:72], encoded.encode())
            elif encoded.startswith("$argon2"):
                from argon2 import PasswordHasher

                valid = PasswordHasher().verify(encoded, password or "")
            else:
                valid = user.check_password(password or "")
        except (ValueError, TypeError):
            valid = False
        except Exception as exc:
            if exc.__class__.__module__.startswith("argon2"):
                valid = False
            else:
                raise
        if valid and self.user_can_authenticate(user):
            if encoded.startswith("$"):
                user.set_password(password)
                user.save(update_fields=["password"])
            return user
        return None


class LegacyCsrfFieldMiddleware:
    """Accept the historical form name while retaining Django's token/origin checks."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.method == "POST"
            and "_csrf" in request.POST
            and "csrfmiddlewaretoken" not in request.POST
        ):
            request.POST = request.POST.copy()
            request.POST["csrfmiddlewaretoken"] = request.POST["_csrf"]
        return self.get_response(request)


class SessionVersionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            from django.conf import settings

            started = request.session.get("session_started_at")
            if started is None:
                request.session["session_started_at"] = time.time()
            elif not 0 <= time.time() - started < settings.SESSION_COOKIE_AGE:
                auth.logout(request)
                return self.get_response(request)
            version = request.session.get("session_version")
            if version is not None and version != request.user.session_version:
                auth.logout(request)
        return self.get_response(request)


ROLES = {
    "read": {"owner", "admin", "accountant", "member", "viewer"},
    "write": {"owner", "admin", "accountant", "member"},
    "finance": {"owner", "admin", "accountant"},
    "admin": {"owner", "admin"},
}


def select_company(request):
    members = CompanyMember.objects.filter(
        user_uid=request.user.user_uid, status="active"
    )
    member = (
        members.filter(company_uid=request.session.get("company_uid", "")).first()
        or members.order_by("id").first()
    )
    if not member:
        return None
    request.company = Company.objects.filter(
        company_uid=member.company_uid, status="active"
    ).first()
    request.role = member.role
    if request.company:
        request.session["company_uid"] = member.company_uid
    return request.company


def scoped(permission="read"):
    def decorate(view):
        @functools.wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("/connexion")
            if not select_company(request):
                return redirect("/app/entreprises")
            from django.conf import settings

            if settings.MFA_REQUIRED_FOR_ADMINS and request.role in ROLES["admin"]:
                from .services.account_security import enabled

                if not enabled(request.user):
                    return redirect("/app/compte/securite")
            needed = (
                permission
                if request.method != "GET" or permission in ("finance", "admin")
                else "read"
            )
            if request.role not in ROLES[needed]:
                return render(
                    request,
                    "core/error.html",
                    {"error": "Votre rôle ne permet pas cette action."},
                    status=403,
                )
            if needed == "admin":
                from core.services.account_security import admin_security_redirect

                destination = admin_security_redirect(
                    request, recent_required=kwargs.get("action") != "retour"
                )
                if destination:
                    return redirect(destination)
            try:
                return view(request, *args, **kwargs)
            except BusinessError as exc:
                if request.headers.get("Accept") == "application/json":
                    return JsonResponse({"error": str(exc)}, status=exc.status)
                return render(
                    request, "core/error.html", {"error": str(exc)}, status=exc.status
                )

        return wrapped

    return decorate


def navigation(request):
    context = {
        "company": getattr(request, "company", None),
        "role": getattr(request, "role", ""),
        "can_write": getattr(request, "role", "") in ROLES["write"],
        "can_finance": getattr(request, "role", "") in ROLES["finance"],
        "can_admin": getattr(request, "role", "") in ROLES["admin"],
        "csp_nonce": getattr(request, "csp_nonce", ""),
    }
    if request.user.is_authenticated:
        from .models import UserPreferences
        from .services.branding import for_company, palette

        context["preferences"] = UserPreferences.objects.filter(
            user_uid=request.user.user_uid
        ).first()
        if getattr(request, "company", None):
            brand = for_company(request.company)
            context.update(branding=brand, palette=palette(brand.accent_color))
    return context


@transaction.atomic
def throttle(request, scope, limit=10, seconds=900, by_ip=True):
    # REMOTE_ADDR deliberately ignores untrusted client forwarding headers.
    bucket = sha256(
        f"django:{scope}:{request.META.get('REMOTE_ADDR', '') if by_ip else 'subject'}".encode()
    ).hexdigest()
    row, _ = RateLimit.objects.get_or_create(
        bucket=bucket, defaults={"attempts": "[]", "expires_at": 0}
    )
    row = RateLimit.objects.select_for_update().get(pk=row.pk)
    now = int(time.time())
    attempts = [t for t in json.loads(row.attempts or "[]") if t > now - seconds]
    if len(attempts) >= limit:
        raise BusinessError("Trop de tentatives. Réessayez plus tard.", 429)
    row.attempts = json.dumps(attempts + [now])
    row.expires_at = now + seconds
    row.save()


def scoped_get(model, request, **lookup):
    obj = model.objects.filter(
        company_uid=request.company.company_uid, **lookup
    ).first()
    if not obj:
        raise BusinessError("Élément introuvable.", 404)
    if (
        model._meta.db_table == "documents"
        and obj.document_uid in hidden_expense_documents(request)
    ):
        raise BusinessError("Élément introuvable.", 404)
    return obj


def hidden_expense_documents(request):
    if getattr(request, "role", "") in ROLES["finance"]:
        return []
    from .models import Ingestion

    return (
        Ingestion.objects.filter(
            company_uid=request.company.company_uid,
            source_kind__in=["expense_note", "expense", "expense_receipt", "receipt"],
        )
        .exclude(uploaded_by_user_uid=request.user.user_uid)
        .exclude(document_uid__isnull=True)
        .values_list("document_uid", flat=True)
    )
