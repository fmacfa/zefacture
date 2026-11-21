from datetime import date, timedelta
from decimal import Decimal
from collections import defaultdict
import json
import re
from django.db import transaction
from core.common import (
    BusinessError,
    decimal,
    money,
    today,
    stamp,
    json_text,
    iso_date,
    email,
    country,
)
from core.models import (
    Company,
    Customer,
    Document,
    DocumentLine,
    InvoiceEvent,
    PdpSession,
)
from .calculations import calculate


CUSTOMER_FIELDS = "type legal_name trade_name local_registration siren siret vat_number email phone address_line1 address_line2 postal_code city country_code electronic_address electronic_address_scheme".split()
COMPANY_FIELDS = "legal_name trade_name siren siret vat_number address_line1 address_line2 postal_code city country_code billing_email support_email phone website legal_form share_capital rcs_city rcs_number ape_code bank_iban bank_bic payment_terms invoice_footer vat_regime has_vat_on_debits quote_number_prefix invoice_number_prefix credit_note_number_prefix".split()
INVOICE_TYPES = ("invoice", "progress_invoice")
ISSUED_INVOICE_STATUSES = {"sent", "paid", "accepted"}


def save_customer(company, data, customer=None):
    customer = customer or Customer(company_uid=company.company_uid)
    for field in CUSTOMER_FIELDS:
        if field in data:
            setattr(customer, field, str(data[field] or "").strip())
    if "delivery_address" in data:
        from .sale_details import address_from
        customer.delivery_address = address_from(data)
    if len(customer.local_registration) > 100:
        raise BusinessError("L’identifiant local est limité à 100 caractères.")
    if not customer.legal_name:
        raise BusinessError("Nom du client requis.")
    if customer.type not in ("business", "individual"):
        raise BusinessError("Type de client invalide.")
    customer.country_code = country(customer.country_code)
    customer.email = email(customer.email, optional=True)
    for name, length in (("siren", 9), ("siret", 14)):
        value = getattr(customer, name) or ""
        if value and (len(value) != length or not value.isdigit()):
            raise BusinessError(f"{name.upper()} invalide.")
    customer.updated_at = stamp()
    customer.save()
    return customer


def snapshot(party, seller=False):
    result = {
        k: getattr(party, k, "") or ""
        for k in "siren siret vat_number address_line1 address_line2 postal_code city country_code".split()
    }
    result.update(
        name=party.legal_name,
        email=getattr(party, "billing_email" if seller else "email") or "",
    )
    if seller:
        from .branding import snapshot as branding_snapshot

        result["branding"] = branding_snapshot(party)
        for key in "bank_iban bank_bic payment_terms invoice_footer vat_regime has_vat_on_debits legal_form share_capital rcs_city rcs_number".split():
            result[key] = getattr(party, key, "") or ""
        siren = "".join(result["siren"].split())
        if (
            result["country_code"].upper() == "FR"
            and result["rcs_city"]
            and re.fullmatch(r"[0-9]{9}", siren)
        ):
            result["rcs_number"] = siren
        session = PdpSession.objects.filter(company_uid=party.company_uid).first()
        for key in "pdp_company_id pdp_company_number pdp_company_number_scheme pdp_company_env".split():
            result[key] = getattr(session, key, "") or ""
    else:
        result.update(
            local_registration=party.local_registration,
            type=party.type,
            electronic_address=party.electronic_address or "",
            electronic_address_scheme=party.electronic_address_scheme or "",
        )
    return json_text(result)


def event(document, code, label, payload=None):
    return InvoiceEvent.objects.create(
        document_uid=document.document_uid,
        company_uid=document.company_uid,
        provider="local",
        event_code="local:" + code,
        event_label=label,
        direction=document.direction,
        payload=json_text(payload or {}),
        occurred_at=stamp(),
    )


def record_issuance(document, file, label):
    document.status, document.sent_at = "sent", stamp()
    document.updated_at = document.sent_at
    document.save()
    event(document, "sent", label, {"file_uid": file.file_uid})


