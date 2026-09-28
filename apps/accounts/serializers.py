"""
Serializers de auth.

Reglas que se respetan aca:
- El rol nunca se acepta desde la API publica. `RegisterSerializer` fuerza
  `paciente` y descarta cualquier `rol` que venga en el body.
- La contrasena se valida con los validadores de Django (argon2 hashea despues).
- Los errores de contrasena van en `errors`, no en `detail`, para que el
  frontend pueda pegarlos al campo del formulario.
"""

import logging

from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from .models import EmailVerificationCode, Role, User
from .services import send_verification_code

logger = logging.getLogger(__name__)


def _enmascarar(email):
    """Deja ver de que dominio viene el intento sin volcar el email entero."""
    local, _, dominio = email.partition("@")
    if not dominio:
        return local[:1] + "***"
    return f"{local[:1]}***@{dominio}"


def _log_login_failure(email, user_encontrado):
    """
    Un rejected de login es siempre igual para el cliente, asi que sin esto no
    hay forma de saber si el usuario se equivocaron en el email o en la clave.
    """
    logger.warning(
        "Login rechazado: email=%s usuario_existe=%s",
        _enmascarar(email),
        user_encontrado,
    )


class UserSerializer(serializers.ModelSerializer):
    """Representacion publica de un usuario."""

    rol_display = serializers.CharField(source="get_rol_display", read_only=True)
    is_clinica = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "nombre",
            "telefono",
            "rol",
            "rol_display",
            "is_clinica",
            "email_verified",
            "must_change_password",
            "date_joined",
            "last_login",
        )
        read_only_fields = fields


class RegisterSerializer(serializers.Serializer):
    """
    Alta publica. Siempre paciente y sin email verificado.

    Crea el usuario, genera el codigo de 6 digitos y lo manda por email, pero
    NO devuelve tokens: hasta verificar el email no hay sesion.
    """

    # Solo se llena si no hay SMTP configurado; la vista lo agrega a la respuesta.
    dev_code: str | None = None

    email = serializers.EmailField(max_length=254)
    nombre = serializers.CharField(max_length=150, required=False, allow_blank=True)
    telefono = serializers.CharField(
        max_length=32, required=False, allow_blank=True
    )
    password = serializers.CharField(
        write_only=True, style={"input_type": "password"}, trim_whitespace=False
    )
    password_confirm = serializers.CharField(
        write_only=True, style={"input_type": "password"}, trim_whitespace=False
    )

    def validate_email(self, value):
        from .exceptions import EmailAlreadyInUse

        email = value.strip().lower()
        if User.objects.filter(email=email).exists():
            raise EmailAlreadyInUse()
        return email

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError(
                {"password_confirm": "Las contrasenas no coinciden."}
            )

        candidate = User(
            email=attrs["email"],
            nombre=attrs.get("nombre", ""),
            telefono=attrs.get("telefono", ""),
        )
        try:
            password_validation.validate_password(attrs["password"], candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)})

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        validated_data.pop("password_confirm", None)
        email = validated_data.pop("email")
        nombre = validated_data.pop("nombre", "")
        telefono = validated_data.pop("telefono", "")

        # create_user() hashea con Argon2id y pone rol=paciente + email_verified=False.
        user = User.objects.create_user(
            email=email,
            password=validated_data["password"],
            nombre=nombre,
            telefono=telefono,
            rol=Role.PACIENTE,
            email_verified=False,
        )

        _record, code = EmailVerificationCode.issue(user)
        # Sin SMTP configurado, send_verification_code devuelve el codigo en
        # claro para poder probarlo. La vista lo lee desde el serializer.
        self.dev_code = send_verification_code(user, code)

        return user


