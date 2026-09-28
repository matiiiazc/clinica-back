# Cómo trabajamos en este repo

## Ramas

- `main` — lo que está en producción. Nadie commitea directo acá.
- `develop` — donde se integra. Cada PR entra acá.
- `feature/...` — una rama por cosa. `feature/appointments-conflictos`

Flujo: `feature/...` → PR a `develop` → `main` en cada release.

## Antes de abrir un PR

```bash
python manage.py test apps --noinput
```

Los 52 tests tienen que pasar. Si agregaste algo, el test va con el código: no
hay PR sin cobertura de lo que cambió.

## Commits

Mensajes en modo imperativo y en inglés, porque el código va en inglés:

```
Add appointment overlap validation
Fix refresh cookie SameSite in production
```

Tipos que usamos: `Add`, `Fix`, `Refactor`, `Test`, `Docs`, `Chore`.

## Cosas que no se tocan

- **`.env` nunca se commitea.** Tiene `SECRET_KEY`, `JWT_SECRET` y la clave de
  la base. Cada uno tiene el suyo local, copiado de `.env.example`. Si lo
  commiteás por error, hay que rotar las claves.
- **Los roles no se aceptan desde la API pública.** `RegisterSerializer` fuerza
  `paciente` y descarta el `rol` que venga en el body. No aflojar eso.
- **El hash de la contraseña nunca viaja.** El código de verificación se hashea
  con Argon2id y el email lleva el código en claro, nunca el hash.

## Cosas que aprendimos a la mala

- Los tests usan una base propia, así que **un test verde no dice que las
  migraciones estén aplicadas en la base real**. Cuando Agregues un modelo,
  corré `python manage.py makemigrations` y `python manage.py migrate`.
- Los flags de las cookies se calculan al importar los settings, así que
  probá el modo producción en un proceso aparte, no con `override_settings`.
- Los throttles viven en la cache, que es local por proceso. Con varios workers
  cada uno lleva su propio conteo, así que hay que configurar `CACHES` con Redis
  antes de correr gunicorn con más de un worker.

## Levantar en local

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # y completar las claves
python manage.py migrate
python manage.py create_staff admin@clinica.com --rol admin
python manage.py runserver
```