@transaction.atomic
def record_handover(document):
    document = Document.objects.select_for_update().get(pk=document.pk)
    if document.direction != "outgoing" or document.type not in INVOICE_TYPES or document.status not in ISSUED_INVOICE_STATUSES:
        raise BusinessError("Seule une facture client émise peut être remise au client.", 409)
    existing = InvoiceEvent.objects.filter(company_uid=document.company_uid, document_uid=document.document_uid,
                                           event_code="local:customer_handover").first()
    return existing or event(document, "customer_handover", "Remise au client déclarée")


@transaction.atomic
def issue_invoice(document):
    from .files import generate_document, issued_invoice_pdf

    # Match the lock order used by invoice editing and SUPERPDP.
    company = Company.objects.select_for_update().get(company_uid=document.company_uid)
    invoice = (
        Document.objects.select_for_update()
        .filter(pk=document.pk, company_uid=document.company_uid)
        .first()
    )
    if (
        not invoice
        or invoice.direction != "outgoing"
        or invoice.type not in INVOICE_TYPES
    ):
        raise BusinessError("Facture sortante introuvable.", 404)
    if invoice.status in ISSUED_INVOICE_STATUSES:
        issued_invoice_pdf(invoice)
        return invoice
    if (
        invoice.status != "draft"
        or not invoice.customer_uid
        or invoice.pdp_invoice_id
    ):
        raise BusinessError("Seul un brouillon complet peut être émis par l’API.", 409)
    finalize_number(company, invoice)
    file = generate_document(invoice)
    record_issuance(invoice, file, "Facture émise par l’API")
    return invoice


def finalize_number(company, document):
    """Called inside the issuing transaction, under company and document locks."""
    if document.number not in (None, "", document.draft_reference):
        return
    document.number = number(company, document.type, issue_date=document.issue_date)
    document.save(update_fields=["number"])


def number(company, kind, *, issue_date=None):
    prefix_kind = {"quote": "quote", "credit_note": "credit_note"}.get(kind, "invoice")
    counter_field = "next_" + prefix_kind + "_number"
    next_value = getattr(company, counter_field)
    prefix = getattr(company, prefix_kind + "_number_prefix") or {
        "quote": "DE",
        "credit_note": "AV",
    }.get(kind, "FA")
    if issue_date:
        kinds = (kind,) if kind in {"credit_note", "quote"} else INVOICE_TYPES
        newer = Document.objects.filter(
            company_uid=company.company_uid, direction="outgoing", type__in=kinds,
            number__regex=rf"^{re.escape(prefix)}-[0-9]{{4}}-[0-9]+$",
            issue_date__gt=issue_date,
        ).exclude(status__in=["draft", "cancelled", "simulated"])
        if newer.exists():
            raise BusinessError(
                "Une facture a déjà été émise dans cette série avec une date ultérieure. "
                "Vérifiez la date d’émission du brouillon.", 409,
            )
    year = (issue_date or today())[:4]
    while True:
        result = f"{prefix}-{year}-{next_value:04d}"
        next_value += 1
        if not Document.objects.filter(
            company_uid=company.company_uid, direction="outgoing", number__iexact=result
        ).exists():
            break
    setattr(company, counter_field, next_value)
    company.save(update_fields=[counter_field])
    return result


