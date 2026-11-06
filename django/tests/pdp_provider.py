"""Local contract fixture derived from SUPERPDP OpenAPI 1.39.0.beta.

Only synthetic invoices and credentials. Implements actual HTTP pagination,
binary downloads, lifecycle POSTs and an acceptance-before-disconnect failure.
"""

import copy
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from pypdf import PdfWriter

COMPANY_ID = 9007199254740993
FIRST_INVOICE_ID = 9007199254741011
FIRST_EVENT_ID = 9007199254742011


def pdf_bytes():
    stream = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(stream)
    return stream.getvalue()


def party(name="Fournisseur Exemple", siren="987654321"):
    return {
        "name": name,
        "legal_registration_identifier": {"scheme": "0002", "value": siren},
        "postal_address": {
            "address_line1": "1 rue Exemple",
            "post_code": "75001",
            "city": "Paris",
            "country_code": "FR",
        },
        "electronic_address": {"scheme": "0002", "value": siren},
    }


def invoice(identifier=FIRST_INVOICE_ID, direction="in", number="FOURN-2026-001"):
    return {
        "id": identifier,
        "company_id": COMPANY_ID,
        "created_at": "2026-11-06T10:00:00Z",
        "direction": direction,
        "external_id": "",
        "processing_rule": "B2B",
        "en_invoice": {
            "number": number,
            "issue_date": "2026-11-06",
            "payment_due_date": "2026-12-06",
            "type_code": 380,
            "currency_code": "EUR",
            "process_control": {
                "business_process_type": "urn:peppol:bis:billing",
                "specification_identifier": "urn:cen.eu:en16931:2017",
            },
            "seller": party(),
            "buyer": party("Entreprise Démonstration SAS", "123456789"),
            "totals": {
                "sum_invoice_lines_amount": "100.00",
                "total_without_vat": "100.00",
                "total_vat_amount": {"value": "20.00", "currency_code": "EUR"},
                "total_with_vat": "120.00",
                "amount_due_for_payment": "120.00",
            },
            "vat_break_down": [
                {
                    "vat_category_code": "S",
                    "vat_category_rate": "20",
                    "vat_category_taxable_amount": "100.00",
                    "vat_category_tax_amount": "20.00",
                }
            ],
            "lines": [
                {
                    "identifier": "1",
                    "invoiced_quantity": "2",
                    "invoiced_quantity_code": "C62",
                    "net_amount": "100.00",
                    "price_details": {"item_net_price": "50.00"},
                    "item_information": {"name": "Prestation reçue"},
                    "vat_information": {
                        "invoiced_item_vat_category_code": "S",
                        "invoiced_item_vat_rate": "20",
                    },
                }
            ],
        },
    }


def from_document(document, identifier=FIRST_INVOICE_ID):
    result = invoice(identifier, "out", document.number)
    result["external_id"] = document.document_uid
    data = result["en_invoice"]
    data.update(
        issue_date=document.issue_date,
        payment_due_date=document.due_date,
        currency_code=document.currency_code,
        type_code=381 if document.type == "credit_note" else 380,
    )
    for role in ("seller", "buyer"):
        source = getattr(document, role)
        data[role] = party(source["name"], source.get("siren") or "")
    data["totals"] = {
        "sum_invoice_lines_amount": str(document.total_excluding_tax),
        "total_without_vat": str(document.total_excluding_tax),
        "total_with_vat": str(document.total_including_tax),
        "total_vat_amount": {"value": str(document.total_vat)},
        "amount_due_for_payment": str(document.total_including_tax),
    }
    data["lines"] = [
        {
            "identifier": str(line.position),
            "invoiced_quantity": str(line.quantity),
            "net_amount": str(line.total_excluding_tax),
            "price_details": {"item_net_price": str(line.unit_price_excluding_tax)},
            "item_information": {"name": line.description},
            "vat_information": {
                "invoiced_item_vat_category_code": line.vat_category_code,
                "invoiced_item_vat_rate": str(line.vat_rate),
            },
        }
        for line in document.lines
    ]
    return result


def event(invoice_id=FIRST_INVOICE_ID, identifier=FIRST_EVENT_ID, code="fr:202"):
    return {
        "id": identifier,
        "invoice_id": invoice_id,
        "created_at": "2026-11-06T10:01:00Z",
        "status_code": code,
        "status_text": "Événement de test",
    }


