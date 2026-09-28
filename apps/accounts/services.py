"""
Envio de emails transaccionales.

En desarrollo, sin SMTP_HOST configurado, Django usa el backend de consola y
el codigo queda impreso en la terminal. Eso evita tener que configurar un
servidor SMTP solo para probar el registro.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

#: En development con backend de consola, `send_verification_code` devuelve el
#: codigo para que la API lo muestre y se pueda probar el flujo completo.
DEV_BACKEND = "django.core.mail.backends.console.EmailBackend"


def _is_dev_backend():
    return settings.MAILERS["default"]["BACKEND"] == DEV_BACKEND


def _deliver(to, subject, template, context):
    html = render_to_string(template, context)
    send_mail(
        subject=subject,
        message=context.get("texto_plano", ""),
        recipient_list=[to],
        html_message=html,
        from_email=settings.EMAIL_FROM,
        fail_silently=False,
    )


def send_verification_code(user, code, ttl_minutes=None):
    """
    Manda el codigo de verificacion de email.

    Devuelve el codigo solo si el backend es el de consola (dev), para que el
    endpoint /register pueda devolverlo y se pueda probar sin SMTP.
    En produccion devuelve None.
    """
    ttl_minutes = ttl_minutes or settings.EMAIL_VERIFICATION_CODE_TTL_MINUTES
    subject = settings.CLINICA_EMAIL_VERIFICATION_SUBJECT

    try:
        _deliver(
            to=user.email,
            subject=subject,
            template="accounts/email/verification_code.html",
            context={
                "user": user,
                "code": code,
                "clinic": settings.CLINICA_NAME,
                "ttl_minutes": ttl_minutes,
                "frontend_url": settings.FRONTEND_URL,
                "texto_plano": (
                    f"Tu codigo de verificacion para {settings.CLINICA_NAME} es "
                    f"{code}. Vence en {ttl_minutes} minutos.\n"
                    f"{settings.FRONTEND_URL}/verificar?email={user.email}&codigo={code}"
                ),
            },
        )
    except Exception:
        # Un fallo de SMTP no debe tumbar el registro: el usuario puede pedir
        # un codigo nuevo. Se loguea con el email para poder reenviar a mano.
        logger.exception("No se pudo enviar el codigo de verificacion a %s", user.email)
        return code if _is_dev_backend() else None

    return code if _is_dev_backend() else None
