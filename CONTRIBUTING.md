# Contribuer à ZeFacture

Merci de lire le [guide de développement](django/CONTRIBUTING.md) pour installer l’environnement et lancer les tests.

## Proposer une modification

1. Ouvrir un ticket pour un changement fonctionnel important, avec un exemple fictif.
2. Créer une branche ou un fork, conserver les migrations et les données existantes.
3. Ajouter les vérifications adaptées aux règles métier et aux droits concernés ; actualiser les guides.
4. Proposer une pull request avec le problème, le comportement obtenu et les contrôles effectués.
5. Attendre le statut **CI validée** avant fusion.

Les contributions sont distribuées sous AGPL-3.0-only. Respecter les licences des composants ajoutés et mettre à jour leurs notices.

## Protéger les données

Ce dépôt est réservé à l’application Django open source et à ses outils de développement. Le site de présentation est maintenu séparément dans un dépôt privé : ne pas ajouter ses sources, sa configuration d’hébergement ou ses artefacts ici. Le contrôle de publication refuse aussi leur présence dans l’historique Git ; supprimer les fichiers du dernier commit ne suffit pas.

Ne jamais committer une configuration `.env` réelle, une base, un original client, un export, un journal privé, une sauvegarde ou une clé. Les tests et captures doivent utiliser des données inventées. Les seuls environnements d’exemple autorisés sont ceux explicitement présents dans les sources.

Le contrôle `.github/scripts/check_publication.py` vérifie les chemins, types de fichiers et liens documentaires suivis par Git. Gitleaks analyse le contenu et l’historique sans exception globale pour les tests. Un nouvel asset binaire ou une nouvelle catégorie de fichier nécessite une revue explicite et l’adaptation du contrôle ; ne pas contourner une alerte avec une exclusion large.

Pour un problème de sécurité, suivre [SECURITY.md](SECURITY.md) et le canal privé du dépôt. Ne pas ouvrir un ticket public contenant une preuve avec données réelles.
