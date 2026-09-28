"""
Rutas de auth, montadas bajo /api/auth/ en clinica_back/urls.py.

Se agrupan con un router sincrónico para que los paths queden en un solo lugar
y el prefijo /api/auth se cambie en un punto.
"""

from django.urls import path

from .views import (
    ChangePasswordView,
    LoginView,
    LogoutView,
    MeView,
    RefreshView,
    RegisterView,
    ResendCodeView,
    VerifyEmailView,
)

urlpatterns = [
    path("register", RegisterView.as_view(), name="auth-register"),
    path("verify-email", VerifyEmailView.as_view(), name="auth-verify-email"),
    path("resend-code", ResendCodeView.as_view(), name="auth-resend-code"),
    path("login", LoginView.as_view(), name="auth-login"),
    path("refresh", RefreshView.as_view(), name="auth-refresh"),
    path("logout", LogoutView.as_view(), name="auth-logout"),
    path("me", MeView.as_view(), name="auth-me"),
    path("change-password", ChangePasswordView.as_view(), name="auth-change-password"),
]
