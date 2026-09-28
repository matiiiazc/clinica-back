"""
Carga del .env y validaciones de arranque.

Se ejecuta desde manage.py / wsgi.py antes de tocar settings, asi que un
proyecto con la SECRET_KEY de ejemplo no llega a arrancar por error.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# .env local + .env.<entorno> opcionales, sin sobreescribir variables ya
# definidas en el proceso (en produccion manda el entorno real).
for candidate in (".env", f".env.{os.environ.get('DJANGO_ENV', 'development')}"):
    env_file = BASE_DIR / candidate
    if env_file.exists():
        load_dotenv(env_file, override=False)


# La que genera `django-admin startproject`, para no bloquear el arranque en dev.
PLACEHOLDER_SECRET_KEY = "django-insecure-"


def validate_settings():
    """Falla temprano y con un mensaje claro si la config de produccion es insegura."""
    from django.conf import settings

    problems = []

    if not settings.SECRET_KEY:
        problems.append(
            "Falta SECRET_KEY. Generala con:\n"
            "  python -c \"from django.core.management.utils import get_random_secret_key "
            "as g; print(g())\""
        )
    elif settings.SECRET_KEY.startswith(PLACEHOLDER_SECRET_KEY) and not settings.DEBUG:
        problems.append("SECRET_KEY es la de ejemplo de Django y DEBUG=False.")

    if not settings.DEBUG:
        if not settings.SIMPLE_JWT.get("SIGNING_KEY"):
            problems.append(
                "Falta JWT_SECRET. Debe ser distinto de SECRET_KEY para que un "
                "token de la API no sirva como cookie de sesion."
            )
        if not settings.ALLOWED_HOSTS:
            problems.append("ALLOWED_HOSTS vacio con DEBUG=False.")

        # Con credenciales habilitadas, un origen de desarrollo en la lista
        # deja que cualquiera que tenga algo sirviendo en ese puerto haga
        # requests autenticadas desde el navegador.
        if settings.CORS_ALLOW_CREDENTIALS:
            locales = [
                origen
                for origen in settings.CORS_ALLOWED_ORIGINS
                if "localhost" in origen or "127.0.0.1" in origen
            ]
            if locales:
                problems.append(
                    "CORS_ALLOWED_ORIGINS con credenciales habilitadas incluye "
                    f"orígenes de desarrollo: {', '.join(locales)}. Poné ahi el "
                    "dominio real del front."
                )

    if not settings.DEBUG and "sqlite3" in settings.DATABASES["default"]["ENGINE"]:
        problems.append("DEBUG=False con SQLite. Postgres es obligatorio en produccion.")

    if problems:
        raise SystemExit(
            "\nConfiguracion invalida:\n"
            + "\n".join(f"  - {problem}" for problem in problems)
            + "\n"
        )


def run_migrations_needed():
    """Avisa (sin romper) si hay migraciones sin aplicar. Util para entrypoints."""
    import subprocess

    result = subprocess.run(
        [sys.executable, str(BASE_DIR / "manage.py"), "migrate", "--check"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(
            "\n[clinica_back] Faltan migraciones por aplicar. "
            "Ejecuta: python manage.py migrate\n"
        )
