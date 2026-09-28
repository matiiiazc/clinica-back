"""
Configuración de clinica_back.

Los valores sensibles salen del .env (ver .env.example) y nunca se commitean.
Los helpers de env_* están en clinica_back/env.py.
"""

import os
from datetime import timedelta
from pathlib import Path

from clinica_back.env import (
    database_from_url,
    env_bool,
    env_int,
    env_list,
    env_str,
)

BASE_DIR = Path(__file__).resolve().parent.parent

# Clave usada para firmar los JWT y los cookies de sesión.
# OBLIGATORIA en producción: el manage.py aborta el arranque si falta.
SECRET_KEY = env_str("SECRET_KEY", default="")

DEBUG = env_bool("DEBUG", default=True)

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# Orígenes del frontend (Vite/Next) habilitados para CORS.
CORS_ALLOWED_ORIGINS = env_list("CORS_ORIGINS", default=["http://localhost:5173"])
CORS_ALLOW_CREDENTIALS = True

# Solo se registra si viene en CSRF_TRUSTED_ORIGINS (los mismos que CORS).
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", default=[])


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Terceros
    "rest_framework",
    "corsheaders",
    # Apps del proyecto
    "apps.accounts",
    "apps.clinica",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "clinica_back.urls"

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

WSGI_APPLICATION = "clinica_back.wsgi.application"
ASGI_APPLICATION = "clinica_back.asgi.application"


# Base de datos
# Por omisión PostgreSQL. Se acepta un DATABASE_URL (Neon, Railway, Render,
# Docker...) o las variables sueltas DB_*. DB_ENGINE=sqlite queda para pruebas
# rápidas; validate_settings() impide usarla con DEBUG=False.

DATABASE_URL = env_str("DATABASE_URL", default="")
DB_ENGINE = env_str("DB_ENGINE", default="postgresql").lower()

if DB_ENGINE == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / env_str("DB_NAME", default="db.sqlite3"),
        }
    }
elif DATABASE_URL:
    DATABASES = {
        "default": database_from_url(
            DATABASE_URL, conn_max_age=env_int("DB_CONN_MAX_AGE", default=60)
        )
    }
elif DB_ENGINE == "postgresql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env_str("DB_NAME", default="clinica_back"),
            "USER": env_str("DB_USER", default="postgres"),
            "PASSWORD": env_str("DB_PASSWORD", default="postgres"),
            "HOST": env_str("DB_HOST", default="127.0.0.1"),
            "PORT": env_str("DB_PORT", default="5432"),
            "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", default=60),
        }
    }
else:
    raise ValueError(f"DB_ENGINE={DB_ENGINE!r} no soportado. Usá postgresql o sqlite.")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Autenticación
# App propia: el usuario se identifica con email, no con username.
AUTH_USER_MODEL = "accounts.User"

# Argon2id como hasher principal, igual que el argon2 de los backends Node.
# memory_cost 19456 (~19 MB) y time_cost 2 son los valores recomendados por OWASP
# y los mismos que usaba hashPassword() en web_rrhh_back.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# El login es con email; DRF devuelve 401 en vez de redirigir a /login.
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.EmailBackend"]

# Ver .env: ACCESS_TOKEN_MINUTES (default 15) y REFRESH_TOKEN_DAYS (default 7)
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=env_int("ACCESS_TOKEN_MINUTES", default=15)
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env_int("REFRESH_TOKEN_DAYS", default=7)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": False,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": env_str("JWT_SECRET", default=""),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "TOKEN_TYPE_CLAIM": "token_type",
}

# Cookie HttpOnly donde se deja el refresh token, para que la SPA pueda
# refrescar sin guardar el token en localStorage. Mismo criterio que
# setTokenCookie() en los backends Node.
AUTH_COOKIE = env_str("AUTH_COOKIE_NAME", default="clinica_refresh")

# Duración del refresh cuando el login viene con remember_me=true.
REFRESH_TOKEN_DAYS_REMEMBER = env_int("REFRESH_TOKEN_DAYS_REMEMBER", default=30)

# Datos que se firman junto al user_id en el access token.
SIMPLE_JWT_TOKEN_ATTRIBUTES = ("email", "nombre", "rol", "email_verified")

