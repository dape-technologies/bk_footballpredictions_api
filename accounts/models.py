import re

from django.contrib.auth.models import AbstractUser
from django.db import models

from .managers import UserManager


class User(AbstractUser):
    notification_preferences = models.JSONField(default=dict, blank=True)
    username = None
    phone = models.CharField(max_length=20, unique=True)
    date_of_birth = models.DateField(blank=True, null=True)
    is_blocked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["first_name", "last_name", "date_of_birth"]

    objects = UserManager()

    @staticmethod
    def normalize_phone(value):
        raw = re.sub(r"\D", "", str(value or ""))
        if raw.startswith("256") and len(raw) == 12:
            return f"0{raw[3:]}"
        return raw

    def save(self, *args, **kwargs):
        self.phone = self.normalize_phone(self.phone)
        super().save(*args, **kwargs)

    @property
    def is_product_owner(self):
        return self.is_superuser or self.groups.filter(name="Product Owner").exists()

    @property
    def is_developer(self):
        return not self.is_superuser and self.groups.filter(name="Developer").exists()

    @property
    def full_name(self):
        return self.get_full_name().strip()

    def __str__(self):
        return f"{self.full_name} · {self.phone}"


class ActivityLog(models.Model):
    class Category(models.TextChoices):
        AUTHENTICATION = "authentication", "Authentication"
        ACCOUNT = "account", "Account"
        SUBSCRIPTION = "subscription", "Subscription"
        CONTENT = "content", "Content"
        ADMINISTRATION = "administration", "Administration"

    actor = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="activity_logs",
    )
    category = models.CharField(max_length=32, choices=Category.choices)
    action = models.CharField(max_length=64)
    description = models.CharField(max_length=240)
    target_type = models.CharField(max_length=80, blank=True)
    target_id = models.CharField(max_length=80, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.action} · {self.description}"


class SecurityChallenge(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    purpose = models.CharField(max_length=30)
    session_digest = models.CharField(max_length=64)
    binding = models.CharField(max_length=160)
    code_hash = models.CharField(max_length=128)
    code_ciphertext = models.TextField(blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class PrivilegedSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="privileged_sessions")
    session_digest = models.CharField(max_length=64, unique=True)
    device = models.CharField(max_length=300, blank=True)
    ip_address = models.GenericIPAddressField(null=True)
    started_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True)


class EmailNotice(models.Model):
    dedup_key = models.CharField(max_length=180, unique=True)
    recipients = models.JSONField(default=list)
    subject = models.CharField(max_length=180)
    body = models.TextField()
    sent_at = models.DateTimeField(null=True)
    attempts = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
