from uuid import uuid4

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class Package(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True)
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
    is_featured = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)
    deleted_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "price", "name"]

    @property
    def is_open(self):
        return self.deleted_at is None and self.closes_at > timezone.now()

    def __str__(self):
        return self.name


class Purchase(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchases")
    package = models.ForeignKey(Package, on_delete=models.PROTECT, related_name="purchases")
    price_snapshot = models.DecimalField(max_digits=12, decimal_places=0)
    currency_snapshot = models.CharField(max_length=8, default="UGX")
    code_snapshot = models.CharField(max_length=120, blank=True)
    link_snapshot = models.URLField(max_length=500, blank=True)
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
        self.save(update_fields=["paid_at", "code_snapshot", "link_snapshot", "updated_at"])
        return True

    def __str__(self):
        return f"{self.user} · {self.package}"


def payment_reference():
    return f"BKP-{uuid4().hex[:12].upper()}"


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    purchase = models.ForeignKey(Purchase, on_delete=models.PROTECT, related_name="payments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments")
    package = models.ForeignKey(Package, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=0, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=8, default="UGX")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reference = models.CharField(max_length=24, unique=True, default=payment_reference, editable=False)
    internal_reference = models.CharField(max_length=80, blank=True, db_index=True)
    provider = models.CharField(max_length=40, default="relworx")
    payer_msisdn = models.CharField(max_length=20)
    provider_message = models.CharField(max_length=240, blank=True)
    provider_payload = models.JSONField(default=dict, blank=True)
    paid_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.reference} · {self.user} · {self.status}"


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
