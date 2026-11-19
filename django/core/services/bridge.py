"""Read-only Bridge aggregation client. Bearer tokens live for a single operation."""

import json
from decimal import Decimal
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

import requests
from django.conf import settings

from core.common import BusinessError
from .files import decrypt


ORIGIN = "https://api.bridgeapi.io"
PREFIX = "/v3/aggregation/"
VERSION = "2025-01-15"


class BridgeError(BusinessError):
    def __init__(self, message, provider_status=0):
        self.provider_status = provider_status
        super().__init__(message, 502)


class Bridge:
    def __init__(self, feed):
        if settings.EXTERNAL_SERVICES_DISABLED:
            raise BusinessError(
                "Les services externes sont désactivés sur cette instance.", 503
            )
        self.feed = feed
        self.token = ""

    def call(self, method, path, *, data=None, params=None):
        headers = {
            "Client-Id": self.feed.client_id,
            "Client-Secret": decrypt(self.feed.secret_encrypted),
            "Bridge-Version": VERSION,
            "Accept": "application/json",
        }
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        try:
            with requests.request(
                method,
                ORIGIN + path,
                headers=headers,
                json=data,
                params=params,
                allow_redirects=False,
                timeout=(5, 20),
                stream=True,
            ) as response:
                if response.status_code not in (200, 201):
                    if response.status_code in (401, 403):
                        raise BridgeError(
                            "Bridge a refusé l’accès. Vérifiez les identifiants et l’autorisation bancaire.",
                            response.status_code,
                        )
                    if response.status_code == 429:
                        raise BridgeError(
                            "Bridge limite temporairement les requêtes. Une nouvelle tentative est prévue.",
                            429,
                        )
                    raise BridgeError(
                        "Bridge n’a pas accepté la demande.", response.status_code
                    )
                raw = bytearray()
                for chunk in response.iter_content(65536):
                    raw.extend(chunk)
                    if len(raw) > 8 * 1024 * 1024:
                        raise BridgeError(
                            "La réponse bancaire dépasse la taille attendue."
                        )
                result = json.loads(raw, parse_float=Decimal)
                if not isinstance(result, dict):
                    raise ValueError
                return result
        except (ValueError, RecursionError, requests.RequestException) as exc:
            raise BridgeError("Réponse bancaire indisponible ou illisible.") from exc

    def authorize(self, *, provision=False):
        identity = {"external_user_id": self.feed.external_uid}
        if not self.feed.remote_user and provision:
            try:
                self.call("POST", PREFIX + "users", data=identity)
            except BridgeError as exc:
                # Recover a user created by a previous interrupted request.
                if exc.provider_status not in (400, 409):
                    raise
        token = self.call("POST", PREFIX + "authorization/token", data=identity)
        user = token.get("user") or {}
        try:
            remote = str(UUID(user["uuid"]))
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise BridgeError("Identité bancaire illisible.") from exc
        if (
            user.get("external_user_id") != self.feed.external_uid
            or self.feed.remote_user
            and remote != self.feed.remote_user
            or not isinstance(token.get("access_token"), str)
            or not token["access_token"]
        ):
            raise BridgeError(
                "L’identité bancaire ne correspond pas à cette entreprise."
            )
        self.token = token["access_token"]
        return remote

    def management_url(self, email, callback, nonce):
        session = self.call(
            "POST",
            PREFIX + "user-management-sessions",
            data={
                "user_email": email,
                "callback_url": callback,
                "context": nonce,
                "account_types": "payment",
            },
        )
        url = session.get("url")
        if not isinstance(url, str) or not url.startswith(
            "https://connect.bridgeapi.io/user/"
        ):
            raise BridgeError("Lien de connexion bancaire invalide.")
        return url

    def pages(self, resource, **filters):
        path = PREFIX + resource
        params = {"limit": 500, **filters}
        seen = set()
        while True:
            page = self.call("GET", path, params=params)
            records = page.get("resources")
            pagination = page.get("pagination")
            if not isinstance(records, list) or not isinstance(pagination, dict):
                raise BridgeError("Page de données bancaires incomplète.")
            yield records
            uri = pagination.get("next_uri")
            if uri in (None, "", "null"):
                return
            if not isinstance(uri, str) or len(uri) > 8192:
                raise BridgeError("Pagination bancaire invalide.")
            parts = urlsplit(uri)
            if (parts.scheme or parts.netloc) and (parts.scheme, parts.netloc) != (
                "https",
                "api.bridgeapi.io",
            ):
                raise BridgeError("Origine de pagination bancaire inattendue.")
            if (
                parts.path != PREFIX + resource
                or not parts.query
                or parts.fragment
                or uri in seen
            ):
                raise BridgeError("Pagination bancaire incohérente.")
            seen.add(uri)
            # Some Bridge cursors omit the original account and date filters.
            cursor = dict(parse_qsl(parts.query, max_num_fields=12))
            params = {**cursor, **filters}
