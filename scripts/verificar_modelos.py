"""
Verifica que la exclusion constraint de turnos sigue viva tras la migracion
0002 y que los dos modelos nuevos se comportan como se definio.

    python scripts/verificar_modelos.py
"""

import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "clinica_back.settings")

# El .env lo carga bootstrap.validate_settings(), no settings.py. Hay que
# llamarlo antes de django.setup(), igual que hace manage.py.
from clinica_back.bootstrap import validate_settings

validate_settings()
django.setup()

from datetime import timedelta

from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.clinica.models import (
    ColaEspera,
    ConsultaRecepcion,
    Especialidad,
    PerfilMedico,
    PerfilPaciente,
    Turno,
)

fallos = 0


def comprobar(titulo, ok, detalle=""):
    global fallos
    print(f"{'PASA' if ok else 'FALLA'}  {titulo}{' -> ' + detalle if detalle else ''}")
    if not ok:
        fallos += 1


def main():
    c = connection.cursor()

    # 1. La constraint de no solapamiento no debe haberse perdido
    c.execute(
        "SELECT conname, contype::text FROM pg_constraint "
        "WHERE conrelid='clinica_turno'::regclass AND contype IN ('x','c') ORDER BY conname"
    )
    cons = c.fetchall()
    nombres = {r[0] for r in cons}
    comprobar("exclusion constraint sigue viva", "turno_sin_superposicion" in nombres)
    comprobar(
        "check fin>inicio sigue vivo", "turno_fin_posterior_a_inicio" in nombres
    )
    c.execute("SELECT extname FROM pg_extension WHERE extname='btree_gist'")
    comprobar("extension btree_gist", bool(c.fetchone()))

    sello = timezone.now().strftime("%H%M%S%f")
    esp = Especialidad.objects.create(nombre=f"Esp {sello}")

    User.objects.create_user(
        email=f"med.{sello}@t.com", password="x", nombre="Dr. Uno", rol="medico"
    )
    User.objects.create_user(
        email=f"pac.{sello}@t.com", password="x", nombre="Pac. Uno"
    )
    User.objects.create_user(
        email=f"rec.{sello}@t.com", password="x", nombre="Recepcion", rol="recepcion"
    )

    # PerfilMedico no lo crea create_user: es un modelo aparte, con su propia
    # tabla y su propia OneToOne contra User.
    med = PerfilMedico.objects.create(
        user=User.objects.get(email=f"med.{sello}@t.com"),
        especialidad=esp,
        matricula=f"M{sello}",
    )

    # 2. Estado por defecto del medico
    comprobar("estado por defecto es libre", med.estado == "libre", med.estado)
    comprobar("propiedad disponible", med.disponible is True)

    med.estado = PerfilMedico.Estado.ATENDIENDO
    med.save()
    comprobar("atendiendo no recibe turnos", med.disponible is False)
    med.estado = PerfilMedico.Estado.INACTIVO
    med.save()
    comprobar("inactivo no recibe turnos", med.disponible is False)
    med.estado = PerfilMedico.Estado.LIBRE
    med.save()

    # 3. Los estados del turno siguen siendo los originales
    estados = [e for e in Turno.Estado.values]
    comprobar(
        "los 5 estados de turno intactos",
        estados == ["pendiente", "confirmado", "atendido", "cancelado", "no_asistio"],
        ", ".join(estados),
    )

    # 4. Consulta a recepcion: paciente pregunta, recepcion responde
    pac = User.objects.get(email=f"pac.{sello}@t.com")
    rec = User.objects.get(email=f"rec.{sello}@t.com")
    con = ConsultaRecepcion.objects.create(paciente=pac, mensaje="Tienen cobertura?")
    comprobar("consulta arranca abierta", con.estado == "abierta")
    con.respuesta = "Si, PAMI."
    con.respondida_por = rec
    con.estado = ConsultaRecepcion.Estado.RESPONDIDA
    con.respondida_en = timezone.now()
    con.save()
    con.refresh_from_db()
    comprobar("consulta queda respondida con autor", con.estado == "respondida" and con.respondida_por_id == rec.id)

    # 5. Cola de espera: urgente primero, y FIFO dentro de la misma prioridad
    esp_urgente = Especialidad.objects.create(nombre=f"Urg {sello}")
    esp_tercera = Especialidad.objects.create(nombre=f"Terc {sello}")

    # Dos normales en especialidades distintas, porque la constraint única
    # impide dos entradas esperando para la misma especialidad.
    ColaEspera.objects.create(paciente=pac, especialidad=esp, prioridad=ColaEspera.Prioridad.NORMAL)
    ColaEspera.objects.create(paciente=pac, especialidad=esp_urgente, prioridad=ColaEspera.Prioridad.URGENTE)
    ColaEspera.objects.create(paciente=pac, especialidad=esp_tercera, prioridad=ColaEspera.Prioridad.NORMAL)

    # El filtro va por paciente de esta corrida: si una corrida anterior
    # crasheo puede quedar entradas en la cola y la asercion compararia contra
    # una de esas en vez de contra la propia.
    cola = list(
        ColaEspera.objects.filter(paciente=pac, estado="esperando").order_by("-prioridad", "creado")
    )
    comprobar("la cola tiene las 3 entradas de esta corrida", len(cola) == 3, f"{len(cola)} entradas")
    comprobar(
        "urgente va primero, aunque se haya creado en el medio",
        cola[0].prioridad == ColaEspera.Prioridad.URGENTE,
        f"prioridad del primero: {cola[0].prioridad}",
    )

    # FIFO se mide solo entre las dos normales: comparar la urgente contra una
    # normal no prueba nada, porque la prioridad pisa al orden de llegada.
    normales = [c for c in cola if c.prioridad == ColaEspera.Prioridad.NORMAL]
    comprobar("las dos normales estan en la cola", len(normales) == 2)
    comprobar(
        "FIFO: la normal mas antigua va antes",
        normales[0].especialidad_id == esp.id and normales[1].especialidad_id == esp_tercera.id,
        f"primera={normales[0].especialidad_id == esp.id and 'esp' or 'tercera'}, "
        f"creado {normales[0].creado:%H:%M:%S.%f} vs {normales[1].creado:%H:%M:%S.%f}",
    )

    # 6. No se puede encolar dos veces vivo para la misma especialidad
    try:
        with transaction.atomic():
            ColaEspera.objects.create(paciente=pac, especialidad=esp)
        comprobar("no se duplica en la misma especialidad", False, "acepto el duplicado")
    except Exception:
        comprobar("no se duplica en la misma especialidad", True)

    # 7. Rango de fechas invalido
    try:
        with transaction.atomic():
            ColaEspera.objects.create(
                paciente=pac,
                especialidad=Especialidad.objects.create(nombre=f"Mal {sello}"),
                desde=timezone.now().date(),
                hasta=timezone.now().date() - timedelta(days=3),
            )
        comprobar("rechaza rango de fechas invertido", False, "acepto el rango malo")
    except Exception:
        comprobar("rechaza rango de fechas invertido", True)

    # 8. Ventana de fechas
    hoy = timezone.now().date()
    entrada = ColaEspera.objects.filter(paciente=pac, estado="esperando", especialidad=esp).first()
    entrada.desde, entrada.hasta = hoy, hoy + timedelta(days=7)
    entrada.save()
    comprobar("cubre hoy", entrada.cubre(hoy) is True)
    comprobar("no cubre pasado", entrada.cubre(hoy - timedelta(days=1)) is False)
    comprobar("no cubre futuro", entrada.cubre(hoy + timedelta(days=8)) is False)

    print(f"\n{'Todo OK' if fallos == 0 else f'{fallos} comprobacion(es) fallaron'}")

    limpiar()


def limpiar():
    """
    Borra lo que dejo el script. Barre por patron de email y no solo por el
    sello de esta corrida: si una corrida anterior crasheo, sus filas quedan
    y hacen fallar el borrado por las FKs en PROTECT.
    """
    try:
        ColaEspera.objects.filter(paciente__email__endswith="@t.com").delete()
        ConsultaRecepcion.objects.filter(paciente__email__endswith="@t.com").delete()
        Turno.objects.filter(paciente__email__endswith="@t.com").delete()
        PerfilMedico.objects.filter(user__email__endswith="@t.com").delete()
        PerfilPaciente.objects.filter(user__email__endswith="@t.com").delete()
        User.objects.filter(email__endswith="@t.com").delete()
        for nombre in ("Esp ", "Urg ", "Mal "):
            Especialidad.objects.filter(nombre__startswith=nombre).delete()
        print("limpieza: OK")
    except Exception as exc:
        # Que una limpieza falle no debe tapar el resultado de las pruebas.
        print(f"limpieza: fallo parcial -> {type(exc).__name__}: {exc}")


main()