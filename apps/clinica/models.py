"""
Modelos del dominio clinico: especialidades, profesionales, agenda y turnos.

Decisiones que no son obvias:

1. `PerfilMedico` y `PerfilPaciente` cuelgan del `User` de accounts con
   OneToOne. La tabla de credenciales y la del perfil quedan separadas, asi
   un medico no arrastra campos de paciente y viceversa. El rol se sigue
   leyendo de `User.rol`: los perfiles no lo duplican.

2. `Turno` guarda `inicio` y `fin` como DateTimeField, no como fecha + hora
   sueltas. Es lo que permite poner un ExclusionConstraint de PostgreSQL que
   prohiba el solapamiento en la base y no solo en Python (ver mas abajo).

3. `Turno.especialidad` esta desnormalizado a proposito. Si el medico cambia
   de especialidad, los turnos ya tomados deben seguir diciendo con que
   especialidad se Reservesaron.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.db import models
from django.db.models import F, Q
from django.db.models.expressions import RawSQL
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User

#: Dia de la semana segun ISO: 0 es lunes, 6 domingo.
DIAS_SEMANA = [
    (0, _("Lunes")),
    (1, _("Martes")),
    (2, _("Miércoles")),
    (3, _("Jueves")),
    (4, _("Viernes")),
    (5, _("Sábado")),
    (6, _("Domingo")),
]


class Especialidad(models.Model):
    """Grupo de profesionales: cardiologia, dermatologia, etc."""

    nombre = models.CharField("nombre", max_length=80, unique=True)
    descripcion = models.TextField("descripción", blank=True)
    activa = models.BooleanField("activa", default=True)

    #: Largo por defecto del turno en esta especialidad. Recepcion lo puede
    #: pisar al agendar, pero tener un default evita tener que preguntar siempre.
    duracion_turno_minutos = models.PositiveSmallIntegerField(
        "duración del turno (min)", default=30
    )

    class Meta:
        verbose_name = _("especialidad")
        verbose_name_plural = _("especialidades")
        ordering = ("nombre",)

    def __str__(self):
        return self.nombre


class PerfilPaciente(models.Model):
    """
    Datos clinicos y de contacto del paciente.

    El documento (DNI) es opcional porque no todos lo tienen o no lo dan, pero
    cuando se carga tiene que ser unico: es como se evita dar dos turnos a la
    misma persona con dos cuentas distintas.
    """

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="perfil_paciente"
    )
    documento = models.CharField("documento", max_length=32, null=True, blank=True, unique=True)
    fecha_nacimiento = models.DateField("fecha de nacimiento", null=True, blank=True)
    direccion = models.CharField("dirección", max_length=200, blank=True)
    obra_social = models.CharField("obra social", max_length=120, blank=True)

    class Meta:
        verbose_name = _("perfil de paciente")
        verbose_name_plural = _("perfiles de paciente")

    def __str__(self):
        return f"{self.user.get_full_name()} ({self.user.email})"


class FichaMedica(models.Model):
    """
    Datos de salud que declara el paciente al registrarse.

    Decidido el 2026-10-05: se pide en el register y es obligatoria. El motivo
    es que una alergia o condicion Cronica que el paciente no menciona porque
    no le sale en el formulario es exactamente la que el medico necesita saber.

    Por eso `alergias` y `enfermedades_cronicas` no admiten cadena vacia: quien
    no tiene ninguna escribe "Ninguna". Aceptar el silencio haria el campo
    opcional en la practica.

    La ficha cuelga de `User` y no de `PerfilPaciente` porque se crea en el
    register, y hasta ahora el register no guarantee que exista un perfil
    clinico completo.

    `verificada_por` queda en null mientras nadie profesional la haya
    confirmado. Que sea obligatoria en el register reduce el riesgo de que falte
    informacion, pero no lo elimina: el paciente pudo escribir cualquier cosa y
    puede editarla despues. El medico ve el dato con esa marca.
    """

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="ficha_medica"
    )

    alergias = models.TextField("alergias")
    enfermedades_cronicas = models.TextField("enfermedades crónicas")
    medicacion = models.TextField("medicación habitual", blank=True)
    observaciones = models.TextField("observaciones", blank=True)

    verificada_por = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="fichas_medicas_verificadas",
    )
    verificada_en = models.DateTimeField("verificada", null=True, blank=True)

    creado = models.DateTimeField("creado", default=timezone.now)
    actualizado = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        verbose_name = _("ficha médica")
        verbose_name_plural = _("fichas médicas")

    def __str__(self):
        return f"Ficha médica de {self.user.get_full_name()}"

    @property
    def verificada(self):
        return self.verificada_por_id is not None


class PerfilMedico(models.Model):
    """
    Ficha del profesional: en que especialidad atiende y si esta disponible.

    `estado` reemplaza al antiguo booleano `activo`. Decidido el 2026-10-05:
    el medico no solo esta activo o no, sino que tiene tres situaciones que
    recepcion necesita distinguir para saber a quien le puede agendar.

    - `libre`: atiende y se le pueden reservar horarios.
    - `atendiendo`: esta con un paciente ahora, pero el dia sigue.
    - `inactivo`: no recibe turnos. No es lo mismo que borrar: sus turnos
      historicos siguen intactos y sigue apareciendo en el historial.
    """

    class Estado(models.TextChoices):
        LIBRE = "libre", _("Libre")
        ATENDIENDO = "atendiendo", _("Atendiendo")
        INACTIVO = "inactivo", _("Inactivo")

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="perfil_medico"
    )
    especialidad = models.ForeignKey(
        Especialidad, on_delete=models.PROTECT, related_name="medicos"
    )
    matricula = models.CharField("matrícula", max_length=64, unique=True)
    estado = models.CharField(
        "estado", max_length=20, choices=Estado.choices, default=Estado.LIBRE
    )

    class Meta:
        verbose_name = _("perfil de médico")
        verbose_name_plural = _("perfiles de médico")
        ordering = ("user__nombre",)
        indexes = [
            models.Index(fields=["especialidad"], name="idx_medico_especialidad"),
            # Recepcion filtra por estado constantly para listar disponibles.
            models.Index(fields=["estado", "especialidad"], name="idx_medico_estado"),
        ]

    def __str__(self):
        return f"{self.user.get_full_name()} - {self.especialidad.nombre}"

    @property
    def disponible(self):
        """Solo `libre` recibe turnos. `atendiendo` esta ocupado ahora."""
        return self.estado == self.Estado.LIBRE

    @property
    def nombre(self):
        return self.user.nombre or self.user.email


class Agenda(models.Model):
    """
    Tramo horario en que un profesional atiende, por dia de la semana.

    Un dia puede tener varios tramos (manana y tarde), asi que la unicidad es
    sobre (medico, dia, hora_inicio) y no solo sobre el dia.
    """

    medico = models.ForeignKey(
        PerfilMedico, on_delete=models.CASCADE, related_name="agendas"
    )
    dia_semana = models.PositiveSmallIntegerField("día de la semana", choices=DIAS_SEMANA)
    hora_inicio = models.TimeField("hora de inicio")
    hora_fin = models.TimeField("hora de fin")
    activo = models.BooleanField("activo", default=True)

    class Meta:
        verbose_name = _("agenda")
        verbose_name_plural = _("agendas")
        ordering = ("medico__user__nombre", "dia_semana", "hora_inicio")
        constraints = [
            models.UniqueConstraint(
                fields=["medico", "dia_semana", "hora_inicio"],
                name="agenda_unica_por_tramo",
            ),
            models.CheckConstraint(
                condition=Q(hora_fin__gt=models.F("hora_inicio")),
                name="agenda_hora_fin_posterior",
            ),
        ]

    def __str__(self):
        return f"{self.medico} - {self.get_dia_semana_display()} {self.hora_inicio:%H:%M}"

    @property
    def duracion_minutos(self):
        inicio = timezone.datetime.combine(timezone.datetime(2000, 1, 1), self.hora_inicio)
        fin = timezone.datetime.combine(timezone.datetime(2000, 1, 1), self.hora_fin)
        return int((fin - inicio).total_seconds() // 60)


class Bloqueo(models.Model):
    """
    Dias en que el profesional no atiende: vacaciones, capacitaciones, feriados.

    Es un rango de fechas, no un dia suelto, porque las vacaciones se piden
    por temporada y cargar 15 dias uno por uno es una tortura.
    """

    medico = models.ForeignKey(
        PerfilMedico, on_delete=models.CASCADE, related_name="bloqueos"
    )
    desde = models.DateField("desde")
    hasta = models.DateField("hasta")
    motivo = models.CharField("motivo", max_length=200, blank=True)

    class Meta:
        verbose_name = _("bloqueo")
        verbose_name_plural = _("bloqueos")
        ordering = ("desde",)
        constraints = [
            models.CheckConstraint(
                condition=Q(hasta__gte=models.F("desde")),
                name="bloqueo_rango_valido",
            )
        ]

    def __str__(self):
        return f"{self.medico} bloqueado del {self.desde} al {self.hasta}"

    def cubre(self, fecha):
        return self.desde <= fecha <= self.hasta


#: Estados que ocupan el horario del profesional. Un turno cancelado libera el
#: slot, por eso queda fuera de la lista: es lo que usa el ExclusionConstraint
#: de abajo para decidir si dos turnos se pisan.
ESTADOS_OCUPAN = ["pendiente", "confirmado", "atendido"]


class Turno(models.Model):
    """
    Un turno tomado.

    La superposicion se prohibe con un ExclusionConstraint de PostgreSQL
    (ver Meta.constraints). Se podria validar solo en el serializer, pero dos
    personas podrian pedir el mismo horario a la vez y las dos pasarian el
    chequeo de Python. La base es el unico lugar donde no hay carrera.
    """

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", _("Pendiente")
        CONFIRMADO = "confirmado", _("Confirmado")
        ATENDIDO = "atendido", _("Atendido")
        CANCELADO = "cancelado", _("Cancelado")
        NO_ASISTIO = "no_asistio", _("No asistió")

    #: Transiciones validas. Cancelar y ausentarse son terminales desde el
    #: punto de vista de recepcion; atendido y no_asistio cierran el turno.
    TRANSICIONES = {
        Estado.PENDIENTE: {Estado.CONFIRMADO, Estado.CANCELADO, Estado.NO_ASISTIO},
        Estado.CONFIRMADO: {Estado.ATENDIDO, Estado.CANCELADO, Estado.NO_ASISTIO},
        Estado.ATENDIDO: set(),
        Estado.CANCELADO: set(),
        Estado.NO_ASISTIO: {Estado.CONFIRMADO},
    }

    paciente = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="turnos_como_paciente"
    )
    medico = models.ForeignKey(
        PerfilMedico, on_delete=models.PROTECT, related_name="turnos"
    )
    especialidad = models.ForeignKey(
        Especialidad, on_delete=models.PROTECT, related_name="turnos"
    )

    inicio = models.DateTimeField("inicio")
    fin = models.DateTimeField("fin")

    estado = models.CharField("estado", max_length=20, choices=Estado.choices, default=Estado.PENDIENTE)
    motivo = models.CharField("motivo de consulta", max_length=200, blank=True)
    notas = models.TextField("notas", blank=True)

    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="turnos_creados",
    )
    created_at = models.DateTimeField("creado", default=timezone.now)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        verbose_name = _("turno")
        verbose_name_plural = _("turnos")
        ordering = ("inicio",)
        constraints = [
            # El corazon del modelo: dos turnos del mismo profesional no pueden
            # pisarse. '[)' es half-open: un turno que termina 10:00 y otro que
            # empieza 10:00 NO se pisan, que es justo lo que quiere recepcion.
            ExclusionConstraint(
                name="turno_sin_superposicion",
                expressions=[
                    ("medico", "="),
                    # tstzrange(...) via RawSQL y no Range(F(...)): la version
                    # con F() genera un rango de tipo "unknown" que PostgreSQL
                    # no puede indexar con gist. Como inicio/fin ya son columnas
                    # timestamptz reales, el cast sale solo. RawSQL y no un
                    # string, porque Django valida que los strings referencien
                    # campos existentes y acá no hay ninguno.
                    (RawSQL("tstzrange(inicio, fin, '[)')", []), "&&"),
                ],
                condition=Q(estado__in=ESTADOS_OCUPAN),
                violation_error_message="El profesional ya tiene un turno en ese horario.",
            ),
            models.CheckConstraint(
                condition=Q(fin__gt=models.F("inicio")),
                name="turno_fin_posterior_a_inicio",
            ),
        ]
        indexes = [
            models.Index(fields=["medico", "inicio"], name="idx_turno_medico_inicio"),
            models.Index(fields=["paciente", "inicio"], name="idx_turno_paciente"),
            models.Index(fields=["estado", "inicio"], name="idx_turno_estado"),
        ]

    def __str__(self):
        return f"{self.paciente} con {self.medico} el {self.inicio:%d/%m %H:%M}"

    @property
    def duracion_minutos(self):
        return int((self.fin - self.inicio).total_seconds() // 60)

    def puede_transicionar_a(self, nuevo):
        return nuevo in self.TRANSICIONES.get(self.estado, set())

    @classmethod
    def defaults_de_turno(cls):
        """Valores por defecto tomados de la configuracion."""
        return {
            "duracion_minutos": settings.TURNO_DURACION_MINUTOS,
            "slot_minutos": settings.TURNO_SLOT_MINUTOS,
        }


class ConsultaRecepcion(models.Model):
    """
    Pregunta de un paciente a recepcion.

    No es un turno ni una nota clinica: es el canal para resolver dudas de
    cobertura y disponibilidad antes de reservar. Definido el 2026-10-05.

    `paciente` apunta a `User` y no a `PerfilPaciente`, igual que `Turno.paciente`,
    para no obligar a que exista un perfil clinico solo para preguntar algo.
    """

    class Estado(models.TextChoices):
        ABIERTA = "abierta", _("Abierta")
        RESPONDIDA = "respondida", _("Respondida")

    paciente = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="consultas_recepcion"
    )
    mensaje = models.TextField("mensaje")

    respuesta = models.TextField("respuesta", blank=True)
    respondida_por = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="consultas_respondidas",
    )
    estado = models.CharField("estado", max_length=20, choices=Estado.choices, default=Estado.ABIERTA)

    creada = models.DateTimeField("creada", default=timezone.now)
    respondida_en = models.DateTimeField("respondida", null=True, blank=True)

    class Meta:
        verbose_name = _("consulta a recepción")
        verbose_name_plural = _("consultas a recepción")
        ordering = ("estado", "-creada")
        indexes = [
            # Recepcion abre la bandeja y solo le importan las abiertas.
            models.Index(fields=["estado", "-creada"], name="idx_consulta_estado"),
        ]

    def __str__(self):
        return f"Consulta de {self.paciente} ({self.estado})"


class ColaEspera(models.Model):
    """
    Alguien que quiere turno pero todavia no hay lugar.

    Se encola contra una **especialidad**, no contra un medico: el paciente que
    espera cardiologia no le importa cual cardialogo lo atienda, y atarlo a un
    medico dejaria la cola vacia en cuanto ese medico no tenga hueco. El medico
    es opcional y solo filtra cuando el paciente lo pide expresamente.

    `prioridad` es un entero y no un texto a proposito. El orden de la cola es
    urgente primero, y con un texto habria que resolverlo con Case/When o
    confiando en que "urgente" venga antes que "normal" al ordenar
    alfabeticamente, que es fragil.
    """

    class Prioridad(models.IntegerChoices):
        NORMAL = 0, _("Normal")
        URGENTE = 10, _("Urgente")

    class Estado(models.TextChoices):
        ESPERANDO = "esperando", _("Esperando")
        AGENDADO = "agendado", _("Agendado")
        DESCARTADO = "descartado", _("Descartado")

    paciente = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="entradas_en_espera"
    )
    especialidad = models.ForeignKey(
        Especialidad, on_delete=models.PROTECT, related_name="entradas_en_espera"
    )
    medico = models.ForeignKey(
        PerfilMedico,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="entradas_en_espera",
    )

    prioridad = models.PositiveSmallIntegerField(
        "prioridad", choices=Prioridad.choices, default=Prioridad.NORMAL
    )
    estado = models.CharField("estado", max_length=20, choices=Estado.choices, default=Estado.ESPERANDO)

    desde = models.DateField("disponible desde", null=True, blank=True)
    hasta = models.DateField("disponible hasta", null=True, blank=True)
    nota = models.TextField("nota", blank=True)

    turno = models.OneToOneField(
        Turno,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="origen_espera",
    )

    creado = models.DateTimeField("creado", default=timezone.now)
    actualizado = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        verbose_name = _("entrada en cola de espera")
        verbose_name_plural = _("entradas en cola de espera")
        ordering = ("-prioridad", "creado")
        constraints = [
            models.CheckConstraint(
                condition=Q(hasta__isnull=True) | Q(desde__isnull=True) | Q(hasta__gte=F("desde")),
                name="espera_rango_valido",
            ),
            # Un paciente no puede tener dos entradas vivas para la misma
            # especialidad. Solo mira las que siguen esperando: una vez agendado
            # puede volver a encolarse mas adelante.
            models.UniqueConstraint(
                fields=["paciente", "especialidad"],
                condition=Q(estado="esperando"),
                name="espera_unica_por_paciente_especialidad",
            ),
        ]
        indexes = [
            models.Index(fields=["estado", "-prioridad", "creado"], name="idx_espera_cola"),
        ]

    def __str__(self):
        return f"{self.paciente} esperando {self.especialidad} ({self.estado})"

    def cubre(self, fecha):
        """Si la ventana de fechas deja pasar ese dia. Sin fechas, siempre."""
        if self.desde and fecha < self.desde:
            return False
        if self.hasta and fecha > self.hasta:
            return False
        return True
