# ZeFacture — API et webhooks

Contrat de l’édition Django, vérifié dans les routes et services le 19 novembre 2026. Les exemples sont fictifs. Remplacez `https://factures.example.com` par l’adresse HTTPS de votre instance ZeFacture.

## 1. Choisir le sens de l’échange

| Besoin | Fonction disponible |
| --- | --- |
| Lire/créer des clients, devis ou factures depuis un autre outil | API HTTP `/api/v1` avec clé Bearer |
| Accepter un devis, le facturer, créer un avancement ou générer Factur-X | Actions POST documentées ci-dessous |
| Émettre une facture et remettre son PDF depuis un site marchand | Émission par POST, téléchargement authentifié du PDF par GET |
| Être averti d’une modification dans ZeFacture | Webhooks sortants configurables par entreprise |
| Récupérer stocks, CRM ou finances pour analyse | Exports CSV/Excel/PDF depuis **Rapports** |
| Synchroniser les factures et statuts SUPERPDP | Synchronisation officielle configurée par entreprise ; récepteur `/webhooks/superpdp` facultatif pour un relais autorisé |
| Interroger ZeFacture depuis Codex ou un autre assistant | Connecteur MCP et API de consultation en lecture seule, voir [configuration](#9-consulter-zefacture-depuis-codex) |

Il n’existe pas de récepteur de webhook générique permettant de modifier toutes les données. L’API publique n’expose pas encore de CRUD des stocks, contacts CRM, opportunités, membres, comptes bancaires ou paramètres email. Ne pas utiliser les routes de formulaires `/app/` comme contrat d’API.

L’intégration SUPERPDP et la commande de synchronisation sont décrites dans le [guide dédié](GUIDE_SUPERPDP.md). Les clés `/api/v1` ne sont pas des jetons d’accès au compte SUPERPDP.

## 2. Créer et protéger une clé

Un propriétaire ou administrateur ouvre **API & intégrations**, nomme une clé pour l’outil destinataire et choisit **API métier** ou **Codex · lecture seule**. Il copie la valeur affichée une seule fois. La clé appartient à l’entreprise sélectionnée et peut être révoquée depuis cette même page. Son empreinte seule est conservée pour l’authentification. Les clés créées avant l’ajout de ce choix gardent l’accès **API métier**.

Transmettre la clé uniquement dans l’en-tête :

```http
Authorization: Bearer zf_live_VOTRE_CLE
Content-Type: application/json
```

Conserver la valeur dans le gestionnaire de secrets du système appelant. L’accès **API métier** donne accès aux routes de création et de lecture documentées ci-dessous. L’accès **Codex · lecture seule** autorise uniquement les routes de consultation `/api/v1/assistant/` de cette entreprise, y compris ses justificatifs de frais. Il ne donne accès ni aux originaux ni au texte OCR brut. Les clés ne sont pas liées au rôle d’un membre, n’expirent pas automatiquement et n’exigent pas de second facteur par appel. Créer une clé par intégration et révoquer les anciennes après rotation.

L’API nécessite HTTPS en production. Les sessions et formulaires du navigateur ne sont pas nécessaires pour les appels Bearer. Le contrôle d’accès est limité à l’entreprise de la clé ; un identifiant provenant d’une autre entreprise ne donne pas accès à ses données.

### Premier appel sans inscrire la clé dans le script

Exécuter ce programme Python avec l’origine réelle de l’instance ; il ne fait que lire les clients :

```python
import getpass
import json
from urllib.request import Request, urlopen

origin = input("Origine de l’instance (https://...) : ").strip().rstrip("/")
key = getpass.getpass("Clé API : ")
request = Request(
    origin + "/api/v1/customers",
    headers={"Authorization": "Bearer " + key},
)
with urlopen(request, timeout=30) as response:
    print(json.dumps(json.load(response), ensure_ascii=False, indent=2))
```

Le résultat contient des données métier : conserver les sorties privées. Pour un POST, utiliser la même authentification, `Content-Type: application/json` et un corps JSON encodé en UTF-8.

## 3. Routes et réponses

Les routes sont sans slash final. `{uid}` désigne l’identifiant du document renvoyé par ZeFacture.

| Méthode | Route | Résultat |
| --- | --- | --- |
| GET | `/api/v1/customers` | Liste des clients de l’entreprise, y compris archivés avec leur statut |
| POST | `/api/v1/customers` | Crée un client, réponse 201 |
| GET | `/api/v1/quotes` | Liste des devis sortants |
| POST | `/api/v1/quotes` | Crée un devis brouillon, réponse 201 |
| GET | `/api/v1/invoices` | Liste des factures et factures d’avancement sortantes |
| POST | `/api/v1/invoices` | Crée une facture brouillon (201) ou retrouve la même création (200) |
| POST | `/api/v1/quotes/{uid}/accept` | Accepte le devis si son état et ses données le permettent, réponse 200 |
| POST | `/api/v1/quotes/{uid}/invoice` | Crée ou retrouve la facture de solde du devis accepté, réponse 201 |
| POST | `/api/v1/quotes/{uid}/progress-invoices` | Crée un avancement (201) ou retrouve le même avancement (200) |
| POST | `/api/v1/invoices/{uid}/facturx` | Génère ou vérifie le fichier Factur-X, réponse 200 |
| POST | `/api/v1/invoices/{uid}/issue` | Émet la facture et fige le PDF. Renvoie 200 avec le chemin de téléchargement |
| POST | `/api/v1/invoices/{uid}/delivery` | Consigne la remise effective au client après sa confirmation par le site marchand. Un nouvel appel retrouve le même événement. |
| GET, HEAD | `/api/v1/invoices/{uid}/pdf` | Renvoie le PDF conservé de la facture émise, ou ses en-têtes avec HEAD |

Les listes renvoient tous les éléments. Le PDF s’obtient via la route dédiée avec la clé **API métier** de l’entreprise. Les listes et les actions documentées dans le tableau constituent le contrat de cette API.

Réussite :

```json
{"data": {"document_uid": "IDENTIFIANT_RENVOYE", "status": "draft"}}
```

Les listes utilisent un tableau dans `data`. Un document comprend notamment numéro, type, direction, statut, client, dates, devise, totaux, reste dû, lignes et métadonnées des fichiers (`file_uid`, `kind`, `mime_type`, `sha256`, `file_size`). Les champs présents dépendent de la ressource ; l’extrait ci-dessus est volontairement réduit.

Erreur métier :

```json
{"error": "Clé API invalide."}
```

| Code | Interprétation habituelle |
| --- | --- |
| 400 | JSON invalide ou corps qui n’est pas un objet |
| 401 | Clé absente, invalide ou révoquée |
| 404 | Document/action introuvable ou inaccessible dans cette entreprise |
| 405 | Méthode non autorisée sur une route reconnue |
| 409 | État incompatible, document figé ou incohérence avec l’original |
| 422 | Données métier invalides ou validation Factur-X bloquante |

Une route inconnue ou une erreur du proxy peut renvoyer une réponse non JSON. Contrôler d’abord le statut HTTP et le type de contenu. Une erreur serveur ne prouve pas qu’une requête n’a produit aucun effet : consulter les données avant de rejouer une création.

## 4. Corps des créations et actions

### Créer un client

```json
{
  "type": "business",
  "legal_name": "Entreprise Exemple",
  "email": "contact@example.test",
  "address_line1": "10 rue Exemple",
  "postal_code": "75001",
  "city": "Paris",
  "country_code": "FR"
}
```

Pour un particulier, utiliser `"type": "individual"` et son nom dans `legal_name`. Champs reconnus supplémentaires : `trade_name`, `siren`, `siret`, `vat_number`, `phone`, `address_line2`, `electronic_address`, `electronic_address_scheme`. Le pays est un code à deux lettres ; SIREN/SIRET renseignés doivent respecter leurs longueurs. Préserver les identifiants en chaînes.

Ce POST crée une nouvelle fiche : il ne fait pas de mise à jour par email ou `customer_uid`. Il n’enregistre pas les contacts, opportunités et paramètres du profil CRM. Lire `data.customer_uid` pour les documents suivants.

### Créer un devis ou une facture

Envoyer ce corps à `/api/v1/quotes` ou `/api/v1/invoices`, après remplacement du client par un identifiant actif de la même entreprise :

```json
{
  "customer_uid": "IDENTIFIANT_DU_CLIENT",
  "issue_date": "2026-11-06",
  "due_date": "2026-12-06",
  "currency_code": "EUR",
  "subject": "Prestation de démonstration",
  "reference": "Commande externe DEMO-001",
  "lines": [
    {
      "description": "Prestation",
      "quantity": "2",
      "unit_price_excluding_tax": "50.00",
      "vat_rate": "20",
      "discount_percent": "0",
      "unit_code": "C62"
    }
  ]
}
```

Les dates sont `AAAA-MM-JJ`, l’échéance ne précède pas l’émission. Les valeurs omises utilisent les valeurs par défaut de l’entreprise. Devises disponibles : EUR, USD, GBP, CHF, CAD, JPY, SEK, NOK, DKK et PLN.

Il faut de 1 à 1 000 lignes ; quantité positive, prix non négatif, TVA et remise entre 0 et 100. Envoyer les décimaux en chaînes avec un point. ZeFacture recalcule les totaux ; les réponses API convertissent les valeurs décimales en nombres JSON. Éviter de reconstruire des montants comptables à partir de nombres flottants binaires côté client.

Pour un tarif TTC, envoyez `unit_price_including_tax` dans la ligne, avec `vat_rate`. Le TTC renseigné sert de base au calcul du prix unitaire HT. Le champ historique `unit_price_excluding_tax` reste disponible pour les tarifs HT. Le taux détermine la catégorie par défaut (`S` si positif, `Z` à zéro). À zéro, `vat_category_code` permet aussi de préciser `E` (exonérée), `AE` (autoliquidation), `K` (livraison intra-UE), `G` (export) ou `O` (hors champ), selon l’opération.

Pour appliquer une remise à tout le document, ajoutez à la racine `global_discount_type` et `global_discount_value`. Les types acceptés sont `percent` (0 à 100, après les remises de ligne) et `amount` (montant fixe HT au centime dans la devise du document). Le serveur répartit la remise entre les taux. La réponse donne `global_discount_amount`, les totaux, `rounding_amount` et `amount_due`. Par exemple, cette partie d’une requête produit 180,00 € HT, 27,00 € de TVA et 207,00 € à payer.

```json
{
  "global_discount_type": "percent",
  "global_discount_value": "10",
  "lines": [
    {"description": "Article", "quantity": "1", "unit_price_including_tax": "120", "vat_rate": "20"},
    {"description": "Service", "quantity": "1", "unit_price_excluding_tax": "100", "vat_rate": "10"}
  ]
}
```

Pour générer un Factur-X à la création d’un brouillon, ajouter `"generate_facturx": true`. Les coordonnées nécessaires à la validation doivent être complètes. Un échec de cette génération annule la création dans la transaction. Pour un parcours de vente, créez le brouillon puis appelez `/issue`, qui génère le Factur-X et fige la facture en une seule opération.

Pour accepter un devis ou créer le solde, envoyer `{}` à l’action correspondante. Pour un avancement, transmettre une clé propre à l’étape dans l’en-tête `Idempotency-Key`, par exemple `chantier-demo-etape-1`, et le pourcentage dans le corps de la demande.

```json
{"percent": "30"}
```

Le devis doit être accepté et le pourcentage rester dans le solde disponible. Les avancements antérieurs sont déduits et un solde déjà créé est retrouvé par l’action `/invoice`.

### Émettre et télécharger après une vente

1. Une fois le paiement confirmé par votre site, créer ou retrouver la fiche client et le brouillon de facture. Associer le `document_uid` renvoyé à l’identifiant de la commande.
2. Envoyer `POST /api/v1/invoices/{uid}/issue` avec un corps vide ou `{}`. ZeFacture valide le Factur-X, conserve le PDF et fige les données de la facture. En cas d’échec de la validation, le document reste un brouillon.
3. Récupérer `data.pdf_url` dans la réponse et télécharger ce chemin sur la même instance avec la clé **API métier**. Le site marchand peut ensuite remettre le document au client selon ses propres règles d’accès.

Après la remise effective du document, le site peut appeler `POST /api/v1/invoices/{uid}/delivery` avec un corps vide. Le champ `data.delivered_at` donne l’horodatage de la déclaration. Le téléchargement du PDF peut servir à une prévisualisation et laisse cette date disponible pour la confirmation réelle.

La création de devis et factures accepte aussi `delivery_date` (AAAA-MM-JJ), `purchase_order` (référence de commande de l’acheteur), `operation_category` (`B1` biens, `S1` services, `M1` mixte) et `delivery_address` (objet `line1`, `line2`, `postal_code`, `city` et `country_code` ISO). Une adresse vide explicite reprend l’adresse de facturation. En omettant le champ sur une nouvelle facture, elle reprend l’adresse de livraison configurée sur le client. Les clients acceptent également `local_registration` et `delivery_address`.

La réponse d’émission contient les métadonnées habituelles, avec `issued_at` et `pdf_url`. Le statut `sent` désigne l’émission locale. Le site appelant gère la remise au client. L’envoi par email, le dépôt SUPERPDP et l’enregistrement du règlement sont des opérations distinctes dans ZeFacture.

```sh
ZEFACTURE_ORIGIN=https://factures.example.com
INVOICE_UID=IDENTIFIANT_RENVOYE_A_LA_CREATION
# Charger ZEFACTURE_API_KEY depuis les secrets du serveur.

curl --fail-with-body --silent --show-error \
  --request POST \
  --header "Authorization: Bearer ${ZEFACTURE_API_KEY}" \
  --header "Content-Type: application/json" \
  --data '{}' \
  "${ZEFACTURE_ORIGIN}/api/v1/invoices/${INVOICE_UID}/issue"

curl --fail --silent --show-error \
  --header "Authorization: Bearer ${ZEFACTURE_API_KEY}" \
  --output facture.pdf \
  "${ZEFACTURE_ORIGIN}/api/v1/invoices/${INVOICE_UID}/pdf"
```

L’émission peut être rejouée avec le même `document_uid`. Un deuxième appel retrouve la facture et son PDF, même si la facture a ensuite été marquée payée. Le serveur renvoie le fichier conservé en `application/pdf` et en pièce jointe. Le téléchargement est en lecture seule. Un brouillon, une simulation ou une facture dont le PDF conservé est absent ou endommagé renvoie 409. Une facture reçue ou d’une autre entreprise renvoie 404. Le PDF d’une facture synchronisée depuis SUPERPDP est celui déjà conservé avec cette facture, y compris la version lisible lorsque l’original est en XML.

Pour suivre le passage au statut émis, abonnez votre intégration à `document.status_changed` et filtrez sur `type=invoice` ou `progress_invoice`, `direction=outgoing` et `status=sent`. Chargez ensuite le PDF depuis le serveur de l’intégration.

### Reprises et volume

À chaque commande, conserver durablement une référence unique dans la boutique, par exemple `boutique-demo-commande-42`. Transmettre cette valeur dans le champ JSON `reference` ou l’en-tête HTTP `Idempotency-Key` (1 à 200 caractères ASCII imprimables). ZeFacture exige au moins un des deux pour créer une facture ou un avancement. Les références et les clés sont propres à l’entreprise et restent reconnues après une rotation de la clé API. Préfixer les commandes si plusieurs boutiques utilisent la même entreprise.

Après une coupure réseau, renvoyer la même demande avec le même identifiant et le même contenu. La première création renvoie **201**, une répétition **200** avec le même `document_uid`, y compris après l’émission. Une demande sans identifiant renvoie **400**. Un contenu différent avec un identifiant déjà utilisé, ou une référence déjà présente sur une facture historique dont la demande originale est inconnue, renvoie **409**. Retrouver alors la facture et vérifier la commande avant toute action. Enregistrer le `document_uid` puis appeler `/issue` sur cet identifiant pour obtenir le numéro définitif. Un aperçu de brouillon porte une référence provisoire.

Pour deux avancements légitimes de même montant, attribuer un identifiant différent à chaque étape. Le solde d’un devis accepté et l’émission d’une facture disposent également d’une protection par identifiant de document.

Il n’existe pas de quota applicatif par clé dans cette version. Éviter les rafales et les extractions complètes fréquentes ; prévoir les limites adaptées dans le reverse proxy avant un usage public soutenu. La pagination, les permissions fines, l’idempotence générale et une spécification OpenAPI ne sont pas encore livrées.

## 5. Configurer un webhook sortant

1. Dans la bonne entreprise, ouvrir **API & intégrations → Webhooks sortants**.
2. Saisir un nom, une URL **HTTPS** et les événements à recevoir.
3. Copier le secret affiché à la création dans le gestionnaire de secrets du destinataire.
4. Utiliser le test, qui émet `webhook.test` sans données métier ; contrôler la réception et la signature.
5. Consulter les livraisons. L’interface permet pause/réactivation, renouvellement du secret et relance des échecs définitifs. Avant rotation, préparer le destinataire pour la nouvelle valeur.

Les destinations privées ou réservées et les redirections sont refusées par défaut. Pour un service interne, seul l’exploitant serveur peut autoriser un hôte précis via `OUTBOUND_ALLOWED_HOSTS` ; le certificat TLS reste contrôlé. La connexion utilise une adresse DNS validée.

| Événement | Déclencheur |
| --- | --- |
| `customer.created`, `customer.updated` | Création ou modification d’une fiche client |
| `document.created`, `document.updated`, `document.status_changed` | Création, modification ou transition de document |
| `stock.movement` | Mouvement de stock explicite |
| `crm.opportunity.created`, `crm.opportunity.updated` | Opportunité créée ou modifiée |
| `crm.activity.created` | Activité CRM créée |
| `email.sent` | Envoi accepté par la messagerie |
| `webhook.test` | Test volontaire de l’endpoint |

L’abonnement ne renvoie pas l’historique antérieur. Il n’existe pas d’événement universel couvrant chaque changement de chaque table. Le détail de `data` varie selon l’événement ; examiner un exemple fictif du parcours concerné avant de brancher une automatisation métier.

## 6. Signature, corps brut et dédoublonnage

Chaque POST comporte un JSON UTF-8 et ces en-têtes :

```http
Content-Type: application/json
X-ZeFacture-Id: IDENTIFIANT_EVENEMENT
X-ZeFacture-Event: customer.created
X-ZeFacture-Signature: t=HORODATAGE_UNIX,v1=SIGNATURE_HEXADECIMALE
```

Enveloppe du corps :

```json
{
  "id": "IDENTIFIANT_EVENEMENT",
  "type": "customer.created",
  "created_at": "2026-11-06T10:00:00+00:00",
  "company_uid": "IDENTIFIANT_ENTREPRISE",
  "data": {}
}
```

Calcul : `HMAC-SHA256(secret, octets(timestamp) + b"." + corps_HTTP_brut)`, représenté en hexadécimal. Ne pas reformater ni sérialiser à nouveau le JSON avant vérification. Comparer en temps constant, vérifier une fenêtre d’horodatage — cinq minutes dans l’exemple — puis vérifier l’entreprise attendue et la cohérence des en-têtes.

Exemple Python indépendant du framework du récepteur :

```python
import hashlib
import hmac
import json
import time


def verify_webhook(raw, signature, event_id, event_type, secret, company_uid):
    try:
        parts = dict(item.split("=", 1) for item in signature.split(","))
        timestamp = parts["t"]
        received = parts["v1"]
        if abs(time.time() - int(timestamp)) > 300:
            raise ValueError("Horodatage expiré")
        expected = hmac.new(
            secret.encode("utf-8"),
            timestamp.encode("ascii") + b"." + raw,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(received, expected):
            raise ValueError("Signature invalide")
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or payload.get("id") != event_id
            or payload.get("type") != event_type
            or payload.get("company_uid") != company_uid
        ):
            raise ValueError("Événement inattendu")
        return payload
    except (KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise ValueError("Webhook refusé") from exc
```

`raw` est le corps en octets fourni par le serveur HTTP, avant décodage. Le secret et l’entreprise attendue proviennent de la configuration privée du destinataire, jamais de valeurs non vérifiées du corps. Le serveur récepteur doit aussi limiter la taille des requêtes et les événements qu’il traite.

Après vérification, enregistrer l’événement dans une boîte de réception persistante avec une contrainte d’unicité sur `(company_uid, id)`, puis répondre 2xx **après** cet enregistrement. Un événement déjà accepté peut recevoir 2xx sans réexécuter ses effets. Traiter les opérations métier depuis cette file, avec leurs propres garanties transactionnelles. Le code de vérification seul n’assure ni persistance ni dédoublonnage.

## 7. Tentatives, erreurs et exploitation

Le worker fait jusqu’à six tentatives avec délai croissant. Un timeout peut survenir après l’acceptation par le destinataire : la livraison est donc susceptible d’être répétée. L’identifiant d’événement reste identique ; l’horodatage et la signature sont recalculés à chaque tentative. L’ordre de réception n’est pas garanti, notamment après une reprise.

Répondre 2xx pour une acceptation durable ; les réponses non 2xx et erreurs réseau sont réessayées. Pour une signature invalide, refuser la requête et vérifier la configuration privée. Les 100 dernières livraisons sont consultables dans l’interface, avec statut et diagnostic expurgé des secrets. Après six échecs, corriger la destination puis relancer explicitement depuis cet écran.

`WEBHOOK_DELIVERY_DISABLED=1` ou `EXTERNAL_SERVICES_DISABLED=1` empêche les sorties. Une restauration active ces verrous et garde le worker arrêté pour ne pas rejouer les files avant validation. Ces paramètres relèvent de l’exploitant ; voir le [guide d’auto-hébergement](GUIDE_AUTOHEBERGEMENT.md).

## 8. Recette d’une intégration

- Créer une clé distincte sur une entreprise de test et vérifier que la révocation produit un refus 401.
- Créer un client et un document fictifs ; conserver leurs identifiants et contrôler les totaux, statuts et originaux dans l’interface.
- Recevoir `webhook.test`, puis un événement métier abonné ; vérifier la signature sur les octets réellement reçus.
- Représenter deux fois le même événement, un corps modifié et un horodatage ancien : aucun double effet ni acceptation du corps altéré/ancien ne doit se produire chez le destinataire.
- Simuler une indisponibilité du récepteur et vérifier les reprises puis la relance depuis ZeFacture.
- Valider les destinataires et les files avant activation sur des données réelles.

Ces essais utilisent des données fictives. Les tests automatiques du projet couvrent l’isolation API, les exports, les signatures, les reprises et les protections réseau ; ils ne valident pas le service récepteur propre à chaque intégration.

## 9. Consulter ZeFacture depuis Codex

Le connecteur tourne sur le poste qui exécute Codex. Il appelle l’instance ZeFacture via HTTPS ou, si elle tourne sur le même poste, via `http://127.0.0.1:8090`. Codex reçoit les résultats des consultations et peut les utiliser dans la conversation.

1. Depuis l’entreprise à consulter, créer une clé **Codex · lecture seule** dans **API & intégrations**.
2. Sur le poste qui exécute Codex, créer un environnement Python 3.13 ou 3.14 et installer le connecteur depuis le dossier `django/` des sources.

   ```sh
   python3 -m venv .venv-mcp
   .venv-mcp/bin/python -m pip install -r tools/mcp-requirements.txt
   ```

3. Enregistrer la clé sur une ligne dans un fichier privé, en dehors du dépôt, par exemple `~/.config/zefacture/codex.key`. Sur macOS et Linux, utiliser `chmod 600` sur ce fichier. Le connecteur peut aussi lire `ZEFACTURE_MCP_API_KEY` depuis son environnement.
4. Dans le `config.toml` de Codex, ajouter ce bloc avec les chemins absolus du poste et l’origine de l’instance. Ouvrir une nouvelle session pour charger la configuration.

   ```toml
   [mcp_servers.zefacture]
   command = "/chemin/absolu/django/.venv-mcp/bin/python"
   args = [
     "/chemin/absolu/django/tools/zefacture_mcp.py",
     "--url", "http://127.0.0.1:8090",
     "--key-file", "/chemin/prive/zefacture/codex.key",
   ]
   ```

   Sur Windows, utiliser le chemin de `python.exe` dans `.venv-mcp/Scripts/`. La [documentation OpenAI sur MCP](https://developers.openai.com/codex/mcp) indique aussi comment ajouter le serveur depuis Codex.

Exemples de questions : « Compare mes ventes HT d’octobre et de septembre », « Quelles factures fournisseurs ai-je reçues ce mois-ci ? », « Quels justificatifs ont été enregistrés en novembre et pour quels montants ? », « Montre-moi les champs proposés pour ce ticket ». Les montants restent séparés par devise.

Les outils MCP sont `synthese_facturation`, `lister_factures`, `lister_justificatifs` et `consulter_justificatif`. Ils consultent les données et ne déposent, ne valident et n’envoient aucun document.

### Utiliser l’API de consultation sans MCP

Envoyer une clé **Codex · lecture seule** avec `Authorization: Bearer …`. Les chemins ci-dessous sont relatifs à l’origine de l’instance.

| Route GET | Paramètres | Résultat |
| --- | --- | --- |
| `/api/v1/assistant/summary` | `date_from`, `date_to`, `compare` (`none`, `previous`, `year`) | Ventes et achats HT par devise, hors brouillons et avec avoirs déduits |
| `/api/v1/assistant/invoices` | `date_from`, `date_to`, `direction` (`incoming`, `outgoing`), `limit`, `cursor` | Factures, avancements et avoirs. Dates et statuts de l’instance |
| `/api/v1/assistant/receipts` | `date_from`, `date_to`, `limit`, `cursor` | Tickets et notes de frais, champs enregistrés et états des traitements |
| `/api/v1/assistant/receipts/{id}` | Identifiant retourné par la liste | Champs enregistrés et, quand disponibles, suggestions à relire |

Les dates suivent le format `AAAA-MM-JJ`. Sans dates, la synthèse couvre le début de l’année jusqu’à aujourd’hui et les listes prennent les entrées les plus récentes. Les listes renvoient `next_cursor` lorsque d’autres entrées correspondent à la recherche. Redonner ce curseur et les mêmes filtres pour la page suivante. Le maximum est de 100 entrées par appel. Les montants sont renvoyés sous forme de chaînes décimales.

Une clé inconnue ou révoquée produit `401`. Une clé du mauvais type produit `403`. Une écriture sur ces routes produit `405`. Le dépôt et l’analyse des justificatifs se font dans l’interface. Les webhooks sortants restent disponibles pour notifier les événements métier documentés plus haut.
