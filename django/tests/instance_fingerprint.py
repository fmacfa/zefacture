"""Private operational verification: hashes and counts only, no business values."""

import hashlib
import json
from pathlib import Path
from django.apps import apps
from django.conf import settings
from django.db import connection
from core.services.migration import table_hash

tables = {}
existing_tables = set(connection.introspection.table_names())
for model in apps.get_app_config("core").get_models():
    if model._meta.db_table not in existing_tables:
        continue
    fields = [field.attname for field in model._meta.concrete_fields]
    rows = list(model.objects.order_by(model._meta.pk.name).values(*fields))
    tables[model._meta.db_table] = {
        "rows": len(rows),
        "sha256": table_hash(rows, fields),
    }
files = {}
for target in sorted(settings.DATA_DIR.rglob("*")):
    if target.is_file():
        with target.open("rb") as stream:
            files[str(target.relative_to(settings.DATA_DIR))] = hashlib.file_digest(
                stream, "sha256"
            ).hexdigest()
print(json.dumps({"tables": tables, "files": files}, sort_keys=True))
