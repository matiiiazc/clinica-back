"""
Datos de demostracion para probar los paneles con contenido real.

Los paneles se ven vacios sin esto: no hay profesionales, ni agenda, ni turnos.
Crea el minimo para que un paciente pueda pedir turno, un medico tenga agenda, y
recepcion tenga algo que agendar y responder.

No es una migracion ni un seed de produccion: es un comando de desarrollo, y
por eso pide `--si` antes de tocar nada. Si corre dos veces no rompe nada, pero
tampoco duplica: usa `get_or_create` y saltea lo que ya existe.

Los turnos se generan **relativos a hoy** (tres semanas para atras, tres para
adelante): los del pasado quedan atendidos o ausentes y los del futuro
pendientes o confirmados. Si se generaran con fechas fijas, a los pocos dias la
agenda quedaria entera en el pasado y los paneles se verian vacios.

Uso:
    python manage.py datos_demo                 # muestra el plan, no escribe
    python manage.py datos_demo --si            # escribe
    python manage.py datos_demo --si --reset    # borra lo demo y lo rehace
"""

from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Role, User
from apps.clinica.models import (
    Agenda,
    Bloqueo,
    ColaEspera,
    ConsultaRecepcion,
    Especialidad,
    FichaMedica,
    PerfilMedico,
    Turno,
)
from apps.clinica.services import duracion_de

CONTRASENA = "Clinica2026!Demo"

#: Cuantos dias hacia atras y hacia adelante se agendan turnos, contando hoy.
DIAS_ATRAS = 21
DIAS_ADELANTE = 21

ESPECIALIDADES = [
    ("Cardiología", "Enfermedad cardiovascular e hipertensión.", 30),
    ("Dermatología", "Piel, pelo y uñas.", 30),
    ("Pediatría", "Atención de niños y adolescentes.", 30),
    ("Clínica General", "Consultas generales y control de salud.", 20),
]

# (nombre, email, matricula, especialidad, estado, [(dia_semana, desde, hasta)])
# Los dias van de 0=lunes a 6=domingo, igual que `DIAS_SEMANA` en models.py.
MEDICOS = [
    (
        "Laura Gómez",
        "medico.cardiologia@clinica.local",
        "MP-1001",
        "Cardiología",
        PerfilMedico.Estado.LIBRE,
        [(0, "09:00", "13:00"), (2, "09:00", "13:00"), (4, "09:00", "13:00")],
    ),
    (
        "Diego Fernández",
        "medico.dermatologia@clinica.local",
        "MP-1002",
        "Dermatología",
        PerfilMedico.Estado.LIBRE,
        [(1, "10:00", "14:00"), (3, "10:00", "14:00")],
    ),
    (
        "Sofía Ruiz",
        "medico.pediatria@clinica.local",
        "MP-1003",
        "Pediatría",
        PerfilMedico.Estado.ATENDIENDO,
        [(0, "08:00", "12:00"), (1, "08:00", "12:00"), (4, "08:00", "12:00")],
    ),
    (
        "Martín Díaz",
        "medico.general@clinica.local",
        "MP-1004",
        "Clínica General",
        PerfilMedico.Estado.LIBRE,
        [(1, "15:00", "19:00"), (5, "15:00", "19:00")],
    ),
]

# (nombre, email, telefono)
PACIENTES = [
    ("Ana Torres", "paciente.ana@correo.local", "11-5555-0001"),
    ("Bruno Díaz", "paciente.bruno@correo.local", "11-5555-0002"),
    ("Carla Méndez", "paciente.carla@correo.local", "11-5555-0003"),
    ("Diego Paola", "paciente.diego@correo.local", "11-5555-5555"),
]

