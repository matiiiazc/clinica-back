"""
Admin de Django para el dominio clinico.

Este es el panel de emergencia: donde se miran datos rare y se corrige a mano lo
que la API no deja. La operacion diaria va por los paneles de la app.

Un detalle importante: `PerfilMedico.estado` y `Turno.estado` son los dos unicos
lugares donde un cambio manual puede romper una regla de negocio, asi que se
agregan a la lista de "no editables" y se cambian por la API, que valida.
"""

from django.contrib import admin

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


@admin.register(Especialidad)
class EspecialidadAdmin(admin.ModelAdmin):
    list_display = ("nombre", "activa", "duracion_turno_minutos", "total_medicos")
    list_filter = ("activa",)
    search_fields = ("nombre", "descripcion")
    ordering = ("nombre",)

    @admin.display(description="medicos")
    def total_medicos(self, obj):
        return obj.medicos.count()


class AgendaInline(admin.TabularInline):
    """Tramos que atiende el profesional, editables desde su ficha."""

    model = Agenda
    extra = 1
    fields = ("dia_semana", "hora_inicio", "hora_fin", "activo")


class BloqueoInline(admin.TabularInline):
    """Vacaciones y feriados, por temporada y no de a un dia."""

    model = Bloqueo
    extra = 0
    fields = ("desde", "hasta", "motivo")


@admin.register(PerfilMedico)
class PerfilMedicoAdmin(admin.ModelAdmin):
    """Ver la agenda y los bloqueos del profesional en la misma pagina."""

    list_display = ("nombre", "especialidad", "matricula", "estado")
    list_filter = ("estado", "especialidad")
    search_fields = ("user__email", "user__nombre", "matricula")
    # Cambiar el estado por aca esquivaria la validacion de la API.
    readonly_fields = ("estado",)
    inlines = (AgendaInline, BloqueoInline)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user", "especialidad")

    @admin.display(description="nombre", ordering="user__nombre")
    def nombre(self, obj):
        return obj.user.get_full_name() or obj.user.email


@admin.register(Turno)
class TurnoAdmin(admin.ModelAdmin):
    list_display = ("inicio", "medico_nombre", "paciente_nombre", "especialidad", "estado")
    list_filter = ("estado", "especialidad")
    search_fields = ("paciente__email", "medico__user__email")
    date_hierarchy = "inicio"
    ordering = ("-inicio",)
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("paciente", "medico__user", "especialidad")
        )

    @admin.display(description="medico", ordering="medico__user__nombre")
    def medico_nombre(self, obj):
        return obj.medico.user.get_full_name() or obj.medico.user.email

    @admin.display(description="paciente", ordering="paciente__nombre")
    def paciente_nombre(self, obj):
        return obj.paciente.get_full_name() or obj.paciente.email


@admin.register(FichaMedica)
class FichaMedicaAdmin(admin.ModelAdmin):
    list_display = ("user", "verificada", "verificada_por", "actualizado")
    list_filter = ("verificada_en",)
    search_fields = ("user__email", "user__nombre", "alergias")
    readonly_fields = ("creado", "actualizado", "verificada_por", "verificada_en")

    @admin.display(description="paciente", ordering="user__nombre")
    def user(self, obj):
        return obj.user.get_full_name() or obj.user.email

    @admin.display(boolean=True, description="verificada")
    def verificada(self, obj):
        return obj.verificada


@admin.register(ConsultaRecepcion)
class ConsultaRecepcionAdmin(admin.ModelAdmin):
    list_display = ("paciente", "estado", "creada", "respondida_por")
    list_filter = ("estado",)
    search_fields = ("paciente__email", "mensaje", "respuesta")
    readonly_fields = ("creada", "respondida_en", "respondida_por")

    @admin.display(description="paciente", ordering="paciente__nombre")
    def paciente(self, obj):
        return obj.paciente.get_full_name() or obj.paciente.email


@admin.register(ColaEspera)
class ColaEsperaAdmin(admin.ModelAdmin):
    list_display = ("paciente_nombre", "especialidad", "prioridad", "estado", "creado")
    list_filter = ("estado", "prioridad", "especialidad")
    search_fields = ("paciente__email",)
    ordering = ("-prioridad", "creado")
    readonly_fields = ("creado", "actualizado")

    @admin.display(description="paciente", ordering="paciente__nombre")
    def paciente_nombre(self, obj):
        return obj.paciente.get_full_name() or obj.paciente.email