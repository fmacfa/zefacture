"""Legacy table/column names retained so every imported field has an explicit home."""

from django.db import models
from django.utils import timezone
from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from .common import uid, stamp, unpack


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


class Customer(models.Model):
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
                condition=models.Q(number__isnull=False) & ~models.Q(number=""),
                name="unique_company_document_number",
            )
        ]


class DocumentLine(models.Model):
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
            for m in StockMovement.objects.filter(item_uid=self.item_uid)
        )

    class Meta:
        db_table = "inventory_items"


class StockMovement(models.Model):
    movement_uid = models.CharField(max_length=32, unique=True, default=uid)
    item_uid = models.CharField(max_length=32, db_index=True, default="")
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


class Ingestion(models.Model):
    ingestion_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True, default="")
    uploaded_by_user_uid = models.CharField(max_length=32, db_index=True, default="")
    document_uid = models.CharField(max_length=32, db_index=True, null=True, blank=True)
    source_kind = models.TextField(default="supplier_invoice")
    expense_date = models.TextField(null=True, blank=True)
    expense_category = models.TextField(null=True, blank=True)
    expense_description = models.TextField(null=True, blank=True)
    merchant_name = models.TextField(null=True, blank=True)
    expense_amount = models.DecimalField(
        max_digits=30, decimal_places=8, null=True, blank=True
    )
    expense_currency = models.TextField(default="EUR")
    expense_status = models.TextField(default="pending")
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
    key_uid = models.CharField(max_length=32, unique=True, default=uid)
    company_uid = models.CharField(max_length=32, db_index=True, default="")
    name = models.TextField(default="")
    key_hash = models.TextField(default="")
    prefix = models.TextField(default="")
    status = models.TextField(default="active")
    created_at = models.TextField(default=stamp)
    revoked_at = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "api_keys"


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
            )
        ]


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
    company_uid = models.CharField(max_length=32, unique=True)
    display_name = models.CharField(max_length=120, blank=True, default="")
    accent_color = models.CharField(max_length=7, default="#176f60")
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


class UserPreferences(models.Model):
    user_uid = models.CharField(max_length=32, unique=True)
    theme = models.CharField(max_length=10, default="system")
    density = models.CharField(max_length=15, default="comfortable")

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
