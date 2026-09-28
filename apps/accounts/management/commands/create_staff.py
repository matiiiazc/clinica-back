"""Da de alta al personal de la clinica: recepcion, medico o admin."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils.crypto import get_random_string

from apps.accounts.models import EmailVerificationCode, Role
from apps.accounts.services import send_verification_code

User = get_user_model()


class Command(BaseCommand):
    help = "Crea un usuario interno de la clinica (recepcion, medico o admin)."

    def add_arguments(self, parser):
        parser.add_argument("email", help="Email del usuario.")
        parser.add_argument(
            "--rol",
            default=Role.ADMIN,
            choices=[role.value for role in Role],
            help="Rol interno. Default: admin.",
        )
        parser.add_argument("--nombre", default="", help="Nombre y apellido.")
        parser.add_argument("--telefono", default="", help="Telefono.")
        parser.add_argument(
            "--password",
            default="",
            help="Contrasena inicial. Si se omite, se genera una aleatoria.",
        )
        parser.add_argument(
            "--verificado",
            action="store_true",
            help="Marca el email como verificado y no manda codigo.",
        )
        parser.add_argument(
            "--no-force-password-change",
            action="store_true",
            help="No Obliga a cambiar la contrasena en el primer login.",
        )

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            raise CommandError(f"El email {email} ya esta registrado.")

        verified = options["verificado"]
        password = options["password"] or get_random_string(20)

        user = User.objects.create_user(
            email=email,
            password=password,
            nombre=options["nombre"],
            telefono=options["telefono"],
            rol=options["rol"],
            email_verified=verified,
            must_change_password=not verified and not options["no_force_password_change"],
        )

        self.stdout.write(self.style.SUCCESS(f"Usuario creado: {user.email} ({user.rol})"))

        if verified:
            self.stdout.write("Email marcado como verificado.")
        else:
            _, code = EmailVerificationCode.issue(user)
            send_verification_code(user, code)
            self.stdout.write("Se envio el codigo de verificacion al email.")
            if user.must_change_password:
                self.stdout.write("Debera cambiar la contrasena en el primer login.")

        if not options["password"]:
            self.stdout.write(self.style.WARNING(f"Contrasena temporal: {password}"))
