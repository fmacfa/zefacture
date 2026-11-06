from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F
from core.models import User, UserSecurity, AuditEvent


class Command(BaseCommand):
    help = "Récupération locale par l’exploitant, après contrôle d’identité. Invalide toutes les sessions."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--confirm-identity-checked", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        if not options["confirm_identity_checked"]:
            raise CommandError(
                "Vérifiez l’identité du demandeur hors application, puis ajoutez --confirm-identity-checked."
            )
        user = (
            User.objects.select_for_update()
            .filter(email__iexact=options["email"])
            .first()
        )
        if not user:
            raise CommandError("Compte introuvable.")
        UserSecurity.objects.filter(user_uid=user.user_uid).update(
            totp_secret_encrypted="",
            recovery_hashes=[],
            enabled_at=None,
            last_totp_counter=-1,
        )
        User.objects.filter(pk=user.pk).update(session_version=F("session_version") + 1)
        AuditEvent.objects.create(
            user_uid=user.user_uid, action="security.mfa_reset_by_operator"
        )
        self.stdout.write(
            "Second facteur réinitialisé ; toutes les sessions sont invalidées. L’utilisateur devra le réactiver."
        )
