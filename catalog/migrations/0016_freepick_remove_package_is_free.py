import catalog.models
import catalog.storage
import django.core.validators
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone


def migrate_package_free_picks(apps, schema_editor):
    Package = apps.get_model("catalog", "Package")
    FreePick = apps.get_model("catalog", "FreePick")
    for package in Package.objects.filter(is_free=True, deleted_at__isnull=True).iterator():
        reveal_image = str(package.paid_image_optimized or package.paid_image or "")
        if not reveal_image:
            continue
        FreePick.objects.create(
            name=package.name,
            package_type=package.package_type,
            win_probability=package.win_probability,
            closes_at=package.closes_at,
            betslip_link=package.betslip_link,
            code=package.code,
            reveal_image=reveal_image,
            is_active=package.is_active,
        )
        package.is_active = False
        package.deleted_at = timezone.now()
        package.save(update_fields=["is_active", "deleted_at", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("catalog", "0015_package_is_free"),
    ]

    operations = [
        migrations.CreateModel(
            name="FreePick",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("package_type", models.CharField(default="Accumulator", max_length=80)),
                ("win_probability", models.PositiveSmallIntegerField(default=70, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(100)])),
                ("closes_at", models.DateTimeField()),
                ("betslip_link", models.URLField(max_length=500)),
                ("code", models.CharField(max_length=120)),
                ("reveal_image", models.ImageField(storage=catalog.storage.PrivatePackageStorage(), upload_to=catalog.models.free_pick_image_upload_to)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_free_picks", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.RunPython(migrate_package_free_picks, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="package",
            name="is_free",
        ),
    ]
