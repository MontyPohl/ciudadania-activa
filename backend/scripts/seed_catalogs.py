#!/usr/bin/env python
"""Carga de catálogos semilla desde la línea de comandos (§7 y §12 del plan).

Envoltorio del management command `seed_catalogs`, que es la implementación
real. Uso:

    python scripts/seed_catalogs.py

Equivale a `python manage.py seed_catalogs` y es igualmente idempotente.
"""

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def main() -> None:
    sys.path.insert(0, str(BACKEND_DIR))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

    import django

    django.setup()

    from django.core.management import call_command

    call_command("seed_catalogs")


if __name__ == "__main__":
    main()
