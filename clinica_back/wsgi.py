"""WSGI config for clinica_back."""

import os
import sys

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "clinica_back.settings")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from clinica_back.bootstrap import validate_settings  # noqa: E402

validate_settings()

application = get_wsgi_application()
