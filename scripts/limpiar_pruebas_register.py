"""
Confirma que las cuentas creadas por la prueba de punta a punta quedaron en la
base con su ficha, y despues las borra.

    python scripts/limpiar_pruebas_register.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "clinica_back.settings")

from clinica_back.bootstrap import validate_settings

validate_settings()

import django

django.setup()

from apps.accounts.models import User
from apps.clinica.models import ColaEspera, ConsultaRecepcion, FichaMedica

PRUEBA = "prueba."


def main():
    cuentas = User.objects.filter(email__startswith=PRUEBA)
    print(f"cuentas de prueba en la base: {cuentas.count()}")

    for u in cuentas:
        ficha = FichaMedica.objects.filter(user=u).first()
        if ficha is None:
            print(f"  {u.email}: SIN FICHA  <- esto estaria mal")
            continue
        print(f"  {u.email}")
        print(f"    rol: {u.rol} | verificado: {u.email_verified}")
        print(f"    alergias: {ficha.alergias}")
        print(f"    cronicas: {ficha.enfermedades_cronicas}")
        print(f"    medicacion: {ficha.medicacion or '(vacia)'}")
        print(f"    observaciones: {ficha.observaciones or '(vacia)'}")
        print(f"    verificada: {ficha.verificada}")

    sin_ficha = [
        u.email for u in cuentas if not FichaMedica.objects.filter(user=u).exists()
    ]
    print(f"\ncuentas registradas sin ficha: {len(sin_ficha)}")
    if sin_ficha:
        print("  " + "\n  ".join(sin_ficha))

    ColaEspera.objects.filter(paciente__in=cuentas).delete()
    ConsultaRecepcion.objects.filter(paciente__in=cuentas).delete()
    fichas = FichaMedica.objects.filter(user__in=cuentas).delete()
    cuentas.delete()
    print(f"\nlimpiado: {fichas[0]} fichas, quedan {User.objects.filter(email__startswith=PRUEBA).count()} cuentas de prueba")


main()