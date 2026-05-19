from __future__ import annotations

from django.db import migrations


def _enable_vector(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("CREATE EXTENSION IF NOT EXISTS vector;")


def _disable_vector(apps, schema_editor):
    return


class Migration(migrations.Migration):

    initial = True
    dependencies: list = []

    operations = [
        migrations.RunPython(_enable_vector, _disable_vector),
    ]