class VerifyEmailSerializer(serializers.Serializer):
    """Confirma el email con el codigo recibido."""

    email = serializers.EmailField(max_length=254)
    code = serializers.CharField(max_length=12, trim_whitespace=True)

    def validate(self, attrs):
        from .exceptions import InvalidVerificationCode

        email = attrs["email"].strip().lower()
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            # Mismo error que si el codigo estuviera mal, para no confirmar
            # que emails estan registrados.
            raise InvalidVerificationCode(errors={"code": ["Codigo incorrecto o expirado."]})

        record = (
            EmailVerificationCode.objects.filter(
                user=user,
                purpose=EmailVerificationCode.Purpose.REGISTRO,
                used_at__isnull=True,
            )
            .order_by("-created_at")
            .first()
        )

        if record is None:
            raise InvalidVerificationCode(
                errors={"code": ["No hay ningun codigo pendiente. Pide uno nuevo."]}
            )

        if not record.check_code(attrs["code"]):
            raise InvalidVerificationCode(
                errors={"code": ["Codigo incorrecto o expirado."]}
            )

        attrs["user"] = user
        attrs["record"] = record
        return attrs

    def save(self, **kwargs):
        user = self.validated_data["user"]
        user.email_verified = True
        user.save(update_fields=["email_verified"])
        self.validated_data["record"].consume()
        return user


class ResendCodeSerializer(serializers.Serializer):
    """Reenvia el codigo de verificacion."""

    email = serializers.EmailField(max_length=254)


class LoginSerializer(serializers.Serializer):
    """Login por email + contrasena. Solo para cuentas ya verificadas."""

    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(
        write_only=True, style={"input_type": "password"}, trim_whitespace=False
    )
    remember_me = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        from .exceptions import EmailNotVerified, InactiveUser

        email = attrs["email"].strip().lower()
        # El front ya limpia espacios, pero si alguien pega la clave desde un
        # chat o un email suele quedar un espacio o salto de linea al final y
        # el login falla sin que se entienda por que.
        password = attrs["password"].strip()

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            # Se hashea algo para que el tiempo de respuesta sea el mismo que
            # con un email existente, y no se pueda enumerar cuentas.
            User().set_password(password)
            _log_login_failure(email, user_encontrado=False)
            raise serializers.ValidationError(
                {"password": ["Credenciales invalidas."]}
            )

        if not user.check_password(password):
            _log_login_failure(email, user_encontrado=True)
            raise serializers.ValidationError(
                {"password": ["Credenciales invalidas."]}
            )

        # A partir de aca la contrasena es correcta, asi que se puede ser
        # claro con el motivo del rechazo.
        if not user.is_active:
            raise InactiveUser()
        if not user.email_verified:
            raise EmailNotVerified()

        attrs["user"] = user
        return attrs

    def get_user(self):
        return self.validated_data["user"]


class ChangePasswordSerializer(serializers.Serializer):
    """Cambio de contrasena del usuario autenticado."""

    current_password = serializers.CharField(
        write_only=True, style={"input_type": "password"}, trim_whitespace=False
    )
    new_password = serializers.CharField(
        write_only=True, style={"input_type": "password"}, trim_whitespace=False
    )
    new_password_confirm = serializers.CharField(
        write_only=True, style={"input_type": "password"}, trim_whitespace=False
    )

    def validate(self, attrs):
        user = self.context["request"].user

        if not user.check_password(attrs["current_password"]):
            raise serializers.ValidationError(
                {"current_password": ["La contrasena actual no es correcta."]}
            )

        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError(
                {"new_password_confirm": ["Las contrasenas no coinciden."]}
            )

        if user.check_password(attrs["new_password"]):
            raise serializers.ValidationError(
                {"new_password": ["La nueva contrasena debe ser distinta de la actual."]}
            )

        try:
            password_validation.validate_password(attrs["new_password"], user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": list(exc.messages)})

        return attrs

    @transaction.atomic
    def save(self, **kwargs):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password"])
        return user


class UpdateProfileSerializer(serializers.ModelSerializer):
    """Edicion de los datos propios. El email no se cambia por aca."""

    class Meta:
        model = User
        fields = ("nombre", "telefono")
