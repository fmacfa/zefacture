from django.core.management.base import BaseCommand, CommandError
from core.common import BusinessError
from core.models import Company, PdpSyncState
from core.services.pdp_sync import sync_company


class Command(BaseCommand):
    help = "Récupérer les factures, originaux et événements SUPERPDP sans émettre de nouvelle facture."

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--company", help="Identifiant de l'entreprise ZeFacture")
        target.add_argument(
            "--all",
            action="store_true",
            help="Entreprises dont la synchronisation automatique est activée",
        )

    def handle(self, *args, **options):
        companies = (
            [options["company"]]
            if options["company"]
            else list(
                PdpSyncState.objects.filter(enabled=True).values_list(
                    "company_uid", flat=True
                )
            )
        )
        failed = 0
        for company_uid in companies:
            try:
                result = sync_company(company_uid, force=True)
                self.stdout.write(
                    f"{company_uid} : "
                    + ("synchronisation effectuée" if result else "déjà en cours")
                )
            except (BusinessError, Company.DoesNotExist) as exc:
                failed += 1
                self.stderr.write(
                    str(exc)
                    if isinstance(exc, BusinessError)
                    else "Entreprise introuvable."
                )
        if failed:
            raise CommandError(
                f"{failed} synchronisation(s) en échec ; consulter le suivi SUPERPDP."
            )
