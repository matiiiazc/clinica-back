"""
App clinica: dominio de la clinica (pacientes, medicos, turnos, historias).

Aun sin modelos. Sirve de contenedor para las proximas features y como ejemplo
de la estructura que sigue cada app: urls, serializers, views, permissions.
"""

from django.apps import AppConfig


class ClinicaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.clinica"
    label = "clinica"
    verbose_name = "Clínica"
