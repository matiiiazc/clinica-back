"""
Rutas del dominio clinico.

Se montan bajo `/api/` (ver `clinica_back/urls.py`), asi que todos los paths de
aca son relativos a ese prefijo: `GET /api/turnos/`, no `GET /api/clinica/turnos/`.

Sin barra final, igual que en `apps/accounts/urls.py`, para no tener dos URLs
distintas apuntando al mismo recurso.
"""

from django.urls import path

from . import views

app_name = "clinica"

urlpatterns = [
    # --- Panel ---
    path("panel/resumen", views.PanelResumenView.as_view(), name="panel-resumen"),
    # --- Especialidades ---
    path("especialidades", views.EspecialidadListView.as_view(), name="especialidad-lista"),
    path(
        "especialidades/<int:pk>",
        views.EspecialidadDetailView.as_view(),
        name="especialidad-detalle",
    ),
    # --- Profesionales ---
    path("medicos", views.MedicoListView.as_view(), name="medico-lista"),
    path("medicos/mi-perfil", views.MiPerfilMedicoView.as_view(), name="medico-mi-perfil"),
    path("medicos/<int:pk>", views.MedicoDetailView.as_view(), name="medico-detalle"),
    path(
        "medicos/<int:pk>/agenda",
        views.MedicoAgendaView.as_view(),
        name="medico-agenda",
    ),
    path(
        "medicos/<int:pk>/agenda/<int:agenda_id>",
        views.MedicoAgendaItemView.as_view(),
        name="medico-agenda-item",
    ),
    path(
        "medicos/<int:pk>/bloqueos",
        views.MedicoBloqueoView.as_view(),
        name="medico-bloqueos",
    ),
    # --- Disponibilidad ---
    path("disponibilidad", views.DisponibilidadView.as_view(), name="disponibilidad"),
    # --- Turnos ---
    path("turnos", views.TurnoListView.as_view(), name="turno-lista"),
    path("turnos/mios", views.MisTurnosView.as_view(), name="turno-mios"),
    path("turnos/solicitar", views.TurnoSolicitarView.as_view(), name="turno-solicitar"),
    path("turnos/<int:pk>", views.TurnoDetailView.as_view(), name="turno-detalle"),
    # --- Consultas a recepcion ---
    path("consultas", views.ConsultaListView.as_view(), name="consulta-lista"),
    path(
        "consultas/<int:pk>/responder",
        views.ConsultaResponderView.as_view(),
        name="consulta-responder",
    ),
    # --- Cola de espera ---
    path("cola", views.ColaListView.as_view(), name="cola-lista"),
    path("cola/<int:pk>", views.ColaDetailView.as_view(), name="cola-detalle"),
    path(
        "cola/<int:pk>/prioridad",
        views.ColaPrioridadView.as_view(),
        name="cola-prioridad",
    ),
    path("cola/<int:pk>/agendar", views.ColaAgendarView.as_view(), name="cola-agendar"),
    # --- Ficha medica ---
    path("ficha", views.MiFichaView.as_view(), name="ficha-mia"),
    # `<uuid:...>` y no `<int:...>`: el User de accounts tiene UUID como primary
    # key. Un id numerico aca no matchearia nunca y daria 404 en todos lados.
    path(
        "pacientes/<uuid:user_id>/ficha",
        views.PacienteFichaView.as_view(),
        name="paciente-ficha",
    ),
]