import os
from pathlib import Path
from urllib.parse import urlparse, unquote

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(os.getenv("ZEFACTURE_ENV_FILE", BASE_DIR / ".env"), override=False)
ENVIRONMENT = os.getenv("APP_ENV", "local")
DEBUG = os.getenv("DEBUG", "0") == "1"
SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY", "local-development-only-zefacture-change-before-production"
)
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR.parent / "storage/django")).resolve()
APP_URL = os.getenv("APP_URL", "http://127.0.0.1:8090").rstrip("/")
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
CSRF_TRUSTED_ORIGINS = [
    x for x in os.getenv("CSRF_TRUSTED_ORIGINS", APP_URL).split(",") if x
]
REGISTRATION_OPEN = os.getenv("REGISTRATION_OPEN", "0") == "1"
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core.apps.CoreConfig",
]
MIDDLEWARE = [
    "core.middleware.ProxyBoundaryMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "core.security.LegacyCsrfFieldMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.security.SessionVersionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.security.navigation",
            ]
        },
    }
]
database_url = os.getenv("DATABASE_URL", "")
if database_url:
    parsed = urlparse(database_url)
    if parsed.scheme not in ("postgres", "postgresql"):
        raise RuntimeError("DATABASE_URL doit utiliser PostgreSQL")
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "",
            "PORT": parsed.port or 5432,
            "CONN_MAX_AGE": 60,
        }
    }
else:
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": DATA_DIR / "db.sqlite3",
            "OPTIONS": {"timeout": 20},
        }
    }
AUTH_USER_MODEL = "core.User"
AUTHENTICATION_BACKENDS = ["core.security.LegacyPasswordBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
LOGIN_URL = "/connexion"
LOGIN_REDIRECT_URL = "/app"
LANGUAGE_CODE = "fr-fr"
TIME_ZONE = os.getenv("APP_TIMEZONE", "Europe/Paris")
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
APPEND_SLASH = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 86400
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
CSRF_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
TRUST_PROXY = os.getenv("TRUST_PROXY", "0") == "1"
PROXY_SHARED_SECRET = os.getenv("PROXY_SHARED_SECRET", "")
MFA_REQUIRED_FOR_ADMINS = (
    os.getenv("MFA_REQUIRED_FOR_ADMINS", "1" if ENVIRONMENT == "production" else "0")
    == "1"
)
SENSITIVE_REAUTH_REQUIRED = (
    os.getenv("SENSITIVE_REAUTH_REQUIRED", "1" if ENVIRONMENT == "production" else "0")
    == "1"
)
EXTERNAL_SERVICES_DISABLED = os.getenv("EXTERNAL_SERVICES_DISABLED", "0") == "1"
SUPERPDP_SYNC_INTERVAL = max(60, min(86400, int(os.getenv("SUPERPDP_SYNC_INTERVAL", "300"))))
SUPERPDP_MAX_ORIGINAL_BYTES = 25 * 1024 * 1024
SUPERPDP_MAX_SYNC_PAGES = 10
DATA_UPLOAD_MAX_MEMORY_SIZE = 16 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
MAIL_TRANSPORT = os.getenv("MAIL_TRANSPORT", "outbox")
EMAIL_DELIVERY_DISABLED = os.getenv("EMAIL_DELIVERY_DISABLED", "0") == "1"
WEBHOOK_DELIVERY_DISABLED = os.getenv("WEBHOOK_DELIVERY_DISABLED", "0") == "1"
OUTBOUND_ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("OUTBOUND_ALLOWED_HOSTS", "").split(",")
    if host.strip()
]
EMAIL_HOST = os.getenv("SMTP_HOST", "localhost")
EMAIL_PORT = int(os.getenv("SMTP_PORT", "587"))
EMAIL_HOST_USER = os.getenv("SMTP_USERNAME", "")
EMAIL_HOST_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("SMTP_TLS", "1") == "1"
EMAIL_USE_SSL = os.getenv("SMTP_SSL", "0") == "1"
EMAIL_TIMEOUT = 15
DEFAULT_FROM_EMAIL = os.getenv("MAIL_FROM_EMAIL", "zefacture@localhost")
MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "ZeFacture")
INSTANCE_CONTACT = os.getenv(
    "INSTANCE_CONTACT", "Contactez l'administrateur de cette instance."
)
INSTANCE_LEGAL = os.getenv(
    "INSTANCE_LEGAL",
    "Instance privée auto-hébergée. L'exploitant renseigne ici son identité et ses coordonnées avant toute ouverture publique.",
)
INSTANCE_PRIVACY_NOTES = os.getenv("INSTANCE_PRIVACY_NOTES", "")
if ENVIRONMENT == "production":
    if (
        SECRET_KEY.startswith(("local-", "replace_"))
        or len(SECRET_KEY) < 50
        or not database_url
        or DEBUG
    ):
        raise RuntimeError(
            "Configurer une clé Django privée et PostgreSQL avant de lancer en production"
        )
    parsed_app = urlparse(APP_URL)
    if (
        parsed_app.scheme != "https"
        or not parsed_app.hostname
        or parsed_app.path
        or parsed_app.username
        or parsed_app.password
        or parsed_app.query
        or parsed_app.fragment
        or "*" in ALLOWED_HOSTS
        or parsed_app.hostname not in ALLOWED_HOSTS
    ):
        raise RuntimeError(
            "APP_URL doit être une origine HTTPS présente dans ALLOWED_HOSTS, sans chemin ni wildcard."
        )
    if TRUST_PROXY and len(PROXY_SHARED_SECRET) < 32:
        raise RuntimeError(
            "Définir PROXY_SHARED_SECRET (32 caractères minimum) pour le proxy de production."
        )
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "1") == "1"
    SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = (
        os.getenv("SECURE_HSTS_INCLUDE_SUBDOMAINS", "0") == "1"
    )
    SECURE_HSTS_PRELOAD = os.getenv("SECURE_HSTS_PRELOAD", "0") == "1"
if TRUST_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
}
