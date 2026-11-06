# CRM, rapports et intégrations

Pour les parcours pas à pas, consulter le [manuel utilisateur](GUIDE_UTILISATEUR.md). Les routes exactes, exemples d’appels, limites et exemple de vérification des signatures figurent dans le [guide API et webhooks](GUIDE_API_WEBHOOKS.md).

## CRM et équipe

Dans **Clients**, créer un professionnel ou un particulier et choisir sa relation : prospect, client, fournisseur ou les deux. Les anciennes fiches restent disponibles et sont considérées comme clients tant qu'aucune relation différente n'est enregistrée.

La fiche rassemble coordonnées, contacts supplémentaires, étiquettes, notes internes, responsable, opportunités, activités, relances, documents et suivi des emails. Une opportunité suit les étapes À qualifier, Qualifiée, Proposition, Négociation, Gagnée ou Perdue, avec montant, devise, responsable et date de clôture prévue. Gagner une opportunité transforme un prospect en client ; cela ne crée pas de facture automatiquement.

L'écran **CRM** filtre les opportunités par étape, responsable et recherche. Les relances restent visibles jusqu'à leur réalisation. Les responsables proposés sont les membres actifs de l'entreprise. Les lecteurs consultent ; les membres, comptables et administrateurs peuvent écrire. Les paramètres email et webhooks restent réservés aux administrateurs/propriétaires. Les justificatifs de frais des autres utilisateurs conservent leur restriction d'accès.

## Périodes, comparaisons et exports

Le dashboard et **Rapports** acceptent une période libre, le mois, trimestre ou l'année en cours, ou toutes les dates. Comparaison avec la période précédente de même durée, la même période l'année précédente, des dates personnalisées, ou aucune comparaison. Le 29 février se compare au 28 février d'une année non bissextile. Un pourcentage d'évolution n'est pas inventé lorsque la base de comparaison est nulle.

Ventes et achats suivent la date d'émission et excluent brouillons, annulations, refus et simulations. Les avoirs sont déduits. Les encaissements/décaissements suivent les transactions bancaires comptabilisées. Ces indicateurs restent distincts, et chaque devise est séparée. Le dashboard affiche le reste dû **actuel** des documents sélectionnés ; il ne reconstitue pas le solde historique de chaque facture avant ses paiements. Les opportunités gagnées utilisent leur date de clôture et leur état actuel.

Les exports **PDF, Excel (.xlsx) et CSV** portent sur toutes les lignes sélectionnées, au-delà de la pagination : synthèse/comparatifs, documents, lignes détaillées, clients/prospects, contacts, opportunités, activités, stocks, mouvements, transactions, TVA, frais et prévisions. Ils sont réservés aux rôles ayant accès aux finances.

Les limites de jour suivent le fuseau de l'instance, y compris pour les horodatages UTC historiques. Clients, contacts et activités sont filtrés par leur date de création ; documents par émission ; mouvements et transactions par date d'opération ; frais par date de dépense ; prévisions par échéance. Le stock est recalculé à la fin de la période à partir des mouvements enregistrés. Le prix affiché est le prix actuel du catalogue, pas une valorisation comptable historique. Les prévisions manuelles historiques restent en EUR. La TVA suit la logique par date d'émission conservée dans l'application.

Les CSV neutralisent les formules. Les cellules Excel saisies par l'utilisateur restent du texte ; les codes postaux et identifiants conservent leurs zéros. Les textes dépassant la capacité d'une cellule Excel sont conservés par morceaux dans un onglet **Textes longs**, avec ligne, colonne et ordre à concaténer. Les nombres dépassant 15 chiffres significatifs sont exportés comme texte pour préserver leur précision. Les cellules longues des PDF continuent sur des lignes « Suite ». Au-delà de 10 000 enregistrements, choisir Excel/CSV ou réduire la période pour le PDF.

## Connecter une messagerie depuis l'interface

Ouvrir **Emails → Configurer ma messagerie**. Chaque entreprise choisit une connexion active :

- **Configuration du serveur** : conserve le transport défini dans l'environnement, pour compatibilité.
- **Boîte locale** : prépare les messages sans les livrer.
- **SMTP** : saisir expéditeur, hôte, port, chiffrement STARTTLS ou TLS direct, identifiant et mot de passe/mot de passe d'application. Enregistrer puis vérifier la connexion. Ports pris en charge : 25, 465, 587 et 2525, toujours avec TLS.
- **Google** : enregistrer les identifiants OAuth de l'application, puis utiliser **Se connecter avec Google**.
- **Microsoft** : même principe avec **Se connecter avec Microsoft**, pour comptes Outlook personnels et Microsoft 365 autorisés par leur organisation.

