import core.common
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_document_contacts_and_notes'),
    ]

    operations = [
        migrations.CreateModel(
            name='BankFeed',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('company_uid', models.CharField(max_length=32, unique=True)),
                ('external_uid', models.CharField(default=core.common.uid, max_length=32, unique=True)),
                ('client_id', models.CharField(max_length=180)),
                ('secret_encrypted', models.TextField()),
                ('remote_user', models.CharField(blank=True, max_length=36)),
                ('owner_uid', models.CharField(blank=True, max_length=32)),
                ('enabled', models.BooleanField(default=False)),
                ('auto_reconcile', models.BooleanField(default=True)),
                ('next_sync_at', models.DateTimeField(db_index=True, null=True)),
                ('last_success_at', models.DateTimeField(null=True)),
                ('last_error', models.TextField(blank=True)),
                ('lease', models.CharField(blank=True, max_length=32)),
                ('lease_until', models.DateTimeField(null=True)),
            ],
            options={
                'db_table': 'django_bank_feeds',
            },
        ),
        migrations.AddField(
            model_name='documentcontact',
            name='automatic',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='documentcontact',
            name='contact',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to='core.customercontact'),
        ),
        migrations.CreateModel(
            name='BankFeedAccount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('remote_id', models.CharField(max_length=40)),
                ('shared', models.BooleanField(default=True)),
                ('cursor', models.DateTimeField(null=True)),
                ('account', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='bank_feed', to='core.bankaccount')),
                ('feed', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='core.bankfeed')),
            ],
            options={
                'db_table': 'django_bank_feed_accounts',
            },
        ),
        migrations.CreateModel(
            name='BankFeedEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('remote_id', models.CharField(max_length=40)),
                ('changed_at', models.DateTimeField()),
                ('day', models.DateField()),
                ('amount', models.DecimalField(decimal_places=8, max_digits=30)),
                ('label', models.TextField()),
                ('bank_reference', models.CharField(blank=True, max_length=256)),
                ('status', models.CharField(max_length=16)),
                ('review', models.CharField(blank=True, max_length=16)),
                ('previous_invoice_status', models.CharField(blank=True, max_length=32)),
                ('retired_identity', models.BooleanField(default=False)),
                ('feed_account', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='core.bankfeedaccount')),
                ('transaction', models.OneToOneField(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='bank_feed', to='core.banktransaction')),
            ],
            options={
                'db_table': 'django_bank_feed_entries',
            },
        ),
        migrations.AddConstraint(
            model_name='bankfeedaccount',
            constraint=models.UniqueConstraint(fields=('feed', 'remote_id'), name='uniq_feed_account'),
        ),
        migrations.AddConstraint(
            model_name='bankfeedentry',
            constraint=models.UniqueConstraint(fields=('feed_account', 'remote_id'), name='uniq_feed_entry'),
        ),
    ]
