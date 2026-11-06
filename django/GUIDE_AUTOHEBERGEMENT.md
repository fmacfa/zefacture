# ZeFacture — guide complet de l’édition auto-hébergée

Édition Django candidate `0.1.0-rc.1`, mise à jour le 6 novembre 2026. Chaque exploitant utilise son propre domaine et ses propres applications externes. **https://zefacture.com** sert d’exemple dans ce guide ; la publication du code ne signifie pas qu’une instance publique ou des applications Google/Microsoft sont déjà actives.

Guides complémentaires, ajoutés le 6 novembre : [utilisation quotidienne et équipe](GUIDE_UTILISATEUR.md), [API et webhooks](GUIDE_API_WEBHOOKS.md), [publication GitHub](GUIDE_PUBLICATION_GITHUB.md). Développement et tests : [CONTRIBUTING.md](CONTRIBUTING.md) ; reprise des données : [MIGRATION.md](MIGRATION.md).

Le [guide SUPERPDP](GUIDE_SUPERPDP.md) couvre la connexion, l’émission et la réception, les originaux, les statuts, les reprises et la recette fournisseur. Sa migration est additive et l’activation reste explicite par entreprise.

## 1. Ordre de mise en service

1. Choisir le serveur, installer Docker et protéger les accès d’administration.
2. Installer une instance vide **ou** restaurer la sauvegarde de l’instance existante, selon les sections suivantes.
3. Renseigner le domaine, le contact et l’identité de l’exploitant ; mettre HTTPS en service.
4. Activer la double authentification des administrateurs et personnaliser chaque entreprise.
5. Créer les applications OAuth Google et Microsoft ; enregistrer leurs identifiants privés.
6. Autoriser un compte Gmail et un compte Outlook de test depuis ZeFacture, puis vérifier la réception des emails autorisés.
7. Tester une restauration hors de l’instance principale et effectuer la recette de la section 12.
8. Ouvrir les accès clients par invitations. Le dépôt GitHub exécute la CI lors des push et pull requests ; consulter son résultat sur le commit utilisé.

Les connexions Gmail/Outlook servent à **envoyer des emails au nom d’une entreprise**. La connexion au compte ZeFacture utilise son mot de passe et son second facteur. Une entreprise possède une connexion email active ; plusieurs entreprises peuvent choisir des fournisseurs différents.

## 2. Architecture et responsabilités

| Élément | Rôle | Données privées |
| --- | --- | --- |
| Caddy | HTTPS public, reverse proxy | Certificats TLS et clé de liaison avec Django |
| Django / Gunicorn | Interface, API, PDF et Factur-X | Accès à la base et au stockage |
| Worker Django | OCR, emails, webhooks et SUPERPDP | Même base et stockage que le serveur web |
| PostgreSQL 17 | Données et files de traitement | Volume Docker `database` |
| Volume `documents` | Originaux, imports, logos et chiffrement | Contient aussi `app.key` |
| Fichier `.env` | Configuration privée de l’exploitant | Clés, identifiants d’applications, accès serveur |

Le chiffrement des secrets ne protège pas contre un administrateur de serveur possédant simultanément la base et `app.key`. Protéger l’hôte, les volumes et les sauvegardes ensemble. La base n’a pas de port public ; le port applicatif est limité à l’adresse locale du serveur.

Pour commencer, prévoir un serveur Linux avec Docker Engine et le plugin Compose, un accès SSH et un stockage persistant. Deux vCPU et 4 Go de RAM constituent un point de départ à mesurer ; des imports volumineux et plusieurs rendus simultanés peuvent demander davantage. Les plafonds mémoire sont configurables avec `WEB_MEMORY_LIMIT`, `WORKER_MEMORY_LIMIT`, `DB_MEMORY_LIMIT` et `PROXY_MEMORY_LIMIT`.

Les exemples supposent que les fichiers du dossier `django/` sont présents et que les commandes sont exécutées depuis ce dossier. Utiliser le moteur Docker de l’hôte prévu et vérifier le contexte actif avec `docker context show` avant toute opération sur les données.

## 3. Nouvelle installation sans données antérieures

**Cette section concerne une base vide. Pour conserver l’instance existante, utiliser la section 4.**

Créer la configuration privée :

```sh
python3 tools/configure.py --production --domain zefacture.com
chmod 600 .env
```

