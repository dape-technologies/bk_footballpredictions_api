from uuid import uuid4
from pathlib import Path
from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from .storage import PrivatePackageStorage


def paid_package_image_upload_to(instance, filename):
    suffix = Path(filename).suffix.lower()[:10]
    return f"packages/paid/{uuid4().hex}{suffix}"


def free_pick_image_upload_to(instance, filename):
    suffix = Path(filename).suffix.lower()[:10]
    return f"free-picks/{uuid4().hex}{suffix}"


class Package(models.Model):
    class OfferType(models.TextChoices):
        ONE_OFF = "one_off", "One-off package"
        WEEKLY = "weekly", "Weekly plan content"

    image_optimized = models.ImageField(upload_to="packages/optimized/", blank=True, null=True, editable=False)
    paid_image_optimized = models.ImageField(
        storage=PrivatePackageStorage(), upload_to="packages/paid/optimized/",
        blank=True, null=True, editable=False,
    )
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True)
    offer_type = models.CharField(max_length=16, choices=OfferType.choices, default=OfferType.ONE_OFF, db_index=True)
    package_type = models.CharField(max_length=80, default="Accumulator")
    description = models.TextField(blank=True, default="")
    price = models.DecimalField(max_digits=12, decimal_places=0, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=8, default="UGX")
    win_probability = models.PositiveSmallIntegerField(
        default=70,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    closes_at = models.DateTimeField()
    betslip_link = models.URLField(max_length=500)
    code = models.CharField(max_length=120)
    access_label = models.CharField(max_length=80, default="Premium predictions")
    benefits = models.JSONField(default=list, blank=True)
    image = models.ImageField(upload_to="packages/", blank=True, null=True)
    paid_image = models.ImageField(
        storage=PrivatePackageStorage(), upload_to=paid_package_image_upload_to,
        blank=True, null=True,
    )
    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)
    deleted_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "price", "name"]

    @property
    def is_open(self):
        return self.is_active and self.deleted_at is None and self.closes_at > timezone.now()

    def __str__(self):
        return self.name


