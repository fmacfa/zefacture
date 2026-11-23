import calendar
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q, Sum

from core.common import BusinessError, unpack
from core.models import BankAccount, BankTransaction, Ingestion, StatementControl
from .receipt_extraction import EXPENSE_KINDS, simplify

CENT = Decimal(".01")
DAY = r"(\d{2}/\d{2}/\d{4})"
MONEY = r"(?<![\d,.])[-−+]?\s*(?:\d{1,3}(?:[ \u00a0\u202f.]\d{3})+|\d+)[,.]\d{2}(?!\d)"


def amount(value):
    try:
        v = Decimal("".join(str(value).split()).replace("−", "-").replace(",", "."))
        if not v.is_finite() or abs(v) >= 10**22 or v != v.quantize(CENT):
            raise InvalidOperation()
        return v
    except (InvalidOperation, ValueError):
        raise BusinessError(
            "Saisissez les montants au centime, avec un signe pour les soldes débiteurs."
        )


def printed(transcript):
    balances, totals = [], {}
    for line in simplify(transcript or "").splitlines():
        found = re.search(r"solde\s+(crediteur|debiteur)\s+au\s+" + DAY, line)
        if found:
            values = re.findall(MONEY, line[found.end() :])
            if len(values) == 1:
                day, month, year = map(int, found[2].split("/"))
                try:
                    point = (
                        date(year, month, day),
                        abs(amount(values[0])) * (-1 if found[1] == "debiteur" else 1),
                    )
                except (ValueError, BusinessError):
                    return {}
                if point not in balances:
                    balances.append(point)
        elif "total des mouvements" in line:
            values = re.findall(MONEY, line.split("total des mouvements", 1)[1])
            if (
                len(values) == 2
                and "crediteurs" not in line
                and "debiteurs" not in line
            ):
                totals.update(
                    debits=str(abs(amount(values[0]))),
                    credits=str(abs(amount(values[1]))),
                )
            elif len(values) == 1 and ("debiteurs" in line or "crediteurs" in line):
                totals["debits" if "debiteurs" in line else "credits"] = str(
                    abs(amount(values[0]))
                )
    if len(balances) != 2 or balances[0][0] >= balances[1][0]:
        return {}
    opening_day, opening = balances[0]
    # Usually the opening balance is the previous statement's closing balance.
    # A first statement can also include movements on its opening day.
    label = opening_day.strftime("%d/%m/%Y")
    has_opening_day = any(
        re.match(r"^\s*" + re.escape(label) + r"\s", l) for l in transcript.splitlines()
    )
    start = opening_day if has_opening_day else opening_day + timedelta(days=1)
    return {
        "start": start.isoformat(),
        "end": balances[1][0].isoformat(),
        "opening": str(opening),
        "closing": str(balances[1][1]),
        **totals,
    }


def period_bounds(month):
    return month.replace(day=1), month.replace(
        day=calendar.monthrange(month.year, month.month)[1]
    )


def ledger(control):
    movements = BankTransaction.objects.filter(
        company_uid=control.account.company_uid,
        account_uid=control.account.account_uid,
        status="booked",
        transaction_date__gte=control.start.isoformat(),
        transaction_date__lte=control.end.isoformat(),
    )
    by_direction = dict(
        movements.values_list("direction").annotate(n=Sum("amount")).order_by()
    )
    debit, credit = (
        by_direction.get("outgoing", Decimal(0)),
        by_direction.get("incoming", Decimal(0)),
    )
    computed = control.opening + credit - debit
    prior = (
        StatementControl.objects.filter(
            account=control.account, end=control.start - timedelta(days=1)
        ).first()
        if control.start > date.min
        else None
    )
    matches = (
        computed == control.closing
        and (control.debits is None or debit == control.debits)
        and (control.credits is None or credit == control.credits)
        and (not prior or prior.closing == control.opening)
    )
    return dict(
        debit=debit,
        credit=credit,
        computed=computed,
        delta=computed - control.closing,
        previous_gap=control.opening - prior.closing if prior else None,
        matches=matches,
        count=movements.count(),
    )


