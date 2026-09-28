"""
Cambia la contrasena de un usuario existente.

Sirve para cuando se pierde la clave de una cuenta interna: el admin puede
resetear la suya, pero si se pierde la del unico admin no hay forma de
recuperarla desde la API. La clave se genera sola si no se pasa por parametro.
"""

import secrets
import string

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import User

#: Sin vocales ni caracteres que se confunden (0/O, 1/l/I).
ALFABETO_SIN_AMBIGUEDAD = "".join(
    c for c in (string.ascii_uppercase + string.ascii_lowercase + string.digits) if c not in "O0lI"
)


class Command(BaseCommand):
    help = "Cambia la contrasena de un usuario existente."

    def add_arguments(self, parser):
        parser.add_argument("email", help="Email del usuario.")
        parser.add_argument(
            "--password",
            help="Contrasena nueva. Si se omite, se genera una aleatoria.",
        )
        parser.add_argument(
            "--no-force-password-change",
            action="store_true",
            help="No obliga a cambiar la contrasena en el proximo login.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        password = options["password"] or self.generar()

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise CommandError(f"No existe ningun usuario con {email}.")

        try:
            validate_password(password, user=user)
        except ValidationError as exc:
            raise CommandError(
                "La contrasena no cumple las reglas:\n"
                + "\n".join(f"  - {m}" for m in exc.messages)
            )

        user.set_password(password)
        if not options["no_force_password_change"]:
            user.must_change_password = True
        user.save(update_fields=["password", "must_change_password"])

        self.stdout.write(f"Contrasena de {user.email} actualizada.")
        if options["no_force_password_change"]:
            self.stdout.write("No se va a pedir cambiarla en el proximo login.")
        else:
            self.stdout.write("Va a tener que cambiarla en el proximo login.")

    def generar(self):
        # 20 caracteres en grupos de 4: pasa los validadores y se puede
        # dictar o copiar en grupos sin equivocarse.
        clave = "".join(secrets.choice(ALFABETO_SIN_AMBIGUEDAD) for _ in range(20))
        return f"{clave[:4]}-{clave[4:8]}-{clave[8:12]}-{clave[12:16]}-{clave[16:]}"
