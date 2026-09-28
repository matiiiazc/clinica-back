"""
Backends de autenticacion.

Django busca el usuario por USERNAME_FIELD. Ours es `email`, asi que este
backend normaliza a minusculas antes de comparar: asi se puede loguear
"Ana@Clinica.com" aunque se haya registrado como "ana@clinica.com".
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

UserModel = get_user_model()


class EmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        email = username or kwargs.get(UserModel.USERNAME_FIELD)
        if not email or not password:
            return None

        try:
            user = UserModel.objects.get(email=email.strip().lower())
        except UserModel.DoesNotExist:
            # Se ejecuta un hash falso para que el tiempo de respuesta no
            # revele si el email existe o la contrasena esta mal.
            UserModel().set_password(password)
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
