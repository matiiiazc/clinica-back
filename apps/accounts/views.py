"""
Vistas de auth.

Endpoints (todos bajo /api/auth):
  POST /register       alta publica, siempre rol paciente, manda codigo por email
  POST /verify-email   canjea el codigo y devuelve tokens
  POST /resend-code    reenvia el codigo
  POST /login          email + contrasena, solo si el email esta verificado
  POST /refresh        refresca el access token
  POST /logout         invalida el refresh token y limpia las cookies
  GET  /me             usuario del access token
  PATCH /me            actualiza nombre / telefono
  POST /change-password

Los tokens van en dos lugares a la vez: en el body de la respuesta y en cookies
HttpOnly. El body para SPAs que los guardan en memoria, la cookie para que el
refresh sobreviva a recargar la pagina sin exponer el token a un XSS.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import update_last_login
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .exceptions import ResendTooSoon
from .models import EmailVerificationCode, User
from .serializers import (
    ChangePasswordSerializer,
    LoginSerializer,
    RegisterSerializer,
    ResendCodeSerializer,
    UpdateProfileSerializer,
    UserSerializer,
    VerifyEmailSerializer,
)
from .services import send_verification_code
from .throttling import LoginThrottle, RegisterThrottle, VerifyThrottle

logger = logging.getLogger(__name__)


# --- Emision de tokens y cookies ---


def issue_tokens(user, remember_me=False):
    """
    Genera el par access/refresh para un usuario.

    `remember_me` alarga el refresh a 30 dias (mismo criterio que el back de
    meled, donde rememberMe daba 30 dias en vez de la ventana corta).
    """
    refresh = RefreshToken.for_user(user)

    if remember_me:
        refresh.set_exp(lifetime=timedelta(days=settings.REFRESH_TOKEN_DAYS_REMEMBER))

    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
    }


def set_auth_cookies(response, tokens, remember_me=False):
    """Deja el access en `..._access` y el refresh en `..._refresh`."""
    access_max_age = int(settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds())
    refresh_max_age = int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds())
    if remember_me:
        refresh_max_age = settings.REFRESH_TOKEN_DAYS_REMEMBER * 24 * 60 * 60

    response.set_cookie(
        settings.AUTH_COOKIE + "_access",
        tokens["access"],
        max_age=access_max_age,
        httponly=True,
        secure=not settings.DEBUG,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        path="/",
    )
    response.set_cookie(
        settings.AUTH_COOKIE + "_refresh",
        tokens["refresh"],
        max_age=refresh_max_age,
        httponly=True,
        secure=not settings.DEBUG,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        path="/",
    )


def clear_auth_cookies(response):
    response.delete_cookie(
        settings.AUTH_COOKIE + "_access",
        path="/",
        samesite=settings.SESSION_COOKIE_SAMESITE,
    )
    response.delete_cookie(
        settings.AUTH_COOKIE + "_refresh",
        path="/",
        samesite=settings.SESSION_COOKIE_SAMESITE,
    )


def session_payload(user, tokens, extra=None):
    """Respuesta canonica de login / refresh / verify."""
    payload = {
        "user": UserSerializer(user).data,
        "tokens": tokens,
    }
    if extra:
        payload.update(extra)
    return payload


# --- Vistas ---


class RegisterView(APIView):
    """POST /api/auth/register - alta publica de pacientes."""

    permission_classes = [AllowAny]
    throttle_classes = [RegisterThrottle]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return Response(
            {
                "user": UserSerializer(user).data,
                "message": "Cuenta creada. Revisa tu email para verificarla.",
                "dev_code": getattr(serializer, "dev_code", None),
            },
            status=status.HTTP_201_CREATED,
        )


class VerifyEmailView(APIView):
    """POST /api/auth/verify-email - canjea el codigo y abre sesion."""

    permission_classes = [AllowAny]
    throttle_classes = [VerifyThrottle]

    def post(self, request):
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        tokens = issue_tokens(user)
        response = Response(session_payload(user, tokens))
        set_auth_cookies(response, tokens)
        response["X-CSRFToken"] = get_token(request)
        return response


class ResendCodeView(APIView):
    """POST /api/auth/resend-code - reenvia el codigo de verificacion."""

    permission_classes = [AllowAny]
    throttle_classes = [VerifyThrottle]

    def post(self, request):
        serializer = ResendCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].strip().lower()

        # Mismo 200 para email existente y no existente: no revelamos que cuentas
        # hay registradas.
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response({"message": "Si el email existe, enviamos un codigo."})

        if user.email_verified:
            return Response({"message": "Ese email ya estaba verificado."})

        last = (
            EmailVerificationCode.objects.filter(
                user=user, purpose=EmailVerificationCode.Purpose.REGISTRO
            )
            .order_by("-created_at")
            .first()
        )
        if last and not last.can_resend():
            wait = settings.EMAIL_VERIFICATION_RESEND_SECONDS - int(
                (timezone.now() - last.created_at).total_seconds()
            )
            raise ResendTooSoon(detail=f"Esperá {wait}s para pedir otro codigo.")

        _, code = EmailVerificationCode.issue(user)
        dev_code = send_verification_code(user, code)

        return Response(
            {"message": "Si el email existe, enviamos un codigo.", "dev_code": dev_code}
        )


class LoginView(APIView):
    """POST /api/auth/login."""

    permission_classes = [AllowAny]
    throttle_classes = [LoginThrottle]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        user = serializer.get_user()
        remember_me = serializer.validated_data.get("remember_me", False)
        tokens = issue_tokens(user, remember_me=remember_me)
        update_last_login(None, user)

        response = Response(
            session_payload(
                user, tokens, {"must_change_password": user.must_change_password}
            )
        )
        set_auth_cookies(response, tokens, remember_me=remember_me)
        response["X-CSRFToken"] = get_token(request)
        return response


class RefreshView(APIView):
    """
    POST /api/auth/refresh.

    Acepta el refresh en el body o en la cookie `..._refresh` y devuelve un
    access nuevo. Como ROTATE_REFRESH_TOKENS esta activo, cada refresh rota el
    token: hay que usar siempre el ultimo.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        raw = request.data.get("refresh") or request.COOKIES.get(
            settings.AUTH_COOKIE + "_refresh"
        )
        if not raw:
            return Response(
                {"code": "no_refresh_token", "detail": "Falta el refresh token."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            refresh = RefreshToken(raw)
        except TokenError:
            return Response(
                {"code": "invalid_refresh", "detail": "Refresh token invalido."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            user = User.objects.get(id=refresh["user_id"], is_active=True)
        except User.DoesNotExist:
            return Response(
                {"code": "invalid_refresh", "detail": "Usuario no disponible."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        tokens = {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        }
        response = Response({"user": UserSerializer(user).data, "tokens": tokens})
        set_auth_cookies(response, tokens)
        response["X-CSRFToken"] = get_token(request)
        return response


class LogoutView(APIView):
    """POST /api/auth/logout. No invalida el token, solo limpia las cookies."""

    permission_classes = [AllowAny]

    def post(self, request):
        response = Response({"message": "Sesion cerrada."})
        clear_auth_cookies(response)
        return response


class MeView(APIView):
    """GET /api/auth/me y PATCH /api/auth/me."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        serializer = UpdateProfileSerializer(
            request.user, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UserSerializer(request.user).data)


class ChangePasswordView(APIView):
    """POST /api/auth/change-password."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"message": "Contrasena actualizada."})


class HealthView(APIView):
    """GET /api/health - chequeo de vida, sin autenticacion."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        from django.db import connection

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            database = "ok"
        except Exception:
            database = "error"
            logger.exception("Health check: no se pudo consultar la base")

        return Response(
            {
                "status": "ok" if database == "ok" else "degraded",
                "database": database,
                "version": "1.0.0",
            }
        )
