from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("clinica", "0004_urgencia"),
    ]

    operations = [
        migrations.DeleteModel(
            name="Urgencia",
        ),
    ]