class FreePick(models.Model):
    name = models.CharField(max_length=100)
    package_type = models.CharField(max_length=80, default="Accumulator")
    win_probability = models.PositiveSmallIntegerField(
        default=70,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    closes_at = models.DateTimeField()
    betslip_link = models.URLField(max_length=500)
    code = models.CharField(max_length=120)
    reveal_image = models.ImageField(
        storage=PrivatePackageStorage(),
        upload_to=free_pick_image_upload_to,
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="created_free_picks",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    @property
    def is_live(self):
        return self.is_active and self.closes_at > timezone.now()

    def __str__(self):
        return self.name


class Purchase(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchases")
    package = models.ForeignKey(Package, on_delete=models.PROTECT, related_name="purchases")
    price_snapshot = models.DecimalField(max_digits=12, decimal_places=0)
    currency_snapshot = models.CharField(max_length=8, default="UGX")
    code_snapshot = models.CharField(max_length=120, blank=True)
    link_snapshot = models.URLField(max_length=500, blank=True)
    paid_image_snapshot = models.CharField(max_length=500, blank=True)
    initiated_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-initiated_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["user", "package"], name="unique_user_package_purchase"),
        ]

    @property
    def is_paid(self):
        return self.paid_at is not None

    @property
    def status(self):
        if self.is_paid:
            return Payment.Status.PAID
        cached = getattr(self, "_prefetched_objects_cache", {}).get("payments")
        latest = max(cached, key=lambda item: (item.created_at, item.id)) if cached else self.payments.order_by("-created_at", "-id").first()
        return latest.status if latest else "unpaid"

    def complete(self, paid_at=None):
        if self.is_paid:
            return False
        self.paid_at = paid_at or timezone.now()
        self.code_snapshot = self.package.code
        self.link_snapshot = self.package.betslip_link
        paid_image = self.package.paid_image_optimized or self.package.paid_image
        self.paid_image_snapshot = paid_image.name if paid_image else ""
        self.save(update_fields=["paid_at", "code_snapshot", "link_snapshot", "paid_image_snapshot", "updated_at"])
        return True

    def __str__(self):
        return f"{self.user} · {self.package}"


class SubscriptionPlan(models.Model):
    key = models.SlugField(max_length=40, unique=True, default="weekly")
    name = models.CharField(max_length=100, default="Weekly Plan")
    description = models.TextField(blank=True, default="Seven days of access to every weekly subscriber package.")
    price = models.DecimalField(max_digits=12, decimal_places=0, validators=[MinValueValidator(500)])
    currency = models.CharField(max_length=8, default="UGX")
    duration_days = models.PositiveSmallIntegerField(default=7, validators=[MinValueValidator(1)])
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return self.name


class SubscriptionOrder(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="subscription_orders")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="orders")
    price_snapshot = models.DecimalField(max_digits=12, decimal_places=0)
    currency_snapshot = models.CharField(max_length=8, default="UGX")
    duration_days_snapshot = models.PositiveSmallIntegerField(default=7)
    initiated_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(blank=True, null=True)
    access_starts_at = models.DateTimeField(blank=True, null=True, db_index=True)
    access_ends_at = models.DateTimeField(blank=True, null=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-initiated_at", "-id"]

    @property
    def is_paid(self):
        return self.paid_at is not None

    @property
    def is_active(self):
        now = timezone.now()
        return bool(self.access_starts_at and self.access_ends_at and self.access_starts_at <= now < self.access_ends_at)

    @property
    def status(self):
        if self.is_active:
            return "active"
        if self.is_paid:
            return "expired"
        cached = getattr(self, "_prefetched_objects_cache", {}).get("payments")
        latest = max(cached, key=lambda item: (item.created_at, item.id)) if cached else self.payments.order_by("-created_at", "-id").first()
        return latest.status if latest else "unpaid"

    def complete(self, paid_at=None):
        if self.is_paid:
            return False
        starts_at = paid_at or timezone.now()
        self.paid_at = starts_at
        self.access_starts_at = starts_at
        self.access_ends_at = starts_at + timedelta(days=self.duration_days_snapshot)
        self.save(update_fields=["paid_at", "access_starts_at", "access_ends_at", "updated_at"])
        eligible = Package.objects.filter(
            offer_type=Package.OfferType.WEEKLY,
            deleted_at__isnull=True,
            is_active=True,
            closes_at__gt=starts_at,
        )
        SubscriptionContentGrant.objects.bulk_create(
            [SubscriptionContentGrant(order=self, package=package) for package in eligible],
            ignore_conflicts=True,
        )
        return True

    def __str__(self):
        return f"{self.user} · {self.plan} · {self.status}"


class SubscriptionContentGrant(models.Model):
    order = models.ForeignKey(SubscriptionOrder, on_delete=models.PROTECT, related_name="content_grants")
    package = models.ForeignKey(Package, on_delete=models.PROTECT, related_name="subscription_grants")
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-granted_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["order", "package"], name="unique_subscription_content_grant"),
        ]

    def __str__(self):
        return f"{self.order} · {self.package}"


def payment_reference():
    return f"BKP-{uuid4().hex[:12].upper()}"


def receipt_number():
    return f"BK-RCP-{uuid4().hex[:12].upper()}"


