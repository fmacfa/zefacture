# Migration depuis ZeFacture PHP

## Principe

La version Django utilise ses propres base et stockage. Le PHP et sa base MySQL/SQLite restent disponibles pour contrôle ou retour arrière. Ne jamais diriger Django vers la base MySQL d'origine.

Les 22 tables métier historiques sont reprises intégralement, avec leurs identifiants internes et publics, séquences de numérotation, valeurs monétaires, dates, snapshots JSON, événements, clés API hachées et secrets chiffrés. Les tables internes Django sont ajoutées séparément.

Les mots de passe PHP bcrypt/Argon2 restent valides. Lors d'une connexion réussie, Django met à jour le hachage. Les sessions PHP ne sont pas réutilisées : une nouvelle connexion est nécessaire. Les anciens tokens PDP restent déchiffrables grâce à la copie de `storage/app.key`.

## Procédure

1. Mettre l'application PHP en maintenance pour empêcher toute écriture jusqu'à la bascule. L'export utilise une lecture cohérente de la base ; aucun export ne peut inclure des écritures effectuées après son instantané.
2. Sauvegarder le dossier PHP et sa configuration privée.
3. Installer les dépendances Python du dossier Django et exporter :

       python manage.py migrate_legacy export /chemin/prive/legacy.tar.gz --source /chemin/zefacture-php

   Le dossier source doit contenir `.env` et `storage/`. La connexion MySQL reprend les paramètres de cet environnement. Les fichiers référencés et les répertoires de transcription sont copiés et hachés. Un fichier manquant arrête l'export.

4. Créer une instance Docker vierge avec les sources Django. Ne pas lancer `bootstrap` si des comptes doivent être importés.

       python3 tools/configure.py
       docker compose up -d --build
       docker compose exec -T web python manage.py migrate_legacy import - < /chemin/prive/legacy.tar.gz
       docker compose exec -T web python manage.py migrate_legacy verify - < /chemin/prive/legacy.tar.gz

5. Le résultat doit indiquer `equal: true`. La comparaison vérifie chaque colonne et chaque fichier, et pas seulement les nombres de lignes. L'import ne réécrit aucun original ni montant.
6. Se connecter avec un compte historique, vérifier les entreprises, documents et pièces jointes, puis basculer le domaine. Réactiver les emails uniquement après contrôle de la boîte d'envoi et des coordonnées de l'instance.

L'import refuse toute base métier non vide. En cas de vérification infructueuse, la transaction est annulée et seuls les fichiers nouvellement copiés pendant cette tentative sont supprimés. Réparer la cause et recommencer sur une cible vide.

## Après bascule

Toute nouvelle opération appartient à Django. Conserver le PHP en lecture seule. Un retour au PHP après de nouvelles écritures Django nécessite une réconciliation des données ; restaurer aveuglément l'ancien instantané ferait perdre ces opérations.

La comparaison stricte à l'archive d'origine est destinée à la migration. Elle doit naturellement différer après une modification légitime ou le renouvellement d'un mot de passe. Utiliser ensuite les sauvegardes complètes Django.

## Compatibilité fonctionnelle

Les URL métier et les routes API sont conservées. L'interface regroupe l'ancien assistant devis en un formulaire avec sauvegarde du brouillon. L'OCR devient un travail local en arrière-plan ; l'analyse IA reste optionnelle. SMTP remplace l'envoi via `mail()` PHP ; le mode de stockage local `outbox` reste le défaut.

La génération Factur-X et la transmission sont deux actions explicites dans l'interface. La génération seule ne déclenche pas d'envoi externe. Les factures historiques déjà émises sont téléchargées depuis leur original, même si elles ont été produites par le PHP.
