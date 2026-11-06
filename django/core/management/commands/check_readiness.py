"""Read-only installation diagnostics. Never prints credentials or sends requests."""

import json
import os
import stat
from urllib.parse import urlsplit
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from core.models import CompanyMember, UserSecurity


class Command(BaseCommand):
    help = "Vérifier la préparation d’une instance, sans appel externe ni affichage de secrets."

    def add_arguments(self, parser):
        parser.add_argument("--production", action="store_true")
        parser.add_argument("--require-oauth", action="store_true")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        checks = []

        def check(name, passed, detail, blocking=True):
            checks.append(
                {
                    "check": name,
                    "status": "ok" if passed else "block" if blocking else "warning",
                    "detail": detail,
                }
            )

        production = options["production"]
        origin = urlsplit(settings.APP_URL)
        check(
            "environment",
            not production or settings.ENVIRONMENT == "production",
            "APP_ENV=production requis pour le serveur public.",
        )
        check("debug", not settings.DEBUG, "DEBUG doit rester désactivé.")
        check(
            "https_origin",
            origin.scheme == "https"
            and bool(origin.hostname)
            and not origin.path
            and not origin.query
            and not origin.fragment,
            "APP_URL doit être une origine HTTPS, sans chemin ni paramètre.",
            production,
        )
        check(
            "allowed_hosts",
            origin.hostname in settings.ALLOWED_HOSTS
            and "*" not in settings.ALLOWED_HOSTS,
            "Le domaine public doit figurer explicitement dans ALLOWED_HOSTS.",
            production,
        )
        check(
            "database",
            connection.vendor == "postgresql",
            "PostgreSQL requis pour les écritures concurrentes de production.",
            production,
        )
        executor = MigrationExecutor(connection)
        migrations_ready = not executor.migration_plan(
            executor.loader.graph.leaf_nodes()
        )
        check(
            "migrations",
            migrations_ready,
            "Toutes les migrations doivent être appliquées.",
        )
        key = settings.DATA_DIR / "app.key"
        check(
            "encryption_key",
            key.is_file() and not stat.S_IMODE(key.stat().st_mode) & 0o077,
            "app.key doit être présente et privée (0600), et sauvegardée avec les données.",
        )
        check(
            "proxy",
            settings.TRUST_PROXY and len(settings.PROXY_SHARED_SECRET) >= 32,
            "Le proxy HTTPS doit partager une clé privée avec Django et fixer l’adresse cliente.",
            production,
        )
        check(
            "secure_cookies",
            getattr(settings, "SESSION_COOKIE_SECURE", False)
            and getattr(settings, "CSRF_COOKIE_SECURE", False),
            "Cookies Secure requis en production.",
            production,
        )
        check(
            "ssl_redirect",
            getattr(settings, "SECURE_SSL_REDIRECT", False),
            "La redirection HTTPS doit être active.",
            production,
        )
        check(
            "admin_mfa_policy",
            settings.MFA_REQUIRED_FOR_ADMINS,
            "La double authentification doit être obligatoire pour les administrateurs.",
            production,
        )
        check(
            "recent_auth_policy",
            settings.SENSITIVE_REAUTH_REQUIRED,
            "Les réglages sensibles doivent demander une vérification récente.",
            production,
        )
        if migrations_ready:
            admins = (
                CompanyMember.objects.filter(
                    status="active", role__in=["owner", "admin"]
                )
                .values_list("user_uid", flat=True)
                .distinct()
            )
            secured = UserSecurity.objects.filter(enabled_at__isnull=False).values_list(
                "user_uid", flat=True
            )
            unsecured = admins.exclude(user_uid__in=secured).count()
            check(
                "admin_mfa_enrollment",
                unsecured == 0,
                f"Administrateurs restant à équiper : {unsecured}.",
                production,
            )
        contact = settings.INSTANCE_CONTACT.strip()
        legal = settings.INSTANCE_LEGAL.strip()
        check(
            "operator_contact",
            "@" in contact
            and "exemple" not in contact.lower()
            and "example" not in contact.lower(),
            "Renseigner un vrai contact de l’exploitant dans INSTANCE_CONTACT.",
            production,
        )
        check(
            "operator_identity",
            len(legal) > 30
            and not any(
                marker in legal.casefold()
                for marker in ("renseigne", "avant ouverture", "avant toute ouverture")
            ),
            "Renseigner l’identité réelle de l’exploitant dans INSTANCE_LEGAL.",
            production,
        )
        server_mail = settings.MAIL_TRANSPORT != "outbox"
        check(
            "account_email",
            server_mail,
            "Le transport du serveur doit permettre les emails de réinitialisation et être testé avec une adresse contrôlée.",
            False,
        )
        if settings.MAIL_TRANSPORT == "smtp":
            check(
                "server_smtp_tls",
                settings.EMAIL_USE_TLS != settings.EMAIL_USE_SSL
                and bool(settings.EMAIL_HOST_USER and settings.EMAIL_HOST_PASSWORD),
                "SMTP serveur : un mode TLS et des identifiants sont requis.",
                production,
            )
        for provider, prefix in (
            ("google", "GOOGLE_MAIL"),
            ("microsoft", "MICROSOFT_MAIL"),
        ):
            configured = bool(
                os.getenv(prefix + "_CLIENT_ID")
                and os.getenv(prefix + "_CLIENT_SECRET")
            )
            check(
                provider + "_oauth_app",
                configured,
                "Paire Client ID / secret serveur requise pour proposer ce fournisseur à toutes les entreprises.",
                options["require_oauth"],
            )
        check(
            "external_services",
            not settings.EXTERNAL_SERVICES_DISABLED,
            "Les services externes sont verrouillés ; conserver ce verrou sur une reprise de validation.",
            False,
        )
        check(
            "email_delivery",
            not settings.EMAIL_DELIVERY_DISABLED,
            "Livraison email verrouillée ; activer seulement après contrôle des files et tests autorisés.",
            False,
        )
        check(
            "webhook_delivery",
            not settings.WEBHOOK_DELIVERY_DISABLED,
            "Livraisons webhook verrouillées ; activer seulement après contrôle des endpoints et files.",
            False,
        )
        checks.append(
            {
                "check": "public_validation",
                "status": "manual",
                "detail": "Vérifier de l’extérieur DNS/TLS, consentements réels Google/Microsoft, réception email, sauvegarde hors serveur et restauration. Aucun de ces points n’est certifié par cette commande.",
            }
        )
        result = {
            "ready": not any(c["status"] == "block" for c in checks),
            "checks": checks,
        }
        if options["json"]:
            self.stdout.write(json.dumps(result, ensure_ascii=False))
        else:
            for item in checks:
                self.stdout.write(
                    f"{item['status'].upper():7} {item['check']}: {item['detail']}"
                )
        if not result["ready"]:
            raise CommandError(
                "Préparation incomplète : corriger les contrôles bloquants."
            )