@transaction.atomic
def save_document(
    company,
    user,
    kind,
    data,
    document=None,
    direction="outgoing",
    *,
    imported_sale=False,
):
    if kind not in {"quote", "invoice", "progress_invoice", "credit_note"}:
        raise BusinessError("Type de document invalide.")
    company = Company.objects.select_for_update().get(pk=company.pk)
    if document:
        document = Document.objects.select_for_update().get(
            pk=document.pk, company_uid=company.company_uid
        )
        if document.status != "draft" or document.direction != direction:
            raise BusinessError("Ce document est figé.", 409)
        if document.source_quote_uid:
            raise BusinessError(
                "Une facture issue d'un devis doit être annulée puis recréée pour changer ses montants.",
                409,
            )
    else:
        document = Document(
            company_uid=company.company_uid,
            type=kind,
            direction=direction,
            created_by_user_uid=user.user_uid,
        )
    customer = Customer.objects.filter(
        company_uid=company.company_uid,
        customer_uid=data.get("customer_uid"),
        status="active",
    ).first()
    if not customer and (kind != "quote" or data.get("customer_uid")):
        raise BusinessError(
            "Sélectionnez un client ou fournisseur actif de cette entreprise."
        )
    discount_type = data.get("global_discount_type", document.global_discount_type)
    discount_value = decimal(data.get("global_discount_value", document.global_discount_value))
    computed = calculate(data.get("lines", []), discount_type, discount_value)
    document.global_discount_type = discount_type
    document.global_discount_value = discount_value
    document.global_discount_amount = computed["allowance"]
    document.customer_uid = customer.customer_uid if customer else None
    if direction == "incoming":
        document.number = str(data.get("number", "")).strip()
        if not document.number:
            raise BusinessError("Numéro fournisseur requis.")
        if (
            Document.objects.filter(
                company_uid=company.company_uid, direction="incoming", customer_uid=customer.customer_uid, number__iexact=document.number
            )
            .exclude(pk=document.pk)
            .exists()
        ):
            raise BusinessError("Une facture porte déjà ce numéro pour ce fournisseur.", 409)
        document.seller_snapshot = snapshot(customer)
        document.buyer_snapshot = snapshot(company, seller=True)
        document.status = "received"
        document.received_at = stamp()
    else:
        if imported_sale:
            reference = str(data.get("number") or "").strip()
            if not reference or kind != "invoice" or document.pk:
                raise BusinessError(
                    "Le numéro de la facture de vente originale est requis."
                )
            if Document.objects.filter(
                company_uid=company.company_uid, direction="outgoing", number__iexact=reference
            ).exists():
                raise BusinessError(
                    "Une facture porte déjà ce numéro. Vérifiez le doublon dans les imports.",
                    409,
                )
            document.number, document.status = reference, "sent"
        else:
            if not document.number:
                document.number = document.draft_reference
        document.seller_snapshot = snapshot(company, seller=True)
        document.buyer_snapshot = snapshot(customer) if customer else "{}"
    if customer and any(
        data.get(key) for key in ("contact_name", "contact_email", "contact_phone")
    ):
        side = "seller_snapshot" if direction == "incoming" else "buyer_snapshot"
        party = json.loads(getattr(document, side))
        if data.get("contact_name"):
            party["contact_name"] = str(data["contact_name"]).strip()[:200]
        if data.get("contact_email"):
            party["contact_email"] = email(data["contact_email"])
        if data.get("contact_phone"):
            party["contact_phone"] = str(data["contact_phone"]).strip()[:80]
        setattr(document, side, json_text(party))
    from core.numbers import company_style, current_style

    style = company_style(company, current_style())
    if style.customized:
        side = "seller_snapshot" if direction == "outgoing" else "buyer_snapshot"
        snapshot_data = json.loads(getattr(document, side))
        snapshot_data["number_display"] = {
            "group": style.grouping,
            "decimal": style.decimal,
        }
        setattr(document, side, json_text(snapshot_data))
    document.issue_date = iso_date(data.get("issue_date") or today())
    from .sale_details import update_details
    update_details(document, data, customer)
    if customer and data.get("delivery_mode") == "billing" and any(t["category"] == "K" for t in computed["taxes"]):
        document.delivery_address = {"line1": customer.address_line1 or "", "line2": customer.address_line2 or "",
                                     "city": customer.city or "", "postal_code": customer.postal_code or "",
                                     "country_code": customer.country_code}
    from .branding import for_company

    brand = for_company(company)
    document.due_date = iso_date(
        data.get("due_date")
        or str(
            date.fromisoformat(document.issue_date)
            + timedelta(
                days=brand.quote_validity_days
                if kind == "quote"
                else brand.payment_due_days
            )
        )
    )
    if document.due_date < document.issue_date:
        raise BusinessError("L'échéance précède l'émission.")
    document.currency_code = str(
        data.get("currency_code") or brand.default_currency
    ).upper()
    if document.currency_code not in {
        "EUR",
        "USD",
        "GBP",
        "CHF",
        "CAD",
        "JPY",
        "SEK",
        "NOK",
        "DKK",
        "PLN",
    }:
        raise BusinessError("Devise non prise en charge.")
    for key in (
        "reference",
        "subject",
        "buyer_reference",
        "payment_terms",
        "payment_method_code",
        "notes",
    ):
        if key in data:
            setattr(document, key, str(data[key] or "").strip())
    document.payment_terms = (
        document.payment_terms or company.payment_terms or "Paiement à l'échéance."
    )
    document.payment_method_code = document.payment_method_code or "30"
    if kind == "credit_note":
        source = Document.objects.filter(
            document_uid=data.get("source_invoice_uid"),
            company_uid=company.company_uid,
            direction="outgoing",
            type__in=["invoice", "progress_invoice"],
            status__in=["sent", "paid"],
        ).first()
        if (
            not source
            or source.customer_uid != document.customer_uid
            or source.currency_code != document.currency_code
        ):
            raise BusinessError(
                "Sélectionnez la facture émise à corriger pour ce client et cette devise."
            )
        document.source_invoice_uid = source.document_uid
    document.total_excluding_tax, document.total_vat, document.total_including_tax = (
        computed["net"],
        computed["vat"],
        computed["payable"],
    )
    document.rounding_amount = computed["rounding"]
    document.amount_due = 0 if kind in {"quote", "credit_note"} else computed["payable"]
    new = document.pk is None
    document.updated_at = stamp()
    document.save()
    document.lines.delete()
    DocumentLine.objects.bulk_create(
        [
            DocumentLine(document_uid=document.document_uid, **item)
            for item in computed["lines"]
        ]
    )
    from .invoice_contacts import assign_automatically

    assign_automatically(document)
    event(
        document,
        "created" if new else "updated",
        "Document créé" if new else "Brouillon mis à jour",
    )
    return document


