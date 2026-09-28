"""
Modelos de identidad: usuarios de la clinica y verificacion de email.

Regla de roles: el registro publico (POST /api/auth/register) SIEMPRE crea
usuarios con rol `paciente`. Los roles internos (admin, medico, recepcion) se
asignan desde el admin de Django o por linea de comando, nunca desde la API
publica.
"""

import secrets
import string
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Role(models.TextChoices):
    """Roles de la clinica. `paciente` es el unico que se puede autodregistrar."""

    PACIENTE = "paciente", _("Paciente")
    RECEPCION = "recepcion", _("Recepcion")
    MEDICO = "medico", _("Médico")
    ADMIN = "admin", _("Administrador")


#: Roles que la clinica asigna a mano. El registro publico nunca los entrega.
STAFF_ROLES = frozenset({Role.RECEPCION, Role.MEDICO, Role.ADMIN})


class UserManager(BaseUserManager):
    """Manager con create_user / create_superuser sobre el email."""

    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("El email es obligatorio.")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra_fields)
        # set_password() hashea con Argon2id (ver PASSWORD_HASHERS en settings).
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        extra_fields.setdefault("rol", Role.PACIENTE)
        extra_fields.setdefault("email_verified", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("rol", Role.ADMIN)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("email_verified", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("El superusuario debe tener is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("El superusuario debe tener is_superuser=True.")

        return self._create_user(email, password, **extra_fields)

    def staff(self):
        """Usuarios internos de la clinica (excluye pacientes)."""
        return self.filter(rol__in=list(STAFF_ROLES))

    def pacientes(self):
        return self.filter(rol=Role.PACIENTE)


class User(AbstractBaseUser, PermissionsMixin):
    """
    Usuario identificado por email.

    `is_staff` se deriva del rol: da acceso al admin de Django. `is_superuser`
    queda reservado para la persona que opera la instancia.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField("email", unique=True, db_index=True)
    nombre = models.CharField("nombre", max_length=150, blank=True)
    telefono = models.CharField("teléfono", max_length=32, blank=True)

    rol = models.CharField(
        "rol", max_length=20, choices=Role.choices, default=Role.PACIENTE
    )

    email_verified = models.BooleanField("email verificado", default=False)
    must_change_password = models.BooleanField("debe cambiar la contraseña", default=False)

    is_active = models.BooleanField("activo", default=True)
    is_staff = models.BooleanField("acceso al admin", default=False)

    last_login = models.DateTimeField("último acceso", null=True, blank=True)
    date_joined = models.DateTimeField("alta", default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = _("usuario")
        verbose_name_plural = _("usuarios")
        ordering = ("-date_joined",)
        constraints = [
            # Red de seguridad: un paciente nunca puede tener is_staff=True, que
            # es lo que le abriria el admin de Django. save() ya mantiene esto
            # consistente, pero un .update() sobre el queryset lo esquivaria.
            models.CheckConstraint(
                condition=~models.Q(rol="paciente", is_staff=True),
                name="paciente_sin_acceso_admin",
            )
        ]
        indexes = [
            models.Index(fields=["rol"], name="idx_usuario_rol"),
            models.Index(fields=["email_verified"], name="idx_usuario_verificado"),
        ]

    def __str__(self):
        return f"{self.email} ({self.get_rol_display()})"

    def save(self, *args, **kwargs):
        # El rol es la fuente de verdad; is_staff se mantiene consistente solo.
        self.is_staff = self.rol in STAFF_ROLES or self.is_superuser
        super().save(*args, **kwargs)

    def get_full_name(self):
        return self.nombre or self.email

    def get_short_name(self):
        return (self.nombre or self.email).split(" ")[0]

    @property
    def is_clinica(self):
        """True para quien trabaja en la clinica (no para pacientes)."""
        return self.rol in STAFF_ROLES

    @property
    def is_admin(self):
        return self.rol == Role.ADMIN or self.is_superuser

    def puede_verificar_email(self):
        return not self.email_verified


class EmailVerificationCode(models.Model):
    """
    Codigo numerico de un solo uso para confirmar el email.

    Se guarda hasheado con Argon2 (mismo criterio que las contrasenas): si alguien
    lee la tabla no puede completar el registro de otra persona. Un unico
    registro vigente por usuario, viejo o nuevo.
    """

    class Purpose(models.TextChoices):
        REGISTRO = "registro", _("Registro")
        CAMBIO_EMAIL = "cambio_email", _("Cambio de email")
        RESET_PASSWORD = "reset_password", _("Restablecer contraseña")

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="verification_codes"
    )
    purpose = models.CharField(
        "propósito", max_length=20, choices=Purpose.choices, default=Purpose.REGISTRO
    )
    code_hash = models.CharField("hash del código", max_length=255)
    created_at = models.DateTimeField("creado", default=timezone.now)
    expires_at = models.DateTimeField("expira")
    used_at = models.DateTimeField("usado", null=True, blank=True)
    attempts = models.PositiveSmallIntegerField("intentos", default=0)

    class Meta:
        verbose_name = _("código de verificación")
        verbose_name_plural = _("códigos de verificación")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["user", "purpose"], name="idx_codigo_usuario"),
        ]

    def __str__(self):
        return f"{self.user.email} - {self.get_purpose_display()} ({self.expires_at:%H:%M})"

    # --- ciclo de vida ---

    @classmethod
    def generate_code(cls):
        """
        Devuelve (codigo_en_claro, hash).

        El codigo en claro se manda por email y no se guarda jamas; el hash es
        lo unico que queda en la base.
        """
        length = settings.EMAIL_VERIFICATION_CODE_LENGTH
        code = "".join(secrets.choice(string.digits) for _ in range(length))
        return code, cls.hash_code(code)

    @staticmethod
    def hash_code(code):
        from django.contrib.auth.hashers import make_password

        return make_password(str(code))

    @classmethod
    def issue(cls, user, purpose=None):
        """
        Invalida los codigos previos del usuario y crea uno nuevo, ya hasheado.

        Devuelve (registro, codigo_en_claro) para poder mandarlo por email.
        """
        purpose = purpose or cls.Purpose.REGISTRO
        cls.objects.filter(user=user, purpose=purpose, used_at__isnull=True).update(
            used_at=timezone.now()
        )

        code, code_hash = cls.generate_code()
        record = cls.objects.create(
            user=user,
            purpose=purpose,
            code_hash=code_hash,
            expires_at=timezone.now()
            + timedelta(minutes=settings.EMAIL_VERIFICATION_CODE_TTL_MINUTES),
        )
        return record, code

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_usable(self):
        return self.used_at is None and not self.is_expired

    def check_code(self, code):
        """
        Valida el codigo en claro contra el hash (Argon2). No consume el codigo.
        Devuelve True/False.
        """
        from django.contrib.auth.hashers import check_password

        if not self.is_usable:
            return False
        return check_password(str(code), self.code_hash)

    def consume(self):
        """Marca el codigo como usado para que no sirva por segunda vez."""
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])

    def ttl_seconds(self):
        remaining = self.expires_at - timezone.now()
        return max(0, int(remaining.total_seconds()))

    def can_resend(self):
        """Respeta la ventana de espera entre dos envios al mismo usuario."""
        wait = timedelta(seconds=settings.EMAIL_VERIFICATION_RESEND_SECONDS)
        return self.created_at + wait <= timezone.now()
