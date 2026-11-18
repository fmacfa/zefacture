from django.core.management.base import BaseCommand, CommandError

from core.common import BusinessError
from core.services.instance_setup import issue_setup_code


class Command(BaseCommand):
    help = "Générer un code privé valable sept jours pour créer le premier compte."

    def handle(self, *args, **options):
        try:
            self.stdout.write(issue_setup_code())
        except BusinessError as exc:
            raise CommandError(str(exc)) from exc
