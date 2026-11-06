import time
from urllib.parse import urlsplit
from django.contrib import auth, messages
from django.conf import settings
from django.db import transaction
from django.shortcuts import redirect, render
from core.common import BusinessError
from core.models import User, UserSecurity, AuditEvent
from core.security import select_company, throttle, scoped, ROLES
from core.services import account_security as service


def challenge(request):
    pending = request.session.get("mfa_login", {})
    user = User.objects.filter(
        user_uid=pending.get("user_uid", ""), is_active=True
    ).first()
    if (
        not user
        or pending.get("version") != user.session_version
        or not 0 <= time.time() - pending.get("created", 0) < 300
    ):
        request.session.pop("mfa_login", None)
        return redirect("/connexion")
    error = ""
    if request.method == "POST":
        try:
            throttle(request, "mfa:" + user.user_uid, limit=5, seconds=300, by_ip=False)
            if not service.verify_code(user, request.POST.get("code")):
                raise BusinessError("Code incorrect ou déjà utilisé.")
            from .authentication import start_session

            request.session.pop("mfa_login", None)
            start_session(request, user)
            service.audit(request, "security.login_mfa")
            return redirect("/app")
        except BusinessError as exc:
            error = str(exc)
    return render(
        request,
        "core/mfa_challenge.html",
        {"title": "Vérifier la connexion", "error": error},
    )


def verify(request):
    if not request.user.is_authenticated:
        return redirect("/connexion")
    select_company(request)
    destination = request.GET.get("next", "/app")
    try:
        parsed = urlsplit(destination)
    except ValueError:
        destination = "/app"
        parsed = urlsplit(destination)
    if (
        parsed.scheme
        or parsed.netloc
        or not destination.startswith("/app")
        or "\\" in destination
    ):
        destination = "/app"
    error = ""
    if request.method == "POST":
        try:
            throttle(
                request,
                "reauth:" + request.user.user_uid,
                limit=5,
                seconds=300,
                by_ip=False,
            )
            valid = auth.authenticate(
                request,
                username=request.user.email,
                password=request.POST.get("password"),
            )
            if not valid or (
                service.enabled(valid)
                and not service.verify_code(valid, request.POST.get("code"))
            ):
                raise BusinessError("Vérification incorrecte.")
            request.session["verified_at"] = time.time()
            service.audit(request, "security.reauthenticated")
            return redirect(destination)
        except BusinessError as exc:
            error = str(exc)
    return render(
        request,
        "core/reauth.html",
        {
            "title": "Confirmer votre identité",
            "has_mfa": service.enabled(request.user),
            "error": error,
        },
    )


def security(request):
    if not request.user.is_authenticated:
        return redirect("/connexion")
    select_company(request)
    error, codes = "", []
    active = service.enabled(request.user)
    if request.method == "POST":
        try:
            throttle(
                request,
                "security-settings:" + request.user.user_uid,
                limit=10,
                seconds=900,
            )
            action = request.POST.get("action")
            if action == "activate":
                if not service.recent(request):
                    return redirect(service.verification_url(request))
                codes = service.activate(request, request.POST.get("code", ""))
            else:
                valid = auth.authenticate(
                    request,
                    username=request.user.email,
                    password=request.POST.get("password"),
                )
                if not valid:
                    raise BusinessError("Mot de passe incorrect.")
                if active and not service.verify_code(valid, request.POST.get("code")):
                    raise BusinessError("Code incorrect ou déjà utilisé.")
                request.session["verified_at"] = time.time()
                if action == "prepare" and not active:
                    service.prepare_setup(request)
                elif action in {"disable", "recovery"} and active:
                    if (
                        action == "disable"
                        and settings.MFA_REQUIRED_FOR_ADMINS
                        and getattr(request, "role", "") in ROLES["admin"]
                    ):
                        raise BusinessError(
                            "La double authentification est obligatoire pour les administrateurs de cette instance."
                        )
                    with transaction.atomic():
                        row = UserSecurity.objects.select_for_update().get(
                            user_uid=request.user.user_uid
                        )
                        if action == "disable":
                            (
                                row.totp_secret_encrypted,
                                row.recovery_hashes,
                                row.enabled_at,
                            ) = "", [], None
                        else:
                            codes, row.recovery_hashes = service.new_recovery_codes()
                        row.save()
                        service.invalidate_other_sessions(request)
                        service.audit(
                            request,
                            "security.mfa_disabled"
                            if action == "disable"
                            else "security.recovery_rotated",
                        )
                    messages.success(request, "Paramètres de sécurité mis à jour.")
                else:
                    raise BusinessError("Action indisponible.")
            active = service.enabled(request.user)
        except BusinessError as exc:
            error = str(exc)
    return render(
        request,
        "core/account_security.html",
        {
            "title": "Sécurité du compte",
            "error": error,
            "mfa_enabled": active,
            "setup": service.setup_details(request) if not active else None,
            "recovery_codes": codes,
            "mfa_required": settings.MFA_REQUIRED_FOR_ADMINS
            and getattr(request, "role", "") in ROLES["admin"],
            "events": AuditEvent.objects.filter(
                user_uid=request.user.user_uid, action__startswith="security."
            ).order_by("-id")[:20],
        },
    )


@scoped("admin")
def audit_log(request):
    return render(
        request,
        "core/audit.html",
        {
            "title": "Journal de sécurité",
            "events": AuditEvent.objects.filter(
                company_uid=request.company.company_uid
            ).order_by("-id")[:200],
        },
    )
