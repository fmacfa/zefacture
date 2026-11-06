# ZeFacture — guide d’utilisation

Édition Django auto-hébergée, mise à jour le 6 novembre 2026. Ce manuel décrit les fonctions présentes. Le domaine `zefacture.com` est prévu ; sa mise en ligne et les connexions aux fournisseurs restent à effectuer.

Pour installer et administrer le serveur, lire le [guide d’auto-hébergement](GUIDE_AUTOHEBERGEMENT.md). Pour automatiser un échange, lire le [guide API et webhooks](GUIDE_API_WEBHOOKS.md).

## 1. Premiers pas

1. Ouvrir l’adresse fournie par l’exploitant et se connecter avec son propre compte, ou accepter l’invitation reçue. Il n’existe pas de mot de passe commun par défaut.
2. Vérifier l’entreprise affichée en haut de l’écran. Le sélecteur permet de passer entre les entreprises dont on est membre ; les données et réglages appartiennent à l’entreprise sélectionnée.
3. Dans **Mon compte → Sécurité**, activer la double authentification et conserver les codes de secours. Elle est obligatoire pour les administrateurs lorsque la politique de production est active.
4. Un administrateur complète **Mon entreprise**, puis **Personnalisation** : coordonnées, mentions, logo, couleurs, conditions de paiement et modèles d’emails.
5. Créer une fiche client, préparer un devis ou une facture, vérifier ses lignes et télécharger son PDF avant de l’envoyer.

En cas d’oubli du mot de passe, utiliser **Mot de passe oublié**. La réception du lien nécessite que l’exploitant ait activé la messagerie du serveur. Réinitialiser le mot de passe ne supprime pas le second facteur.

## 2. Entreprises, équipe et droits

**Mes entreprises**, accessible par le sélecteur supérieur, permet de créer ou sélectionner une entreprise. Un même compte peut travailler dans plusieurs entreprises avec des rôles différents.

| Rôle | Utilisation principale |
| --- | --- |
| Propriétaire | Administration de son entreprise, des membres et des intégrations ; son accès est protégé contre le retrait depuis l’équipe |
| Administrateur | Administration de l’entreprise, invitations, rôles, messagerie, API, webhooks et journal |
| Comptable | Gestion métier, finances, rapports et exports, sans administration de l’équipe ou des intégrations |
| Membre | Création et suivi métier, CRM, inventaire et ses frais ; pas d’accès aux écrans financiers et aux exports de rapports |
| Lecteur | Consultation des écrans autorisés, sans modification ; inventaire, imports et finances restent réservés à leurs rôles respectifs |

### Inviter plusieurs collaborateurs

1. Un propriétaire ou administrateur ouvre **Collaborateurs**.
2. Saisir l’adresse de la personne et choisir son rôle ; créer l’invitation.
3. L’invitation est placée dans la boîte d’envoi. Tant que la messagerie n’est pas activée, l’administrateur peut transmettre lui-même le lien affiché par un canal privé.
4. La personne utilise exactement l’adresse invitée, crée son compte si nécessaire et accepte l’invitation. Le lien expire après sept jours et ne crée pas plusieurs adhésions lors d’une réutilisation.
5. Depuis ce même écran, modifier le rôle, retirer un accès ou annuler une invitation en attente. L’interface protège le propriétaire et le propre accès de l’administrateur qui effectue l’action.

Les membres ne partagent pas de mot de passe. Aucun nombre maximal de membres n’est imposé par le code de cette édition ; la capacité dépend du serveur et de son utilisation. Retirer un accès conserve les documents et l’historique métier.

## 3. Clients professionnels, particuliers et CRM

Dans **Clients**, créer une fiche **professionnel** ou **particulier**. Renseigner le nom, les coordonnées, le pays et, pour les professionnels concernés, les identifiants d’entreprise et de TVA. L’annuaire et le contrôle VIES dépendent de leurs services externes ; la saisie manuelle reste disponible.

Dans la fiche, choisir la relation : **Prospect**, **Client**, **Fournisseur** ou **Client et fournisseur**. Les anciennes fiches sont conservées et considérées comme clients jusqu’à modification de cette relation.

La fiche rassemble :

- Contacts supplémentaires, fonction, email et téléphone.
- Responsable parmi les membres actifs, étiquettes et notes internes.
- Opportunités avec montant, devise, responsable et date de clôture prévue.
- Notes, appels, rendez-vous, tâches et emails préparés depuis le CRM.
- Documents et suivi des emails associés.

