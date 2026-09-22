from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0004_recentwin_caption_alter_recentwin_settled_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="package",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
