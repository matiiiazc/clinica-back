# clinica_back

Backend de la clinica en Django + DRF. Auth con **Argon2id** y **JWT**, replicando
el esquema de los backends Node del proyecto (`web_rrhh_back` y el back de Meled).

## Regla de roles

**El registro publico siempre crea `paciente`.** Aunque el body mande
`rol: "admin"`, el serializer lo descarta. Los roles internos (`recepcion`,
`medico`, `admin`) se dan de alta a mano, nunca por la API publica.

| Rol | Como se crea | Acceso |
|---|---|---|
| `paciente` | `POST /api/auth/register` (publico) | Sus propios datos |
| `recepcion` | `create_staff` / admin de Django | Operacion de la clinica |
| `medico` | `create_staff` / admin de Django | Historias clinicas |
| `admin` | `create_staff` / admin de Django | Todo + gestion de usuarios |

Un paciente **no** puede loguearse hasta verificar su email con el codigo de 6
digitos que le llega por correo.

## Puesta en marcha

```bash
cd Desktop/proyectos/clinica_back

python -m venv .venv
.venv\Scripts\activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env          # Windows (Linux/mac: cp)
```

Generar las claves y ponerlas en `.env`:

```bash
python -c "from django.core.management.utils import get_random_secret_key as g; print(g()); print(g())"
```

La primera va como `SECRET_KEY`, la segunda como `JWT_SECRET`. Son distintas a
proposito: asi el JWT de la API nunca firma la cookie de sesion de Django.

La base `clinica_back` tiene que existir en PostgreSQL:

```sql
CREATE DATABASE clinica_back;
```

```bash
python manage.py migrate
python manage.py create_staff admin@clinica.com --rol admin --nombre "Tu Nombre" --verificado
python manage.py runserver
```

El admin queda en http://127.0.0.1:8000/admin/

## Endpoints

Base: `/api/auth`. Sin prefijo `api`, sin barra final.

| Metodo | Ruta | Auth | Que hace |
|---|---|---|---|
| POST | `/api/auth/register` | no | Alta de paciente, manda el codigo de verificacion. **No** devuelve tokens |
| POST | `/api/auth/verify-email` | no | Canjea el codigo, marca el email como verificado y devuelve tokens |
| POST | `/api/auth/resend-code` | no | Reenvia el codigo (maximo 1 vez cada 60s) |
| POST | `/api/auth/login` | no | Email + contrasena. 403 si el email no esta verificado |
| POST | `/api/auth/refresh` | refresh | Access token nuevo. Acepta el body o la cookie |
| POST | `/api/auth/logout` | no | Limpia las cookies |
| GET | `/api/auth/me` | si | Usuario del token actual |
| PATCH | `/api/auth/me` | si | Actualiza nombre y telefono |
| POST | `/api/auth/change-password` | si | Cambia la contrasena |
| GET | `/health` | no | Chequeo de vida, incluye estado de la base |

### Registro

```bash
curl -X POST http://127.0.0.1:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"ana@correo.com","nombre":"Ana Perez","telefono":"1122334455",
       "password":"Clinica2026Segura","password_confirm":"Clinica2026Segura"}'
```

```json
{
  "user": { "id": "...", "email": "ana@correo.com", "rol": "paciente",
            "email_verified": false, "must_change_password": false },
  "message": "Cuenta creada. Revisa tu email para verificarla."
}
```

El rol sale siempre como `paciente`. Un `rol: "admin"` en el body se ignora.

### Verificacion

```bash
curl -X POST http://127.0.0.1:8000/api/auth/verify-email \
  -H "Content-Type: application/json" \
  -d '{"email":"ana@correo.com","code":"123456"}'
```

Devuelve `access` y `refresh`, y los deja ademas en cookies HttpOnly
(`clinica_auth_access`, `clinica_auth_refresh`).

### Login

```bash
curl -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"ana@correo.com","password":"Clinica2026Segura","remember_me":false}'
```

`remember_me: true` extiende el refresh de 7 a 30 dias.

## Formato de los errores

