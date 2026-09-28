"""
Throttles por IP para los endpoints sensibles.

Cada vista sensible declara su throttle con scope propio, asi los limites se
ajustan por separado: loguearse 10 veces es normal, registrarse 5 ya es raro.

La ventana es de 15 minutos, la misma que usaba `express-rate-limit` en
web_rrhh_back. DRF viene con 60 segundos por defecto, asi que la ventana se
escribe en la propia rate en vez de usar THROTTLE_WINDOW.

Ojo: los contadores viven en la cache. Con la cache local de Django cada
worker lleva su propio conteo; para correr varios procesos (uvicorn workers,
gunicorn) hay que usar Redis o Memcached en CACHES.
"""

from django.conf import settings
from rest_framework.throttling import AnonRateThrottle

#: 15 minutos, alineado con el rateLimit() de los backends Node.
WINDOW_SECONDS = 15 * 60


class ClinicThrottle(AnonRateThrottle):
    """Base: lee el limite desde una setting DRF_THROTTLE_<SCOPE>."""

    #: Nombre de la setting con el maximo de requests por ventana.
    setting_name = None

    def get_rate(self):
        return f"{int(getattr(settings, self.setting_name))}/{WINDOW_SECONDS}"

    def parse_rate(self, rate):
        """
        Ignora el sufijo de la rate y usa siempre la ventana de 15 minutos.

        El parse_rate de DRF solo mira el primer caracter del periodo
        ('s', 'm', 'h', 'd'), asi que no puede expresar 15 minutos: leeria
        '900' como si fuera '9' segundos.
        """
        num_requests = int(rate.split("/")[0])
        return (num_requests, WINDOW_SECONDS)


class LoginThrottle(ClinicThrottle):
    setting_name = "DRF_THROTTLE_LOGIN"
    scope = "login"


class VerifyThrottle(ClinicThrottle):
    setting_name = "DRF_THROTTLE_VERIFY"
    scope = "verify"


class RegisterThrottle(ClinicThrottle):
    setting_name = "DRF_THROTTLE_REGISTER"
    scope = "register"
