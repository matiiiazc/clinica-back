"""
Tests de apps.clinica.

Por ahora cubren la ficha medica del paciente, que es lo que se creo en el
register. Los turnos, especialidades y la cola de espera todavia no tienen API,
asi que no hay nada que testear de ellos aca.
"""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User

# Se reusa la base de accounts: ya limpia la cache en cada test, que es lo que
# hace falta para no comerse el throttle de /register (5 requests por IP cada
# 15 minutos). Copiar el setUp aca seria duplicar la regla.
from apps.accounts.tests import ClinicTestCase
from apps.clinica.models import (
    ColaEspera,
    ConsultaRecepcion,
    Especialidad,
    FichaMedica,
    PerfilMedico,
    PerfilPaciente,
    Turno,
)

PASSWORD = "UnaClave2026"


class TestFichaMedica(ClinicTestCase):
    """La ficha de salud se pide en el register y es obligatoria."""

    def payload(self, **overrides):
        data = {
            "email": "paciente@correo.com",
            "nombre": "Ana Perez",
            "password": PASSWORD,
            "password_confirm": PASSWORD,
            "ficha_alergias": "Penicilina",
            "ficha_enfermedades_cronicas": "Ninguna",
        }
        data.update(overrides)
        return data

    def registrar(self, **overrides):
        return self.client.post(
            reverse("auth-register"), self.payload(**overrides), content_type="application/json"
        )

    def registrar_sin_ficha(self):
        """Igual que registrar(), pero saca los campos de la ficha del payload."""
        data = self.payload()
        data.pop("ficha_alergias")
        data.pop("ficha_enfermedades_cronicas")
        return self.client.post(
            reverse("auth-register"), data, content_type="application/json"
        )

    def test_el_register_crea_la_ficha(self):
        self.registrar()

        ficha = FichaMedica.objects.get(user__email="paciente@correo.com")
        self.assertEqual(ficha.alergias, "Penicilina")
        self.assertEqual(ficha.enfermedades_cronicas, "Ninguna")
        self.assertEqual(ficha.medicacion, "")
        self.assertEqual(ficha.observaciones, "")

    def test_guarda_los_campos_opcionales(self):
        self.registrar(
            ficha_medicacion="Losartan 50mg",
            ficha_observaciones="Tengo mariscos",
        )

        ficha = FichaMedica.objects.get(user__email="paciente@correo.com")
        self.assertEqual(ficha.medicacion, "Losartan 50mg")
        self.assertEqual(ficha.observaciones, "Tengo mariscos")

    def test_sin_ficha_no_se_registra(self):
        """Sin la ficha el registro se rechaza: es obligatoria."""
        response = self.registrar_sin_ficha()
        self.assertEqual(response.status_code, 400, response.content)

        errors = response.json()["errors"]
        self.assertIn("ficha_alergias", errors)
        self.assertIn("ficha_enfermedades_cronicas", errors)

        self.assertFalse(User.objects.filter(email="paciente@correo.com").exists())
        self.assertEqual(FichaMedica.objects.count(), 0)

    def test_alergias_en_blanco_no_se_registra(self):
        """"Ninguna" es valido; el silencio no."""
        response = self.registrar(ficha_alergias="")
        self.assertEqual(response.status_code, 400, response.content)

        self.assertIn("ficha_alergias", response.json()["errors"])
        self.assertFalse(User.objects.filter(email="paciente@correo.com").exists())

    def test_cronicas_en_blanco_no_se_registra(self):
        response = self.registrar(ficha_enfermedades_cronicas="   ")
        self.assertEqual(response.status_code, 400, response.content)

        self.assertIn("ficha_enfermedades_cronicas", response.json()["errors"])

    def test_solo_blancos_no_alcanza(self):
        response = self.registrar(ficha_alergias="  ", ficha_enfermedades_cronicas="\n")
        self.assertEqual(response.status_code, 400, response.content)

    def test_la_ficha_nace_sin_verificar(self):
        """Registrarse no verifica nada: eso lo hace un profesional."""
        self.registrar()

        ficha = FichaMedica.objects.get()
        self.assertIsNone(ficha.verificada_por)
        self.assertIsNone(ficha.verificada_en)
        self.assertFalse(ficha.verificada)

    def test_un_profesional_puede_verificarla(self):
        self.registrar()
        ficha = FichaMedica.objects.get()

        medico = User.objects.create_user(
            email="doc@correo.com", password=PASSWORD, nombre="Dr. Uno", rol="medico"
        )

        ficha.verificada_por = medico
        ficha.verificada_en = timezone.now()
        ficha.save()

        ficha.refresh_from_db()
        self.assertTrue(ficha.verificada)

    def test_es_uno_a_uno_con_el_usuario(self):
        """No puede haber dos fichas para la misma persona."""

        self.registrar()
        ficha = FichaMedica.objects.get()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FichaMedica.objects.create(
                    user=ficha.user, alergias="Otra", enfermedades_cronicas="Otra"
                )

    def test_no_aparece_en_el_payload_de_usuario(self):
        """La ficha no se filtra en /me ni en el register."""
        self.registrar()

        response = self.registrar(
            email="otro@correo.com",
            ficha_alergias="Secuela",
            ficha_enfermedades_cronicas="Asma",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertNotIn("ficha_medica", response.json()["user"])
        self.assertNotIn("alergias", response.json()["user"])


class TestColaEspera(ClinicTestCase):
    """La cola se ordena por prioridad y despues por llegada."""

    def setUp(self):
        self.paciente = User.objects.create_user(
            email="pac@correo.com", password=PASSWORD, nombre="Pac"
        )
        self.esp1 = Especialidad.objects.create(nombre="Cardiologia")
        self.esp2 = Especialidad.objects.create(nombre="Dermatologia")
        self.esp3 = Especialidad.objects.create(nombre="Clinica")

    def test_urgente_va_antes_although_se_cree_al_medio(self):
        ColaEspera.objects.create(paciente=self.paciente, especialidad=self.esp1)
        ColaEspera.objects.create(
            paciente=self.paciente,
            especialidad=self.esp2,
            prioridad=ColaEspera.Prioridad.URGENTE,
        )
        ColaEspera.objects.create(paciente=self.paciente, especialidad=self.esp3)

        cola = list(
            ColaEspera.objects.filter(estado="esperando").order_by("-prioridad", "creado")
        )
        self.assertEqual(cola[0].prioridad, ColaEspera.Prioridad.URGENTE)
        self.assertEqual(cola[0].especialidad_id, self.esp2.id)

    def test_fifo_dentro_de_la_misma_prioridad(self):
        ColaEspera.objects.create(paciente=self.paciente, especialidad=self.esp1)
        ColaEspera.objects.create(paciente=self.paciente, especialidad=self.esp2)

        cola = list(
            ColaEspera.objects.filter(estado="esperando").order_by("-prioridad", "creado")
        )
        self.assertEqual(cola[0].especialidad_id, self.esp1.id)
        self.assertEqual(cola[1].especialidad_id, self.esp2.id)

    def test_no_hay_dos_entradas_vivas_para_la_misma_especialidad(self):

        ColaEspera.objects.create(paciente=self.paciente, especialidad=self.esp1)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ColaEspera.objects.create(paciente=self.paciente, especialidad=self.esp1)

    def test_agendado_libera_el_lugar(self):
        """Una vez agendado, el paciente puede volver a encolarse."""
        entrada = ColaEspera.objects.create(
            paciente=self.paciente, especialidad=self.esp1
        )
        entrada.estado = ColaEspera.Estado.AGENDADO
        entrada.save()

        ColaEspera.objects.create(paciente=self.paciente, especialidad=self.esp1)

        vivas = ColaEspera.objects.filter(
            paciente=self.paciente, especialidad=self.esp1, estado="esperando"
        )
        self.assertEqual(vivas.count(), 1)

    def test_rango_de_fechas_invertido_se_rechaza(self):

        hoy = timezone.now().date()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ColaEspera.objects.create(
                    paciente=self.paciente,
                    especialidad=self.esp1,
                    desde=hoy,
                    hasta=hoy - timedelta(days=2),
                )

    def test_cubre_respeta_la_ventana(self):

        hoy = timezone.now().date()
        entrada = ColaEspera.objects.create(
            paciente=self.paciente,
            especialidad=self.esp1,
            desde=hoy,
            hasta=hoy + timedelta(days=5),
        )
        self.assertTrue(entrada.cubre(hoy))
        self.assertFalse(entrada.cubre(hoy - timedelta(days=1)))
        self.assertFalse(entrada.cubre(hoy + timedelta(days=6)))

    def test_sin_fechas_cubre_todo(self):
        entrada = ColaEspera.objects.create(
            paciente=self.paciente, especialidad=self.esp1
        )

        self.assertTrue(entrada.cubre(timezone.now().date()))
        self.assertTrue(entrada.cubre(timezone.now().date() + timedelta(days=900)))


class TestEstadoMedico(ClinicTestCase):
    """Solo un medico libre recibe turnos."""

    def setUp(self):
        self.esp = Especialidad.objects.create(nombre="Cardiologia")
        self.user = User.objects.create_user(
            email="doc@correo.com", password=PASSWORD, nombre="Dr. Uno", rol="medico"
        )
        self.medico = PerfilMedico.objects.create(
            user=self.user, especialidad=self.esp, matricula="M-1"
        )

    def test_nace_libre(self):
        self.assertEqual(self.medico.estado, PerfilMedico.Estado.LIBRE)
        self.assertTrue(self.medico.disponible)

    def test_atendiendo_no_disponible(self):
        self.medico.estado = PerfilMedico.Estado.ATENDIENDO
        self.medico.save()
        self.assertFalse(self.medico.disponible)

    def test_inactivo_no_disponible(self):
        self.medico.estado = PerfilMedico.Estado.INACTIVO
        self.medico.save()
        self.assertFalse(self.medico.disponible)

    def test_los_estados_del_turno_no_cambiaron(self):
        """Agregar los estados del medico no toco los del turno."""
        self.assertEqual(
            [e for e in Turno.Estado.values],
            ["pendiente", "confirmado", "atendido", "cancelado", "no_asistio"],
        )