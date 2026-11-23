"""Legacy table/column names retained so every imported field has an explicit home."""

from django.db import models
from django.utils import timezone
from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from .common import uid, stamp, unpack
from .payment_details import PaymentMethod, PaidBy


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra):
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.update(is_staff=True, is_superuser=True)
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    user_uid = models.CharField(max_length=32, unique=True, default=uid)
    email = models.CharField(max_length=254, unique=True, default="")
    password = models.CharField(max_length=255, db_column="password_hash", default="")
    name = models.TextField(default="")
    terms_accepted_at = models.TextField(null=True, blank=True)
    created_at = models.TextField(default=stamp)
    updated_at = models.TextField(null=True, blank=True)
    session_version = models.IntegerField(default=1)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    objects = UserManager()
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    def __str__(self):
        return self.email

    class Meta:
        db_table = "users"


class Company(models.Model):
    ingestion_ai = models.JSONField(default=dict, blank=True)
    company_uid = models.CharField(max_length=32, unique=True, default=uid)
    owner_user_uid = models.CharField(max_length=32, db_index=True, default="")
    legal_name = models.TextField(default="")
    trade_name = models.TextField(null=True, blank=True)
    siren = models.TextField(null=True, blank=True)
    siret = models.TextField(null=True, blank=True)
    vat_number = models.TextField(null=True, blank=True)
    address_line1 = models.TextField(null=True, blank=True)
    address_line2 = models.TextField(null=True, blank=True)
    postal_code = models.TextField(null=True, blank=True)
    city = models.TextField(null=True, blank=True)
    country_code = models.TextField(default="FR")
    billing_email = models.TextField(null=True, blank=True)
    support_email = models.TextField(null=True, blank=True)
    phone = models.TextField(null=True, blank=True)
    website = models.TextField(null=True, blank=True)
    legal_form = models.TextField(null=True, blank=True)
    share_capital = models.TextField(null=True, blank=True)
    rcs_city = models.TextField(null=True, blank=True)
    rcs_number = models.TextField(null=True, blank=True)
    ape_code = models.TextField(null=True, blank=True)
    bank_iban = models.TextField(null=True, blank=True)
    bank_bic = models.TextField(null=True, blank=True)
    payment_terms = models.TextField(null=True, blank=True)
    invoice_footer = models.TextField(null=True, blank=True)
    vat_regime = models.TextField(null=True, blank=True)
    has_vat_on_debits = models.IntegerField(null=True, blank=True)
    quote_number_prefix = models.TextField(default="DE")
    invoice_number_prefix = models.TextField(default="FA")
    credit_note_number_prefix = models.TextField(default="AV")
    next_quote_number = models.IntegerField(default=1)
    next_invoice_number = models.IntegerField(default=1)
    next_credit_note_number = models.IntegerField(default=1)
    pdp_company_id = models.TextField(null=True, blank=True)
    pdp_company_verification_status = models.TextField(null=True, blank=True)
    status = models.TextField(default="active")
    created_at = models.TextField(default=stamp)
    updated_at = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "companies"


class CompanyMember(models.Model):
    company_uid = models.CharField(max_length=32, db_index=True, default="")
    user_uid = models.CharField(max_length=32, db_index=True, default="")
    role = models.TextField(default="member")
    status = models.TextField(default="active")
    joined_at = models.TextField(null=True, blank=True)
    created_at = models.TextField(default=stamp)

    class Meta:
        db_table = "company_members"
        unique_together = (("company_uid", "user_uid"),)


class CompanyInvitation(models.Model):
    invitation_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True, default="")
    invited_by_user_uid = models.CharField(max_length=32, db_index=True, default="")
    email = models.TextField(default="")
    role = models.TextField(default="member")
    token = models.CharField(max_length=128, unique=True, default="")
    status = models.TextField(default="pending")
    expires_at = models.TextField(default="")
    accepted_at = models.TextField(null=True, blank=True)
    created_at = models.TextField(default=stamp)

    class Meta:
        db_table = "company_invitations"


class PasswordReset(models.Model):
    account_version = models.IntegerField(null=True)
    reset_uid = models.CharField(max_length=32, unique=True, default=uid)
    user_uid = models.CharField(max_length=32, db_index=True, default="")
    email = models.TextField(default="")
    token_hash = models.CharField(max_length=128, unique=True, default="")
    status = models.TextField(default="pending")
    expires_at = models.TextField(default="")
    used_at = models.TextField(null=True, blank=True)
    created_at = models.TextField(default=stamp)

    class Meta:
        db_table = "password_reset_tokens"


class PdpSession(models.Model):
    session_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True, default="")
    provider = models.TextField(default="superpdp")
    api_base = models.TextField(default="https://api.superpdp.tech")
    client_id = models.TextField(null=True, blank=True)
    client_secret_encrypted = models.TextField(null=True, blank=True)
    access_token_encrypted = models.TextField(null=True, blank=True)
    refresh_token_encrypted = models.TextField(null=True, blank=True)
    token_expires_at = models.TextField(null=True, blank=True)
    pdp_user_id = models.TextField(null=True, blank=True)
    pdp_company_id = models.TextField(null=True, blank=True)
    pdp_company_number = models.TextField(null=True, blank=True)
    pdp_company_number_scheme = models.TextField(null=True, blank=True)
    pdp_company_env = models.TextField(null=True, blank=True)
    pdp_company_payload = models.TextField(null=True, blank=True)
    user_verification_status = models.TextField(null=True, blank=True)
    company_verification_status = models.TextField(null=True, blank=True)
    status = models.TextField(default="not_configured")
    last_checked_at = models.TextField(null=True, blank=True)
    created_at = models.TextField(default=stamp)
    updated_at = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "pdp_oauth_sessions"
        unique_together = (("company_uid", "provider"),)


