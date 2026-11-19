import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.db.models import Case, F, Q, Value, When
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.common import BusinessError, iso_date, stamp, uid
from core.models import (
    BankAccount,
    BankFeed,
    BankFeedAccount,
    BankFeedEntry,
    BankTransaction,
    Company,
    Document,
    PdpInvoice,
)
from .bridge import Bridge, BridgeError
from .finance import reconcile


INTERVAL = timedelta(hours=4)
LEASE = timedelta(minutes=3)


def identifier(value):
    result = str(value)
    if not re.fullmatch(r"[0-9]{1,40}", result):
        raise BridgeError("Identifiant bancaire invalide.")
    return result


def numeric(value):
    try:
        amount = Decimal(str(value))
        if (
            not amount.is_finite()
            or abs(amount) >= Decimal("1e21")
            or amount != amount.quantize(Decimal(".00000001"))
        ):
            raise ValueError
        return amount
    except (ValueError, InvalidOperation) as exc:
        raise BridgeError("Montant bancaire invalide.") from exc


def account_data(row):
    if not isinstance(row, dict):
        raise BridgeError("Compte bancaire illisible.")
    remote_id = identifier(row.get("id"))
    if row.get("data_access") == "disabled":
        return remote_id, None
    if row.get("data_access") != "enabled":
        raise BridgeError("Droit d’accès bancaire indéterminé.")
    currency = row.get("currency_code")
    if not isinstance(currency, str) or not re.fullmatch("[A-Z]{3}", currency):
        raise BridgeError("Devise bancaire invalide.")
    balance = row.get("accounting_balance")
    return remote_id, {
        "name": str(row.get("name") or "Compte bancaire")[:200],
        "iban": "".join(str(row.get("iban") or "").split()).upper(),
        "currency_code": currency,
        "current_balance": numeric(
            balance if balance is not None else row.get("balance")
        ),
    }


def guard(feed_id, lease_id):
    feed = BankFeed.objects.select_for_update().get(pk=feed_id)
    if feed.lease != lease_id or not feed.enabled:
        raise BusinessError("La synchronisation bancaire a été interrompue.", 409)
    feed.lease_until = timezone.now() + LEASE
    feed.save(update_fields=["lease_until"])
    return feed


@transaction.atomic
def update_accounts(feed_id, lease_id, rows):
    feed = guard(feed_id, lease_id)
    all_links = BankFeedAccount.objects.filter(feed=feed)
    enabled_ids = [k for k, v in rows.items() if v is not None]
    all_links.exclude(remote_id__in=enabled_ids).update(shared=False)
    result = []
    for remote_id, values in rows.items():
        link = all_links.filter(remote_id=remote_id).select_related("account").first()
        if values is None:
            if link:
                all_links.filter(pk=link.pk).update(shared=False)
            continue
        if not link:
            # The same IBAN and currency are required to attach to a pre-existing ledger.
            candidates = []
            if values["iban"]:
                accounts = BankAccount.objects.select_for_update(of=("self",)).filter(
                    company_uid=feed.company_uid,
                    status="active",
                    currency_code=values["currency_code"],
                )
                candidates = [
                    a
                    for a in accounts.filter(
                        Q(bank_feed__isnull=True) | Q(bank_feed__shared=False)
                    ).select_related("bank_feed")
                    if "".join((a.iban or "").split()).upper() == values["iban"]
                ]
            if len(candidates) > 1:
                raise BusinessError(
                    "Plusieurs comptes locaux partagent le même IBAN et la même devise. Vérifiez les fiches de comptes."
                )
            account = (
                candidates[0]
                if candidates
                else BankAccount.objects.create(
                    company_uid=feed.company_uid,
                    name=values["name"],
                    iban=values["iban"],
                    currency_code=values["currency_code"],
                )
            )
            link = getattr(account, "bank_feed", None)
            if link:
                # Reconsent can give the same physical account a new provider id.
                if (
                    sum(
                        1
                        for item in rows.values()
                        if item
                        and (item["iban"], item["currency_code"])
                        == (values["iban"], values["currency_code"])
                    )
                    != 1
                ):
                    raise BusinessError(
                        "Plusieurs comptes autorisés correspondent au même ancien compte. Vérifiez les autorisations dans Bridge."
                    )
                BankFeedEntry.objects.filter(feed_account=link).update(
                    retired_identity=True
                )
                link.remote_id = remote_id
                link.save(update_fields=["remote_id"])
            else:
                link = BankFeedAccount.objects.create(
                    feed=feed, remote_id=remote_id, account=account
                )
        account = BankAccount.objects.select_for_update().get(
            pk=link.account_id, company_uid=feed.company_uid
        )
        if account.currency_code != values["currency_code"]:
            raise BusinessError(
                "La devise du compte connecté a changé. Vérifiez le compte."
            )
        if not link.shared:
            link.shared = True
            link.cursor = None
            link.save(update_fields=["shared", "cursor"])
        if account.status == "active":
            # Bridge's balance already includes the movements being imported.
            account.current_balance = values["current_balance"]
            account.connected_provider, account.last_synced_at = "bridge", stamp()
            account.save(
                update_fields=[
                    "current_balance",
                    "connected_provider",
                    "last_synced_at",
                ]
            )
            result.append(link)
    return result


