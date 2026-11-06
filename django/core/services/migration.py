"""Read-only legacy export and transactional, field-for-field import.

Archives are private: they include password hashes and the encrypted-secret key.
No extraction of arbitrary archive members is performed.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from decimal import Decimal
from datetime import datetime, date
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.color import no_style
from django.db import connection, transaction
from dotenv import dotenv_values

from core.common import BusinessError, stamp


def models_by_table():
    return {
        m._meta.db_table: m
        for m in apps.get_app_config("core").get_models()
        if not m._meta.db_table.startswith("django_")
    }


def canonical(value):
    if isinstance(value, (datetime, date)):
        return str(value)
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return format(Decimal(str(value)).normalize(), "f")
    return value


def table_hash(rows, columns):
    normalized = [[canonical(r[c]) for c in columns] for r in rows]
    return hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def export_legacy(root, archive):
    root, archive = Path(root).resolve(), Path(archive).resolve()
    if archive.exists():
        raise BusinessError("La sauvegarde existe déjà ; choisissez un autre nom.")
    config = dotenv_values(root / ".env")
    mysql = (
        config.get("DB_CONNECTION") or config.get("DB_DRIVER") or "sqlite"
    ) == "mysql"
    # The live PHP factory also chooses MySQL whenever DB_HOST is configured.
    mysql = mysql or bool(config.get("DB_HOST") and config.get("DB_DATABASE"))
    if mysql:
        import pymysql

        args = dict(
            host=config.get("DB_HOST", "127.0.0.1"),
            port=int(config.get("DB_PORT", 3306)),
            user=config.get("DB_USERNAME"),
            password=config.get("DB_PASSWORD"),
            database=config.get("DB_DATABASE"),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
        if config.get("DB_SOCKET"):
            args["unix_socket"] = config["DB_SOCKET"]
        source = pymysql.connect(**args)
        cursor = source.cursor()
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
    else:
        database = root / (config.get("DB_DATABASE") or "storage/zefacture.sqlite")
        source = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        cursor = source.cursor()
        cursor.execute("BEGIN")
    snapshot = {
        "format": "zefacture-legacy-v1",
        "created_at": stamp(),
        "tables": {},
        "files": {},
    }
    try:
        for table in models_by_table():
            if mysql:
                cursor.execute(f"SHOW COLUMNS FROM `{table}`")
                columns = [r["Field"] for r in cursor.fetchall()]
            else:
                columns = [
                    r[1] for r in cursor.execute(f"PRAGMA table_info(`{table}`)")
                ]
            pk = "id" if "id" in columns else columns[0]
            cursor.execute(f"SELECT * FROM `{table}` ORDER BY `{pk}`")
            rows = [
                {k: canonical(v) for k, v in dict(r).items()} for r in cursor.fetchall()
            ]
            snapshot["tables"][table] = dict(
                columns=columns, rows=rows, sha256=table_hash(rows, columns)
            )
        with tempfile.TemporaryDirectory(prefix="zefacture-export-") as temp:
            temp = Path(temp)
            storage = root / "storage"
            referenced = {"storage/app.key"}
            for table, fields in {
                "document_files": ["storage_path"],
                "inventory_items": ["image_path"],
                "document_ingestions": ["storage_path", "transcription_output_path"],
            }.items():
                for row in snapshot["tables"][table]["rows"]:
                    referenced.update(str(row[f]) for f in fields if row.get(f))
            expanded = set()
            for relative in referenced:
                original = (root / relative).resolve()
                if not original.is_relative_to(storage.resolve()):
                    raise BusinessError("Chemin historique hors stockage.")
                if original.is_dir():
                    expanded.update(
                        str(p.relative_to(root))
                        for p in original.rglob("*")
                        if p.is_file()
                    )
                else:
                    expanded.add(relative)
            for relative in sorted(expanded):
                if not relative.startswith("storage/"):
                    raise BusinessError("Chemin de fichier historique inattendu.")
                original = (root / relative).resolve()
                if not original.is_relative_to(storage.resolve()):
                    raise BusinessError("Chemin historique hors stockage.")
                if not original.is_file():
                    raise BusinessError(f"Fichier historique manquant : {relative}")
                target = temp / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, target)
                raw = target.read_bytes()
                snapshot["files"][relative] = dict(
                    sha256=hashlib.sha256(raw).hexdigest(), size=len(raw)
                )
            (temp / "snapshot.json").write_text(
                json.dumps(snapshot, ensure_ascii=False, default=str)
            )
            archive.parent.mkdir(parents=True, exist_ok=True)
            with open(archive, "xb") as handle:
                os.chmod(archive, 0o600)
                with tarfile.open(fileobj=handle, mode="w:gz") as tar:
                    tar.add(temp / "snapshot.json", arcname="snapshot.json")
                    tar.add(temp / "storage", arcname="storage")
    finally:
        source.rollback()
        source.close()
    return {
        "tables": len(snapshot["tables"]),
        "rows": sum(len(t["rows"]) for t in snapshot["tables"].values()),
        "files": len(snapshot["files"]),
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }


def import_legacy(archive, verify_only=False):
    models = models_by_table()
    with tarfile.open(archive, "r:gz") as tar:
        raw = tar.extractfile("snapshot.json").read(100_000_001)
        if len(raw) > 100_000_000:
            raise BusinessError("Archive trop volumineuse.")
        snapshot = json.loads(raw)
        if snapshot.get("format") != "zefacture-legacy-v1" or set(
            snapshot["tables"]
        ) != set(models):
            raise BusinessError("Format ou tables de migration inattendus.")
        for table, data in snapshot["tables"].items():
            fields = {f.column: f for f in models[table]._meta.concrete_fields}
            if set(data["columns"]) - set(fields):
                raise BusinessError(f"Colonnes non prises en charge : {table}")
            if table_hash(data["rows"], data["columns"]) != data["sha256"]:
                raise BusinessError(f"Somme de contrôle invalide : {table}")
        with tempfile.TemporaryDirectory(prefix="zefacture-import-") as temp:
            staged = Path(temp)
            for relative, metadata in snapshot["files"].items():
                parts = Path(relative).parts
                if not relative.startswith("storage/") or ".." in parts:
                    raise BusinessError("Chemin de fichier interdit.")
                member = tar.getmember(relative)
                if not member.isfile() or member.size != metadata["size"]:
                    raise BusinessError("Membre d'archive invalide.")
                content = tar.extractfile(member).read()
                if hashlib.sha256(content).hexdigest() != metadata["sha256"]:
                    raise BusinessError("Pièce jointe altérée.")
                target = staged.joinpath(*parts[1:])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            if not verify_only:
                if any(m.objects.exists() for m in models.values()):
                    raise BusinessError(
                        "Importer dans une base vide uniquement. La source reste intacte."
                    )
                copied = []
                try:
                    with transaction.atomic():
                        for table, data in snapshot["tables"].items():
                            model = models[table]
                            fields = {f.column: f for f in model._meta.concrete_fields}
                            objects = [
                                model(**{fields[k].attname: v for k, v in row.items()})
                                for row in data["rows"]
                            ]
                            model.objects.bulk_create(objects)
                        with connection.cursor() as cursor:
                            for sql in connection.ops.sequence_reset_sql(
                                no_style(), list(models.values())
                            ):
                                cursor.execute(sql)
                        for relative in snapshot["files"]:
                            dest = settings.DATA_DIR.joinpath(*Path(relative).parts[1:])
                            if dest.exists():
                                if (
                                    hashlib.sha256(dest.read_bytes()).hexdigest()
                                    != snapshot["files"][relative]["sha256"]
                                ):
                                    raise BusinessError(
                                        "Le stockage contient déjà un fichier différent."
                                    )
                            else:
                                dest.parent.mkdir(
                                    parents=True, exist_ok=True, mode=0o700
                                )
                                shutil.copyfile(
                                    staged.joinpath(*Path(relative).parts[1:]), dest
                                )
                                os.chmod(dest, 0o600)
                                copied.append(dest)
                        compare_snapshot(snapshot, models)
                except Exception:
                    for dest in copied:
                        dest.unlink(missing_ok=True)
                    raise
            else:
                compare_snapshot(snapshot, models)
    return {
        "tables": len(models),
        "rows": sum(len(t["rows"]) for t in snapshot["tables"].values()),
        "files": len(snapshot["files"]),
        "equal": True,
    }


def compare_snapshot(snapshot, models):
    for table, data in snapshot["tables"].items():
        model = models[table]
        fields = {f.column: f.attname for f in model._meta.concrete_fields}
        rows = [
            {column: getattr(obj, fields[column]) for column in data["columns"]}
            for obj in model.objects.order_by(model._meta.pk.name)
        ]
        if table_hash(rows, data["columns"]) != data["sha256"]:
            raise BusinessError(f"Comparaison champ par champ échouée : {table}")
    for relative, data in snapshot["files"].items():
        dest = settings.DATA_DIR.joinpath(*Path(relative).parts[1:])
        if (
            not dest.is_file()
            or hashlib.sha256(dest.read_bytes()).hexdigest() != data["sha256"]
        ):
            raise BusinessError(f"Comparaison de fichier échouée : {relative}")
