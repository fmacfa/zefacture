"""Resolve once and pin connections, with explicit opt-in for private services."""

import ipaddress
import socket
from urllib.parse import urlsplit
import urllib3
from django.conf import settings
from core.common import BusinessError


def resolve_host(host, port):
    allowed = host in getattr(settings, "OUTBOUND_ALLOWED_HOSTS", [])
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = sorted(
            {record[4][0] for record in records},
            key=lambda value: (ipaddress.ip_address(value).version, value),
        )
        if not addresses or (
            not allowed
            and any(
                not ipaddress.ip_address(address).is_global for address in addresses
            )
        ):
            raise BusinessError(
                "Cette destination réseau est privée ou réservée. L'administrateur du serveur doit l'autoriser explicitement."
            )
        return addresses[0]
    except (ValueError, OSError) as exc:
        raise BusinessError("Le nom du serveur ne peut pas être résolu.", 502) from exc


def checked_url(url):
    try:
        parsed = urlsplit(str(url))
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or len(url) > 2000
        ):
            raise ValueError()
        if any(ord(c) < 33 for c in url):
            raise ValueError()
        port = parsed.port or 443
        if not 1 <= port <= 65535:
            raise ValueError()
        return parsed
    except ValueError as exc:
        raise BusinessError(
            "Utilisez une URL HTTPS complète, sans identifiants ni fragment."
        ) from exc


def https_request(url, method="POST", body=None, headers=None, max_bytes=1_000_000):
    if settings.EXTERNAL_SERVICES_DISABLED:
        raise BusinessError(
            "Les services externes sont désactivés sur cette instance.", 503
        )
    parsed = checked_url(url)
    address = resolve_host(parsed.hostname, parsed.port or 443)
    request_headers = dict(headers or {})
    request_headers["Host"] = parsed.netloc
    try:
        with urllib3.HTTPSConnectionPool(
            address,
            port=parsed.port or 443,
            server_hostname=parsed.hostname,
            assert_hostname=parsed.hostname,
            cert_reqs="CERT_REQUIRED",
        ) as pool:
            response = pool.urlopen(
                method,
                (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""),
                body=body,
                headers=request_headers,
                redirect=False,
                retries=False,
                timeout=urllib3.Timeout(connect=5, read=20),
                preload_content=False,
            )
            try:
                raw = response.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raise BusinessError("Réponse du serveur trop volumineuse.", 502)
                return response.status, raw
            finally:
                response.close()
    except (urllib3.exceptions.HTTPError, OSError) as exc:
        raise BusinessError("Connexion au serveur distant impossible.", 502) from exc
