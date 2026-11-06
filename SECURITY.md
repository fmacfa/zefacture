# Sécurité

## Versions et niveau de validation

La version `0.1.0-rc.1` est une préversion en cours de validation. Les corrections sont préparées sur la branche principale et dans les candidates suivantes. Aucun engagement de maintenance longue durée n’est annoncé pour cette première candidate.

Les tests automatisés et audits de dépendances ne constituent pas une garantie d’absence de vulnérabilité. Les vérifications opérationnelles des comptes fournisseurs, de l’hébergement et de la restauration restent nécessaires avant l’usage réel.

## Signaler une vulnérabilité en privé

Utiliser [Report a vulnerability](https://github.com/fmacfa/zefacture/security/advisories/new), dans l’onglet **Security** du dépôt. Pour un problème propre à une instance hébergée par un tiers, contacter aussi son exploitant via son canal privé.

Indiquer la version ou le commit, les prérequis, les étapes de reproduction, l’impact attendu et un exemple entièrement fictif. Ne pas transmettre de données client, mot de passe, jeton OAuth, clé API, code MFA ou sauvegarde. Conserver les détails sensibles hors des tickets et pull requests publics.

## Propriétés à préserver

Les changements doivent préserver l’isolation des entreprises, les contrôles de rôle, les originaux émis et leurs empreintes, les arrondis monétaires, le chiffrement des secrets et la traçabilité. Les statuts fournisseurs ne doivent pas écraser les montants et rapprochements locaux. Les transmissions incertaines ne doivent pas être répétées automatiquement.

Les protections d’exploitation sont décrites dans la [documentation de sécurité](django/SECURITY.md) et le [guide d’auto-hébergement](django/GUIDE_AUTOHEBERGEMENT.md). Ne jamais publier les configurations privées, bases, documents ou archives de sauvegarde avec les sources.
