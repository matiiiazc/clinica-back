"""
Rutas del proyecto.

/api/...    API DRF
/admin/     admin de Django (solo usuarios staff)
/health     chequeo de vida
"""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from apps.accounts.views import HealthView


def root(request):
    return JsonResponse(
        {
            "name": "clinica_back",
            "version": "1.0.0",
            "docs": "/api/schema/",
            "endpoints": {
                "auth": "/api/auth/",
                "admin": "/admin/",
                "health": "/health",
            },
        }
    )


urlpatterns = [
    path("", root),
    path("health", HealthView.as_view(), name="health"),
    path("api/auth/", include("apps.accounts.urls")),
    path("api/", include("apps.clinica.urls")),
    path("admin/", admin.site.urls),
]
