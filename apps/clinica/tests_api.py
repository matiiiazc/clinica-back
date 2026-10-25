"""
Tests de la API del dominio clinica contra PostgreSQL.

Se prueba contra la base real y no contra un mock por dos razones concretas:
el corazon del modelo es un `ExclusionConstraint` de PostgreSQL que impide que
dos turnos del mismo profesional se pisen, y eso no existe en SQLite; y la cola
de espera se apoya en un `UniqueConstraint` parcial que solo se puede verificar
contra la base de verdad.

La organizacion es por rol, porque es la pregunta que el producto hace: "que
puede ver y hacer cada uno". Los tests de permisos son los mas numerosos a
proposito: un endpoint sin chequeo de rol es el error mas caro y el mas facil de
no notar.

Se reusa `ClinicTestCase` de accounts porque ya limpia la cache en cada test, que
es lo que evita que el throttle de /register (5 requests por IP cada 15 minutos)
rompa la suite.
"""

from datetime import date, datetime, time, timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role, User

from apps.accounts.tests import ClinicTestCase
from apps.clinica.models import (
    Agenda,
    Bloqueo,
    ColaEspera,
    ConsultaRecepcion,
    Especialidad,
    FichaMedica,
    PerfilMedico,
    PerfilPaciente,
    Turno,
    Urgencia,
)

PASSWORD = "Clinica2026Segura"


class BaseClinica(ClinicTestCase):
    """
    Escenario comun: una clinica con una especialidad, un medico con agenda,
    una recepcion, un admin y dos pacientes.

    Los horarios estan anclados a una fecha fija (`DIA`) y no a "hoy" para que
    el test no dependa del dia en que se corre. Un test que pasa el lunes y
    falla el domingo es un test que no prueba nada.
    """

    #: Lunes a las 10:00, con timezone activa.
    DIA = date(2026, 6, 15)
    HORA = time(10, 0)

    def setUp(self):
        super().setUp()
        cache_clear()

        # --- Usuarios ---
        self.admin = User.objects.create_user(
            email="admin@clinica.com", password=PASSWORD, rol=Role.ADMIN, is_staff=True
        )
        self.recepcion = User.objects.create_user(
            email="recepcion@clinica.com", password=PASSWORD, rol=Role.RECEPCION
        )
        self.medico_user = User.objects.create_user(
            email="medico@clinica.com", password=PASSWORD, rol=Role.MEDICO
        )
        self.medico_user.nombre = "Dr House"
        self.medico_user.save()

        self.paciente = self.crear_paciente("paciente@correo.com", "Ana Perez")
        self.otro_paciente = self.crear_paciente("otro@correo.com", "Beto Gomez")

        # --- Dominio ---
        self.especialidad = Especialidad.objects.create(
            nombre="Cardiologia", duracion_turno_minutos=30
        )
        self.otra_especialidad = Especialidad.objects.create(
            nombre="Dermatologia", duracion_turno_minutos=20
        )

        self.medico = PerfilMedico.objects.create(
            user=self.medico_user,
            especialidad=self.especialidad,
            matricula="MP-1234",
            estado=PerfilMedico.Estado.LIBRE,
        )

        # Agenda de lunes a viernes, 9 a 13 y 16 a 19.
        for dia in range(5):
            Agenda.objects.create(
                medico=self.medico,
                dia_semana=dia,
                hora_inicio=time(9, 0),
                hora_fin=time(13, 0),
            )

    def crear_paciente(self, email, nombre):
        user = User.objects.create_user(email=email, password=PASSWORD, rol=Role.PACIENTE)
        user.nombre = nombre
        user.email_verified = True
        user.save()
        FichaMedica.objects.create(
            user=user, alergias="Ninguna", enfermedades_cronicas="Ninguna"
        )
        return user

    # --- Utilidades ---

    def instante(self, hora=None):
        """Datetime aware del dia de test a la hora indicada."""
        return timezone.make_aware(
            timezone.datetime.combine(self.DIA, hora or self.HORA)
        )

    def turno_valido(self, **overrides):
        data = {
            "paciente": self.paciente,
            "medico": self.medico,
            "especialidad": self.especialidad,
            "inicio": self.instante(),
            "fin": self.instante(time(10, 30)),
            "estado": Turno.Estado.PENDIENTE,
        }
        data.update(overrides)
        return Turno.objects.create(**data)

    def futuro_en_agenda(self, hora=10):
        """
        Un datetime futuro que cae dentro de la agenda del medico.

        Necesario para los endpoints que rechazan el pasado (solicitar turno,
        agendar desde la cola). No se puede usar `self.instante()` porque DIA es
        una fecha fija de 2026: cuando la suite corra despues de ese dia, todos
        los tests de reserva empiezan a fallar por una razon que no tiene que ver
        con el codigo. Se busca el primer dia habil dentro de una semana.
        """
        dia = timezone.localtime().date() + timedelta(days=1)
        while dia.weekday() > 4:
            dia += timedelta(days=1)
        return timezone.make_aware(timezone.datetime.combine(dia, time(hora, 0)))

    def como(self, user):
        """Cliente ya autenticado con el rol de `user`."""
        client = APIClient()
        client.force_authenticate(user=user)
        return client


def cache_clear():
    """La cache se limpia en ClinicTestCase; se reexporta por claridad."""
    from django.core.cache import cache

    cache.clear()


# ---------------------------------------------------------------------------
# Especialidades
# ---------------------------------------------------------------------------


