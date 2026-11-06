from django.core.management.base import BaseCommand
from core.services.mail import flush


class Command(BaseCommand):
    help = "Traiter les emails en attente via le transport enregistré."

    def handle(self, *args, **options):
        self.stdout.write(f"{flush()} emails traités")
