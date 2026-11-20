import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from django.conf import settings
from django.contrib import auth, messages
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_http_methods, require_POST
from core.common import BusinessError, email, stamp, digest
from core.forms import FirstConnectionForm
from core.models import User, Company, CompanyMember, PasswordReset
from core.security import throttle
from core.services import instance_setup
from core.services.account_security import lock_pending_invitation


INSTALLATION_SESSION_KEY = "installation_hash"


def start_session(request, user):
    auth.login(request, user, backend="core.security.LegacyPasswordBackend")
    request.session["session_version"] = user.session_version
    request.session["verified_at"] = time.time()
    request.session["session_started_at"] = time.time()


@transaction.atomic
def register(data, token=""):
    invitation = lock_pending_invitation(token) if token else None
    if token and not invitation:
        raise BusinessError("Invitation expirée ou révoquée.", 404)
    if not settings.REGISTRATION_OPEN and not invitation:
        raise BusinessError(
            "Les inscriptions publiques sont fermées. Demandez une invitation.", 403
        )
    address = email(data.get("email"))
    if invitation and invitation.email.lower() != address:
        raise BusinessError("Cette invitation concerne une autre adresse email.")
    if User.objects.filter(email__iexact=address).exists():
        raise BusinessError("Un compte utilise déjà cette adresse. Connectez-vous.")
    name = str(data.get("name", "")).strip()
    if not name or not data.get("terms"):
        raise BusinessError("Nom et acceptation des conditions requis.")
    password = data.get("password", "")
    if password != data.get("password_confirmation", password):
        raise BusinessError("Les mots de passe diffèrent.")
    user = User(email=address, name=name, terms_accepted_at=stamp())
    validate_password(password, user)
    user.set_password(password)
    user.save()
    if invitation:
        CompanyMember.objects.create(
            company_uid=invitation.company_uid,
            user_uid=user.user_uid,
            role=invitation.role,
            joined_at=stamp(),
        )
        invitation.status, invitation.accepted_at = "accepted", stamp()
        invitation.save()
    else:
        company = Company.objects.create(
            owner_user_uid=user.user_uid,
            legal_name=data.get("company_name") or name,
            billing_email=address,
        )
        CompanyMember.objects.create(
            company_uid=company.company_uid,
            user_uid=user.user_uid,
            role="owner",
            joined_at=stamp(),
        )
    return user


