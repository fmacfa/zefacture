"""Consistent Docker backup: stop writers, dump PostgreSQL, copy documents+key."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone

root = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument("--project", default="zefacture")
parser.add_argument("--env-file", default=str(root / ".env"))
parser.add_argument("--output")
parser.add_argument(
    "--leave-stopped",
    action="store_true",
    help="Conserver web/worker arrêtés après une sauvegarde réussie, pour une mise à jour.",
)
args = parser.parse_args()
os.umask(0o077)
envfile = Path(args.env_file).resolve()
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
        compose + list(arguments), cwd=root, env=environment, check=True, **kwargs
    )


destination = (
    Path(args.output).resolve()
    if args.output
    else root
    / "backups"
    / (
        args.project
        + "-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + ".tar.gz"
    )
)
if destination.exists():
    raise SystemExit("La destination existe déjà.")
destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
with tempfile.TemporaryDirectory(prefix="zefacture-backup-") as temp:
    folder = Path(temp)
    run("stop", "web", "worker")
    completed = False
    try:
        with (folder / "database.dump").open("wb") as stream:
            run(
                "exec",
                "-T",
                "db",
                "pg_dump",
                "-U",
                "zefacture",
                "-d",
                "zefacture",
                "--format=custom",
                "--no-owner",
                stdout=stream,
            )
        with (folder / "documents.tar.gz").open("wb") as stream:
            run(
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "--entrypoint",
                "tar",
                "web",
                "-cz",
                "-C",
                "/data",
                ".",
                stdout=stream,
            )
        (folder / "environment.env").write_bytes(envfile.read_bytes())
        manifest = {}
        for name in ("database.dump", "documents.tar.gz", "environment.env"):
            with (folder / name).open("rb") as stream:
                manifest[name] = hashlib.file_digest(stream, "sha256").hexdigest()
        (folder / "manifest.json").write_text(
            json.dumps({"format": "zefacture-backup-v1", "sha256": manifest})
        )
        with tarfile.open(destination, "x:gz") as tar:
            for file in folder.iterdir():
                tar.add(file, arcname=file.name)
        completed = True
    finally:
        if not args.leave_stopped or not completed:
            run("start", "web", "worker")
print("Sauvegarde créée :", destination)