class SaleOperation(models.TextChoices):
    GOODS = "B1", "Livraison de biens"
    SERVICES = "S1", "Prestation de services"
    MIXED = "M1", "Biens et services"


class Customer(models.Model):
    delivery_address = models.JSONField(default=dict, blank=True)
    local_registration = models.CharField(max_length=100, blank=True, default="")
    customer_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True, default="")
    type = models.TextField(default="business")
    legal_name = models.TextField(default="")
    trade_name = models.TextField(null=True, blank=True)
    siren = models.TextField(null=True, blank=True)
    siret = models.TextField(null=True, blank=True)
    vat_number = models.TextField(null=True, blank=True)
    email = models.TextField(null=True, blank=True)
    phone = models.TextField(null=True, blank=True)
    address_line1 = models.TextField(null=True, blank=True)
    address_line2 = models.TextField(null=True, blank=True)
    postal_code = models.TextField(null=True, blank=True)
    city = models.TextField(null=True, blank=True)
    country_code = models.TextField(default="FR")
    electronic_address = models.TextField(null=True, blank=True)
    electronic_address_scheme = models.TextField(null=True, blank=True)
    status = models.TextField(default="active")
    created_at = models.TextField(default=stamp)
    updated_at = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "customers"


class Document(models.Model):
    delivery_date = models.DateField(null=True, blank=True)
    purchase_order = models.CharField(max_length=100, blank=True, default="")
    global_discount_type = models.CharField(max_length=7, default="percent")
    global_discount_value = models.DecimalField(max_digits=16, decimal_places=4, default=0)
    global_discount_amount = models.DecimalField(max_digits=30, decimal_places=2, default=0)
    operation_category = models.CharField(max_length=2, choices=SaleOperation.choices, blank=True, default="")
    delivery_address = models.JSONField(default=dict, blank=True)
    document_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True, default="")
    customer_uid = models.CharField(max_length=32, db_index=True, null=True, blank=True)
    direction = models.TextField(default="outgoing")
    type = models.TextField(default="")
    status = models.TextField(default="draft")
    number = models.TextField(null=True, blank=True)
    issue_date = models.TextField(null=True, blank=True)
    due_date = models.TextField(null=True, blank=True)
    currency_code = models.TextField(default="EUR")
    reference = models.TextField(null=True, blank=True)
    subject = models.TextField(null=True, blank=True)
    buyer_reference = models.TextField(null=True, blank=True)
    payment_terms = models.TextField(null=True, blank=True)
    payment_method_code = models.TextField(null=True, blank=True)
    expense_payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, blank=True, default="")
    expense_paid_by = models.CharField(max_length=16, choices=PaidBy.choices, blank=True, default="")
    expense_description = models.TextField(blank=True, default="")
    notes = models.TextField(null=True, blank=True)
    source_quote_uid = models.CharField(
        max_length=32, db_index=True, null=True, blank=True
    )
    source_invoice_uid = models.CharField(
        max_length=32, db_index=True, null=True, blank=True
    )
    progress_percent = models.DecimalField(
        max_digits=30, decimal_places=8, null=True, blank=True
    )
    seller_snapshot = models.TextField(default="{}")
    buyer_snapshot = models.TextField(default="{}")
    total_excluding_tax = models.DecimalField(
        max_digits=30, decimal_places=8, default=0
    )
    total_vat = models.DecimalField(max_digits=30, decimal_places=8, default=0)
    total_including_tax = models.DecimalField(
        max_digits=30, decimal_places=8, default=0
    )
    amount_due = models.DecimalField(max_digits=30, decimal_places=8, default=0)
    pdp_invoice_id = models.TextField(null=True, blank=True)
    pdp_external_id = models.TextField(null=True, blank=True)
    pdp_processing_rule = models.TextField(null=True, blank=True)
    sent_at = models.TextField(null=True, blank=True)
    received_at = models.TextField(null=True, blank=True)
    accepted_at = models.TextField(null=True, blank=True)
    paid_at = models.TextField(null=True, blank=True)
    public_token_hash = models.TextField(null=True, blank=True)
    public_token_expires_at = models.TextField(null=True, blank=True)
    created_by_user_uid = models.CharField(max_length=32, db_index=True, default="")
    created_at = models.TextField(default=stamp)
    updated_at = models.TextField(null=True, blank=True)
    rounding_amount = models.DecimalField(max_digits=30, decimal_places=8, default=0)

    @property
    def journal_sign(self):
        sign = -1 if self.direction == "incoming" else 1
        return -sign if self.type == "credit_note" else sign

    @property
    def draft_reference(self):
        return "BROUILLON-" + self.document_uid

    @property
    def lines(self):
        return DocumentLine.objects.filter(document_uid=self.document_uid).order_by(
            "position", "id"
        )

    @property
    def files(self):
        return DocumentFile.objects.filter(document_uid=self.document_uid).order_by(
            "-id"
        )

    @property
    def seller(self):
        return unpack(self.seller_snapshot)

    @property
    def buyer(self):
        return unpack(self.buyer_snapshot)

    @property
    def get_status_label(self):
        if self.direction == "outgoing" and self.type in ("invoice", "progress_invoice") and self.status == "sent":
            return "Émise"
        return {
            "draft": "Brouillon",
            "sent": "Envoyé",
            "paid": "Payé",
            "accepted": "Accepté",
            "received": "Reçu",
            "cancelled": "Annulé",
            "refused": "Refusé",
            "disputed": "En litige",
            "simulated": "Simulation",
        }.get(self.status, self.status)

    @property
    def url(self):
        section = (
            "reception"
            if self.direction == "incoming"
            else {"quote": "devis", "credit_note": "avoirs"}.get(self.type, "factures")
        )
        return f"/app/{section}/{self.document_uid}"

    class Meta:
        db_table = "documents"
        constraints = [
            models.UniqueConstraint(
                fields=["company_uid", "number"],
                condition=~models.Q(direction="incoming") & models.Q(number__isnull=False) & ~models.Q(number=""),
                name="unique_issued_document_number",
            ),
            models.UniqueConstraint(
                fields=["company_uid", "customer_uid", "number"],
                condition=models.Q(direction="incoming", number__isnull=False) & ~models.Q(number=""),
                name="unique_supplier_document_number",
            ),
        ]


