"""Create a private Docker configuration, without overwriting existing files."""

import os
import secrets
import argparse
import re
from pathlib import Path

root = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(
    description="Créer une configuration privée sans écraser un fichier existant."
)
parser.add_argument("--production", action="store_true")
parser.add_argument("--domain", help="Nom DNS seul, par exemple zefacture.com")
parser.add_argument("--output", default=str(root / ".env"))
args = parser.parse_args()
if args.production and not args.domain:
    parser.error("--production exige --domain")
if args.domain and (
    len(args.domain) > 253
    or not re.fullmatch(
        r"(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}",
        args.domain,
    )
):
    parser.error("Domaine DNS invalide, sans protocole ni chemin")
destination = Path(args.output).resolve()
if destination.exists():
    raise SystemExit(".env existe déjà ; aucune modification.")
text = (
    (root / ".env.example")
    .read_text()
    .replace("replace_with_at_least_50_random_characters", secrets.token_urlsafe(60))
    .replace("replace_with_random_hex_password", secrets.token_hex(32))
    .replace("replace_with_random_proxy_secret", secrets.token_urlsafe(48))
)
if args.production:
    overrides = {
        "APP_ENV": "production",
        "APP_URL": "https://" + args.domain.lower(),
        "ALLOWED_HOSTS": args.domain.lower() + ",localhost,127.0.0.1",
        "DOMAIN": args.domain.lower(),
        "TRUST_PROXY": "1",
        "MFA_REQUIRED_FOR_ADMINS": "1",
        "SENSITIVE_REAUTH_REQUIRED": "1",
    }
    text = "\n".join(
        line for line in text.splitlines() if line.split("=", 1)[0] not in overrides
    )
    text += (
        "\n" + "\n".join(key + "=" + value for key, value in overrides.items()) + "\n"
    )
with open(destination, "x") as stream:
    os.chmod(destination, 0o600)
    stream.write(text)
(root / "backups").mkdir(mode=0o700, exist_ok=True)
print("Configuration privée créée :", destination)
print(
    "Renseignez les coordonnées de l’exploitant et les applications OAuth avant ouverture. Consultez GUIDE_AUTOHEBERGEMENT.md."
)
