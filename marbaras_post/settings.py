"""
Django settings for the Marbaras Post platform (a1post.bg-style courier site).

Standalone project — runs out of the box on SQLite with no external services.
Brand, contact details and DHL/DPI credentials are all env-overridable.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path):
    """Tiny .env loader (no dependency). KEY=VALUE per line; # comments."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def env(key, default=""):
    return os.environ.get(key, default)


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-insecure-change-me-in-production")

# On a hosting platform (Railway/Render) default DEBUG to False for safety, so a
# forgotten env var can't expose the debug page. Local stays True.
_ON_HOST = bool(
    env("RAILWAY_PUBLIC_DOMAIN", "") or env("RAILWAY_ENVIRONMENT", "")
    or env("RENDER_EXTERNAL_HOSTNAME", "")
)
DEBUG = env("DJANGO_DEBUG", "False" if _ON_HOST else "True").lower() in (
    "1", "true", "yes", "on",
)
ALLOWED_HOSTS = [h for h in env("DJANGO_ALLOWED_HOSTS", "*").split(",") if h] or ["*"]
CSRF_TRUSTED_ORIGINS = [
    o for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o
]
# Trust the platform's external hostname at runtime so logins/forms work over
# HTTPS without extra config (Render + Railway).
for _host_env in ("RENDER_EXTERNAL_HOSTNAME", "RAILWAY_PUBLIC_DOMAIN"):
    _h = env(_host_env, "")
    if _h:
        ALLOWED_HOSTS.append(_h)
        CSRF_TRUSTED_ORIGINS.append(f"https://{_h}")
CSRF_TRUSTED_ORIGINS.append("https://*.onrender.com")
CSRF_TRUSTED_ORIGINS.append("https://*.up.railway.app")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "marbaras_post.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.brand_and_i18n",
            ],
        },
    },
]

WSGI_APPLICATION = "marbaras_post.wsgi.application"

# Use a persistent Postgres database when DATABASE_URL is set (Railway/Render
# provide it once you add a Postgres service) so data survives redeploys.
# Falls back to local SQLite for development.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
_database_url = env("DATABASE_URL", "")
if _database_url:
    import dj_database_url

    DATABASES["default"] = dj_database_url.parse(
        _database_url, conn_max_age=600, ssl_require=False
    )

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "bg"
TIME_ZONE = "Europe/Sofia"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/app/"
LOGOUT_REDIRECT_URL = "/"

# ---------------------------------------------------------------------------
# Brand & contact (rename the whole site from here)
# ---------------------------------------------------------------------------
BRAND = {
    "name": env("BRAND_NAME", "Marbaras Post"),
    "short": env("BRAND_SHORT", "MPost"),
    "phone": env("BRAND_PHONE", "00359877888766"),
    "email": env("BRAND_EMAIL", "office@marbaras.com"),
    "company": env("BRAND_COMPANY", "STILKOLOR OOD"),
    "address": env("BRAND_ADDRESS", "bul. Vladislav Varnenchik 281, Varna"),
    "hours": env("BRAND_HOURS", "Mon–Fri, 08:30–17:30"),
}

# ---------------------------------------------------------------------------
# DHL / DPI Global Mail (same credentials model as the marbaras shop)
# ---------------------------------------------------------------------------
GLOBAL_MAIL_API_KEY = env("GLOBAL_MAIL_API_KEY", "")
GLOBAL_MAIL_API_SECRET = env("GLOBAL_MAIL_API_SECRET", "")
GLOBAL_MAIL_CUSTOMER_EKP = env("GLOBAL_MAIL_CUSTOMER_EKP", "")
GLOBAL_MAIL_TEST_MODE = env("GLOBAL_MAIL_TEST_MODE", "True").lower() in (
    "1", "true", "yes", "on",
)
GLOBAL_MAIL_PRODUCT_CODE = env("GLOBAL_MAIL_PRODUCT_CODE", "GPT")
# True when your DPI contract has no GPP product (so every destination uses
# GLOBAL_MAIL_PRODUCT_CODE / GPT and combines onto one AWB).
GLOBAL_MAIL_DISABLE_BUILTIN_PRODUCT_MAP = env(
    "GLOBAL_MAIL_DISABLE_BUILTIN_PRODUCT_MAP", "False"
)
GLOBAL_MAIL_HS_CODE = env("GLOBAL_MAIL_HS_CODE", "711311")
GLOBAL_MAIL_FALLBACK_EMAIL = env("GLOBAL_MAIL_FALLBACK_EMAIL", "")
SHOP_COUNTRY = env("SHOP_COUNTRY", "BG")