# Espera mínima entre dos envíos del código de verificación, en segundos.
EMAIL_VERIFICATION_RESEND_SECONDS = env_int(
    "EMAIL_VERIFICATION_RESEND_SECONDS", default=60
)
# Minutos de validez del código de verificación por email.
EMAIL_VERIFICATION_CODE_TTL_MINUTES = env_int(
    "EMAIL_VERIFICATION_CODE_TTL_MINUTES", default=10
)
# Longitud del código numérico de verificación.
EMAIL_VERIFICATION_CODE_LENGTH = env_int("EMAIL_VERIFICATION_CODE_LENGTH", default=6)


REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
    "EXCEPTION_HANDLER": "apps.accounts.exceptions.api_exception_handler",
    "DATETIME_FORMAT": "iso-8601",
}

# Anti fuerza bruta: requests por IP cada 15 minutos (ventana de AnonRateThrottle).
# Se leen por apps/accounts/throttling.py, con un scope por endpoint.
DRF_THROTTLE_LOGIN = env_int("DRF_THROTTLE_LOGIN", default=10)
DRF_THROTTLE_VERIFY = env_int("DRF_THROTTLE_VERIFY", default=10)
DRF_THROTTLE_REGISTER = env_int("DRF_THROTTLE_REGISTER", default=5)

# Internacionalización
LANGUAGE_CODE = "es-ar"
TIME_ZONE = env_str("TIME_ZONE", default="America/Argentina/Buenos_Aires")
USE_I18N = True
USE_TZ = True


# Archivos estáticos
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_FILE_STORAGE = "django.core.files.storage.FileSystemStorage"


# Email (Django 6.x usa MAILERS en lugar de EMAIL_BACKEND).
# Sin SMTP_HOST todo se imprime en consola, handy para desarrollo.
SMTP_HOST = env_str("SMTP_HOST", default="")
SMTP_PORT = env_int("SMTP_PORT", default=587)
SMTP_USER = env_str("SMTP_USER", default="")
SMTP_PASSWORD = env_str("SMTP_PASSWORD", default="")
EMAIL_FROM = env_str("EMAIL_FROM", default="Clínica <no-reply@clinica.local>")

if SMTP_HOST:
    MAILERS = {
        "default": {
            "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
            "HOST": SMTP_HOST,
            "PORT": SMTP_PORT,
            "USERNAME": SMTP_USER,
            "PASSWORD": SMTP_PASSWORD,
            "USE_TLS": env_bool("SMTP_USE_TLS", default=True),
            "FROM_EMAIL": EMAIL_FROM,
        },
    }
    # Los tests escriben en memoria en vez de intentar salir a internet.
    MAILERS["testing"] = {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}
else:
    MAILERS = {
        "default": {
            "BACKEND": "django.core.mail.backends.console.EmailBackend",
            "FROM_EMAIL": EMAIL_FROM,
        },
        "testing": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"},
    }

# Nota: Django 6.x usa MAILERS y rechaza EMAIL_BACKEND si esta definido.
# Las libs de terceros que aun leen EMAIL_BACKEND usan el valor de MAILERS
# mediante mailers.get_backend(); no hace falta el alias.

# Dirección de los correos de verificación (cambiar por la real de la clínica).
CLINICA_EMAIL_VERIFICATION_SUBJECT = env_str(
    "CLINICA_VERIFICATION_SUBJECT", default="Verificá tu email - Clínica"
)
CLINICA_NAME = env_str("CLINICA_NAME", default="Clínica")
FRONTEND_URL = env_str("FRONTEND_URL", default="http://localhost:5173")


# En producción el frontend y el backend viven en dominios distintos: la cookie
# de refresh necesita SameSite=None + Secure para viajar en requests cross-site.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SAMESITE = "None" if not DEBUG else "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = "None" if not DEBUG else "Lax"
CSRF_COOKIE_SECURE = not DEBUG

# Esto es lo que de verdad importa acá: la cookie del refresh token. Sin esto,
# SimpleJWT deja los defaults (SameSite=Lax, Secure=False), y con el front en
# otro dominio el refresh no viaja y el token va en claro.
AUTH_COOKIE_SAMESITE = "None" if not DEBUG else "Lax"
AUTH_COOKIE_SECURE = not DEBUG
AUTH_COOKIE_HTTPONLY = True
AUTH_COOKIE_DOMAIN = env_str("AUTH_COOKIE_DOMAIN", default="") or None

if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", default=True)
    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = True
    SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", default=31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    X_FRAME_OPTIONS = "DENY"
    SECURE_CONTENT_TYPE_NOSNIFF = True
