# ZeFacture

**Facturation, CRM et gestion d’activité libres, à héberger sur votre serveur.**

ZeFacture réunit devis, factures Factur-X, clients professionnels et particuliers, suivi commercial, stocks, équipe, finances et exports dans une application Django. Les données métier restent dans votre base PostgreSQL et votre stockage.

**Version candidate `0.1.0-rc.1` — phase de validation avant production.** Les tests automatiques couvrent l’application et des fournisseurs simulés. Les connexions réelles SUPERPDP, Gmail et Outlook, la réception des emails et le déploiement public doivent être validés par l’exploitant avant utilisation réelle. Voir les [limites et recettes restantes](#état-de-la-version-candidate).

[Installation et exploitation](django/GUIDE_AUTOHEBERGEMENT.md) · [Manuel utilisateur](django/GUIDE_UTILISATEUR.md) · [API et webhooks](django/GUIDE_API_WEBHOOKS.md) · [SUPERPDP](django/GUIDE_SUPERPDP.md) · [Changements](CHANGELOG.md)

## Fonctionnalités

| Domaine | Fonctions |
| --- | --- |
| Facturation | Devis, factures, avancements, avoirs, TVA, PDF et Factur-X avec validation locale |
| Clients et CRM | Professionnels/particuliers, prospects, fournisseurs, contacts, opportunités, tâches et historique |
| Catalogue et stock | Produits/services, images, mouvements explicites, seuils et historique |
| Entreprises et équipe | Plusieurs entreprises, invitations, propriétaire, administrateur, comptable, membre et lecteur |
| Pilotage | Dashboard, tableaux, périodes libres, comparatifs et évolution mensuelle |
| Exports | PDF, CSV et Excel sur toutes les lignes sélectionnées, selon les droits |
| Achats | Factures fournisseurs, notes de frais, justificatifs et OCR local |
| Finances | Transactions, import CSV, paiements partiels, rapprochement, prévisions et états de TVA |
| Messagerie | SMTP, Gmail/Google Workspace et Outlook/Microsoft 365, avec signature et modèles par entreprise |
| Automatisation | Clés API par entreprise et webhooks sortants signés avec suivi des livraisons |
| SUPERPDP | Dépôts Factur-X, réception, originaux PDF/XML et synchronisation des événements |
| Personnalisation | Logo, couleurs, documents et emails par entreprise ; thème et densité par utilisateur |
| Sécurité | Permissions, isolation des entreprises, TOTP, codes de secours, vérification récente et journal |

L’interface s’adapte aux téléphones, tablettes et ordinateurs. Django, PostgreSQL, le rendu PDF/Factur-X et l’OCR fonctionnent sans runtime PHP. L’IA et les connexions aux fournisseurs externes sont facultatives.

## Essayer sur une installation vierge

Prérequis : Docker Engine avec le plugin Compose, Python 3 pour générer la configuration et un stockage persistant. Deux processeurs et 4 Go de RAM constituent un point de départ à adapter à la charge.

```sh
git clone https://github.com/fmacfa/zefacture.git
cd zefacture/django
python3 tools/configure.py
docker compose up -d --build
docker compose exec web python manage.py bootstrap --email vous@example.com --name "Votre nom" --company "Votre entreprise"
```

Remplacer l’adresse d’exemple par la vôtre. Le premier mot de passe est demandé dans le terminal ; aucun compte prédéfini n’existe. Ouvrir **http://127.0.0.1:8090**. Les emails restent dans la boîte locale tant qu’aucune livraison n’a été configurée. Inviter ensuite les collaborateurs depuis l’interface.

**Pour une instance avec des données existantes**, suivre la [migration PHP](django/MIGRATION.md) ou la [sauvegarde/restauration Django](django/GUIDE_AUTOHEBERGEMENT.md#4-déplacer-linstance-existante-sans-perdre-ses-données). Ne pas exécuter un import sur une base déjà utilisée ou supprimer ses volumes.

## Installer sur votre domaine

L’installation de production nécessite HTTPS, une configuration privée, l’identité de l’exploitant, la MFA des administrateurs, une messagerie de récupération de compte et des sauvegardes hors serveur. Le [guide complet](django/GUIDE_AUTOHEBERGEMENT.md) détaille Caddy, Docker, OAuth et la recette avant ouverture.

Chaque instance utilise **son propre domaine et ses propres identifiants**. `zefacture.com` dans les guides est un exemple lié au projet ; ce dépôt ne fournit ni hébergement public ni compte Google, Microsoft ou SUPERPDP. Les conditions d’accès de chaque fournisseur restent applicables.

## État de la version candidate

La validation locale de cette candidate comprend **131 tests applicatifs sous Python 3.13 et 3.14**, des parcours navigateur, des contrôles responsive à 320/390/768/1440 px et une restauration comparée par empreintes. La CI GitHub recalcule ces contrôles sur le commit publié ; consulter [ses résultats](https://github.com/fmacfa/zefacture/actions/workflows/django.yml).

Limites à connaître avant les essais :

- La recette avec les véritables comptes SUPERPDP, Gmail et Outlook reste à effectuer ; les tests de fournisseur sont simulés.
- L’API publique couvre les clients, devis, factures et certaines actions de facturation ; elle n’expose pas toutes les fonctions de l’interface.
- Les mouvements de stock sont explicites. Une facture ne décrémente pas automatiquement les quantités.
- Les tableaux du dashboard sont prédéfinis. Les devises restent séparées, sans conversion automatique.
- Les statuts SUPERPDP sont suivis séparément des montants et rapprochements bancaires locaux. Un résultat de dépôt incertain exige une vérification avant reprise.
- Une seule messagerie active est disponible par entreprise. Les boîtes partagées, alias et l’authentification Microsoft par certificat ne sont pas configurables dans cette version.
- L’état de TVA suit les dates d’émission ; le journal comptable exporté n’est pas un FEC.

Effectuer les essais avec des données fictives, puis suivre la [recette d’ouverture](django/GUIDE_AUTOHEBERGEMENT.md#12-recette-avant-ouverture) et la [recette SUPERPDP](django/GUIDE_SUPERPDP.md#10-recette-fournisseur-avant-production). La préversion ne constitue pas une validation réglementaire ou opérationnelle de votre installation.

## Documentation

| Besoin | Guide |
| --- | --- |
| Installer, mettre à jour, sauvegarder et restaurer | [Auto-hébergement](django/GUIDE_AUTOHEBERGEMENT.md) |
| Utiliser le CRM, l’équipe, les documents, le stock et les exports | [Manuel utilisateur](django/GUIDE_UTILISATEUR.md) |
| Connecter un autre outil | [API et webhooks](django/GUIDE_API_WEBHOOKS.md) |
| Connecter les factures émises et reçues | [SUPERPDP](django/GUIDE_SUPERPDP.md) |
| Comprendre les intégrations et les règles des rapports | [Intégrations](django/INTEGRATIONS.md) |
| Reprendre une installation PHP | [Migration](django/MIGRATION.md) |
| Développer et tester | [Contribution](CONTRIBUTING.md) |
| Préparer une distribution | [Publication](django/GUIDE_PUBLICATION_GITHUB.md) |
| Signaler une vulnérabilité | [Sécurité](SECURITY.md) |

## Contribuer et signaler un problème

Les contributions passent par une pull request et les contrôles de CI. Lire [CONTRIBUTING.md](CONTRIBUTING.md), puis utiliser les [tickets](https://github.com/fmacfa/zefacture/issues) pour un bug reproductible ou une proposition.

Ne joindre que des exemples fictifs. Pour une vulnérabilité, utiliser le [canal privé](https://github.com/fmacfa/zefacture/security/advisories/new), sans publier de secret ou de donnée client dans un ticket.

## Licence

ZeFacture est distribué sous **[AGPL-3.0-only](LICENSE)**. Les dépendances conservent leurs [licences respectives](django/THIRD_PARTY_NOTICES.md). Chaque instance propose le code embarqué via `/source` ; reconstruire l’archive après une modification locale.