def authentication(request, mode="login", token=""):
    if (
        mode == "login"
        and request.method in {"GET", "HEAD"}
        and instance_setup.setup_available()
    ):
        return redirect("first_connection")
    error = ""
    retry_after = 0
    if request.method == "POST":
        try:
            throttle(request, mode)
            if mode == "login":
                throttle(
                    request,
                    "login-address:"
                    + digest(str(request.POST.get("email", "")).strip().lower()),
                    limit=20,
                    seconds=900,
                    by_ip=False,
                )
                user = auth.authenticate(
                    request,
                    username=request.POST.get("email"),
                    password=request.POST.get("password"),
                )
                if not user:
                    raise BusinessError("Email ou mot de passe incorrect.")
                from core.services.account_security import enabled

                if enabled(user):
                    request.session.cycle_key()
                    request.session["mfa_login"] = {
                        "user_uid": user.user_uid,
                        "version": user.session_version,
                        "created": time.time(),
                    }
                    return redirect("/connexion/verification")
                start_session(request, user)
                return redirect("/app")
            if mode == "register":
                user = register(
                    request.POST,
                    request.POST.get("invite") or request.GET.get("invite", ""),
                )
                start_session(request, user)
                return redirect("/app")
            if mode == "forgot":
                address = email(request.POST.get("email"))
                throttle(
                    request,
                    "forgot-address:" + digest(address),
                    limit=5,
                    seconds=3600,
                    by_ip=False,
                )
                user = User.objects.filter(
                    email__iexact=address, is_active=True
                ).first()
                if user:
                    from core.services.mail import queue

                    raw = secrets.token_hex(32)
                    PasswordReset.objects.create(
                        user_uid=user.user_uid,
                        email=address,
                        account_version=user.session_version,
                        token_hash=digest(raw),
                        expires_at=(
                            datetime.now(timezone.utc) + timedelta(hours=1)
                        ).strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    queue(
                        None,
                        None,
                        "password_reset",
                        address,
                        "Réinitialiser votre mot de passe",
                        settings.APP_URL + "/mot-de-passe-reinitialiser/" + raw,
                    )
                messages.success(
                    request,
                    "Si ce compte existe, un lien de réinitialisation a été préparé.",
                )
                return redirect("/connexion")
            if mode == "reset":
                with transaction.atomic():
                    resets = PasswordReset.objects.filter(
                        token_hash=digest(token), status="pending", expires_at__gt=stamp(),
                    )
                    user_uid = resets.values_list("user_uid", flat=True).first()
                    user = User.objects.select_for_update().filter(user_uid=user_uid, is_active=True).first()
                    if not user or not resets.select_for_update().filter(
                        user_uid=user.user_uid, account_version=user.session_version, email__iexact=user.email,
                    ).first():
                        raise BusinessError("Lien expiré ou déjà utilisé. Demandez un nouveau lien.")
                    password = request.POST.get("password", "")
                    if password != request.POST.get("password_confirmation"):
                        raise BusinessError("Les mots de passe diffèrent.")
                    validate_password(password, user)
                    user.set_password(password)
                    user.session_version += 1
                    user.save()
                    PasswordReset.objects.filter(
                        user_uid=user.user_uid, status="pending"
                    ).update(status="used", used_at=stamp())
                messages.success(
                    request, "Mot de passe remplacé. Vous pouvez vous connecter."
                )
                return redirect("/connexion")
        except BusinessError as exc:
            error = str(exc)
            retry_after = getattr(exc, "retry_after", 0)
        except ValidationError as exc:
            error = " ".join(exc.messages)
    title = {
        "login": "Connexion",
        "register": "Créer un compte",
        "forgot": "Mot de passe oublié",
        "reset": "Nouveau mot de passe",
    }[mode]
    response = render(
        request,
        "core/auth.html",
        dict(
            title=title,
            mode=mode,
            error=error,
            invite=request.GET.get("invite", ""),
            registration_open=settings.REGISTRATION_OPEN,
        ),
        status=429 if retry_after else 200,
    )
    if retry_after:
        response["Retry-After"] = str(retry_after)
    return response


@sensitive_post_parameters("token")
@require_http_methods(["GET", "HEAD", "POST"])
@sensitive_variables("token", "setup_hash")
def authorize_first_connection(request):
    if request.method != "POST":
        setup_hash = request.session.get(INSTALLATION_SESSION_KEY)
        authorized = setup_hash and instance_setup.setup_available(setup_hash)
        return HttpResponse(status=204 if authorized else 403)

    if not instance_setup.setup_available():
        return HttpResponse(status=403)
    try:
        throttle(request, "first-connection-link", limit=10, seconds=900)
    except BusinessError as exc:
        return HttpResponse(status=exc.status)
    token = request.POST.get("token", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return HttpResponse(status=403)
    setup_hash = digest(token)
    if not instance_setup.setup_available(setup_hash):
        return HttpResponse(status=403)
    request.session.cycle_key()
    request.session[INSTALLATION_SESSION_KEY] = setup_hash
    return HttpResponse(status=204)


@sensitive_post_parameters("password")
@require_http_methods(["GET", "HEAD", "POST"])
@sensitive_variables("form", "data", "setup_hash")
def first_connection(request):
    if not instance_setup.setup_available():
        return redirect("/connexion")
    setup_hash = request.session.get(INSTALLATION_SESSION_KEY)
    authorized = bool(setup_hash and instance_setup.setup_available(setup_hash))
    submitting = request.method == "POST"
    form = FirstConnectionForm(request.POST if submitting and authorized else None)
    status = 403 if submitting and not authorized else 200
    if submitting and authorized:
        try:
            throttle(request, "first-connection", limit=10, seconds=900)
            if form.is_valid():
                data = form.cleaned_data
                user = instance_setup.create_first_owner(
                    address=data["email"],
                    name=data["name"],
                    company_name=data["company_name"],
                    password=data["password"],
                    setup_hash=setup_hash,
                )
                request.session.pop(INSTALLATION_SESSION_KEY, None)
                start_session(request, user)
                return redirect(
                    "/app/compte/securite"
                    if settings.MFA_REQUIRED_FOR_ADMINS
                    else "/app"
                )
            status = 422
        except BusinessError as exc:
            form.add_error(None, str(exc))
            status = exc.status
        except ValidationError as exc:
            form.add_error("password", exc)
            status = 422
    return render(
        request,
        "core/first_connection.html",
        {"form": form, "authorized": authorized},
        status=status,
    )


@require_POST
def logout(request):
    auth.logout(request)
    return redirect("/connexion")


@transaction.atomic
def account(request):
    if not request.user.is_authenticated:
        return redirect("/connexion")
    from core.security import select_company

    select_company(request)
    error = ""
    retry_after = 0
    if request.method == "POST":
        try:
            throttle(request, "account:" + request.user.user_uid, limit=10, seconds=900)
            user = User.objects.select_for_update().get(pk=request.user.pk)
            if user.session_version != request.user.session_version:
                raise BusinessError("Vos identifiants ont changé. Reconnectez-vous.", 409)
            verified = auth.authenticate(
                request,
                username=user.email,
                password=request.POST.get("current_password"),
            )
            if not verified:
                raise BusinessError("Mot de passe actuel incorrect.")
            from core.services.account_security import enabled, verify_code, audit

            if enabled(verified) and not verify_code(
                verified, request.POST.get("code")
            ):
                raise BusinessError(
                    "Code d’authentification incorrect ou déjà utilisé."
                )
            user = verified
            address = email(request.POST.get("email"))
            if User.objects.exclude(pk=user.pk).filter(email__iexact=address).exists():
                raise BusinessError("Adresse email déjà utilisée.")
            name = request.POST.get("name", "").strip()
            if not name:
                raise BusinessError("Nom requis.")
            password = request.POST.get("password")
            if password:
                validate_password(password, user)
                if password != request.POST.get("password_confirmation"):
                    raise BusinessError("Les mots de passe diffèrent.")
                user.set_password(password)
            if password or address.casefold() != user.email.casefold():
                PasswordReset.objects.filter(user_uid=user.user_uid, status="pending").update(status="cancelled")
            user.email, user.name, user.updated_at = address, name, stamp()
            user.session_version += 1
            user.save()
            auth.update_session_auth_hash(request, user)
            request.session["session_version"] = user.session_version
            audit(request, "security.account_updated")
            messages.success(
                request, "Compte mis à jour ; les autres sessions ont été fermées."
            )
            return redirect("/app/compte")
        except BusinessError as exc:
            error = str(exc)
            retry_after = getattr(exc, "retry_after", 0)
        except ValidationError as exc:
            error = " ".join(exc.messages)
    from core.services.account_security import enabled

    response = render(
        request,
        "core/account.html",
        {"title": "Mon compte", "error": error, "has_mfa": enabled(request.user)},
        status=429 if retry_after else 200,
    )
    if retry_after:
        response["Retry-After"] = str(retry_after)
    return response
