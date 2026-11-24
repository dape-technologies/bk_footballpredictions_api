import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0005_secure_otp_visibility"),
    ]

    operations = [
        migrations.CreateModel(
            name="RecoveryProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("private_phrase_hash", models.CharField(max_length=128)),
                ("life_phrase_hash", models.CharField(max_length=128)),
                ("prompt_version", models.PositiveSmallIntegerField(default=1)),
                ("configured_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="recovery_profile", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="PasswordRecoveryChallenge",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("is_decoy", models.BooleanField(default=False)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("device", models.CharField(blank=True, max_length=300)),
                ("expires_at", models.DateTimeField()),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="password_recovery_challenges", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "indexes": [
                    models.Index(fields=["public_id", "expires_at"], name="accounts_pa_public__2cc6c7_idx"),
                    models.Index(fields=["user", "created_at"], name="accounts_pa_user_id_aa5985_idx"),
                ],
            },
        ),
    ]