Todos los errores usan la misma envoltura, con un `code` estable para que el
frontend decida que mostrar sin parsear texto:

```json
{ "code": "email_not_verified", "detail": "Verificá tu email antes de iniciar sesion." }
```

Cuando el problema es de un campo puntual, viene en `errors`:

```json
{
  "code": "invalid",
  "errors": { "password": ["Esta contraseña es demasiado común.", "Esta contraseña es demasiado corta."] }
}
```

| Codigo | HTTP | Cuando |
|---|---|---|
| `email_already_in_use` | 409 | Email ya registrado |
| `email_not_verified` | 403 | Login con el email sin verificar |
| `invalid_code` | 400 | Codigo de verificacion malo, vencido o ya usado |
| `resend_too_soon` | 429 | Reenvio antes de los 60s |
| `inactive_user` | 403 | Cuenta desactivada |
| `throttled` | 429 | Superaste los intentos desde esa IP |
| `invalid` | 400 | Error de validacion de campo (ver `errors`) |

## Seguridad

- **Argon2id** como hasher principal (`PASSWORD_HASHERS`), con los parametros que
  ya usaba `hashPassword()` en `web_rrhh_back`.
- El **codigo de verificacion tambien va hasheado** en la base. Si alguien lee la
  tabla, no puede completar el registro de otra persona.
- Los **tokens no viajan en la URL** (a diferencia del `?token=` de
  `web_rrhh_back`, que era necesario para servir archivos en `<img>`). Si mas
  adelante hacen falta archivos protegidos, se resuelve con URLs firmadas de
  corta duracion.
- El codigo es de **un solo uso** y ademas expira a los 10 minutos.
- El codigo y la contrasena se verifican con `check_password()`, que es
  constante en el tiempo. Los errores no distinguen entre "email no existe",
  "contrasena mala" y "codigo incorrecto", para no permitir enumerar cuentas.
- **Throttles por IP**: 10 intentos de login, 10 de verificacion y 5 de registro
  cada 15 minutos (misma ventana que el `rateLimit()` de los backends Node).
- El **rol nunca se acepta desde la API** y hay un constraint en la base que
  impide que un paciente tenga `is_staff` (que abriria el admin de Django).

> Con la cache local de Django cada worker lleva su propio contador de throttles.
> Para correr varios procesos hay que poner Redis o Memcached en `CACHES`.

## Verciones

- Python 3.14, Django 6.1
- PostgreSQL 18 (`psycopg` 3)
- `djangorestframework` 3.18, `djangorestframework-simplejwt` 5.5
- `argon2-cffi` 25.1, `django-cors-headers` 4.9

## Tests

```bash
python manage.py test apps
```

45 tests. Cubren el registro (incluido el intento de escalada de privilegios),
la verificacion, el login, los tokens, los roles, los permisos y el flujo
completo por HTTP real (`apps/accounts/test_http.py`, que levanta un servidor
de verdad con `LiveServerTestCase`).

> Hay que pasar `apps` como label: sin argumentos, Django busca tests solo en
> la raiz del proyecto y los apps viven un nivel mas abajo, asi que no encuentra
> ninguno.

## Estructura

```
clinica_back/
  manage.py
  clinica_back/          # proyecto: settings, urls, wsgi, env, bootstrap
  apps/
    accounts/            # usuarios, roles, verificacion por email
      models.py          # User + EmailVerificationCode
      serializers.py     # register / verify / login / change-password
      views.py           # endpoints + emision de tokens y cookies
      permissions.py     # IsStaffUser, IsAdminUser, IsPatient
      throttling.py      # anti fuerza bruta por IP
      exceptions.py      # errores con `code` estable
      services.py        # envio de emails
      admin.py           # alta y gestion de usuarios
      templates/         # email de verificacion
    clinica/             # dominio de la clinica (vacio, listo para crecer)
```

## Proximos pasos

Lo siguiente seria agregar en `apps/clinica/`: perfiles de paciente y medico,
disponibilidad, agenda de turnos, historias clinicas y consentimientos. Los
permisos por rol ya estan listos en `apps/accounts/permissions.py`.
