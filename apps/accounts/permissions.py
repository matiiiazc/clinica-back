"""Permisos por rol."""

from rest_framework.permissions import BasePermission

from .exceptions import AdminOnly, StaffOnly
from .models import STAFF_ROLES, Role


class IsStaffUser(BasePermission):
    """Solo personal de la clinica: recepcion, medicos y administradores."""

    message = "Recurso restringido al personal de la clinica."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and (user.is_superuser or user.rol in STAFF_ROLES)
        )


class IsAdminUser(BasePermission):
    """Solo administradores de la clinica."""

    message = "Se requiere rol de administrador."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and user.is_authenticated and user.is_active and (user.is_admin)
        )


class IsPatient(BasePermission):
    """Solo pacientes. Los roles internos no entran aca."""

    message = "Recurso exclusivo para pacientes."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and user.rol == Role.PACIENTE
        )


class IsSelfOrStaff(BasePermission):
    """El usuario puede tocar su propio recurso; el staff, cualquiera."""

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.is_superuser or user.rol in STAFF_ROLES:
            return True
        owner_id = getattr(obj, "user_id", None) or getattr(obj, "id", None)
        return owner_id is not None and str(owner_id) == str(user.id)
