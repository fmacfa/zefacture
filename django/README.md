# ZeFacture — édition Django auto-hébergée

Version candidate `0.1.0-rc.1`. Lire les [limites de la candidate](CHANGELOG.md) et la [procédure de publication](GUIDE_PUBLICATION_GITHUB.md) avant un déploiement réel.

Application libre de facturation sous **AGPL-3.0-only**. Python/Django 5.2 LTS, PostgreSQL, templates Django et JavaScript léger, WeasyPrint et factur-x. Aucun runtime PHP, service IA ou abonnement PDP n'est nécessaire pour la gestion locale.

Le [guide complet d’auto-hébergement](GUIDE_AUTOHEBERGEMENT.md) détaille l’installation, le transfert des données existantes, les applications Google/Microsoft depuis zéro, la personnalisation des entreprises clientes, la sécurité, les sauvegardes et la CI. Un parcours de configuration OAuth est aussi disponible dans **Emails → Connexion → Configurer Google / Microsoft**.

Pour le travail quotidien : [manuel utilisateur](GUIDE_UTILISATEUR.md) — équipe et rôles, CRM, stocks, devis/factures, finances, tableaux de bord, périodes, exports et messagerie. Pour les automatismes : [contrat API et webhooks avec exemples](GUIDE_API_WEBHOOKS.md). La [préparation de la publication GitHub](GUIDE_PUBLICATION_GITHUB.md) décrit la sélection des sources et l’activation de la CI lors de la future création du dépôt.

## Installation avec Docker

Prérequis : Docker Engine avec Compose récent, 2 processeurs et 4 Go de RAM conseillés, un disque persistant. Les commandes ci-dessous se lancent dans ce dossier.

    python3 tools/configure.py
    docker compose up -d --build
    docker compose exec web python manage.py bootstrap --email vous@example.com --name "Votre nom" --company "Votre entreprise"

Ouvrir http://127.0.0.1:8090. Le mot de passe du premier compte est demandé dans le terminal. Il n'existe aucun compte ou mot de passe par défaut. Les inscriptions publiques sont fermées ; le propriétaire invite ses collaborateurs.

Le service web lance les migrations, le worker traite l'OCR et la boîte d'envoi. PostgreSQL et les documents utilisent des volumes nommés. Les fichiers ne sont jamais servis directement par le proxy.

## Mettre en ligne avec HTTPS

Pointer le DNS du domaine vers le serveur, ouvrir les ports 80 et 443, puis définir dans `.env` :

    APP_ENV=production
    APP_URL=https://factures.example.com
    DOMAIN=factures.example.com
    ALLOWED_HOSTS=factures.example.com,localhost,127.0.0.1
    TRUST_PROXY=1
    PROXY_SHARED_SECRET=valeur_privee_generee_par_tools_configure
    SECURE_SSL_REDIRECT=1
    INSTANCE_CONTACT=contact@example.com
    INSTANCE_LEGAL=Identité et coordonnées de l'exploitant de cette instance

    docker compose --profile https up -d --build
    docker compose exec web python manage.py check --deploy
    docker compose exec web python manage.py check_readiness --production --require-oauth

Caddy gère les certificats. Le port direct de l'application reste lié à 127.0.0.1. Ne publiez pas un autre accès direct faisant confiance aux en-têtes proxy. Conservez `DEBUG=0`, une clé privée aléatoire et des hôtes explicites. Voir la [liste de déploiement Django](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/).

Une installation neuve peut générer directement sa configuration avec `python3 tools/configure.py --production --domain votre-domaine.fr`. Pour une configuration ancienne, ajouter une clé proxy aléatoire d’au moins 32 caractères, identique pour Caddy et Django. HSTS sur les sous-domaines et le preload demandent une décision explicite sur l’ensemble du domaine ; leurs recommandations restent visibles dans `check --deploy` tant qu’ils sont désactivés. La production impose par défaut la double authentification des administrateurs et une vérification récente pour les réglages sensibles.

## Fonctions