def entry_data(row, link):
    if (
        not isinstance(row, dict)
        or identifier(row.get("account_id")) != link.remote_id
        or row.get("currency_code") != link.account.currency_code
    ):
        raise BridgeError(
            "Opération bancaire reçue pour un autre compte ou une autre devise."
        )
    if not all(isinstance(row.get(key), bool) for key in ("future", "deleted")):
        raise BridgeError("État de l’opération bancaire indéterminé.")
    changed = parse_datetime(str(row.get("updated_at") or ""))
    if not changed or timezone.is_naive(changed):
        raise BridgeError("Date d’actualisation bancaire invalide.")
    return identifier(row.get("id")), {
        "day": date.fromisoformat(iso_date(row.get("booking_date") or row.get("date"))),
        "changed_at": changed,
        "amount": numeric(row.get("amount")),
        "label": str(
            row.get("provider_description")
            or row.get("clean_description")
            or "Opération bancaire"
        )[:2000],
        "bank_reference": str(row.get("provider_external_id") or "")[:256],
        "status": "cancelled"
        if row["deleted"]
        else "pending"
        if row["future"]
        else "booked",
    }


def candidates(entry):
    return BankTransaction.objects.filter(
        company_uid=entry.feed_account.feed.company_uid,
        account_uid=entry.feed_account.account.account_uid,
        transaction_date=entry.day.isoformat(),
        amount=abs(entry.amount),
        direction="incoming" if entry.amount > 0 else "outgoing",
        status="booked",
    ).filter(Q(bank_feed__isnull=True) | Q(bank_feed__retired_identity=True))


def adopt_transaction(entry, tx):
    retired = (
        BankFeedEntry.objects.select_for_update()
        .filter(transaction=tx, retired_identity=True)
        .first()
    )
    if retired:
        entry.previous_invoice_status = retired.previous_invoice_status
        retired.transaction = None
        retired.review = ""
        retired.save(update_fields=["transaction", "review"])
    entry.transaction, entry.review = tx, ""
    entry.save(update_fields=["transaction", "review", "previous_invoice_status"])


def ledger_values(entry):
    return {
        "transaction_date": entry.day.isoformat(),
        "amount": abs(entry.amount),
        "direction": "incoming" if entry.amount > 0 else "outgoing",
        "label": entry.label,
        "status": entry.status,
    }


def project_entry(entry, *, distinct=False):
    values = ledger_values(entry)
    if not entry.transaction_id and entry.bank_reference and not distinct:
        # Preserve the original ledger/payment when the bank rekeys an operation, even on a correction.
        by_reference = list(
            BankFeedEntry.objects.filter(
                feed_account=entry.feed_account,
                retired_identity=True,
                bank_reference=entry.bank_reference,
                transaction__isnull=False,
            ).select_related("transaction")[:2]
        )
        if len(by_reference) == 1:
            adopt_transaction(entry, by_reference[0].transaction)
    tx = (
        BankTransaction.objects.select_for_update()
        .filter(pk=entry.transaction_id)
        .first()
        if entry.transaction_id
        else None
    )
    if tx:
        changed_payment = (tx.amount, tx.direction, tx.status) != (
            values["amount"],
            values["direction"],
            values["status"],
        )
        if tx.document_uid and changed_payment:
            entry.review = "correction"
        else:
            entry.review = ""
            # Preserve categories, accounting references and invoice links.
            for key, value in values.items():
                setattr(tx, key, value)
            tx.updated_at = stamp()
            tx.save(update_fields=[*values, "updated_at"])
    elif entry.status == "booked" and entry.amount:
        matches = candidates(entry)
        if not distinct and matches.exists():
            matches = matches.select_for_update(of=("self",))
            exact = (
                list(matches.filter(external_id=entry.bank_reference)[:2])
                if entry.bank_reference
                else []
            )
            if not exact:
                exact = list(matches.filter(label__iexact=entry.label)[:2])
            if len(exact) == 1:
                adopt_transaction(entry, exact[0])
            else:
                entry.review = "overlap"
        else:
            entry.transaction = BankTransaction.objects.create(
                company_uid=entry.feed_account.feed.company_uid,
                account_uid=entry.feed_account.account.account_uid,
                category="other",
                imported_at=stamp(),
                external_id=entry.bank_reference or None,
                **values,
            )
            entry.review = ""
    else:
        entry.review = ""
    entry.save(update_fields=["transaction", "review"])


