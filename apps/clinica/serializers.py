"""
Serializers del dominio clinico.

Convenciones:

1. Los serializers de lectura exponen los datos ya formateados para pantalla
   (`nombre_completo`, `medico_nombre`, `hora`) para que el front no tenga que
   derivarlos ni interpretar claves foraneas.

2. Los de escritura validan con las reglas de `apps.clinica.services` y no solo
   con los campos del modelo. Un turno valido en la base puede ser invalido como
   pedido: un medico `inactivo` o fuera de agenda tiene que rechazarse con un
   mensaje que se entienda, no con un 500 del constraint de PostgreSQL.

3. Los serializers de paciente nunca aceptan el campo de rol ni de propietario.
   El rol lo asigna la clinica a mano y el propietario lo saca del request.
   Aceptarlos seria dejar que un paciente se auto-asigne el turno de otro.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from apps.accounts.models import Role

from . import services
from .models import (
    Agenda,
    Bloqueo,
    ColaEspera,
    ConsultaRecepcion,
    Especialidad,
    FichaMedica,
    PerfilMedico,
    Turno,
    Urgencia,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Especialidades
# ---------------------------------------------------------------------------


class EspecialidadSerializer(serializers.ModelSerializer):
    """Listado publico de especialidades. Lo necesita el paciente para pedir turno."""

    #: No es una columna: se cuenta en la vista con un annotate para no disparar
    # una consulta por cada fila del listado.
    medicos_disponibles = serializers.IntegerField(read_only=True)

    class Meta:
        model = Especialidad
        fields = (
            "id",
            "nombre",
            "descripcion",
            "activa",
            "duracion_turno_minutos",
            "medicos_disponibles",
        )
        read_only_fields = ("medicos_disponibles",)


# ---------------------------------------------------------------------------
# Medicos
# ---------------------------------------------------------------------------


class MedicoSerializer(serializers.ModelSerializer):
    """
    Perfil del profesional visto desde un panel.

    `disponible` y `proximo_horario` se calculan porque son lo que la recepcion
    mira antes de agendar. `proximo_horario` viene de services para que el
    "disponible" de la pantalla sea el mismo criterio que usa el backend al
    validar la reserva, y no una opinion del front.
    """

    nombre_completo = serializers.SerializerMethodField()
    email = serializers.EmailField(source="user.email", read_only=True)
    especialidad = serializers.CharField(source="especialidad.nombre", read_only=True)
    especialidad_id = serializers.IntegerField(read_only=True)
    estado = serializers.CharField(read_only=True)
    disponible = serializers.BooleanField(read_only=True)
    proximo_horario = serializers.SerializerMethodField()
    agendas = serializers.SerializerMethodField()

    class Meta:
        model = PerfilMedico
        fields = (
            "id",
            "nombre",
            "nombre_completo",
            "email",
            "matricula",
            "especialidad",
            "especialidad_id",
            "estado",
            "disponible",
            "proximo_horario",
            "agendas",
        )

    def get_nombre_completo(self, obj):
        return obj.user.get_full_name() or obj.user.email

    def get_proximo_horario(self, obj):
        # Solo se calcula si el panel lo pide: recorrer 14 dias por cada medico
        # de un listado largo es caro y el front casi siempre lo muestra igual.
        if not self.context.get("con_proximo_horario"):
            return None
        proximos = services.proximos_horarios(obj, limite=1)
        return proximos[0] if proximos else None

    def get_agendas(self, obj):
        return [
            {
                "id": a.id,
                "dia_semana": a.dia_semana,
                "dia_nombre": a.get_dia_semana_display(),
                "hora_inicio": a.hora_inicio.strftime("%H:%M"),
                "hora_fin": a.hora_fin.strftime("%H:%M"),
            }
            for a in obj.agendas.filter(activo=True)
        ]


class MedicoEstadoSerializer(serializers.Serializer):
    """El medico cambia su propio estado. No admite de que rol se trata."""

    estado = serializers.ChoiceField(choices=PerfilMedico.Estado.choices)

    def validate_estado(self, value):
        # Un medico puede pasar a inactivo aunque tenga turnos tomados: esos
        # turnos los cancela recepcion uno por uno. Si se bloqueara aca, dejaria
        # al profesional sin poder avisar que no atiende y sin poder volver.
        return value


# ---------------------------------------------------------------------------
# Agenda y bloqueos
# ---------------------------------------------------------------------------


class AgendaSerializer(serializers.ModelSerializer):
    dia_nombre = serializers.CharField(source="get_dia_semana_display", read_only=True)

    class Meta:
        model = Agenda
        fields = (
            "id",
            "medico",
            "dia_semana",
            "dia_nombre",
            "hora_inicio",
            "hora_fin",
            "activo",
        )

    def validate(self, attrs):
        hora_inicio = attrs.get("hora_inicio")
        hora_fin = attrs.get("hora_fin")
        if hora_inicio and hora_fin and hora_fin <= hora_inicio:
            raise serializers.ValidationError(
                {"hora_fin": "La hora de fin tiene que ser posterior a la de inicio."}
            )
        return attrs


class BloqueoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bloqueo
        fields = ("id", "medico", "desde", "hasta", "motivo")

    def validate(self, attrs):
        desde = attrs.get("desde")
        hasta = attrs.get("hasta")
        if desde and hasta and hasta < desde:
            raise serializers.ValidationError(
                {"hasta": "La fecha de fin no puede ser anterior a la de inicio."}
            )
        return attrs


class ValidarDisponibilidadSerializer(serializers.Serializer):
    """
    Datos minimos para preguntar si un horario se puede reservar.

    Se valida con los mismos campos que despues usa el alta del turno: si aca se
    aceptara un `medico` inexistente, la respuesta seria un 500 en vez del 400
    que espera la pantalla.
    """

    medico = serializers.PrimaryKeyRelatedField(queryset=PerfilMedico.objects.all())
    inicio = serializers.DateTimeField()


# ---------------------------------------------------------------------------
# Turnos
# ---------------------------------------------------------------------------


class TurnoSerializer(serializers.ModelSerializer):
    """Lectura de turno. Todo lo que la pantalla necesita, ya aplanado."""

    paciente = serializers.SerializerMethodField()
    # UUIDField y no IntegerField: el pk de User es un UUID, y `int(uuid)` no
    # revienta, devuelve el entero de 128 bits del UUID. Serializado asi, el
    # `paciente_id` que llega al front es un numero de 39 digitos que `JSON.parse`
    # no puede representar sin perder precision, y la URL de la ficha
    # (`/api/pacientes/<uuid>/ficha`) nunca matchearia.
    paciente_id = serializers.UUIDField(read_only=True)
    medico_id = serializers.IntegerField(read_only=True)
    medico_nombre = serializers.SerializerMethodField()
    especialidad = serializers.CharField(source="especialidad.nombre", read_only=True)
    duracion_minutos = serializers.IntegerField(read_only=True)
    puede_cancelar = serializers.SerializerMethodField()
    puede_asistir = serializers.SerializerMethodField()
    posibles_estados = serializers.SerializerMethodField()

    class Meta:
        model = Turno
        fields = (
            "id",
            "paciente",
            "paciente_id",
            "medico_id",
            "medico_nombre",
            "especialidad",
            "inicio",
            "fin",
            "duracion_minutos",
            "estado",
            "motivo",
            "notas",
            "posibles_estados",
            "puede_cancelar",
            "puede_asistir",
            "created_at",
        )
        read_only_fields = ("notas",)

    def get_paciente(self, obj):
        return obj.paciente.get_full_name() or obj.paciente.email

    def get_medico_nombre(self, obj):
        medico = obj.medico
        return medico.user.get_full_name() or medico.user.email

    def get_posibles_estados(self, obj):
        """Los estados a los que este turno puede pasar, para pintar botones."""
        return sorted(obj.TRANSICIONES.get(obj.estado, set()))

    def get_puede_cancelar(self, obj):
        return bool(
            obj.estado in {Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO}
        )

    def get_puede_asistir(self, obj):
        # Asistir tiene sentido solo cuando el turno ya ocurrio. Sin este chequeo
        # el boton "no asistio" aparece en turnos de la semana que viene.
        return bool(
            obj.estado in {Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO}
            and obj.inicio <= timezone.localtime()
        )


class TurnoCreateSerializer(serializers.ModelSerializer):
    """
    Alta de turno hecha por recepcion o administracion.

    No se usa el ModelSerializer plano porque hay reglas que el modelo no conoce:
    el medico tiene que estar libre, el horario tiene que caer en su agenda y no
    puede pisarse con otro turno. Las tres se validan antes de escribir para
    poder devolver un mensaje util.

    `especialidad` no se pide: se deduce de la del medico. Si se aceptara el
    campo, recepcion podria agendar una consulta de cardiologia con un
    dermatologo y el turno quedaria mal clasificado para siempre.
    """

    class Meta:
        model = Turno
        fields = ("paciente", "medico", "inicio", "motivo")

    def validate(self, attrs):
        inicio = attrs["inicio"]
        duracion = services.duracion_de(attrs["medico"])
        fin = inicio + timedelta(minutes=duracion)

        motivo = services.motivo_de_rechazo(attrs["medico"], inicio, fin)
        if motivo:
            raise serializers.ValidationError({"inicio": motivo})

        attrs["fin"] = fin
        # Se deriva del medico, no se pide: ver la nota de la clase.
        attrs["especialidad"] = attrs["medico"].especialidad
        return attrs

    def create(self, validated_data):
        validated_data.setdefault("estado", Turno.Estado.PENDIENTE)
        return super().create(validated_data)


class TurnoSolicitarSerializer(serializers.Serializer):
    """
    Pedido de turno del paciente.

    El paciente no elige medico: elige especialidad y el sistema le ofrece el
    primer profesional con horario libre. Es lo que se acordo en el sprint, y
    ademas evita que el paciente reserve el unico slot de un dermatologo sin
    saber nada de el.
    """

    especialidad = serializers.PrimaryKeyRelatedField(
        queryset=Especialidad.objects.filter(activa=True)
    )
    inicio = serializers.DateTimeField()

    def validate_inicio(self, value):
        ahora = timezone.localtime()
        if value <= ahora:
            raise serializers.ValidationError("El horario tiene que ser futuro.")
        if value > ahora + timedelta(days=90):
            raise serializers.ValidationError(
                "No se puede reservar con mas de 90 dias de anticipacion."
            )
        return value


class TurnoEstadoSerializer(serializers.Serializer):
    """Cambio de estado. Valida contra la tabla de transiciones del modelo."""

    estado = serializers.ChoiceField(choices=Turno.Estado.choices)

    def validate(self, attrs):
        turno = self.context["turno"]
        nuevo = attrs["estado"]
        if nuevo == turno.estado:
            raise serializers.ValidationError(
                {"estado": f"El turno ya esta en estado {turno.get_estado_display().lower()}."}
            )
        if not turno.puede_transicionar_a(nuevo):
            # `Turno.Estado(...)` y no `Turno(...)`: `Turno` es la clase del
            # modelo, y llamarla con un string devuelve una instancia a medio
            # construir que revienta con RelatedObjectDoesNotExist. El enum
            # anidado es el que sabe traducir "atendido" a "Atendido".
            permitidos = ", ".join(
                Turno.Estado(valor).label.lower()
                for valor in sorted(turno.TRANSICIONES.get(turno.estado, set()))
            )
            raise serializers.ValidationError(
                {
                    "estado": (
                        f"No se puede pasar de "
                        f"{turno.get_estado_display().lower()} a "
                        f"{Turno.Estado(nuevo).label.lower()}. "
                        f"Estados validos desde aca: {permitidos or 'ninguno'}."
                    )
                }
            )
        return attrs


# ---------------------------------------------------------------------------
# Consultas a recepcion
# ---------------------------------------------------------------------------


class ConsultaSerializer(serializers.ModelSerializer):
    paciente = serializers.SerializerMethodField()
    paciente_id = serializers.UUIDField(read_only=True)  # Ver TurnoSerializer.
    respondida_por = serializers.SerializerMethodField()
    esta_abierta = serializers.SerializerMethodField()

    class Meta:
        model = ConsultaRecepcion
        fields = (
            "id",
            "paciente",
            "paciente_id",
            "mensaje",
            "respuesta",
            "respondida_por",
            "estado",
            "esta_abierta",
            "creada",
            "respondida_en",
        )
        read_only_fields = ("respuesta", "respondida_por", "respondida_en", "estado")

    def get_paciente(self, obj):
        return obj.paciente.get_full_name() or obj.paciente.email

    def get_respondida_por(self, obj):
        if not obj.respondida_por:
            return None
        return obj.respondida_por.get_full_name() or obj.respondida_por.email

    def get_esta_abierta(self, obj):
        return obj.estado == ConsultaRecepcion.Estado.ABIERTA


class ConsultaCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsultaRecepcion
        fields = ("mensaje",)

    def validate_mensaje(self, value):
        if not value.strip():
            raise serializers.ValidationError("Escribi la consulta antes de enviarla.")
        return value.strip()


class ConsultaRespuestaSerializer(serializers.Serializer):
    respuesta = serializers.CharField()

    def validate_respuesta(self, value):
        if not value.strip():
            raise serializers.ValidationError("La respuesta no puede estar vacia.")
        return value.strip()


# ---------------------------------------------------------------------------
# Cola de espera
# ---------------------------------------------------------------------------


class ColaEsperaSerializer(serializers.ModelSerializer):
    paciente = serializers.SerializerMethodField()
    paciente_id = serializers.UUIDField(read_only=True)  # Ver TurnoSerializer.
    especialidad = serializers.CharField(source="especialidad.nombre", read_only=True)
    especialidad_id = serializers.IntegerField(read_only=True)
    medico = serializers.SerializerMethodField()
    medico_id = serializers.IntegerField(read_only=True)
    estado = serializers.CharField(read_only=True)

    class Meta:
        model = ColaEspera
        fields = (
            "id",
            "paciente",
            "paciente_id",
            "especialidad",
            "especialidad_id",
            "medico",
            "medico_id",
            "prioridad",
            "estado",
            "desde",
            "hasta",
            "nota",
            "turno",
            "creado",
        )
        read_only_fields = ("turno",)

    def get_paciente(self, obj):
        return obj.paciente.get_full_name() or obj.paciente.email

    def get_medico(self, obj):
        if not obj.medico:
            return None
        return obj.medico.user.get_full_name() or obj.medico.user.email


class ColaEsperaCreateSerializer(serializers.ModelSerializer):
    """
    Alta en la cola.

    `medico` se acepta pero es opcional: el paciente puede pedir "cualquier
    cardiologo" y la recepcion decide a quien le toca.
    """

    paciente = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(rol=Role.PACIENTE), required=False, write_only=True
    )
    prioridad = serializers.ChoiceField(
        choices=ColaEspera.Prioridad.choices, required=False
    )
    nota = serializers.CharField(required=False, allow_blank=True, max_length=200)

    class Meta:
        model = ColaEspera
        fields = ("paciente", "especialidad", "medico", "prioridad", "desde", "hasta", "nota")

    def validate(self, attrs):
        # El unico escenario que la base no puede covering: el paciente en
        # espera para una especialidad con un `medico` de otra. Sin este chequeo
        # la recepcion recibe una peticion imposible de satisfacer.
        medico = attrs.get("medico")
        especialidad = attrs.get("especialidad")
        if medico and especialidad and medico.especialidad_id != especialidad.id:
            raise serializers.ValidationError(
                {
                    "medico": (
                        "Ese profesional no pertenece a la especialidad "
                        "elegida."
                    )
                }
            )
        if (
            attrs.get("prioridad", ColaEspera.Prioridad.NORMAL)
            == ColaEspera.Prioridad.URGENTE
            and len((attrs.get("nota") or "").strip()) < 5
        ):
            raise serializers.ValidationError(
                {"nota": "El motivo de la urgencia debe tener entre 5 y 200 caracteres."}
            )
        return attrs


class ColaEsperaPrioridadSerializer(serializers.Serializer):
    """Recepcion cambia la prioridad. El paciente no."""

    prioridad = serializers.ChoiceField(choices=ColaEspera.Prioridad.choices)


class ColaEsperaAgendarSerializer(serializers.Serializer):
    """
    Conversion de una entrada de la cola en un turno concreto.

    Va en un endpoint propio y no en un PATCH del recurso porque es la unica
    operacion que crea un turno y ademas cierra la entrada: meterlo en el CRUD
    abriria la puerta a que un PATCH cualquiera change el estado a "agendado"
    sin generar el turno, y quedaria una cola que miente.
    """

    inicio = serializers.DateTimeField()

    def validate_inicio(self, value):
        if value <= timezone.localtime():
            raise serializers.ValidationError("El horario tiene que ser futuro.")
        return value


# ---------------------------------------------------------------------------
# Ficha medica
# ---------------------------------------------------------------------------


class FichaMedicaSerializer(serializers.ModelSerializer):
    """
    Ficha del paciente vista por el paciente.

    `verificada_por` y `verificada_en` son de solo lectura a proposito: si el
    paciente pudiera setearlos se auto-verificaria su propia ficha y dejaria de
    significar nada que un profesional la haya confirmado.
    """

    verificada_por = serializers.SerializerMethodField()
    verificada = serializers.BooleanField(read_only=True)

    class Meta:
        model = FichaMedica
        fields = (
            "id",
            "alergias",
            "enfermedades_cronicas",
            "medicacion",
            "observaciones",
            "verificada",
            "verificada_por",
            "verificada_en",
            "creado",
            "actualizado",
        )
        read_only_fields = ("verificada_por", "verificada_en", "verificada")

    def get_verificada_por(self, obj):
        if not obj.verificada_por:
            return None
        return obj.verificada_por.get_full_name() or obj.verificada_por.email

    def validate(self, attrs):
        # Misma regla que en el register: cadena vacia no, "Ninguna" si. Se
        # repite aca porque el paciente puede editar despues y no tiene por que
        # acordarse de la regla.
        for campo in ("alergias", "enfermedades_cronicas"):
            valor = attrs.get(campo)
            if valor is not None and not valor.strip():
                raise serializers.ValidationError(
                    {campo: "Este campo no puede estar en blanco. Escribi Ninguna si no aplica."}
                )
        return attrs


class FichaMedicaLecturaSerializer(FichaMedicaSerializer):
    """Igual que la anterior pero de solo lectura, para personal de la clinica."""

    class Meta(FichaMedicaSerializer.Meta):
        read_only_fields = (
            "alergias",
            "enfermedades_cronicas",
            "medicacion",
            "observaciones",
            "verificada",
            "verificada_por",
            "verificada_en",
        )


class FichaMedicaVerificarSerializer(serializers.Serializer):
    """Un profesional confirma que la ficha es correcta."""

    nota = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Observacion opcional del profesional que verifica.",
    )


# ---------------------------------------------------------------------------
# Alta de profesionales
# ---------------------------------------------------------------------------


class MedicoCreateSerializer(serializers.Serializer):
    """
    Alta de profesional con su cuenta de usuario en un solo paso.

    Recepcion no puede inventar el email de alguien que no existe ni mandarlo a
    un correo que no es suyo, asi que se toma un `User` existente ya verificado
    en vez de crear uno. El alta de la cuenta es un flujo aparte, con su codigo
    de verificacion.
    """

    email = serializers.EmailField()
    matricula = serializers.CharField(max_length=64)
    especialidad = serializers.PrimaryKeyRelatedField(
        queryset=Especialidad.objects.filter(activa=True)
    )

    def validate_email(self, value):
        user = User.objects.filter(email__iexact=value).first()
        if user is None:
            raise serializers.ValidationError(
                "No existe ningun usuario con ese email. Crear primero la cuenta."
            )
        if user.rol != Role.PACIENTE:
            raise serializers.ValidationError(
                f"Ese usuario ya tiene el rol {user.get_rol_display().lower()}."
            )
        if hasattr(user, "perfil_medico"):
            raise serializers.ValidationError("Ese usuario ya tiene un perfil de medico.")
        return value

    def validate_matricula(self, value):
        if PerfilMedico.objects.filter(matricula__iexact=value).exists():
            raise serializers.ValidationError("Esa matricula ya esta registrada.")
        return value

    def create(self, validated_data):
        user = User.objects.get(email__iexact=validated_data["email"])
        perfil = PerfilMedico.objects.create(
            user=user,
            matricula=validated_data["matricula"],
            especialidad=validated_data["especialidad"],
            estado=PerfilMedico.Estado.LIBRE,
        )
        # El rol lo sube el mismo endpoint. Si algo fallara entre la creacion del
        # perfil y el cambio de rol, el usuario tendria ficha de medico pero rol
        # de paciente: nadie veria su panel. Se hace en una transaccion.
        with transaction.atomic():
            user.rol = Role.MEDICO
            user.is_staff = False
            user.save(update_fields=["rol", "is_staff"])
        return perfil


# ---------------------------------------------------------------------------
# Urgencias
# ---------------------------------------------------------------------------


class UrgenciaSerializer(serializers.ModelSerializer):
    """
    Lectura de una urgencia, con todo lo que la pantalla necesita ya aplanado.

    `posibles_estados` sale de la tabla de transiciones del modelo, no del front:
    los botones de la guardia tienen que ser los mismos estados que el backend va
    a aceptar, y hardcodearlos es como se llega a un boton que devuelve 400.
    """

    paciente = serializers.SerializerMethodField()
    # UUIDField y no IntegerField: el pk de User es un UUID (ver TurnoSerializer).
    paciente_id = serializers.UUIDField(read_only=True)
    paciente_detalle = serializers.SerializerMethodField()
    especialidad = serializers.SerializerMethodField()
    especialidad_id = serializers.IntegerField(read_only=True)
    medico = serializers.SerializerMethodField()
    medico_id = serializers.IntegerField(read_only=True)
    triaje_display = serializers.CharField(source="get_triaje_display", read_only=True)
    estado = serializers.CharField(read_only=True)
    posibles_estados = serializers.SerializerMethodField()
    registrada_por = serializers.SerializerMethodField()

    class Meta:
        model = Urgencia
        fields = (
            "id",
            "paciente",
            "paciente_id",
            "paciente_detalle",
            "motivo",
            "triaje",
            "triaje_display",
            "estado",
            "especialidad",
            "especialidad_id",
            "medico",
            "medico_id",
            "observaciones",
            "turno",
            "posibles_estados",
            "registrada_por",
            "creada",
            "actualizada",
            "cerrada_en",
        )

    def get_paciente(self, obj):
        return obj.paciente.get_full_name() or obj.paciente.email

    def get_paciente_detalle(self, obj):
        """
        Datos de contacto y del perfil que la guardia necesita al abrir una
        urgencia. `perfil_paciente` es un OneToOne y puede no existir: el alta
        publica lo crea, pero una cuenta vieja o de demo puede no tenerlo. El
        `getattr` devuelve None en ese caso (RelatedObjectDoesNotExist hereda de
        AttributeError) y la pantalla muestra los campos vacios.
        """
        user = obj.paciente
        perfil = getattr(user, "perfil_paciente", None)
        return {
            "id": str(user.id),
            "nombre": user.get_full_name() or user.email,
            "email": user.email,
            "telefono": user.telefono,
            "email_verificado": user.email_verified,
            "documento": perfil.documento if perfil else None,
            "fecha_nacimiento": (
                perfil.fecha_nacimiento.isoformat()
                if perfil and perfil.fecha_nacimiento
                else None
            ),
            "direccion": perfil.direccion if perfil else "",
            "obra_social": perfil.obra_social if perfil else "",
        }

    def get_especialidad(self, obj):
        # MethodField y no `source` porque `especialidad` es opcional: en la
        # guardia se registra antes de saber el destino y el campo llega vacio.
        if not obj.especialidad_id:
            return None
        return obj.especialidad.nombre

    def get_medico(self, obj):
        if not obj.medico_id:
            return None
        return obj.medico.user.get_full_name() or obj.medico.user.email

    def get_posibles_estados(self, obj):
        return sorted(obj.TRANSICIONES.get(obj.estado, set()))

    def get_registrada_por(self, obj):
        if not obj.registrada_por:
            return None
        return obj.registrada_por.get_full_name() or obj.registrada_por.email


class UrgenciaCreateSerializer(serializers.ModelSerializer):
    """
    Alta de urgencia.

    El `paciente` se acepta pero no es obligatorio: la recepcion registra a
    alguien que no esta usando el sistema, y en ese caso lo manda por id. Un
    paciente que se registra a si mismo no lo manda: la view le pone el suyo y
    descarta cualquier valor que venga, para que nadie registre una urgencia a
    nombre de otro.
    """

    paciente = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(rol=Role.PACIENTE), required=False
    )

    class Meta:
        model = Urgencia
        fields = ("paciente", "motivo", "triaje", "especialidad", "medico", "observaciones")
        extra_kwargs = {
            "motivo": {
                "error_messages": {
                    "required": "Conta que esta pasando antes de registrar.",
                    "blank": "Conta que esta pasando antes de registrar.",
                }
            },
            "triaje": {
                "error_messages": {
                    "invalid_choice": (
                        "El triaje tiene que ser 0 (no urgente), 10 (urgente) "
                        "o 20 (emergencia)."
                    )
                }
            },
        }

    def validate_motivo(self, value):
        # `value` no es None aca: el campo es obligatorio y DRF ya rechazo el
        # faltante con el mensaje de `extra_kwargs`.
        if not value.strip():
            raise serializers.ValidationError("Conta que esta pasando antes de registrar.")
        return value.strip()

    def validate_observaciones(self, value):
        # El modelo es TextField y no tiene tope; se acota en la API para que un
        # cliente no pueda guardar un texto arbitrariamente grande.
        value = (value or "").strip()
        if len(value) > 2000:
            raise serializers.ValidationError(
                "Las observaciones no pueden superar los 2000 caracteres."
            )
        return value

    def validate(self, attrs):
        # Mismo chequeo que en la cola: un `medico` de otra especialidad seria
        # una derivacion imposible de atender.
        medico = attrs.get("medico")
        especialidad = attrs.get("especialidad")
        if medico and especialidad and medico.especialidad_id != especialidad.id:
            raise serializers.ValidationError(
                {"medico": "Ese profesional no pertenece a la especialidad elegida."}
            )
        return attrs


class UrgenciaEstadoSerializer(serializers.Serializer):
    """Cambio de estado (y opcionalmente de triaje) validado contra las transiciones."""

    estado = serializers.ChoiceField(
        choices=Urgencia.Estado.choices,
        error_messages={
            "required": "Indica el estado al que queres pasar la urgencia.",
            "invalid_choice": "Ese estado no existe.",
        },
    )
    triaje = serializers.ChoiceField(
        choices=Urgencia.Triaje.choices,
        required=False,
        error_messages={
            "invalid_choice": (
                "El triaje tiene que ser 0 (no urgente), 10 (urgente) o 20 (emergencia)."
            )
        },
    )

    def validate(self, attrs):
        urgencia = self.context["urgencia"]
        nuevo = attrs["estado"]

        # El paciente solo puede cerrar la suya; si ademas manda un triaje, se
        # rechaza en vez de ignorarlo, para que no crea que lo cambio.
        if "triaje" in attrs:
            user = self.context.get("user")
            es_staff = bool(
                user and (user.is_superuser or user.rol in {Role.RECEPCION, Role.ADMIN})
            )
            if not es_staff:
                raise serializers.ValidationError(
                    {"triaje": "Solo la guardia puede cambiar el triaje."}
                )
        if nuevo == urgencia.estado:
            raise serializers.ValidationError(
                {"estado": f"La urgencia ya esta en estado {urgencia.get_estado_display().lower()}."}
            )
        if not urgencia.puede_transicionar_a(nuevo):
            permitidos = ", ".join(
                Urgencia.Estado(valor).label.lower()
                for valor in sorted(urgencia.TRANSICIONES.get(urgencia.estado, set()))
            )
            raise serializers.ValidationError(
                {
                    "estado": (
                        f"No se puede pasar de {urgencia.get_estado_display().lower()} a "
                        f"{Urgencia.Estado(nuevo).label.lower()}. "
                        f"Estados validos desde aca: {permitidos or 'ninguno'}."
                    )
                }
            )
        return attrs


class UrgenciaDerivarSerializer(serializers.Serializer):
    """
    Derivacion de una urgencia a un turno ("preparar la consulta").

    Se pide el profesional y el horario porque la guardia no reserva el primero
    que aparezca: si el paciente ya vio a alguien, se lo agenda con ese.
    """

    medico = serializers.PrimaryKeyRelatedField(
        queryset=PerfilMedico.objects.all(),
        error_messages={
            "required": "Elegi el profesional para la consulta.",
            "does_not_exist": "Ese profesional no existe.",
            "incorrect_type": "El profesional tiene que ser un id numerico.",
        },
    )
    inicio = serializers.DateTimeField(
        error_messages={
            "required": "Indica el horario de la consulta.",
            "invalid": "El horario no tiene un formato valido.",
        }
    )
    motivo = serializers.CharField(required=False, allow_blank=True)

    def validate_inicio(self, value):
        if value <= timezone.localtime():
            raise serializers.ValidationError("El horario tiene que ser futuro.")
        return value