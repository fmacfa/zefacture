"""Seed only the disposable CI browser instance, through a local HTTP provider."""

import json
import hashlib
import os
from django.conf import settings
from core.models import Company, Document, PdpInvoice, PdpOutbox, PdpSession, User
from core.services.files import encrypt
from core.services.pdp_sync import sync_company
from tests.pdp_provider import (
    Provider,
    invoice,
    event,
    FIRST_INVOICE_ID,
    FIRST_EVENT_ID,
)

if (
    settings.ENVIRONMENT != "testing"
    or User.objects.count() != 1
    or not User.objects.filter(email="demo@example.test").exists()
):
    raise RuntimeError("Disposable synthetic browser instance required")
user = User.objects.get(email="demo@example.test")
company = Company.objects.get(owner_user_uid=user.user_uid, siren="123456789")
if PdpInvoice.objects.exists() or PdpOutbox.objects.exists():
    raise RuntimeError("Refusing to replace existing provider data")
provider = Provider()
try:
    os.environ["SUPERPDP_TEST_BASE_URL"] = provider.origin
    PdpSession.objects.create(
        company_uid=company.company_uid,
        api_base=provider.origin,
        client_id="synthetic-client",
        client_secret_encrypted=encrypt("synthetic-secret"),
    )
    xml = b'<?xml version="1.0"?><Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"/>'
    provider.add(invoice(number="FOURNISSEUR-SUPERPDP-001"), xml, "application/xml")
    provider.add(invoice(FIRST_INVOICE_ID + 1, "out", "EMISE-SUPERPDP-002"))
    for i, code in enumerate(["fr:202", "fr:205", "fr:212"]):
        item = event(identifier=FIRST_EVENT_ID + i, code=code)
        provider.events[item["id"]] = item
    sync_company(company.company_uid, force=True)
    links = list(PdpInvoice.objects.order_by("remote_id"))
    outgoing = Document.objects.get(document_uid=links[1].document_uid)
    PdpOutbox.objects.create(
        company_uid=company.company_uid,
        document_uid=outgoing.document_uid,
        request_key="synthetic-uncertain",
        operation="invoice",
        remote_company_id=links[1].remote_company_id,
        remote_environment="production",
        status="uncertain",
        payload={},
        last_error="Résultat fictif à vérifier avant reprise",
    )
    print(
        json.dumps(
            {
                "synthetic_data_only": True,
                "incoming_uid": links[0].document_uid,
                "outgoing_uid": links[1].document_uid,
                "incoming_original_sha256": hashlib.sha256(xml).hexdigest(),
                "remote_id": links[0].remote_id,
            }
        )
    )
finally:
    provider.close()