class Payment(models.Model):
    class SettlementClassification(models.TextChoices):
        PRIMARY = "primary", "Primary"
        DUPLICATE = "duplicate", "Duplicate entitlement settlement"

    class Status(models.TextChoices):
        CREATED = "created", "Created"
        SUBMITTED = "submitted", "Submitted"
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"
        NEEDS_REVIEW = "needs_review", "Needs review"

    class FailureCategory(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        PROVIDER = "provider", "Provider"
        SYSTEM = "system", "System"
        VALIDATION = "validation", "Validation"

    ACTIVE_STATUSES = (Status.CREATED, Status.SUBMITTED, Status.PENDING, Status.NEEDS_REVIEW, Status.EXPIRED)
    TERMINAL_STATUSES = (Status.PAID, Status.FAILED, Status.CANCELLED)

    purchase = models.ForeignKey(Purchase, on_delete=models.PROTECT, related_name="payments", blank=True, null=True)
    subscription_order = models.ForeignKey(
        SubscriptionOrder, on_delete=models.PROTECT, related_name="payments", blank=True, null=True,
    )
    duplicate_of = models.ForeignKey(
        "self", on_delete=models.PROTECT, related_name="duplicate_settlements", blank=True, null=True,
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments")
    package = models.ForeignKey(Package, on_delete=models.PROTECT, related_name="payments", blank=True, null=True)
    amount = models.DecimalField(max_digits=12, decimal_places=0, validators=[MinValueValidator(0)])
    base_amount = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    provider_fee_estimate = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    provider_net_amount = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    developer_commission = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=8, default="UGX")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED, db_index=True)
    settlement_classification = models.CharField(
        max_length=16, choices=SettlementClassification.choices,
        default=SettlementClassification.PRIMARY, db_index=True,
    )
    reference = models.CharField(max_length=24, unique=True, default=payment_reference, editable=False)
    internal_reference = models.CharField(max_length=80, blank=True, null=True, unique=True)
    provider = models.CharField(max_length=40, default="relworx")
    provider_transaction_id = models.CharField(max_length=120, blank=True, db_index=True)
    provider_charge = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    payer_msisdn = models.CharField(max_length=20)
    provider_message = models.CharField(max_length=240, blank=True)
    provider_payload = models.JSONField(default=dict, blank=True)
    failure_category = models.CharField(max_length=20, choices=FailureCategory.choices, blank=True)
    failure_code = models.CharField(max_length=80, blank=True)
    failure_reason = models.TextField(blank=True)
    failed_at = models.DateTimeField(blank=True, null=True)
    submitted_at = models.DateTimeField(blank=True, null=True)
    confirmed_at = models.DateTimeField(blank=True, null=True, db_index=True)
    last_checked_at = models.DateTimeField(blank=True, null=True)
    check_attempts = models.PositiveIntegerField(default=0)
    reconciliation_next_at = models.DateTimeField(blank=True, null=True, db_index=True)
    receipt_number = models.CharField(max_length=32, unique=True, blank=True, null=True)
    receipt_issued_at = models.DateTimeField(blank=True, null=True)
    paid_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="payment_status_created_idx"),
            models.Index(fields=["failure_category", "failed_at"], name="payment_failure_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(purchase__isnull=False, subscription_order__isnull=True)
                    | models.Q(purchase__isnull=True, subscription_order__isnull=False)
                ),
                name="payment_has_one_checkout_target",
            ),
        ]

    @property
    def checkout_expires_at(self):
        return self.created_at + timedelta(minutes=max(1, settings.PAYMENT_PENDING_EXPIRY_MINUTES))

    @property
    def can_retry(self):
        return self.status in (self.Status.FAILED, self.Status.CANCELLED)

    @property
    def product_name(self):
        if self.package_id:
            return self.package.name
        if self.subscription_order_id:
            return self.subscription_order.plan.name
        return "BK access"

    def issue_receipt(self):
        if self.status != self.Status.PAID:
            return False
        changed = []
        if not self.receipt_number:
            self.receipt_number = receipt_number()
            changed.append("receipt_number")
        if not self.receipt_issued_at:
            self.receipt_issued_at = timezone.now()
            changed.append("receipt_issued_at")
        if changed:
            changed.append("updated_at")
            self.save(update_fields=changed)
        return bool(changed)

    def __str__(self):
        return f"{self.reference} · {self.user} · {self.status}"


