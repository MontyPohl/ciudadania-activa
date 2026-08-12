# ADR-0014 — Modelo de usuario personalizado desde la primera migración

**Estado:** Aceptada (corrección C-06)
**Sprint:** 1
**Fecha:** 2026-08-01

## Contexto

El dominio requiere un `User` propio (identificado por `email`, con roles y estado de verificación
que se agregan en el Sprint 2). Django solo permite fijar `AUTH_USER_MODEL` **antes** de aplicar
la primera migración: cambiarlo después de migrar obliga a recrear la base de datos o a una
migración manual de datos larga y frágil. El Sprint 1 es, por lo tanto, el único momento en que
esta decisión puede tomarse sin costo de retrabajo.

## Decisión

- `AUTH_USER_MODEL = "auth_app.User"` se declara en `config/settings/base.py` desde el primer
  commit.
- `apps/auth_app` define un **esqueleto mínimo** de `User`: `AbstractBaseUser` + `PermissionsMixin`,
  clave primaria `UUID`, `email` único como `USERNAME_FIELD`, `full_name`, `is_active`,
  `is_staff`, `date_joined`, con su `UserManager` (`create_user` / `create_superuser`).
- La migración `auth_app.0001_initial` se aplica **antes** que cualquier otra migración que
  referencie usuarios.
- El resto de los campos de dominio (`google_oauth_id`, `role`, `verification_status`) y el modelo
  `citizen_profiles` se agregan en el **Sprint 2** mediante migraciones incrementales sobre este
  mismo modelo — no se redefine ni se vuelve a tocar `AUTH_USER_MODEL`.
- Ninguna app declara claves foráneas a `User` directamente: toda referencia usa
  `settings.AUTH_USER_MODEL`.

## Consecuencias

- Se evita el riesgo bloqueante de migrar con el `User` estándar de Django, que habría hecho
  imposible introducir el modelo propio en el Sprint 2 sin recrear la base de datos.
- El Sprint 1 entrega un modelo de usuario deliberadamente incompleto (sin `role` ni
  `verification_status`); cualquier funcionalidad que dependa de esos campos queda fuera de
  alcance hasta el Sprint 2, incluida la autenticación real.
- Las pruebas de este sprint verifican el comportamiento base (`create_user`,
  `create_superuser`, unicidad de `email`, `get_user_model()` devolviendo `auth_app.User`) sin
  anticipar reglas de rol o verificación.
