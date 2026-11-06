# Contribuer

Les contributions à ZeFacture sont distribuées sous AGPL-3.0-only.

Installer les dépendances de `requirements.txt`, configurer une base PostgreSQL de développement, puis exécuter `python manage.py test tests --noinput` depuis ce dossier.

Les modifications métier doivent conserver l'isolation entre entreprises, les snapshots des documents émis, les arrondis décimaux et la traçabilité. Ajouter des migrations Django plutôt que modifier manuellement les bases. Couvrir les régressions financières et les droits d'accès par des tests.

Ne jamais ajouter de données client, fichiers PDF réels, secrets, mots de passe ou archives de sauvegarde. Les exemples et tests doivent être fictifs. Régénérer `source.tar.gz` via `build_source` pour une distribution, sans la versionner.

Les dépendances directes figurent dans `requirements.in`, leurs versions résolues dans `requirements.txt`. Tester la construction Docker et les migrations avant de proposer une mise à jour.

La CI GitHub principale se trouve dans `../.github/workflows/django.yml`. Elle vérifie Python 3.13/3.14 et PostgreSQL 17, les dépendances, le build Docker, les parcours Playwright et la restauration d’une instance fictive. Le dépôt public exécute ces contrôles sur les push et pull requests ; suivre le résultat correspondant au commit proposé. Le statut `CI validée` rassemble les jobs requis.

Pour les parcours navigateur, installer `npm ci --prefix tests/browser`, puis Chromium avec `npx playwright install chromium` depuis `tests/browser`. Le script `CI=true CI_HTTP_PORT=8093 bash tools/ci_e2e.sh` crée seulement ses propres projets fictifs et les nettoie à la fin. Choisir un port libre et un moteur Docker de validation ; aucun secret de production n’est nécessaire. Les fichiers de résultat restent ignorés par Git.

SUPERPDP : `tests/test_superpdp.py` utilise un véritable serveur HTTP local à contrat 1.39.0.beta, avec pagination, originaux binaires et coupure après acceptation. Le sous-ensemble de contrat dans `tests/fixtures/superpdp-contract.json` conserve URL, version, date et empreinte de la spécification officielle. Les tests vérifient notamment la précision des identifiants, les totaux, les reprises, l’absence de réémission automatique, les changements concurrents de connexion et l’isolation des entreprises. Les parcours `browser-superpdp.cjs` vérifient aussi les téléchargements et quatre largeurs d’écran. Ils n’utilisent aucun compte ou document réel du fournisseur.

Le contrôle de publication et Gitleaks couvrent aussi les fichiers suivis et l’historique. Voir les [règles de contribution](https://github.com/fmacfa/zefacture/blob/main/CONTRIBUTING.md) ; aucun fichier privé réel ne doit être ajouté aux exemples ou aux tests.
