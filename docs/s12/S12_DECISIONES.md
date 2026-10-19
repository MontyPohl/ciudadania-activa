# Sprint 12 — Base y decisiones

- **BASE_S12:** `7503c32896023b510cc93f03c390f0b249304b12` (`origin/main@7503c32`).
- **Rama:** `feat/s12-rewards-redemption`.
- **Producción (§0.2):** consulta ejecutada por el usuario el 19/10/2026 en el SQL Editor de Supabase: `rewards=0`, `redemptions=0`, `redemption_tx=0`, `dup_rewards=0`. Cumple lo esperado. No se volvió a consultar producción en este paso. Si el despliegue ocurre más de un día después, se repite según 06 §6.5.

## Decisiones

Estados copiados de `PLAN_S12.md` (v5). D01–D03 y D10–D12 están aprobadas; D04–D09 se aplican tal como están en el plan, conservando sus estados originales.

| ID | Decisión | Estado |
|---|---|---|
| S12-D01 | El S12 absorbe saldos, resumen y movimientos. El ranking Top 10 es recortable a S13 | **Aprobada** 19/10 |
| S12-D02 | Endurecer la base con una sola migración aditiva y un guard de precondición: (1) vincular la fila `REDEMPTION` con su vale; (2) `UNIQUE(user, institution, period_month)`; (3) un CHECK de forma para `REDEMPTION`; (4) una recompensa por institución. Antes se verifica producción en solo lectura | **Aprobada** 19/10 |
| S12-D03 | `CANJE_REALIZADO` va en S12. `RECOMPENSA_DISPONIBLE` y los reintentos automáticos pasan a S13 | **Aprobada** 19/10 |
| S12-D04 | El operador ve código, fecha, recompensa y "Nombre I.", nunca email, cédula ni ID | Recomendada |
| S12-D05 | Código `CA-XXXX-XXXX-XXXX-XXXX`: Crockford base32 de 80 bits generado con `secrets`, **sin bucle de reintento** | Recomendada |
| S12-D06 | Un `POST` repetido recibe un 409 `MONTHLY_LIMIT_REACHED` con el `redemption_id` existente, y la UI muestra ese vale | Recomendada |
| S12-D07 | El S12 entrega la capacidad y las pruebas de aceptación. EX-11 tiene su propio gate | **Aceptada** 19/10 |
| S12-D08 | Worktrees desde `origin/main@7503c32` (S11 ya publicado). Sin rebase final, salvo que `origin/main` avance durante S12 | Recomendada (actualizada en v5) |
| S12-D09 | Desempate del ranking: puntos ↓, `MAX(credited_at)` ↑, `user_id`. Si se elige `ALIAS` con el alias vacío, o no hay perfil, se muestra `Usuario privado` | Recomendada |
| **S12-D10** | El correo de canje **no incluye el código**: nombra la institución y la recompensa, remite a la plataforma y lleva el aviso de simulación. Sigue la política vigente de `dispatch._enviar_email`: el correo no transporta nada que otorgue algo | **Aprobada** 19/10 (delegada por el usuario) |
| **S12-D11** | Entrada estricta (`StrictInputMixin`). `redeem` no acepta body, y un `cost` o `credit_cost` devuelve 400. En el formulario del operador, `credit_cost` es `read_only` y se ignora. Así el costo nunca viene del cliente (D-02) | **Aprobada** 19/10 (delegada por el usuario) |
| **S12-D12** | La ruta del operador es **`/institucion/recompensas`**. `(citizen)/recompensas` y `(operator)/recompensas` resolverían la misma URL, y Next.js no lo permite | **Aprobada** 19/10 (delegada por el usuario; corrige el plan §22) |
