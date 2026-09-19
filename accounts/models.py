import re

from django.contrib.auth.models import AbstractUser
from django.db import models

from .managers import UserManager


class User(AbstractUser):
    username = None
    phone = models.CharField(max_length=20, unique=True)
    display_name = models.CharField(max_length=120)
    is_blocked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["display_name"]

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

    def __str__(self):
        return f"{self.display_name} · {self.phone}"