class TestEspecialidades(BaseClinica):
    """Listado para todos, escritura solo para administracion."""

    def test_paciente_ve_especialidades_activas(self):
        response = self.como(self.paciente).get(reverse("clinica:especialidad-lista"))
        self.assertEqual(response.status_code, 200)
        nombres = [e["nombre"] for e in response.json()]
        self.assertIn("Cardiologia", nombres)

    def test_especialidad_inactiva_no_aparece(self):
        Especialidad.objects.create(nombre="Radiologia", activa=False)
        response = self.como(self.paciente).get(reverse("clinica:especialidad-lista"))
        self.assertNotIn("Radiologia", [e["nombre"] for e in response.json()])

    def test_recepcion_no_crea_especialidades(self):
        response = self.como(self.recepcion).post(
            reverse("clinica:especialidad-lista"), {"nombre": "Neurologia"}, format="json"
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_crea_especialidad(self):
        response = self.como(self.admin).post(
            reverse("clinica:especialidad-lista"),
            {"nombre": "Neurologia", "duracion_turno_minutos": 45},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["duracion_turno_minutos"], 45)

    def test_baja_es_logica_y_no_borra(self):
        """Borrar dejaria turnos historicos apuntando al vacio."""
        response = self.como(self.admin).delete(
            reverse("clinica:especialidad-detalle", args=[self.especialidad.id])
        )
        self.assertEqual(response.status_code, 204)
        self.especialidad.refresh_from_db()
        self.assertFalse(self.especialidad.activa)


# ---------------------------------------------------------------------------
# Profesionales
# ---------------------------------------------------------------------------


class TestMedicos(BaseClinica):
    def test_recepcion_filtra_disponibles(self):
        self.medico.estado = PerfilMedico.Estado.INACTIVO
        self.medico.save()
        response = self.como(self.recepcion).get(
            reverse("clinica:medico-lista"), {"disponibles": "1"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["results"]), 0)

    def test_paciente_no_lista_medicos(self):
        """No es informacion que el paciente necesite: elige especialidad."""
        response = self.como(self.paciente).get(reverse("clinica:medico-lista"))
        self.assertEqual(response.status_code, 403)

    def test_medico_cambia_su_propio_estado(self):
        response = self.como(self.medico_user).patch(
            reverse("clinica:medico-mi-perfil"), {"estado": "atendiendo"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["estado"], "atendiendo")

    def test_recepcion_no_cambia_estado_de_tercero(self):
        response = self.como(self.recepcion).patch(
            reverse("clinica:medico-mi-perfil"), {"estado": "inactivo"}, format="json"
        )
        self.assertEqual(response.status_code, 403)

    def test_alta_de_profesional_promueve_el_rol(self):
        nuevo = self.crear_paciente("nuevo@correo.com", "Carla Ruiz")
        response = self.como(self.admin).post(
            reverse("clinica:medico-lista"),
            {
                "email": nuevo.email,
                "matricula": "MP-9999",
                "especialidad": self.otra_especialidad.id,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        nuevo.refresh_from_db()
        self.assertEqual(nuevo.rol, Role.MEDICO)

    def test_matricula_duplicada_se_rechaza(self):
        nuevo = self.crear_paciente("nuevo@correo.com", "Carla Ruiz")
        response = self.como(self.admin).post(
            reverse("clinica:medico-lista"),
            {"email": nuevo.email, "matricula": "MP-1234", "especialidad": self.especialidad.id},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_no_se_puede_crear_medico_de_usuario_inexistente(self):
        response = self.como(self.admin).post(
            reverse("clinica:medico-lista"),
            {"email": "fantasma@correo.com", "matricula": "MP-1", "especialidad": self.especialidad.id},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_el_personal_puede_leer_la_agenda_de_un_profesional(self):
        """
        Regresion: el GET de la agenda se caia con 500.

        `get_permissions` combinaba `IsStaffUser() | IsAdminUser()`, con las
        clases ya instanciadas. El `|` de permisos de DRF funciona entre clases,
        no entre objetos, asi que reventaba con TypeError en la primera llamada.
        Como el unico test que tocaba la agenda era el POST (que va por otra
        rama), nadie lo vio.
        """
        response = self.como(self.recepcion).get(
            reverse("clinica:medico-agenda", args=[self.medico.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), len(self.medico.agendas.all()))

    def test_el_medico_tambien_lee_una_agenda(self):
        """El personal incluye al medico: por eso el permiso es `staff | admin`."""
        response = self.como(self.medico_user).get(
            reverse("clinica:medico-agenda", args=[self.medico.id])
        )
        self.assertEqual(response.status_code, 200)

    def test_el_paciente_no_lee_la_agenda_de_ningun_profesional(self):
        response = self.como(self.paciente).get(
            reverse("clinica:medico-agenda", args=[self.medico.id])
        )
        self.assertEqual(response.status_code, 403)

    def test_recepcion_carga_un_tramo_de_agenda(self):
        response = self.como(self.recepcion).post(
            reverse("clinica:medico-agenda", args=[self.medico.id]),
            {"dia_semana": 4, "hora_inicio": "15:00", "hora_fin": "19:00"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)

    def test_el_medico_no_carga_tramos_en_una_agenda(self):
        """Alta es de recepcion: el medico solo cambia su estado."""
        response = self.como(self.medico_user).post(
            reverse("clinica:medico-agenda", args=[self.medico.id]),
            {"dia_semana": 4, "hora_inicio": "15:00", "hora_fin": "19:00"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_recepcion_gestiona_bloqueos(self):
        response = self.como(self.recepcion).get(
            reverse("clinica:medico-bloqueos", args=[self.medico.id])
        )
        self.assertEqual(response.status_code, 200)

    def test_el_medico_no_gestiona_bloqueos(self):
        response = self.como(self.medico_user).get(
            reverse("clinica:medico-bloqueos", args=[self.medico.id])
        )
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# Disponibilidad y slots
# ---------------------------------------------------------------------------


class TestDisponibilidad(BaseClinica):
    def test_slots_respeta_la_agenda(self):
        """
        De 9:00 a 13:00 con turnos de 30 min: 15 horarios.

        El ultimo arranca 12:30 y termina 13:00 exacto. Uno a las 12:45 ya no
        entra, porque el turno se pasaria del cierre: el slot tiene que caber
        entero dentro del tramo.

        Los horarios vienen en ISO-8601 con offset (-03:00), asi que el chequeo
        compara el reloj y no el string completo: escribir "T09:00:00" como
        sufijo seria igual de fragil que el bug queYM esta linea busca cazar.
        """
        response = self.como(self.paciente).get(
            reverse("clinica:disponibilidad"),
            {"medico": self.medico.id, "fecha": self.DIA.isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        horarios = response.json()["medicos"][0]["horarios"]
        self.assertEqual(len(horarios), 15)

        relojes = [str(datetime.fromisoformat(h)) for h in horarios]
        self.assertTrue(relojes[0].startswith("2026-06-15 09:00"))
        self.assertTrue(relojes[-1].startswith("2026-06-15 12:30"))

    def test_un_turno_tomado_saca_el_slot(self):
        self.turno_valido(inicio=self.instante(time(10, 0)), fin=self.instante(time(10, 30)))
        response = self.como(self.paciente).get(
            reverse("clinica:disponibilidad"),
            {"medico": self.medico.id, "fecha": self.DIA.isoformat()},
        )
        horarios = response.json()["medicos"][0]["horarios"]
        # Un turno de 10:00 a 10:30 tapa tres slots posibles: 09:45, 10:00 y
        # 10:15. Los tres arrancan antes de las 10:30 y terminan despues de las
        # 10:00, asi que los tres se pisan.
        self.assertEqual(len(horarios), 12)
        self.assertFalse(
            any(str(datetime.fromisoformat(h))[-8:] in ("09:45:00", "10:00:00", "10:15:00") for h in horarios)
        )

    def test_turno_cancelado_libera_el_slot(self):
        """Si no, un turno cancelado bloquearia el horario para siempre."""
        self.turno_valido(
            inicio=self.instante(time(10, 0)),
            fin=self.instante(time(10, 30)),
            estado=Turno.Estado.CANCELADO,
        )
        response = self.como(self.paciente).get(
            reverse("clinica:disponibilidad"),
            {"medico": self.medico.id, "fecha": self.DIA.isoformat()},
        )
        self.assertEqual(len(response.json()["medicos"][0]["horarios"]), 15)

    def test_bloqueo_deja_el_dia_vacio(self):
        Bloqueo.objects.create(
            medico=self.medico, desde=self.DIA, hasta=self.DIA, motivo="Vacaciones"
        )
        response = self.como(self.paciente).get(
            reverse("clinica:disponibilidad"),
            {"medico": self.medico.id, "fecha": self.DIA.isoformat()},
        )
        self.assertEqual(response.json()["medicos"][0]["horarios"], [])

    def test_medico_inactivo_no_aparece(self):
        self.medico.estado = PerfilMedico.Estado.INACTIVO
        self.medico.save()
        response = self.como(self.paciente).get(
            reverse("clinica:disponibilidad"), {"fecha": self.DIA.isoformat()}
        )
        self.assertEqual(response.json()["medicos"], [])

    def test_fecha_mal_escrita_da_400(self):
        response = self.como(self.paciente).get(
            reverse("clinica:disponibilidad"), {"fecha": "15/06/2026"}
        )
        self.assertEqual(response.status_code, 400)

    def test_devuelve_agenda_y_horarios_ocupados(self):
        """La pantalla de medicos disponibles necesita ver que esta ocupado."""
        self.turno_valido(
            inicio=self.instante(time(10, 0)), fin=self.instante(time(10, 30))
        )
        response = self.como(self.paciente).get(
            reverse("clinica:disponibilidad"),
            {"medico": self.medico.id, "fecha": self.DIA.isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        medico = response.json()["medicos"][0]

        self.assertEqual(len(medico["horarios_ocupados"]), 1)
        ocupado = medico["horarios_ocupados"][0]
        # Se comparan los relojes y no el string: el offset depende de la
        # timezone del entorno y no es lo que este test quiere fijar.
        desde = datetime.fromisoformat(ocupado["inicio"])
        hasta = datetime.fromisoformat(ocupado["fin"])
        self.assertEqual((desde.hour, desde.minute), (10, 0))
        self.assertEqual((hasta.hour, hasta.minute), (10, 30))

        self.assertEqual(len(medico["agenda"]), 1)
        self.assertEqual(medico["agenda"][0]["hora_inicio"], "09:00")
        self.assertEqual(medico["agenda"][0]["hora_fin"], "13:00")


class TestValidarDisponibilidad(BaseClinica):
    """Chequeo previo a reservar: no crea turno, solo informa."""

    def validar(self, inicio, medico=None, user=None):
        return self.como(user or self.paciente).get(
            reverse("clinica:disponibilidad-validar"),
            {"medico": (medico or self.medico).id, "inicio": inicio.isoformat()},
        )

    def test_horario_libre_es_disponible(self):
        response = self.validar(self.futuro_en_agenda(11))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["disponible"])
        self.assertIsNone(response.json()["motivo"])

    def test_horario_ocupado_informa_el_motivo(self):
        inicio = self.futuro_en_agenda(11)
        self.turno_valido(inicio=inicio, fin=inicio + timedelta(minutes=30))
        response = self.validar(inicio)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["disponible"])
        self.assertIn("turno", response.json()["motivo"])

    def test_fuera_de_agenda_no_esta_disponible(self):
        response = self.validar(self.futuro_en_agenda(23))
        self.assertFalse(response.json()["disponible"])
        self.assertIn("agenda", response.json()["motivo"])

    def test_horario_pasado_no_esta_disponible(self):
        response = self.validar(timezone.now() - timedelta(days=1))
        self.assertFalse(response.json()["disponible"])
        self.assertIn("futuro", response.json()["motivo"])

    def test_medico_inexistente_da_400(self):
        response = self.como(self.paciente).get(
            reverse("clinica:disponibilidad-validar"),
            {"medico": 999999, "inicio": self.futuro_en_agenda(11).isoformat()},
        )
        self.assertEqual(response.status_code, 400)

    def test_recepcion_tambien_puede_validar(self):
        response = self.validar(self.futuro_en_agenda(11), user=self.recepcion)
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# Turnos: asignacion
# ---------------------------------------------------------------------------


class TestAsignacionDeTurnos(BaseClinica):
    def test_recepcion_asigna_un_turno(self):
        response = self.como(self.recepcion).post(
            reverse("clinica:turno-lista"),
            {
                "paciente": self.paciente.id,
                "medico": self.medico.id,
                "inicio": self.instante().isoformat(),
                "motivo": "Dolor de pecho",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["estado"], "pendiente")
        self.assertEqual(response.json()["duracion_minutos"], 30)

    def test_paciente_id_viene_como_uuid_usable_en_la_url_de_la_ficha(self):
        """
        `paciente_id` tiene que viajar como el UUID, no como un entero.

        El pk de User es UUID y `int(uuid)` no lanza error: devuelve el entero de
        128 bits. Si el serializer lo deja pasar, el JSON lleva un numero de 39
        digitos que el navegador no puede representar sin perder precision, y la
        pantalla "ver ficha del paciente" manda una id que no matchea nunca.
        """
        response = self.como(self.recepcion).post(
            reverse("clinica:turno-lista"),
            {
                "paciente": self.paciente.id,
                "medico": self.medico.id,
                "inicio": self.instante().isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)

        id_recibido = response.json()["paciente_id"]
        self.assertEqual(id_recibido, str(self.paciente.id))

        # El round trip completo: la id que dio el listado tiene que servir para
        # abrir la ficha.
        ficha = self.como(self.medico_user).get(
            reverse("clinica:paciente-ficha", args=[id_recibido])
        )
        self.assertEqual(ficha.status_code, 200)

    def test_recepcion_no_agenda_sobre_otro_turno(self):
        self.turno_valido()
        response = self.como(self.recepcion).post(
            reverse("clinica:turno-lista"),
            {
                "paciente": self.paciente.id,
                "medico": self.medico.id,
                "inicio": self.instante(time(10, 15)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        # El exception handler de accounts envuelve los errores de validacion en
        # {"code": ..., "errors": {...}}, asi que la clave del campo esta un
        # nivel mas adentro que el body crudo de DRF.
        self.assertIn("inicio", response.json()["errors"])

    def test_no_se_agenda_a_medico_inactivo(self):
        self.medico.estado = PerfilMedico.Estado.INACTIVO
        self.medico.save()
        response = self.como(self.recepcion).post(
            reverse("clinica:turno-lista"),
            {"paciente": self.paciente.id, "medico": self.medico.id, "inicio": self.instante().isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_no_se_agenda_fuera_de_agenda(self):
        """Un martes a las 23 esta libre en la base, pero no se atiende."""
        martes = timezone.make_aware(
            timezone.datetime.combine(date(2026, 6, 16), time(23, 0))
        )
        response = self.como(self.recepcion).post(
            reverse("clinica:turno-lista"),
            {"paciente": self.paciente.id, "medico": self.medico.id, "inicio": martes.isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_no_se_agenda_en_dia_bloqueado(self):
        Bloqueo.objects.create(medico=self.medico, desde=self.DIA, hasta=self.DIA, motivo="Feriado")
        response = self.como(self.recepcion).post(
            reverse("clinica:turno-lista"),
            {"paciente": self.paciente.id, "medico": self.medico.id, "inicio": self.instante().isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_la_base_rechaza_solapamiento_aunque_el_serializer_pase(self):
        """Defensa en profundidad: el constraint de PostgreSQL es el ultimo filtro."""
        from django.db import IntegrityError, transaction

        self.turno_valido()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Turno.objects.create(
                    paciente=self.otro_paciente,
                    medico=self.medico,
                    especialidad=self.especialidad,
                    inicio=self.instante(time(10, 15)),
                    fin=self.instante(time(10, 45)),
                    estado=Turno.Estado.PENDIENTE,
                )

    def test_paciente_no_crea_turnos_directamente(self):
        response = self.como(self.paciente).post(
            reverse("clinica:turno-lista"),
            {"paciente": self.paciente.id, "medico": self.medico.id, "inicio": self.instante().isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_medico_no_agenda_turnos(self):
        """El medico recibe turnos, no los asigna: es lo que se acordo."""
        response = self.como(self.medico_user).post(
            reverse("clinica:turno-lista"),
            {"paciente": self.paciente.id, "medico": self.medico.id, "inicio": self.instante().isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# Turnos: estados
# ---------------------------------------------------------------------------


class TestEstadosDeTurno(BaseClinica):
    def test_transicion_valida_desde_el_sistema(self):
        turno = self.turno_valido(estado=Turno.Estado.CONFIRMADO)
        response = self.como(self.medico_user).patch(
            reverse("clinica:turno-detalle", args=[turno.id]), {"estado": "atendido"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        turno.refresh_from_db()
        self.assertEqual(turno.estado, Turno.Estado.ATENDIDO)

    def test_transicion_invalida_se_explica(self):
        """De PENDIENTE no se pasa a ATENDIDO: hay que confirmar antes."""
        turno = self.turno_valido()
        response = self.como(self.recepcion).patch(
            reverse("clinica:turno-detalle", args=[turno.id]), {"estado": "atendido"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("confirmado", str(response.json()).lower())

    def test_medico_no_cierra_el_turno_de_otro(self):
        otro = self.medico_user.__class__.objects.create_user(
            email="medico2@clinica.com", password=PASSWORD, rol=Role.MEDICO
        )
        PerfilMedico.objects.create(
            user=otro, especialidad=self.especialidad, matricula="MP-777"
        )
        turno = self.turno_valido()
        response = self.como(otro).patch(
            reverse("clinica:turno-detalle", args=[turno.id]), {"estado": "atendido"}, format="json"
        )
        self.assertEqual(response.status_code, 404)

    def test_paciente_cancela_su_turno(self):
        turno = self.turno_valido(estado=Turno.Estado.CONFIRMADO)
        response = self.como(self.paciente).patch(
            reverse("clinica:turno-detalle", args=[turno.id]), {"estado": "cancelado"}, format="json"
        )
        self.assertEqual(response.status_code, 200)

    def test_paciente_no_marca_atendido(self):
        """
        Marcar atendido es responsabilidad del profesional.

        El turno arranca CONFIRMADO a proposito, para que el rechazo sea por rol
        (403) y no porque la transicion no exista desde PENDIENTE (400): los dos
        rechazos son legítimos pero probarian dos cosas distintas.
        """
        turno = self.turno_valido(estado=Turno.Estado.CONFIRMADO)
        response = self.como(self.paciente).patch(
            reverse("clinica:turno-detalle", args=[turno.id]), {"estado": "atendido"}, format="json"
        )
        self.assertEqual(response.status_code, 403)

    def test_paciente_no_ve_turno_de_otro(self):
        """404 y no 403: un 403 confirma que ese id existe."""
        turno = self.turno_valido(paciente=self.otro_paciente)
        response = self.como(self.paciente).get(
            reverse("clinica:turno-detalle", args=[turno.id])
        )
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# Turnos: paneles del paciente y del medico
# ---------------------------------------------------------------------------


class TestMisTurnos(BaseClinica):
    def test_paciente_ve_solo_los_suyos(self):
        self.turno_valido(paciente=self.paciente)
        self.turno_valido(paciente=self.otro_paciente, inicio=self.instante(time(11, 0)), fin=self.instante(time(11, 30)))
        response = self.como(self.paciente).get(reverse("clinica:turno-mios"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)

    def test_historico_oculta_los_cerrados(self):
        self.turno_valido(estado=Turno.Estado.ATENDIDO)
        response = self.como(self.paciente).get(reverse("clinica:turno-mios"))
        self.assertEqual(len(response.json()), 0)
        response = self.como(self.paciente).get(
            reverse("clinica:turno-mios"), {"historico": "1"}
        )
        self.assertEqual(len(response.json()), 1)

    def test_medico_filtra_por_periodo(self):
        """
        El filtro de periodo es como trabaja un consultorio: dia, semana o mes.

        Se pone un turno hoy y otro en 20 dias. El de hoy tiene que aparecer en
        las tres vistas; el de los 20 dias solo en el mes, segun caiga dentro.
        """
        hoy = timezone.localtime().date()
        hoy_10 = timezone.make_aware(timezone.datetime.combine(hoy, time(10, 0)))

        def turno(inicio):
            return Turno.objects.create(
                paciente=self.paciente,
                medico=self.medico,
                especialidad=self.especialidad,
                inicio=inicio,
                fin=inicio + timedelta(minutes=30),
                estado=Turno.Estado.PENDIENTE,
            )

        turno(hoy_10)

        # 20 dias adelante: casi siempre cae en otro mes, pero no garantizado.
        # Por eso el segundo turno se usa solo para la asercion de "dia".
        mas_adelante = timezone.make_aware(
            timezone.datetime.combine(hoy + timedelta(days=20), time(10, 0))
        )
        if mas_adelante.month == hoy.month:
            turno(mas_adelante)

        dia = self.como(self.medico_user).get(
            reverse("clinica:turno-mios"), {"periodo": "dia"}
        )
        self.assertEqual(len(dia.json()), 1)

        semana = self.como(self.medico_user).get(
            reverse("clinica:turno-mios"), {"periodo": "semana"}
        )
        self.assertGreaterEqual(len(semana.json()), 1)

        mes = self.como(self.medico_user).get(
            reverse("clinica:turno-mios"), {"periodo": "mes"}
        )
        self.assertGreaterEqual(len(mes.json()), 1)

    def test_medico_solo_ve_sus_turnos(self):
        """Dos profesionales con agenda el mismo dia: cada uno ve lo suyo."""
        otro = User.objects.create_user(
            email="medico2@clinica.com", password=PASSWORD, rol=Role.MEDICO
        )
        otro_medico = PerfilMedico.objects.create(
            user=otro, especialidad=self.especialidad, matricula="MP-777"
        )
        inicio = self.futuro_en_agenda()
        for medico in (self.medico, otro_medico):
            Turno.objects.create(
                paciente=self.paciente,
                medico=medico,
                especialidad=self.especialidad,
                inicio=inicio,
                fin=inicio + timedelta(minutes=30),
                estado=Turno.Estado.PENDIENTE,
            )
        # El filtro por defecto es el dia de hoy, asi que hay que preguntar por
        # la semana para que el turno futuro entre en la respuesta.
        response = self.como(self.medico_user).get(
            reverse("clinica:turno-mios"), {"periodo": "semana"}
        )
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["medico_id"], self.medico.id)


class TestSolicitudDeTurno(BaseClinica):
    def test_paciente_solicita_y_el_sistema_elige_medico(self):
        response = self.como(self.paciente).post(
            reverse("clinica:turno-solicitar"),
            {
                "especialidad": self.especialidad.id,
                "inicio": self.futuro_en_agenda().isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["medico_id"], self.medico.id)

    def test_no_hay_horario_no_es_error_fatal(self):
        """409 con `puede_esperar`: es una situacion esperada, no una falla."""
        response = self.como(self.paciente).post(
            reverse("clinica:turno-solicitar"),
            {
                "especialidad": self.especialidad.id,
                "inicio": self.futuro_en_agenda(hora=22).isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()["puede_esperar"])

    def test_no_se_solicita_en_el_pasado(self):
        ayer = timezone.now() - timedelta(days=1)
        response = self.como(self.paciente).post(
            reverse("clinica:turno-solicitar"),
            {"especialidad": self.especialidad.id, "inicio": ayer.isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_recepcion_no_solicita_turnos(self):
        manana = timezone.now() + timedelta(days=1)
        response = self.como(self.recepcion).post(
            reverse("clinica:turno-solicitar"),
            {"especialidad": self.especialidad.id, "inicio": manana.isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# Consultas a recepcion
# ---------------------------------------------------------------------------


class TestConsultas(BaseClinica):
    def test_paciente_pregunta(self):
        response = self.como(self.paciente).post(
            reverse("clinica:consulta-lista"), {"mensaje": "Coversami obra social?"}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["estado"], "abierta")

    def test_mensaje_vacio_se_rechaza(self):
        response = self.como(self.paciente).post(
            reverse("clinica:consulta-lista"), {"mensaje": "   "}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_recepcion_ve_las_abiertas_arriba(self):
        abierta = ConsultaRecepcion.objects.create(
            paciente=self.paciente, mensaje="Consulta abierta"
        )
        respondida = ConsultaRecepcion.objects.create(
            paciente=self.paciente,
            mensaje="Consulta vieja",
            respuesta="Ya",
            estado=ConsultaRecepcion.Estado.RESPONDIDA,
        )
        response = self.como(self.recepcion).get(reverse("clinica:consulta-lista"))
        self.assertEqual(response.json()[0]["id"], abierta.id)

    def test_recepcion_responde(self):
        consulta = ConsultaRecepcion.objects.create(
            paciente=self.paciente, mensaje="Obra social?"
        )
        response = self.como(self.recepcion).post(
            reverse("clinica:consulta-responder", args=[consulta.id]),
            {"respuesta": "Si, la cubre PAMI."},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["estado"], "respondida")

    def test_no_se_responde_dos_veces(self):
        consulta = ConsultaRecepcion.objects.create(
            paciente=self.paciente,
            mensaje="Obra social?",
            respuesta="Ya",
            estado=ConsultaRecepcion.Estado.RESPONDIDA,
        )
        response = self.como(self.recepcion).post(
            reverse("clinica:consulta-responder", args=[consulta.id]),
            {"respuesta": "Otra vez"},
            format="json",
        )
        self.assertEqual(response.status_code, 409)

    def test_paciente_no_responde_su_consulta(self):
        consulta = ConsultaRecepcion.objects.create(
            paciente=self.paciente, mensaje="Hola"
        )
        response = self.como(self.paciente).post(
            reverse("clinica:consulta-responder", args=[consulta.id]),
            {"respuesta": "Me respondo solo"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_paciente_no_ve_consultas_de_otros(self):
        ConsultaRecepcion.objects.create(paciente=self.otro_paciente, mensaje="Ajena")
        response = self.como(self.paciente).get(reverse("clinica:consulta-lista"))
        self.assertEqual(len(response.json()), 0)


# ---------------------------------------------------------------------------
# Cola de espera
# ---------------------------------------------------------------------------


class TestColaDeEspera(BaseClinica):
    def test_paciente_se_encola(self):
        response = self.como(self.paciente).post(
            reverse("clinica:cola-lista"),
            {"especialidad": self.especialidad.id, "nota": "Urgente por dolor"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["prioridad"], 0)

    def test_no_se_encola_dos_veces_en_la_misma_especialidad(self):
        """El constraint parcial de la base lo cubre; aca se evita el 500."""
        ColaEspera.objects.create(
            paciente=self.paciente, especialidad=self.especialidad
        )
        response = self.como(self.paciente).post(
            reverse("clinica:cola-lista"),
            {"especialidad": self.especialidad.id},
            format="json",
        )
        self.assertEqual(response.status_code, 409)

    def test_no_se_encola_a_medico_de_otra_especialidad(self):
        otro_medico = PerfilMedico.objects.create(
            user=User.objects.create_user(
                email="derma@clinica.com", password=PASSWORD, rol=Role.MEDICO
            ),
            especialidad=self.otra_especialidad,
            matricula="MP-555",
        )
        response = self.como(self.paciente).post(
            reverse("clinica:cola-lista"),
            {"especialidad": self.especialidad.id, "medico": otro_medico.id},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_cola_ordena_urgente_primero(self):
        normal = ColaEspera.objects.create(paciente=self.paciente, especialidad=self.especialidad)
        urgente = ColaEspera.objects.create(
            paciente=self.otro_paciente,
            especialidad=self.otra_especialidad,
            prioridad=ColaEspera.Prioridad.URGENTE,
        )
        response = self.como(self.recepcion).get(reverse("clinica:cola-lista"))
        self.assertEqual(response.json()[0]["id"], urgente.id)

    def test_recepcion_agenda_desde_la_cola(self):
        entrada = ColaEspera.objects.create(
            paciente=self.paciente, especialidad=self.especialidad, nota="Dolor"
        )
        response = self.como(self.recepcion).post(
            reverse("clinica:cola-agendar", args=[entrada.id]),
            {"inicio": self.futuro_en_agenda().isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["turno"]["estado"], Turno.Estado.CONFIRMADO)
        entrada.refresh_from_db()
        self.assertEqual(entrada.estado, ColaEspera.Estado.AGENDADO)

    def test_agendar_crea_el_turno_y_cierra_la_entrada_juntos(self):
        """Si un paso fallara solo, quedaria una cola que miente."""
        entrada = ColaEspera.objects.create(
            paciente=self.paciente, especialidad=self.especialidad
        )
        self.como(self.recepcion).post(
            reverse("clinica:cola-agendar", args=[entrada.id]),
            {"inicio": self.futuro_en_agenda().isoformat()},
            format="json",
        )
        self.assertEqual(Turno.objects.count(), 1)
        entrada.refresh_from_db()
        self.assertIsNotNone(entrada.turno_id)

    def test_no_se_agenda_a_horario_ocupado(self):
        """Ya hay un turno ese dia, asi que la cola no se puede cerrar."""
        entrada = ColaEspera.objects.create(
            paciente=self.paciente, especialidad=self.especialidad
        )
        inicio = self.futuro_en_agenda()
        Turno.objects.create(
            paciente=self.otro_paciente,
            medico=self.medico,
            especialidad=self.especialidad,
            inicio=inicio,
            fin=inicio + timedelta(minutes=30),
            estado=Turno.Estado.CONFIRMADO,
        )
        response = self.como(self.recepcion).post(
            reverse("clinica:cola-agendar", args=[entrada.id]),
            {"inicio": inicio.isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        entrada.refresh_from_db()
        self.assertEqual(entrada.estado, ColaEspera.Estado.ESPERANDO)

    def test_recepcion_cambia_prioridad(self):
        entrada = ColaEspera.objects.create(
            paciente=self.paciente, especialidad=self.especialidad
        )
        response = self.como(self.recepcion).patch(
            reverse("clinica:cola-prioridad", args=[entrada.id]),
            {"prioridad": 10},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["prioridad"], 10)

    def test_paciente_sale_de_la_cola(self):
        entrada = ColaEspera.objects.create(
            paciente=self.paciente, especialidad=self.especialidad
        )
        response = self.como(self.paciente).delete(
            reverse("clinica:cola-detalle", args=[entrada.id])
        )
        self.assertEqual(response.status_code, 204)
        entrada.refresh_from_db()
        self.assertEqual(entrada.estado, ColaEspera.Estado.DESCARTADO)

    def test_cancelar_un_turno_devuelve_la_entrada_a_la_cola(self):
        """Si no, recepcion tiene que recargar la entrada a mano."""
        entrada = ColaEspera.objects.create(
            paciente=self.paciente, especialidad=self.especialidad
        )
        r = self.como(self.recepcion).post(
            reverse("clinica:cola-agendar", args=[entrada.id]),
            {"inicio": self.futuro_en_agenda().isoformat()},
            format="json",
        )
        # Si el agendar falla, el `get()` de abajo revienta con DoesNotExist y
        # el error real del POST se pierde. Por eso se comprueba antes.
        self.assertEqual(r.status_code, 201, r.json())
        turno = Turno.objects.get()
        self.como(self.recepcion).patch(
            reverse("clinica:turno-detalle", args=[turno.id]),
            {"estado": "cancelado"},
            format="json",
        )
        entrada.refresh_from_db()
        self.assertEqual(entrada.estado, ColaEspera.Estado.ESPERANDO)
        self.assertIsNone(entrada.turno_id)


# ---------------------------------------------------------------------------
# Ficha medica
# ---------------------------------------------------------------------------


class TestFichaMedicaAPI(BaseClinica):
    def test_paciente_ve_su_ficha(self):
        response = self.como(self.paciente).get(reverse("clinica:ficha-mia"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["alergias"], "Ninguna")

    def test_paciente_no_puede_auto_verificar(self):
        """Si pudiera, el sello de 'verificada' no significaria nada."""
        self.paciente.ficha_medica.alergias = "Penicilina"
        self.paciente.ficha_medica.save()
        response = self.como(self.paciente).patch(
            reverse("clinica:ficha-mia"),
            {"alergias": "Penicilina", "verificada_por": self.medico_user.id},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["verificada"])

    def test_editar_deja_la_ficha_sin_verificar(self):
        ficha = self.paciente.ficha_medica
        ficha.verificada_por = self.medico_user
        ficha.save()
        self.como(self.paciente).patch(
            reverse("clinica:ficha-mia"), {"alergias": "Polen"}, format="json"
        )
        ficha.refresh_from_db()
        self.assertFalse(ficha.verificada)

    def test_medico_verifica_la_ficha(self):
        response = self.como(self.medico_user).post(
            reverse("clinica:paciente-ficha", args=[self.paciente.id]),
            {"nota": "Coincide con lo que declara"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["verificada"])

    def test_recepcion_no_verifica_fichas(self):
        """Verificar es una tarea clinica, no administrativa."""
        response = self.como(self.recepcion).post(
            reverse("clinica:paciente-ficha", args=[self.paciente.id]),
            {"nota": "Dale"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_medico_solo_ve_fichas_de_sus_pacientes(self):
        response = self.como(self.medico_user).get(
            reverse("clinica:paciente-ficha", args=[self.otro_paciente.id]),
            {"solo_mis_pacientes": "1"},
        )
        self.assertEqual(response.status_code, 403)

    def test_medico_ve_la_ficha_de_alguien_con_turno(self):
        self.turno_valido()
        response = self.como(self.medico_user).get(
            reverse("clinica:paciente-ficha", args=[self.paciente.id]),
            {"solo_mis_pacientes": "1"},
        )
        self.assertEqual(response.status_code, 200)

    def test_paciente_no_abre_la_ficha_de_otro(self):
        response = self.como(self.paciente).get(
            reverse("clinica:paciente-ficha", args=[self.otro_paciente.id])
        )
        self.assertIn(response.status_code, (403, 404))


# ---------------------------------------------------------------------------
# Resumen de panel
# ---------------------------------------------------------------------------


class TestPanelResumen(BaseClinica):
    def turno_de_hoy(self, hora=10):
        """
        Turno para hoy, a diferencia de `turno_valido` que usa la fecha fija.

        El resumen cuenta `inicio__date=hoy`, asi que un turno en DIA (2026-06-15)
        no apareceria en el conteo salvo que hoy fuera ese dia.
        """
        inicio = timezone.make_aware(
            timezone.datetime.combine(
                timezone.localtime().date(), time(hora, 0)
            )
        )
        return Turno.objects.create(
            paciente=self.paciente,
            medico=self.medico,
            especialidad=self.especialidad,
            inicio=inicio,
            fin=inicio + timedelta(minutes=30),
            estado=Turno.Estado.PENDIENTE,
        )

    def test_resumen_de_recepcion(self):
        self.turno_de_hoy()
        ConsultaRecepcion.objects.create(paciente=self.paciente, mensaje="Hola")
        response = self.como(self.recepcion).get(reverse("clinica:panel-resumen"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["turnos_hoy"], 1)
        self.assertEqual(response.json()["consultas_abiertas"], 1)

    def test_resumen_de_medico(self):
        self.turno_de_hoy()
        response = self.como(self.medico_user).get(reverse("clinica:panel-resumen"))
        self.assertEqual(response.json()["turnos_hoy"], 1)
        self.assertEqual(response.json()["estado"], "libre")

    def test_resumen_de_paciente(self):
        self.turno_valido(inicio=timezone.now() + timedelta(days=1), fin=timezone.now() + timedelta(days=1, minutes=30))
        response = self.como(self.paciente).get(reverse("clinica:panel-resumen"))
        self.assertEqual(response.json()["turnos_proximos"], 1)
        self.assertFalse(response.json()["ficha_verificada"])

    def test_resumen_de_admin(self):
        response = self.como(self.admin).get(reverse("clinica:panel-resumen"))
        self.assertEqual(response.json()["especialidades"], 2)
        self.assertEqual(response.json()["medicos"], 1)


class TestBusquedaDePacientes(BaseClinica):
    """
    El buscador que usa recepcion para elegir a quien agendar un turno.
    """

    def test_recepcion_busca_por_nombre_o_email(self):
        response = self.como(self.recepcion).get(
            reverse("clinica:paciente-busqueda"), {"q": self.paciente.email.split("@")[0]}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["id"], str(self.paciente.id))

    def test_devuelve_el_uuid_que_acepta_el_alta_de_turno(self):
        """
        El id devuelto tiene que servir tal cual en `POST /api/turnos`.

        Es el contrato entre el buscador y el alta: si uno devuelve el UUID y el
        otro espera otra cosa, la pantalla no puede agendar sin intervening al
        usuario.
        """
        encontrado = self.como(self.recepcion).get(
            reverse("clinica:paciente-busqueda"), {"q": "paciente"}
        ).json()[0]

        response = self.como(self.recepcion).post(
            reverse("clinica:turno-lista"),
            {
                "paciente": encontrado["id"],
                "medico": self.medico.id,
                "inicio": self.instante().isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)

    def test_texto_muy_corto_no_devuelve_toda_la_base(self):
        response = self.como(self.recepcion).get(reverse("clinica:paciente-busqueda"), {"q": "a"})
        self.assertEqual(response.json(), [])

    def test_paciente_no_busca_pacientes(self):
        response = self.como(self.paciente).get(reverse("clinica:paciente-busqueda"), {"q": "pac"})
        self.assertEqual(response.status_code, 403)

    def test_el_medico_si_puede(self):
        """
        Los medicos son "staff" y pueden buscar.

        Es la misma politica que ya aplica `MedicoAgendaView`: el profesional ve
        la agenda de otro porque no filtra datos del paciente. Ver el listado de
        pacientes es el mismo nivel de dato, y en el mostrador es quien
        identifica a quien tiene el turno.
        """
        response = self.como(self.medico_user).get(
            reverse("clinica:paciente-busqueda"), {"q": "pac"}
        )
        self.assertEqual(response.status_code, 200)


class TestListadoDeUsuarios(BaseClinica):
    """El listado de cuentas que muestra el panel del admin."""

    def test_admin_ve_las_cuentas(self):
        response = self.como(self.admin).get(reverse("auth-usuarios"))
        self.assertEqual(response.status_code, 200)
        emails = [u["email"] for u in response.json()]
        self.assertIn(self.admin.email, emails)
        self.assertIn(self.paciente.email, emails)

    def test_filtra_por_rol(self):
        response = self.como(self.admin).get(reverse("auth-usuarios"), {"rol": "medico"})
        self.assertEqual([u["email"] for u in response.json()], [self.medico_user.email])

    def test_rol_invalido_da_400(self):
        response = self.como(self.admin).get(reverse("auth-usuarios"), {"rol": "jefe"})
        self.assertEqual(response.status_code, 400)

    def test_recepcion_no_lista_usuarios(self):
        response = self.como(self.recepcion).get(reverse("auth-usuarios"))
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# Urgencias
# ---------------------------------------------------------------------------


class TestUrgencias(BaseClinica):
    """Guardia: registro, estados y derivacion a una consulta."""

    def urgencia(self, **overrides):
        data = {
            "paciente": self.paciente,
            "motivo": "Dolor de pecho",
            "triaje": Urgencia.Triaje.URGENTE,
            "estado": Urgencia.Estado.REGISTRADA,
        }
        data.update(overrides)
        return Urgencia.objects.create(**data)

    def test_paciente_registra_su_propia_urgencia(self):
        response = self.como(self.paciente).post(
            reverse("clinica:urgencia-lista"),
            {"motivo": "Me caí", "triaje": 10},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["paciente"], "Ana Perez")
        self.assertEqual(response.json()["estado"], "registrada")

    def test_paciente_no_puede_registrar_para_otro(self):
        """Aunque mande un `paciente`, la urgencia queda a su nombre."""
        response = self.como(self.paciente).post(
            reverse("clinica:urgencia-lista"),
            {"paciente": str(self.otro_paciente.id), "motivo": "Fiebre"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Urgencia.objects.get().paciente_id, self.paciente.id)

    def test_recepcion_registra_para_un_paciente(self):
        response = self.como(self.recepcion).post(
            reverse("clinica:urgencia-lista"),
            {"paciente": str(self.paciente.id), "motivo": "Se desmayó", "triaje": 20},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["triaje"], 20)

    def test_recepcion_sin_paciente_da_400(self):
        response = self.como(self.recepcion).post(
            reverse("clinica:urgencia-lista"), {"motivo": "Algo"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_motivo_vacio_se_rechaza(self):
        response = self.como(self.paciente).post(
            reverse("clinica:urgencia-lista"), {"motivo": "   "}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_por_defecto_no_lista_las_cerradas(self):
        self.urgencia()
        self.urgencia(estado=Urgencia.Estado.CERRADA, triaje=Urgencia.Triaje.NO_URGENTE)
        response = self.como(self.recepcion).get(reverse("clinica:urgencia-lista"))
        self.assertEqual(len(response.json()), 1)

    def test_todas_incluye_las_cerradas(self):
        self.urgencia()
        self.urgencia(estado=Urgencia.Estado.CERRADA, triaje=Urgencia.Triaje.NO_URGENTE)
        response = self.como(self.recepcion).get(
            reverse("clinica:urgencia-lista"), {"todas": "1"}
        )
        self.assertEqual(len(response.json()), 2)

    def test_paciente_solo_ve_las_suyas(self):
        self.urgencia(estado=Urgencia.Estado.EN_ESPERA)
        self.urgencia(paciente=self.otro_paciente)
        response = self.como(self.paciente).get(reverse("clinica:urgencia-lista"))
        self.assertEqual(len(response.json()), 1)

    def test_recepcion_avanza_el_estado(self):
        u = self.urgencia()
        response = self.como(self.recepcion).patch(
            reverse("clinica:urgencia-detalle", args=[u.id]),
            {"estado": "en_atencion"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        u.refresh_from_db()
        self.assertEqual(u.estado, Urgencia.Estado.EN_ATENCION)

    def test_transicion_invalida_da_400(self):
        u = self.urgencia()
        response = self.como(self.recepcion).patch(
            reverse("clinica:urgencia-detalle", args=[u.id]),
            {"estado": "derivada"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_recepcion_reclasifica_el_triaje(self):
        u = self.urgencia()
        response = self.como(self.recepcion).patch(
            reverse("clinica:urgencia-detalle", args=[u.id]),
            {"estado": "en_espera", "triaje": 20},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        u.refresh_from_db()
        self.assertEqual(u.triaje, Urgencia.Triaje.EMERGENCIA)

    def test_paciente_solo_puede_cerrar_la_suya(self):
        u = self.urgencia(estado=Urgencia.Estado.EN_ESPERA)
        response = self.como(self.paciente).patch(
            reverse("clinica:urgencia-detalle", args=[u.id]),
            {"estado": "en_atencion"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_paciente_cierra_la_suya_y_queda_cerrada_en(self):
        u = self.urgencia(estado=Urgencia.Estado.EN_ESPERA)
        response = self.como(self.paciente).patch(
            reverse("clinica:urgencia-detalle", args=[u.id]),
            {"estado": "cerrada"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        u.refresh_from_db()
        self.assertIsNotNone(u.cerrada_en)

    def test_paciente_no_ve_la_urgencia_de_otro(self):
        u = self.urgencia(paciente=self.otro_paciente)
        response = self.como(self.paciente).get(
            reverse("clinica:urgencia-detalle", args=[u.id])
        )
        self.assertEqual(response.status_code, 404)

    def test_el_medico_no_entra_al_modulo_de_urgencias(self):
        response = self.como(self.medico_user).get(reverse("clinica:urgencia-lista"))
        self.assertEqual(response.status_code, 403)

    def test_recepcion_deriva_y_genera_el_turno(self):
        u = self.urgencia(estado=Urgencia.Estado.EN_ATENCION)
        response = self.como(self.recepcion).post(
            reverse("clinica:urgencia-derivar", args=[u.id]),
            {"medico": self.medico.id, "inicio": self.futuro_en_agenda(11).isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        u.refresh_from_db()
        self.assertEqual(u.estado, Urgencia.Estado.DERIVADA)
        self.assertIsNotNone(u.turno_id)
        self.assertEqual(u.turno.paciente_id, u.paciente_id)

    def test_derivar_a_horario_ocupado_da_409(self):
        u = self.urgencia(estado=Urgencia.Estado.EN_ATENCION)
        inicio = self.futuro_en_agenda(11)
        self.turno_valido(inicio=inicio, fin=inicio + timedelta(minutes=30))
        response = self.como(self.recepcion).post(
            reverse("clinica:urgencia-derivar", args=[u.id]),
            {"medico": self.medico.id, "inicio": inicio.isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 409)

    def test_paciente_no_deriva(self):
        u = self.urgencia(estado=Urgencia.Estado.EN_ATENCION)
        response = self.como(self.paciente).post(
            reverse("clinica:urgencia-derivar", args=[u.id]),
            {"medico": self.medico.id, "inicio": self.futuro_en_agenda(11).isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_derivar_una_cerrada_da_409(self):
        u = self.urgencia(estado=Urgencia.Estado.CERRADA)
        response = self.como(self.recepcion).post(
            reverse("clinica:urgencia-derivar", args=[u.id]),
            {"medico": self.medico.id, "inicio": self.futuro_en_agenda(11).isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 409)

    # --- Datos del paciente en la urgencia ---

    def test_detalle_incluye_los_datos_del_paciente(self):
        PerfilPaciente.objects.create(
            user=self.paciente,
            documento="30111222",
            fecha_nacimiento=date(1990, 5, 20),
            direccion="Calle 1",
            obra_social="OSDE",
        )
        u = self.urgencia()
        response = self.como(self.recepcion).get(
            reverse("clinica:urgencia-detalle", args=[u.id])
        )
        self.assertEqual(response.status_code, 200)
        detalle = response.json()["paciente_detalle"]
        self.assertEqual(detalle["nombre"], "Ana Perez")
        self.assertEqual(detalle["documento"], "30111222")
        self.assertEqual(detalle["fecha_nacimiento"], "1990-05-20")
        self.assertEqual(detalle["direccion"], "Calle 1")
        self.assertEqual(detalle["obra_social"], "OSDE")

    def test_detalle_sin_perfil_no_rompe(self):
        u = self.urgencia()
        response = self.como(self.recepcion).get(
            reverse("clinica:urgencia-detalle", args=[u.id])
        )
        detalle = response.json()["paciente_detalle"]
        self.assertIsNone(detalle["documento"])
        self.assertIsNone(detalle["fecha_nacimiento"])
        self.assertEqual(detalle["direccion"], "")
        self.assertEqual(detalle["obra_social"], "")

    # --- Validaciones y mensajes de error ---

    def test_listado_con_estado_invalido_da_400(self):
        response = self.como(self.recepcion).get(
            reverse("clinica:urgencia-lista"), {"estado": "volando"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("estado", response.json()["errors"])

    def test_falta_motivo_da_400_con_mensaje(self):
        response = self.como(self.paciente).post(
            reverse("clinica:urgencia-lista"), {}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("motivo", response.json()["errors"])

    def test_triaje_invalido_da_400(self):
        response = self.como(self.paciente).post(
            reverse("clinica:urgencia-lista"),
            {"motivo": "Fiebre", "triaje": 99},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("triaje", response.json()["errors"])

    def test_paciente_no_puede_cambiar_el_triaje(self):
        u = self.urgencia(estado=Urgencia.Estado.EN_ESPERA)
        response = self.como(self.paciente).patch(
            reverse("clinica:urgencia-detalle", args=[u.id]),
            {"estado": "cerrada", "triaje": 20},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("triaje", response.json()["errors"])

    def test_transicion_invalida_informa_los_estados_validos(self):
        u = self.urgencia()  # registrada
        response = self.como(self.recepcion).patch(
            reverse("clinica:urgencia-detalle", args=[u.id]),
            {"estado": "derivada"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "Estados validos desde aca", response.json()["errors"]["estado"][0]
        )

    def test_derivar_sin_medico_da_400(self):
        u = self.urgencia(estado=Urgencia.Estado.EN_ATENCION)
        response = self.como(self.recepcion).post(
            reverse("clinica:urgencia-derivar", args=[u.id]),
            {"inicio": self.futuro_en_agenda(11).isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("medico", response.json()["errors"])

    def test_derivar_sin_inicio_da_400(self):
        u = self.urgencia(estado=Urgencia.Estado.EN_ATENCION)
        response = self.como(self.recepcion).post(
            reverse("clinica:urgencia-derivar", args=[u.id]),
            {"medico": self.medico.id},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("inicio", response.json()["errors"])

    def test_observaciones_demasiado_largas_dan_400(self):
        response = self.como(self.paciente).post(
            reverse("clinica:urgencia-lista"),
            {"motivo": "Fiebre", "observaciones": "x" * 2001},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("observaciones", response.json()["errors"])