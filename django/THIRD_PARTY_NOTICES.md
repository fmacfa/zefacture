# Dépendances

ZeFacture est distribué sous AGPL-3.0-only. Les composants tiers conservent leurs propres licences et notices, incluses dans leurs distributions.

Composants principaux : Django (BSD), WeasyPrint (BSD), factur-x (BSD), SaxonC-HE (MPL-2.0), psycopg (LGPL), pypdf (BSD), Pillow (HPND), PyNaCl (Apache-2.0), argon2-cffi (MIT), bcrypt (Apache-2.0), Gunicorn (MIT), WhiteNoise (MIT), requests (Apache-2.0), PyMySQL (MIT).

L'image Docker contient également Python, PostgreSQL dans un service séparé, les bibliothèques Pango/Harfbuzz, Poppler, Tesseract et des polices libres. Caddy est un service optionnel. Consulter les licences des paquets dans les images et distributions concernées.

Le fichier requirements.txt fixe les versions Python utilisées. La bibliothèque factur-x distribue les schémas et règles nécessaires à la validation EN16931 ; SaxonC exécute ces règles localement.

Les codes pays ISO constituent des données de référence. Les polices sont celles installées dans le système ; aucune police commerciale n'est incluse dans les sources ZeFacture.

Les exports Excel utilisent openpyxl et et_xmlfile (licence MIT), sans service cloud.

La double authentification utilise PyOTP (MIT) et python-qrcode (BSD). Les tests de navigateur utilisent Playwright (Apache-2.0) et Chromium ; ils ne sont pas nécessaires au fonctionnement du serveur. Les outils CI coverage.py, Ruff et pip-audit conservent leurs licences respectives.

La CI utilise aussi [Gitleaks](https://github.com/gitleaks/gitleaks), sous licence MIT, pour rechercher les secrets dans l’historique. Son binaire n’est pas inclus dans l’application ; l’installateur de CI vérifie l’empreinte de l’archive officielle de la version fixée.
