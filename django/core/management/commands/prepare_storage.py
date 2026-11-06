import os
from django.conf import settings
from django.core.management.base import BaseCommand
from core.services.files import secret_box


class Command(BaseCommand):
    help = (
        "Initialiser ou vérifier la clé de chiffrement privée sans afficher sa valeur."
    )

    def handle(self, *args, **options):
        settings.DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        secret_box()
        os.chmod(settings.DATA_DIR / "app.key", 0o600)
        self.stdout.write("Stockage privé prêt.")