class DocumentLine(models.Model):
    unit_price_including_tax = models.DecimalField(max_digits=30, decimal_places=8, null=True, blank=True)
    global_discount_amount = models.DecimalField(max_digits=30, decimal_places=2, default=0)

    @property
    def net_before_global_discount(self):
        return self.total_excluding_tax + self.global_discount_amount

    line_uid = models.CharField(max_length=32, unique=True, default=uid)
    document_uid = models.CharField(max_length=32, db_index=True, default="")
    position = models.IntegerField(default=1)
    item_reference = models.TextField(null=True, blank=True)
    description = models.TextField(default="")
    quantity = models.DecimalField(max_digits=30, decimal_places=8, default=1)
    unit_code = models.TextField(default="C62")
    unit_price_excluding_tax = models.DecimalField(
        max_digits=30, decimal_places=8, default=0
    )
    discount_percent = models.DecimalField(max_digits=30, decimal_places=8, default=0)
    vat_category_code = models.TextField(default="S")
    vat_rate = models.DecimalField(max_digits=30, decimal_places=8, default=20)
    total_excluding_tax = models.DecimalField(
        max_digits=30, decimal_places=8, default=0
    )
    total_vat = models.DecimalField(max_digits=30, decimal_places=8, default=0)
    total_including_tax = models.DecimalField(
        max_digits=30, decimal_places=8, default=0
    )
    created_at = models.TextField(default=stamp)

    class Meta:
        db_table = "document_lines"


class DocumentFile(models.Model):
    file_uid = models.CharField(max_length=32, unique=True, default=uid)
    document_uid = models.CharField(max_length=32, db_index=True, default="")
    kind = models.TextField(default="")
    storage_path = models.TextField(default="")
    mime_type = models.TextField(null=True, blank=True)
    sha256 = models.TextField(null=True, blank=True)
    file_size = models.IntegerField(null=True, blank=True)
    generated_at = models.TextField(null=True, blank=True)
    created_at = models.TextField(default=stamp)

    class Meta:
        db_table = "document_files"


class InventoryItem(models.Model):
    currency_code = models.CharField(max_length=3, blank=True, default="")
    vat_category_code = models.CharField(max_length=2, default="", blank=True)
    unit_price_including_tax = models.DecimalField(max_digits=30, decimal_places=8, null=True, blank=True)
    item_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True, default="")
    reference = models.TextField(null=True, blank=True)
    name = models.TextField(default="")
    description = models.TextField(null=True, blank=True)
    item_type = models.TextField(default="service")
    image_path = models.TextField(null=True, blank=True)
    image_mime_type = models.TextField(null=True, blank=True)
    stock_tracking = models.IntegerField(default=0)
    reorder_level = models.DecimalField(
        max_digits=30, decimal_places=8, null=True, blank=True
    )
    unit_code = models.TextField(default="C62")
    unit_price_excluding_tax = models.DecimalField(
        max_digits=30, decimal_places=8, default=0
    )
    vat_rate = models.DecimalField(max_digits=30, decimal_places=8, default=20)
    status = models.TextField(default="active")
    created_at = models.TextField(default=stamp)
    updated_at = models.TextField(null=True, blank=True)

    @property
    def stock(self):
        return sum(
            m.quantity if m.movement_type == "in" else -m.quantity
            for m in StockMovement.objects.filter(company_uid=self.company_uid, item_uid=self.item_uid)
        )

    class Meta:
        db_table = "inventory_items"


class StockMovement(models.Model):
    movement_uid = models.CharField(max_length=32, unique=True, default=uid)
    item_uid = models.CharField(max_length=32, db_index=True, default="")
    source_document_uid = models.CharField(max_length=32, blank=True, default="", db_index=True)
    source_line_uid = models.CharField(max_length=32, blank=True, default="")
    company_uid = models.CharField(max_length=32, db_index=True, default="")
    movement_type = models.TextField(default="")
    quantity = models.DecimalField(max_digits=30, decimal_places=8)
    occurred_on = models.TextField(default="")
    reason = models.TextField(null=True, blank=True)
    reference = models.TextField(null=True, blank=True)
    created_by_user_uid = models.CharField(max_length=32, db_index=True, default="")
    created_at = models.TextField(default=stamp)

    class Meta:
        db_table = "inventory_stock_movements"
        constraints = [
            models.UniqueConstraint(fields=("company_uid", "source_line_uid"), condition=~models.Q(source_line_uid=""), name="unique_stock_invoice_line")
        ]


class Ingestion(models.Model):
    batch_uid = models.CharField(max_length=32, blank=True, default="", db_index=True)
    source_invoice_ref = models.CharField(max_length=140, blank=True, default="", db_index=True)
    source_invoice_match = models.CharField(max_length=64, blank=True, default="", db_index=True)
    ingestion_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True, default="")
    uploaded_by_user_uid = models.CharField(max_length=32, db_index=True, default="")
    document_uid = models.CharField(max_length=32, db_index=True, null=True, blank=True)
    source_kind = models.TextField(default="supplier_invoice")
    expense_date = models.TextField(null=True, blank=True)
    expense_payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, blank=True, default="")
    expense_paid_by = models.CharField(max_length=16, choices=PaidBy.choices, blank=True, default="")
    expense_category = models.TextField(null=True, blank=True)
    expense_description = models.TextField(null=True, blank=True)
    merchant_name = models.TextField(null=True, blank=True)
    expense_amount = models.DecimalField(
        max_digits=30, decimal_places=8, null=True, blank=True
    )
    expense_currency = models.TextField(default="EUR")
    expense_paid_eur = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    expense_payer_name = models.CharField(max_length=180, blank=True, default="")
    expense_status = models.TextField(default="pending")
    archived_at = models.DateTimeField(null=True, blank=True)
    original_filename = models.TextField(default="")
    storage_path = models.TextField(default="")
    mime_type = models.TextField(null=True, blank=True)
    sha256 = models.TextField(null=True, blank=True)
    file_size = models.IntegerField(null=True, blank=True)
    status = models.TextField(default="uploaded")
    transcription_status = models.TextField(default="pending")
    transcription_output_path = models.TextField(null=True, blank=True)
    transcription_text = models.TextField(null=True, blank=True)
    transcription_json = models.TextField(null=True, blank=True)
    extraction_status = models.TextField(default="pending")
    extraction_model = models.TextField(null=True, blank=True)
    extraction_json = models.TextField(null=True, blank=True)
    last_error = models.TextField(null=True, blank=True)
    created_at = models.TextField(default=stamp)
    updated_at = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "document_ingestions"


