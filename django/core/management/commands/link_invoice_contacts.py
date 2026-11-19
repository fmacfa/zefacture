from django.core.management.base import BaseCommand

from core.models import Document
from core.services.invoice_contacts import assign_automatically


class Command(BaseCommand):
    help = "Rattacher les factures existantes à leur contact CRM de facturation."

    def add_arguments(self, parser):
        parser.add_argument("--company", help="Limiter le traitement à une entreprise.")

    def handle(self, *args, **options):
        docs = Document.objects.filter(
            type__in=["invoice", "progress_invoice", "credit_note"],
            crm_link__isnull=True,
        )
        if options["company"]:
            docs = docs.filter(company_uid=options["company"])
        count = sum(
            assign_automatically(doc)
            for doc in docs.order_by("pk").iterator(chunk_size=200)
        )
        self.stdout.write(f"{count} facture(s) rattachée(s).")
