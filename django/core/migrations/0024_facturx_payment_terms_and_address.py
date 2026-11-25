from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0023_expense_claims_registers_evidence_and_statement_controls'),
    ]

    operations = [
        migrations.AddField(
            model_name='company',
            name='collection_fee_terms',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='company',
            name='early_payment_terms',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='company',
            name='electronic_address',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='company',
            name='electronic_address_scheme',
            field=models.CharField(blank=True, default='', max_length=4),
        ),
        migrations.AddField(
            model_name='company',
            name='late_payment_terms',
            field=models.TextField(blank=True, default=''),
        ),
    ]
