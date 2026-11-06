import importlib.util
import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "publication", Path(__file__).resolve().parents[1] / "scripts/check_publication.py"
)
publication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publication)


class PublicationChecks(unittest.TestCase):
    def test_deleting_the_private_site_does_not_make_its_history_public_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "--quiet", "-b", "main", str(root)], check=True)
            for name in ["VERSION", "django/VERSION", "CHANGELOG.md", "django/CHANGELOG.md"]:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("candidate\n")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            commit = ["git", "-c", "user.name=CI policy test", "-c", "user.email=ci@example.test", "commit", "--quiet", "-m"]
            subprocess.run(commit + ["Public application"], cwd=root, check=True)
            with contextlib.redirect_stdout(io.StringIO()):
                publication.check(root)
            site = root / "site-presentation/README.md"
            site.parent.mkdir()
            site.write_text("Private website fixture\n")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(commit + ["Private site accidentally added"], cwd=root, check=True)
            subprocess.run(["git", "rm", "--quiet", str(site)], cwd=root, check=True)
            subprocess.run(commit + ["Remove private site from current tree"], cwd=root, check=True)
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                publication.check(root)
            self.assertIn("private site sources", output.getvalue())

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
            "site-presentation/src/pages.mjs",
            "site-presentation/assets/social.png",
            "site-presentation/README.md",
            "django/site-presentation/README.md",
            ".github/site-presentation/content.json",
            ".openai/hosting.json",
            "django/.openai/hosting.json",
            "django/dist/index.html",
            "django/playwright-report/index.html",
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