| Domaine | Parcours disponibles |
| --- | --- |
| Accès | Connexion, réinitialisation, compte, invitations, rôles, plusieurs entreprises |
| Clients | Professionnels et particuliers, France/international, annuaire SUPERPDP, contrôle VIES |
| Ventes | Devis, sauvegarde du brouillon, PDF, acceptation publique, facturation du solde et avancements |
| Facturation | Factures, avoirs, remises, TVA par taux, arrondis décimaux, Factur-X, fichiers et historique |
| Achats | Factures reçues, acceptation, refus, litige, règlement, justificatifs |
| Inventaire | Catalogue produits/services, images, entrées/sorties de stock, seuils |
| Dépenses | Notes de frais et tickets, visibilité par utilisateur et par rôle |
| Imports | PDF/images, transcription/OCR local, correction manuelle, extraction IA optionnelle, conversion contrôlée |
| Finances | Comptes, transactions, import/export CSV, dédoublonnage, paiements partiels, rapprochement, prévisions, export comptable, état de TVA |
| Intégrations | Boîte d'envoi, SMTP/Resend, clés API, OAuth SUPERPDP, émission et récepteur d'événements |

Les compléments CRM, comparatifs, exports PDF/Excel/CSV, webhooks sortants et connexions SMTP/Gmail/Outlook depuis l'interface sont décrits dans [INTEGRATIONS.md](INTEGRATIONS.md). Les connexions email et webhooks sont propres à chaque entreprise ; les anciennes données sont conservées par une migration additive.

**Personnalisation** : logo, couleurs, nom d’affichage, présentation des documents, devise/délais par défaut, signature, modèles d’emails et adresse de réponse par entreprise. Thème clair/sombre/système et densité par utilisateur. Les instantanés et originaux émis restent immuables. **Sécurité** : TOTP, codes de secours à usage unique, vérification récente et journal des actions sensibles.

Les documents émis sont figés. Un original absent ou altéré bloque le téléchargement ; il doit être restauré, jamais remplacé silencieusement. Une facture émise se corrige par un avoir. Les simulations PDP restent exclues des états financiers.

L'état de TVA conserve la logique historique par date d'émission. Il ne constitue pas une déclaration de TVA sur encaissements. L'export comptable est un journal de facturation, pas un FEC. Les devises restent séparées dans les soldes.

La génération Factur-X produit un PDF/A-3 avec XML EN16931. Le XSD et le Schematron sont exécutés localement avec SaxonC avant livraison. Un échec ou un validateur indisponible bloque l'émission. Les avertissements non bloquants des règles sont conservés dans le rapport ; aucune date de livraison n'est inventée.

## Emails, OCR et services externes

`MAIL_TRANSPORT=outbox` bloque les livraisons de la configuration serveur, y compris les messages historiques utilisant un autre transport. Les administrateurs consultent les messages dans **Emails**.

Cette règle concerne la configuration historique du serveur. Une connexion explicitement choisie dans **Emails → Connexion** dispose de son propre transport. `EMAIL_DELIVERY_DISABLED=1` constitue le verrou global pour tous les emails, et `WEBHOOK_DELIVERY_DISABLED=1` pour les webhooks. Une restauration active aussi `EXTERNAL_SERVICES_DISABLED=1`, qui bloque OAuth, SMTP, webhooks, PDP, annuaires et analyse IA ; les trois verrous restent actifs sur les copies de validation.

Pour livrer les messages, renseigner `MAIL_TRANSPORT=smtp`, les variables SMTP de `.env.example` et l'adresse d'expédition, puis redémarrer web et worker. Les PDF émis sont joints aux messages de devis/factures. `resend` est également pris en charge avec `RESEND_API_KEY`.

L'OCR utilise Poppler et Tesseract, inclus dans l'image Docker. L'analyse via OpenRouter nécessite `OPENROUTER_API_KEY` et `OPENROUTER_MODEL` et transmet le texte à ce fournisseur lorsqu'elle est déclenchée. La saisie manuelle fonctionne sans IA.

SUPERPDP reste optionnel. Le [guide émission/réception](GUIDE_SUPERPDP.md) détaille OAuth, dépôts Factur-X durables, synchronisation des factures entrantes/sortantes, conservation des originaux PDF/XML et suivi des statuts. La synchronisation est activée par entreprise et utilise l’API officielle, sans dépendre d’un abonnement webhook. Les événements ne réécrivent pas les montants ou les rapprochements ; les déclarations métier se transmettent explicitement. Les essais automatisés utilisent un fournisseur HTTP local ; une recette sur le véritable sandbox SUPERPDP reste requise avant les dépôts réels.

