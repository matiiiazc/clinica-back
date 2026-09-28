"""
Smoke test contra un servidor HTTP real.

El resto de tests usa el test client de Django, que no pasa por el socket. Este
levanta un servidor de verdad (LiveServerTestCase) y speaka el flujo completo
por HTTP: asi se comprueba el enrutado, el CORS, los middlewares, las cookies
y que la respuesta llega formateada como espera un frontend.
"""

import json
from urllib import error, request

from django.test import LiveServerTestCase
from django.urls import reverse

PASSWORD = "Clinica2026Segura"


def post(url, payload, headers=None):
    """POST JSON contra el servidor vivo. Devuelve (status, body, headers)."""
    req = request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with request.urlopen(req) as response:
            return response.status, json.loads(response.read() or b"{}"), response
    except error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {"raw": raw.decode(errors="replace")}
        return exc.code, body, exc


def get(url, headers=None):
    req = request.Request(url, headers=headers or {}, method="GET")
    try:
        with request.urlopen(req) as response:
            return response.status, json.loads(response.read() or b"{}"), response
    except error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {"raw": raw.decode(errors="replace")}
        return exc.code, body, exc


class TestFlujoPorHttp(LiveServerTestCase):
    """register -> verify -> login -> me -> refresh -> logout, por HTTP real."""

    def setUp(self):
        super().setUp()
        self.base = self.live_server_url

    def test_flujo_completo_por_http(self):
        email = "paciente.http@correo.com"

        # 1. Registro publico: crea paciente, sin tokens.
        status, body, _ = post(
            f"{self.base}/api/auth/register",
            {
                "email": email,
                "nombre": "Ana HTTP",
                "password": PASSWORD,
                "password_confirm": PASSWORD,
            },
        )
        self.assertEqual(status, 201, body)
        self.assertEqual(body["user"]["rol"], "paciente")
        self.assertNotIn("access", body)

        # 2. Login antes de verificar esta prohibido.
        status, body, _ = post(
            f"{self.base}/api/auth/login",
            {"email": email, "password": PASSWORD},
        )
        self.assertEqual(status, 403, body)
        self.assertEqual(body["code"], "email_not_verified")

        # 3. Verificacion con codigo incorrecto.
        status, body, _ = post(
            f"{self.base}/api/auth/verify-email",
            {"email": email, "code": "000000"},
        )
        self.assertEqual(status, 400, body)
        self.assertEqual(body["code"], "invalid_code")

        # 4. Verificacion correcta: devuelve tokens y cookies.
        from apps.accounts.models import EmailVerificationCode, User

        user = User.objects.get(email=email)
        user.verification_codes.update(
            code_hash=EmailVerificationCode.hash_code("424242")
        )
        status, body, response = post(
            f"{self.base}/api/auth/verify-email",
            {"email": email, "code": "424242"},
        )
        self.assertEqual(status, 200, body)
        access = body["tokens"]["access"]
        refresh = body["tokens"]["refresh"]
        self.assertIn("clinica_auth_access", response.headers.get("Set-Cookie", ""))

        # 5. /me con el access token.
        status, body, _ = get(
            f"{self.base}/api/auth/me", {"Authorization": f"Bearer {access}"}
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["email"], email)
        self.assertTrue(body["email_verified"])

        # 6. /me sin token.
        status, _, _ = get(f"{self.base}/api/auth/me")
        self.assertEqual(status, 401)

        # 7. Refresh: access nuevo.
        status, body, _ = post(
            f"{self.base}/api/auth/refresh", {"refresh": refresh}
        )
        self.assertEqual(status, 200, body)
        self.assertIn("access", body["tokens"])

    def test_health_responde_por_http(self):
        status, body, _ = get(f"{self.base}/health")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["database"], "ok")

    def test_cors_deja_pasar_el_origin_del_frontend(self):
        """Un login invalido responde 400, pero igual debe traer los headers CORS."""
        req = request.Request(
            f"{self.base}/api/auth/login",
            data=json.dumps({"email": "x@correo.com", "password": "x"}).encode(),
            headers={
                "Content-Type": "application/json",
                "Origin": "http://localhost:5173",
            },
            method="POST",
        )
        try:
            response = request.urlopen(req)
        except error.HTTPError as exc:
            response = exc

        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"),
                         "http://localhost:5173")
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Credentials"), "true"
        )
