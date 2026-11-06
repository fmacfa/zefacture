import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from core.services.jobs import work_one
from core.services.mail import flush
from core.services.webhooks import flush as flush_webhooks
from core.services.pdp_sync import flush_outbox, sync_due


class Command(BaseCommand):
    help = "Traiter imports, emails, webhooks et échanges SUPERPDP en arrière-plan."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            worked = work_one()
            flush()
            flush_webhooks()
            flush_outbox()
            sync_due()
            if options["once"]:
                break
            if not worked:
                time.sleep(3)
