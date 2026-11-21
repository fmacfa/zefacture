# ZeFacture — guide d’utilisation

Édition Django auto-hébergée, mise à jour le 6 novembre 2026. Ce manuel décrit les fonctions présentes. Le domaine `zefacture.com` est prévu ; sa mise en ligne et les connexions aux fournisseurs restent à effectuer.

Pour installer et administrer le serveur, lire le [guide d’auto-hébergement](GUIDE_AUTOHEBERGEMENT.md). Pour automatiser un échange, lire le [guide API et webhooks](GUIDE_API_WEBHOOKS.md).

## 1. Premiers pas

Sur une nouvelle installation, ouvrez **Créer le premier compte** pour renseigner votre entreprise, votre nom, votre adresse e-mail et votre mot de passe. Ouvrez le lien privé transmis par l’exploitant dans le même navigateur, puis cliquez sur **Créer mon espace** dans le formulaire déjà rempli. Le lien peut être ouvert dans un autre onglet. Après création, cette adresse sert à vous connecter. La génération du lien est expliquée dans le [guide d’auto-hébergement](GUIDE_AUTOHEBERGEMENT.md#3-nouvelle-installation-sans-données-antérieures).

1. Ouvrir l’adresse fournie par l’exploitant et se connecter avec son propre compte, ou accepter l’invitation reçue. Il n’existe pas de mot de passe commun par défaut.
2. Vérifier l’entreprise affichée en haut de l’écran. Le sélecteur permet de passer entre les entreprises dont on est membre ; les données et réglages appartiennent à l’entreprise sélectionnée.
3. La double authentification s’active depuis **Mon compte → Sécurité**. Elle est facultative par défaut. L’exploitant peut l’exiger des administrateurs. Si vous l’activez, conservez vos codes de secours.
4. Un administrateur complète les rubriques de **Mon entreprise**, puis choisit le logo, les couleurs et les modèles d’emails dans **Personnalisation**.
5. Créer une fiche client, préparer un devis ou une facture, vérifier ses lignes et télécharger son PDF avant de l’envoyer.

En cas d’oubli du mot de passe, utiliser **Mot de passe oublié**. La réception du lien nécessite que l’exploitant ait activé la messagerie du serveur. Réinitialiser le mot de passe ne supprime pas le second facteur.

### Se déplacer dans l’application

La barre latérale regroupe le tableau de bord et les rapports sous **Pilotage**, les documents clients et les contacts sous **Ventes et CRM**, et les factures fournisseurs, les frais et les imports sous **Achats**. **Banque et finances** donne un accès direct aux comptes et aux transactions. Ouvrir **Réglages de l’entreprise** pour la personnalisation ou les intégrations. Sur téléphone, **Menu** ouvre la navigation.

### Choisir le format des nombres

Dans **Réglages de l’entreprise → Format des nombres**, un administrateur choisit le séparateur des milliers (aucun, espace, point ou virgule) et le séparateur décimal (virgule ou point). L’aperçu montre par exemple `1.234.567,89`, `1,234,567.89` ou `1234567,89`. Cliquer sur **Enregistrer pour l’entreprise** pour appliquer ce choix à toute l’équipe. Les rubriques Identité, Coordonnées, Immatriculation et TVA, Paiement et Documents restent accessibles dans le même menu. Les montants, quantités, taux et champs de saisie s’alignent à droite. Tant que l’entreprise utilise les préférences personnelles, chaque membre peut choisir ses séparateurs dans **Mon compte → Affichage**.

Les rapports PDF et les CSV utilisent ce format. Avec une préférence personnalisée, le classeur Excel ouvre la feuille **Affichage** avec les séparateurs choisis. La feuille **Données** conserve les cellules numériques pour les calculs et Excel y utilise les paramètres régionaux du poste. L’adresse postale, les identifiants et les numéros de facture conservent leur écriture. Les nouveaux documents enregistrent le format de leur auteur pour le PDF. Un PDF déjà émis garde son contenu d’origine.

### Modifier son mot de passe

Dans **Mon compte**, saisir le mot de passe actuel puis le nouveau. L’indicateur en estime la robustesse pendant la saisie sur l’appareil. Le serveur applique un minimum de dix caractères et contrôle sa liste de mots de passe courants à l’enregistrement. Pour la connexion, la fenêtre glissante de quinze minutes autorise dix tentatives par IP et vingt par adresse email, toutes IP confondues. La tentative suivante reçoit le délai restant. Une connexion redevient possible quand les tentatives précédentes sortent de la fenêtre.

## 2. Entreprises, équipe et droits

**Mes entreprises**, accessible par le sélecteur supérieur, permet de créer ou sélectionner une entreprise. Un même compte peut travailler dans plusieurs entreprises avec des rôles différents.

Dans **Mon entreprise**, choisissez **Identité**, **Coordonnées**, **Immatriculation et TVA**, **Paiement** ou **Documents**. Cliquez sur **Enregistrer cette rubrique** avant de passer à la suivante. Si la page demande de confirmer votre mot de passe, ouvrez le lien proposé dans un nouvel onglet. Après confirmation, revenez au formulaire rempli et enregistrez la rubrique.

| Rôle | Utilisation principale |
| --- | --- |
| Propriétaire | Administration de son entreprise, des membres et des intégrations ; son accès est protégé contre le retrait depuis l’équipe |
| Administrateur | Administration de l’entreprise, invitations, rôles, messagerie, API, webhooks et journal |
| Comptable | Gestion métier, finances, rapports et exports, sans administration de l’équipe ou des intégrations |
| Membre | Création et suivi métier, CRM, inventaire et ses frais ; pas d’accès aux écrans financiers et aux exports de rapports |
| Lecteur | Consultation des écrans autorisés, sans modification ; inventaire, imports et finances restent réservés à leurs rôles respectifs |

Pour une entreprise française, **Forme juridique** reprend les formes courantes (EI, EURL, SARL, SASU, SAS, etc.). Choisissez celle de votre justificatif d’immatriculation, ou **Autre (préciser)**. Dans **Immatriculation et TVA**, saisissez le SIREN une seule fois. Si vous êtes immatriculé au RCS, indiquez la ville du greffe pour composer la mention RCS. [Identifiants des entreprises](https://entreprendre.service-public.gouv.fr/vosdroits/F31190).

Choisissez le **Régime TVA** indiqué dans **Déclarer la TVA**, dans l’espace professionnel impots.gouv.fr. Le menu propose la franchise en base, le réel normal et le réel simplifié français jusqu’à fin 2026. Une valeur antérieure reste consultable. En cas de doute, laissez **Non renseigné**. La TVA reste calculée d’après les lignes du document. [Où retrouver votre régime](https://www.impots.gouv.fr/facturation-electronique-qu-est-ce-que-ca-change-pour-moi) · [Fin du régime simplifié en 2027](https://www.impots.gouv.fr/professionnel/questions/quelles-differences-y-t-il-entre-le-regime-simplifie-dimposition-et-le-reel).

### Inviter plusieurs collaborateurs

1. Un propriétaire ou administrateur ouvre **Collaborateurs**.
2. Saisir l’adresse de la personne et choisir son rôle ; créer l’invitation.
3. L’invitation est placée dans la boîte d’envoi. Tant que la messagerie n’est pas activée, l’administrateur peut transmettre lui-même le lien affiché par un canal privé.
4. La personne utilise exactement l’adresse invitée, crée son compte si nécessaire et accepte l’invitation. Le lien expire après sept jours et ne crée pas plusieurs adhésions lors d’une réutilisation.
5. Depuis ce même écran, modifier le rôle, retirer un accès ou annuler une invitation en attente. L’interface protège le propriétaire et le propre accès de l’administrateur qui effectue l’action.

Les membres ne partagent pas de mot de passe. Aucun nombre maximal de membres n’est imposé par le code de cette édition ; la capacité dépend du serveur et de son utilisation. Retirer un accès conserve les documents et l’historique métier.

## 3. Clients professionnels, particuliers et CRM

Dans **Clients → Nouveau client**, choisir **Professionnel** ou **Particulier**, puis compléter le nom et les coordonnées. Pour les professionnels, ouvrir **Immatriculation et facturation électronique** pour renseigner les identifiants lorsque nécessaire. Terminer avec **Créer le client**. Dans **Pays**, tapez le nom du pays ou son code ISO, par exemple **Allemagne (DE)**. Les identifiants SIREN et SIRET concernent la France. Pour un client allemand, renseignez le pays et son numéro de TVA avec le préfixe DE. **Vérifier la TVA avec VIES** affiche le résultat et les coordonnées communiquées par le service. Une validation peut être renvoyée sans nom ni adresse. Le pays d’établissement se choisit aussi à la création de votre propre entreprise.

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

Le seuil déclenche un avertissement dans la fiche ; il ne crée pas automatiquement de commande d’achat ou d’email d’alerte. La réception et la sortie de marchandises se confirment dans l’inventaire. L’archivage d’un article conserve son historique.

### Récupérer les achats et suivre les quantités consommées

Dans **Achats → Inventaire**, ouvrir **Récupérer les achats et les ventes**, choisir **Achats fournisseurs**, puis la facture. La page est aussi accessible depuis la fiche fournisseur via **Récupérer les achats dans l’inventaire**. Les factures reçues, acceptées ou payées, y compris celles importées par SUPERPDP, sont disponibles avec leurs lignes détaillées. Choisir les articles reçus, vérifier les quantités et la date de réception, puis cliquer sur **Enregistrer les entrées**.

Une référence unique dans la même unité propose l’article. En l’absence de référence, une désignation identique et unique peut suffire. Pour un nouveau produit, choisir **Créer un produit depuis cette ligne** puis compléter son prix de vente dans le catalogue. Laisser les prestations sur **Ignorer cette ligne**. Si la réception a déjà été saisie manuellement, rattacher un mouvement existant de même quantité, unité et sens préserve le solde. Chaque ligne est enregistrée une seule fois.

Sur la fiche d’un article, **Acheté sur facture**, **Sorti du stock** et **Disponible** présentent les quantités dans son unité. Pour du matériel utilisé en interne, cliquer sur **Enregistrer une consommation**, saisir la quantité utilisée, la date et le motif. Pour des produits vendus, choisir **Ventes aux clients** depuis l’inventaire ou **Enregistrer les sorties de stock** sur la facture émise. Les devis, acomptes et brouillons ne constituent pas des mouvements de marchandises.

Pour consulter ou exporter le stock d’une période, utiliser **Rapports → Stock en fin de période** ou **Mouvements de stock**, avec un rôle financier. Le stock passé est reconstruit à partir des mouvements enregistrés ; le prix du catalogue reste son prix actuel.

## 5. Devis, factures, avancements et avoirs

Un nouveau brouillon porte une référence `BROUILLON-…`. L’enregistrement, l’aperçu et l’annulation laissent le compteur disponible pour le prochain document émis. Le numéro définitif est attribué à l’envoi, à l’émission par API ou à l’acceptation d’un devis. L’année correspond à la date d’émission saisie. Vérifier cette date si un document plus récent a déjà été émis dans la série. Les numéros déjà attribués avant la mise à jour et les numéros des factures importées sont conservés.

### Préparer et envoyer un devis

1. Ouvrir **Devis → Nouveau devis**, sélectionner un client et saisir objet, dates et devise.
2. Ajouter les lignes, choisir le prix unitaire HT ou TTC, la quantité, la TVA et les remises. Vérifier le total et sa ventilation par taux.
3. Enregistrer le document et télécharger le PDF. Compléter un brouillon sans client avant de l’envoyer ou de l’accepter.
4. Utiliser l’action d’envoi après vérification du destinataire. L’email et le PDF sont préparés dans la file ; la livraison dépend de la messagerie et du worker.
5. Le lien transmis permet au destinataire de consulter et d’accepter le devis. Il est privé et temporaire ; dans cette version, sa durée est de trente jours après l’envoi, indépendamment de l’échéance affichée du devis.

Après acceptation, utiliser **Facturer** pour la facture de solde ou **Facture d’avancement** pour un pourcentage. Les avancements déjà facturés sont déduits du solde ; le cumul ne peut pas dépasser 100 %. Contrôler la nouvelle échéance avant l’émission.

#### Saisir un prix TTC et une remise globale

Dans **Prestations et produits**, chaque ligne a son propre sélecteur **HT / TTC**. Pour un tarif annoncé au client toutes taxes comprises, choisissez **TTC**, renseignez le prix unitaire et le taux applicable. Vous pouvez mélanger lignes HT et TTC dans un même document. L’éditeur affiche le total HT de chaque ligne après sa remise individuelle.

Sous les lignes, **Remise globale** accepte un pourcentage du sous-total HT après les remises de ligne, ou un montant fixe HT dans la devise du document. La remise fixe se saisit au centime. Le récapitulatif montre son montant, la base et la TVA de chaque taux, puis le TTC à payer. Un éventuel ajustement d’arrondi apparaît aussi sur la facture.

Prenons deux articles à 120 € TTC (TVA 20 %) avec 10 % de remise de ligne, et une prestation à 110 € TTC (TVA 10 %). Le sous-total HT est de 280 €. Une remise globale de 10 % enlève 18 € de la base à 20 % et 10 € de la base à 10 %. La facture indique **252 € HT, 41,40 € de TVA et 293,40 € TTC**.

#### Ajouter un article depuis le devis

Sur la ligne renseignée, cliquez sur **Ajouter au catalogue**. Vérifiez le nom, la référence, le choix **Produit** ou **Service** et le tarif affiché, puis confirmez. Le catalogue reprend la désignation, le prix unitaire avant remises, l’unité, la devise, le taux et la catégorie de TVA. La ligne reste dans le devis. Vous pourrez insérer l’article depuis le sélecteur d’inventaire sur un autre devis dans la même devise.

Si la référence existe, **Utiliser cet article** reprend sa référence et préserve le prix négocié dans le devis. Sans référence, la recherche utilise le nom de l’article. En présence de plusieurs résultats, précisez la référence ou choisissez l’article dans l’inventaire. La création d’un article laisse son stock à zéro.

### Créer et suivre une facture

Depuis **Factures → Nouvelle facture**, sélectionner le client, les dates, la devise et les lignes. Le téléchargement produit le PDF Factur-X avec son XML embarqué lorsque les données sont valides. La génération est locale ; les validations XSD et Schematron doivent aboutir. Une erreur doit être corrigée avant l’émission.

Le PDF téléchargeable et les fichiers XML/rapport de validation apparaissent dans la fiche. Générer ou télécharger un fichier ne signifie pas qu’il a été livré à son destinataire. Consulter séparément le statut du document et celui de l’email ou du dépôt PDP.

Les brouillons restent modifiables. Les documents émis conservent leurs montants, coordonnées et fichiers originaux. Une facture émise se corrige avec un **avoir**, créé depuis **Avoirs** en sélectionnant la facture d’origine, le même client et la même devise. Une facture émise n’est pas remplacée par une nouvelle version silencieuse.

L’action **Marquer payé** correspond à un règlement intégral. Un paiement partiel peut être déclaré sur la fiche client de la facture ou rapproché depuis **Finances → Transactions**. Une facture échue avec reste dû peut faire l’objet d’une relance manuelle depuis sa fiche.

#### Suivre les encaissements des ventes

Dans **Factures**, les encarts **À encaisser aujourd’hui** et **Dont en retard** affichent les soldes ouverts à ce jour. **Encaissé sur la période** utilise la date effective des règlements, même si la facture a été émise avant la période. La période initiale couvre les douze derniers mois. Cliquez sur un montant pour voir les factures correspondantes, séparées par devise. La recherche porte sur les tiers, le numéro et l’objet. Sans encart sélectionné, les dates filtrent la date d’émission.

Ouvrez la facture et utilisez **Suivi de la facture client → Enregistrer le règlement** avec son montant et sa date. Pour une facture de 1 200 € TTC échue et un paiement de 400 €, il reste 800 € à encaisser, dont 800 € échus. À l’arrivée de la transaction, Bridge peut confirmer le paiement existant. Le même versement reste compté une seule fois. La fiche donne accès à la transaction. **Annuler la déclaration** permet de corriger un montant saisi avant confirmation bancaire.

Le rapprochement automatique nécessite l’option activée, une transaction comptabilisée dans la bonne devise et une référence de facture non ambiguë. Pour confirmer automatiquement une déclaration, le montant doit correspondre et les dates doivent être à sept jours d’écart au plus. En cas d’homonymie de montants, ouvrez **Finances → Transactions → Traiter**, puis sélectionnez la déclaration avec sa date ou **Nouveau règlement** si la transaction est supplémentaire. Les règlements historiques de date inconnue restent visibles sur leur fiche.

Dans la fiche, **Émission**, **Remise et transmission** et **Encaissement** se lisent séparément. Après remise du PDF en main propre ou dans un espace client, utilisez **Déclarer la remise au client**. Les événements de messagerie et de SUPERPDP conservent leur propre suivi.

#### Relancer depuis la liste des retards

Dans **Factures clients**, ouvrez **Dont en retard**. Cliquez sur **Préparer une relance** pour une facture, ou cochez plusieurs lignes puis cliquez sur **Préparer les relances sélectionnées**. Relisez le destinataire, l’objet, le texte et le solde de chaque facture. Pour une vente de 1 200 € avec 400 € déjà rapprochés, l’e-mail réclame **800 €**.

**Confirmer l’envoi** place les messages dans **Emails**. La fiche de la facture conserve la trace de la relance. Une facture soldée, un destinataire manquant ou une relance déjà en attente ou envoyée le même jour sont signalés. Si un paiement arrive entre l’aperçu et la confirmation, ouvrez un nouvel aperçu. Si le solde change après confirmation, le worker annule le message devenu obsolète avant son expédition. Le mode de messagerie **outbox** conserve les messages pour consultation locale.

#### Champs à vérifier avant émission

| Rubrique | Informations à renseigner |
| --- | --- |
| Entreprise émettrice | Pays d’établissement, identité légale, forme juridique, immatriculation, adresse, régime de TVA et option sur les débits. Les entreprises françaises disposent de suggestions de formes juridiques. Une entreprise étrangère peut renseigner sa forme locale. |
| Client | Professionnel ou particulier, raison sociale ou nom, adresse de facturation, adresse de livraison et e-mail de facturation. Pour une entreprise étrangère, distinguez l’immatriculation locale du numéro de TVA. |
| Document | Objet, référence interne, référence acheteur, bon de commande, nature de l’opération, dates d’émission, d’échéance et de livraison ou de prestation, devise, moyen et conditions de paiement. |
| Lignes | Désignation, référence, quantité, unité, prix unitaire HT ou TTC, remise, taux et catégorie de TVA. |
| Fiscalité et mentions | Pour une ligne taxée, saisissez le taux applicable, y compris un taux libre. À 0 %, choisissez entre taux zéro, exonération, autoliquidation, livraison intra-UE, export hors UE et hors champ. Renseignez le motif applicable dans **Notes / motif d’exonération**. Pour une livraison intra-UE, précisez la date et le pays de livraison. |

Le taux de TVA s’applique à la ligne. La forme juridique saisie pour l’entreprise ne sélectionne pas le taux.

#### Renseigner les données commerciales

Dans **Commande et livraison**, sélectionnez la nature de l’opération, saisissez le numéro du bon de commande fourni par l’acheteur et la date réelle de livraison ou de réalisation. Choisissez l’adresse de la fiche client, l’adresse de facturation ou une adresse propre au document. Ces données alimentent le PDF et son XML Factur-X. La mention de TVA sur les débits reprend le réglage de l’entreprise enregistré dans le document. Un client étranger peut avoir un identifiant d’immatriculation local et un numéro de TVA distincts. Les originaux déjà émis restent consultables dans leur version d’origine.

Un site de vente peut créer le brouillon, émettre la facture et télécharger son PDF avec une clé **API métier**. Le site associe ensuite le PDF à la commande pour le remettre au client. [Émettre et télécharger après une vente](GUIDE_API_WEBHOOKS.md#émettre-et-télécharger-après-une-vente).

### Suivre une facture avec un contact CRM

Dans la liste, le client ou fournisseur et le numéro identifient la facture. **Voir la facture** ouvre les montants, les fichiers et le suivi. **Modifier** permet de corriger un brouillon. Pour une facture déjà émise ou reçue, **Modifier le suivi** donne accès aux informations internes et l’original reste conservé.

ZeFacture rattache les factures clients et fournisseurs au contact de facturation de leur fiche CRM. Il utilise le nom ou l’adresse email figurant sur la facture. Quand une fiche ne possède aucun interlocuteur identifiable, il peut créer un contact de facturation au nom du tiers. Plusieurs correspondances possibles laissent la décision à l’équipe. Un contact choisi ou retiré manuellement conserve ce choix lors des synchronisations suivantes.

Dans **Contact CRM**, **Modifier le contact** ouvre le nom, la fonction, l’email et le téléphone préremplis. **Enregistrer les coordonnées** actualise la fiche CRM et les autres factures qui affichent cette personne. Les coordonnées inscrites dans le PDF ou le XML d’origine sont conservées.

**Créer un nouveau contact** permet de saisir une autre personne et de la lier à cette facture. Pour utiliser un contact déjà dans le CRM, ouvrir **Lier un contact existant**, le sélectionner puis cliquer sur **Lier le contact**. **Retirer le lien** détache le contact de cette facture.

Dans **Notes internes**, saisir une note et cliquer sur **Ajouter la note**. Elle apparaît avec son auteur et sa date et reste dans ZeFacture. L’auteur ou un administrateur peut la modifier. Les membres en lecture seule peuvent consulter le suivi des factures auxquelles ils ont accès.

## 6. Achats, imports et notes de frais

Sur une facture fournisseur, le téléchargement et l’archive de période privilégient la source identifiée. L’ajout d’une annexe conserve cet original. Pour un ancien dossier contenant plusieurs pièces jointes sans source identifiée, ouvrir la rubrique **Fichiers**, comparer les aperçus puis cliquer sur **Désigner comme facture originale** sur la bonne pièce. Pour une facture SUPERPDP, le lien d’origine reste fourni par la plateforme.

### Importer plusieurs factures de vente ou d’achat

1. Dans **Achats → Imports**, sélectionner les PDF ou images. Un lot accepte jusqu’à 50 fichiers, avec 15 Mo maximum par fichier. Sur le navigateur, choisir le type par fichier si le lot mélange des achats et des ventes.
2. Si une connexion IA est active, son fournisseur est présélectionné. Un clic sur **Importer** lance la lecture OCR puis l’analyse de chaque nouveau fichier du lot. Le fournisseur reçoit le texte de la pièce. **OCR et extraction locale** conserve le traitement sur le serveur. **Importer seulement** reporte le traitement. Après la sélection, cliquer sur **Importer** pour envoyer les fichiers. Chaque ligne affiche l’envoi, la mise en attente, la lecture OCR puis le préremplissage. Le symbole tourne pendant le traitement. La liste, le lot et la fiche se mettent à jour automatiquement. Quand **Analyse terminée · à vérifier** apparaît, ouvrir la fiche pour relire les champs. Un doublon renvoie à l’original. En cas d’échec d’envoi, **Importer** reprend les fichiers en erreur.
3. Un **Doublon exact** renvoie vers l’original déjà enregistré. Un **Doublon possible** invite à comparer le numéro, le tiers, la date et le montant sur la fiche. Deux scans peuvent être signalés avant validation s’ils partagent une référence, ou si le tiers, la date, le total TTC et la devise concordent. Deux fournisseurs peuvent utiliser le même numéro. Après comparaison, la case de confirmation permet de conserver les deux factures.
4. Vérifier les champs et les lignes avec l’original. Pour un achat, **Valider et créer la facture reçue** la classe dans les factures fournisseurs. Pour une vente, **Enregistrer la facture de vente déjà émise** conserve le numéro, la date et le fichier émis ailleurs. L’import ne déclenche aucun envoi au client ou à SUPERPDP. Après contrôle, les opérations bancaires peuvent être rapprochées des deux types de facture.

Pour compléter un lot déjà importé avec l’OCR local, ouvrir **Ouvrir le dernier lot importé** dans la liste ou **Voir le lot** sur une fiche. **Analyser le lot avec l’IA** indique le nombre de pièces concernées et le fournisseur. Cliquer sur le bouton lance le préremplissage sur ces pièces. Les textes OCR disponibles sont réutilisés et les pièces déjà traitées par une IA conservent leur résultat.

**Factures fournisseurs** centralise les documents reçus : saisie du numéro fournisseur, dates, lignes et original joint, ou réception synchronisée depuis SUPERPDP. Les actions disponibles permettent acceptation, refus, litige et règlement selon le statut. Le téléchargement restitue l’original fourni ; il ne fabrique pas une facture à la place du fournisseur.

Ouvrir **Achats et finances → Factures fournisseurs**. La première vérification de l’entreprise chez SUPERPDP lance la récupération de l’historique. Le panneau **Factures reçues via SUPERPDP** affiche le nombre de documents enregistrés et la date de la dernière récupération réussie. **Synchroniser SUPERPDP** lance une nouvelle recherche. Un administrateur peut activer la récupération périodique dans **Gérer la synchronisation**.

Dans les actions d’une facture, choisir **Original XML** ou **Original PDF** pour enregistrer le fichier du fournisseur sur son ordinateur. Pour un original XML, **PDF de lecture** permet aussi de télécharger la version lisible lorsqu’elle est disponible. Les fichiers sont déjà conservés dans ZeFacture dès la synchronisation. Cliquer sur **Voir la facture** pour consulter les lignes et le **Suivi SUPERPDP**. [Guide SUPERPDP](GUIDE_SUPERPDP.md).

Pour télécharger plusieurs factures en une fois, ouvrir **Factures fournisseurs → Rechercher des factures**. Choisir **Date de facture** ou **Réception dans ZeFacture**, puis renseigner **Du** et **Au**. Les deux jours sont inclus. Sélectionner **PDF uniquement**, **XML uniquement** ou **PDF et XML** et cliquer sur **Télécharger le ZIP**. La recherche et le statut s’appliquent aussi au ZIP, qui inclut toutes les pages. Le fichier `fichiers-indisponibles.txt` signale les formats demandés qui manquent dans une archive partielle.

Depuis le tableau de bord ou les listes, les encarts de statut ouvrent les factures correspondantes. Dans **Factures fournisseurs**, choisir par exemple **Reçues**, **Payées** ou **En litige**. **Tous** réunit les statuts de la période.

Dans **Rechercher des factures**, cliquer sur **Filtrer** pour actualiser la liste et les cartes **Totaux de la sélection**. Elles couvrent toutes les pages et affichent le HT, la TVA, le TTC et le reste dû actuel, par devise. Les avoirs sont déduits du HT, de la TVA et du TTC. **Effacer les filtres** revient à la liste complète. **Synchroniser SUPERPDP** conserve les filtres appliqués.

**Aperçu** ouvre les PDF et les images depuis les factures fournisseurs, les imports et les notes de frais. Les flèches changent de page. Utiliser **+**, **−** ou **Ajuster à la largeur** pour lire un détail sur ordinateur ou téléphone. Si SUPERPDP fournit un original XML accompagné d’un PDF de lecture, l’aperçu utilise ce PDF. Le lien de téléchargement reste disponible. Les montants affichés viennent de la facture liée ou du TTC déjà enregistré sur le justificatif.

Pour importer des reçus, ouvrir **Achats → Notes de frais → Importer des justificatifs**, choisir le type **Ticket de caisse** ou **Note de frais** et joindre les PDF ou images. Cliquer sur **Importer** pour lancer le traitement choisi. La liste permet de rechercher un commerçant, un motif ou un fichier et de filtrer par catégorie, dates de dépense et remboursement. Les totaux couvrent toutes les pages et séparent les devises. Les notes dont le montant ou la devise manque sont signalées.

**Voir la note** affiche directement le justificatif. Utiliser le zoom ou les flèches pour examiner une pièce de plusieurs pages, **Agrandir** pour ouvrir le lecteur et **Télécharger l’original** pour conserver le fichier. Comparer au justificatif le commerçant, la date, le TTC, la devise et la catégorie, puis cliquer sur **Enregistrer le justificatif**. **Relancer l’analyse** permet de reprendre une lecture incomplète.

Pour une facture fournisseur, passer par **Imports → Facture fournisseur**. Après transcription, relire les informations de facturation, le total TTC et les lignes. La conversion en facture reçue est une action explicite réservée aux rôles financiers.

### Configurer les clés IA de l’entreprise

Avec un compte administrateur, ouvrir **Réglages de l’entreprise → Intelligence artificielle** ou **Gérer les clés IA de l’entreprise** dans **Imports**. Sur la carte Google Gemini, OpenAI, Anthropic ou OpenRouter, choisir **Ajouter une clé**, renseigner la clé et l’identifiant du modèle puis **Enregistrer la connexion**. **Tester avec un ticket fictif** contacte le fournisseur. Le bouton affiche un indicateur et le nom du fournisseur pendant la requête. Le résultat apparaît sous le bouton, qui redevient disponible pour un nouvel essai. Une clé enregistrée est chiffrée. **Modifier la connexion** permet de remplacer la clé ou de changer uniquement le modèle. **Désactiver et retirer la clé** coupe le fournisseur pour l’entreprise.

Pour Gemma 4, créer la clé dans Google AI Studio et saisir par exemple `gemma-4-26b-a4b-it` dans **Modèle** sur la carte **Google Gemini**. Le quota disponible dépend du projet Google. [Modèles Gemma hébergés par Google](https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api) · [Tarifs Google](https://ai.google.dev/gemini-api/docs/pricing#gemma-4)

Sur une facture importée, **Préremplir avec l’IA** permet de lancer une analyse après transcription. À l’import, **OCR puis analyse avec** le fournisseur configuré est déjà sélectionné. Avec plusieurs connexions, choisir le **Fournisseur par défaut** dans **Réglages de l’entreprise → Intelligence artificielle**. Ce choix peut être modifié pour un lot. Le texte OCR est transmis à ce fournisseur. Sur une note, l’IA propose le commerçant, la date, le TTC, la devise, la TVA lisible et la catégorie des biens ou services. Elle peut aussi décrire l’achat. Le moyen de paiement, l’origine des fonds et le motif professionnel nécessitent des indices explicites sur la pièce. Sur une facture, le tiers, ses coordonnées, le numéro, les dates, les totaux et les lignes identifiables remplissent le formulaire. Un moyen de paiement reconnu peut aussi être proposé. Les éléments absents ou ambigus restent à compléter. Les montants, la TVA et les dates doivent être relus avant enregistrement. Les informations déjà saisies dans la fiche de frais restent prioritaires. Voir la [configuration des fournisseurs](GUIDE_AUTOHEBERGEMENT.md#analyse-facultative-des-justificatifs).

### Indiquer le règlement et le motif d’une dépense

Sur la fiche, les champs **Paiement et motif** sont visibles à côté du justificatif. Choisir le moyen de paiement et préciser qui a avancé les fonds. Vérifier le motif professionnel, puis cliquer sur **Enregistrer le justificatif**.

### Suivre le remboursement d’une note

Une note enregistrée en **Fonds personnels** apparaît dans **À rembourser**. Une dépense réglée par l’entreprise apparaît dans **Sans remboursement**. Quand l’origine est inconnue, la note figure dans **À préciser**. Un responsable ou un comptable peut ajuster l’état sur la fiche. Après le versement, sélectionner **Remboursée**, indiquer **Date du versement** et cliquer sur **Enregistrer le remboursement**. Pour corriger un montant déjà remboursé, rectifier d’abord l’état du remboursement. Ce suivi déclare un versement effectué. Le mouvement bancaire correspondant se retrouve dans **Transactions**.

Le tableau de bord affiche les notes de la période selon leur date de dépense, leur état actuel et leurs montants par devise. Cliquer sur un état ouvre les notes correspondantes. Le rapport **Notes de frais** inclut l’état du remboursement et la date du versement.

À l’import d’un lot, les détails de dépense s’appliquent aux achats et aux justificatifs sélectionnés. Chaque fiche permet de les ajuster. Les descriptions saisies dans les anciennes notes figurent sous **Motif de la dépense**. Le rapport **Notes de frais** reprend le motif, le moyen de paiement et l’origine des fonds dans les exports PDF, CSV et Excel.

Sur une facture fournisseur, **Règlement et motif de la dépense** présente le même suivi que le justificatif d’origine. Sur une facture client, **Règlement reçu** permet de préciser le moyen d’encaissement. Pour solder une facture, sélectionner le **Moyen de paiement du solde** puis **Marquer payé**. L’enregistrement des renseignements seuls laisse le solde à régler disponible pour le rapprochement bancaire.

Pour retirer une note de la liste active, ouvrir sa fiche et choisir **Archiver le justificatif**. Depuis **Notes de frais → Voir les archives**, ouvrir la note puis **Restaurer le justificatif**. L’original et la facture liée sont conservés. Les rapports de notes de frais portent sur les justificatifs actifs.

Les administrateurs et comptables peuvent traiter les frais de l’entreprise. Les autres utilisateurs gardent la visibilité de leurs propres justificatifs. Enregistrer les champs du ticket conserve l’original et ne crée pas de facture ni de paiement.

## 7. Finances et rapprochement

### Relier une ou plusieurs banques

1. Un propriétaire ou un administrateur ouvre **Finances → Comptes bancaires → Configurer Bridge**, renseigne les identifiants de son application Bridge et enregistre. Le guide d’auto-hébergement décrit l’activation du service d’agrégation. [Configuration de Bridge](GUIDE_AUTOHEBERGEMENT.md#connexion-bancaire-bridge).
2. Cliquer sur **Relier ma banque**, puis sur **Continuer vers Bridge**. Choisir l’établissement et les comptes de paiement à partager. Valider la demande auprès de la banque, puis revenir dans ZeFacture.
3. Pour ajouter une deuxième banque, renouveler un accès ou retirer un compte, le même administrateur choisit **Relier ou gérer mes banques**. Chaque compte partagé dispose de son propre solde et d’un lien **Transactions de ce compte**. La période d’historique disponible dépend de l’établissement.
4. **Synchroniser maintenant** demande une actualisation au serveur. Le worker récupère ensuite les nouvelles opérations et les corrections toutes les quatre heures. La date de dernière réussite et les éventuelles erreurs apparaissent dans **Comptes bancaires**.

### Saisir un salaire, une CFE ou un autre décaissement

Dans le tableau de bord, **Paiements hors facture** présente les opérations bancaires classées de la période. Choisir **Saisir un salaire**, **Saisir la CFE** ou **Saisir une dépense**. Renseigner la date, le libellé, le montant et le compte, vérifier la catégorie puis enregistrer. Si le paiement figure déjà dans Bridge ou dans un relevé importé, ouvrir l’opération dans **Transactions → Traiter → Classer sans facture** pour utiliser la ligne existante. Les raccourcis sont disponibles aux rôles financiers.

### Importer un relevé bancaire

Dans **Finances → Comptes bancaires**, créer un compte avec sa devise et son solde d’ouverture, ou reprendre un compte déjà relié à Bridge. Choisir **Importer des relevés**, vérifier le compte de destination et sélectionner jusqu’à **50 fichiers du même compte** en une fois, avec 5 Mo maximum par fichier. Les formats peuvent être mélangés. Le suivi indique l’état de chaque fichier et les doublons. **Ouvrir mon dernier lot** permet de reprendre la vérification pendant sept jours.

| Format | Contenu et vérification |
| --- | --- |
| CSV | Jusqu’à 1 000 opérations. Indiquer la date, le libellé et le montant signé, ou des colonnes débit et crédit. Une référence bancaire facilite la détection des doublons. |
| OFX ou QFX | Un compte bancaire ou carte par fichier, avec les opérations comptabilisées, leur identifiant bancaire et la devise du compte. |
| PDF texte ou scanné | Jusqu’à 15 pages et 100 propositions. La lecture et l’OCR sont locaux. Si un fournisseur IA est configuré, il est présélectionné et reçoit le texte des pages pour proposer les dates, libellés, montants et sens. Choisissez **Lecture locale uniquement** pour garder ces textes sur le serveur. Si le fournisseur est indisponible, la lecture locale permet de continuer. Comparer le **Texte lu dans le relevé** à l’original, corriger la date, le libellé, le montant et le sens. **Ajouter une ligne** permet de compléter une opération manquante. Cocher les opérations vérifiées puis **Confirmer les lignes cochées**. |

Dans **Finances → Transactions**, **Toutes** présente les mouvements de tous les comptes. **Sans facture liée** inclut les dépenses déjà classées et les opérations encore à traiter. Utilisez **À traiter**, **Classées sans facture** ou **Rapprochées** pour affiner la vue. La période, le compte et la recherche s’appliquent aux compteurs. Les encaissements, décaissements et variations nettes des écritures comptabilisées sont calculés pour toute la sélection et affichés séparément par devise.

Le récapitulatif indique les lignes ajoutées, déjà présentes ou rejetées. Un nouvel import du même relevé conserve ses écritures déjà enregistrées. Les correspondances entre sources utilisent le compte, la date, le montant, le sens et la référence ou le libellé. Vérifier les libellés qui diffèrent entre la banque et le relevé.

### Classer un mouvement sans facture

Dans **Finances → Transactions**, filtrer sur **À traiter**, ouvrir **Traiter** sur la ligne, puis **Classer sans facture**. Choisir par exemple **Salaires**, **Cotisations sociales**, **Remboursement de frais versé**, **Paiement de TVA**, **Frais bancaires** ou **Autre dépense** pour une sortie. Pour une entrée, choisir **Remboursement de TVA reçu**, **Autre remboursement reçu** ou **Autre encaissement**. Le champ **Motif ou bénéficiaire** permet de préciser la dépense. Cliquer sur **Enregistrer le classement**. Une opération saisie dans **Nouvelle transaction** peut recevoir sa catégorie immédiatement.

Le classement porte sur l’écriture bancaire existante et conserve son montant. Bridge respecte le classement lors des synchronisations suivantes. **Modifier** permet de corriger la catégorie. **Remettre à traiter** rend l’opération disponible pour un rapprochement avec une facture. **État de TVA** affiche les paiements et remboursements bancaires classés pour la période, à côté des montants issus des factures. Les filtres de la liste et les rapports permettent de retrouver les catégories et les motifs dans les exports.

### Rapprocher les règlements

Dans **Transactions**, rechercher un libellé ou filtrer par compte. L’option **Rapprocher automatiquement** associe un mouvement comptabilisé à une facture identifiée par son numéro complet, sa devise et le sens du paiement. Pour une vente, un montant partiel peut réduire le reste dû ou confirmer un règlement déclaré. Pour un achat, le rapprochement automatique exige le montant du solde. Un encaissement règle une facture client et un décaissement règle une facture fournisseur. L’historique de la facture conserve le paiement.

Pour un paiement partiel ou un libellé sans référence exploitable, ouvrir **Traiter → Rapprocher d’une facture**, sélectionner la facture et cliquer sur **Rapprocher**. Dans **Opérations à vérifier**, choisir l’écriture existante si elle correspond au même mouvement, ou **Créer une opération distincte** si deux paiements sont réellement différents. Si la banque annule ou modifie un paiement déjà rapproché, vérifier le mouvement puis confirmer la correction. La facture retrouve alors le montant dû correspondant.

### Comptes manuels et exports

**Ajouter un compte manuel** permet de suivre un autre compte avec sa devise et son solde d’ouverture. **Exporter le CSV** reprend les filtres de la liste. Les menus **Trésorerie**, **État de TVA** et **Export comptable** restent accessibles aux propriétaires, administrateurs et comptables.

Les soldes sont présentés par devise. L’état de TVA utilise la date d’émission des factures. Le journal exporté sert au suivi de la facturation. Pour le régime des encaissements ou la production du fichier des écritures comptables (FEC), faire valider les traitements complémentaires par le comptable de l’entreprise.

## 8. Dashboard, périodes et comparatifs

**Vue d’ensemble** affiche les indicateurs de la période, les comparatifs, les sommes à encaisser/à payer, les devis en attente, les factures échues et les derniers documents. Les indicateurs bancaires sont réservés aux rôles financiers.

Dans le filtre :

1. Choisir les dates **Du / Au**, ou le mois, trimestre, l’année en cours ou toutes les dates.
2. Comparer à la période précédente de même durée, à la même période l’année précédente, à des dates personnalisées, ou désactiver la comparaison.
3. Cliquer sur **Appliquer**. Avec des dates de comparaison personnalisées, compléter les deux bornes.

**Rapports** ajoute l’exploration des jeux de données et un tableau d’évolution mensuelle. Les tableaux et cartes sont prédéfinis ; cette version ne possède pas de constructeur de widgets ou de dashboard libre.

Pour lire correctement les chiffres : ventes/achats suivent l’émission, avec déduction des avoirs et exclusion des brouillons, annulations, refus et simulations. Les flux bancaires suivent les transactions comptabilisées. Les restes dus affichés sont ceux d’aujourd’hui sur les documents sélectionnés, pas un solde historique reconstitué à la date de fin. Aucune variation en pourcentage n’est calculée sur une base nulle.

## 9. Télécharger en PDF, CSV ou Excel

Dans le journal des factures, un achat est négatif et son avoir est positif. Un achat de 120 € TTC suivi d’un avoir fournisseur de 24 € représente −96 € TTC. Si un ancien export comportait des avoirs fournisseurs, régénérer le fichier de la période et vérifier avec le comptable les montants déjà repris.

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

**Enregistrer et tester** vérifie le serveur SMTP et affiche un résumé après succès. Pour rouvrir le formulaire, choisir **Modifier**. **Tester la connexion sans envoyer** vérifie l’accès à la messagerie. **Envoyer un test à mon adresse** prépare un email d’essai. Vérifier ensuite le résultat dans **Emails** et dans votre boîte de réception. Le statut `sent` signifie que le serveur ou fournisseur a accepté l’envoi, sans garantir sa réception ou sa lecture.

Après un changement de connexion, les anciens messages ne sont pas reroutés automatiquement. Vérifier destinataire et expéditeur avant leur relance explicite. Une erreur réseau peut survenir après acceptation distante : consulter la messagerie avant de réessayer pour éviter un doublon.

Les clés API se créent dans **API & intégrations** ; les webhooks sortants dans l’écran associé. Ils permettent d’automatiser certains échanges, selon le [contrat documenté](GUIDE_API_WEBHOOKS.md). L’API ne couvre pas encore toutes les fonctions de l’interface.

## 11. Personnalisation et utilisation sur téléphone

Dans **Personnalisation**, les rubriques **Identité visuelle**, **Documents**, **Adresse et signature** et **Modèles d’emails** possèdent chacune leur propre enregistrement. L’aperçu de facture dans les deux premières rubriques réagit au nom, au logo, à la couleur, à la présentation, à la devise et à l’échéance avant enregistrement. Les mentions légales et bancaires restent dans **Mon entreprise**. Les variables autorisées des modèles figurent dans l’écran et le [guide d’administration](GUIDE_AUTOHEBERGEMENT.md#8-personnalisation-pour-les-clients).

Une nouvelle identité visuelle s’applique aux nouveaux documents et aux brouillons sauvegardés ; elle ne modifie pas les originaux déjà émis. Les entreprises d’une même instance conservent leurs réglages distincts.

**Mon compte → Préférences d’affichage** propose thème clair, sombre ou système et tableaux confortables/compacts. Sur téléphone, ouvrir la navigation avec **Menu**. Les blocs et formulaires se réorganisent ; faire défiler horizontalement les tableaux larges dans leur zone. Les mêmes comptes et droits s’appliquent sur ordinateur et mobile. L’application nécessite une connexion au serveur ; il n’existe pas de mode de travail hors ligne avec synchronisation.

## 12. Résoudre les difficultés courantes

| Situation | Action |
| --- | --- |
| Une rubrique est absente ou renvoie un refus | Vérifier l’entreprise sélectionnée et demander à un administrateur de contrôler son rôle |
| Un membre ne reçoit pas son invitation | Vérifier l’adresse et la boîte d’envoi ; un administrateur peut transmettre le lien privé encore valide |
| Un tableau paraît vide | Choisir **Toutes les dates**, contrôler le jeu de données et l’entreprise |
| Le stock ne change pas après facturation | Ouvrir **Récupérer les achats et les ventes**, choisir la facture et confirmer les lignes physiques |
| Une facture émise doit être corrigée | Préparer un avoir lié ; ne pas modifier ses originaux |
| Un original n’est plus téléchargeable | Demander à l’exploitant de restaurer l’original vérifié depuis une sauvegarde |
| Gmail/Outlook demande une reconnexion | Faire réautoriser la connexion par un administrateur ; les prérequis fournisseur sont détaillés dans le guide serveur |
| Un code TOTP est refusé | Vérifier l’heure du téléphone et utiliser le code suivant ou un code de secours inutilisé |
| Téléphone et codes de secours perdus | Contacter l’exploitant pour une vérification d’identité et la récupération administrative |

Pour une assistance, indiquer l’écran, la date, le message d’erreur et un exemple fictif. Ne pas transmettre de mot de passe, clé API, code de secours ou document client dans un ticket public.
