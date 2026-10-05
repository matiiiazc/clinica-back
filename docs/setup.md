# Puesta en marcha para colaboradores

Como levantar los dos repos localmente. Todo lo que hay que hacer esta escrito
aca: no hace falta ningun `.env` real del repo.

## Que NO se comparte

Cada repo tiene un `.env` con claves reales que **nunca se sube a Git**. Lo que
se sube es `.env.example`, que es la plantilla con los valores de ejemplo.

Si alguien te pasa un `.env` por WhatsApp, mail o chat, **cambia las claves** de
ese proyecto antes de seguir. Se regeneran en un segundo (ver abajo).

## Requisitos

| Herramienta | Version | Nota |
|---|---|---|
| Python | 3.14 o superior | Probado en 3.14.5 |
| Node | 24 o superior | |
| pnpm | 11 o superior | El front se instala con pnpm, no con npm |
| PostgreSQL | 15 o superior | O una URL de Neon / Railway / Docker |

Si usan la `nvm` de Node, `.nvmrc` dice la version exacta.

---

## 1. Base de datos

Opciones, de menos a maslabor:

**PostgreSQL local**

```bash
createdb clinica_back
```

Queda escuchando en `127.0.0.1:5432` con usuario `postgres`.

**Neon o cualquier Postgres hosteado**

Pedirle a la persona que administra el proyecto la *connection string* por un
canal seguro. Va en `DATABASE_URL` del `.env` del back (opcion A de la
plantilla, hay que descomentar esa linea y borrar las variables sueltas).

---

## 2. Back (`clinica_back`)

```bash
git clone <url-del-repo> clinica_back
cd clinica_back

python -m venv .venv
.venv\Scripts\activate        # en Windows
# source .venv/bin/activate   # en Linux/Mac

pip install -r requirements.txt

copy .env.example .env        # en Windows
# cp .env.example .env        # en Linux/Mac
```

### Generar las claves

`SECRET_KEY` y `JWT_SECRET` van distintas entre si, y las dos tienen que ser
valores propios de cada maquina. Para generarlas:

```bash
python -c "from django.core.management.utils import get_random_secret_key as g; print('SECRET_KEY=' + g())"
python -c "from django.core.management.utils import get_random_secret_key as g; print('JWT_SECRET=' + g())"
```

Copiar cada salida en su linea del `.env`.

### Migraciones y usuario inicial

```bash
python manage.py migrate
python manage.py createsuperuser
```

Ojo: `python manage.py test` **sin argumentos no encuentra tests y muestra
"NO TESTS RAN"**, y eso no es un error. Hay que pasar la app:

```bash
python manage.py test apps
```

### Arrancar

```bash
python manage.py runserver 127.0.0.1:8000
```

Deberia responder `{"status": "ok", "database": "ok"}` en http://127.0.0.1:8000/health

---

## 3. Front (`clinica_front`)

```bash
git clone <url-del-repo> clinica_front
cd clinica_front

pnpm install
pnpm typecheck                # debe salir limpio

copy .env.example .env        # en Windows
pnpm dev
```

---

## 4. El puerto tiene que coincidir en los dos lados

Este es el error mas comun y el mas confuso, asi que va aparte.

El front corre en `VITE_PORT` (5173 por defecto). El back tiene que permitir
ese origen en tres variables suyas:

```
CORS_ORIGINS=http://localhost:5173
CSRF_TRUSTED_ORIGINS=http://localhost:5173
FRONTEND_URL=http://localhost:5173
```

Si se cambia el puerto del front, hay que cambiar **las tres** del `.env` del
back, si no el login y el registro fallan con un error de CORS o de CSRF que no
dice que el problema es el puerto.

Si el puerto默认 esta ocupado, Vite prueba el siguiente libre y lo dice al
arrancar. Se puede fijar con `VITE_PORT` en el `.env` del front.

---

## Problemas frecuentes

**"La contraseña del administrador no es válida" al crear el superuser.**
Postgres no acepta la conexion: casi siempre es que `DB_PASSWORD` del `.env`
no es la del usuario de Postgres de esa maquina.

**El login o el registro se rechaza con error de CORS.**
El `VITE_PORT` del front no coincide con `CORS_ORIGINS` del back. Ver arriba.

**`429 throttled` al probar el registro.**
El endpoint de registro permite 5 requests cada 15 minutos por IP. Reiniciar
el back limpia la cuenta, porque el throttle vive en la cache en memoria de
Django. Para desarrollo se puede subir `DRF_THROTTLE_REGISTER` en el `.env`.

**El codigo de verificacion no llega por email.**
No hay SMTP configurado, y eso es lo esperado en desarrollo: sin `SMTP_HOST`
los codigos se imprimen en la terminal del back y ademas se devuelven en el
campo `dev_code` de la respuesta, asi que se puede completar el registro sin
configurar nada. Para probar el envio real hay que completar `SMTP_HOST`,
`SMTP_USER` y `SMTP_PASSWORD`.

**El front levanta en un puerto raro.**
Casi siempre hay otro proyecto de Vite corriendo. Se ve el puerto en la salida
de `pnpm dev`.