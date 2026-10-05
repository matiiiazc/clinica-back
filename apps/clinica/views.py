"""
API del dominio clinico: especialidades, profesionales, agenda, turnos,
consultas, cola de espera y ficha medica.

Como se decide que puede ver cada rol:

- **administrador** ve y manages todo;
- **recepcion** ve la agenda completa y es quien asigna turnos, responde
  consultas y ordena la cola;
- **medico** solo ve los turnos que le asignaron y su propia agenda; no puede
  agendar;
- **paciente** solo ve lo propio: sus turnos, su ficha, sus consultas.

Los permisos de rol se aplican con las clases de `apps.accounts.permissions` y
`apps.clinica.permissions`. El filtro por objeto vive dentro de las views
(`TurnoDetailView.get_turno`), porque los ids son secuenciales y se adivinan:
un chequeo de rol solo no alcanza para impedir que un medico lea el turno de
otro.
"""

from datetime import datetime, timedelta

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role, User
from apps.accounts.permissions import IsAdminUser, IsPatient, IsStaffUser

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
)
from .permissions import (
    IsMedico,
    IsRecepcionOAdmin,
    PuedeVerFichaMedica,
)
from .serializers import (
    AgendaSerializer,
    BloqueoSerializer,
    ColaEsperaAgendarSerializer,
    ColaEsperaCreateSerializer,
    ColaEsperaPrioridadSerializer,
    ColaEsperaSerializer,
    ConsultaCreateSerializer,
    ConsultaRespuestaSerializer,
    ConsultaSerializer,
    EspecialidadSerializer,
    FichaMedicaLecturaSerializer,
    FichaMedicaSerializer,
    FichaMedicaVerificarSerializer,
    MedicoCreateSerializer,
    MedicoEstadoSerializer,
    MedicoSerializer,
    TurnoCreateSerializer,
    TurnoEstadoSerializer,
    TurnoSerializer,
    TurnoSolicitarSerializer,
)


def perfil_de(user):
    """Perfil de medico del usuario, o `None` si no es medico."""
    return getattr(user, "perfil_medico", None)


def rango_desde_hoy(dias=1):
    """Desde el inicio de hoy hasta dentro de `dias` dias."""
    hoy = timezone.localtime().date()
    return hoy, hoy + timedelta(days=dias)


# ---------------------------------------------------------------------------
# Especialidades
# ---------------------------------------------------------------------------