@transaction.atomic
def bill_quote(company, user, quote, percent=None):
    # Lock company before document consistently with save_document and all numbering.
    company = Company.objects.select_for_update().get(pk=company.pk)
    quote = Document.objects.select_for_update().get(
        pk=quote.pk, company_uid=company.company_uid
    )
    if quote.type != "quote" or quote.status != "accepted" or not quote.customer_uid:
        raise BusinessError("Acceptez le devis avant de le facturer.")
    previous = Document.objects.filter(
        company_uid=company.company_uid, source_quote_uid=quote.document_uid
    ).exclude(status="cancelled")
    final = previous.filter(type="invoice").first()
    if final:
        if percent is None:
            return final
        raise BusinessError("Une facture de solde existe déjà.", 409)
    already = sum((d.progress_percent or Decimal(0) for d in previous), Decimal(0))
    percent = decimal(percent) if percent is not None else None
    if already >= 100 or percent is not None and not 0 < percent <= 100 - already:
        raise BusinessError("Le pourcentage dépasse le solde facturable.")
    cumulative = Decimal(100) if percent is None else already + percent
    source = list(quote.lines.values())
    bases, billed = defaultdict(Decimal), defaultdict(Decimal)
    for item in source:
        bases[(item["vat_category_code"], item["vat_rate"])] += item[
            "total_excluding_tax"
        ]
    for line in DocumentLine.objects.filter(
        document_uid__in=previous.values("document_uid")
    ):
        billed[(line.vat_category_code, line.vat_rate)] += line.total_excluding_tax
    if not already and percent is None:
        lines = source
    else:
        lines = []
        for (category, rate), basis in bases.items():
            net = money(basis * cumulative / 100) - billed[(category, rate)]
            if net < 0:
                raise BusinessError("Les factures liées dépassent le devis.")
            if net:
                lines.append(
                    dict(
                        description=f"{'Solde' if percent is None else 'Avancement ' + str(percent) + ' %'} du devis {quote.number}",
                        quantity=1,
                        unit_price_excluding_tax=net,
                        vat_rate=rate,
                        vat_category_code=category,
                    )
                )
    discount_type = quote.global_discount_type if not already and percent is None else "percent"
    discount_value = quote.global_discount_value if not already and percent is None else 0
    computed = calculate(lines, discount_type, discount_value)
    previous_gross = sum((d.total_including_tax for d in previous), Decimal(0))
    gross = money(quote.total_including_tax * cumulative / 100) - previous_gross
    rounding = gross - computed["gross"]
    zero_final = percent is None and not already and not quote.total_including_tax
    if (gross <= 0 and not zero_final) or abs(rounding) > abs(quote.rounding_amount) + max(Decimal("0.02"), len(bases) * Decimal("0.02")):
        raise BusinessError("Solde trop faible ou montants historiques incohérents.")
    data = {
        key: getattr(quote, key)
        for key in "customer_uid currency_code subject buyer_reference payment_terms payment_method_code notes delivery_date delivery_address operation_category purchase_order".split()
    }
    data.update(
        lines=lines,
        global_discount_type=discount_type,
        global_discount_value=discount_value,
        reference=f"Devis {quote.number}",
        issue_date=today(),
        due_date=str(date.fromisoformat(today()) + timedelta(days=30)),
    )
    document = save_document(
        company, user, "invoice" if percent is None else "progress_invoice", data
    )
    document.source_quote_uid = quote.document_uid
    document.progress_percent = percent
    document.seller_snapshot, document.buyer_snapshot = (
        quote.seller_snapshot,
        quote.buyer_snapshot,
    )
    document.rounding_amount, document.total_including_tax, document.amount_due = (
        rounding,
        gross,
        gross,
    )
    document.save()
    event(
        document,
        "created_from_quote",
        "Facturation du devis " + quote.number,
        {"percent": percent},
    )
    return document