class ExpenseReimbursement(models.Model):
    receipt = models.OneToOneField(Ingestion, on_delete=models.CASCADE, related_name="reimbursement")
    status = models.CharField(max_length=12, choices=[
        ("due", "À rembourser"), ("paid", "Remboursée"), ("exempt", "Sans remboursement"),
        ("unknown", "À préciser"),
    ])
    paid_on = models.DateField(null=True, blank=True)
    updated_by = models.CharField(max_length=32)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "django_expense_reimbursements"
        constraints = [models.CheckConstraint(
            condition=models.Q(status="paid", paid_on__isnull=False)
            | models.Q(status__in=["due", "exempt", "unknown"], paid_on__isnull=True),
            name="expense_reimbursement_date",
        )]


class InvoiceEvent(models.Model):
    event_uid = models.CharField(max_length=32, unique=True, default=uid)
    document_uid = models.CharField(max_length=32, db_index=True, null=True, blank=True)
    company_uid = models.CharField(max_length=32, db_index=True, default="")
    provider = models.TextField(default="superpdp")
    pdp_event_id = models.TextField(null=True, blank=True)
    pdp_invoice_id = models.TextField(null=True, blank=True)
    event_code = models.TextField(default="")
    event_label = models.TextField(null=True, blank=True)
    direction = models.TextField(default="")
    payload = models.TextField(null=True, blank=True)
    occurred_at = models.TextField(null=True, blank=True)
    created_at = models.TextField(default=stamp)

    class Meta:
        db_table = "invoice_events"


class Email(models.Model):
    reminder_balance = models.DecimalField(max_digits=30, decimal_places=2, null=True, blank=True)
    email_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True, null=True, blank=True)
    document_uid = models.CharField(max_length=32, db_index=True, null=True, blank=True)
    template = models.TextField(default="")
    recipient_email = models.TextField(default="")
    recipient_name = models.TextField(null=True, blank=True)
    sender_email = models.TextField(default="")
    sender_name = models.TextField(default="")
    subject = models.TextField(default="")
    body_text = models.TextField(default="")
    body_html = models.TextField(null=True, blank=True)
    status = models.TextField(default="queued")
    transport = models.TextField(default="outbox")
    provider_message_id = models.TextField(null=True, blank=True)
    provider_response = models.TextField(null=True, blank=True)
    sent_at = models.TextField(null=True, blank=True)
    last_error = models.TextField(null=True, blank=True)
    created_at = models.TextField(default=stamp)
    updated_at = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "email_outbox"


class ApiKey(models.Model):
    class Scope(models.TextChoices):
        BUSINESS = "business", "API métier"
        ASSISTANT = "assistant_read", "Codex · lecture seule"

    key_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True, default="")
    name = models.TextField(default="")
    key_hash = models.TextField(default="")
    prefix = models.TextField(default="")
    scope = models.CharField(max_length=24, choices=Scope, default=Scope.BUSINESS)
    status = models.TextField(default="active")
    created_at = models.TextField(default=stamp)
    revoked_at = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "api_keys"


class InvoiceApiCreation(models.Model):
    company_uid = models.CharField(max_length=32)
    request_key = models.CharField(max_length=64, null=True)
    reference_hash = models.CharField(max_length=64, null=True)
    payload_hash = models.CharField(max_length=64)
    document = models.OneToOneField(Document, on_delete=models.PROTECT)

    class Meta:
        db_table = "django_invoice_api_creations"
        constraints = [models.UniqueConstraint(
            fields=["company_uid", "request_key"], name="unique_invoice_api_request",
        ), models.UniqueConstraint(
            fields=["company_uid", "reference_hash"], name="unique_invoice_api_order",
        )]


class BankAccount(models.Model):
    account_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True, default="")
    name = models.TextField(default="")
    bank_name = models.TextField(null=True, blank=True)
    iban = models.TextField(null=True, blank=True)
    currency_code = models.TextField(default="EUR")
    opening_balance = models.DecimalField(max_digits=30, decimal_places=8, default=0)
    current_balance = models.DecimalField(max_digits=30, decimal_places=8, default=0)
    status = models.TextField(default="active")
    connected_provider = models.TextField(null=True, blank=True)
    last_synced_at = models.TextField(null=True, blank=True)
    created_at = models.TextField(default=stamp)
    updated_at = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "bank_accounts"


