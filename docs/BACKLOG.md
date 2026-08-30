# Backlog — Ciudadanía Activa

Tablero de trabajo trasladado desde el **Anexo E** de la tesis (Roadmap, backlog MVP y análisis
de riesgos), con las prioridades originales. Se actualiza a medida que cada ítem se planifica en
un sprint concreto — ver [PLAN_DESARROLLO.md](PLAN_DESARROLLO.md) y [PROGRESO_SPRINTS.md](PROGRESO_SPRINTS.md).

## Prioridad Alta

| # | Ítem | Sprint(s) | Estado |
|---|---|---|---|
| 1 | Configuración base: Django, Next.js, PostgreSQL/PostGIS y CI | S1 | Completado |
| 2 | Autenticación Google OAuth, roles y permisos | S3 | Completado |
| 3 | Verificación manual con cédula/selfie | S4 | Completado |
| 4 | CRUD de instituciones, categorías y operadores | S2 · S9 | En curso |
| 5 | Creación de reportes, foto, mapa y advertencia de duplicados | S5 · S6 | En curso |
| 6 | Clasificación IA y auditoría | S8 | En curso |
| 7 | Confirmaciones ciudadanas de reportes activos | S7 | Pendiente |
| 8 | `rewards_app`: puntos pendientes/acreditados, créditos 1:1 y reversión | S2 · S9 · S10 | En curso |
| 9 | Panel institucional: validación, estados, evidencias y administración de recompensa | S9 · S10 | Pendiente |
| 10 | Ranking mensual y privacidad configurable | S11 | Pendiente |
| 11 | Canje simulado de 1.000 créditos y límite mensual | S12 | Pendiente |

> **Estado a 22/08/2026.** El ítem 1 pertenece íntegramente al Sprint 1, cerrado el 20/08/2026. Los
> ítems 4 y 8 abarcan varios sprints: el Sprint 2 entregó su base de datos —`institutions`,
> `categories`, `institution_operators` y las tres tablas del ledger, con sus restricciones, índices
> y catálogos semilla, más el registro en el admin de Django para inspección interna— pero la
> funcionalidad que describen (gestión de catálogos y operadores según rol; cálculo de puntos
> pendientes y acreditados, créditos 1:1 y reversión) llega en los Sprints 9 a 11, de modo que
> siguen **En curso**. El **ítem 3** pasó a **Completado** el 23/08/2026, al
> cerrarse el Sprint 4: infraestructura productiva aprovisionada, integración por Pull Request
> con CI en verde, migración aplicada en producción y flujo real verificado de extremo a extremo
> con una identidad verdadera. El **ítem 5** pasó a **En curso** el 26/08/2026 con S5-A:
> API pública local del mapa, catálogo público de categorías, bbox privado/indexable,
> caché y seed sintético. La interfaz del mapa y toda la creación de reportes de S6 siguen
> pendientes; esta implementación local todavía no fue integrada ni desplegada.
> El **ítem 6** pasó a **En curso** el 29/08/2026 con S8-A: contrato de clasificación IA
> cerrado por escrito, prompt versionado y ADR-0008 y ADR-0009 escritos. Es un avance
> **documental**: no hay integración con Gemini, ni SDK, ni migración, ni endpoints, y
> `ai.classify_report` sigue siendo un no-op.

## Prioridad Media

| # | Ítem | Sprint(s) | Estado |
|---|---|---|---|
| 12 | Dashboard institucional con métricas | S13 | Pendiente |
| 13 | Simulación de publicaciones de X/Twitter | S13 | Pendiente |

> **Alcance revisado el 20/08/2026 (CT-43).** El ítem 13 es una **simulación interna** de
> publicaciones de X/Twitter, cargada desde un dataset del propio proyecto: no usa la API de X, ni
> OAuth, ni *scraping*, ni credenciales sociales. **Telegram queda fuera del MVP.**

## Prioridad Baja

| # | Ítem | Sprint(s) | Estado |
|---|---|---|---|
| 14 | PWA / aplicación móvil | Fuera del MVP | No planificado |
| 15 | Insignias, niveles, campañas, GPS de proximidad y similitud perceptual | Fuera del MVP | No planificado |

## Riesgos (Anexo E.2)

| Riesgo | Nivel | Mitigación | Seguimiento |
|---|---|---|---|
| Alcance excesivo | Alto | Recompensas reales, insignias, campañas y antifraude avanzado quedan fuera del MVP | Todos los sprints |
| Farming de puntos | Alto | Puntos pendientes, una confirmación por usuario/reporte, autor excluido, validación institucional | S2 · S7 · S9 |
| Reportes duplicados | Medio | Advertencia de proximidad (`ST_DWithin`) y opción de confirmar el reporte existente | S6 |
| Fotos repetidas | Medio | Hash exacto (`file_hash` SHA-256) en el MVP; similitud perceptual queda como mejora futura | S6 |
| Errores de IA | Medio | Revisión humana y auditoría; sin rechazo automático | S8 · S9 |
| Privacidad del ranking | Medio | Visibilidad opt-in, modo "Usuario privado" | S11 |
| Costos o cambio de proveedor de IA | Medio | Arquitectura desacoplada (`ai_client_app`) y mocks de prueba | S8 |
| Privacidad de cédulas/selfies | Alto | HMAC-SHA-256, almacenamiento privado y acceso restringido | S4 |
| Presupuesto real de recompensas | Alto | El MVP usa vales simulados; convenios reales quedan fuera de alcance | S12 |
| Inconsistencias de saldos | Medio | Ledger transaccional inmutable y reversiones auditables | S2 · S10 |
| Rendimiento del mapa/ranking | Medio | Índices GIST/B-tree, bbox máximo y canónico, caché Redis generacional con TTL de 60 s y agregaciones acotadas al mes en curso | S5 · S11 · S15 |
| Dependencia de dos integrantes | Medio | Documentación por sprint, PR con revisión cruzada, este tablero | Todos los sprints |
| Sprint 16 de solo dos días | Medio | Todo debe cerrarse en el Sprint 15; el 16 es exclusivamente cierre | S15 · S16 |
