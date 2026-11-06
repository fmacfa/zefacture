import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "publication", Path(__file__).resolve().parents[1] / "scripts/check_publication.py"
)
publication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publication)


class PublicationChecks(unittest.TestCase):
    def test_private_datasets_and_environments_are_rejected_in_source_folders(self):
        for path in [
            "django/.env",
            "django/.env.production",
            "django/core/customer.csv",
            "django/core/client.pdf",
            "django/tests/client.xlsx",
            "django/backups/snapshot.json",
            "django/test-results/report.json",
            "django/data/customers.json",
            "storage/app.key",
            "docs/private.md",
        ]:
            with self.subTest(path=path):
                self.assertIsNotNone(publication.path_issue(path))
        self.assertIsNone(publication.path_issue("django/.env.example"))
        self.assertIsNone(publication.path_issue("django/core/services/pdp.py"))

    def test_symlinks_and_submodules_cannot_import_files_outside_the_reviewed_tree(
        self,
    ):
        self.assertIsNotNone(publication.path_issue("django/core/source.py", "120000"))
        self.assertIsNotNone(publication.path_issue("django/core/source.py", "160000"))
        self.assertIsNotNone(publication.path_issue("../private.py"))

    def test_contact_examples_are_distinct_from_unreviewed_addresses(self):
        self.assertTrue(publication.contact_allowed("demo@example.test"))
        self.assertTrue(publication.contact_allowed("you@example.com"))
        self.assertFalse(
            publication.contact_allowed("contact@" + "unreviewed-domain.fr")
        )