@transaction.atomic
def change_status(document, action):
    company = None
    if action == "accept" and document.type == "quote":
        company = Company.objects.select_for_update().get(company_uid=document.company_uid)
    document = Document.objects.select_for_update().get(pk=document.pk)
    if action == "accept" and document.type == "quote":
        if (
            document.status not in {"draft", "sent", "accepted"}
            or not document.customer_uid
        ):
            raise BusinessError("Ce devis ne peut pas être accepté.", 409)
        if document.status == "accepted":
            return document
        from .files import generate_document

        finalize_number(company, document)
        generate_document(document)
        document.status, document.accepted_at = "accepted", stamp()
    elif action == "cancel":
        if (
            document.direction != "outgoing"
            or document.status not in {"draft", "simulated"}
            and document.type != "quote"
        ):
            raise BusinessError(
                "Une facture émise doit être corrigée par un avoir.", 409
            )
        if (
            Document.objects.filter(source_quote_uid=document.document_uid)
            .exclude(status="cancelled")
            .exists()
        ):
            raise BusinessError("Ce devis possède des factures actives.", 409)
        document.status, document.amount_due = "cancelled", 0
    elif action == "pay":
        allowed = {"sent"} if document.direction == "outgoing" else {"accepted"}
        if document.type not in {
            "invoice",
            "progress_invoice",
        } or document.status not in allowed | {"paid"}:
            raise BusinessError("Ce document ne peut pas être payé.", 409)
        if document.status == "paid":
            return document
        if document.direction == "outgoing":
            from .sale_receipts import mark_balance_paid
            mark_balance_paid(document)
        else:
            document.status, document.amount_due, document.paid_at = "paid", 0, stamp()
    elif document.direction == "incoming" and action in {"accept", "refuse", "dispute"}:
        if document.status not in {"received", "accepted", "disputed"}:
            raise BusinessError("Transition fournisseur impossible.", 409)
        document.status = {
            "accept": "accepted",
            "refuse": "refused",
            "dispute": "disputed",
        }[action]
        if action == "accept":
            document.accepted_at = stamp()
        if action == "refuse":
            document.amount_due = 0
    else:
        raise BusinessError("Action impossible pour ce document.")
    document.updated_at = stamp()
    document.save()
    event(document, document.status, "Statut : " + document.status)
    return document


def payload(document):
    keys = "document_uid type direction status number customer_uid issue_date due_date currency_code reference subject buyer_reference payment_terms payment_method_code notes source_quote_uid source_invoice_uid progress_percent total_excluding_tax total_vat total_including_tax rounding_amount amount_due accepted_at created_at".split()
    result = {key: getattr(document, key) for key in keys}
    result.update(
        global_discount_type=document.global_discount_type, global_discount_value=document.global_discount_value,
        global_discount_amount=document.global_discount_amount,
        delivery_date=str(document.delivery_date) if document.delivery_date else None,
        purchase_order=document.purchase_order, operation_category=document.operation_category,
        delivery_address=document.delivery_address,
    )
    result["customer_name"] = document.buyer.get("name", "")
    result["lines"] = list(document.lines.values())
    result["files"] = list(
        document.files.values("file_uid", "kind", "mime_type", "sha256", "file_size")
    )
    return result
