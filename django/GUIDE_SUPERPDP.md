# ZeFacture — émission et réception avec SUPERPDP

Édition Django, 6 novembre 2026. Contrat vérifié : **SUPERPDP API 1.39.0.beta**, publié le 3 novembre 2026 et téléchargé depuis [l’OpenAPI officielle](https://api.superpdp.tech/openapi/superpdp.json). La [documentation du fournisseur](https://www.superpdp.tech/openapi/) reste la référence pour l’accès aux comptes et les évolutions de l’API.

Les tests de ZeFacture utilisent un fournisseur HTTP local et des données fictives. Ils ne constituent pas une émission réelle, une réception réelle entre plateformes ou une validation des autorisations d’une entreprise par SUPERPDP. Effectuer la recette du §10 avant l’ouverture de l’instance.

## 1. Parcours disponibles

| Fonction | Comportement |
| --- | --- |
| Connexion | Autorisation OAuth de l’entreprise ou identifiants techniques autorisés |
| Émission | Factur-X original placé dans une file durable puis déposé par le worker |
| Réception | Récupération des factures entrantes et de leurs données structurées |
| Factures émises ailleurs | Récupération des factures sortantes du même compte SUPERPDP |
| Originaux | Conservation du PDF Factur-X ou XML original, avec empreinte SHA-256 |
| Lecture d’un XML | Récupération d’une représentation PDF séparée lorsque SUPERPDP peut la fournir |
| Suivi | Synchronisation paginée des événements et affichage dans chaque fiche |
| Déclarations métier | Approbation, litige, refus, paiement/encaissement intégral, sur action explicite |
| Reprise | Curseurs durables, dédoublonnage et absence de réémission automatique après résultat incertain |

La synchronisation utilise l’API officielle. Elle fonctionne sans abonnement webhook. Les autres fonctions du fournisseur — mandats, administration de l’annuaire, déclarations B2C/B2B internationales séparées et ensemble des statuts avancés — ne deviennent pas automatiquement des fonctions de cette interface.

## 2. Préparer l’entreprise et l’application

Disposer d’un compte SUPERPDP autorisé pour l’entreprise et d’une application OAuth confidentielle. L’inscription, les consentements, les vérifications d’identité et le droit de gérer l’entreprise se font auprès de SUPERPDP ; une clé API ne remplace pas ces vérifications.

Pour une instance de production, l’exploitant configure sa paire privée dans `.env` :

```dotenv
SUPERPDP_OAUTH_CLIENT_ID=identifiant_de_l_application
SUPERPDP_OAUTH_CLIENT_SECRET=secret_prive_de_l_application
SUPERPDP_OAUTH_REDIRECT_URI=https://zefacture.com/app/entreprise/pdp/callback
SUPERPDP_SYNC_INTERVAL=300
```

Déclarer exactement cette URL de retour dans l’application SUPERPDP. Pour une autre instance, utiliser son domaine et l’adresse affichée dans ZeFacture. Ne pas ajouter de slash final. Recréer web et worker après modification de l’environnement, avec les bons paramètres de projet et de fichier privé :

```sh
docker compose up -d --force-recreate web worker
```

Pour une instance restaurée, utiliser la fonction Compose et le fichier de configuration indiqués dans le [guide d’auto-hébergement](GUIDE_AUTOHEBERGEMENT.md#43-passer-la-copie-validée-sur-le-domaine). Ne pas remplacer les clés ou volumes restaurés.

## 3. Connecter depuis l’interface

1. Sélectionner la bonne entreprise et vérifier son SIREN dans **Mon entreprise**.
2. Un propriétaire ou administrateur ouvre **Mon entreprise → Connexion SUPERPDP**.
3. Choisir **Se connecter à SUPERPDP**, puis lire et accorder personnellement le consentement chez le fournisseur.
4. De retour dans ZeFacture, utiliser **Vérifier la connexion SUPERPDP** et contrôler l’environnement et l’état de vérification de l’entreprise.
5. Une fois l’entreprise vérifiée, ZeFacture demande une première récupération de son historique. Ouvrir **Achats et finances → Factures fournisseurs** pour suivre son avancement.

L’état OAuth est lié à l’utilisateur, l’entreprise, la version de configuration et la paire d’identifiants serveur. Il expire après dix minutes et est consommé en base avant l’échange, même si cet échange échoue. Les jetons sont chiffrés avec `app.key` et les renouvellements sont sérialisés pour préserver leur rotation.

Lorsque l’application OAuth de l’instance est absente, utiliser **Identifiants techniques** avec l’identifiant et le secret client d’un compte autorisé, ou son jeton d’accès. **Enregistrer et vérifier** vérifie les nouveaux identifiants et l’entreprise avant d’afficher le résultat. Modifier le Client ID exige le secret associé. **Connexion vérifiée** et **Entreprise vérifiée** indiquent que le profil correspond. Si SUPERPDP examine encore le dossier, suivre son état chez le fournisseur. La synchronisation automatique s’active séparément.

Le compte distant doit correspondre au SIREN local lorsqu’il utilise le schéma `fr_siren`. Une première synchronisation lie les données à l’identifiant d’entreprise et à l’environnement du fournisseur. Une autre entreprise ou le passage de sandbox à production demande une entreprise ZeFacture distincte ; les historiques ne sont pas fusionnés silencieusement.

**Déconnecter SUPERPDP** retire les secrets locaux de connexion et désactive la synchronisation automatique. Les factures, originaux et historiques sont conservés. Révoquer aussi l’accès chez le fournisseur lorsque c’est nécessaire ; une requête déjà acceptée à distance ne peut pas être annulée par la déconnexion.

## 4. Émettre une facture ou un avoir

1. Créer et vérifier le brouillon : client, coordonnées, dates, devise, lignes, TVA et références.
2. Générer ou télécharger Factur-X et corriger toute erreur de validation locale.
3. Utiliser l’émission **SUPERPDP** dans la fiche du document. La règle B2B/B2C/B2B internationale suit le destinataire, avec le choix disponible dans le parcours existant.
4. ZeFacture fige le document et son original, puis prépare une transmission durable. Le statut métier local d’émission ne prouve pas la livraison distante.
5. Le worker dépose les octets de cet original avec l’identifiant ZeFacture comme `external_id`. Suivre le résultat dans **Suivi SUPERPDP** ou dans les **Transmissions récentes** de la connexion.
6. Après acceptation du dépôt, la synchronisation récupère la référence distante, son original et ses événements.

Le dépôt utilise `POST /v1.beta/invoices`, un corps `application/pdf` et les paramètres `external_id` et `processing_rule`. Aucun `disable_pre_check` n’est ajouté. Une réponse positive signifie que SUPERPDP a accepté la requête dans son traitement asynchrone ; un rejet ultérieur reste possible et doit être lu dans les événements.

Un document déjà émis n’est pas régénéré pour changer son contenu ou son identité visuelle. Une correction suit le parcours d’avoir et de nouvelle émission approprié. Une nouvelle tentative d’envoi ne réinitialise pas un paiement local effectué pendant l’attente.

## 5. Recevoir les factures et leurs originaux

Ouvrir **Achats et finances → Factures fournisseurs**. Le panneau **Factures reçues via SUPERPDP** indique combien de documents de l’entreprise sont enregistrés et la date de la dernière récupération réussie. Un membre disposant du droit d’écriture peut cliquer sur **Synchroniser SUPERPDP**. La liste s’actualise à la fin du traitement et permet aussi une actualisation manuelle. Une nouvelle récupération retrouve les documents déjà enregistrés.

Dans les actions de chaque facture synchronisée, **Original PDF** ou **Original XML** télécharge le fichier conservé. Pour un XML, **PDF de lecture** télécharge aussi la représentation lisible lorsque le fournisseur l’a fournie. Les noms de téléchargement reprennent le numéro de facture. Choisir **Voir la facture** pour consulter la fiche et le suivi des statuts.

Dans **Connexion SUPERPDP → Factures et statuts** :

- **Synchroniser maintenant** demande une récupération par le worker.
- **Activer la synchronisation automatique** contrôle les nouveautés périodiquement, toutes les 300 secondes par défaut. L’intervalle serveur est limité entre 60 secondes et un jour.
- **Reparcourir tout l’historique** remet les curseurs au début, en conservant les documents et en dédoublonnant ceux déjà reçus.

La synchronisation traite les factures entrantes **et** sortantes. Les achats apparaissent dans **Factures fournisseurs**, y compris les avoirs reçus ; les ventes dans **Factures** ou **Avoirs**. Les correspondants sont retrouvés par identifiants ou créés dans l’entreprise sélectionnée, sans écraser les coordonnées des fiches déjà présentes.

Chaque facture conserve ses données structurées originales. Les totaux proviennent du fournisseur ; ils ne sont pas recalculés depuis les prix unitaires. Les ventilations TVA et les ajustements de remises/charges au niveau du document alimentent la projection locale des lignes. Les valeurs structurées complètes et le fichier original restent conservés, même lorsque le prix unitaire projeté est limité aux huit décimales du stockage local.

Une facture existante peut être rapprochée par son identifiant PDP. Un document importé manuellement n’est rapproché automatiquement qu’avec le même original, la même empreinte et des métadonnées cohérentes. Un numéro fournisseur seul ne suffit pas. Si deux fournisseurs utilisent le même numéro, une référence locale distincte est créée ; le numéro original reste visible dans **Suivi SUPERPDP** et dans l’original. Les compteurs locaux de numérotation ne sont pas consommés par l’import.

Le **Contact CRM** de la facture est rattaché automatiquement au fournisseur pour un achat et au client pour une vente. ZeFacture exploite le nom et l’email du contact structuré transmis par SUPERPDP. Une correspondance ambiguë peut être tranchée depuis la fiche de la facture. Une nouvelle synchronisation conserve les choix manuels.

### PDF et XML

**Aperçu** ouvre le PDF directement dans ZeFacture. Si l’original est un XML, le lecteur utilise le PDF de lecture récupéré auprès de SUPERPDP lorsqu’il est disponible. Les flèches changent de page et les contrôles de zoom agrandissent le document. Le HT, la TVA et le TTC de la facture figurent au-dessus du lecteur.

**Télécharger l’original SUPERPDP** restitue exactement les octets récupérés. Pour un original XML CII/UBL, le lien principal restitue un XML ; **Télécharger la version PDF** apparaît lorsqu’une conversion de lecture distincte a été récupérée. Une conversion ne remplace jamais l’original.

Les originaux sont limités à 25 Mio et les réponses JSON à 2 Mo. Les types de facture couverts incluent les factures commerciales, factures rectificatives/d’acompte/autofacturées, factures de construction prises en charge et les avoirs 381/261/396. Un type inconnu, un montant hors précision ou une incohérence est signalé et laisse le curseur avant l’élément, sans création partielle ni suppression de données. L’original demeure récupérable chez le fournisseur pour traitement contrôlé.

Un fichier local absent ou dont l’empreinte a changé bloque son téléchargement ; il n’est pas remplacé silencieusement. Restaurer l’original vérifié depuis une sauvegarde. Les références et pièces justificatives externes présentes dans les factures ne provoquent aucun téléchargement arbitraire vers leurs URL.

### Télécharger un ZIP par période

Pour vérifier les montants avant l’export, appliquer la période, le statut ou la recherche avec **Filtrer**. **Totaux de la sélection** présente le HT, la TVA, le TTC et le reste dû actuel de tous les résultats, groupés par devise. Les avoirs sont déduits des trois premiers montants. Les statuts retenus sont ceux du filtre, y compris les brouillons ou annulations si **Tous les statuts** est choisi.

Dans **Factures fournisseurs → Rechercher des factures**, choisir les deux bornes de la période, le format souhaité et **Télécharger le ZIP**. **Date de facture** utilise la date inscrite sur le document. **Réception dans ZeFacture** utilise le jour où le document est entré dans l’application, y compris pour un historique importé en une seule fois. Les deux jours sont inclus. Les droits d’accès, la recherche et le statut sont les mêmes que pour la liste.

Le dossier **PDF** contient le PDF original ou le PDF de lecture conservé. Le dossier **XML** contient l’original XML ou le XML de facturation embarqué dans un PDF Factur-X. Le téléchargement fonctionne avec les fichiers déjà enregistrés, même après la déconnexion de SUPERPDP. Lorsqu’un format manque, l’archive fournit les fichiers disponibles et un relevé `fichiers-indisponibles.txt`. Si aucun fichier ne correspond, un message s’affiche dans le formulaire.

## 6. Comprendre les statuts

Le fournisseur publie un historique d’événements, pas un état unique remplaçant toutes les informations de la facture. ZeFacture conserve chaque identifiant d’événement, code, date, texte et détails. Les codes inconnus sont aussi conservés. La fiche affiche les codes rencontrés et le dernier événement selon l’identifiant distant ; un événement plus ancien reçu ensuite ne fait pas reculer cet affichage.

| Exemples de codes | Sens de suivi |
| --- | --- |
| `api:uploaded`, `fr:200` | Dépôt |
| `api:validated` | Validation sémantique du fournisseur |
| `api:sent`, `fr:201` | Transmission / émission |
| `api:received`, `fr:202`, `fr:203` | Réception / mise à disposition |
| `fr:205`, `fr:207`, `fr:210` | Approbation / litige / refus |
| `fr:211`, `fr:212` | Paiement transmis / encaissement déclaré |
| `api:invalid`, `api:rejected`, `fr:213` | Échec ou rejet à examiner |

Les événements ne réécrivent ni les montants, ni les instantanés, ni le reste dû local. Ils ne créent pas de transaction bancaire. Continuer à enregistrer ou rapprocher les règlements dans les finances ; une notification distante seule ne prouve pas l’encaissement bancaire local.

## 7. Transmettre une décision métier

Sur une facture liée et synchronisée, effectuer d’abord l’action locale puis utiliser le bouton explicite de **Suivi SUPERPDP** :

- Facture fournisseur acceptée : transmettre l’approbation `fr:205`.
- Facture fournisseur en litige ou refusée : transmettre `fr:207` ou `fr:210`, avec un **code motif du référentiel accepté par le fournisseur** et une note éventuelle. ZeFacture ne choisit pas un motif à la place de l’utilisateur.
- Paiement fournisseur intégral vérifié : un rôle financier peut déclarer `fr:211`.
- Encaissement intégral d’une vente vérifié : un rôle financier peut déclarer `fr:212`.

Ces déclarations passent par la même file durable. Un double clic ne crée pas plusieurs déclarations pour la même action locale. Les éventuels refus de code motif ou de déclaration doivent être examinés dans les transmissions. Les détails utilisent le contrat actuel `details`, sans inventer une ventilation fiscale.

La déclaration détaillée d’encaissements partiels, les autres codes avancés et l’automatisation complète de l’e-reporting ne sont pas implémentés par ces boutons. Les paiements partiels locaux restent possibles sans déclencher automatiquement une déclaration incorrecte au fournisseur.

## 8. Reprises après erreur

Les curseurs sont enregistrés après chaque facture et chaque événement durablement traités. Si un téléchargement échoue, les éléments précédents restent présents ; reprendre la synchronisation au même endroit. Une location de traitement en base empêche deux synchronisations simultanées de la même entreprise ; elle expire après interruption du worker. Une erreur d’une entreprise ne désactive pas les autres.

| État de transmission | Action |
| --- | --- |
| En attente | Vérifier que le worker est actif |
| Transmission en cours | Attendre le résultat ; un traitement interrompu passe en résultat incertain |
| Dépôt confirmé | Suivre les événements ultérieurs |
| Requête explicitement refusée | Corriger la configuration ou la cause, puis **Reprendre la requête refusée** |
| Résultat incertain | Synchroniser et vérifier directement chez SUPERPDP avant toute reprise |

Après une coupure réseau ou un HTTP 5xx, le fournisseur peut avoir accepté le dépôt. ZeFacture ne rejoue pas automatiquement le POST. La synchronisation recherche la facture par sa référence externe dans les données du compte, contrôle l’identité, les totaux et l’empreinte de l’original, puis confirme le lien lorsqu’il est cohérent.

Si aucun dépôt n’existe, un administrateur peut **Réautoriser après vérification**, avec confirmation explicite de l’absence chez SUPERPDP. Une synchronisation complète réussie postérieure à la tentative est obligatoire ; un identifiant distant déjà connu bloque cette reprise. Cette décision reste nécessaire pour les déclarations métier incertaines, dont l’API ne fournit pas de clé d’idempotence générale.

Ne pas contourner ce suivi en recréant une facture ou en modifiant directement les tables. Un dépôt refusé nécessitant une modification du contenu peut demander un avoir ou une nouvelle émission contrôlée selon sa situation ; l’original déjà figé reste conservé.

### Commande d’exploitation

Pour synchroniser immédiatement une entreprise sans créer de nouvelle facture à distance :

```sh
docker compose exec web python manage.py sync_superpdp --company IDENTIFIANT_ENTREPRISE_ZEFACTURE
```

Pour toutes les entreprises ayant activé la synchronisation automatique :

```sh
docker compose exec web python manage.py sync_superpdp --all
```

La commande retourne une erreur si une synchronisation échoue. Elle ne vide pas la file des émissions ; le worker traite celle-ci séparément. Les identifiants d’entreprise sont ceux de ZeFacture, pas les identifiants numériques SUPERPDP.

## 9. Sécurité, sauvegarde et mise à jour

La connexion et la politique de synchronisation sont réservées aux propriétaires/administrateurs avec les contrôles MFA et de vérification récente de production. Les déclarations de règlement exigent un rôle financier. Les fichiers utilisent les contrôles d’entreprise et de visibilité habituels ; aucune URL publique de document n’est créée.

Les requêtes sont limitées au serveur officiel HTTPS `api.superpdp.tech`, avec certificat vérifié, résolution DNS contrôlée, adresse publique validée et sans redirection. L’exception HTTP localhost n’existe que pour les tests. Aucun secret ou corps d’erreur sensible du fournisseur n’est exposé dans le diagnostic.

Chaque lot utilise le jeton exact dont l’entreprise vient d’être vérifiée. Une modification concurrente des identifiants ne peut pas basculer son dépôt ou ses téléchargements vers un autre compte. Un jeton expirant pendant un long lot interrompt celui-ci et laisse les curseurs reprendre après une nouvelle vérification.

Le récepteur historique `/webhooks/superpdp` reste facultatif pour un relais autorisé par l’exploitant. Son secret est reçu uniquement dans `X-ZeFacture-Webhook-Secret`. Pour enregistrer une notification, transmettre l’entreprise distante (`company_id`), l’environnement (`environment` valant `sandbox` ou `production`) et la facture (`invoice_id`). ZeFacture recoupe la liaison enregistrée. Si elle est incomplète, la récupération officielle peut être demandée sans rattacher le contenu à une facture. Les anciennes factures sans liaison officielle peuvent encore être reconnues par la paire exacte identifiant local et identifiant fournisseur. Une notification peut demander une synchronisation ; elle ne sert pas de preuve pour réécrire les montants ou le suivi officiel. Il n’est pas présenté comme un contrat d’abonnement webhook fourni et configuré automatiquement chez SUPERPDP.

La migration `0006_superpdp_sync` crée quatre tables supplémentaires. Elle ne modifie ni les colonnes ni les données des anciennes tables et n’active aucun échange externe. Sauvegarder avant mise à jour, reconstruire les images, appliquer les migrations et vérifier les empreintes selon le [guide d’exploitation](GUIDE_AUTOHEBERGEMENT.md).

Les sauvegardes contiennent originaux, clé, liaisons, curseurs, événements et transmissions. Une restauration garde le worker arrêté et active `EXTERNAL_SERVICES_DISABLED=1` : synchronisation et émissions sont bloquées. Contrôler les transmissions en attente/incertaines avant de lever ce verrou et reprendre le worker. Les verrous propres aux emails et webhooks sortants ne remplacent pas le verrou général des services externes pour SUPERPDP.

## 10. Recette fournisseur avant production

Utiliser une entreprise ZeFacture de test séparée et le **véritable environnement sandbox SUPERPDP**. La case **Simulation locale sans envoi** ne remplace pas ce sandbox.

1. Autoriser l’entreprise sandbox et vérifier son profil, son environnement et son droit d’accès.
2. Émettre une facture Factur-X fictive ; relever la référence distante et attendre les événements réels.
3. Faire parvenir une facture de test au compte sandbox ; synchroniser et comparer les champs, montants et octets de l’original avec ceux du fournisseur.
4. Vérifier les originaux PDF et XML, puis la version PDF de lecture lorsqu’elle existe.
5. Tester une reprise et un reparcours complet : aucun doublon ni changement d’original ne doit apparaître.
6. Utiliser les déclarations de test proposées dans la fiche sandbox ; les documents restent en **Simulation** et exclus des indicateurs réels.
7. Vérifier les droits de deux membres et l’isolation d’une autre entreprise.
8. Tester une sauvegarde/restauration hors de l’instance principale, avec les sorties externes bloquées.
9. Connecter ensuite l’entreprise de production distincte et effectuer les contrôles autorisés avant son premier véritable dépôt.

Les tests automatisés du projet couvrent le contrat HTTP, les grands identifiants, la pagination, les originaux, les montants, les limites d’entreprise, les reprises et les pertes de réponse après acceptation. Ils ne remplacent pas ces contrôles sur le compte du fournisseur.