class BankTransaction(models.Model):
    transaction_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True, default="")
    account_uid = models.CharField(max_length=32, db_index=True, null=True, blank=True)
    document_uid = models.CharField(max_length=32, db_index=True, null=True, blank=True)
    transaction_date = models.TextField(default="")
    label = models.TextField(default="")
    amount = models.DecimalField(max_digits=30, decimal_places=8, default=0)
    direction = models.TextField(default="")
    category = models.TextField(null=True, blank=True)
    standalone_category = models.CharField(max_length=32, blank=True, default="")
    classification_note = models.CharField(max_length=500, blank=True, default="")
    status = models.TextField(default="booked")
    external_id = models.TextField(null=True, blank=True)
    import_hash = models.TextField(null=True, blank=True)
    imported_at = models.TextField(null=True, blank=True)
    created_at = models.TextField(default=stamp)
    updated_at = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "bank_transactions"
        constraints = [
            models.UniqueConstraint(
                fields=["company_uid", "import_hash"],
                condition=models.Q(import_hash__isnull=False)
                & ~models.Q(import_hash=""),
                name="unique_company_bank_import",
            ),
            models.CheckConstraint(
                condition=models.Q(standalone_category="") | models.Q(document_uid__isnull=True) | models.Q(document_uid=""),
                name="bank_class_or_invoice",
            ),
        ]


class BankAllocation(models.Model):
    class Kind(models.TextChoices):
        DOCUMENT = "document", "Facture ou avoir"
        CLAIM = "claim", "Note de frais groupée"
        FEE = "fee", "Commission ou frais"
        CREDIT = "credit", "Crédit antérieur / trop-perçu"
        RENT = "rent", "Terme de loyer"
        CARRY = "carry", "Solde reporté"
        OTHER = "other", "Autre montant justifié"

    transaction = models.ForeignKey(BankTransaction, on_delete=models.CASCADE, related_name="allocations")
    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="bank_allocations", null=True, blank=True)
    claim = models.ForeignKey("ExpenseClaim", on_delete=models.PROTECT, related_name="allocations", null=True, blank=True)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    note = models.CharField(max_length=500, blank=True)
    previous_status = models.CharField(max_length=32, blank=True)
    previous_receipt_date = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "django_bank_allocations"
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=~models.Q(amount=0), name="bank_allocation_nonzero"),
            models.CheckConstraint(condition=models.Q(kind="document", document__isnull=False)
                                  | (~models.Q(kind="document") & models.Q(document__isnull=True)),
                                  name="bank_allocation_document_kind"),
            models.UniqueConstraint(fields=["transaction", "document"], name="bank_allocation_one_per_document"),
            models.CheckConstraint(condition=models.Q(kind="claim", claim__isnull=False, amount__lt=0)
                                  | (~models.Q(kind="claim") & models.Q(claim__isnull=True)),
                                  name="bank_allocation_claim_kind"),
            models.UniqueConstraint(fields=["transaction", "claim"], name="bank_allocation_one_per_claim"),
        ]


class SaleReceipt(models.Model):
    receipt_uid = models.CharField(max_length=32, unique=True, default=uid)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="receipts")
    amount = models.DecimalField(max_digits=30, decimal_places=8)
    received_on = models.DateField(null=True, blank=True, db_index=True)
    origin = models.CharField(max_length=12, choices=[
        ("manual", "Déclaré"), ("bank", "Bancaire"), ("legacy", "Historique")
    ])
    transaction = models.ForeignKey(BankTransaction, on_delete=models.PROTECT, null=True, blank=True, related_name="sale_receipts")
    cancelled = models.BooleanField(default=False)

    class Meta:
        db_table = "django_sale_receipts"
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="sale_receipt_positive"),
            models.UniqueConstraint(fields=["transaction", "document"], name="sale_receipt_one_per_tx_document"),
        ]


class BankStatementImport(models.Model):
    item_uid = models.CharField(max_length=32, unique=True, default=uid)
    batch_uid = models.CharField(max_length=32, db_index=True)
    position = models.PositiveSmallIntegerField()
    account = models.ForeignKey(BankAccount, on_delete=models.CASCADE)
    user_uid = models.CharField(max_length=32)
    filename = models.CharField(max_length=255)
    kind = models.CharField(max_length=4)
    provider = models.CharField(max_length=16, blank=True)
    file_digest = models.CharField(max_length=64)
    contents = models.BinaryField(null=True)
    status = models.CharField(max_length=16, default="queued", db_index=True)
    result = models.JSONField(default=dict)
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    started_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "django_bank_statement_imports"
        constraints = [
            models.UniqueConstraint(fields=("batch_uid", "position"), name="bank_statement_batch_position")
        ]


class StatementMatch(models.Model):
    account = models.ForeignKey(BankAccount, on_delete=models.CASCADE)
    transaction = models.ForeignKey(BankTransaction, on_delete=models.CASCADE, related_name="statement_matches")
    kind = models.CharField(max_length=3)
    file_digest = models.CharField(max_length=64)
    row_key = models.CharField(max_length=64)

    class Meta:
        db_table = "django_bank_statement_matches"
        constraints = [models.UniqueConstraint(fields=("account", "row_key"), name="bank_statement_row_once")]


class BankFeed(models.Model):
    company_uid = models.CharField(max_length=32, unique=True)
    external_uid = models.CharField(max_length=32, unique=True, default=uid)
    client_id = models.CharField(max_length=180)
    secret_encrypted = models.TextField()
    remote_user = models.CharField(max_length=36, blank=True)
    owner_uid = models.CharField(max_length=32, blank=True)
    enabled = models.BooleanField(default=False)
    auto_reconcile = models.BooleanField(default=True)
    next_sync_at = models.DateTimeField(null=True, db_index=True)
    last_success_at = models.DateTimeField(null=True)
    last_error = models.TextField(blank=True)
    lease = models.CharField(max_length=32, blank=True)
    lease_until = models.DateTimeField(null=True)

    class Meta:
        db_table = "django_bank_feeds"


class BankFeedAccount(models.Model):
    feed = models.ForeignKey(BankFeed, on_delete=models.CASCADE)
    remote_id = models.CharField(max_length=40)
    account = models.OneToOneField(BankAccount, on_delete=models.PROTECT, related_name="bank_feed")
    shared = models.BooleanField(default=True)
    cursor = models.DateTimeField(null=True)

    class Meta:
        db_table = "django_bank_feed_accounts"
        constraints = [models.UniqueConstraint(fields=["feed", "remote_id"], name="uniq_feed_account")]


