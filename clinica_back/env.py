"""
Lectura de variables de entorno.

Unica diferencia con un os.getenv normal: los valores ya tienen tipo, con lo
que settings.py no necesita castear a mano ni repetir los defaults.

Soporta el prefijo de entorno de Django, asi que el mismo .env sirve para
`manage.py` y para un eventual process manager en produccion.
"""

import os
from urllib.parse import unquote, urlparse

_TRUE = {"1", "true", "t", "yes", "y", "on", "si", "sí"}
_FALSE = {"0", "false", "f", "no", "n", "off"}


def env_str(name, default=""):
    """Devuelve la variable como texto, o `default` si no esta definida o vacia."""
    value = os.environ.get(name, default)
    return value if value not in (None, "") else default


def env_bool(name, default=False):
    """Interpreta '1', 'true', 'yes', 'on' (y variantes SI/NO en español) como boolean."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(
        f"{name}={raw!r} no es un booleano valido. Usá true/false."
    )


def env_int(name, default=0):
    """Devuelve la variable como entero. Falla ruidosamente si no lo es."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name}={raw!r} no es un entero valido.") from exc


def env_list(name, default=None, separator=","):
    """Devuelve la variable como lista, separando por comas y descartando vacios."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return list(default) if default else []
    return [item.strip() for item in raw.split(separator) if item.strip()]


def database_from_url(url, conn_max_age=60):
    """
    Convierte un DATABASE_URL de Postgres en el dict que espera DATABASES.

    Acepta postgres:// y postgresql://, con o sin puerto, y con o sin path.
    Ej: postgresql://user:pass@host:5432/clinica_back?sslmode=require
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("postgres", "postgresql", "pgsql", "psql"):
        raise ValueError(
            f"esquema de DB no soportado: {parsed.scheme!r}. Usá postgresql://"
        )
    if not parsed.hostname:
        raise ValueError("DATABASE_URL sin host.")

    query = dict(
        pair.split("=", 1) if "=" in pair else (pair, "")
        for pair in parsed.query.split("&")
        if pair
    )

    config = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")) or "postgres",
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": unquote(parsed.hostname),
        "PORT": str(parsed.port or 5432),
        "CONN_MAX_AGE": conn_max_age,
    }

    if query.get("sslmode"):
        config["OPTIONS"] = {"sslmode": query["sslmode"]}

    return config
