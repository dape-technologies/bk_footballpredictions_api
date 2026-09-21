import re

from django.contrib.auth.models import AbstractUser
from django.db import models

from .managers import UserManager


class User(AbstractUser):
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
