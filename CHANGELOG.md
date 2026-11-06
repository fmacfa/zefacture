# Changements

## 0.1.0-rc.1 — 6 novembre 2026

Première version candidate publique de l’édition Django auto-hébergée, sous AGPL-3.0-only.

### Fonctions incluses

- Devis, acceptation, factures de solde/d’avancement, avoirs, PDF et Factur-X validé localement.
- CRM professionnels/particuliers, contacts, opportunités, tâches, activités et emails.
- Catalogue, stocks par mouvements, entreprises, membres, invitations et rôles.
- Dashboard, périodes et comparatifs, exports PDF/CSV/Excel et gestion financière.
- Connexions SMTP/Gmail/Outlook, modèles d’emails et personnalisation par entreprise.
- API limitée aux ressources documentées et webhooks sortants signés.
- SUPERPDP : émission en file durable, synchronisation des factures entrantes/sortantes, originaux PDF/XML, événements, décisions métier explicites et reprise contrôlée.
- MFA TOTP, codes de secours, vérification récente, isolation des entreprises et journal.
- Installation Docker, HTTPS Caddy, migration PHP, sauvegardes/restauration et guides.

### Validation

131 tests applicatifs ont passé sous Python 3.13 et 3.14 avant publication, avec parcours navigateur, responsive et restauration par empreintes sur données fictives. La CI du dépôt vérifie les commits publiés, les dépendances, les fichiers distribués et la recherche de secrets.

### Limites et recette externe

La validation avec les véritables comptes SUPERPDP, Gmail et Outlook ainsi qu’un déploiement public reste requise. Cette version candidate n’est pas présentée comme une version stable de production. L’API ne couvre pas toutes les ressources ; les stocks sont manuels ; les rapports et limites des messageries sont décrits dans les guides. Aucune migration ne doit être appliquée sans sauvegarde.
