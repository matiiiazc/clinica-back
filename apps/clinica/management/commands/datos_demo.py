"""
Datos de demostracion para probar los paneles con contenido real.

Los paneles se ven vacios sin esto: no hay profesionales, ni agenda, ni turnos.
Crea el minimo para que un paciente pueda pedir turno, un medico tenga agenda, y
recepcion tenga algo que agendar y responder.

No es una migracion ni un seed de produccion: es un comando de desarrollo, y
por eso pide `--si` antes de tocar nada. Si corre dos veces no rompe nada, pero
tampoco duplica: usa `get_or_create` y saltea lo que ya existe.

Uso:
    python manage.py datos_demo          # muestra el plan, no escribe
    python manage.py datos_demo --si     # escribe
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Role, User
from apps.clinica.models import (
    Agenda,
    ColaEspera,
    ConsultaRecepcion,
    Especialidad,
    FichaMedica,
    PerfilMedico,
    Turno,
)
from apps.clinica.services import duracion_de

CONTRASENA = "Clinica2026!Demo"

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
    ("Diego Paola", "paciente.diego@correo.local", "11-5555-0004"),
]

MOTIVOS = ["Control de presión", "Dolor de garganta", "Chequeo anual"]


class Command(BaseCommand):
    help = "Carga datos de demostración para poder probar los paneles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--si",
            action="store_true",
            help="Confirma la escritura. Sin esto solo muestra el plan.",
        )

    def handle(self, *args, **opciones):
        if not self.hay_datos():
            self.describir_plan()
            if not opciones["si"]:
                self.stdout.write("")
                self.stdout.write(self.style.WARNING("Volver a correr con --si para escribir."))
                return

        with transaction.atomic():
            # Todo dentro de una transaccion: si algo falla a mitad de camino,
            # la base queda como estaba. Un seed a medias deja datos que nadie
            # sabe de donde salieron.
            self.crear_especialidades()
            self.crear_staff()
            self.crear_profesionales()
            self.crear_pacientes()
            self.crear_turnos()
            self.crear_consultas()
            self.crear_cola()

        self.resumen()

    # --- Chequeo previo ---------------------------------------------------

    def hay_datos(self):
        return User.objects.filter(email__endswith="@clinica.local").exists()

    def describir_plan(self):
        self.stdout.write("Se va a crear:")
        self.stdout.write(f"  - {len(ESPECIALIDADES)} especialidades")
        self.stdout.write(f"  - {len(MEDICOS)} profesionales con agenda")
        self.stdout.write("  - 1 recepción y 1 administrador")
        self.stdout.write(f"  - {len(PACIENTES)} pacientes con ficha médica")
        self.stdout.write("  - turnos de esta semana y de la que viene")
        self.stdout.write("  - 2 consultas (una abierta) y 2 entradas de cola")
        self.stdout.write("")
        self.stdout.write(f"Todos con la contraseña: {CONTRASENA}")

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
            usuario = self.crear_usuario(
                email,
                nombre=nombre,
                rol=Role.PACIENTE,
                telefono=telefono,
                verificado=True,
            )
            FichaMedica.objects.get_or_create(
                user=usuario,
                defaults={
                    "alergias": "Ninguna",
                    "enfermedades_cronicas": "Ninguna",
                    "observaciones": "Ficha de demostración.",
                },
            )
        self.stdout.write(f"{len(PACIENTES)} pacientes con ficha médica.")

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

    # --- Contenido clínico ------------------------------------------------

    def crear_turnos(self):
        if Turno.objects.exists():
            return

        hoy = timezone.localtime().date()
        medicos = list(PerfilMedico.objects.select_related("especialidad"))
        pacientes = self.pacientes()
        if not medicos or not pacientes:
            return

        estados = [Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO, Turno.Estado.ATENDIDO]
        creados = 0

        for i, medico in enumerate(medicos):
            duracion = duracion_de(medico)
            for salto in range(0, 14):
                dia = hoy + timedelta(days=salto)
                weekday = dia.weekday()

                # Un turno tiene que caer en un tramo que el profesional atiende:
                # el serializer lo rechaza si no, y el panel se vería con turnos
                # imposibles.
                tramo = (
                    medico.agendas.filter(dia_semana=weekday, activo=True)
                    .order_by("hora_inicio")
                    .first()
                )
                if tramo is None:
                    continue

                # Se recorre el tramo de a una hora, no al azar: garantiza que
                # los turnos del mismo dia no se pisen entre si.
                offset = (salto % 3) * timedelta(hours=1)
                inicio = timezone.make_aware(
                    timezone.datetime.combine(dia, tramo.hora_inicio) + offset,
                    timezone.get_current_timezone(),
                )
                if inicio <= timezone.localtime():
                    continue

                Turno.objects.create(
                    paciente=pacientes[i % len(pacientes)],
                    medico=medico,
                    especialidad=medico.especialidad,
                    inicio=inicio,
                    fin=inicio + timedelta(minutes=duracion),
                    estado=estados[i % len(estados)],
                    motivo=MOTIVOS[i % len(MOTIVOS)],
                    created_by=pacientes[i % len(pacientes)],
                )
                creados += 1

        self.stdout.write(f"{creados} turnos.")

    def crear_consultas(self):
        if ConsultaRecepcion.objects.exists():
            return

        pacientes = self.pacientes()
        if not pacientes:
            return

        ConsultaRecepcion.objects.create(
            paciente=pacientes[0],
            mensaje="Necesito una receta del Klik. Me quedan dos comprimidos.",
        )

        # Una respondida, para que el historial no se vea siempre vacío. Los
        # campos se escriben directo porque `responder()` no existe todavía en el
        # modelo: el cambio de estado vive en el serializer.
        ConsultaRecepcion.objects.create(
            paciente=pacientes[1],
            mensaje="Consulta respondida de ejemplo para que se vea el historial.",
            respuesta="Pasá por el mostrador con tu DNI.",
            estado=ConsultaRecepcion.Estado.RESPONDIDA,
            respondida_por=self.admin(),
            respondida_en=timezone.now(),
        )
        self.stdout.write("2 consultas de recepción.")

    def crear_cola(self):
        if ColaEspera.objects.exists():
            return

        pacientes = self.pacientes()
        if not pacientes:
            return

        ColaEspera.objects.create(
            paciente=pacientes[2],
            especialidad=Especialidad.objects.get(nombre="Cardiología"),
            estado=ColaEspera.Estado.ESPERANDO,
            prioridad=ColaEspera.Prioridad.URGENTE,
            nota="Urgente: dolor en el pecho.",
        )
        ColaEspera.objects.create(
            paciente=pacientes[3],
            especialidad=Especialidad.objects.get(nombre="Dermatología"),
            estado=ColaEspera.Estado.ESPERANDO,
        )
        self.stdout.write("2 entradas en cola de espera.")

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