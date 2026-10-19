# Sprint 12 — Base y decisiones

- **BASE_S12:** `7503c32896023b510cc93f03c390f0b249304b12` (`origin/main@7503c32`).
- **Rama:** `feat/s12-rewards-redemption`.
- **Producción (§0.2):** consulta ejecutada por el usuario el 19/10/2026 en el SQL Editor de Supabase: `rewards=0`, `redemptions=0`, `redemption_tx=0`, `dup_rewards=0`. Cumple lo esperado. No se volvió a consultar producción en este paso. Si el despliegue ocurre más de un día después, se repite según 06 §6.5.

## Decisiones

Estado al cierre local del 19/10/2026: las decisiones están implementadas o aplicadas según
su alcance. Las aprobaciones del plan se conservan; implementación local no implica
publicación ni despliegue.

| ID | Decisión | Estado |
|---|---|---|
| S12-D01 | El S12 absorbe saldos, resumen y movimientos. El ranking Top 10 era recortable a S13; se entregó completo (carril C integrado) | **Aprobada e implementada en local** 19/10 |
| S12-D02 | Endurecer la base con una sola migración aditiva y un guard de precondición: (1) vincular la fila `REDEMPTION` con su vale; (2) `UNIQUE(user, institution, period_month)`; (3) un CHECK de forma para `REDEMPTION`; (4) una recompensa por institución. Antes se verifica producción en solo lectura | **Aprobada e implementada en local** 19/10 |
| S12-D03 | `CANJE_REALIZADO` va en S12. `RECOMPENSA_DISPONIBLE` y los reintentos automáticos pasan a S13 | **Aprobada e implementada en local** 19/10 |
| S12-D04 | El operador ve código, fecha, recompensa y "Nombre I.", nunca email, cédula ni ID | **Implementada en local** 19/10 |
| S12-D05 | Código `CA-XXXX-XXXX-XXXX-XXXX`: Crockford base32 de 80 bits generado con `secrets`, **sin bucle de reintento** | **Implementada en local** 19/10 |
| S12-D06 | Un `POST` repetido recibe un 409 `MONTHLY_LIMIT_REACHED` con el `redemption_id` existente, y la UI muestra ese vale | **Implementada en local** 19/10 |
| S12-D07 | El S12 entrega la capacidad y las pruebas de aceptación. EX-11 tiene su propio gate | **Aceptada y aplicada** 19/10; EX-11 conserva su gate |
| S12-D08 | Worktrees desde `origin/main@7503c32` (S11 ya publicado). Sin rebase final, salvo que `origin/main` avance durante S12 | **Aplicada** 19/10: base `7503c32`, sin rebase; `origin/main` no avanzó |
| S12-D09 | Desempate del ranking: puntos ↓, `MAX(credited_at)` ↑, `user_id`. Si se elige `ALIAS` con el alias vacío, o no hay perfil, se muestra `Usuario privado` | **Implementada en local** 19/10 |
| **S12-D10** | El correo de canje **no incluye el código**: nombra la institución y la recompensa, remite a la plataforma y lleva el aviso de simulación. Sigue la política vigente de `dispatch._enviar_email`: el correo no transporta nada que otorgue algo | **Aprobada e implementada en local** 19/10 (delegada por el usuario) |
| **S12-D11** | Entrada estricta (`StrictInputMixin`). `redeem` no acepta body, y un `cost` o `credit_cost` devuelve 400. En el formulario del operador, `credit_cost` es `read_only` y se ignora. Así el costo nunca viene del cliente (D-02) | **Aprobada e implementada en local** 19/10 (delegada por el usuario) |
| **S12-D12** | La ruta del operador es **`/institucion/recompensas`**. `(citizen)/recompensas` y `(operator)/recompensas` resolverían la misma URL, y Next.js no lo permite | **Aprobada e implementada en local** 19/10 (delegada por el usuario; corrige el plan §22) |

## Cierre local y pendientes

Los carriles A, B y C quedaron integrados en `feat/s12-rewards-redemption`; el carril C no
se recortó. El estado es **implementado y verificado en local, sin push, sin PR y sin
despliegue**. Las migraciones `rewards_app.0005` y `notifications_app.0002` no se aplicaron
en producción. El resultado de la verificación única, los fixes y los desvíos quedan en
[PROGRESO_SPRINTS.md](../PROGRESO_SPRINTS.md#sprint-12--catálogo-de-recompensas-y-canje-simulado).

Antes del PR quedan pendientes la regularización de la facturación de GitHub Actions y
un commit de CI aparte que excluya de `ci-backend.yml` los tres launchers de EX-10
(`test_ex10_official_launcher.py`, `test_ex10_launcher.py`,
`test_ex10_preflight_launcher.py`): está propuesto, no aplicado. Repetir §0.2 si el despliegue
ocurre más de un día después del conteo del 19/10.

**Pendiente S13 (03 §A0 #5):** cuando `RECOMPENSA_DISPONIBLE` tenga plantilla,
`test_un_tipo_sin_plantilla_queda_fallido_y_no_envia` deberá cambiar otra vez de ejemplo;
en S12 usa ese tipo porque `CANJE_REALIZADO` ya tiene plantilla. La notificación de
disponibilidad y los reintentos automáticos siguen fuera del alcance de S12.
