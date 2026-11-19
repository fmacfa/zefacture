import core.common
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_archive_ingestions'),
    ]

    operations = [
        migrations.CreateModel(
            name='DocumentContact',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('contact', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='core.customercontact')),
                ('document', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='crm_link', to='core.document')),
            ],
            options={
                'db_table': 'django_document_contacts',
            },
        ),
        migrations.CreateModel(
            name='DocumentNote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('note_uid', models.CharField(default=core.common.uid, max_length=32, unique=True)),
                ('body', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('author', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('document', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='internal_notes', to='core.document')),
            ],
            options={
                'db_table': 'django_document_notes',
            },
        ),
    ]