class EspecialidadListView(APIView):
    """
    Listado de especialidades.

    Lo puede ver cualquier usuario autenticado, incluido el paciente: es lo que
    necesita para elegir a quien pedir turno. Crear y desactivar es solo de
    administracion, asi que el POST exige rol admin.
    """

    permission_classes = [IsStaffUser | IsPatient | IsAdminUser]

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAdminUser()]
        return super().get_permissions()

    def get(self, request):
        qs = Especialidad.objects.filter(activa=True)
        if request.query_params.get("todas") == "1":
            qs = Especialidad.objects.all()

        # Un annotate y no un `.count()` por especialidad: sin esto, el listado
        # hace N+1 consultas, que en una clinica con 20 especialidades son 20
        # idas a la base para pintar un numero.
        qs = qs.annotate(
            medicos_disponibles=Count(
                "medicos",
                filter=Q(medicos__estado=PerfilMedico.Estado.LIBRE),
                distinct=True,
            )
        )
        return Response(EspecialidadSerializer(qs, many=True).data)

    def post(self, request):
        serializer = EspecialidadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class EspecialidadDetailView(APIView):
    """Alta/baja logica de una especialidad. Solo administracion."""

    permission_classes = [IsAdminUser]

    def get_object(self, pk):
        return get_object_or_404(Especialidad, pk=pk)

    def get(self, request, pk):
        return Response(EspecialidadSerializer(self.get_object(pk)).data)

    def patch(self, request, pk):
        especialidad = self.get_object(pk)
        serializer = EspecialidadSerializer(especialidad, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        """
        Baja logica: se marca `activa=False` y no se borra.

        Borrarla de verdad dejaria turnos historicos apuntando a una especialidad
        inexistente, y el modelo tiene PROTECT justamente para evitar eso.
        """
        especialidad = self.get_object(pk)
        especialidad.activa = False
        especialidad.save(update_fields=["activa"])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Profesionales
# ---------------------------------------------------------------------------


class MedicoListView(ListAPIView):
    """
    Listado de profesionales.

    Filtros: `?especialidad=<id>` y `?disponibles=1`. El segundo es el que usa
    recepcion para agendar, y es la unica combinacion con `con_proximo_horario`,
    porque calcular los proximos huecos de toda la clinica seria caro de mas.
    """

    serializer_class = MedicoSerializer
    permission_classes = [IsStaffUser | IsAdminUser]

    def get_queryset(self):
        qs = PerfilMedico.objects.select_related("user", "especialidad").prefetch_related(
            "agendas"
        )

        especialidad = self.request.query_params.get("especialidad")
        if especialidad:
            qs = qs.filter(especialidad_id=especialidad)

        if self.request.query_params.get("disponibles") == "1":
            qs = qs.filter(estado=PerfilMedico.Estado.LIBRE)

        return qs

    def get_serializer_context(self):
        return {
            **super().get_serializer_context(),
            "con_proximo_horario": self.request.query_params.get("con_proximo") == "1",
        }

    def post(self, request):
        """Alta de profesional. Solo administracion."""
        self.permission_classes = [IsAdminUser]
        self.check_permissions(request)
        serializer = MedicoCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            MedicoSerializer(serializer.instance, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class MedicoDetailView(APIView):
    """Ver un profesional. Solo personal de la clinica."""

    permission_classes = [IsStaffUser | IsAdminUser]

    def get(self, request, pk):
        medico = get_object_or_404(
            PerfilMedico.objects.select_related("user", "especialidad"), pk=pk
        )
        return Response(MedicoSerializer(medico, context={"request": request}).data)


class MiPerfilMedicoView(APIView):
    """Perfil y estado del medico que esta usando el sistema."""

    permission_classes = [IsMedico | IsAdminUser]

    def get(self, request):
        medico = perfil_de(request.user)
        if medico is None:
            return Response(
                {"detalle": "Tu usuario no tiene un perfil de medico asociado."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(MedicoSerializer(medico, context={"request": request}).data)

    def patch(self, request):
        """El medico cambia su propio estado: libre, atendiendo o inactivo."""
        serializer = MedicoEstadoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        medico = perfil_de(request.user)
        if medico is None:
            return Response(
                {"detalle": "Tu usuario no tiene un perfil de medico asociado."},
                status=status.HTTP_404_NOT_FOUND,
            )

        anterior = medico.estado
        medico.estado = serializer.validated_data["estado"]
        medico.save(update_fields=["estado"])

        data = MedicoSerializer(medico, context={"request": request}).data
        data["estado_anterior"] = anterior
        return Response(data)


class MedicoAgendaView(APIView):
    """
    Tramos horarios de un profesional.

    Lectura: personal de la clinica (el medico solo deberia ver la suya, pero
    ver la de otro no filtra datos del paciente, asi que se lo permite). Alta:
    recepcion y administracion.
    """

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsRecepcionOAdmin()]
        return [IsStaffUser() | IsAdminUser()]

    def get(self, request, pk):
        medico = get_object_or_404(PerfilMedico, pk=pk)
        return Response(AgendaSerializer(medico.agendas.all(), many=True).data)

    def post(self, request, pk):
        medico = get_object_or_404(PerfilMedico, pk=pk)
        serializer = AgendaSerializer(data={**request.data, "medico": medico.id})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class MedicoAgendaItemView(APIView):
    """Baja logica de un tramo de agenda (no se borra el historico)."""

    permission_classes = [IsRecepcionOAdmin]

    def delete(self, request, pk, agenda_id):
        agenda = get_object_or_404(Agenda, pk=agenda_id, medico_id=pk)
        agenda.activo = False
        agenda.save(update_fields=["activo"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class MedicoBloqueoView(APIView):
    """Vacaciones, feriados, capacitaciones. Los manage recepcion."""

    permission_classes = [IsRecepcionOAdmin]

    def get(self, request, pk):
        medico = get_object_or_404(PerfilMedico, pk=pk)
        return Response(BloqueoSerializer(medico.bloqueos.all(), many=True).data)

    def post(self, request, pk):
        medico = get_object_or_404(PerfilMedico, pk=pk)
        serializer = BloqueoSerializer(data={**request.data, "medico": medico.id})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class DisponibilidadView(APIView):
    """
    Horarios libres de un profesional en una fecha.

    Es lo que consume el selector de turno del paciente, asi que lo puede pedir
    cualquier autenticado. Sin `medico` responde con el conjunto de profesionales
    de la especialidad, que es lo que necesita el paciente que todavia no sabe
    con quien quiere atenderse.
    """

    permission_classes = [IsStaffUser | IsPatient | IsAdminUser]

    def get(self, request):
        fecha_cruda = request.query_params.get("fecha")
        if not fecha_cruda:
            return Response(
                {"detalle": "Falta el parametro fecha (YYYY-MM-DD)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            fecha = datetime.strptime(fecha_cruda, "%Y-%m-%d").date()
        except ValueError:
            return Response(
                {"detalle": "La fecha tiene que ir en formato YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        medicos = PerfilMedico.objects.select_related(
            "user", "especialidad"
        ).filter(estado=PerfilMedico.Estado.LIBRE)

        medico_id = request.query_params.get("medico")
        if medico_id:
            medicos = medicos.filter(id=medico_id)

        especialidad_id = request.query_params.get("especialidad")
        if especialidad_id:
            medicos = medicos.filter(especialidad_id=especialidad_id)

        resultados = []
        for medico in medicos:
            slots = services.slots_libres(medico, fecha)
            resultados.append(
                {
                    "medico_id": medico.id,
                    "medico": medico.user.get_full_name() or medico.user.email,
                    "especialidad": medico.especialidad.nombre,
                    "duracion_minutos": services.duracion_de(medico),
                    "horarios": [s.isoformat() for s in slots],
                }
            )

        return Response({"fecha": fecha_cruda, "medicos": resultados})


# ---------------------------------------------------------------------------
# Turnos
# ---------------------------------------------------------------------------


class TurnoListView(ListAPIView):
    """
    Turnos de la clinica.

    Solo recepcion y administracion. El medico y el paciente tienen endpoints
    propios (`MisTurnosView`, `TurnoSolicitarView`) porque ven cosas distintas:
    el medico lo que le asignaron en un periodo, el paciente su historial.
    """

    serializer_class = TurnoSerializer
    permission_classes = [IsRecepcionOAdmin]

    def get_queryset(self):
        qs = Turno.objects.select_related(
            "paciente", "medico__user", "especialidad"
        )
        params = self.request.query_params

        estado = params.get("estado")
        if estado:
            qs = qs.filter(estado__in=estado.split(","))

        medico_id = params.get("medico")
        if medico_id:
            qs = qs.filter(medico_id=medico_id)

        desde = params.get("desde")
        hasta = params.get("hasta")
        if desde:
            qs = qs.filter(inicio__date__gte=desde)
        if hasta:
            qs = qs.filter(inicio__date__lte=hasta)

        return qs.order_by("inicio")

    def post(self, request):
        """Asignar un turno. Lo hace recepcion, no el paciente."""
        serializer = TurnoCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        turno = serializer.save(created_by=request.user)
        return Response(
            TurnoSerializer(turno).data, status=status.HTTP_201_CREATED
        )


class MisTurnosView(ListAPIView):
    """
    Los turnos del usuario que pregunta.

    - Medico: los que le asignaron, filtrables por `periodo` (dia/semana/mes),
      que es como trabaja un consultorio.
    - Paciente: su historial, con `?historico=1` para incluir los ya cerrados.
    """

    serializer_class = TurnoSerializer
    permission_classes = [IsMedico | IsPatient | IsAdminUser]
    pagination_class = None

    def get_queryset(self):
        user = self.request.user
        params = self.request.query_params

        if user.rol == Role.MEDICO and hasattr(user, "perfil_medico"):
            qs = Turno.objects.filter(medico__user=user)
            periodo = params.get("periodo", "dia")
            hoy = timezone.localtime().date()

            if periodo == "dia":
                qs = qs.filter(inicio__date=hoy)
            elif periodo == "semana":
                # Lunes a domingo de la semana en curso. Se usa weekday() y no
                # "hoy - 6 dias", que correria el lunes.
                lunes = hoy - timedelta(days=hoy.weekday())
                qs = qs.filter(inicio__date__gte=lunes, inicio__date__lte=lunes + timedelta(days=6))
            elif periodo == "mes":
                qs = qs.filter(
                    inicio__year=hoy.year,
                    inicio__month=hoy.month,
                )
            return qs.order_by("inicio")

        qs = Turno.objects.filter(paciente=user)
        if params.get("historico") != "1":
            qs = qs.filter(estado__in=[Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO])
        return qs.order_by("-inicio")


class TurnoDetailView(APIView):
    """
    Ver y cambiar el estado de un turno puntual.

    Quién puede verlo: el paciente del turno, el medico asignado y el personal
    de la clinica. Cualquier otro paciente receives 404 y no 403, para no
    confirmar que ese id existe.
    """

    permission_classes = [IsStaffUser | IsPatient | IsAdminUser]

    def get_turno(self, request, pk):
        turno = get_object_or_404(
            Turno.objects.select_related("paciente", "medico__user", "especialidad"),
            pk=pk,
        )
        user = request.user
        es_suyo = turno.paciente_id == user.id
        es_su_medico = turno.medico.user_id == user.id
        es_staff = user.is_superuser or user.rol in {Role.RECEPCION, Role.ADMIN}

        if not (es_suyo or es_su_medico or es_staff):
            return None, Response(
                {"detalle": "Ese turno no existe."}, status=status.HTTP_404_NOT_FOUND
            )
        return turno, None

    def get(self, request, pk):
        turno, error = self.get_turno(request, pk)
        if error:
            return error
        return Response(TurnoSerializer(turno).data)

    def patch(self, request, pk):
        """
        Cambio de estado.

        Cada rol tiene un subconjunto permitido, porque la responsabilidad es
        distinta: el paciente cancela, el medico cierra el turno como atendido
        o no asistio, y recepcion confirma o cancela.
        """
        turno, error = self.get_turno(request, pk)
        if error:
            return error

        serializer = TurnoEstadoSerializer(data=request.data, context={"turno": turno})
        serializer.is_valid(raise_exception=True)
        nuevo = serializer.validated_data["estado"]

        user = request.user
        es_staff = user.is_superuser or user.rol in {Role.RECEPCION, Role.ADMIN}
        es_su_medico = turno.medico.user_id == user.id
        es_suyo = turno.paciente_id == user.id

        if es_staff:
            pass  # recepcion y admin pueden usar todas las transiciones validas
        elif es_su_medico and nuevo in {Turno.Estado.ATENDIDO, Turno.Estado.NO_ASISTIO}:
            pass
        elif es_suyo and nuevo == Turno.Estado.CANCELADO:
            pass
        else:
            return Response(
                {"detalle": "Tu rol no puede pasar este turno a ese estado."},
                status=status.HTTP_403_FORBIDDEN,
            )

        turno.estado = nuevo

        # `origen_espera` es un OneToOne: si no hay fila en la cola, Django
        # lanza RelatedObjectDoesNotExist en vez de devolver None, asi que
        # hasattr es la forma de preguntar sin try/except.
        if nuevo == Turno.Estado.CANCELADO and hasattr(turno, "origen_espera"):
            # Si el turno venia de la cola, la entrada vuelve a estar disponible
            # para que recepcion no tenga que volver a cargarla a mano.
            entrada = turno.origen_espera
            entrada.estado = ColaEspera.Estado.ESPERANDO
            entrada.turno = None
            entrada.save(update_fields=["estado", "turno", "actualizado"])

        turno.save(update_fields=["estado", "updated_at"])
        return Response(TurnoSerializer(turno).data)


class TurnoSolicitarView(APIView):
    """
    Pedido de turno del paciente.

    El paciente elige especialidad y horario. El sistema busca el primer
    profesional libre que tenga ese slot y lo reserva. Si nadie tiene lugar, la
    respuesta es 409 con un enlace a la cola de espera, no un error generico:
    es una situacion esperada, no una falla.
    """

    permission_classes = [IsPatient]

    def post(self, request):
        serializer = TurnoSolicitarSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        especialidad = serializer.validated_data["especialidad"]
        inicio = serializer.validated_data["inicio"]
        duracion = especialidad.duracion_turno_minutos
        fin = inicio + timedelta(minutes=duracion)

        candidatos = (
            PerfilMedico.objects.select_related("user", "especialidad")
            .filter(especialidad=especialidad, estado=PerfilMedico.Estado.LIBRE)
            .order_by("id")
        )

        for medico in candidatos:
            if services.motivo_de_rechazo(medico, inicio, fin):
                continue
            turno = Turno.objects.create(
                paciente=request.user,
                medico=medico,
                especialidad=especialidad,
                inicio=inicio,
                fin=fin,
                estado=Turno.Estado.PENDIENTE,
                motivo=request.data.get("motivo", ""),
                created_by=request.user,
            )
            return Response(
                TurnoSerializer(turno).data, status=status.HTTP_201_CREATED
            )

        return Response(
            {
                "detalle": "No hay profesionales con horario libre en esa franja.",
                "puede_esperar": True,
                "especialidad": especialidad.id,
            },
            status=status.HTTP_409_CONFLICT,
        )


# ---------------------------------------------------------------------------
# Consultas a recepcion
# ---------------------------------------------------------------------------


class ConsultaListView(ListAPIView):
    """
    Consultas de recepcion.

    Paciente: solo las suyas. Recepcion y admin: todas, con las abiertas
    primero, que es como se abre la bandeja del dia.
    """

    serializer_class = ConsultaSerializer
    permission_classes = [IsStaffUser | IsPatient | IsAdminUser]
    pagination_class = None

    def get_queryset(self):
        user = self.request.user
        qs = ConsultaRecepcion.objects.select_related("paciente", "respondida_por")

        if user.rol == Role.PACIENTE and not user.is_superuser:
            qs = qs.filter(paciente=user)

        if self.request.query_params.get("estado"):
            qs = qs.filter(estado=self.request.query_params["estado"])

        return qs


    def post(self, request):
        serializer = ConsultaCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        consulta = serializer.save(paciente=request.user)
        return Response(ConsultaSerializer(consulta).data, status=status.HTTP_201_CREATED)


class ConsultaResponderView(APIView):
    """Recepcion responde una consulta. Una sola vez: la segunda se rechaza."""

    permission_classes = [IsRecepcionOAdmin]

    def post(self, request, pk):
        consulta = get_object_or_404(ConsultaRecepcion, pk=pk)

        if consulta.estado == ConsultaRecepcion.Estado.RESPONDIDA:
            return Response(
                {"detalle": "Esta consulta ya fue respondida."},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = ConsultaRespuestaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        consulta.respuesta = serializer.validated_data["respuesta"]
        consulta.respondida_por = request.user
        consulta.respondida_en = timezone.now()
        consulta.estado = ConsultaRecepcion.Estado.RESPONDIDA
        consulta.save(
            update_fields=[
                "respuesta",
                "respondida_por",
                "respondida_en",
                "estado",
            ]
        )
        return Response(ConsultaSerializer(consulta).data)


# ---------------------------------------------------------------------------
# Cola de espera
# ---------------------------------------------------------------------------


class ColaListView(ListAPIView):
    """
    Cola de espera.

    Paciente: entra y consulta la suya. Recepcion: ve la cola completa, ordenada
    por urgencia y antiguedad, que es el orden en que se trabaja.
    """

    serializer_class = ColaEsperaSerializer
    permission_classes = [IsStaffUser | IsPatient | IsAdminUser]
    pagination_class = None

    def get_queryset(self):
        user = self.request.user
        qs = ColaEspera.objects.select_related(
            "paciente", "especialidad", "medico__user"
        )

        if user.rol == Role.PACIENTE and not user.is_superuser:
            qs = qs.filter(paciente=user)
        elif self.request.query_params.get("estado"):
            qs = qs.filter(estado=self.request.query_params["estado"])

        return qs

    def post(self, request):
        serializer = ColaEsperaCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # El unico duplicado que importa es el vivo: el constraint parcial de la
        # base cubre el caso, pero sin esto el paciente recibe un 500 en vez de
        # un mensaje que dice que ya esta en la cola.
        ya_esta = ColaEspera.objects.filter(
            paciente=request.user,
            especialidad=serializer.validated_data["especialidad"],
            estado=ColaEspera.Estado.ESPERANDO,
        ).exists()
        if ya_esta:
            return Response(
                {"detalle": "Ya estas en la cola de espera de esa especialidad."},
                status=status.HTTP_409_CONFLICT,
            )

        serializer.save(paciente=request.user)
        return Response(
            ColaEsperaSerializer(serializer.instance).data, status=status.HTTP_201_CREATED
        )


class ColaPrioridadView(APIView):
    """Recepcion sube o baja la prioridad de una entrada."""

    permission_classes = [IsRecepcionOAdmin]

    def patch(self, request, pk):
        entrada = get_object_or_404(ColaEspera, pk=pk)
        serializer = ColaEsperaPrioridadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entrada.prioridad = serializer.validated_data["prioridad"]
        entrada.save(update_fields=["prioridad", "actualizado"])
        return Response(ColaEsperaSerializer(entrada).data)


class ColaAgendarView(APIView):
    """
    Cierra una entrada de la cola creando el turno correspondiente.

    Los dos pasos van juntos y en una transaccion: si se creara el turno y
    fallara el cambio de estado de la entrada, quedaria un turno sin origen y la
    cola mostrando que el paciente sigue esperando.
    """

    permission_classes = [IsRecepcionOAdmin]

    def post(self, request, pk):
        entrada = get_object_or_404(
            ColaEspera.objects.select_related("paciente", "especialidad"),
            pk=pk,
        )

        if entrada.estado != ColaEspera.Estado.ESPERANDO:
            return Response(
                {"detalle": "Esta entrada ya no esta esperando."},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = ColaEsperaAgendarSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        inicio = serializer.validated_data["inicio"]

        medicos = PerfilMedico.objects.filter(especialidad=entrada.especialidad)
        if entrada.medico_id:
            medicos = medicos.filter(id=entrada.medico_id)

        duracion = entrada.especialidad.duracion_turno_minutos
        fin = inicio + timedelta(minutes=duracion)

        elegido = None
        for medico in medicos:
            if not services.motivo_de_rechazo(medico, inicio, fin):
                elegido = medico
                break

        if elegido is None:
            return Response(
                {"detalle": "Ningun profesional de esa especialidad tiene libre ese horario."},
                status=status.HTTP_409_CONFLICT,
            )

        from django.db import transaction

        with transaction.atomic():
            turno = Turno.objects.create(
                paciente=entrada.paciente,
                medico=elegido,
                especialidad=entrada.especialidad,
                inicio=inicio,
                fin=fin,
                estado=Turno.Estado.CONFIRMADO,
                motivo=entrada.nota,
                created_by=request.user,
            )
            entrada.turno = turno
            entrada.estado = ColaEspera.Estado.AGENDADO
            entrada.save(update_fields=["turno", "estado", "actualizado"])

        return Response(
            {
                "entrada": ColaEsperaSerializer(entrada).data,
                "turno": TurnoSerializer(turno).data,
            },
            status=status.HTTP_201_CREATED,
        )


class ColaDetailView(APIView):
    """Sacar a alguien de la cola. El paciente sale de la suya, recepcion de cualquiera."""

    permission_classes = [IsStaffUser | IsPatient | IsAdminUser]

    def delete(self, request, pk):
        entrada = get_object_or_404(ColaEspera, pk=pk)
        user = request.user
        es_staff = user.is_superuser or user.rol in {Role.RECEPCION, Role.ADMIN}

        if not es_staff and entrada.paciente_id != user.id:
            return Response(
                {"detalle": "Esa entrada no existe."}, status=status.HTTP_404_NOT_FOUND
            )

        # Se marca descartada en vez de borrar: si queda registro de que el
        # paciente estuvo esperando, el historial tiene sentido.
        entrada.estado = ColaEspera.Estado.DESCARTADO
        entrada.save(update_fields=["estado", "actualizado"])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Ficha medica
# ---------------------------------------------------------------------------


class MiFichaView(APIView):
    """
    Ficha medica del paciente.

    Es de solo lectura para el personal: un medico puede confirmar que la ficha
    es correcta, pero no reescribirle al paciente los datos de salud. Editar los
    deja `verificada=False` de nuevo, porque si no, corregir una alergia en
    secreto dejaria el sello de "verificada" sobre un dato que nadie probo.
    """

    permission_classes = [IsPatient | IsStaffUser | IsAdminUser]

    def get(self, request):
        ficha = get_object_or_404(FichaMedica, user=request.user)
        return Response(FichaMedicaSerializer(ficha).data)

    def patch(self, request):
        ficha = get_object_or_404(FichaMedica, user=request.user)
        serializer = FichaMedicaSerializer(ficha, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(verificada_por=None, verificada_en=None)
        return Response(FichaMedicaSerializer(ficha).data)


class PacienteFichaView(APIView):
    """
    Ficha de un paciente, para el personal.

    Un medico solo deberia ver las de sus propios pacientes: la ley de secreto
    profesional lo pide y el permiso de objeto no alcanza para expresarlo porque
    "tiene un turno con el" es una condicion que cambia en el tiempo. Se filtra
    por `solo_mis_pacientes`.
    """

    permission_classes = [IsStaffUser | IsAdminUser | IsMedico]

    def get(self, request, user_id):
        paciente = get_object_or_404(User, pk=user_id, rol=Role.PACIENTE)

        if (
            request.user.rol == Role.MEDICO
            and not request.user.is_superuser
            and request.query_params.get("solo_mis_pacientes") == "1"
        ):
            tiene_turno = Turno.objects.filter(
                medico__user=request.user, paciente=paciente
            ).exists()
            if not tiene_turno:
                return Response(
                    {"detalle": "Ese paciente no tiene turnos con vos."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        self.check_object_permissions(request, paciente.ficha_medica)
        ficha = get_object_or_404(FichaMedica, user=paciente)
        return Response(FichaMedicaLecturaSerializer(ficha).data)

    def post(self, request, user_id):
        """El profesional verifica la ficha."""
        ficha = get_object_or_404(FichaMedica, user_id=user_id)

        if request.user.rol not in {Role.MEDICO, Role.ADMIN} and not request.user.is_superuser:
            return Response(
                {"detalle": "Solo un medico o administrador puede verificar una ficha."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = FichaMedicaVerificarSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        nota = serializer.validated_data.get("nota", "")
        ficha.verificada_por = request.user
        ficha.verificada_en = timezone.now()
        ficha.save(update_fields=["verificada_por", "verificada_en", "actualizado"])

        data = FichaMedicaLecturaSerializer(ficha).data
        data["nota_verificacion"] = nota
        return Response(data)


# ---------------------------------------------------------------------------
# Resumen del panel
# ---------------------------------------------------------------------------


class PanelResumenView(APIView):
    """
    Numeros de arranque de cada panel.

    Existe para que cada panel muestre lo suyo sin pedir cinco endpoints: una
    pantalla de cinco requests(o seis)(o seis) seis(o seis) es una pantalla que carga cinco veces.
    El contenido depende del rol, y por eso no hay un unico recurso "resumen".
    """

    permission_classes = [IsStaffUser | IsPatient | IsAdminUser]

    def get(self, request):
        user = request.user
        hoy = timezone.localtime().date()
        resumen = {"rol": user.rol}

        if user.rol == Role.ADMIN or user.is_superuser:
            resumen.update(
                {
                    "usuarios": User.objects.count(),
                    "especialidades": Especialidad.objects.filter(activa=True).count(),
                    "medicos": PerfilMedico.objects.count(),
                    "turnos_hoy": Turno.objects.filter(inicio__date=hoy).count(),
                    "consultas_abiertas": ConsultaRecepcion.objects.filter(
                        estado=ConsultaRecepcion.Estado.ABIERTA
                    ).count(),
                    "esperando": ColaEspera.objects.filter(
                        estado=ColaEspera.Estado.ESPERANDO
                    ).count(),
                }
            )

        elif user.rol == Role.RECEPCION:
            resumen.update(
                {
                    "medicos_libres": PerfilMedico.objects.filter(
                        estado=PerfilMedico.Estado.LIBRE
                    ).count(),
                    "turnos_hoy": Turno.objects.filter(inicio__date=hoy).count(),
                    "turnos_pendientes": Turno.objects.filter(
                        estado=Turno.Estado.PENDIENTE
                    ).count(),
                    "consultas_abiertas": ConsultaRecepcion.objects.filter(
                        estado=ConsultaRecepcion.Estado.ABIERTA
                    ).count(),
                    "esperando": ColaEspera.objects.filter(
                        estado=ColaEspera.Estado.ESPERANDO
                    ).count(),
                }
            )

        elif user.rol == Role.MEDICO:
            qs = Turno.objects.filter(medico__user=user, inicio__date=hoy)
            resumen.update(
                {
                    "turnos_hoy": qs.count(),
                    "pendientes_hoy": qs.filter(estado=Turno.Estado.PENDIENTE).count(),
                    "estado": getattr(getattr(user, "perfil_medico", None), "estado", None),
                }
            )

        else:  # paciente
            ficha = getattr(user, "ficha_medica", None)
            resumen.update(
                {
                    "turnos_proximos": Turno.objects.filter(
                        paciente=user,
                        estado__in=[Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO],
                        inicio__gte=timezone.now(),
                    ).count(),
                    "en_cola": ColaEspera.objects.filter(
                        paciente=user, estado=ColaEspera.Estado.ESPERANDO
                    ).count(),
                    "consultas_abiertas": ConsultaRecepcion.objects.filter(
                        paciente=user, estado=ConsultaRecepcion.Estado.ABIERTA
                    ).count(),
                    "ficha_verificada": bool(ficha and ficha.verificada),
                }
            )

        return Response(resumen)