import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def migrate_purchase_data(apps, schema_editor):
    Package = apps.get_model("catalog", "Package")
    Purchase = apps.get_model("catalog", "Purchase")
    Payment = apps.get_model("catalog", "Payment")

    for package in Package.objects.all():
        package.closes_at = package.request_deadline or package.commences_at or package.created_at
        if not package.is_active and package.deleted_at is None:
            package.deleted_at = package.updated_at or timezone.now()
        package.save(update_fields=["closes_at", "deleted_at"])

    for payment in Payment.objects.filter(status="refunded"):
        payment.status = "cancelled"
        payment.save(update_fields=["status"])

    duplicate_groups = {}
    for purchase in Purchase.objects.order_by("user_id", "package_id", "-id"):
        duplicate_groups.setdefault((purchase.user_id, purchase.package_id), []).append(purchase)

    for purchases in duplicate_groups.values():
        paid_purchases = [
            purchase for purchase in purchases
            if Payment.objects.filter(purchase_id=purchase.id, status="paid").exists()
        ]
        keeper = paid_purchases[0] if paid_purchases else purchases[0]
        for duplicate in purchases:
            if duplicate.pk == keeper.pk:
                continue
            Payment.objects.filter(purchase_id=duplicate.pk).update(purchase_id=keeper.pk)
            duplicate.delete()

    for purchase in Purchase.objects.select_related("package", "user").order_by("id"):
        purchase.currency_snapshot = purchase.package.currency
        paid_payment = Payment.objects.filter(purchase_id=purchase.pk, status="paid").order_by("paid_at", "id").first()
        if paid_payment:
            purchase.paid_at = paid_payment.paid_at or purchase.approved_at or purchase.starts_at or purchase.updated_at
            purchase.code_snapshot = purchase.package.code
            purchase.link_snapshot = purchase.package.betslip_link
        else:
            purchase.paid_at = None
            purchase.code_snapshot = ""
            purchase.link_snapshot = ""
        purchase.save(update_fields=["currency_snapshot", "paid_at", "code_snapshot", "link_snapshot"])

    for payment in Payment.objects.select_related("user").all():
        phone = "".join(character for character in payment.user.phone if character.isdigit())
        if phone.startswith("0") and len(phone) == 10:
            phone = f"256{phone[1:]}"
        payment.payer_msisdn = f"+{phone}" if phone else ""
        if payment.provider == "manual":
            payment.provider = "legacy"
        payment.save(update_fields=["payer_msisdn", "provider"])


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0005_package_deleted_at"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RenameModel(old_name="Subscription", new_name="Purchase"),
        migrations.RenameField(model_name="purchase", old_name="requested_at", new_name="initiated_at"),
        migrations.RenameField(model_name="payment", old_name="subscription", new_name="purchase"),
        migrations.AlterField(
            model_name="payment",
            name="purchase",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payments", to="catalog.purchase"),
        ),
        migrations.AlterField(
            model_name="purchase",
            name="user",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchases", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name="purchase",
            name="package",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchases", to="catalog.package"),
        ),
        migrations.AddField(model_name="package", name="closes_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="purchase", name="currency_snapshot", field=models.CharField(default="UGX", max_length=8)),
        migrations.AddField(model_name="purchase", name="code_snapshot", field=models.CharField(blank=True, max_length=120)),
        migrations.AddField(model_name="purchase", name="link_snapshot", field=models.URLField(blank=True, max_length=500)),
        migrations.AddField(model_name="purchase", name="paid_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="payment", name="internal_reference", field=models.CharField(blank=True, db_index=True, max_length=80)),
        migrations.AddField(model_name="payment", name="payer_msisdn", field=models.CharField(blank=True, default="", max_length=20)),
        migrations.AddField(model_name="payment", name="provider_message", field=models.CharField(blank=True, max_length=240)),
        migrations.AddField(model_name="payment", name="provider_payload", field=models.JSONField(blank=True, default=dict)),
        migrations.RunPython(migrate_purchase_data, migrations.RunPython.noop),
        migrations.RemoveField(model_name="package", name="commences_at"),
        migrations.RemoveField(model_name="package", name="duration_days"),
        migrations.RemoveField(model_name="package", name="is_active"),
        migrations.RemoveField(model_name="package", name="request_deadline"),
        migrations.AlterField(model_name="package", name="closes_at", field=models.DateTimeField()),
        migrations.AlterField(model_name="package", name="betslip_link", field=models.URLField(max_length=500)),
        migrations.AlterField(model_name="package", name="code", field=models.CharField(max_length=120)),
        migrations.RemoveField(model_name="purchase", name="activation_source"),
        migrations.RemoveField(model_name="purchase", name="approved_at"),
        migrations.RemoveField(model_name="purchase", name="approved_by"),
        migrations.RemoveField(model_name="purchase", name="customer_message"),
        migrations.RemoveField(model_name="purchase", name="expires_at"),
        migrations.RemoveField(model_name="purchase", name="owner_note"),
        migrations.RemoveField(model_name="purchase", name="starts_at"),
        migrations.RemoveField(model_name="purchase", name="status"),
        migrations.AlterField(
            model_name="payment",
            name="provider",
            field=models.CharField(default="relworx", max_length=40),
        ),
        migrations.AlterField(
            model_name="payment",
            name="payer_msisdn",
            field=models.CharField(max_length=20),
        ),
        migrations.AlterField(
            model_name="payment",
            name="status",
            field=models.CharField(choices=[("pending", "Pending"), ("paid", "Paid"), ("failed", "Failed"), ("cancelled", "Cancelled")], default="pending", max_length=20),
        ),
        migrations.AlterModelOptions(name="purchase", options={"ordering": ["-initiated_at", "-id"]}),
        migrations.AddConstraint(
            model_name="purchase",
            constraint=models.UniqueConstraint(fields=("user", "package"), name="unique_user_package_purchase"),
        ),
    ]
