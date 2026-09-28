"""
Tests del flujo de auth completo contra PostgreSQL.

Se cubre lo que importa de verdad:
- el registro publico SIEMPRE crea paciente, aunque manden rol=admin
- sin verificar el email no se puede loguear
- el codigo es de un solo uso
- argon2 es el hasher y el hash nunca es la contrasena en claro
- los roles internos dan acceso al admin de Django
"""

from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import EmailVerificationCode, Role, User

PASSWORD = "Clinica2026Segura"


class ClinicTestCase(TestCase):
    """
    Base de los tests.

    Limpia la cache en cada test: los throttles de DRF cuentan por IP y todos
    los tests salen desde 127.0.0.1, asi que sin esto el sexto POST del mismo
    test suite se come un 429. Las subclases que necesiten mas setUp tienen que
    llamar a super().setUp().
    """

    def setUp(self):
        super().setUp()
        cache.clear()
        mail.outbox = []
        self.client = APIClient()


def register_payload(**overrides):
    data = {
        "email": "paciente@correo.com",
        "nombre": "Ana Perez",
        "telefono": "1122334455",
        "password": PASSWORD,
        "password_confirm": PASSWORD,
    }
    data.update(overrides)
    return data


class TestRegistroPublico(ClinicTestCase):
    """POST /api/auth/register"""

    def test_registro_devuelve_201_y_crea_paciente(self):
        response = self.client.post(
            reverse("auth-register"), register_payload(), format="json"
        )

        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="paciente@correo.com")
        self.assertEqual(user.rol, Role.PACIENTE)
        self.assertFalse(user.email_verified)
        self.assertFalse(user.is_staff)
        self.assertEqual(response.data["user"]["rol"], Role.PACIENTE)

    def test_registro_intenta_escalar_privilegios_y_se_lo_impide(self):
        """Un admin en el body no puede convertirse en admin."""
        response = self.client.post(
            reverse("auth-register"),
            register_payload(rol="admin", is_staff=True, is_superuser=True),
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="paciente@correo.com")
        self.assertEqual(user.rol, Role.PACIENTE)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_password_se_guarda_con_argon2(self):
        self.client.post(
            reverse("auth-register"), register_payload(), format="json"
        )
        user = User.objects.get(email="paciente@correo.com")

        self.assertTrue(user.password.startswith("argon2$"))
        self.assertNotIn(PASSWORD, user.password)
        self.assertTrue(check_password(PASSWORD, user.password))

    def test_registro_manda_email_con_el_codigo(self):
        self.client.post(
            reverse("auth-register"), register_payload(), format="json"
        )

        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        # El hash que se guarda en la base nunca puede viajar en el email.
        self.assertNotIn(
            EmailVerificationCode.objects.get().code_hash, body
        )
        self.assertIn(settings.CLINICA_NAME, body)

    def test_registro_guarda_el_codigo_hasheado(self):
        self.client.post(
            reverse("auth-register"), register_payload(), format="json"
        )
        record = EmailVerificationCode.objects.get()

        self.assertTrue(record.code_hash.startswith("argon2$"))
        self.assertTrue(record.is_usable)

    def test_registro_devuelve_dev_code_sin_smtp(self):
        """
        Sin SMTP la API tiene que devolver el codigo, si no no hay forma de probarlo.

        Durante los tests Django cambia el backend a locmem, asi que hay que
        forzar el de consola para estar en las mismas condiciones que en dev.
        """
        from django.core import mail as django_mail

        with (
            override_settings(
                MAILERS={
                    "default": {
                        "BACKEND": "django.core.mail.backends.console.EmailBackend"
                    }
                }
            ),
            mock.patch.object(django_mail, "send_mail"),
        ):
            response = self.client.post(
                reverse("auth-register"), register_payload(), format="json"
            )

        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data.get("dev_code"))

        # Y tiene que servir para verificar de verdad, no solo estar presente.
        verificacion = self.client.post(
            reverse("auth-verify-email"),
            {"email": register_payload()["email"], "code": response.data["dev_code"]},
            format="json",
        )
        self.assertEqual(verificacion.status_code, 200)

    def test_email_duplicado_devuelve_409(self):
        self.client.post(
            reverse("auth-register"), register_payload(), format="json"
        )
        response = self.client.post(
            reverse("auth-register"), register_payload(), format="json"
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "email_already_in_use")
        self.assertEqual(User.objects.count(), 1)

    def test_passwords_distintos_devuelve_400(self):
        response = self.client.post(
            reverse("auth-register"),
            register_payload(password_confirm="OtraClave2026"),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("password_confirm", response.data["errors"])
        self.assertFalse(User.objects.exists())

    def test_password_corto_devuelve_400_con_error_de_campo(self):
        response = self.client.post(
            reverse("auth-register"),
            register_payload(password="123", password_confirm="123"),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.data["errors"])

    def test_email_invalido_devuelve_400(self):
        response = self.client.post(
            reverse("auth-register"), register_payload(email="no-es-mail"),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.data["errors"])


class TestVerificacion(ClinicTestCase):
    """POST /api/auth/verify-email"""

    def setUp(self):
        super().setUp()
        self.client.post(
            reverse("auth-register"), register_payload(), format="json"
        )
        self.user = User.objects.get(email="paciente@correo.com")

    def _forzar_codigo(self, code):
        """Deja un código conocido como vigente, sin pasar por el email."""
        self.user.verification_codes.update(
            code_hash=EmailVerificationCode.hash_code(code)
        )
        return code

    def test_codigo_correcto_verifica_y_devuelve_tokens(self):
        codigo = self._forzar_codigo("123456")

        response = self.client.post(
            reverse("auth-verify-email"),
            {"email": "paciente@correo.com", "code": codigo},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)
        self.assertIn("access", response.data["tokens"])
        self.assertIn("refresh", response.data["tokens"])
        self.assertEqual(response.data["user"]["email_verified"], True)

    def test_codigo_incorrecto_devuelve_400(self):
        self._forzar_codigo("123456")

        response = self.client.post(
            reverse("auth-verify-email"),
            {"email": "paciente@correo.com", "code": "000000"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "invalid_code")
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_verified)

    def test_codigo_expirado_devuelve_400(self):
        self._forzar_codigo("123456")
        EmailVerificationCode.objects.update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

        response = self.client.post(
            reverse("auth-verify-email"),
            {"email": "paciente@correo.com", "code": "123456"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "invalid_code")

    def test_codigo_es_de_un_solo_uso(self):
        self._forzar_codigo("123456")
        payload = {"email": "paciente@correo.com", "code": "123456"}

        first = self.client.post(reverse("auth-verify-email"), payload, format="json")
        second = self.client.post(reverse("auth-verify-email"), payload, format="json")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)
        self.assertIn("used_at", EmailVerificationCode.objects.get().__dict__)

    def test_email_inexistente_no_revela_nada(self):
        """Mismo error que un codigo erroneo, para no filtrar cuentas."""
        response = self.client.post(
            reverse("auth-verify-email"),
            {"email": "nadie@correo.com", "code": "123456"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "invalid_code")


class TestReenvioDeCodigo(ClinicTestCase):
    """POST /api/auth/resend-code"""

    def setUp(self):
        super().setUp()
        self.client.post(
            reverse("auth-register"), register_payload(), format="json"
        )

    def test_reenvio_devuelve_200_y_manda_email(self):
        EmailVerificationCode.objects.update(
            created_at=timezone.now() - timedelta(minutes=5)
        )
        mail.outbox = []

        response = self.client.post(
            reverse("auth-resend-code"), {"email": "paciente@correo.com"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)

    def test_reenvio_se_restringe_a_60_segundos(self):
        response = self.client.post(
            reverse("auth-resend-code"), {"email": "paciente@correo.com"},
            format="json",
        )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data["code"], "resend_too_soon")

    def test_reenvio_a_email_desconocido_devuelve_200(self):
        response = self.client.post(
            reverse("auth-resend-code"), {"email": "nadie@correo.com"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)  # solo el del registro


class TestLogin(ClinicTestCase):
    """POST /api/auth/login"""

    def setUp(self):
        super().setUp()
        self.client.post(
            reverse("auth-register"), register_payload(), format="json"
        )
        self.user = User.objects.get(email="paciente@correo.com")

    def _verificar(self):
        self.user.verification_codes.update(
            code_hash=EmailVerificationCode.hash_code("123456"),
            used_at=timezone.now(),
        )
        self.user.email_verified = True
        self.user.save(update_fields=["email_verified"])

    def test_login_exitoso_devuelve_tokens(self):
        self._verificar()

        response = self.client.post(
            reverse("auth-login"),
            {"email": "paciente@correo.com", "password": PASSWORD},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data["tokens"])
        self.assertEqual(response.data["user"]["rol"], Role.PACIENTE)

    def test_login_sin_verificar_devuelve_403(self):
        response = self.client.post(
            reverse("auth-login"),
            {"email": "paciente@correo.com", "password": PASSWORD},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "email_not_verified")

    def test_login_con_password_incorrecto_devuelve_400(self):
        self._verificar()

        response = self.client.post(
            reverse("auth-login"),
            {"email": "paciente@correo.com", "password": "Incorrecta123"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.data["errors"])

    def test_login_es_case_insensitive_en_el_email(self):
        self._verificar()

        response = self.client.post(
            reverse("auth-login"),
            {"email": "PACIENTE@Correo.COM", "password": PASSWORD},
            format="json",
        )

        self.assertEqual(response.status_code, 200)

    def test_usuario_desactivado_no_puede_entrar(self):
        self._verificar()
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        response = self.client.post(
            reverse("auth-login"),
            {"email": "paciente@correo.com", "password": PASSWORD},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "inactive_user")

    def test_usuario_inexistente_no_revela_que_falta(self):
        response = self.client.post(
            reverse("auth-login"),
            {"email": "nadie@correo.com", "password": PASSWORD},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.data["errors"])


class TestMeYRefresh(ClinicTestCase):
    def setUp(self):
        super().setUp()
        self.client.post(
            reverse("auth-register"), register_payload(), format="json"
        )
        self.user = User.objects.get(email="paciente@correo.com")
        self.user.verification_codes.update(
            code_hash=EmailVerificationCode.hash_code("123456"), used_at=timezone.now()
        )
        self.user.email_verified = True
        self.user.save(update_fields=["email_verified"])

        self.login = self.client.post(
            reverse("auth-login"),
            {"email": "paciente@correo.com", "password": PASSWORD},
            format="json",
        )
        self.tokens = self.login.data["tokens"]

    def test_me_exige_token(self):
        self.assertEqual(self.client.get(reverse("auth-me")).status_code, 401)

    def test_me_devuelve_el_usuario(self):
        response = self.client.get(
            reverse("auth-me"), HTTP_AUTHORIZATION=f"Bearer {self.tokens['access']}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], "paciente@correo.com")
        self.assertEqual(response.data["rol"], Role.PACIENTE)

    def test_refresh_devuelve_un_access_nuevo(self):
        response = self.client.post(
            reverse("auth-refresh"),
            {"refresh": self.tokens["refresh"]},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data["tokens"])

    def test_refresh_invalido_devuelve_401(self):
        response = self.client.post(
            reverse("auth-refresh"), {"refresh": "basura"}, format="json"
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["code"], "invalid_refresh")

    def test_logout_limpia_las_cookies(self):
        response = self.client.post(reverse("auth-logout"), {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.cookies[settings.AUTH_COOKIE + "_access"].value, ""
        )

    def test_patch_me_actualiza_nombre(self):
        response = self.client.patch(
            reverse("auth-me"),
            {"nombre": "Ana Paula Perez", "telefono": "1155556666"},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.tokens['access']}",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.nombre, "Ana Paula Perez")

    def test_patch_me_no_puede_cambiar_el_rol(self):
        """El perfil propio no toca rol ni is_staff: no hay escalada de privilegios."""
        verified_antes = self.user.email_verified

        self.client.patch(
            reverse("auth-me"),
            {"rol": "admin", "is_staff": True, "email_verified": False},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.tokens['access']}",
        )

        self.user.refresh_from_db()
        self.assertEqual(self.user.rol, Role.PACIENTE)
        self.assertFalse(self.user.is_staff)
        self.assertEqual(self.user.email_verified, verified_antes)

    def test_cambio_de_contrasena(self):
        response = self.client.post(
            reverse("auth-change-password"),
            {
                "current_password": PASSWORD,
                "new_password": "NuevaClave2026",
                "new_password_confirm": "NuevaClave2026",
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.tokens['access']}",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(check_password("NuevaClave2026", self.user.password))

    def test_cambio_de_contrasena_con_actual_incorrecta(self):
        response = self.client.post(
            reverse("auth-change-password"),
            {
                "current_password": "Mal12345678",
                "new_password": "NuevaClave2026",
                "new_password_confirm": "NuevaClave2026",
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.tokens['access']}",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("current_password", response.data["errors"])


class TestRolesInternos(ClinicTestCase):
    """Los roles de la clinica se dan de alta, no se registran."""

    def test_create_staff_asigna_rol_y_permite_entrar_al_admin(self):
        admin_user = User.objects.create_user(
            email="admin@clinica.com",
            password=PASSWORD,
            nombre="La Dueña",
            rol=Role.ADMIN,
            email_verified=True,
        )
        self.assertTrue(admin_user.is_staff)
        self.assertTrue(admin_user.is_admin)
        self.assertTrue(admin_user.is_clinica)

    def test_paciente_con_is_staff_es_rechazado_por_la_base(self):
        """
        El constraint de Postgres es la ultima linea de defensa.

        Un .update() sobre el queryset se saltea save(), que es justamente lo
        que mantiene is_staff consistente con el rol.
        """
        from django.db import IntegrityError, transaction

        paciente = User.objects.create_user(
            email="p2@correo.com", password=PASSWORD, rol=Role.PACIENTE
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.filter(pk=paciente.pk).update(is_staff=True)

    def test_paciente_no_tiene_acceso_al_admin(self):
        paciente = User.objects.create_user(
            email="p@correo.com", password=PASSWORD, rol=Role.PACIENTE
        )
        self.assertFalse(paciente.is_staff)
        self.assertFalse(paciente.is_clinica)

    def test_medico_es_staff_pero_no_admin(self):
        medico = User.objects.create_user(
            email="doc@clinica.com", password=PASSWORD, rol=Role.MEDICO
        )
        self.assertTrue(medico.is_staff)
        self.assertTrue(medico.is_clinica)
        self.assertFalse(medico.is_admin)

    def test_is_staff_se_deriva_del_rol_al_guardar(self):
        user = User.objects.create_user(
            email="x@clinica.com", password=PASSWORD, rol=Role.PACIENTE
        )
        user.is_staff = True
        user.save()

        user.refresh_from_db()
        self.assertFalse(user.is_staff)

    def test_superuser_creado_por_el_manager(self):
        root = User.objects.create_superuser(email="root@clinica.com", password=PASSWORD)

        self.assertTrue(root.is_staff)
        self.assertTrue(root.is_superuser)
        self.assertEqual(root.rol, Role.ADMIN)
        self.assertTrue(root.email_verified)


class TestHealth(ClinicTestCase):
    def test_health_responde_ok(self):
        response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["database"], "ok")


@override_settings(REST_FRAMEWORK={**settings.REST_FRAMEWORK})
class TestConfigDeSeguridad(TestCase):
    def test_argon2_es_el_hasher_principal(self):
        self.assertEqual(
            settings.PASSWORD_HASHERS[0],
            "django.contrib.auth.hashers.Argon2PasswordHasher",
        )

    def test_jwt_secret_es_distinta_de_secret_key(self):
        self.assertTrue(settings.SIMPLE_JWT["SIGNING_KEY"])
        self.assertNotEqual(
            settings.SIMPLE_JWT["SIGNING_KEY"], settings.SECRET_KEY
        )

    def test_db_es_postgres(self):
        self.assertEqual(
            settings.DATABASES["default"]["ENGINE"],
            "django.db.backends.postgresql",
        )


class TestCookiesDeSesion(TestCase):
    """
    La cookie del refresh token es la que sostiene la sesion, asi que sus flags
    se chequean aparte: si quedan en los defaults de SimpleJWT (SameSite=Lax,
    Secure=False) el front de otro dominio no puede refrescar y el token viaja
    en claro.

    Ojo: los flags se calculan en el import del modulo a partir de DEBUG, y el
    runner de tests pone DEBUG=False despues del import. Por eso aca no se
    asserta el valor concreto segun DEBUG, sino que esten definidos y que
    coincidan con los de Django.
    """

    FLAGS = (
        "AUTH_COOKIE_SAMESITE",
        "AUTH_COOKIE_SECURE",
        "AUTH_COOKIE_HTTPONLY",
    )

    def test_los_flags_de_la_cookie_de_refresh_estan_definidos(self):
        """Si falta alguno, gana el default de SimpleJWT y se pierde el control."""
        for nombre in self.FLAGS:
            with self.subTest(flag=nombre):
                self.assertTrue(
                    hasattr(settings, nombre),
                    f"{nombre} no esta en settings: cae al default de SimpleJWT",
                )

    def test_el_refresh_siempre_va_http_only(self):
        """Es el unico flag que no depende de DEBUG: el token no se lee desde JS."""
        self.assertTrue(settings.AUTH_COOKIE_HTTPONLY)

    def test_las_cookies_de_jwt_y_las_de_django_coinciden(self):
        """
        Si divergen, una de las dos quedo sin hardenear por un cambio parcial.
        Es el invariante que se rompio cuando se endurecieron SESSION/CSRF y se
        olvidaron las de SimpleJWT.
        """
        self.assertEqual(settings.AUTH_COOKIE_SAMESITE, settings.SESSION_COOKIE_SAMESITE)
        self.assertEqual(settings.AUTH_COOKIE_SECURE, settings.SESSION_COOKIE_SECURE)
        self.assertEqual(settings.AUTH_COOKIE_HTTPONLY, settings.SESSION_COOKIE_HTTPONLY)


class TestGuardDeArranque(TestCase):
    """validate_settings() es lo que impide desplegar con la config de dev."""

    def test_en_desarrollo_no_hay_guardas_de_produccion(self):
        """
        Tiene que arrancar siempre en dev, si no no se puede trabajar. El DEBUG
        se fuerza a proposito: el runner de tests lo pone en False despues del
        import y sin esto se dispararian las guardas de produccion.
        """
        from clinica_back.bootstrap import validate_settings

        with override_settings(DEBUG=True):
            validate_settings()

    def test_cors_de_desarrollo_con_credenciales_falla_en_produccion(self):
        """Con credenciales, un origen de dev en la lista es un agujero."""
        from clinica_back.bootstrap import validate_settings

        with override_settings(
            DEBUG=False,
            SECRET_KEY="x" * 50,
            CORS_ALLOW_CREDENTIALS=True,
            CORS_ALLOWED_ORIGINS=["https://www.clinica.com"],
        ):
            validate_settings()  # con el dominio real tiene que pasar

        with (
            override_settings(
                DEBUG=False,
                SECRET_KEY="x" * 50,
                CORS_ALLOW_CREDENTIALS=True,
                CORS_ALLOWED_ORIGINS=["https://www.clinica.com", "http://localhost:5173"],
            ),
            self.assertRaises(SystemExit) as cm,
        ):
            validate_settings()

        self.assertIn("localhost:5173", str(cm.exception))

    def test_sin_credenciales_el_origen_de_dev_no_es_problema(self):
        from clinica_back.bootstrap import validate_settings

        with override_settings(
            DEBUG=False,
            SECRET_KEY="x" * 50,
            CORS_ALLOW_CREDENTIALS=False,
            CORS_ALLOWED_ORIGINS=["http://localhost:5173"],
        ):
            validate_settings()  # sin cookies no hay riesgo