class Provider:
    def __init__(self):
        self.invoices = {}
        self.originals = {}
        self.events = {}
        self.requests = []
        self.page_size = 2
        self.failures = {}
        self.disconnect_after_accept = False
        self.posted_invoice = None
        self.company_id = COMPANY_ID
        self.environment = "production"
        self.verification = "verified"
        self.number = "123456789"
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond()

            def respond(self):
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                provider.requests.append(
                    (self.command, self.path, dict(self.headers), raw)
                )
                failure = provider.failures.get((self.command, self.path))
                if failure:
                    return self.reply(
                        failure, {"message": "synthetic-provider-secret-must-not-leak"}
                    )
                if parsed.path == "/oauth2/token" and self.command == "POST":
                    return self.reply(
                        200,
                        {
                            "access_token": "synthetic-access",
                            "refresh_token": "synthetic-refresh",
                            "expires_in": 3600,
                        },
                    )
                if not self.headers.get("Authorization", "").startswith("Bearer "):
                    return self.reply(401, {})
                if parsed.path == "/v1.beta/oauth2_sessions/me":
                    return self.reply(
                        200,
                        {
                            "client_id": "synthetic-client",
                            "company_verification_status": provider.verification,
                            "user_identity_verification_status": "verified",
                            "created_at": "2026-11-06T10:00:00Z",
                        },
                    )
                if parsed.path == "/v1.beta/companies/me":
                    return self.reply(
                        200,
                        {
                            "id": provider.company_id,
                            "env": provider.environment,
                            "number": provider.number,
                            "number_scheme": "fr_siren",
                        },
                    )
                if parsed.path == "/v1.beta/invoices" and self.command == "POST":
                    if (
                        self.headers.get_content_type() != "application/pdf"
                        or not raw.startswith(b"%PDF-")
                    ):
                        return self.reply(400, {})
                    if set(query) != {"external_id", "processing_rule"}:
                        return self.reply(400, {})
                    value = copy.deepcopy(
                        provider.posted_invoice or invoice(direction="out")
                    )
                    value["external_id"] = query["external_id"][0]
                    value["processing_rule"] = query["processing_rule"][0]
                    provider.invoices[value["id"]] = value
                    provider.originals[value["id"]] = (raw, "application/pdf")
                    if provider.disconnect_after_accept:
                        self.close_connection = True
                        return
                    return self.reply(200, value)
                if parsed.path == "/v1.beta/invoice_events" and self.command == "POST":
                    data = json.loads(raw)
                    if (
                        self.headers.get_content_type() != "application/json"
                        or not isinstance(data.get("invoice_id"), int)
                    ):
                        return self.reply(400, {})
                    value = event(
                        data["invoice_id"],
                        FIRST_EVENT_ID + len(provider.events),
                        data["status_code"],
                    )
                    if data.get("details"):
                        value["details"] = data["details"]
                    provider.events[value["id"]] = value
                    return self.reply(200, value)
                if (
                    parsed.path in {"/v1.beta/invoices", "/v1.beta/invoice_events"}
                    and self.command == "GET"
                ):
                    source = (
                        provider.invoices
                        if parsed.path.endswith("invoices")
                        else provider.events
                    )
                    cursor = int(query.get("starting_after_id", [0])[0])
                    rows = [
                        value for key, value in sorted(source.items()) if key > cursor
                    ]
                    limit = min(int(query.get("limit", [100])[0]), provider.page_size)
                    return self.reply(
                        200,
                        {
                            "data": rows[:limit],
                            "has_after": len(rows) > limit,
                            "has_before": bool(cursor),
                            "count": len(source),
                        },
                    )
                if parsed.path.startswith("/v1.beta/invoices/"):
                    try:
                        identifier = int(parsed.path.rsplit("/", 1)[1])
                        value = provider.invoices[identifier]
                    except (KeyError, ValueError):
                        return self.reply(404, {})
                    format = query.get("format", ["en16931"])[0]
                    if format == "original":
                        raw, mime = provider.originals[identifier]
                        return self.reply(200, raw, mime)
                    if format == "factur-x":
                        return self.reply(200, pdf_bytes(), "application/pdf")
                    return self.reply(200, value)
                return self.reply(404, {})

            def reply(self, status, data, mime="application/json"):
                raw = json.dumps(data).encode() if not isinstance(data, bytes) else data
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = "http://127.0.0.1:" + str(self.server.server_port)

    def add(self, value=None, raw=None, mime="application/pdf"):
        value = copy.deepcopy(value or invoice())
        self.invoices[value["id"]] = value
        self.originals[value["id"]] = (raw or pdf_bytes(), mime)
        return value

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
