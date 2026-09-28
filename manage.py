#!/usr/bin/env python
"""Utilidad de linea de comandos de Django para clinica_back."""

import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "clinica_back.settings")

    # El .env se carga antes de leer settings, y se valida la config antes de
    # arrancar Django para fallar con un mensaje util y no con un traceback.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from clinica_back.bootstrap import validate_settings

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "No se pudo importar Django. Instalalo con:\n"
            "  pip install -r requirements.txt"
        ) from exc

    validate_settings()
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
