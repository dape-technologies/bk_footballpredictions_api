from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0014_package_paid_image_package_paid_image_optimized_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="package",
            name="is_free",
            field=models.BooleanField(default=False),
        ),
    ]
