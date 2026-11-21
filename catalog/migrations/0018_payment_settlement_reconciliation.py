import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0017_subscriptionplan_package_offer_type_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="duplicate_of",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="duplicate_settlements",
                to="catalog.payment",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="settlement_classification",
            field=models.CharField(
                choices=[("primary", "Primary"), ("duplicate", "Duplicate entitlement settlement")],
                db_index=True,
                default="primary",
                max_length=16,
            ),
        ),
        migrations.CreateModel(
            name="ReconciliationRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("job", models.CharField(choices=[("payments", "Payments"), ("payouts", "Payouts")], db_index=True, max_length=20)),
                ("status", models.CharField(choices=[("running", "Running"), ("success", "Success"), ("degraded", "Degraded"), ("failed", "Failed")], db_index=True, default="running", max_length=20)),
                ("started_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("finished_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("scanned_count", models.PositiveIntegerField(default=0)),
                ("outcomes", models.JSONField(blank=True, default=dict)),
                ("error_summary", models.CharField(blank=True, max_length=500)),
            ],
            options={
                "ordering": ["-started_at", "-id"],
                "indexes": [models.Index(fields=["job", "status", "finished_at"], name="reconcile_job_status_idx")],
            },
        ),
    ]