@transaction.atomic
def apply_page(feed_id, lease_id, link, rows):
    guard(feed_id, lease_id)
    BankAccount.objects.select_for_update().get(pk=link.account_id)
    newest = link.cursor
    for row in rows:
        remote_id, values = entry_data(row, link)
        entry, new = BankFeedEntry.objects.select_for_update().get_or_create(
            feed_account=link,
            remote_id=remote_id,
            defaults=values,
        )
        changed = values["changed_at"]
        newest = max(newest, changed) if newest else changed
        if entry.retired_identity:
            entry.retired_identity = False
            entry.save(update_fields=["retired_identity"])
        if not new and entry.changed_at >= changed:
            continue
        if not new:
            for key, value in values.items():
                setattr(entry, key, value)
            entry.save(update_fields=list(values))
        project_entry(entry)
    return newest


def match_invoice(company, entry):
    tx = entry.transaction
    if (
        not tx
        or tx.document_uid
        or tx.status != "booked"
        or entry.review
        or not tx.amount
    ):
        return
    direction = "outgoing" if tx.direction == "incoming" else "incoming"
    docs = Document.objects.filter(
        company_uid=company.company_uid,
        direction=direction,
        currency_code=entry.feed_account.account.currency_code,
        amount_due=tx.amount,
        type__in=["invoice", "progress_invoice"],
        status__in=["sent", "received", "accepted"],
    )
    original_numbers = dict(
        PdpInvoice.objects.filter(
            company_uid=company.company_uid,
            document_uid__in=docs.values("document_uid"),
        ).values_list("document_uid", "invoice_number")
    )
    matches = []
    for doc in docs.only("pk", "document_uid", "number").iterator():
        numbers = (
            (doc.number or "").strip(),
            (original_numbers.get(doc.document_uid) or "").strip(),
        )
        if any(
            len(number) >= 4
            and re.search(r"\d", number)
            and re.search(
                r"(?<![\w/-])" + re.escape(number) + r"(?![\w/-])",
                entry.label,
                re.IGNORECASE,
            )
            for number in numbers
        ):
            matches.append(doc.document_uid)
            if len(matches) > 1:
                return
    if len(matches) == 1:
        try:
            reconcile(company, tx, matches[0], exact=True)
        except BusinessError as exc:
            if exc.status != 409:
                raise


@transaction.atomic
def resolve_entry(company, entry_id, choice):
    entry = (
        BankFeedEntry.objects.select_for_update(of=("self",))
        .filter(
            pk=entry_id,
            feed_account__feed__company_uid=company.company_uid,
        )
        .select_related("feed_account__feed", "feed_account__account")
        .first()
    )
    if not entry or not entry.review:
        raise BusinessError("Opération à vérifier introuvable.", 404)
    if entry.review == "overlap":
        if choice == "new":
            project_entry(entry, distinct=True)
        else:
            tx = (
                candidates(entry)
                .select_for_update(of=("self",))
                .filter(transaction_uid=choice)
                .first()
            )
            if not tx:
                raise BusinessError("Choisissez une écriture disponible de ce compte.")
            adopt_transaction(entry, tx)
        return
    if choice != "correct":
        raise BusinessError("Confirmez la correction du mouvement rapproché.")
    tx = BankTransaction.objects.select_for_update().get(
        pk=entry.transaction_id, company_uid=company.company_uid
    )
    if tx.document_uid:
        doc = Document.objects.select_for_update().get(
            company_uid=company.company_uid, document_uid=tx.document_uid
        )
        if doc.amount_due + tx.amount > doc.total_including_tax:
            raise BusinessError(
                "Le solde de la facture a changé. Vérifiez les règlements avant de confirmer cette correction.",
                409,
            )
        doc.amount_due += tx.amount
        if doc.status == "paid":
            doc.status = entry.previous_invoice_status or (
                "received" if doc.direction == "incoming" else "sent"
            )
            doc.paid_at = None
        doc.updated_at = stamp()
        doc.save(update_fields=["amount_due", "status", "paid_at", "updated_at"])
        from .documents import event

        event(
            doc,
            "bank_correction",
            "Rapprochement annulé après correction bancaire",
            {"transaction_uid": tx.transaction_uid, "amount": tx.amount},
        )
        tx.document_uid = None
        tx.save(update_fields=["document_uid"])
    entry.previous_invoice_status = ""
    entry.save(update_fields=["previous_invoice_status"])
    project_entry(entry)