# (email, alergias, enfermedades_cronicas, medicacion, observaciones, verifica)
# `verificar_con` es el email del profesional que firma la ficha; con None la
# ficha queda sin verificar, que es como llega una ficha nueva de verdad.
FICHAS = [
    (
        "paciente.ana@correo.local",
        "Penicilina",
        "Hipertensión arterial",
        "Enalapril 10 mg por día",
        "Control de presión cada tres meses.",
        "medico.cardiologia@clinica.local",
    ),
    (
        "paciente.bruno@correo.local",
        "Ninguna",
        "Ninguna",
        "",
        "Sin antecedentes relevantes.",
        "medico.general@clinica.local",
    ),
    (
        "paciente.carla@correo.local",
        "Ibuprofeno",
        "Asma bronquial",
        "Salbutamol a demanda",
        "Evitar AINEs. Crisis estacionales.",
        None,
    ),
    (
        "paciente.diego@correo.local",
        "Ninguna",
        "Diabetes tipo 2",
        "Metformina 850 mg",
        "Pendiente de revisión por el profesional.",
        None,
    ),
]

# (matricula, desde_en_dias, hasta_en_dias, motivo). Los dias son relativos a
# hoy: asi el bloqueo cae siempre en la agenda que se esta por llenar.
BLOQUEOS = [
    ("MP-1002", 7, 11, "Vacaciones"),
    ("MP-1004", 3, 3, "Capacitación"),
]

MOTIVOS = {
    "Cardiología": ["Control de presión", "Dolor en el pecho", "Seguimiento"],
    "Dermatología": ["Control de lunares", "Erupción en la piel", "Manchas"],
    "Pediatría": ["Control del niño sano", "Fiebre", "Vacunación"],
    "Clínica General": ["Consulta general", "Certificado médico", "Chequeo anual"],
}

# (email_paciente, mensaje, respuesta_o_None, email_responde)
CONSULTAS = [
    (
        "paciente.ana@correo.local",
        "¿La consulta cardiológica entra por mi obra social?",
        None,
        None,
    ),
    (
        "paciente.bruno@correo.local",
        "¿Puedo pedir un turno para la semana que viene a la tarde?",
        None,
        None,
    ),
    (
        "paciente.diego@correo.local",
        "¿Necesito ayuno para el control de diabetes?",
        "Sí, 8 horas de ayuno. Podés tomar agua.",
        "recepcion.demo@clinica.local",
    ),
]

# (email_paciente, especialidad, prioridad, nota)
COLA = [
    ("paciente.carla@correo.local", "Cardiología", ColaEspera.Prioridad.URGENTE, "Dolor en el pecho."),
    ("paciente.diego@correo.local", "Dermatología", ColaEspera.Prioridad.NORMAL, ""),
    ("paciente.ana@correo.local", "Pediatría", ColaEspera.Prioridad.NORMAL, "Turno para su hijo."),
]

#: Cuentas y pacientes que crea este comando. Es lo unico que `--reset` borra.
DEMO_EMAILS = [m[1] for m in MEDICOS] + [p[1] for p in PACIENTES] + [
    "recepcion.demo@clinica.local",
    "admin.demo@clinica.local",
]