Le test de connexion n'envoie pas de message. **Envoyer un test à mon adresse** met un message en file à l'adresse de l'utilisateur connecté. Son résultat apparaît dans Emails. Les devis et factures conservent leur PDF original joint. Un message CRM se rédige dans la fiche du client et peut viser l'adresse principale ou un contact de cette fiche.

Une modification de la connexion n'envoie pas automatiquement les anciens emails. Les messages déjà liés à une ancienne configuration sont bloqués pour éviter de changer silencieusement d'expéditeur ; les relancer explicitement dans Emails après vérification. « sent » indique l'acceptation par le serveur/fournisseur, pas la réception ou la lecture. SMTP et les API ne garantissent pas une livraison exactement une fois lors d'une panne après acceptation distante ; vérifier avant une relance manuelle.

Les mots de passe, secrets d'application et jetons sont chiffrés avec la clé `app.key` existante. Une déconnexion efface les jetons locaux et repasse la connexion en boîte locale. Révoquer également l'autorisation depuis Google/Microsoft si nécessaire. Les réinitialisations de mot de passe du compte ZeFacture utilisent la messagerie de l'instance, car un compte peut appartenir à plusieurs entreprises.

### Déclarer les applications OAuth de l'installation

Une instance auto-hébergée doit posséder ses propres identifiants auprès du fournisseur. Ils se renseignent dans les paramètres avancés de l'écran email ; aucune modification de code n'est nécessaire. Le serveur peut aussi définir `GOOGLE_MAIL_CLIENT_ID`, `GOOGLE_MAIL_CLIENT_SECRET`, `MICROSOFT_MAIL_CLIENT_ID` et `MICROSOFT_MAIL_CLIENT_SECRET` comme valeurs par défaut.

**Google** : créer un client OAuth de type Application Web dans Google Cloud, activer Gmail API, configurer le consentement et déclarer l'URL de retour affichée par ZeFacture. L'autorisation demandée couvre l'identité et `gmail.send`, sans lecture de la boîte. Le mode de test et la vérification de l'application suivent les règles Google et peuvent limiter les utilisateurs ou la durée des autorisations.

**Microsoft** : inscrire une application Web dans Microsoft Entra, choisir les types de comptes voulus (organisations et personnels pour couvrir Microsoft 365 et Outlook), déclarer l'URL de retour, créer un secret et saisir sa **valeur**. Permissions déléguées : `User.Read`, `Mail.Send` et accès durable `offline_access`, avec identité OpenID. Les politiques d'organisation peuvent imposer un consentement administrateur.

L'instance utilise HTTPS, sauf pour les essais sur localhost/127.0.0.1. Les retours OAuth sont liés à l'utilisateur, l'entreprise, la session et la version de configuration, avec état à usage unique et PKCE. Les jetons expirés sont renouvelés avec rotation du jeton de renouvellement lorsqu'il est fourni. L'envoi Outlook par MIME est limité dans cette implémentation à un corps de requête de 4 Mo ; les messages plus volumineux demandent SMTP ou une pièce jointe plus petite.

