import getpass
import os

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from core.common import BusinessError
from core.services.instance_setup import create_first_owner, instance_is_empty


class Command(BaseCommand):
    help = "Créer le premier propriétaire d'une instance vierge."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", default="Administrateur")
        parser.add_argument("--company", default="Mon entreprise")

    def handle(self, *args, **options):
        if not instance_is_empty():
            raise CommandError(
                "L’instance contient déjà des données. Utilisez les invitations."
            )
        password = os.getenv("BOOTSTRAP_PASSWORD") or getpass.getpass(
            "Mot de passe (10 caractères minimum) : "
        )
        try:
            create_first_owner(
                address=options["email"],
                name=options["name"],
                company_name=options["company"],
                password=password,
            )
        except BusinessError as exc:
            raise CommandError(str(exc)) from exc
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from exc
        self.stdout.write("Premier compte créé. Connexion : /connexion")