## Migration du PHP

Lire [MIGRATION.md](MIGRATION.md). L'export lit la base historique sans la modifier. L'import exige une base métier vide, conserve toutes les colonnes historiques, identifiants, hachages et fichiers, puis compare intégralement le résultat. Il ne recalcule pas les anciennes factures.

## Sauvegarde et restauration

    python3 tools/backup.py

La commande arrête brièvement web et worker, exporte PostgreSQL, copie le stockage avec `app.key` et la configuration privée, puis redémarre les services. L'archive est privée, contient des secrets et doit être copiée vers un stockage de sauvegarde protégé.

Pour vérifier une restauration dans un **nouveau** projet Docker :

    python3 tools/restore.py backups/zefacture-DATE.tar.gz --project zefacture-recovery --port 8092

La commande refuse un projet ayant déjà des volumes, contrôle les empreintes et les chemins d'archive, puis restaure base et fichiers. Elle démarre localement et désactive la livraison email. Sa configuration privée est conservée dans `backups/recovery-zefacture-recovery/.env`. Vérifier les données avant de modifier le routage ou de réactiver les intégrations. Ne pas utiliser `docker compose down -v` sur une instance à conserver.

## Mise à jour

Sauvegarder, installer les nouveaux sources, puis :

    docker compose build
    docker compose up -d
    docker compose exec web python manage.py check

Les migrations Django sont versionnées. Pour revenir à une version antérieure, restaurer la sauvegarde dans une instance séparée avec les sources correspondants ; ne pas réimporter une archive PHP sur une base déjà utilisée.

## Développement local

Python 3.13 ou 3.14, PostgreSQL 17, Pango, Poppler, Tesseract :

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt

Configurer `DATABASE_URL=postgresql://utilisateur:motdepasse@127.0.0.1:5432/zefacture`, `DJANGO_SECRET_KEY` et `DATA_DIR` dans `.env`. À défaut de DATABASE_URL, SQLite est disponible pour le développement individuel ; PostgreSQL est obligatoire en production.

Pour garder une configuration de développement distincte de Docker, utiliser `ZEFACTURE_ENV_FILE=/chemin/vers/.env.development` devant les commandes Django.

    .venv/bin/python manage.py migrate
    .venv/bin/python manage.py collectstatic --noinput
    .venv/bin/python manage.py build_source
    .venv/bin/python manage.py runserver 127.0.0.1:8090
    .venv/bin/python manage.py worker

Le worker se lance dans un second terminal. Tests, depuis ce dossier :

    .venv/bin/python manage.py test tests --noinput

Le rôle PostgreSQL de test doit pouvoir créer une base. La suite utilise une base de test distincte et des fichiers temporaires.

## API

Créer une clé dans **API & intégrations**. Elle est affichée une seule fois et limitée à l'entreprise. Les routes historiques `/api/v1/customers`, `/quotes`, `/invoices`, acceptation, avancement et Factur-X sont conservées. Authentification : `Authorization: Bearer zf_live_...`.

Exemple de corps pour POST /api/v1/invoices :

    {
      "customer_uid": "identifiant_du_client",
      "issue_date": "2026-10-25",
      "due_date": "2026-11-24",
      "currency_code": "EUR",
      "lines": [
        {"description": "Prestation", "qty": "2", "unit_price_excluding_tax": "50", "vat_rate": "20"}
      ],
      "generate_facturx": true
    }

Les réponses utilisent l'enveloppe `data`. Envoyer les montants en chaînes décimales évite les imprécisions du client. Les erreurs métier renvoient un code 4xx.

## Licence et sources

[GNU AGPL-3.0-only](LICENSE). Chaque instance expose `/source` et une archive du code effectivement embarqué, produite par `manage.py build_source`. Régénérer l'archive après toute modification locale. Elle exclut les secrets, les documents, les dépendances installées et les caches.

Les dépendances conservent leurs licences : [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Contributions : [CONTRIBUTING.md](CONTRIBUTING.md). Signalement de vulnérabilités : [SECURITY.md](SECURITY.md).