def sync_account(feed, lease_id, client, link):
    since = (
        {"since": (link.cursor - timedelta(minutes=5)).isoformat()}
        if link.cursor
        else {}
    )
    newest = link.cursor
    for page in client.pages("transactions", account_id=link.remote_id, **since):
        mark = apply_page(feed.pk, lease_id, link, page)
        if mark:
            newest = max(newest, mark) if newest else mark
    with transaction.atomic():
        guard(feed.pk, lease_id)
        BankFeedAccount.objects.filter(pk=link.pk).update(cursor=newest)


def sync_one(feed_id):
    now = timezone.now()
    lease_id = uid()
    acquired = (
        BankFeed.objects.filter(pk=feed_id, enabled=True, next_sync_at__lte=now)
        .exclude(remote_user="")
        .filter(
            Q(lease_until__isnull=True) | Q(lease_until__lte=now),
        )
        .update(lease=lease_id, lease_until=now + LEASE, next_sync_at=now)
    )
    if not acquired:
        return
    feed = BankFeed.objects.get(pk=feed_id)
    try:
        client = Bridge(feed)
        client.authorize()
        rows = {}
        for page in client.pages("accounts"):
            with transaction.atomic():
                guard(feed_id, lease_id)
            for row in page:
                key, values = account_data(row)
                rows[key] = values
        links = update_accounts(feed.pk, lease_id, rows)
        problem = None
        for link in links:
            try:
                sync_account(feed, lease_id, client, link)
            except BridgeError as exc:
                problem = exc
        if problem:
            raise problem
        company = Company.objects.get(company_uid=feed.company_uid)
        # Revisit before reconciliation: an invoice can arrive after its payment.
        waiting = BankFeedEntry.objects.filter(
            feed_account__feed=feed,
            feed_account__shared=True,
            retired_identity=False,
            feed_account__account__status="active",
            transaction__status="booked",
            transaction__document_uid__isnull=True,
            review="",
        ).values_list("pk", flat=True)
        if feed.auto_reconcile:
            for pk in waiting.iterator(chunk_size=200):
                with transaction.atomic():
                    if not guard(feed.pk, lease_id).auto_reconcile:
                        break
                    entry = (
                        BankFeedEntry.objects.select_for_update(of=("self",))
                        .select_related("transaction", "feed_account__account")
                        .get(pk=pk)
                    )
                    match_invoice(company, entry)
        retry_at = timezone.now() + (
            timedelta(minutes=5) if not rows and not feed.last_success_at else INTERVAL
        )
        next_sync = Case(
            When(next_sync_at=now, then=Value(retry_at)), default=F("next_sync_at")
        )
        BankFeed.objects.filter(pk=feed_id, lease=lease_id).update(
            last_success_at=timezone.now(),
            next_sync_at=next_sync,
            last_error="",
            lease="",
            lease_until=None,
        )
    except Exception as exc:
        message = (
            str(exc)
            if isinstance(exc, BusinessError)
            else "Le traitement bancaire a été interrompu. Les opérations déjà enregistrées sont conservées."
        )
        BankFeed.objects.filter(pk=feed_id, lease=lease_id).update(
            last_error=message,
            next_sync_at=timezone.now() + timedelta(minutes=15),
            lease="",
            lease_until=None,
        )


def sync_due():
    if settings.EXTERNAL_SERVICES_DISABLED:
        return
    now = timezone.now()
    due = (
        BankFeed.objects.filter(enabled=True, next_sync_at__lte=now)
        .exclude(remote_user="")
        .filter(Q(lease_until__lte=now) | Q(lease_until__isnull=True))
        .order_by("next_sync_at")
        .values_list("pk", flat=True)
    )
    for pk in list(due[:4]):
        sync_one(pk)