@transaction.atomic
def save_control(account, values, actor, reference, source=None, version=""):
    account = BankAccount.objects.select_for_update().get(pk=account.pk)
    try:
        start, end = (date.fromisoformat(values[k]) for k in ("start", "end"))
    except (TypeError, KeyError, ValueError):
        raise BusinessError("Renseignez les deux dates imprimées du relevé.")
    if (
        start > end
        or not reference.strip()
        or len(reference) > 255
        or (source and source.company_uid != account.company_uid)
    ):
        raise BusinessError("Vérifiez la période, le compte et la référence du relevé.")
    data = {k: amount(values.get(k)) for k in ("opening", "closing")}
    for k in ("debits", "credits"):
        data[k] = amount(values[k]) if values.get(k) not in ("", None) else None
        if data[k] is not None and data[k] < 0:
            raise BusinessError(
                "Les totaux des débits et crédits sont saisis en valeur absolue."
            )
    existing = StatementControl.objects.filter(
        account=account, start__lte=end, end__gte=start
    )
    control = existing.filter(start=start, end=end).first()
    if existing.exclude(pk=control.pk if control else None).exists():
        raise BusinessError(
            "La période chevauche un autre relevé du même compte. Vérifiez les dates et les annexes.",
            409,
        )
    if control:
        if all(getattr(control, k) == v for k, v in data.items()) and (
            not source or control.source_id == source.pk
        ):
            return control
        if version != control.updated_at.isoformat():
            raise BusinessError(
                "Un contrôle existe pour ces dates. Ouvrez sa correction depuis la clôture mensuelle.",
                409,
            )
    else:
        control = StatementControl(account=account, start=start, end=end)
    for key, v in data.items():
        setattr(control, key, v)
    if source:
        control.source = source
    control.reference, control.updated_by = reference.strip(), actor
    control.save()
    return control


def review_queue(company, month=None):
    rows = Ingestion.objects.filter(
        company_uid=company.company_uid, archived_at__isnull=True
    )
    expense = Q(source_kind__in=EXPENSE_KINDS)
    missing = (
        Q(expense_date__isnull=True)
        | Q(expense_date="")
        | Q(merchant_name__isnull=True)
        | Q(merchant_name="")
        | Q(expense_amount__isnull=True)
        | Q(expense_currency="")
    )
    rows = rows.filter(
        (expense & missing)
        | (~expense & (Q(document_uid__isnull=True) | Q(document_uid="")))
    )
    result = []
    for row in rows.select_related("closing_review").order_by(
        "source_kind", "original_filename"
    ):
        note = getattr(row, "closing_review", None)
        reviewed_date = (
            note.document_date.isoformat() if note and note.document_date else ""
        )
        parsed = (
            unpack(row.extraction_json)
            if not row.expense_date and not reviewed_date
            else {}
        )
        known_date = (
            row.expense_date
            or reviewed_date
            or (parsed.get("issue_date") if isinstance(parsed, dict) else "")
        )
        if (
            month
            and known_date
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(known_date))
            and not str(known_date).startswith(month.strftime("%Y-%m"))
        ):
            continue
        is_expense = row.source_kind in EXPENSE_KINDS
        if is_expense:
            reason = ", ".join(
                label
                for field, label in (
                    ("expense_date", "date"),
                    ("merchant_name", "commerçant"),
                    ("expense_amount", "TTC"),
                    ("expense_currency", "devise"),
                )
                if getattr(row, field) in ("", None)
            )
            action = "Compléter le justificatif"
        else:
            reason, action = (
                row.last_error or "Facture en attente de vérification",
                "Vérifier la facture",
            )
        result.append(
            dict(
                label=row.original_filename,
                reason=note.reason if note else reason,
                date=known_date,
                note=note,
                action=note.next_action if note else action,
                uid=row.ingestion_uid,
                expense=is_expense,
            )
        )
    return result
