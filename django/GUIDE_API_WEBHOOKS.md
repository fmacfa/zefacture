# ZeFacture — API et webhooks

Contrat de l’édition Django, vérifié dans les routes et services le 6 novembre 2026. Les exemples sont fictifs. `https://zefacture.com` est l’adresse de production prévue ; elle n’est pas encore publiée. Remplacer cette origine par celle de l’instance utilisée pour les essais.

## 1. Choisir le sens de l’échange

| Besoin | Fonction disponible |
| --- | --- |
| Lire/créer des clients, devis ou factures depuis un autre outil | API HTTP `/api/v1` avec clé Bearer |
| Accepter un devis, le facturer, créer un avancement ou générer Factur-X | Actions POST documentées ci-dessous |
| Être averti d’une modification dans ZeFacture | Webhooks sortants configurables par entreprise |
| Récupérer stocks, CRM ou finances pour analyse | Exports CSV/Excel/PDF depuis **Rapports** |
| Synchroniser les factures et statuts SUPERPDP | Synchronisation officielle configurée par entreprise ; récepteur `/webhooks/superpdp` facultatif pour un relais autorisé |

Il n’existe pas de récepteur de webhook générique permettant de modifier toutes les données. L’API publique n’expose pas encore de CRUD des stocks, contacts CRM, opportunités, membres, comptes bancaires ou paramètres email. Ne pas utiliser les routes de formulaires `/app/` comme contrat d’API.

L’intégration SUPERPDP et la commande de synchronisation sont décrites dans le [guide dédié](GUIDE_SUPERPDP.md). Les clés `/api/v1` ne sont pas des jetons d’accès au compte SUPERPDP.

## 2. Créer et protéger une clé

Un propriétaire ou administrateur ouvre **API & intégrations**, nomme une clé pour l’outil destinataire et copie la valeur affichée une seule fois. La clé appartient à l’entreprise sélectionnée et peut être révoquée depuis cette même page. Son empreinte seule est conservée pour l’authentification.

Transmettre la clé uniquement dans l’en-tête :

```http
Authorization: Bearer zf_live_VOTRE_CLE
Content-Type: application/json
```

Conserver la valeur dans le gestionnaire de secrets du système appelant. Une clé n’est pas liée au rôle d’un membre et n’a pas de permissions fines par ressource, d’expiration automatique ou de second facteur par appel. Son détenteur peut effectuer l’ensemble des opérations API disponibles dans cette entreprise. Créer une clé par intégration et révoquer les anciennes après rotation.

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
| POST | `/api/v1/invoices` | Crée une facture brouillon, réponse 201 |
| POST | `/api/v1/quotes/{uid}/accept` | Accepte le devis si son état et ses données le permettent, réponse 200 |
| POST | `/api/v1/quotes/{uid}/invoice` | Crée ou retrouve la facture de solde du devis accepté, réponse 201 |
| POST | `/api/v1/quotes/{uid}/progress-invoices` | Crée une facture d’avancement du devis accepté, réponse 201 |
| POST | `/api/v1/invoices/{uid}/facturx` | Génère ou vérifie le fichier Factur-X, réponse 200 |

Les listes ne sont pas paginées ni filtrées par période par l’API actuelle. Les paramètres de filtres de l’interface ne s’y appliquent pas. Les GET individuels, PUT, PATCH et DELETE ne constituent pas des opérations disponibles. Les métadonnées des fichiers ne contiennent pas leur PDF binaire ni une URL publique de téléchargement.

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

Pour générer Factur-X à la création d’une facture, ajouter `"generate_facturx": true`. Les coordonnées nécessaires à la validation doivent être complètes. Un échec de cette génération annule la création dans la transaction. Un fichier généré ne signifie pas que la facture est envoyée ou que son statut devient `sent` : l’API actuelle n’expose pas l’envoi d’email, l’émission PDP ou le règlement.

Pour accepter un devis ou créer le solde, envoyer `{}` à l’action correspondante. Pour un avancement :

```json
{"percent": "30"}
```

Le devis doit être accepté et le pourcentage rester dans le solde disponible. Les avancements antérieurs sont déduits ; un solde déjà créé est retrouvé par l’action `/invoice`. La génération Factur-X renvoie les métadonnées du document ; télécharger les fichiers depuis l’interface authentifiée.

### Reprises et volume

Il n’existe pas de mécanisme général `Idempotency-Key` pour les POST de création : rejouer la création d’un client ou d’une facture peut créer un doublon. Conserver le `customer_uid`/`document_uid` dans l’outil appelant et rapprocher une référence externe après une réponse incertaine. `reference` n’est pas une contrainte d’unicité.

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
