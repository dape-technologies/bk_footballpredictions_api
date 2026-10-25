import os
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(os.getenv("DJANGO_ENV_FILE", BASE_DIR / ".env"))


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [value.strip() for value in os.getenv(name, default).split(",") if value.strip()]


DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-development-only-change-before-production")
if not DEBUG and SECRET_KEY.startswith("django-insecure-"):
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is false.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "api",
    "accounts",
    "catalog",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.PrivilegedSessionTimeoutMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

if os.getenv("DB_NAME"):
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.environ["DB_NAME"],
        "USER": os.environ["DB_USER"],
        "PASSWORD": os.environ["DB_PASSWORD"],
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "3306"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
            "isolation_level": "read committed",
        },
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 4}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Kampala"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
PRIVATE_MEDIA_ROOT = Path(os.getenv("PRIVATE_MEDIA_ROOT", BASE_DIR / "private_media"))
FRONTEND_DIST_DIR = Path(os.getenv("FRONTEND_DIST_DIR", BASE_DIR / "frontend_dist"))
WHITENOISE_ROOT = FRONTEND_DIST_DIR
SERVE_MEDIA_FILES = env_bool("DJANGO_SERVE_MEDIA", False)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["accounts.backends.PhoneOrAdminAliasBackend"]

ADMIN_LOGIN_USERNAME = os.getenv("ADMIN_LOGIN_USERNAME", "").strip()
ADMIN_SEED_PHONE = os.getenv("ADMIN_SEED_PHONE", "").strip()
ADMIN_SEED_PASSWORD = os.getenv("ADMIN_SEED_PASSWORD", "")

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", not DEBUG)
CSRF_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", not DEBUG)
CSRF_TRUSTED_ORIGINS = env_list(
    "CSRF_TRUSTED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", False)
SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_SECURE_HSTS_PRELOAD", False)
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "accounts.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "DEFAULT_RENDERER_CLASSES": (["rest_framework.renderers.JSONRenderer"] if not DEBUG else [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ]),
}

RELWORX_API_KEY = os.getenv("RELWORX_API_KEY", "")
RELWORX_ACCOUNT_NO = os.getenv("RELWORX_ACCOUNT_NO", "")
RELWORX_WEBHOOK_SIGNING_KEY = os.getenv("RELWORX_WEBHOOK_SIGNING_KEY", "")
RELWORX_API_BASE_URL = os.getenv("RELWORX_API_BASE_URL", "https://payments.relworx.com")
RELWORX_WEBHOOK_URL = os.getenv(
    "RELWORX_WEBHOOK_URL",
    "http://localhost:8000/api/payments/webhooks/relworx",
)
RELWORX_WEBHOOK_TOLERANCE_SECONDS = int(os.getenv("RELWORX_WEBHOOK_TOLERANCE_SECONDS", "300"))
RELWORX_REQUEST_TIMEOUT_SECONDS = int(os.getenv("RELWORX_REQUEST_TIMEOUT_SECONDS", "15"))
RELWORX_COLLECTION_FEE_RATE = Decimal(os.getenv("RELWORX_COLLECTION_FEE_RATE", "0.03"))
PAYMENT_PENDING_EXPIRY_MINUTES = int(os.getenv("PAYMENT_PENDING_EXPIRY_MINUTES", "15"))
RELWORX_PAYOUTS_ENABLED = env_bool("RELWORX_PAYOUTS_ENABLED", False)
OWNER_SECURITY_EMAIL = os.getenv("OWNER_SECURITY_EMAIL", "makubuyahannington93@gmail.com")
DEVELOPER_SECURITY_EMAIL = os.getenv("DEVELOPER_SECURITY_EMAIL", "dapetech.2@gmail.com")
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "25"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", False)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = 15
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "security@bkfootballpredictions.com")
RELWORX_PAYOUT_MIN_AMOUNT = Decimal(os.getenv("RELWORX_PAYOUT_MIN_AMOUNT", "500"))
RELWORX_PAYOUT_MAX_AMOUNT = Decimal(os.getenv("RELWORX_PAYOUT_MAX_AMOUNT", "5000000"))
PAYOUT_EXECUTION_MODE = os.getenv("PAYOUT_EXECUTION_MODE", "request_only")
PAYMENT_OWNER_PAYOUT_MSISDN = os.getenv("PAYMENT_OWNER_PAYOUT_MSISDN", "")
PAYMENT_DEVELOPER_PAYOUT_MSISDN = os.getenv("PAYMENT_DEVELOPER_PAYOUT_MSISDN", "")
DEVELOPER_COMMISSION_RATE = Decimal(os.getenv("DEVELOPER_COMMISSION_RATE", "0.20"))
DEVELOPER_SEED_PHONE = os.getenv("DEVELOPER_SEED_PHONE", "")
DEVELOPER_SEED_PASSWORD = os.getenv("DEVELOPER_SEED_PASSWORD", "")