La commande génère trois secrets aléatoires indépendants, ferme les inscriptions publiques et prépare HTTPS. Elle refuse d’écraser un fichier existant. Compléter `.env` avec l’identité et les coordonnées réelles de l’exploitant :

```dotenv
INSTANCE_CONTACT=Votre nom et votre véritable adresse de contact
INSTANCE_LEGAL=Identité légale complète de l’exploitant, adresse et coordonnées
INSTANCE_PRIVACY_NOTES=Durées réelles de conservation, sauvegardes et procédure de demande d’accès ou de suppression
```

Les pages `/contact`, `/mentions-legales`, `/confidentialite` et `/cgu` doivent être relues et adaptées à l’exploitation réelle. Une adresse telle que `support@zefacture.com` ne doit être renseignée que si elle existe et reçoit effectivement les messages.

Configurer le DNS `A` de `zefacture.com` vers le serveur. Ajouter `AAAA` uniquement si IPv6 fonctionne jusqu’au serveur. Autoriser TCP 80/443 pour HTTPS ; réserver SSH aux administrateurs. Le profil Caddy obtient et renouvelle les certificats lorsque le domaine et les ports sont accessibles :

```sh
docker compose --profile https up -d --build
docker compose ps
docker compose exec web python manage.py bootstrap --email vous@example.com --name "Votre nom" --company "Votre entreprise"
```

Remplacer les coordonnées de l’exemple par celles du premier administrateur. `bootstrap` demande son mot de passe dans le terminal, sans mot de passe prédéfini. Se connecter à `https://zefacture.com`, activer immédiatement **Mon compte → Sécurité**, puis vérifier les réglages de l’entreprise.

La clé proxy générée doit rester identique dans l’environnement de Caddy et celui de Django. Avec un autre reverse proxy, fixer `X-ZeFacture-Proxy-Key`, `X-ZeFacture-Client-IP` et `X-Forwarded-Proto` côté proxy, remplacer les valeurs reçues du navigateur et conserver Django inaccessible directement depuis Internet. Sans cette liaison, Django ne fait pas confiance aux en-têtes transmis.

HSTS est actif pour le domaine de production. `SECURE_HSTS_INCLUDE_SUBDOMAINS` et `SECURE_HSTS_PRELOAD` restent désactivés jusqu’à vérification de **tous** les services concernés par ces options. `check --deploy` les signale alors comme recommandations ; les activer est une décision de l’exploitant sur son domaine.

## 4. Déplacer l’instance existante sans perdre ses données

### 4.1 Sauvegarder l’instance source

Depuis son dossier Django :

```sh
python3 tools/backup.py --project zefacture --output backups/avant-transfert.tar.gz
```

Pour un moteur Docker distant ou un contexte nommé, sélectionner explicitement le contexte de l’instance source. La sauvegarde arrête les écritures web/worker, copie PostgreSQL, les fichiers et la configuration privée, calcule les empreintes puis redémarre les services. Garder aussi les sauvegardes antérieures. Transférer l’archive vers le serveur par un canal privé, avec des permissions restrictives.

### 4.2 Restaurer dans un projet neuf

Placer les sources Django sur le serveur, puis :

```sh
python3 tools/restore.py /chemin/prive/avant-transfert.tar.gz --project zefacture-prod --port 8090
```

Le projet cible ne doit posséder aucun volume existant. La restauration contrôle les empreintes et les chemins, recrée la base et les fichiers, puis applique les migrations additives. Elle conserve les identités, les mots de passe, les références, les originaux et `app.key`.

La configuration restaurée se trouve dans `backups/recovery-zefacture-prod/.env`. Les emails, webhooks et services externes y sont **verrouillés** :

```dotenv
EMAIL_DELIVERY_DISABLED=1
WEBHOOK_DELIVERY_DISABLED=1
EXTERNAL_SERVICES_DISABLED=1
```

Le worker n’est pas démarré par la restauration : les tâches sauvegardées restent en attente pendant la validation. Démarrer ensuite le worker explicitement avec les bons paramètres de projet, lorsque la reprise des traitements est voulue.

Vérifier les documents, les montants, les droits et les empreintes avant d’ouvrir cette copie. Les files existantes sont conservées. Une restauration n’est pas une occasion de rejouer leurs messages.

### 4.3 Passer la copie validée sur le domaine

