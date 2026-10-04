import os
from pathlib import Path

import mongoengine
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def required_secret(name):
    """Read a signing secret from the environment, with no fallback of any kind.

    A default written here would be a signing key published to everyone who clones the
    repository, and a published signing key is the same as no key at all: anyone could
    mint a session for any account. So there is nothing to fall back to, and a missing
    value stops the process at import rather than quietly weakening every token the
    application goes on to issue. setup.sh generates a local value into backend/.env.
    """
    value = os.getenv(name, "").strip()

    if not value:
        raise RuntimeError(
            f"{name} is not set. Run `bash setup.sh` from the repository root, which "
            f"generates a local value into backend/.env."
        )

    return value


SECRET_KEY = required_secret("DJANGO_SECRET_KEY")
DEBUG = os.getenv("NODE_ENV", "development") == "development"
ALLOWED_HOSTS = ["*"]

JWT_SECRET = required_secret("JWT_SECRET")
JWT_EXPIRES_IN = os.getenv("JWT_EXPIRES_IN", "24h")
JWT_ISSUER = "wallet-api"
JWT_AUDIENCE = "wallet-app"

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/wallet_db")
mongoengine.connect(host=MONGODB_URI, uuidRepresentation="standard")

INSTALLED_APPS = [
    "corsheaders",
    "rest_framework",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]

CORS_ALLOW_ALL_ORIGINS = True

ROOT_URLCONF = "wallet_backend.urls"

DATABASES = {}

TEMPLATES = []

REST_FRAMEWORK = {
    "UNAUTHENTICATED_USER": None,
    "EXCEPTION_HANDLER": "apps.shared.errors.api_exception_handler",
    "DEFAULT_RENDERER_CLASSES": ["apps.shared.rendering.ApiRenderer"],
}

USE_TZ = True
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