Une opportunité suit les étapes **À qualifier → Qualifiée → Proposition → Négociation → Gagnée / Perdue**. Passer à Gagnée transforme un prospect en client ; cela ne crée ni devis ni facture automatiquement. Pour poursuivre la vente, créer le document depuis les écrans correspondants.

L’écran **CRM** filtre les opportunités par étape, responsable et recherche. Il présente aussi les tâches à réaliser. Pour une relance, créer une tâche avec échéance et responsable, puis la marquer terminée après l’action. Les notes internes ne sont pas ajoutées automatiquement aux documents ou aux emails.

Pour écrire au client depuis sa fiche, choisir l’activité email et l’adresse principale ou un contact de la fiche. Vérifier le contenu avant validation : un message préparé peut être livré par le worker lorsque la messagerie est active. Cette fonction n’importe pas la boîte de réception Gmail ou Outlook.

## 4. Produits, services et stock

1. Ouvrir **Inventaire → Nouvel article** avec un rôle autorisé à écrire.
2. Renseigner désignation, référence, type produit/service, unité, prix, TVA et image éventuelle.
3. Pour suivre les quantités, activer le suivi de stock et définir le seuil d’alerte.
4. Dans la fiche, enregistrer une **entrée** ou une **sortie** avec une quantité positive, la date, le motif et une référence éventuelle. Le sens du mouvement détermine son effet ; ne pas saisir une quantité négative.
5. Consulter la quantité disponible et l’historique. Une sortie dépassant le stock disponible est refusée.

Le seuil déclenche un avertissement dans la fiche ; il ne crée pas automatiquement de commande d’achat ou d’email d’alerte. Une facture ne décrémente pas automatiquement le stock : enregistrer explicitement le mouvement correspondant. L’archivage d’un article conserve son historique.

Pour consulter ou exporter le stock d’une période, utiliser **Rapports → Stock en fin de période** ou **Mouvements de stock**, avec un rôle financier. Le stock passé est reconstruit à partir des mouvements enregistrés ; le prix du catalogue reste son prix actuel.

## 5. Devis, factures, avancements et avoirs

### Préparer et envoyer un devis

1. Ouvrir **Devis → Nouveau devis**, sélectionner un client et saisir objet, dates et devise.
2. Ajouter les lignes, quantités, prix HT, TVA et remises. Vérifier les totaux calculés.
3. Enregistrer le document et télécharger le PDF. Compléter un brouillon sans client avant de l’envoyer ou de l’accepter.
4. Utiliser l’action d’envoi après vérification du destinataire. L’email et le PDF sont préparés dans la file ; la livraison dépend de la messagerie et du worker.
5. Le lien transmis permet au destinataire de consulter et d’accepter le devis. Il est privé et temporaire ; dans cette version, sa durée est de trente jours après l’envoi, indépendamment de l’échéance affichée du devis.

Après acceptation, utiliser **Facturer** pour la facture de solde ou **Facture d’avancement** pour un pourcentage. Les avancements déjà facturés sont déduits du solde ; le cumul ne peut pas dépasser 100 %. Contrôler la nouvelle échéance avant l’émission.

### Créer et suivre une facture

Depuis **Factures → Nouvelle facture**, sélectionner le client, les dates, la devise et les lignes. Le téléchargement produit le PDF Factur-X avec son XML embarqué lorsque les données sont valides. La génération est locale ; les validations XSD et Schematron doivent aboutir. Une erreur doit être corrigée avant l’émission.

Le PDF téléchargeable et les fichiers XML/rapport de validation apparaissent dans la fiche. Générer ou télécharger un fichier ne signifie pas qu’il a été livré à son destinataire. Consulter séparément le statut du document et celui de l’email ou du dépôt PDP.

Les brouillons restent modifiables. Les documents émis conservent leurs montants, coordonnées et fichiers originaux. Une facture émise se corrige avec un **avoir**, créé depuis **Avoirs** en sélectionnant la facture d’origine, le même client et la même devise. Une facture émise n’est pas remplacée par une nouvelle version silencieuse.

L’action **Marquer payé** correspond à un règlement intégral. Pour un paiement partiel traçable, enregistrer la transaction bancaire et la rapprocher de la facture dans **Finances → Transactions**. Une facture échue avec reste dû peut faire l’objet d’une relance manuelle depuis sa fiche.

## 6. Achats, imports et notes de frais

