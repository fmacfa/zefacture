"""Restore into a NEW Docker project only. Existing volumes are never overwritten."""

import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
import subprocess
import tarfile
import tempfile

root = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument("archive")
parser.add_argument("--project", required=True)
parser.add_argument("--port", type=int, default=8092)
args = parser.parse_args()
if not re.fullmatch(r"[a-z][a-z0-9-]{2,50}", args.project):
    raise SystemExit("Nom de projet invalide.")
if not 1024 <= args.port <= 65535:
    raise SystemExit("Port invalide.")
existing = subprocess.check_output(
    [
        "docker",
        "volume",
        "ls",
        "--filter",
        "label=com.docker.compose.project=" + args.project,
        "--format",
        "{{.Name}}",
    ],
    text=True,
)
if existing.strip():
    raise SystemExit("Ce projet possède déjà des volumes ; choisissez un nouveau nom.")
os.umask(0o077)
recovery = root / "backups" / ("recovery-" + args.project)
recovery.mkdir(parents=True, exist_ok=False, mode=0o700)
envfile = recovery / ".env"
with tempfile.TemporaryDirectory(prefix="zefacture-restore-") as temp:
    folder = Path(temp)
    with tarfile.open(args.archive, "r:gz") as tar:
        manifest = json.load(tar.extractfile("manifest.json"))
        if manifest.get("format") != "zefacture-backup-v1":
            raise SystemExit("Format inconnu.")
        for name in ("database.dump", "documents.tar.gz", "environment.env"):
            member = tar.getmember(name)
            if not member.isfile():
                raise SystemExit("Membre d'archive interdit.")
            with (folder / name).open("wb") as destination:
                shutil.copyfileobj(tar.extractfile(member), destination, 1024 * 1024)
            with (folder / name).open("rb") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != manifest[
                    "sha256"
                ].get(name):
                    raise SystemExit("Somme de contrôle invalide.")
    with tarfile.open(folder / "documents.tar.gz", "r:gz") as data:
        for member in data.getmembers():
            if (
                Path(member.name).is_absolute()
                or ".." in Path(member.name).parts
                or not (member.isfile() or member.isdir())
            ):
                raise SystemExit("Chemin de document interdit.")
    # Local recovery is deliberately isolated; re-enable delivery only after review.
    overrides = {
        "APP_ENV": "local",
        "DEBUG": "0",
        "HTTP_PORT": str(args.port),
        "APP_URL": f"http://127.0.0.1:{args.port}",
        "ALLOWED_HOSTS": "127.0.0.1,localhost,testserver",
        "MAIL_TRANSPORT": "outbox",
        "EMAIL_DELIVERY_DISABLED": "1",
        "WEBHOOK_DELIVERY_DISABLED": "1",
        "EXTERNAL_SERVICES_DISABLED": "1",
        "TRUST_PROXY": "0",
        "SECURE_SSL_REDIRECT": "0",
    }
    lines = [
        line
        for line in (folder / "environment.env").read_text().splitlines()
        if line.split("=", 1)[0] not in overrides
    ]
    envfile.write_text(
        "\n".join(lines + ["=".join(item) for item in overrides.items()]) + "\n"
    )
    environment = os.environ | {"ZEFACTURE_ENV_FILE": str(envfile)}
    compose = [
        "docker",
        "compose",
        "--project-name",
        args.project,
        "--env-file",
        str(envfile),
        "-f",
        str(root / "compose.yaml"),
    ]

    def run(*arguments, **kwargs):
        return subprocess.run(
            compose + list(arguments), env=environment, cwd=root, check=True, **kwargs
        )

    run("up", "-d", "--wait", "db")
    with (folder / "database.dump").open("rb") as stream:
        run(
            "exec",
            "-T",
            "db",
            "pg_restore",
            "-U",
            "zefacture",
            "-d",
            "zefacture",
            "--no-owner",
            "--exit-on-error",
            stdin=stream,
        )
    with (folder / "documents.tar.gz").open("rb") as stream:
        run(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--entrypoint",
            "tar",
            "web",
            "-xz",
            "-C",
            "/data",
            stdin=stream,
        )
    run("up", "-d", "--wait", "--wait-timeout", "180", "web")
print(
    f"Restauration démarrée sur http://127.0.0.1:{args.port}. Configuration privée : {envfile}"
)
print(
    "Worker non démarré : contrôler les files et les verrous avant de reprendre les traitements."
)
