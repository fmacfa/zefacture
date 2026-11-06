import json
import sys
import tempfile
from django.core.management.base import BaseCommand, CommandError
from core.services.migration import export_legacy, import_legacy
from core.common import BusinessError


class Command(BaseCommand):
    help = (
        "Exporter le PHP en lecture seule ou importer/vérifier l'archive dans Django."
    )

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["export", "import", "verify"])
        parser.add_argument("archive")
        parser.add_argument("--source")

    def handle(self, *args, **options):
        try:
            if options["action"] == "export":
                if not options["source"]:
                    raise CommandError("--source requis")
                result = export_legacy(options["source"], options["archive"])
            else:
                if options["archive"] == "-":
                    with tempfile.NamedTemporaryFile(suffix=".tar.gz") as archive:
                        while chunk := sys.stdin.buffer.read(1024 * 1024):
                            archive.write(chunk)
                        archive.flush()
                        result = import_legacy(
                            archive.name, verify_only=options["action"] == "verify"
                        )
                else:
                    result = import_legacy(
                        options["archive"], verify_only=options["action"] == "verify"
                    )
            self.stdout.write(json.dumps(result))
        except BusinessError as exc:
            raise CommandError(str(exc)) from exc
