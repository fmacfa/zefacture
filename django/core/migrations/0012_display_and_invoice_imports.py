import json

from django.db import migrations, models

from core.services.invoice_match import invoice_keys_v1


def index_saved_invoices(apps, schema_editor):
    records = apps.get_model("core", "Ingestion").objects.using(
        schema_editor.connection.alias
    )
    pending = []
    rows = (
        records.exclude(extraction_json__isnull=True)
        .exclude(extraction_json="")
        .only("pk", "source_kind", "extraction_json")
    )
    for row in rows.iterator(chunk_size=500):
        try:
            data = json.loads(row.extraction_json)
        except (ValueError, RecursionError):
            continue
        row.source_invoice_ref, row.source_invoice_match = invoice_keys_v1(
            row.source_kind, data
        )
        if row.source_invoice_ref or row.source_invoice_match:
            pending.append(row)
        if len(pending) == 500:
            records.bulk_update(pending, ["source_invoice_ref", "source_invoice_match"])
            pending.clear()
    if pending:
        records.bulk_update(pending, ["source_invoice_ref", "source_invoice_match"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0011_bank_feeds_and_automatic_invoice_contacts"),
    ]

    operations = [
        migrations.AddField(
            model_name="ingestion",
            name="batch_uid",
            field=models.CharField(
                blank=True, db_index=True, default="", max_length=32
            ),
        ),
        migrations.AddField(
            model_name="ingestion",
            name="source_invoice_ref",
            field=models.CharField(
                blank=True, db_index=True, default="", max_length=140
            ),
        ),
        migrations.AddField(
            model_name="ingestion",
            name="source_invoice_match",
            field=models.CharField(
                blank=True, db_index=True, default="", max_length=64
            ),
        ),
        migrations.AddField(
            model_name="userpreferences",
            name="number_decimal",
            field=models.CharField(blank=True, default="", max_length=8),
        ),
        migrations.AddField(
            model_name="userpreferences",
            name="number_grouping",
            field=models.CharField(blank=True, default="", max_length=8),
        ),
        migrations.AddConstraint(
            model_name="document",
            constraint=models.UniqueConstraint(
                fields=("company_uid", "number"),
                condition=~models.Q(direction="incoming")
                & models.Q(number__isnull=False)
                & ~models.Q(number=""),
                name="unique_issued_document_number",
            ),
        ),
        migrations.AddConstraint(
            model_name="document",
            constraint=models.UniqueConstraint(
                fields=("company_uid", "customer_uid", "number"),
                condition=models.Q(direction="incoming", number__isnull=False)
                & ~models.Q(number=""),
                name="unique_supplier_document_number",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="document", name="unique_company_document_number"
        ),
        migrations.RunPython(index_saved_invoices, migrations.RunPython.noop),
    ]