Dans **ce même fichier privé**, conserver les clés de l’instance restaurée et son mot de passe PostgreSQL. Modifier les paramètres de publication :

```dotenv
APP_ENV=production
DEBUG=0
APP_URL=https://zefacture.com
DOMAIN=zefacture.com
ALLOWED_HOSTS=zefacture.com,localhost,127.0.0.1
TRUST_PROXY=1
SECURE_SSL_REDIRECT=1
MFA_REQUIRED_FOR_ADMINS=1
SENSITIVE_REAUTH_REQUIRED=1
```

Ajouter `PROXY_SHARED_SECRET` si l’ancienne configuration n’en possède pas ; générer une valeur privée avec `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`, puis la saisir uniquement dans le fichier de configuration. **Conserver `app.key` et `DJANGO_SECRET_KEY` d’origine.**

Pour utiliser ce fichier avec Compose, définir une seule fois, dans le terminal courant :

```sh
export ZEFACTURE_ENV_FILE="$PWD/backups/recovery-zefacture-prod/.env"
dc() { docker compose --project-name zefacture-prod --env-file "$ZEFACTURE_ENV_FILE" "$@"; }
dc --profile https up -d --build
```

Les exemples `docker compose` du reste du guide deviennent alors `dc`. Dans un nouveau terminal, redéfinir cette fonction et `ZEFACTURE_ENV_FILE` avant toute commande. Ne pas remplacer le fichier de configuration restauré par celui d’une installation neuve.

Pour préparer les autorisations OAuth, retirer le verrou `EXTERNAL_SERVICES_DISABLED` uniquement sur l’instance destinée à servir. Garder les verrous email/webhook pendant le contrôle des files et des destinataires. Activer ensuite chaque livraison volontairement. Une copie de reprise conservée pour validation doit garder les trois verrous.

Au moment de la bascule définitive, arrêter les écritures sur l’ancienne instance. Les modifications faites simultanément sur deux instances ne se synchronisent pas.

## 5. Google : créer l’application Gmail depuis zéro

### Projet, consentement et permissions