**Factures fournisseurs** centralise les documents reçus : saisie du numéro fournisseur, dates, lignes et original joint, ou réception synchronisée depuis SUPERPDP. Les actions disponibles permettent acceptation, refus, litige et règlement selon le statut. Le téléchargement restitue l’original fourni ; il ne fabrique pas une facture à la place du fournisseur.

Avec une connexion SUPERPDP vérifiée, l’administrateur active la synchronisation depuis **Mon entreprise → Connexion SUPERPDP**. Les originaux PDF/XML et événements sont récupérés par le worker. Dans les fiches, **Suivi SUPERPDP** distingue le dépôt et les statuts distants des règlements locaux ; les décisions métier se transmettent par des boutons explicites. Le [guide SUPERPDP](GUIDE_SUPERPDP.md) explique les parcours et les reprises après résultat incertain.

Dans **Imports**, charger un PDF ou une image, lancer la transcription/OCR, puis relire et corriger le résultat. L’analyse IA est facultative ; lorsqu’elle est configurée et déclenchée, le texte est transmis au service choisi par l’exploitant. Contrôler les champs et les lignes avant de convertir en facture.

**Notes de frais** permet de déposer ses justificatifs et de suivre leur traitement. Les administrateurs et comptables peuvent traiter les frais de l’entreprise ; les autres utilisateurs conservent les restrictions de visibilité par auteur. Un fichier importé n’est pas automatiquement validé ou payé.

## 7. Finances et rapprochement

Les propriétaires, administrateurs et comptables disposent de **Finances** :

- **Comptes** : compte, devise et solde d’ouverture.
- **Transactions** : saisie, import CSV, contrôle des doublons et rapprochement avec un document de la même entreprise.
- **Trésorerie** : prévisions et échéances.
- **TVA** : état par période.
- **Export comptable** : journal de facturation exportable.

Après un import, lire le nombre de lignes importées, ignorées et rejetées. Vérifier le compte choisi et les devises avant rapprochement. Les devises ne sont pas converties automatiquement ni additionnées dans un solde unique.

L’état de TVA suit les dates d’émission ; il ne calcule pas une déclaration sur encaissements. Le journal exporté n’est pas un FEC. Faire valider l’utilisation de ces états pour les démarches comptables de l’entreprise.

## 8. Dashboard, périodes et comparatifs

**Vue d’ensemble** affiche les indicateurs de la période, les comparatifs, les sommes à encaisser/à payer, les devis en attente, les factures échues et les derniers documents. Les indicateurs bancaires sont réservés aux rôles financiers.

Dans le filtre :

1. Choisir les dates **Du / Au**, ou le mois, trimestre, l’année en cours ou toutes les dates.
2. Comparer à la période précédente de même durée, à la même période l’année précédente, à des dates personnalisées, ou désactiver la comparaison.
3. Cliquer sur **Appliquer**. Avec des dates de comparaison personnalisées, compléter les deux bornes.

**Rapports** ajoute l’exploration des jeux de données et un tableau d’évolution mensuelle. Les tableaux et cartes sont prédéfinis ; cette version ne possède pas de constructeur de widgets ou de dashboard libre.

Pour lire correctement les chiffres : ventes/achats suivent l’émission, avec déduction des avoirs et exclusion des brouillons, annulations, refus et simulations. Les flux bancaires suivent les transactions comptabilisées. Les restes dus affichés sont ceux d’aujourd’hui sur les documents sélectionnés, pas un solde historique reconstitué à la date de fin. Aucune variation en pourcentage n’est calculée sur une base nulle.

## 9. Télécharger en PDF, CSV ou Excel

1. Avec un rôle financier, ouvrir **Rapports**.
2. Définir la période et la comparaison.
3. Dans **Données**, choisir le tableau et cliquer sur **Afficher**.
4. Cliquer sur **PDF**, **Excel** ou **CSV**. Le fichier inclut toutes les lignes sélectionnées, au-delà de la page visible.

Jeux disponibles : synthèse et comparatifs, documents, lignes de facturation, clients/prospects, contacts, opportunités, activités, stock en fin de période, mouvements, transactions, TVA, notes de frais et prévisions.

| Format | Usage |
| --- | --- |
| PDF | Lecture et partage d’un rapport figé ; pour plus de 10 000 enregistrements, réduire la période ou utiliser un autre format |
| Excel `.xlsx` | Travail dans un tableur ; les identifiants conservent leurs zéros et les textes trop longs continuent dans l’onglet **Textes longs** |
| CSV | Import vers un autre outil ; les champs susceptibles d’être interprétés comme formules sont neutralisés |

