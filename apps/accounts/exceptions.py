"""
Errores de la API, expresados como `code` estable.

El frontend decide que hacer mirando `code` y no el texto de `detail`, asi que
los mensajes se pueden reescribir sin romper al cliente.
"""

from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler as drf_exception_handler


class Error(APIException):
    """Error de la API con codigo estable y detalle traducible."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "bad_request"
    default_detail = "Peticion invalida."

    def __init__(self, detail=None, code=None, status_code=None, **extra):
        if status_code is not None:
            self.status_code = status_code
        super().__init__(detail or self.default_detail, code or self.default_code)
        self.extra = extra


class EmailNotVerified(Error):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "email_not_verified"
    default_detail = _("Verificá tu email antes de iniciar sesion.")


class EmailAlreadyInUse(Error):
    status_code = status.HTTP_409_CONFLICT
    default_code = "email_already_in_use"
    default_detail = _("Ese email ya esta registrado.")


class InvalidVerificationCode(Error):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "invalid_code"
    default_detail = _("El codigo es incorrecto o expiro.")


class CodeAlreadyUsed(Error):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "code_already_used"
    default_detail = _("Ese codigo ya se uso. Pide uno nuevo.")


class ResendTooSoon(Error):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_code = "resend_too_soon"
    default_detail = _("Espera un momento antes de pedir otro codigo.")


class InactiveUser(Error):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "inactive_user"
    default_detail = _("La cuenta esta desactivada.")


class StaffOnly(Error):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "staff_only"
    default_detail = _("Recurso restringido al personal de la clinica.")


class AdminOnly(Error):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "admin_only"
    default_detail = _("Se requiere rol de administrador.")


class PatientOnly(Error):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "patient_only"
    default_detail = _("Recurso exclusivo para pacientes.")


def api_exception_handler(exc, context):
    """
    Normaliza la forma de los errores.

    Salida: {"code": "...", "detail": "...", "errors": {...}}. El `detail` de
    DRF se conserva como `detail` para no romper a nadie que ya lo use.
    """
    response = drf_exception_handler(exc, context)

    if response is None:
        return None

    data = response.data
    payload = {"code": _first_code(data, getattr(exc, "default_code", "error"))}

    if isinstance(data, dict):
        detail = data.get("detail")
        errors = {k: v for k, v in data.items() if k != "detail"}
        if detail is not None:
            payload["detail"] = str(detail)
        if errors:
            payload["errors"] = errors
    elif isinstance(data, list):
        payload["detail"] = "; ".join(str(item) for item in data)
    else:
        payload["detail"] = str(data)

    if isinstance(exc, Error) and exc.extra:
        payload.update(exc.extra)

    response.data = payload
    return response


def _first_code(data, fallback):
    """Saca el codigo de un error de validacion de DRF, que viene anidado."""
    if isinstance(data, dict):
        for value in data.values():
            found = _first_code(value, "")
            if found:
                return found
    elif isinstance(data, list) and data:
        return _first_code(data[0], "")
    return fallback