class BankFeedEntry(models.Model):
    feed_account = models.ForeignKey(BankFeedAccount, on_delete=models.CASCADE)
    remote_id = models.CharField(max_length=40)
    transaction = models.OneToOneField(BankTransaction, null=True, on_delete=models.PROTECT, related_name="bank_feed")
    changed_at = models.DateTimeField()
    day = models.DateField()
    amount = models.DecimalField(max_digits=30, decimal_places=8)
    label = models.TextField()
    bank_reference = models.CharField(max_length=256, blank=True)
    status = models.CharField(max_length=16)
    review = models.CharField(max_length=16, blank=True)
    previous_invoice_status = models.CharField(max_length=32, blank=True)
    retired_identity = models.BooleanField(default=False)

    class Meta:
        db_table = "django_bank_feed_entries"
        constraints = [models.UniqueConstraint(fields=["feed_account", "remote_id"], name="uniq_feed_entry")]


class Forecast(models.Model):
    forecast_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True, default="")
    label = models.TextField(default="")
    due_date = models.TextField(default="")
    amount = models.DecimalField(max_digits=30, decimal_places=8, default=0)
    direction = models.TextField(default="")
    status = models.TextField(default="planned")
    source_document_uid = models.CharField(
        max_length=32, db_index=True, null=True, blank=True
    )
    created_at = models.TextField(default=stamp)
    updated_at = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "cashflow_forecasts"


class RateLimit(models.Model):
    bucket = models.CharField(max_length=64, primary_key=True)
    attempts = models.TextField()
    expires_at = models.IntegerField()

    class Meta:
        db_table = "auth_rate_limits"


class InstanceSetup(models.Model):
    code_hash = models.CharField(max_length=64, blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "django_instance_setup"


class WebhookReceipt(models.Model):
    receipt_key = models.CharField(max_length=64, primary_key=True)
    document_uid = models.CharField(max_length=32, db_index=True, default="")
    created_at = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "webhook_receipts"


class LegacySchemaVersion(models.Model):
    version = models.CharField(max_length=64, primary_key=True)
    applied_at = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "schema_migrations"


class BackgroundJob(models.Model):
    job_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True)
    ingestion_uid = models.CharField(max_length=32, db_index=True)
    operation = models.CharField(max_length=20)
    provider = models.CharField(max_length=20, blank=True, default="")
    status = models.CharField(max_length=20, default="queued", db_index=True)
    error = models.TextField(default="")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "django_background_jobs"
        constraints = [
            models.UniqueConstraint(
                fields=["ingestion_uid", "operation"],
                condition=models.Q(status__in=["queued", "running"]),
                name="unique_pending_ingestion_job",
            )
        ]


class CustomerProfile(models.Model):
    company_uid = models.CharField(max_length=32, db_index=True)
    customer_uid = models.CharField(max_length=32, unique=True)
    relationship = models.CharField(max_length=20, default="customer")
    tags = models.TextField(default="")
    notes = models.TextField(default="")
    assigned_to_user_uid = models.CharField(max_length=32, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "django_customer_profiles"


class CustomerContact(models.Model):
    contact_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True)
    customer_uid = models.CharField(max_length=32, db_index=True)
    name = models.CharField(max_length=200)
    position = models.CharField(max_length=200, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=80, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "django_customer_contacts"


class DocumentContact(models.Model):
    document = models.OneToOneField(Document, on_delete=models.CASCADE, related_name="crm_link")
    contact = models.ForeignKey(CustomerContact, on_delete=models.SET_NULL, null=True)
    automatic = models.BooleanField(default=False)

    class Meta:
        db_table = "django_document_contacts"


class DocumentNote(models.Model):
    note_uid = models.CharField(max_length=32, unique=True, default=uid)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="internal_notes")
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "django_document_notes"


class Opportunity(models.Model):
    STAGES = [
        ("prospect", "À qualifier"),
        ("qualified", "Qualifiée"),
        ("proposal", "Proposition"),
        ("negotiation", "Négociation"),
        ("won", "Gagnée"),
        ("lost", "Perdue"),
    ]
    opportunity_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True)
    customer_uid = models.CharField(max_length=32, db_index=True)
    title = models.CharField(max_length=240)
    stage = models.CharField(max_length=20, choices=STAGES, default="prospect")
    amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    currency_code = models.CharField(max_length=3, default="EUR")
    expected_close = models.DateField(null=True, blank=True)
    closed_on = models.DateField(null=True, blank=True)
    assigned_to_user_uid = models.CharField(max_length=32, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "django_opportunities"


class CrmActivity(models.Model):
    KINDS = [
        ("note", "Note"),
        ("call", "Appel"),
        ("meeting", "Rendez-vous"),
        ("task", "Tâche / relance"),
        ("email", "Email"),
    ]
    activity_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True)
    customer_uid = models.CharField(max_length=32, db_index=True)
    kind = models.CharField(max_length=20, choices=KINDS, default="note")
    title = models.CharField(max_length=240)
    body = models.TextField(blank=True, default="")
    due_on = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    assigned_to_user_uid = models.CharField(max_length=32, blank=True, default="")
    created_by_user_uid = models.CharField(max_length=32)
    email_uid = models.CharField(max_length=32, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "django_crm_activities"


class EmailConnection(models.Model):
    connection_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, unique=True)
    provider = models.CharField(max_length=20, default="instance")
    version = models.PositiveIntegerField(default=1)
    sender_email = models.EmailField(blank=True, default="")
    sender_name = models.CharField(max_length=200, blank=True, default="")
    smtp_host = models.CharField(max_length=253, blank=True, default="")
    smtp_port = models.PositiveIntegerField(default=587)
    smtp_security = models.CharField(max_length=20, default="starttls")
    smtp_username = models.CharField(max_length=254, blank=True, default="")
    password_encrypted = models.TextField(blank=True, default="")
    client_id = models.CharField(max_length=500, blank=True, default="")
    client_secret_encrypted = models.TextField(blank=True, default="")
    access_token_encrypted = models.TextField(blank=True, default="")
    refresh_token_encrypted = models.TextField(blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)
    account_id = models.CharField(max_length=300, blank=True, default="")
    status = models.CharField(max_length=20, default="unconfigured")
    last_tested_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "django_email_connections"


