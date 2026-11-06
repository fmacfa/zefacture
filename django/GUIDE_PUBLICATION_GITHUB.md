# ZeFacture — publication des versions candidates

Le [dépôt public](https://github.com/fmacfa/zefacture) distribue l’édition Django sous AGPL-3.0-only. La première candidate est `0.1.0-rc.1`. Une publication des sources ne met pas une instance métier en ligne et n’autorise aucun compte fournisseur.

## 1. Contenu distribué

Le dépôt conserve l’application dans `django/`, les workflows dans `.github/`, la licence, le README, les notes de version et les règles de contribution/sécurité à la racine. Les sources PHP historiques, les procédures propres à une instance locale, les données et les sauvegardes ne font pas partie de cette distribution.

```text
README.md, LICENSE, VERSION, CHANGELOG.md
CONTRIBUTING.md, SECURITY.md
.github/workflows/django.yml
.github/scripts/, .github/tests/, .github/ISSUE_TEMPLATE/
.github/dependabot.yml, .github/requirements-ci.txt
django/
  manage.py, config/, core/, templates/, static/
  requirements.in, requirements.txt
  Dockerfile, compose.yaml, Caddyfile, entrypoint.sh
  tools/, tests/, .env.example
  README.md, VERSION, CHANGELOG.md, LICENSE, THIRD_PARTY_NOTICES.md
  GUIDE_*.md, INTEGRATIONS.md, MIGRATION.md, CONTRIBUTING.md, SECURITY.md
```

Un fichier ignoré par Git peut rester privé sur le disque ; le `.gitignore` n’efface pas un secret déjà enregistré. Utiliser une copie contrôlée et un historique propre lors d’une première publication. Ne pas copier le répertoire `.git` d’un espace de travail contenant des éléments non destinés au public.

## 2. Contrôles avant un commit public

Examiner les noms **et** le contenu des fichiers retenus. Exclure les configurations réelles, clés, bases, documents, exports, journaux, résultats privés, sauvegardes, environnements et dépendances installées. Les exemples doivent être fictifs et les contacts réels publiés intentionnellement.

Le contrôle de fichiers et de liens se lance depuis la racine du dépôt, après ajout des sources à l’index :

```sh
python3 -m unittest discover -s .github/tests
python3 .github/scripts/check_publication.py
```

Il examine les fichiers suivis par Git, refuse notamment environnements privés, jeux de données courants, liens symboliques et sous-modules, et contrôle les liens Markdown locaux. Il ne remplace pas une recherche de secrets ou la revue des données ; des informations personnelles peuvent se trouver dans un fichier source textuel valide.

La CI installe une version fixée de Gitleaks, après contrôle de l’empreinte de son archive officielle, puis analyse **tout l’historique accessible** avec les valeurs expurgées. Les tests ne bénéficient pas d’une exclusion globale et les commentaires d’exclusion ne sont pas acceptés par l’appel de CI. Si une valeur est réellement sensible, la retirer de l’historique public et la révoquer ; ne pas la recopier dans un ticket.

## 3. Vérifier l’application et l’archive embarquée

Suivre [CONTRIBUTING.md](CONTRIBUTING.md) pour les tests Python et navigateur. Construire l’image Docker et contrôler une installation vierge. `python manage.py build_source`, depuis un environnement Django configuré, produit l’archive `/source` du code applicatif. Elle doit contenir les versions, guides, licences et sources, sans configuration ou donnée privée.

Cette archive applicative conserve une structure autonome ; elle ne contient pas les workflows placés au-dessus de `django/`. Pour distribuer le dépôt complet, créer l’archive à partir du commit Git vérifié plutôt que compresser tout le dossier de travail.

Pour un changement affectant le schéma, les traitements ou le stockage, effectuer aussi une sauvegarde/restauration isolée et vérifier les empreintes de chaque table et fichier. Une copie restaurée garde ses sorties externes bloquées.

## 4. CI et branche principale

Le [workflow Django CI](https://github.com/fmacfa/zefacture/actions/workflows/django.yml) vérifie :

1. Les fichiers publics, leurs liens et la recherche de secrets dans l’historique.
2. Les erreurs Python et les vulnérabilités connues des dépendances verrouillées.
3. Les tests PostgreSQL 17 sur Python 3.13 et 3.14, migrations et contrôles de production.
4. Docker, les parcours navigateur, les exports et la restauration de données fictives.
5. Le statut final **CI validée**, qui exige la réussite de tous les jobs précédents.

Les actions sont fixées par SHA et le token du workflow est limité à la lecture. Les tests ne demandent aucun identifiant de production. Les artefacts publiés sont sélectionnés parmi les preuves synthétiques, avec conservation de sept jours ; les archives de sauvegarde et configurations des tests ne sont pas téléversées.

Consulter le résultat du **commit exact** qui sera distribué. Une exécution locale ne constitue pas un badge GitHub vert. La branche principale doit exiger `CI validée` avant fusion, interdire les suppressions et les pushes forcés. Les contributions passent par pull request ; Dependabot propose des mises à jour sans fusion automatique.

## 5. Préparer une préversion

1. Mettre à jour `VERSION` et `CHANGELOG.md`, ainsi que leurs copies dans `django/` pour l’archive applicative.
2. Vérifier les guides et les limites connues, sans annoncer une recette fournisseur encore non effectuée.
3. Terminer les contrôles et conserver le commit exact et le résultat de CI.
4. Créer un tag de candidate, par exemple `v0.1.0-rc.1`, sur ce commit.
5. Créer la release GitHub avec l’option **pre-release** ; joindre uniquement une archive du commit vérifié et ses empreintes.
6. Vérifier le tag, les fichiers de la release et les liens de documentation après publication.

Présenter la candidate comme une préversion à évaluer. Le passage à une version stable demande notamment la [recette de déploiement](GUIDE_AUTOHEBERGEMENT.md#12-recette-avant-ouverture) et les [essais réels SUPERPDP](GUIDE_SUPERPDP.md#10-recette-fournisseur-avant-production), ainsi que ceux des messageries utilisées. Aucun compte réel, sauvegarde ou document utilisateur ne doit accompagner la release.

## 6. Signalements et maintenance

Les tickets publics servent aux bugs et propositions illustrés par des données fictives. Pour une vulnérabilité, utiliser le [signalement privé](https://github.com/fmacfa/zefacture/security/advisories/new). Maintenir ce canal et les contacts de l’exploitant fonctionnels.

Après publication, surveiller les résultats de CI, les alertes de dépendances et les signalements. Les mises à jour doivent préserver les données existantes, les originaux et les droits, avec migrations et sauvegardes documentées.