1. Avec le compte Google de l’exploitant, ouvrir [Google Cloud Console](https://console.cloud.google.com/), accepter soi-même les éventuelles conditions du fournisseur et créer un projet dédié, par exemple `ZeFacture Production`.
2. Dans la bibliothèque des API, activer **Gmail API** pour ce projet.
3. Ouvrir **Google Auth Platform → Branding** : nom de l’application, véritable email de support et coordonnées du développeur.
4. Renseigner l’accueil `https://zefacture.com/`, la confidentialité `https://zefacture.com/confidentialite` et les conditions `https://zefacture.com/cgu`. Déclarer `zefacture.com` comme domaine autorisé ; effectuer la vérification de propriété demandée par Google.
5. Dans **Audience**, choisir **External** pour des entreprises et comptes Gmail différents. Ajouter les comptes réservés aux essais dans les utilisateurs de test. Une application Internal est réservée à l’organisation Workspace concernée.
6. Dans **Data Access**, déclarer uniquement les scopes utilisés ci-dessous. Ces réglages suivent le [parcours officiel de consentement Google](https://developers.google.com/workspace/guides/configure-oauth-consent).

```text
openid
email
https://www.googleapis.com/auth/gmail.send
```

`gmail.send` est une permission **sensible** d’envoi. Elle ne donne pas la lecture de la boîte. Sa classification figure dans les [scopes officiels Gmail](https://developers.google.com/workspace/gmail/api/auth/scopes).

### Client Web et secret

Dans **Clients**, créer un client OAuth de type **Web application**, nommé par exemple `ZeFacture Web Production`. Ajouter exactement cette URI aux adresses de redirection autorisées :

```text
https://zefacture.com/app/emails/connexion/google/retour
```

Respecter le protocole, le domaine, la casse et le chemin, sans slash final. Ce parcours fonctionne côté serveur ; les origines JavaScript ne sont pas nécessaires. Copier le Client ID et le secret dans la configuration privée de l’instance, conformément au [guide des identifiants Web Google](https://developers.google.com/workspace/guides/create-credentials#web-application) :

```dotenv
GOOGLE_MAIL_CLIENT_ID=valeur_du_client_id
GOOGLE_MAIL_CLIENT_SECRET=valeur_du_secret
```

Recréer les processus après modification de l’environnement :

```sh
docker compose up -d --force-recreate web worker
```

### Essais puis ouverture aux clients

Dans ZeFacture : **Emails → Connexion → Gmail / Google Workspace → Enregistrer → Se connecter avec Google**. Choisir le compte de test, lire les permissions et accorder personnellement le consentement. Le compte doit posséder une boîte Gmail utilisable.

Un projet External resté en Testing émet, pour ces permissions, des refresh tokens expirant après **sept jours**. Le reconnecter régulièrement n’est pas une configuration de production durable. Cette limite est documentée dans les [règles de durée des autorisations Google](https://developers.google.com/identity/protocols/oauth2).

Avant une ouverture publique à vos clients, préparer le passage en production et la vérification des permissions sensibles : identité et domaine vérifiés, pages publiques accessibles, justification de l’envoi, contacts à jour et démonstration du parcours si demandée. Les exceptions dépendent du contexte de l’application ; la décision appartient à Google. Suivre sa [procédure de vérification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification).

## 6. Microsoft : créer l’application Outlook depuis zéro

### Obtenir le droit d’inscrire une application

Créer ou utiliser un compte Microsoft disposant d’un annuaire Entra et des droits d’inscription. Un compte Outlook personnel seul ne garantit pas la possibilité de créer cet annuaire. Vérifier les prérequis actuels — compte Azure, abonnement et rôle — dans le [guide d’inscription Entra](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app). L’exploitant choisit lui-même son offre et accepte les conditions éventuelles.

### Inscription, plateforme et permissions

Dans [Microsoft Entra](https://entra.microsoft.com/), sélectionner le bon annuaire, puis **Entra ID → App registrations → New registration**. Nom : `ZeFacture Production`. Choisir les comptes de **tous les annuaires Entra et les comptes Microsoft personnels** pour servir Microsoft 365 et Outlook.com. ZeFacture utilise actuellement l’autorité Microsoft `common`.

Ajouter la plateforme **Web** et cette URI exacte :

```text
https://zefacture.com/app/emails/connexion/microsoft/retour
```

Conserver les flux implicites et public client désactivés. Les retours doivent correspondre à l’inscription ; voir les [règles des URI Microsoft](https://learn.microsoft.com/en-us/entra/identity-platform/reply-url).

Dans **API permissions**, utiliser Microsoft Graph en mode **Delegated permissions** : `User.Read` pour l’identité du compte et `Mail.Send` pour l’envoi. Le parcours demande également `openid profile email offline_access`. Les politiques d’une organisation cliente peuvent imposer le consentement de son administrateur. `Mail.Send` délégué convient aux comptes professionnels et personnels, selon la [référence sendMail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0).

### Secret d’application et connexion

Dans **Certificates & secrets**, créer un client secret, noter sa date d’expiration et copier sa **Value** immédiatement. Copier également l’**Application (client) ID** de la page Overview. L’identifiant du secret et le Directory/tenant ID ne remplacent pas ces valeurs.

Cette édition utilise un secret d’application côté serveur. Microsoft recommande les certificats pour les clients confidentiels de production ; l’authentification par certificat n’est pas implémentée dans cette édition. Si la politique de l’organisation l’impose, cette extension devra précéder son déploiement. Voir la [gestion officielle des identifiants d’application](https://learn.microsoft.com/en-us/entra/identity-platform/how-to-add-credentials).

```dotenv
MICROSOFT_MAIL_CLIENT_ID=valeur_application_client_id
MICROSOFT_MAIL_CLIENT_SECRET=valeur_du_secret
```

Recréer web et worker, puis dans ZeFacture : **Emails → Connexion → Outlook / Microsoft 365 → Enregistrer → Se connecter avec Microsoft**. Autoriser un compte possédant une boîte Outlook.com ou Exchange Online utilisable. L’annuaire Entra qui porte l’application n’est pas, à lui seul, une boîte email.

Conserver un responsable et une date de renouvellement pour le secret. Un renouvellement ou une révocation peut demander une nouvelle connexion des comptes concernés. Les contrôles d’éditeur et de consentement des organisations clientes restent des étapes externes à valider avant ouverture.

## 7. Connexions par entreprise et SMTP

L’exploitant enregistre les deux applications une fois dans l’environnement privé du serveur. Chaque entreprise cliente autorise ensuite **son propre compte** depuis l’interface. Les paramètres avancés permettent une application OAuth dédiée à une entreprise ; son ID et son secret doivent être fournis ensemble. Le bouton **Utiliser l’application OAuth du serveur** efface cette dérogation et demande une reconnexion.

| Choix dans l’interface | Comportement |
| --- | --- |
| Configuration du serveur | Transport global de l’exploitant |
| Boîte locale | Conservation dans la file, sans livraison |
| SMTP | Serveur et identifiants de l’entreprise, TLS obligatoire |
| Gmail / Outlook | Envoi avec le compte autorisé, jetons chiffrés |

Pour SMTP, demander à l’hébergeur le nom DNS, le port, le chiffrement, l’identifiant et le secret. Utiliser STARTTLS, généralement 587, ou TLS direct, généralement 465. Les ports 25 et 2525 sont acceptés avec TLS. Les certificats restent vérifiés ; un serveur privé doit être autorisé explicitement par l’exploitant dans `OUTBOUND_ALLOWED_HOSTS`.

Un champ mot de passe vide conserve uniquement le secret d’une configuration inchangée. Toute modification du serveur, du port, du chiffrement ou de l’identifiant impose sa ressaisie. Le secret enregistré n’est jamais réaffiché.

**Vérifier la connexion sans envoyer** contrôle l’accès au fournisseur. **Envoyer un test à mon adresse** prépare un message vers l’adresse du compte ZeFacture connecté ; l’utilisateur déclenche cet envoi lui-même. Contrôler ensuite sa réception, les indésirables et le suivi des emails.

Une acceptation Gmail/SMTP/Graph ne prouve pas la réception finale. En particulier, `202 Accepted` de Graph confirme la prise en charge, pas la livraison. Les erreurs de livraison ultérieures et les rebonds se consultent aussi chez le fournisseur.

Un changement de connexion invalide les anciennes routes d’envoi. Les messages concernés doivent être relancés explicitement après vérification. Une autorisation expirée ou révoquée est indiquée comme nécessitant une reconnexion. La déconnexion efface les jetons locaux et le secret SMTP local ; le consentement chez Google/Microsoft se retire également dans les applications autorisées du compte. Retirer un consentement d’application partagée peut affecter d’autres connexions du même compte.

Pour les emails de **réinitialisation du mot de passe ZeFacture**, configurer également le transport global du serveur : les connexions d’entreprise ne le remplacent pas. Avec SMTP serveur :

```dotenv
MAIL_TRANSPORT=smtp
MAIL_FROM_EMAIL=adresse_expediteur_reellement_autorisee
MAIL_FROM_NAME=ZeFacture
SMTP_HOST=serveur_fourni_par_votre_messagerie
SMTP_PORT=587
SMTP_USERNAME=identifiant_fourni
SMTP_PASSWORD=secret_fourni
SMTP_TLS=1
SMTP_SSL=0
```

Pour TLS direct, utiliser le port fourni, `SMTP_SSL=1` et `SMTP_TLS=0`. Faire configurer SPF, DKIM et DMARC selon les indications de votre fournisseur et du domaine d’envoi.

## 8. Personnalisation pour les clients

Un administrateur accède à **Mon entreprise → Personnalisation**. Les réglages appartiennent à l’entreprise sélectionnée :

- Nom d’affichage et couleur principale ; contraste adapté pour garder le texte lisible.
- Logo PNG/JPEG/WebP, limité à 2 Mo et 16 mégapixels, normalisé en PNG sans métadonnées ; SVG refusé.
- Présentation classique ou compacte des documents, devise et délais par défaut.
- Adresse de réponse, signature et modèles des emails de devis, factures/avoirs et relances.

Les coordonnées légales, l’IBAN/BIC, les conditions de paiement, les mentions de bas de facture et les préfixes de numérotation restent dans **Mon entreprise**. Le nom légal reste présent dans les documents, même avec un nom commercial d’affichage.

Exemple de modèle en texte simple :

```text
Objet : Votre devis {document_number} — {company}

Bonjour {customer},

Voici notre devis de {total} {currency}.
Vous pouvez le consulter ici : {document_link}

{signature}
```

Variables : `{company}`, `{customer}`, `{document_number}`, `{total}`, `{currency}`, `{due_date}`, `{document_link}`, `{original_body}`, `{signature}`. L’interface refuse les attributs, conversions et expressions de programmation. Le lien d’acceptation est conservé si le modèle de devis l’omet. Laisser l’objet et le corps vides rétablit le modèle standard. Les emails déjà préparés conservent leur contenu et leur adresse de réponse.

La présentation est figée dans chaque nouveau document ou brouillon sauvegardé. Les factures et devis **déjà émis conservent leurs originaux** ; changer le logo ne les régénère pas. Les anciens logos nécessaires à leurs instantanés restent dans les sauvegardes.

Chaque utilisateur choisit un thème clair, sombre ou système et la densité des tableaux dans **Mon compte → Préférences d’affichage**. L’interface adapte ses formulaires aux petits écrans ; les tableaux larges défilent dans leur propre zone. La navigation, les labels, le clavier et la réduction des animations sont pris en compte.

## 9. Entreprises, équipe et usage quotidien

| Rôle | Accès principal |
| --- | --- |
| Propriétaire / administrateur | Paramètres, équipe, messagerie, clés et webhooks |
| Comptable | Données métier et finances, sans administration des intégrations |
| Membre | Travail métier selon les écrans autorisés, sans administration financière |
| Lecteur | Consultation autorisée, sans modification |

Créer ou sélectionner l’entreprise dans **Mes entreprises**, puis inviter les collaborateurs depuis **Collaborateurs**. Chaque invitation vise une adresse et expire ; un utilisateur ne peut pas sélectionner une entreprise dont il n’est pas membre actif. Les notes de frais possèdent aussi des règles de visibilité par auteur et rôle.

Le CRM distingue prospects/clients et professionnels/particuliers, avec contacts, opportunités, tâches et activités. Les stocks utilisent des mouvements explicites d’entrée/sortie ; une facture ne décrémente pas automatiquement le stock. Les périodes et comparatifs sont accessibles depuis le dashboard et les rapports. Les exports CSV, Excel et PDF reprennent le jeu de données et les filtres choisis, au-delà de la pagination de l’écran. Les devises restent séparées, sans conversion de change automatique.

Les PDF/Factur-X et l’OCR sont générés localement. L’analyse IA reste facultative et utilise le service configuré par l’exploitant ; son activation transmet le texte concerné. Une transmission PDP nécessite également une configuration et une action dédiées. Le logiciel conserve l’historique mais ne remplace pas la validation des coordonnées, montants et mentions avant émission.

## 10. Sécurité, second facteur et intégrations

En production, les administrateurs doivent activer un second facteur TOTP dans **Mon compte → Sécurité**. Scanner le QR code ou saisir la clé dans une application d’authentification, confirmer un code puis conserver les dix codes de secours affichés **une seule fois**. Chaque code est à usage unique. Un code TOTP déjà consommé est refusé ; attendre le prochain code si nécessaire.

Les changements sensibles demandent une vérification récente du mot de passe, et du second facteur s’il est activé. Cette vérification dure quinze minutes. Les autres sessions sont invalidées lors d’un changement de mot de passe ou de second facteur. Les sessions expirent au plus après 24 heures et à la fermeture du navigateur ; certains navigateurs peuvent restaurer leurs cookies de session avec la fonction de reprise des onglets.

Sans téléphone ni code de secours, l’exploitant doit vérifier l’identité par un autre moyen avant d’utiliser la récupération locale :

```sh
docker compose exec web python manage.py reset_mfa --email adresse_du_compte --confirm-identity-checked
```

Cette commande invalide toutes les sessions et force l’administrateur à réactiver son second facteur. Une réinitialisation du mot de passe par email ne désactive pas la double authentification.

Les actions sensibles sont consultables dans le **Journal de sécurité** de l’entreprise ; les événements personnels de sécurité figurent dans le compte. Ce journal applicatif ne contient pas de secret ni le corps des emails. L’accès serveur conserve l’autorité d’administration de la base.

Les clés API sont affichées une fois puis recherchées par empreinte ; les révoquer lorsqu’elles ne servent plus. Les webhooks sont configurés et mis en pause dans **API & intégrations**. Vérifier la signature HMAC horodatée, refuser les événements trop anciens et dédoublonner leur identifiant. Le contrat et un exemple de validation se trouvent dans le [guide API et webhooks](GUIDE_API_WEBHOOKS.md). Les livraisons échouées sont réessayées jusqu’à six tentatives.

Conserver les fichiers `.env`, `app.key`, les sauvegardes et les exports métier hors du dépôt Git. Utiliser une sauvegarde chiffrée hors serveur et limiter ses destinataires. Les liens temporaires et les journaux d’exploitation restent privés ; les services de supervision doivent retirer les paramètres sensibles des URL.

## 11. Sauvegardes, mises à jour et retour arrière

Créer une sauvegarde régulière avec `tools/backup.py`, puis en stocker une copie privée hors serveur. Planifier cette tâche selon le volume et la perte de données maximale acceptable. Tester périodiquement `tools/restore.py` sur **un nouveau projet**, avec les verrous de livraison conservés. Garder ensemble base, fichiers, configuration et `app.key`.

Avant une mise à jour :

```sh
docker compose build --pull
python3 tools/backup.py --project zefacture --output backups/avant-mise-a-jour.tar.gz --leave-stopped
docker compose up -d --wait web worker
docker compose exec web python manage.py check
docker compose exec web python manage.py check_readiness --production --require-oauth
```

Adapter `--project` et `--env-file` à l’installation ; pour le projet restauré, utiliser les paramètres de la section 4. L’option `--leave-stopped` garde les écritures arrêtées après une sauvegarde réussie. Les migrations sont exécutées avant le démarrage du web ; le worker démarre après son contrôle de santé.

Si la recette échoue, garder l’instance hors des écritures publiques et restaurer la sauvegarde sous un nouveau projet. Une image précédente seule ne restaure pas un schéma ou des données modifiés. Ne pas supprimer les volumes avec `down -v` sur une instance contenant des données à conserver.

Mettre également à jour le système hôte et les images PostgreSQL/Caddy selon leurs versions compatibles. Conserver un historique des versions, sauvegardes, tests et renouvellements de secrets. Après modification d’une application OAuth ou de ses identifiants, vérifier les connexions et les files avant de reprendre les envois.

## 12. Recette avant ouverture

Le diagnostic est en lecture seule, sans appel aux fournisseurs ni affichage de secrets :

```sh
docker compose exec web python manage.py check_readiness --production --require-oauth
docker compose exec web python manage.py check --deploy
```

Il contrôle configuration, base, migrations, clé privée, cookies, proxy, politique MFA et présence des applications OAuth globales. La sortie JSON s’obtient avec `--json`. Un contrôle bloquant renvoie un code d’erreur. Les vérifications externes restent explicitement marquées manuelles.

- [ ] DNS et certificat HTTPS vérifiés depuis un autre réseau ; aucune exposition publique de PostgreSQL ou du port Gunicorn.
- [ ] Pages publiques, identité et contact réels relus ; exigences de l’application Google satisfaites.
- [ ] Administrateurs équipés en MFA, codes de secours conservés et droits d’équipe vérifiés.
- [ ] Gmail autorisé, connexion vérifiée et email de test reçu ; application Google prête pour l’audience prévue.
- [ ] Outlook autorisé, connexion vérifiée et email de test reçu ; expiration du secret suivie.
- [ ] Réinitialisation de mot de passe testée via la messagerie du serveur.
- [ ] Devis, facture Factur-X, paiement partiel, CRM, stock, comparaison et exports contrôlés sur données fictives.
- [ ] Logo, mentions, modèle email et lecture sur téléphone contrôlés pour une entreprise de test.
- [ ] Clés API et webhooks limités aux destinataires nécessaires ; files et statuts revus avant activation.
- [ ] Sauvegarde hors serveur et restauration intégrale contrôlées ; copie de validation maintenue sans sorties externes.

Les tests locaux et la CI vérifient l’application sur des données fictives. Ils ne valident pas automatiquement la propriété du domaine, le certificat public, l’approbation Google, le consentement d’un tenant Microsoft ou la réception réelle d’un message.

## 13. CI et dépôt GitHub

Le workflow principal est `../.github/workflows/django.yml`, dans le [dépôt public](https://github.com/fmacfa/zefacture). GitHub Actions exécute sur les push et pull requests :

1. Le contrôle des fichiers publiés, la recherche de secrets dans le contenu et l’historique, l’analyse Python et l’audit des versions verrouillées.
2. Les tests avec PostgreSQL 17 sur Python 3.13 et 3.14, les migrations, les fichiers statiques et les contrôles Django de production.
3. La construction Docker, les parcours navigateur, les exports et une sauvegarde/restauration vérifiée par empreintes sur données fictives.
4. Le statut final **CI validée**, à rendre obligatoire pour la fusion dans la branche principale.

Les actions sont fixées par SHA, le token GitHub est limité à la lecture et les tests n’utilisent aucun secret de production. Les projets Docker de CI sont neufs et supprimables ; leur nettoyage est réservé à ces projets fictifs. Les preuves publiées par la CI sont limitées aux captures et résultats synthétiques sélectionnés, avec conservation de sept jours. Dependabot prépare des propositions de mise à jour ; aucune fusion automatique n’est activée. Les sources et workflows PHP historiques ne font pas partie de cette distribution Django.

Pour lancer la validation locale équivalente, depuis le dossier Django :

```sh
npm ci --prefix tests/browser
cd tests/browser
npx playwright install chromium
cd ../..
CI=true CI_HTTP_PORT=8093 bash tools/ci_e2e.sh
```

Cette commande crée des projets portant le préfixe `zefacture-ci-` et refuse un projet déjà doté de volumes. Employer un port libre. Les tests Python utilisent une base de test dédiée ; suivre [CONTRIBUTING.md](CONTRIBUTING.md) pour l’environnement de développement. Vérifier le résultat GitHub Actions du commit retenu ; une validation locale seule ne prouve pas la réussite d’une exécution distante.

## 14. Dépannage

| Symptôme | Vérifications |
| --- | --- |
| Google `redirect_uri_mismatch` / Microsoft `AADSTS50011` | Copier l’URI affichée par ZeFacture ; vérifier `APP_URL`, domaine, HTTPS et slash final |
| Google refuse un testeur | Compte ajouté à Audience, type External/Internal, Gmail API activée et règles Workspace |
| Gmail se déconnecte après sept jours | Projet External encore en Testing ; préparer le passage en production et la vérification |
| Microsoft demande un administrateur | Politique de consentement du tenant client ; faire examiner les permissions déléguées prévues |
| Outlook refuse l’envoi | Boîte utilisable, secret non expiré, permissions Mail.Send et identité du compte ; consulter les règles du fournisseur |
| Bouton OAuth désactivé | Paire d’identifiants serveur absente, ou dérogation d’entreprise incomplète |
| Boucle HTTPS | Clé proxy absente/différente, `TRUST_PROXY`, en-tête HTTPS ou domaine incohérent |
| Formulaire refusé en CSRF | Origine réelle, domaine autorisé, cookies et proxy ; conserver la politique Referrer prévue par l’application |
| Erreur SMTP après changement | Ressaisir le secret ; vérifier hôte, port, TLS, certificat et autorisation d’un éventuel hôte privé |
| Connexion refusée après MFA | Heure du téléphone/serveur, code déjà utilisé, prochain code ou code de secours inutilisé |
| Secret illisible | Restaurer `app.key` d’origine avec la bonne base ; ne pas générer une autre clé |
| Email conservé sans livraison | Transport outbox, verrous globaux, worker, statut de connexion ou ancienne route invalidée |
| OCR/analyse en échec | Taille/pages, ressources du conteneur, langues OCR, configuration facultative IA et verrou externe |
| Copie de reprise silencieuse | Les trois verrous sont voulus ; les garder sur une instance réservée à la validation |

Consulter `docker compose ps` et les journaux avec les droits de l’exploitant. Ne partager que des extraits expurgés des identifiants, destinataires, documents et jetons. Les limites connues comprennent une seule messagerie active par entreprise, des emails Outlook limités à 4 000 000 octets après encodage MIME, et l’absence de configuration des boîtes partagées/alias ou de l’authentification Microsoft par certificat.

## 15. Distribution open source

Licence AGPL-3.0-only. Le lien **Code source** reste visible dans l’instance. Reconstruire `python manage.py build_source` après personnalisation ; l’image Docker le fait pendant sa construction. L’archive inclut les sources et ce guide, et exclut configuration privée, données, sauvegardes, dépendances installées et résultats locaux. Vérifier cette séparation avant toute publication du futur dépôt. Les licences tierces sont décrites dans [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