class EmailRoute(models.Model):
    email_uid = models.CharField(max_length=32, unique=True)
    connection_uid = models.CharField(max_length=32)
    connection_version = models.PositiveIntegerField()

    class Meta:
        db_table = "django_email_routes"


class WebhookEndpoint(models.Model):
    endpoint_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True)
    name = models.CharField(max_length=160)
    url = models.URLField(max_length=2000)
    secret_encrypted = models.TextField()
    events = models.JSONField(default=list)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "django_webhook_endpoints"


class WebhookDelivery(models.Model):
    delivery_uid = models.CharField(max_length=32, unique=True, default=uid)
    endpoint_uid = models.CharField(max_length=32, db_index=True)
    company_uid = models.CharField(max_length=32, db_index=True)
    event_uid = models.CharField(max_length=32)
    event_type = models.CharField(max_length=80)
    payload = models.JSONField()
    status = models.CharField(max_length=20, default="queued", db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    last_status_code = models.PositiveIntegerField(null=True)
    last_error = models.CharField(max_length=500, blank=True, default="")
    delivered_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "django_webhook_deliveries"
        constraints = [
            models.UniqueConstraint(
                fields=["endpoint_uid", "event_uid"],
                name="unique_webhook_event_delivery",
            )
        ]


class CompanyBranding(models.Model):
    DEFAULT_ACCENT = "#2048a8"

    company_uid = models.CharField(max_length=32, unique=True)
    display_name = models.CharField(max_length=120, blank=True, default="")
    accent_color = models.CharField(max_length=7, default=DEFAULT_ACCENT)
    logo_path = models.TextField(blank=True, default="")
    logo_sha256 = models.CharField(max_length=64, blank=True, default="")
    document_layout = models.CharField(max_length=20, default="classic")
    default_currency = models.CharField(max_length=3, default="EUR")
    payment_due_days = models.PositiveSmallIntegerField(default=30)
    quote_validity_days = models.PositiveSmallIntegerField(default=30)
    reply_to = models.EmailField(blank=True, default="")
    email_signature = models.TextField(blank=True, default="")
    email_templates = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "django_company_branding"


class CompanyNumberFormat(models.Model):
    company = models.OneToOneField(Company, on_delete=models.CASCADE, primary_key=True, related_name="number_format")
    number_grouping = models.CharField(max_length=8)
    number_decimal = models.CharField(max_length=8)

    class Meta:
        db_table = "django_company_number_formats"


class UserPreferences(models.Model):
    user_uid = models.CharField(max_length=32, unique=True)
    theme = models.CharField(max_length=10, default="system")
    density = models.CharField(max_length=15, default="comfortable")
    number_grouping = models.CharField(max_length=8, blank=True, default="")
    number_decimal = models.CharField(max_length=8, blank=True, default="")

    class Meta:
        db_table = "django_user_preferences"


class EmailEnvelope(models.Model):
    email_uid = models.CharField(max_length=32, unique=True)
    reply_to = models.EmailField(blank=True, default="")

    class Meta:
        db_table = "django_email_envelopes"


class UserSecurity(models.Model):
    user_uid = models.CharField(max_length=32, unique=True)
    totp_secret_encrypted = models.TextField(blank=True, default="")
    recovery_hashes = models.JSONField(default=list)
    last_totp_counter = models.BigIntegerField(default=-1)
    enabled_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "django_user_security"


class OAuthAttempt(models.Model):
    state_hash = models.CharField(max_length=64, primary_key=True)
    company_uid = models.CharField(max_length=32, db_index=True)
    user_uid = models.CharField(max_length=32)
    provider = models.CharField(max_length=20)
    version = models.PositiveIntegerField()
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "django_oauth_attempts"


class AuditEvent(models.Model):
    company_uid = models.CharField(max_length=32, blank=True, default="", db_index=True)
    user_uid = models.CharField(max_length=32, blank=True, default="")
    action = models.CharField(max_length=80)
    target_uid = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "django_audit_events"


class PdpSyncState(models.Model):
    company_uid = models.CharField(max_length=32, unique=True)
    enabled = models.BooleanField(default=False)
    requested = models.BooleanField(default=False)
    remote_company_id = models.CharField(max_length=20, blank=True, default="")
    remote_environment = models.CharField(max_length=20, blank=True, default="")
    invoice_cursor = models.BigIntegerField(default=0)
    event_cursor = models.BigIntegerField(default=0)
    auth_mode = models.CharField(max_length=20, default="auto")
    version = models.PositiveIntegerField(default=1)
    lease = models.CharField(max_length=32, blank=True, default="")
    lease_until = models.DateTimeField(null=True)
    next_sync_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_success_at = models.DateTimeField(null=True)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        db_table = "django_pdp_sync_states"


class PdpInvoice(models.Model):
    company_uid = models.CharField(max_length=32, db_index=True)
    remote_id = models.CharField(max_length=20)
    remote_company_id = models.CharField(max_length=20)
    remote_environment = models.CharField(max_length=20)
    document_uid = models.CharField(max_length=32, null=True, unique=True)
    direction = models.CharField(max_length=8)
    invoice_number = models.TextField()
    imported = models.BooleanField(default=False)
    original_file_uid = models.CharField(max_length=32)
    readable_file_uid = models.CharField(max_length=32, blank=True, default="")
    payload = models.JSONField(default=dict)
    status_codes = models.JSONField(default=list)
    latest_event_id = models.BigIntegerField(default=0)
    latest_status = models.CharField(max_length=80, blank=True, default="")
    synced_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "django_pdp_invoices"
        constraints = [
            models.UniqueConstraint(
                fields=["company_uid", "remote_id"], name="unique_pdp_remote_invoice"
            )
        ]


class PdpRemoteEvent(models.Model):
    company_uid = models.CharField(max_length=32, db_index=True)
    remote_id = models.BigIntegerField()
    remote_invoice_id = models.CharField(max_length=20)
    document_uid = models.CharField(max_length=32, db_index=True)
    status_code = models.CharField(max_length=80)
    payload = models.JSONField()
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "django_pdp_remote_events"
        constraints = [
            models.UniqueConstraint(
                fields=["company_uid", "remote_id"], name="unique_pdp_remote_event"
            )
        ]


class PdpOutbox(models.Model):
    operation_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True)
    document_uid = models.CharField(max_length=32, db_index=True)
    request_key = models.CharField(max_length=80)
    operation = models.CharField(max_length=12)
    remote_company_id = models.CharField(max_length=20)
    remote_environment = models.CharField(max_length=20)
    file_uid = models.CharField(max_length=32, blank=True, default="")
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=12, default="queued", db_index=True)
    remote_id = models.CharField(max_length=20, blank=True, default="")
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "django_pdp_outbox"
        constraints = [
            models.UniqueConstraint(
                fields=["company_uid", "request_key"], name="unique_pdp_operation"
            )
        ]


