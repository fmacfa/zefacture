# Sécurité

Pour une vulnérabilité du logiciel, utiliser le [signalement privé du dépôt](https://github.com/fmacfa/zefacture/security/advisories/new). Pour un problème propre à une instance, contacter en privé son exploitant indiqué dans la page Contact. Ne pas publier de secrets ni de données métier dans un ticket public. Fournir la version, le scénario, l'impact et un exemple fictif.

Les inscriptions publiques sont fermées par défaut. Les sessions, CSRF, contrôles de rôle et de société s'appliquent aux routes métier ; les clés API sont hachées et limitées à leur entreprise. Les fichiers passent par des vues authentifiées. Les secrets PDP utilisent une clé locale indépendante du secret Django.

En production, utiliser HTTPS et PostgreSQL, conserver le service applicatif derrière le proxy, limiter l'accès à la base et protéger les sauvegardes. Ne pas activer DEBUG ou publier le port de PostgreSQL.

Le code source disponible via /source exclut la configuration privée et les données. Si vous personnalisez la distribution, conserver cette séparation et reconstruire l'archive.

En production, les administrateurs activent le second facteur TOTP et conservent leurs codes de secours. Les changements sensibles exigent une vérification récente. Les secrets SMTP restent liés à leur destination et à leur compte : tout changement d’hôte, de port, de chiffrement ou d’identifiant exige leur ressaisie. Les applications OAuth utilisent des paires d’identifiants cohérentes, PKCE et une consommation d’état atomique en base ; une reconnexion change la version des routes d’envoi.

Le proxy doit remplacer les en-têtes clients et présenter la clé privée `PROXY_SHARED_SECRET`. Le serveur ignore les informations de forwarding sans cette liaison. Les conteneurs applicatifs sont non root, leur système de fichiers est en lecture seule hors volumes privés et `/tmp`, et des plafonds mémoire/processus sont appliqués. Les restaurations verrouillent les sorties externes jusqu’à validation explicite de l’exploitant.

Le diagnostic `check_readiness --production --require-oauth` n’effectue aucun appel réseau. Sa réussite ne remplace pas la vérification du TLS public, des consentements et politiques des fournisseurs, de la réception des emails ou d’une restauration hors serveur.

SUPERPDP reste désactivé pour la synchronisation jusqu’à l’activation de chaque entreprise. Les liaisons conservent l’entreprise distante et son environnement ; les dépôts et téléchargements utilisent le jeton exact vérifié pour ce lot. Les originaux sont contrôlés par empreinte, les événements sont dédoublonnés et les POST incertains ne sont pas rejoués automatiquement. Une reprise exige la synchronisation et la vérification explicite de l’absence du dépôt. Les restaurations bloquent ces échanges avec `EXTERNAL_SERVICES_DISABLED` et conservent les files pour leur examen.
