import getpass
import os
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from core.models import User, Company, CompanyMember
from core.common import stamp, email


class Command(BaseCommand):
    help = "Créer le premier propriétaire d'une instance vierge."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", default="Administrateur")
        parser.add_argument("--company", default="Mon entreprise")

    @transaction.atomic
    def handle(self, *args, **options):
        if User.objects.exists():
            raise CommandError("Un utilisateur existe déjà. Utilisez les invitations.")
        password = os.getenv("BOOTSTRAP_PASSWORD") or getpass.getpass(
            "Mot de passe (10 caractères minimum) : "
        )
        validate_password(password)
        user = User.objects.create_user(
            email(options["email"]),
            password,
            name=options["name"],
            is_staff=True,
            is_superuser=True,
            terms_accepted_at=stamp(),
        )
        company = Company.objects.create(
            owner_user_uid=user.user_uid,
            legal_name=options["company"],
            billing_email=user.email,
        )
        CompanyMember.objects.create(
            company_uid=company.company_uid,
            user_uid=user.user_uid,
            role="owner",
            joined_at=stamp(),
        )
        self.stdout.write("Premier compte créé. Connexion : /connexion")