class ExpenseClaim(models.Model):
    claim_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True)
    payer = models.CharField(max_length=180)
    month = models.DateField()
    approved = models.BooleanField(default=False)
    created_by = models.CharField(max_length=32)
    approved_by = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "django_expense_claims"
        constraints = [models.UniqueConstraint(fields=["company_uid", "payer", "month"], name="expense_claim_month")]


class ExpenseClaimLine(models.Model):
    claim = models.ForeignKey(ExpenseClaim, on_delete=models.CASCADE, related_name="lines")
    receipt = models.OneToOneField(Ingestion, on_delete=models.PROTECT, related_name="claim_line")
    amount = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    previous_status = models.CharField(max_length=12, blank=True)

    class Meta:
        db_table = "django_expense_claim_lines"


class EvidenceFile(models.Model):
    file_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True)
    digest = models.CharField(max_length=64)
    filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100)
    pages = models.PositiveIntegerField(default=1)
    contents = models.BinaryField()

    class Meta:
        db_table = "django_evidence_files"
        constraints = [models.UniqueConstraint(fields=["company_uid", "digest"], name="evidence_company_digest")]


class EvidenceLink(models.Model):
    source = models.ForeignKey(EvidenceFile, on_delete=models.PROTECT, related_name="links")
    receipt = models.ForeignKey(Ingestion, on_delete=models.CASCADE, null=True, related_name="proofs")
    transaction = models.ForeignKey(BankTransaction, on_delete=models.CASCADE, null=True, related_name="proofs")
    page = models.PositiveIntegerField(default=0)  # Zero means the entire original.

    class Meta:
        db_table = "django_evidence_links"
        constraints = [
            models.CheckConstraint(condition=models.Q(receipt__isnull=False, transaction__isnull=True)
                | models.Q(receipt__isnull=True, transaction__isnull=False), name="evidence_one_target"),
            models.UniqueConstraint(fields=["source", "receipt", "page"], name="evidence_receipt_page"),
            models.UniqueConstraint(fields=["source", "transaction", "page"], name="evidence_transaction_page"),
        ]


class ExpenseWorkbook(models.Model):
    upload_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True)
    digest = models.CharField(max_length=64)
    filename = models.CharField(max_length=255)
    contents = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "django_expense_workbooks"
        constraints = [models.UniqueConstraint(fields=["company_uid", "digest"], name="workbook_company_digest")]


class ExpenseRegisterLine(models.Model):
    company_uid = models.CharField(max_length=32, db_index=True)
    register = models.CharField(max_length=150)
    sheet = models.CharField(max_length=64)
    key = models.CharField(max_length=180)
    row_number = models.PositiveIntegerField()
    workbook = models.ForeignKey(ExpenseWorkbook, on_delete=models.PROTECT, related_name="lines")
    receipt = models.ForeignKey(Ingestion, on_delete=models.PROTECT, related_name="register_lines")
    source_values = models.JSONField(default=dict)

    class Meta:
        db_table = "django_expense_register_lines"
        constraints = [models.UniqueConstraint(fields=["company_uid", "register", "sheet", "key"], name="register_logical_row")]


class StatementControl(models.Model):
    account = models.ForeignKey(BankAccount, on_delete=models.CASCADE, related_name="controls")
    start = models.DateField()
    end = models.DateField()
    opening = models.DecimalField(max_digits=24, decimal_places=2)
    closing = models.DecimalField(max_digits=24, decimal_places=2)
    debits = models.DecimalField(max_digits=24, decimal_places=2, null=True)
    credits = models.DecimalField(max_digits=24, decimal_places=2, null=True)
    reference = models.CharField(max_length=255)
    source = models.ForeignKey(EvidenceFile, on_delete=models.PROTECT, null=True)
    updated_by = models.CharField(max_length=32)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "django_statement_controls"
        constraints = [models.UniqueConstraint(fields=["account", "start", "end"], name="statement_control_period")]


class ClosingReviewNote(models.Model):
    receipt = models.OneToOneField(Ingestion, on_delete=models.CASCADE, related_name="closing_review")
    document_date = models.DateField(null=True, blank=True)
    reason = models.CharField(max_length=750)
    next_action = models.CharField(max_length=500)
    updated_by = models.CharField(max_length=32)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "django_closing_review_notes"
