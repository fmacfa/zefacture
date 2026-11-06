import base64
import json
import sqlite3
import tarfile
import tempfile
from pathlib import Path
from django.test import override_settings
from django.db import models
from core.common import BusinessError, digest
from core.models import DocumentFile
from core.services.migration import models_by_table, export_legacy, import_legacy
from .test_application import ApplicationCase


class MigrationTests(ApplicationCase):
    def source(self, root):
        (root / "storage").mkdir()
        (root / ".env").write_text("DB_DATABASE=storage/zefacture.sqlite\n")
        (root / "storage/app.key").write_bytes(base64.b64encode(b"x" * 32))
        raw = b"original fixture bytes"
        (root / "storage/original.pdf").write_bytes(raw)
        doc = self.document()
        DocumentFile.objects.create(
            document_uid=doc.document_uid,
            kind="facturx",
            storage_path="storage/original.pdf",
            sha256=digest(raw),
            file_size=len(raw),
        )
        old_user_extra = {"last_login", "is_superuser", "is_active", "is_staff"}
        with sqlite3.connect(root / "storage/zefacture.sqlite") as connection:
            for table, model in models_by_table().items():
                fields = [
                    field
                    for field in model._meta.concrete_fields
                    if not (table == "users" and field.name in old_user_extra)
                ]
                types = [
                    "INTEGER"
                    if isinstance(f, models.IntegerField)
                    else "REAL"
                    if isinstance(f, models.DecimalField)
                    else "TEXT"
                    for f in fields
                ]
                columns = ",".join(
                    '"' + field.column + '" ' + kind
                    for field, kind in zip(fields, types)
                )
                connection.execute('CREATE TABLE "' + table + '" (' + columns + ")")
                for obj in model.objects.order_by(model._meta.pk.name):
                    values = [getattr(obj, field.attname) for field in fields]
                    values = [
                        float(v)
                        if isinstance(f, models.DecimalField) and v is not None
                        else v
                        for f, v in zip(fields, values)
                    ]
                    connection.execute(
                        'INSERT INTO "'
                        + table
                        + '" VALUES ('
                        + ",".join("?" for _ in fields)
                        + ")",
                        values,
                    )

    def test_full_roundtrip_and_nonempty_refusal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.source(root)
            archive = root / "export.tar.gz"
            exported = export_legacy(root, archive)
            self.assertEqual(exported["tables"], 22)
            self.assertEqual(exported["files"], 2)
            for model in models_by_table().values():
                model.objects.all().delete()
            result = import_legacy(archive)
            self.assertTrue(result["equal"])
            self.assertEqual(result["rows"], exported["rows"])
            self.assertTrue(import_legacy(archive, verify_only=True)["equal"])
            with self.assertRaises(BusinessError):
                import_legacy(archive)
            (Path(self.temp.name) / "original.pdf").write_bytes(b"corrupted")
            with self.assertRaises(BusinessError):
                import_legacy(archive, verify_only=True)

    def test_missing_original_blocks_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.source(root)
            (root / "storage/original.pdf").unlink()
            with self.assertRaises(BusinessError):
                export_legacy(root, root / "export.tar.gz")

    def test_unknown_column_blocks_before_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.source(root)
            with sqlite3.connect(root / "storage/zefacture.sqlite") as conn:
                conn.execute("ALTER TABLE companies ADD COLUMN unexpected TEXT")
            archive = root / "export.tar.gz"
            export_legacy(root, archive)
            for model in models_by_table().values():
                model.objects.all().delete()
            with self.assertRaises(BusinessError):
                import_legacy(archive)
            self.assertFalse(
                any(model.objects.exists() for model in models_by_table().values())
            )
