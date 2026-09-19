from datetime import timedelta

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Package(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True)
    description = models.TextField()
    price = models.DecimalField(max_digits=12, decimal_places=0, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=8, default="UGX")
    duration_days = models.PositiveIntegerField(default=1)
    access_label = models.CharField(max_length=80, default="Premium predictions")
    benefits = models.JSONField(default=list, blank=True)
    image = models.ImageField(upload_to="packages/", blank=True, null=True)
    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)
    request_deadline = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "price", "name"]

    @property
    def is_open(self):
        return self.is_active and (not self.request_deadline or self.request_deadline > timezone.now())

    def __str__(self):
        return self.name


class Subscription(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscriptions")
    package = models.ForeignKey(Package, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    price_snapshot = models.DecimalField(max_digits=12, decimal_places=0)
    activation_source = models.CharField(max_length=20, default="manual")
    requested_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(blank=True, null=True)
    starts_at = models.DateTimeField(blank=True, null=True)
    expires_at = models.DateTimeField(blank=True, null=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="approved_subscriptions",
    )
    owner_note = models.TextField(blank=True)
    customer_message = models.CharField(max_length=240, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-requested_at"]

    def activate(self, owner):
        now = timezone.now()
        self.status = self.Status.ACTIVE
        self.approved_by = owner
        self.approved_at = now
        self.starts_at = now
        self.expires_at = now + timedelta(days=self.package.duration_days)
        self.save()

    @property
    def grants_access(self):
        return self.status == self.Status.ACTIVE and self.expires_at and self.expires_at > timezone.now()

    def __str__(self):
        return f"{self.user} · {self.package} · {self.status}"


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
    title = models.CharField(max_length=160)
    summary = models.TextField()
    odds = models.DecimalField(max_digits=7, decimal_places=2, blank=True, null=True)
    settled_at = models.DateTimeField()
    image = models.ImageField(upload_to="wins/", blank=True, null=True)
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-settled_at"]

    def __str__(self):
        return self.title


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

