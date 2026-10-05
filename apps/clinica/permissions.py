"""
Permisos del dominio clinico.

`apps/accounts/permissions.py` define los permisos por rol de forma general
(recepcion, medicos y administradores). Acá van los que dependen de las reglas
de negocio del clinico, que no se pueden expresar solo con "que rol tenes":

- un medico solo ve y toca **sus** turnos, no los de otro profesional;
- la recepcion no agenda para un medico `inactivo`;
- la ficha medica la verifica un profesional, pero el paciente no.

El criterio es no repetir la logica de rol que ya existe: estas clases se
combinan con `IsStaffUser` / `IsPatient` segun corresponda.
"""

from rest_framework.permissions import BasePermission

from apps.accounts.models import STAFF_ROLES, Role


class IsRecepcionOAdmin(BasePermission):
    """Recepcion y administradores: quienes gestionan la agenda."""

    message = "Solo recepcion o administradores pueden hacer esto."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and (user.is_superuser or user.rol in {Role.RECEPCION, Role.ADMIN})
        )


class IsMedico(BasePermission):
    """Solo profesionales de la salud."""

    message = "Recurso exclusivo para medicos."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and (user.is_superuser or user.rol == Role.MEDICO)
        )


class EsMedicoPropio(BasePermission):
    """
    El turno o la agenda tienen que ser de un perfil medico que sea del usuario.

    Va como permiso de objeto: el filtro por rol ya lo hace la view, pero esto
    evita que un medico que.Consiga adivinar un id vea el turno de otro. Es la
    unica defensa real, porque los ids son secuenciales y se adivinan facil.
    """

    message = "Ese recurso pertenece a otro profesional."

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.is_superuser or user.rol in STAFF_ROLES - {Role.MEDICO}:
            return True

        # `obj` puede ser un Turno (medico=PerfilMedico) o un PerfilMedico
        # directo, segun la view.
        medico = getattr(obj, "medico", obj)
        usuario_id = getattr(getattr(medico, "user", None), "id", None)
        return usuario_id is not None and str(usuario_id) == str(user.id)


class PuedeVerFichaMedica(BasePermission):
    """
    La ficha medica es dato sensible: la ve el paciente dueño y el personal de
    la clinica, pero no cualquier paciente.

    El personal tiene legitimidad para verla (es para eso que existe); el filtro
    real esta en la view, que limita al paciente a la suya.
    """

    message = "No podes ver la ficha medica de otro paciente."

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.is_superuser or user.rol in STAFF_ROLES:
            return True
        return str(getattr(obj, "user_id", "")) == str(user.id)