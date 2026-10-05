"""
Reglas de agenda y calculo de horarios.

Vive aca y no en las views por una razon concreta: la misma pregunta
"que horarios tiene libres este medico el martes" la hacen tres endpoints
distintos (disponibilidad del paciente, solicitud de turno del paciente y
agenda del medico). Si cada uno calculara los slots por su cuenta, divergen
en la primera semana y el paciente ve un horario que despues no existe.

Dos conceptos que se confunden seguido:

- **Agenda**: los tramos que el profesional atiende, por dia de la semana. Es
  la capacidad declarada. La manage recepcion.
- **Turno**: un agendamiento concreto que ocupa parte de esa capacidad.

Un slot es libre si cae dentro de un tramo de agenda, no esta bloqueado por
vacaciones y no se pisa con ningun turno en estado que ocupa.
"""

from datetime import datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

from .models import ESTADOS_OCUPAN, Bloqueo, Turno


def _slot_minutos():
    return settings.TURNO_SLOT_MINUTOS


def duracion_de(medico):
    """
    Duracion por defecto del turno segun la especialidad del medico.

    La especialidad es la que define el largo tipico (una consulta de kulit no
    dura lo mismo que una de cardiologia), asi que se lee de ahi y no de una
    constante global. Si la especialidad no define nada, cae al default de
    settings.
    """
    return (
        getattr(medico.especialidad, "duracion_turno_minutos", None)
        or settings.TURNO_DURACION_MINUTOS
    )


def _aware(fecha, hora):
    """Combina fecha + hora en un datetime con timezone, sin errores de DST."""
    return timezone.make_aware(datetime.combine(fecha, hora))


def turnos_ocupan(medico, desde, hasta):
    """
    Turnos que impiden reutilizar el rango `[desde, hasta)`.

    Se filtra por `ESTADOS_OCUPAN` porque un turno cancelado o ausente libera
    el horario. Si no se filtrara, unaConsulta cancelada bloquearia el slot
    para siempre y habria que borrarla a mano de la base.
    """
    return Turno.objects.filter(
        medico=medico,
        estado__in=ESTADOS_OCUPAN,
        inicio__lt=hasta,
        fin__gt=desde,
    )


def bloqueado_el(medico, fecha):
    """Si el profesional pidio vacaciones ese dia."""
    return Bloqueo.objects.filter(medico=medico, desde__lte=fecha, hasta__gte=fecha)


def slots_libres(medico, fecha, duracion_minutos=None):
    """
    Horarios de `fecha` que el medico puede tomar, como datetimes con tz.

    Devuelve una lista ordenada. Si el dia esta bloqueado devuelve lista
    vacia sin exception: para el llamador la respuesta correcta a "no podes
    ese dia" es "no hay horarios", no un error.
    """
    if bloqueado_el(medico, fecha).exists():
        return []

    duracion = duracion_minutos or duracion_de(medico)
    paso = timedelta(minutes=_slot_minutos())
    largo = timedelta(minutes=duracion)

    # Todos los turnos del dia se piden una sola vez: si se consultara la base
    # por cada slot, con agenda de 8 horas serian 32 queries por peticion.
    inicio_dia = _aware(fecha, time(0, 0))
    fin_dia = inicio_dia + timedelta(days=1)
    ocupados = list(
        turnos_ocupan(medico, inicio_dia, fin_dia).values_list("inicio", "fin")
    )

    libres = []
    for agenda in medico.agendas.filter(activo=True, dia_semana=fecha.weekday()):
        cursor = _aware(fecha, agenda.hora_inicio)
        limite = _aware(fecha, agenda.hora_fin)
        # El slot tiene que CABER entero dentro del tramo. Un turno de 30
        # minutos que arranca 15 antes del cierre no alcanza.
        while cursor + largo <= limite:
            fin = cursor + largo
            if not any(c < fin and f > cursor for c, f in ocupados):
                libres.append(cursor)
            cursor += paso

    return sorted(libres)


def slot_libre(medico, inicio, duracion_minutos=None):
    """Si un horario concreto esta disponible para ese medico."""
    duracion = duracion_minutos or duracion_de(medico)
    fin = inicio + timedelta(minutes=duracion)
    return not turnos_ocupan(medico, inicio, fin).exists()


def dentro_de_agenda(medico, inicio, fin):
    """
    Si el tramo cae entero dentro de un tramo de agenda activo de ese dia.

    Se usa para que recepcion no pueda inventarse un turno un martes a las
    23:00 aunque el horario este libre en la base.
    """
    fecha = timezone.localtime(inicio).date()
    for agenda in medico.agendas.filter(activo=True, dia_semana=fecha.weekday()):
        if _aware(fecha, agenda.hora_inicio) <= inicio and fin <= _aware(
            fecha, agenda.hora_fin
        ):
            return True
    return False


def motivo_de_rechazo(medico, inicio, fin):
    """
    Por que no se puede agendar, en texto, o `None` si se puede.

    Devuelve el motivo en vez de levantar la excepcion para que la view elija
    entre un 400 con mensaje o un 409 segun quien露出了 el error.
    """
    fecha = timezone.localtime(inicio).date()

    if not medico.disponible:
        return f"El profesional esta {medico.get_estado_display().lower()} y no recibe turnos."

    if bloqueado_el(medico, fecha).exists():
        return "El profesional no atiende ese dia por un bloqueo de agenda."

    if not dentro_de_agenda(medico, inicio, fin):
        return "El horario esta fuera de la agenda del profesional para ese dia."

    if turnos_ocupan(medico, inicio, fin).exists():
        return "El profesional ya tiene un turno en ese horario."

    return None


def proximos_horarios(medico, desde=None, limite=10):
    """
    Los `limite` horarios libres mas tempranos a partir de `desde`.

    Lo usa la lista de medicos disponibles: en vez de devolver fechas sueltas,
    el panel muestra el proximo hueco real de cada profesional, que es lo que
    de verdad sirve para elegir.
    """
    desde = desde or timezone.localtime()
    resultados = []
    dia = timezone.localtime(desde).date()

    # Se mira hacia adelante 14 dias. Es un tope, no una promesa: si el medico
    # no tiene nada libre en dos semanas, se devuelve lo que haya.
    for _ in range(14):
        for slot in slots_libres(medico, dia):
            if slot >= desde:
                resultados.append(slot)
                if len(resultados) >= limite:
                    return resultados
        dia += timedelta(days=1)

    return resultados