Références officielles : [OAuth Google](https://developers.google.com/identity/protocols/oauth2/web-server), [Gmail send](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send), [OAuth Microsoft](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow), [Microsoft Graph sendMail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0).

## Webhooks sortants

Dans **API & intégrations → Webhooks sortants**, saisir le nom, une URL HTTPS et les événements souhaités. Le secret de signature est affiché à la création et après renouvellement, puis conservé chiffré. L'interface permet test sans données métier, pause/réactivation, renouvellement du secret, consultation des 100 dernières livraisons et relance des échecs définitifs.

Événements disponibles : `customer.created`, `customer.updated`, `document.created`, `document.updated`, `document.status_changed`, `stock.movement`, `crm.opportunity.created`, `crm.opportunity.updated`, `crm.activity.created`, `email.sent`. Le test utilise `webhook.test`. Les changements abonnés créent des livraisons durables dans la transaction métier ; un retour arrière annule aussi ces livraisons. Aucune donnée historique n'est envoyée rétroactivement lors de la création d'un webhook.

Le corps est un JSON contenant `id`, `type`, `created_at`, `company_uid`, `data`. En-têtes : `X-ZeFacture-Id`, `X-ZeFacture-Event`, `X-ZeFacture-Signature: t=HORODATAGE,v1=HMAC`. La signature est l'hexadécimal de HMAC-SHA256, clé = secret, message = octets UTF-8 de `HORODATAGE + "."` suivis du corps HTTP **brut**. Vérifier en temps constant, vérifier une fenêtre d'horodatage adaptée (par exemple cinq minutes), puis dédoublonner sur `id`. Le même identifiant est conservé lors des tentatives ; l'horodatage/signature change. Répondre 2xx uniquement après acceptation durable.

Le worker fait jusqu'à six tentatives avec délai croissant. Les redirections sont refusées. Le DNS est résolu et contrôlé à chaque livraison ; la connexion est liée à l'adresse validée et le certificat est vérifié pour le vrai nom du serveur. Les destinations privées/réservées sont bloquées par défaut, pour SMTP comme pour les webhooks. Un administrateur **du serveur** peut autoriser des hôtes privés précis avec `OUTBOUND_ALLOWED_HOSTS=smtp.interne,automatisation.interne` ; cette liste ne désactive pas TLS.

Le récepteur SUPERPDP existant et les clés API restent disponibles. Les clés restent propres à une entreprise et peuvent être révoquées dans l'interface.

Depuis la migration additive `0006_superpdp_sync`, l’intégration SUPERPDP inclut une file de dépôts, la synchronisation paginée des factures émises/reçues, les originaux PDF/XML et les événements officiels. Elle s’active par entreprise et utilise le worker ; les anciens accès et documents restent conservés sans activation automatique. Voir le [guide SUPERPDP](GUIDE_SUPERPDP.md), notamment pour les statuts distincts des règlements locaux et la reprise d’un résultat incertain.

## Mise à jour, sauvegarde et reprise

La migration `0003_crm_reports_integrations` crée uniquement huit nouvelles tables. Aucune donnée des tables historiques n'est recalculée ou remplacée. Faire une sauvegarde complète avant mise à jour, puis reconstruire l'image, appliquer les migrations et redémarrer web/worker.

Pour une bascule sans nouvelle écriture entre sauvegarde et migration, `tools/backup.py --leave-stopped` conserve web/worker arrêtés après réussite. Redémarrer ensuite avec `docker compose up -d --wait`. Si la sauvegarde échoue, les services sont redémarrés.

`EMAIL_DELIVERY_DISABLED=1` bloque **toutes** les livraisons email, y compris les connexions par entreprise. `WEBHOOK_DELIVERY_DISABLED=1` bloque les webhooks sortants. Le script de restauration active les deux pour éviter qu'une copie de reprise ne contacte vos clients ou vos outils. Réactiver seulement après vérification. Le mode global `MAIL_TRANSPORT=outbox` continue de bloquer les anciens transports serveur ; une connexion d'entreprise choisie explicitement reste utilisable, sauf avec le verrou global de reprise.

Les tests OAuth utilisent des réponses simulées ; les tests SMTP et webhook utilisent des serveurs TLS locaux avec identifiants et données fictifs. Aucun compte Gmail/Outlook réel ni destinataire client n'est utilisé pour les tests.


## Sécurité et personnalisation

Le [guide complet](GUIDE_AUTOHEBERGEMENT.md) décrit les applications Google et Microsoft depuis zéro et les retours exacts de zefacture.com. L’interface affiche les retours de l’instance courante et la présence de la paire Client ID/secret. Un remplacement d’identifiant OAuth exige son secret associé ; l’état est consommé atomiquement, même si l’échange échoue. Une autorisation révoquée demande une reconnexion, sans restitution de l’erreur sensible du fournisseur.

Les migrations `0004_oauth_branding_security` et `0005_email_envelopes` ajoutent six tables sans modifier les champs des tables existantes : personnalisation par entreprise, préférences utilisateur, second facteur TOTP, tentatives OAuth, journal et adresse de réponse figée par email. Les paramètres de personnalisation sont accessibles dans **Mon entreprise → Personnalisation** ; la double authentification dans **Mon compte → Sécurité**.

Un secret SMTP conservé ne peut être réutilisé sur un autre hôte, port, mode TLS ou identifiant sans ressaisie. En production, les administrateurs activent leur second facteur et vérifient récemment leur identité pour gérer les intégrations. Le worker reste arrêté après une restauration, pour conserver les files pendant la validation. Les restaurations activent aussi `EXTERNAL_SERVICES_DISABLED=1` : ce verrou bloque les autres services externes, en plus des verrous email/webhook décrits plus haut.
