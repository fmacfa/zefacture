import ipaddress
import json
import os
import socket
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse
import urllib3
from django.conf import settings
from django.db import transaction
from core.common import BusinessError, stamp, json_text
from core.models import Company, PdpSession
from .files import encrypt, decrypt, read_verified
from .documents import event


def base(value):
    value = value.rstrip("/")
    if value == "https://api.superpdp.tech":
        return value
    test = os.getenv("SUPERPDP_TEST_BASE_URL", "")
    parsed = urlparse(value)
    if (
        settings.ENVIRONMENT == "testing"
        and value == test
        and parsed.scheme == "http"
        and parsed.hostname == "127.0.0.1"
        and parsed.port
        and not parsed.path
        and not parsed.username
    ):
        return value
    raise BusinessError("L'API doit utiliser https://api.superpdp.tech.")


class PdpHttpError(BusinessError):
    def __init__(self, http_status):
        self.http_status = http_status
        super().__init__(f"SUPERPDP a répondu HTTP {http_status}.", 502)


def identifier(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise BusinessError("Identifiant SUPERPDP invalide.", 502)
    text = str(value)
    if (
        not text.isascii()
        or not text.isdigit()
        or not 0 < int(text) <= 9223372036854775807
    ):
        raise BusinessError("Identifiant SUPERPDP invalide.", 502)
    return str(int(text))


def request(
    session, method, path, token="", form=None, body=None, headers=None, binary=False
):
    if settings.EXTERNAL_SERVICES_DISABLED:
        raise BusinessError(
            "Les services externes sont désactivés sur cette instance.", 503
        )
    origin = base(session.api_base)
    parsed = urlparse(origin)
    headers = dict(headers or {})
    headers["Accept"] = (
        "application/pdf, application/xml, text/xml" if binary else "application/json"
    )
    headers["Host"] = parsed.netloc
    if token:
        headers["Authorization"] = "Bearer " + token
    if form is not None:
        body = urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if not path.startswith("/") or path.startswith("//"):
        raise BusinessError("Chemin PDP invalide.")
    try:
        if parsed.scheme == "https":
            ips = sorted(
                {
                    record[4][0]
                    for record in socket.getaddrinfo(
                        parsed.hostname, 443, type=socket.SOCK_STREAM
                    )
                }
            )
            if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
                raise BusinessError("La destination PDP n'est pas publique.", 503)
            pool = urllib3.HTTPSConnectionPool(
                ips[0],
                port=443,
                server_hostname=parsed.hostname,
                assert_hostname=parsed.hostname,
                cert_reqs="CERT_REQUIRED",
            )
        else:
            pool = urllib3.HTTPConnectionPool(parsed.hostname, parsed.port)
        with pool:
            response = pool.urlopen(
                method,
                path,
                body=body,
                headers=headers,
                redirect=False,
                retries=False,
                timeout=urllib3.Timeout(connect=5, read=30),
                preload_content=False,
            )
            limit = settings.SUPERPDP_MAX_ORIGINAL_BYTES if binary else 2_000_000
            raw = response.read(limit + 1)
            status = response.status
            mime = (
                response.headers.get("Content-Type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            response.close()
        if len(raw) > limit:
            raise BusinessError("Réponse PDP trop volumineuse.", 502)
        if not 200 <= status < 300:
            raise PdpHttpError(status)
        if binary:
            return raw, mime
        data = json.loads(raw, parse_float=str)
        if not isinstance(data, (dict, list)):
            raise ValueError()
        return data
    except (urllib3.exceptions.HTTPError, OSError, ValueError) as exc:
        raise BusinessError("Échec de connexion ou réponse PDP invalide.", 502) from exc


def session_for(company):
    return PdpSession.objects.get_or_create(
        company_uid=company.company_uid, provider="superpdp"
    )[0]


def save_token(session, data):
    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        raise BusinessError("Réponse OAuth sans token.", 502)
    session.access_token_encrypted = encrypt(token)
    if data.get("refresh_token"):
        session.refresh_token_encrypted = encrypt(data["refresh_token"])
    session.token_expires_at = (
        datetime.now(timezone.utc)
        + timedelta(seconds=max(60, int(data.get("expires_in", 3600)) - 60))
    ).strftime("%Y-%m-%d %H:%M:%S")
    session.status = "configured"
    session.updated_at = stamp()
    session.save(
        update_fields=[
            "access_token_encrypted",
            "refresh_token_encrypted",
            "token_expires_at",
            "status",
            "updated_at",
        ]
    )
    return token


def access_token(session):
    # Serialize refresh-token rotation, including concurrent web/worker access.
    with transaction.atomic():
        current = PdpSession.objects.select_for_update().get(pk=session.pk)
        result = _access_token(current)
        session.refresh_from_db()
        return result


def _access_token(session):
    if session.access_token_encrypted and (
        not session.token_expires_at or session.token_expires_at > stamp()
    ):
        return decrypt(session.access_token_encrypted)
    if session.refresh_token_encrypted:
        from core.models import PdpSyncState

        state = PdpSyncState.objects.filter(company_uid=session.company_uid).first()
        technical = bool(session.client_id) and (
            not state or state.auth_mode != "oauth"
        )
        form = {
            "grant_type": "refresh_token",
            "refresh_token": decrypt(session.refresh_token_encrypted),
            "client_id": session.client_id
            if technical
            else os.getenv("SUPERPDP_OAUTH_CLIENT_ID", ""),
            "client_secret": decrypt(session.client_secret_encrypted)
            if technical
            else os.getenv("SUPERPDP_OAUTH_CLIENT_SECRET", ""),
        }
    else:
        form = {
            "grant_type": "client_credentials",
            "client_id": session.client_id or "",
            "client_secret": decrypt(session.client_secret_encrypted),
        }
    if not form["client_id"] or not form["client_secret"]:
        raise BusinessError("Connectez SUPERPDP ou configurez ses identifiants OAuth.")
    return save_token(session, request(session, "POST", "/oauth2/token", form=form))


def verify(company, session):
    token = access_token(session)
    status = request(session, "GET", "/v1.beta/oauth2_sessions/me", token)
    profile = request(session, "GET", "/v1.beta/companies/me", token)
    if not isinstance(profile, dict) or not isinstance(status, dict):
        raise BusinessError("Profil SUPERPDP invalide.", 502)
    remote_id = identifier(profile.get("id"))
    if (
        company.siren
        and profile.get("number_scheme") == "fr_siren"
        and profile.get("number") != company.siren
    ):
        raise BusinessError(
            "Le compte SUPERPDP ne correspond pas au SIREN de cette entreprise.", 409
        )
    from core.models import PdpSyncState

    binding = PdpSyncState.objects.filter(company_uid=company.company_uid).first()
    if (
        binding
        and binding.remote_company_id
        and (
            binding.remote_company_id != remote_id
            or binding.remote_environment != profile.get("env")
        )
    ):
        raise BusinessError(
            "Cette entreprise est déjà liée à un autre compte ou environnement SUPERPDP.",
            409,
        )
    for key in ("company_verification_status", "user_verification_status"):
        setattr(session, key, status.get(key, "needs_review"))
    session.pdp_user_id = status.get("user_id")
    session.pdp_company_id = remote_id
    session.pdp_company_number = profile.get("number")
    session.pdp_company_number_scheme = profile.get("number_scheme")
    session.pdp_company_env = profile.get("env")
    session.pdp_company_payload = json_text(profile)
    session.last_checked_at = stamp()
    session.user_verification_status = status.get(
        "user_identity_verification_status",
        status.get("user_verification_status", "needs_review"),
    )
    session.status = (
        "active" if session.company_verification_status == "verified" else "configured"
    )
    session.save(
        update_fields=[
            "company_verification_status",
            "user_verification_status",
            "pdp_user_id",
            "pdp_company_id",
            "pdp_company_number",
            "pdp_company_number_scheme",
            "pdp_company_env",
            "pdp_company_payload",
            "last_checked_at",
            "status",
        ]
    )
    # Freeze the exact token whose company was verified. A concurrent settings
    # edit must not switch a later upload/download to another account's token.
    session._verified_token = token
    company.pdp_company_id = session.pdp_company_id
    company.pdp_company_verification_status = session.company_verification_status
    company.save(update_fields=["pdp_company_id", "pdp_company_verification_status"])
    return profile


def send_invoice(document, file, processing_rule=""):
    company = Company.objects.get(company_uid=document.company_uid)
    session = session_for(company)
    if document.pdp_invoice_id and document.status in {"sent", "paid"}:
        return
    simulated = session.status == "sandbox" and settings.ENVIRONMENT in {
        "local",
        "testing",
        "development",
    }
    rule = processing_rule or (
        "B2C"
        if document.buyer.get("type") == "individual"
        else "B2BInt"
        if document.buyer.get("country_code", "FR") != "FR"
        else "B2B"
    )
    if rule not in {"B2B", "B2C", "B2BInt"}:
        raise BusinessError("Règle de traitement inconnue.")
    if simulated:
        provider_id = "sandbox-" + document.document_uid
    else:
        verify(company, session)
        if session.company_verification_status != "verified":
            raise BusinessError("L'entreprise SUPERPDP doit être vérifiée.", 403)
        data = request(
            session,
            "POST",
            "/v1.beta/invoices?"
            + urlencode(
                {"external_id": document.document_uid, "processing_rule": rule}
            ),
            session._verified_token,
            body=read_verified(file),
            headers={"Content-Type": "application/pdf"},
        )
        if not isinstance(data, dict):
            raise BusinessError("Réponse de dépôt SUPERPDP invalide.", 502)
        provider_id = identifier(data.get("id"))
        if "company_id" in data and identifier(data["company_id"]) != str(
            session.pdp_company_id
        ):
            raise BusinessError("Le dépôt concerne une autre entreprise SUPERPDP.", 409)
        if data.get("direction", "out") != "out":
            raise BusinessError("Direction du dépôt SUPERPDP incohérente.", 409)
    document.pdp_invoice_id, document.pdp_external_id, document.pdp_processing_rule = (
        provider_id,
        document.document_uid,
        rule,
    )
    document.status = "simulated" if simulated else "sent"
    document.sent_at = None if simulated else stamp()
    document.amount_due = (
        0
        if simulated or document.type == "credit_note"
        else document.total_including_tax
    )
    document.save()
    event(
        document,
        "simulated" if simulated else "pdp_sent",
        "Simulation locale" if simulated else "Facture envoyée via SUPERPDP",
        {"pdp_invoice_id": provider_id},
    )
    return provider_id


def directory(company, customer, mode):
    if settings.EXTERNAL_SERVICES_DISABLED:
        raise BusinessError(
            "Les annuaires externes sont désactivés sur cette instance.", 503
        )
    if mode == "vies":
        import requests

        vat = (customer.vat_number or "").replace(" ", "").upper()
        code = "EL" if customer.country_code == "GR" else customer.country_code
        if vat.startswith(code):
            vat = vat[2:]
        if not vat:
            raise BusinessError("Numéro de TVA requis.")
        with requests.Session() as client:
            client.trust_env = False
            response = client.post(
                "https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number",
                json={"countryCode": code, "vatNumber": vat},
                timeout=(5, 20),
                allow_redirects=False,
            )
        if response.status_code != 200:
            raise BusinessError("VIES indisponible.", 502)
        return response.json()
    session = session_for(company)
    number = (customer.siren or customer.siret or "")[:9]
    query = (
        {"number": number}
        if number
        else {
            "formal_name_starts_with": customer.legal_name,
            "post_code_starts_with": customer.postal_code or "",
        }
    )
    token = (
        access_token(session)
        if session.access_token_encrypted or session.client_id
        else ""
    )
    companies = request(
        session,
        "GET",
        "/v1.beta/french_directory/companies?" + urlencode(query | {"limit": 50}),
        token,
    )
    entries = (
        request(
            session,
            "GET",
            "/v1.beta/french_directory/entries?" + urlencode({"number": number}),
            token,
        )
        if number
        else {}
    )
    return {"companies": companies, "entries": entries}