class PaymentEvent(models.Model):
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="events")
    event_type = models.CharField(max_length=64)
    source = models.CharField(max_length=32, default="system")
    status = models.CharField(max_length=20, blank=True)
    message = models.CharField(max_length=240, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["payment", "created_at"], name="payment_event_time_idx")]

    def __str__(self):
        return f"{self.payment.reference} · {self.event_type}"


class PaymentAlert(models.Model):
    class Severity(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="alerts", blank=True, null=True)
    alert_key = models.CharField(max_length=120, unique=True)
    severity = models.CharField(max_length=12, choices=Severity.choices, default=Severity.WARNING)
    title = models.CharField(max_length=120)
    message = models.CharField(max_length=500)
    resolved_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["resolved_at", "severity"], name="payment_alert_open_idx")]

    @property
    def is_resolved(self):
        return self.resolved_at is not None

    def __str__(self):
        return self.title


class ReconciliationRun(models.Model):
    class Job(models.TextChoices):
        PAYMENTS = "payments", "Payments"
        PAYOUTS = "payouts", "Payouts"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        DEGRADED = "degraded", "Degraded"
        FAILED = "failed", "Failed"

    job = models.CharField(max_length=20, choices=Job.choices, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING, db_index=True)
    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    finished_at = models.DateTimeField(blank=True, null=True, db_index=True)
    scanned_count = models.PositiveIntegerField(default=0)
    outcomes = models.JSONField(default=dict, blank=True)
    error_summary = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-started_at", "-id"]
        indexes = [models.Index(fields=["job", "status", "finished_at"], name="reconcile_job_status_idx")]

    def __str__(self):
        return f"{self.job} · {self.status} · {self.started_at}"


def payout_reference():
    return f"BK-PAY-{uuid4().hex[:12].upper()}"


class WalletAccount(models.Model):
    destination_msisdn = models.CharField(max_length=30, blank=True)
    payouts_frozen = models.BooleanField(default=False)
    class Beneficiary(models.TextChoices):
        OWNER = "owner", "Owner"
        DEVELOPER = "developer", "Developer"

    beneficiary = models.CharField(max_length=20, choices=Beneficiary.choices)
    provider = models.CharField(max_length=30, default="relworx")
    currency = models.CharField(max_length=8, default="UGX")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["beneficiary", "provider", "currency"], name="unique_wallet_account")]

    def __str__(self):
        return f"{self.beneficiary} · {self.provider} · {self.currency}"


class JourneyEvent(models.Model):
    event_id = models.CharField(max_length=64, unique=True)
    visitor = models.CharField(max_length=64, db_index=True)
    kind = models.CharField(max_length=30, db_index=True)
    package = models.ForeignKey(Package, on_delete=models.PROTECT, null=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    purchase = models.ForeignKey(Purchase, on_delete=models.SET_NULL, null=True)
    device = models.CharField(max_length=12, default="unknown")
    browser = models.CharField(max_length=20, default="unknown")
    customer_type = models.CharField(max_length=12, default="new")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class WebVitalSample(models.Model):
    sample_id = models.CharField(max_length=100, unique=True)
    metric = models.CharField(max_length=4)
    value = models.FloatField()
    device = models.CharField(max_length=12)
    page = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class PayoutRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    reference = models.CharField(max_length=50, unique=True, default=payout_reference, editable=False)
    client_token = models.CharField(max_length=100, unique=True, blank=True, null=True)
    wallet_account = models.ForeignKey(WalletAccount, on_delete=models.PROTECT, related_name="payout_requests")
    beneficiary = models.CharField(max_length=20, choices=WalletAccount.Beneficiary.choices, db_index=True)
    provider = models.CharField(max_length=30, default="relworx", db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=8, default="UGX")
    destination_msisdn = models.CharField(max_length=30)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="payout_requests")
    provider_request_id = models.CharField(max_length=200, blank=True)
    transaction_id = models.CharField(max_length=200, blank=True)
    provider_fee_actual = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    failure_reason = models.TextField(blank=True)
    reconciliation_next_at = models.DateTimeField(blank=True, null=True, db_index=True)
    check_attempts = models.PositiveIntegerField(default=0)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.reference} · {self.status}"