class Command(BaseCommand):
    help = "Datos de demostración para probar los paneles con contenido real."

    def add_arguments(self, parser):
        parser.add_argument(
            "--si",
            action="store_true",
            help="Confirma la escritura.",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Borra los datos de demostración anteriores antes de recrearlos.",
        )

    def handle(self, *args, **opciones):
        escribir = opciones["si"]
        reset = opciones["reset"]

        if not escribir:
            self.describir_plan(reset=reset)
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("Volver a correr con --si para escribir."))
            return

        with transaction.atomic():
            # Todo dentro de una transaccion: si algo falla a mitad de camino,
            # la base queda como estaba. Un seed a medias deja datos que nadie
            # sabe de donde salieron.
            if reset:
                self.limpiar()
            self.crear_especialidades()
            self.crear_staff()
            self.crear_profesionales()
            self.crear_pacientes()
            self.crear_fichas()
            self.crear_bloqueos()
            self.crear_turnos()
            self.crear_consultas()
            self.crear_cola()

        self.resumen()

    # --- Plan y limpieza --------------------------------------------------

    def describir_plan(self, reset=False):
        self.stdout.write("Se va a crear:")
        self.stdout.write(f"  - {len(ESPECIALIDADES)} especialidades")
        self.stdout.write(f"  - {len(MEDICOS)} profesionales con agenda")
        self.stdout.write("  - 1 recepción y 1 administrador")
        self.stdout.write(f"  - {len(PACIENTES)} pacientes con ficha médica")
        self.stdout.write(f"  - {len(BLOQUEOS)} bloqueos de agenda")
        self.stdout.write(
            f"  - turnos de {DIAS_ATRAS} días atrás a {DIAS_ADELANTE} días adelante"
        )
        self.stdout.write(f"  - {len(CONSULTAS)} consultas y {len(COLA)} entradas de cola")
        self.stdout.write("")
        self.stdout.write(f"Todos con la contraseña: {CONTRASENA}")
        if reset:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING("--reset: se borran los datos demo anteriores.")
            )
        elif self.hay_datos():
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "Ya hay datos demo. Sin --reset se agrega lo que falte, pero "
                    "los turnos viejos (ya pasados) se quedan como están."
                )
            )

    def limpiar(self):
        """Borra solo lo que crea este comando.

        No toca cuentas reales ni especialidades: esas las puede estar usando
        otra parte del sistema. El orden respeta las FK PROTECT de Turno.
        """
        usuarios = User.objects.filter(email__in=DEMO_EMAILS)
        Turno.objects.filter(medico__user__in=usuarios).delete()
        Turno.objects.filter(paciente__in=usuarios).delete()
        ColaEspera.objects.filter(paciente__in=usuarios).delete()
        ConsultaRecepcion.objects.filter(paciente__in=usuarios).delete()
        Bloqueo.objects.filter(medico__user__in=usuarios).delete()
        Agenda.objects.filter(medico__user__in=usuarios).delete()
        FichaMedica.objects.filter(user__in=usuarios).delete()
        PerfilMedico.objects.filter(user__in=usuarios).delete()
        usuarios.delete()
        self.stdout.write("Datos de demostración anteriores borrados.")

    def hay_datos(self):
        return User.objects.filter(email__in=DEMO_EMAILS).exists()

    # --- Escribientes -----------------------------------------------------

    def crear_especialidades(self):
        for nombre, descripcion, duracion in ESPECIALIDADES:
            Especialidad.objects.get_or_create(
                nombre=nombre,
                defaults={
                    "descripcion": descripcion,
                    "duracion_turno_minutos": duracion,
                },
            )
        self.stdout.write(f"{len(ESPECIALIDADES)} especialidades listas.")

    def crear_staff(self):
        """Recepción y admin.

        Estas cuentas no pasan por el registro público a propósito: el rol lo
        decide la clínica, no el que se anota. Por eso quedan verificadas y sin
        código pendiente.
        """
        for nombre, email, rol in [
            ("Recepción Demo", "recepcion.demo@clinica.local", Role.RECEPCION),
            ("Administración Demo", "admin.demo@clinica.local", Role.ADMIN),
        ]:
            self.crear_usuario(email, nombre=nombre, rol=rol, verificado=True)

    def crear_profesionales(self):
        for nombre, email, matricula, especialidad, estado, tramos in MEDICOS:
            usuario = self.crear_usuario(
                email, nombre=nombre, rol=Role.MEDICO, verificado=True
            )
            perfil, creado = PerfilMedico.objects.get_or_create(
                user=usuario,
                matricula=matricula,
                defaults={
                    "especialidad": Especialidad.objects.get(nombre=especialidad),
                    "estado": estado,
                },
            )
            if not creado:
                continue
            for dia, desde, hasta in tramos:
                Agenda.objects.create(
                    medico=perfil,
                    dia_semana=dia,
                    hora_inicio=desde,
                    hora_fin=hasta,
                )
        self.stdout.write(f"{len(MEDICOS)} profesionales con agenda.")

    def crear_pacientes(self):
        for nombre, email, telefono in PACIENTES:
            self.crear_usuario(
                email,
                nombre=nombre,
                rol=Role.PACIENTE,
                telefono=telefono,
                verificado=True,
            )
        self.stdout.write(f"{len(PACIENTES)} pacientes.")

    def crear_fichas(self):
        for email, alergias, cronicas, medicacion, observaciones, verifica in FICHAS:
            usuario = User.objects.filter(email=email).first()
            if usuario is None:
                continue
            ficha, _ = FichaMedica.objects.get_or_create(
                user=usuario,
                defaults={
                    "alergias": alergias,
                    "enfermedades_cronicas": cronicas,
                    "medicacion": medicacion,
                    "observaciones": observaciones,
                },
            )
            if verifica and ficha.verificada_por_id is None:
                profesional = User.objects.filter(email=verifica).first()
                if profesional is not None:
                    ficha.verificada_por = profesional
                    ficha.verificada_en = timezone.now()
                    ficha.save(update_fields=["verificada_por", "verificada_en"])
        self.stdout.write("Fichas médicas (algunas verificadas).")

    def crear_usuario(self, email, nombre="", rol=Role.PACIENTE, telefono="", verificado=False):
        usuario, creado = User.objects.get_or_create(
            email=email,
            defaults={
                "nombre": nombre,
                "rol": rol,
                "telefono": telefono,
                "email_verified": verificado,
            },
        )
        if creado:
            usuario.set_password(CONTRASENA)
            usuario.save(update_fields=["password"])
        return usuario

    def crear_bloqueos(self):
        hoy = timezone.localdate()
        for matricula, desde, hasta, motivo in BLOQUEOS:
            perfil = PerfilMedico.objects.filter(matricula=matricula).first()
            if perfil is None:
                continue
            Bloqueo.objects.get_or_create(
                medico=perfil,
                desde=hoy + timedelta(days=desde),
                hasta=hoy + timedelta(days=hasta),
                defaults={"motivo": motivo},
            )
        self.stdout.write(f"{len(BLOQUEOS)} bloqueos de agenda.")

    def crear_turnos(self):
        """Agenda relativa a hoy, respetando días de atención y bloqueos.

        Los turnos del pasado quedan atendidos o ausentes; los del futuro,
        pendientes o confirmados. Así el panel muestra historia y a la vez tiene
        horarios libres para pedir turno.
        """
        ahora = timezone.localtime()
        tz = timezone.get_current_timezone()
        medicos = list(
            PerfilMedico.objects.select_related("user", "especialidad").order_by("matricula")
        )
        pacientes = self.pacientes()
        if not medicos or not pacientes:
            return

        creados = 0
        for indice, medico in enumerate(medicos):
            duracion = timedelta(minutes=duracion_de(medico))
            for delta in range(-DIAS_ATRAS, DIAS_ADELANTE + 1):
                dia = (ahora + timedelta(days=delta)).date()
                if self.esta_bloqueado(medico, dia):
                    continue
                tramos = medico.agendas.filter(
                    dia_semana=dia.weekday(), activo=True
                ).order_by("hora_inicio")
                for tramo in tramos:
                    slot = timezone.make_aware(datetime.combine(dia, tramo.hora_inicio), tz)
                    fin_tramo = timezone.make_aware(datetime.combine(dia, tramo.hora_fin), tz)
                    while slot + duracion <= fin_tramo:
                        if self.toca_ocupar(indice, slot) and not self.ocupado(
                            medico, slot, slot + duracion
                        ):
                            Turno.objects.create(
                                # Se rota el paciente por turno creado, no por
                                # dia: si no, el mismo paciente quedaria ocupando
                                # todos los horarios del mismo medico.
                                paciente=pacientes[creados % len(pacientes)],
                                medico=medico,
                                especialidad=medico.especialidad,
                                inicio=slot,
                                fin=slot + duracion,
                                estado=self.estado_para(slot, ahora, duracion),
                                motivo=self.motivo_para(medico.especialidad.nombre, slot),
                            )
                            creados += 1
                        slot += duracion

        self.stdout.write(f"{creados} turnos de demostración.")

    # --- Reglas de generación ---------------------------------------------

    @staticmethod
    def toca_ocupar(indice, slot):
        """Deja huecos libres: 2 de cada 3 horarios ocupados, siempre los mismos."""
        return (slot.day + slot.hour + indice) % 3 != 0

    @staticmethod
    def ocupado(medico, inicio, fin):
        """Si el profesional ya tiene un turno que pisa ese horario."""
        return Turno.objects.filter(
            medico=medico,
            estado__in=["pendiente", "confirmado", "atendido"],
            inicio__lt=fin,
            fin__gt=inicio,
        ).exists()

    @staticmethod
    def estado_para(slot, ahora, duracion):
        if slot + duracion <= ahora:
            # Ya pasó: se atendió, o el paciente no vino. Nunca queda pendiente.
            return (
                Turno.Estado.NO_ASISTIO
                if (slot.day + slot.hour) % 5 == 0
                else Turno.Estado.ATENDIDO
            )
        if slot <= ahora:
            return Turno.Estado.CONFIRMADO  # en curso
        return (
            Turno.Estado.PENDIENTE
            if (slot.day + slot.hour) % 3 == 0
            else Turno.Estado.CONFIRMADO
        )

    @staticmethod
    def motivo_para(especialidad, slot):
        motivos = MOTIVOS.get(especialidad) or ["Consulta general"]
        return motivos[(slot.day + slot.hour) % len(motivos)]

    @staticmethod
    def esta_bloqueado(medico, dia):
        return Bloqueo.objects.filter(
            medico=medico, desde__lte=dia, hasta__gte=dia
        ).exists()

    def crear_consultas(self):
        for email, mensaje, respuesta, responde in CONSULTAS:
            paciente = User.objects.filter(email=email).first()
            if paciente is None:
                continue
            consulta, _ = ConsultaRecepcion.objects.get_or_create(
                paciente=paciente,
                mensaje=mensaje,
                defaults={"estado": ConsultaRecepcion.Estado.ABIERTA},
            )
            if respuesta and consulta.estado == ConsultaRecepcion.Estado.ABIERTA:
                consulta.respuesta = respuesta
                consulta.respondida_por = User.objects.filter(email=responde).first()
                consulta.estado = ConsultaRecepcion.Estado.RESPONDIDA
                consulta.respondida_en = timezone.now()
                consulta.save()
        self.stdout.write(f"{len(CONSULTAS)} consultas a recepción.")

    def crear_cola(self):
        for email, especialidad, prioridad, nota in COLA:
            usuario = User.objects.filter(email=email).first()
            esp = Especialidad.objects.filter(nombre=especialidad).first()
            if usuario is None or esp is None:
                continue
            ColaEspera.objects.get_or_create(
                paciente=usuario,
                especialidad=esp,
                estado=ColaEspera.Estado.ESPERANDO,
                defaults={"prioridad": prioridad, "nota": nota},
            )
        self.stdout.write(f"{len(COLA)} entradas en cola de espera.")

    # --- Utilidades -------------------------------------------------------

    def pacientes(self):
        return list(
            User.objects.filter(
                email__in=[email for _, email, _ in PACIENTES]
            ).order_by("email")
        )

    def admin(self):
        return User.objects.filter(rol=Role.ADMIN).first()

    # --- Cierre -----------------------------------------------------------

    def resumen(self):
        admin = self.admin()
        pacientes = self.pacientes()

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Datos de demostración listos."))
        self.stdout.write("")
        self.stdout.write("Entrar con cualquiera de estas cuentas:")
        self.stdout.write(f"  admin.demo@clinica.local / {CONTRASENA}  (administrador)")
        self.stdout.write(f"  recepcion.demo@clinica.local / {CONTRASENA}  (recepción)")
        self.stdout.write(f"  {MEDICOS[0][1]} / {CONTRASENA}  (médico)")
        self.stdout.write(f"  {PACIENTES[0][1]} / {CONTRASENA}  (paciente)")
        self.stdout.write("")
        self.stdout.write("Token de la API, para probar con curl:")
        if admin:
            self.stdout.write(f"  admin:     {RefreshToken.for_user(admin).access_token}")
        if pacientes:
            self.stdout.write(
                f"  paciente:  {RefreshToken.for_user(pacientes[0]).access_token}"
            )
