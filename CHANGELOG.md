# Changements

## Ventilations bancaires et avances en devise

- Rapprochement de plusieurs montants sur une opération, avec factures, avoirs, commissions et reports. Le détail signé est exportable.
- Conservation du montant original des notes de frais en devise, du montant effectivement payé en euros et du nom de la personne ayant avancé les fonds.

## Identité visuelle tricolore

- Le logo et l’interface adoptent le bleu, le blanc et le rouge. Les nouveaux documents utilisent le bleu par défaut, les rapports reprennent la palette et chaque entreprise peut choisir sa propre couleur dans **Personnalisation**.

## Tarifs, catalogue et relances des ventes

- Saisie des prix unitaires HT ou TTC et remise globale en pourcentage ou en montant HT, avec répartition par taux dans les devis, les factures et le XML Factur-X.
- Ajout d’un produit ou d’un service au catalogue depuis une ligne de devis, avec réutilisation d’une référence existante.
- Relance individuelle ou groupée depuis la liste des retards. L’aperçu et le contrôle avant envoi tiennent compte des paiements partiels.
- Choix supplémentaires de formes juridiques et guide des champs de facturation pour les sociétés françaises et étrangères.

## Suivi des ventes et des encaissements

- Dates de prestation, références de commande, adresses de livraison et nature des opérations dans le PDF et le XML. Prise en compte de la TVA sur les débits et de l’immatriculation locale des clients étrangers.
- Déclarations de règlement partiel, confirmation en banque et suivi de la remise au client par l’interface ou l’API.
- Encarts par devise pour les soldes ouverts, les retards et les sommes effectivement encaissées sur une période.

## Relevés bancaires et entreprises étrangères

- Import de plusieurs relevés, suivi par fichier et préremplissage facultatif des opérations PDF par IA après OCR.
- Vue de toutes les transactions et des opérations sans facture liée, avec totaux par devise.
- Recherche du pays par nom, création d’entreprise étrangère et résultat VIES lisible sur la fiche client.

## Correctifs du 20 novembre 2026

- Correction des avoirs fournisseurs dans le journal exporté.
- Création de facture et d’avancement par API avec référence de commande ou clé d’idempotence obligatoire. Les répétitions retrouvent le document créé.
- Numéro définitif à l’émission des nouveaux brouillons et sélection cohérente de l’original fournisseur.
- Renforcement des accès d’équipe, des liens de récupération, des imports multi-entreprises, des journaux HTTP et de l’attribution des notifications SUPERPDP.

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
