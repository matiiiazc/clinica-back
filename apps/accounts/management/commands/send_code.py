"""Reenvia el codigo de verificacion de un usuario."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import EmailVerificationCode
from apps.accounts.services import send_verification_code

User = get_user_model()


class Command(BaseCommand):
    help = "Reenvia el codigo de verificacion de un usuario por email."

    def add_arguments(self, parser):
        parser.add_argument("email")

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise CommandError(f"No existe ningun usuario con {email}.")

        if user.email_verified:
            raise CommandError(f"El email de {email} ya esta verificado.")

        _, code = EmailVerificationCode.issue(user)
        dev_code = send_verification_code(user, code)

        if dev_code:
            self.stdout.write(self.style.WARNING(f"Codigo: {dev_code}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Codigo enviado a {email}."))
