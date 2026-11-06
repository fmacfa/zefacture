"""Check the public Git tree without reading or printing private local values.

This gate rejects common private artefacts and validates the public docs. A
separate Gitleaks job scans content and all reachable Git history for secrets.
"""

import argparse
import re
import subprocess
from pathlib import Path, PurePosixPath

ROOT_FILES = {
    "README.md",
    "LICENSE",
    "VERSION",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    ".gitignore",
    ".gitattributes",
    ".editorconfig",
}
SUFFIXES = {
    ".py",
    ".html",
    ".css",
    ".js",
    ".cjs",
    ".md",
    ".txt",
    ".in",
    ".json",
    ".sh",
    ".yml",
    ".yaml",
    ".toml",
    ".svg",
}
SPECIAL_NAMES = {
    "Dockerfile",
    "Caddyfile",
    "LICENSE",
    "VERSION",
    ".dockerignore",
    ".gitignore",
    ".env.example",
}
PRIVATE_DIRS = {
    "storage",
    "data",
    "backups",
    "test-results",
    "staticfiles",
    "node_modules",
    "vendor",
    "__pycache__",
    ".git",
    ".publication",
    "site-presentation",
    ".openai",
    ".codex",
    ".agents",
    ".sites-runtime",
    "dist",
    "playwright-report",
    "blob-report",
}
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def path_issue(name, mode="100644"):
    path = PurePosixPath(name)
    if mode not in {"100644", "100755"}:
        return "symlink or submodule is not allowed"
    if path.is_absolute() or ".." in path.parts:
        return "invalid path"
    if any(part in PRIVATE_DIRS or part.startswith(".venv") for part in path.parts):
        return "private or generated directory"
    if (
        any(part.startswith(".env") for part in path.parts)
        and name != "django/.env.example"
    ):
        return "private environment file"
    if len(path.parts) == 1:
        return None if name in ROOT_FILES else "unreviewed root file"
    if path.parts[0] not in {"django", ".github"}:
        return "unreviewed source directory"
    if path.suffix.lower() not in SUFFIXES and path.name not in SPECIAL_NAMES:
        return "binary, dataset or unreviewed file type"
    if path.name in {"source.tar.gz", ".gitleaksignore", ".gitleaks.toml"}:
        return "generated archive or secret-scanner suppression"
    return None


def contact_allowed(value):
    domain = value.rsplit("@", 1)[-1].lower()
    return (
        domain
        in {
            "example.com",
            "example.org",
            "example.net",
            "zefacture.com",
            "users.noreply.github.com",
        }
        or domain.endswith((".test", ".example", ".invalid"))
        or value == "git@github.com"
    )


def check(root):
    raw = subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=root)
    entries = []
    for row in raw.split(b"\0"):
        if not row:
            continue
        metadata, name = row.split(b"\t", 1)
        mode = metadata.split()[0].decode()
        entries.append((name.decode("utf-8"), mode))
    if not entries:
        raise SystemExit("No tracked files to check.")
    issues = []
    private_history = subprocess.check_output(
        [
            "git", "log", "--all", "--format=", "--name-only", "--",
            "site-presentation", ":(glob)**/site-presentation/**",
            ".openai", ":(glob)**/.openai/**",
        ],
        cwd=root,
    )
    if private_history.strip():
        issues.append(("Git history", "private site sources or hosting metadata in reachable history"))
    contacts = 0
    links = 0
    for name, mode in entries:
        issue = path_issue(name, mode)
        if issue:
            issues.append((name, issue))
            continue
        target = root / name
        if target.is_symlink() or not target.is_file():
            issues.append((name, "not a regular source file"))
            continue
        raw = target.read_bytes()
        if len(raw) > 2_000_000 or b"\0" in raw:
            issues.append((name, "oversized or binary content"))
            continue
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            issues.append((name, "non-UTF8 source"))
            continue
        if re.search(r"/(?:Users|home)/[^\s/'\"]+", content):
            issues.append((name, "private workstation path"))
        if (
            "BEGIN " + "PRIVATE KEY" in content
            or "BEGIN OPENSSH " + "PRIVATE KEY" in content
        ):
            issues.append((name, "private key marker"))
        for value in set(EMAIL.findall(content)):
            if not contact_allowed(value):
                issues.append((name, "unreviewed contact address; value withheld"))
            contacts += 1
        if target.suffix.lower() == ".md":
            for link in LINK.findall(content):
                if "://" in link or link.startswith(("#", "mailto:")):
                    continue
                local = link.split("#", 1)[0]
                resolved = (target.parent / local).resolve()
                if not resolved.is_relative_to(root.resolve()) or not resolved.exists():
                    issues.append((name, "broken or external local documentation link"))
                links += 1
    for name in ["VERSION", "CHANGELOG.md"]:
        if (root / name).read_bytes() != (root / "django" / name).read_bytes():
            issues.append((name, "repository and application metadata differ"))
    if issues:
        for name, issue in sorted(set(issues)):
            print(f"{name}: {issue}")
        raise SystemExit(
            f"Publication check failed: {len(set(issues))} issue(s). No sensitive value printed."
        )
    print(
        f"Publication check passed: {len(entries)} tracked files, {links} local documentation links, {contacts} reviewed contact occurrences."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    check(parser.parse_args().root.resolve())
