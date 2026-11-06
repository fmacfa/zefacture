"""Browser and reverse-proxy boundaries, without trusting client forwarding headers."""

import hmac
import ipaddress
import secrets
from django.conf import settings


class ProxyBoundaryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        supplied = request.META.pop("HTTP_X_ZEFACTURE_PROXY_KEY", "")
        trusted = bool(
            settings.TRUST_PROXY and settings.PROXY_SHARED_SECRET
        ) and hmac.compare_digest(
            supplied.encode(), settings.PROXY_SHARED_SECRET.encode()
        )
        client = request.META.pop("HTTP_X_ZEFACTURE_CLIENT_IP", "")
        if not trusted:
            request.META.pop("HTTP_X_FORWARDED_PROTO", None)
        else:
            try:
                request.META["REMOTE_ADDR"] = str(ipaddress.ip_address(client))
            except ValueError:
                pass
        return self.get_response(request)


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = secrets.token_urlsafe(24)
        response = self.get_response(request)
        response["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; "
            f"style-src 'self' 'nonce-{request.csp_nonce}'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )
        # HTML form navigations can send Origin:null under no-referrer. Preserve
        # same-origin CSRF checks; callbacks never render a submitting form.
        response["Referrer-Policy"] = (
            "no-referrer"
            if request.path.endswith(("/retour", "/callback"))
            else "same-origin"
        )
        response["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response["Cross-Origin-Opener-Policy"] = "same-origin"
        if request.path.startswith(
            (
                "/app",
                "/api/",
                "/connexion",
                "/inscription",
                "/mot-de-passe",
                "/invitation/",
                "/devis/",
            )
        ):
            response["Cache-Control"] = "private, no-store"
        return response
