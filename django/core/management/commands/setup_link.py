from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from core.common import BusinessError
from core.services.instance_setup import issue_setup_code


class Command(BaseCommand):
    help = "Générer un lien privé valable sept jours pour créer le premier compte."

    def handle(self, *args, **options):
        try:
            token = issue_setup_code()
        except BusinessError as exc:
            raise CommandError(str(exc)) from exc
        page = reverse("first_connection")
        self.stdout.write(f"{settings.APP_URL.rstrip('/')}{page}#installation={token}")
