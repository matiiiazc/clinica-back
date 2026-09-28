"""
Rutas del dominio clinico (vacio por ahora).

Ejemplo de como sumar un endpoint de staff con control de rol:

    router.register("turnos", TurnoViewSet)
    router.register("historias", HistoriaViewSet, basename="historia")

Cada ViewSet declara sus permisos con apps.accounts.permissions (IsStaffUser,
IsAdminUser, IsPatient) para que el acceso dependa del rol del usuario.
"""

from django.urls import path

app_name = "clinica"

urlpatterns: list[path] = []