Les dates filtrées varient selon le tableau : émission pour les documents, opération pour mouvements/transactions, création pour clients/contacts/activités, dépense pour les frais, échéance pour les prévisions. **Toutes les dates** permet de retrouver une fiche créée avant la période courante.

Les PDF de devis/factures se téléchargent aussi individuellement depuis leur fiche. Les exports métier ne remplacent pas la sauvegarde complète du serveur : ils ne permettent pas, seuls, de restaurer les comptes, secrets, originaux et historiques.

## 10. Messagerie et automatisations

Un administrateur ouvre **Emails → Configurer ma messagerie**. Chaque entreprise choisit une connexion active : configuration serveur, boîte locale, SMTP, Gmail/Google Workspace ou Outlook/Microsoft 365.

En SMTP, renseigner expéditeur, hôte, port, TLS, identifiant et secret. En Google/Microsoft, l’exploitant crée d’abord l’application OAuth en suivant le [guide d’auto-hébergement](GUIDE_AUTOHEBERGEMENT.md), puis chaque entreprise autorise son compte depuis l’interface.

**Vérifier la connexion** ne livre pas de message. **Envoyer un test à mon adresse** prépare un véritable email vers le compte connecté. Vérifier ensuite le résultat dans **Emails** et sa réception. Le statut `sent` signifie que le serveur ou fournisseur a accepté l’envoi, sans garantir sa réception ou sa lecture.

Après un changement de connexion, les anciens messages ne sont pas reroutés automatiquement. Vérifier destinataire et expéditeur avant leur relance explicite. Une erreur réseau peut survenir après acceptation distante : consulter la messagerie avant de réessayer pour éviter un doublon.

Les clés API se créent dans **API & intégrations** ; les webhooks sortants dans l’écran associé. Ils permettent d’automatiser certains échanges, selon le [contrat documenté](GUIDE_API_WEBHOOKS.md). L’API ne couvre pas encore toutes les fonctions de l’interface.

## 11. Personnalisation et utilisation sur téléphone

**Personnalisation** règle logo, nom d’affichage, couleur, présentation classique/compacte, devise et délais par défaut, adresse de réponse, signature et modèles d’emails de l’entreprise sélectionnée. Les mentions légales et bancaires restent dans **Mon entreprise**. Les variables autorisées des modèles figurent dans l’écran et le [guide d’administration](GUIDE_AUTOHEBERGEMENT.md#8-personnalisation-pour-les-clients).

Une nouvelle identité visuelle s’applique aux nouveaux documents et aux brouillons sauvegardés ; elle ne modifie pas les originaux déjà émis. Les entreprises d’une même instance conservent leurs réglages distincts.

**Mon compte → Préférences d’affichage** propose thème clair, sombre ou système et tableaux confortables/compacts. Sur téléphone, les blocs et formulaires se réorganisent ; faire défiler horizontalement les tableaux larges dans leur zone. Les mêmes comptes et droits s’appliquent sur ordinateur et mobile. L’application nécessite une connexion au serveur ; il n’existe pas de mode de travail hors ligne avec synchronisation.

## 12. Résoudre les difficultés courantes

| Situation | Action |
| --- | --- |
| Une rubrique est absente ou renvoie un refus | Vérifier l’entreprise sélectionnée et demander à un administrateur de contrôler son rôle |
| Un membre ne reçoit pas son invitation | Vérifier l’adresse et la boîte d’envoi ; un administrateur peut transmettre le lien privé encore valide |
| Un tableau paraît vide | Choisir **Toutes les dates**, contrôler le jeu de données et l’entreprise |
| Le stock ne change pas après facturation | Enregistrer le mouvement explicite dans l’inventaire |
| Une facture émise doit être corrigée | Préparer un avoir lié ; ne pas modifier ses originaux |
| Un original n’est plus téléchargeable | Demander à l’exploitant de restaurer l’original vérifié depuis une sauvegarde |
| Gmail/Outlook demande une reconnexion | Faire réautoriser la connexion par un administrateur ; les prérequis fournisseur sont détaillés dans le guide serveur |
| Un code TOTP est refusé | Vérifier l’heure du téléphone et utiliser le code suivant ou un code de secours inutilisé |
| Téléphone et codes de secours perdus | Contacter l’exploitant pour une vérification d’identité et la récupération administrative |

Pour une assistance, indiquer l’écran, la date, le message d’erreur et un exemple fictif. Ne pas transmettre de mot de passe, clé API, code de secours ou document client dans un ticket public.