class WalletLedgerEntry(models.Model):
    class Type(models.TextChoices):
        PAYMENT_CREDIT = "payment_credit", "Payment credit"
        COMMISSION_CREDIT = "commission_credit", "Commission credit"
        PAYOUT_RESERVATION = "payout_reservation", "Payout reservation"
        PAYOUT_DEBIT = "payout_debit", "Payout debit"
        PAYOUT_RELEASE = "payout_release", "Payout release"
        PAYOUT_FEE = "payout_fee", "Payout fee"

    wallet_account = models.ForeignKey(WalletAccount, on_delete=models.PROTECT, related_name="entries")
    payment = models.ForeignKey(Payment, on_delete=models.SET_NULL, blank=True, null=True, related_name="wallet_entries")
    payout_request = models.ForeignKey(PayoutRequest, on_delete=models.SET_NULL, blank=True, null=True, related_name="ledger_entries")
    entry_type = models.CharField(max_length=30, choices=Type.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    idempotency_key = models.CharField(max_length=180, unique=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["wallet_account", "created_at"], name="wallet_entry_time_idx")]

    def __str__(self):
        return f"{self.wallet_account} · {self.entry_type} · {self.amount}"


class Prediction(models.Model):
    class Access(models.TextChoices):
        FREE = "free", "Free"
        PREMIUM = "premium", "Premium"

    class Result(models.TextChoices):
        PENDING = "pending", "Pending"
        WON = "won", "Won"
        LOST = "lost", "Lost"
        VOID = "void", "Void"

    home_team = models.CharField(max_length=120)
    away_team = models.CharField(max_length=120)
    competition = models.CharField(max_length=120)
    kickoff_at = models.DateTimeField()
    access_level = models.CharField(max_length=12, choices=Access.choices, default=Access.FREE)
    package = models.ForeignKey(Package, on_delete=models.PROTECT, blank=True, null=True, related_name="predictions")
    market = models.CharField(max_length=120)
    selection = models.CharField(max_length=160)
    odds = models.DecimalField(max_digits=7, decimal_places=2, blank=True, null=True)
    confidence = models.PositiveSmallIntegerField(default=70)
    analysis = models.TextField(blank=True)
    betslip_reference = models.CharField(max_length=120, blank=True)
    result = models.CharField(max_length=12, choices=Result.choices, default=Result.PENDING)
    score = models.CharField(max_length=30, blank=True)
    is_published = models.BooleanField(default=False)
    published_at = models.DateTimeField(blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="created_predictions")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kickoff_at", "id"]

    def save(self, *args, **kwargs):
        if self.is_published and not self.published_at:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.home_team} v {self.away_team}"


class RecentWin(models.Model):
    caption = models.TextField(blank=True, default="")
    title = models.CharField(max_length=160, blank=True, default="")
    summary = models.TextField(blank=True, default="")
    odds = models.DecimalField(max_digits=7, decimal_places=2, blank=True, null=True)
    settled_at = models.DateTimeField(default=timezone.now)
    image = models.ImageField(upload_to="wins/", blank=True, null=True)
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-settled_at"]

    def __str__(self):
        return self.caption[:80] or self.title or f"Recent win {self.pk}"


class Testimonial(models.Model):
    member_name = models.CharField(max_length=100)
    quote = models.TextField()
    member_since = models.CharField(max_length=80, blank=True)
    avatar = models.ImageField(upload_to="testimonials/", blank=True, null=True)
    is_published = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "-created_at"]

    def __str__(self):
        return self.member_name
