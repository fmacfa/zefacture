from django.db import migrations


def disconnect_accounts(apps, schema_editor):
    accounts = apps.get_model("core", "BankAccount").objects.using(schema_editor.connection.alias)
    accounts.filter(connected_provider__iexact="bridge").update(connected_provider=None, last_synced_at=None)


def remove_feed_permissions(apps, schema_editor):
    types = apps.get_model("contenttypes", "ContentType").objects.using(schema_editor.connection.alias)
    types.filter(app_label="core", model__in=("bankfeedentry", "bankfeedaccount", "bankfeed")).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0024_facturx_payment_terms_and_address"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(disconnect_accounts, migrations.RunPython.noop),
        migrations.DeleteModel(name="BankFeedEntry"),
        migrations.DeleteModel(name="BankFeedAccount"),
        migrations.DeleteModel(name="BankFeed"),
        migrations.RunPython(remove_feed_permissions, migrations.RunPython.noop),
    ]
