import tarfile
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Construire l'archive AGPL sans secrets, données, dépendances ou fichiers locaux."

    def handle(self, *args, **options):
        root = settings.BASE_DIR
        target = root / "source.tar.gz"
        excluded = {"__pycache__", "staticfiles", ".venv", "node_modules", ".git"}
        with tarfile.open(target, "w:gz") as archive:
            for folder in ("config", "core", "templates", "static", "tests", "tools"):
                for file in sorted((root / folder).rglob("*")):
                    if (
                        file.is_file()
                        and not set(file.parts) & excluded
                        and file.suffix not in {".pyc", ".pyo"}
                    ):
                        archive.add(
                            file, arcname="zefacture/" + str(file.relative_to(root))
                        )
            for name in (
                "manage.py",
                "django_countries_data.py",
                "requirements.txt",
                "requirements.in",
                "Dockerfile",
                "compose.yaml",
                "entrypoint.sh",
                "Caddyfile",
                "LICENSE",
                "README.md",
                "VERSION",
                "CHANGELOG.md",
                "CONTRIBUTING.md",
                "SECURITY.md",
                "MIGRATION.md",
                "INTEGRATIONS.md",
                "GUIDE_AUTOHEBERGEMENT.md",
                "GUIDE_UTILISATEUR.md",
                "GUIDE_API_WEBHOOKS.md",
                "GUIDE_PUBLICATION_GITHUB.md",
                "GUIDE_SUPERPDP.md",
                ".env.example",
                ".dockerignore",
                ".gitignore",
                "THIRD_PARTY_NOTICES.md",
            ):
                if (root / name).is_file():
                    archive.add(root / name, arcname="zefacture/" + name)
        self.stdout.write("Archive de sources créée.")
