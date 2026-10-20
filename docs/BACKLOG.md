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
| 4 | CRUD de instituciones, categorías y operadores | S2 · S9 | Parcial: modelos/admin y `promote_user`; sin CRUD institucional completo por API |
| 5 | Creación de reportes, foto, mapa y advertencia de duplicados | S5 · S6 | Completado |
| 6 | Clasificación IA y auditoría | S8 · post-S12 | Implementada y activa con v2; S8 sigue en curso por CA-4 y cierre metodológico pendiente |
| 7 | Confirmaciones ciudadanas de reportes activos | S7 | Completado |
| 8 | `rewards_app`: puntos pendientes/acreditados, créditos 1:1 y reversión | S2 · S9 · S10 | Completado |
| 9 | Panel institucional: validación, estados, evidencias y administración de recompensa | S9 · S10 · S12 | Completado; smoke autenticado integral S12 pendiente |
| 10 | Ranking mensual y privacidad configurable | S4 · S12 (absorbe S11 funcional) | Completado — implementado, desplegado y verificado (19/10/2026) |
| 11 | Canje simulado de 1.000 créditos y límite mensual | S12 | Completado — implementado, desplegado y verificado (19/10/2026) |

> **Nota histórica (22/08–29/08/2026; no es el estado vigente de la tabla).** El ítem 1 pertenece íntegramente al Sprint 1, cerrado el 20/08/2026. Los
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

> **Actualización S12 (19/10/2026).** Los ítems 10 y 11 incluyen el ranking completo,
> resumen, saldos y movimientos absorbidos del S11 funcional, catálogo, gestión institucional
> y canje con límite mensual garantizado en la base. PR #43 se integró mediante `aebf900`;
> las migraciones se aplicaron en Heroku y el frontend se desplegó en Vercel. El smoke
> productivo sin canjear quedó verificado; el autenticado con Google sigue pendiente del
> usuario. Los tres tests preexistentes de EX-10/S11 se corrigieron en PR #48;
> el defecto real de enumeración se trata en PR #50, abierto al corte del 20/10. `RECOMPENSA_DISPONIBLE` y los reintentos automáticos
> quedan para S13.

## Prioridad Media

| # | Ítem | Sprint(s) | Estado |
|---|---|---|---|
| 12 | Dashboard institucional con métricas | S13 | Completado — implementado, desplegado y verificado (20/10/2026) |
| 13 | Simulación de publicaciones de X/Twitter | S13 | Completado (comando `load_x_mock_dataset`, desplegado el 20/10/2026); cargar el dataset en producción es una decisión aparte |

> **Alcance revisado el 20/08/2026 (CT-43).** El ítem 13 es una **simulación interna** de
> publicaciones de X/Twitter, cargada desde un dataset del propio proyecto: no usa la API de X, ni
> OAuth, ni *scraping*, ni credenciales sociales. **Telegram queda fuera del MVP.**

## Prioridad Baja

| # | Ítem | Sprint(s) | Estado |
|---|---|---|---|
| 14 | PWA / aplicación móvil | Fuera del MVP | No planificado |
| 15 | Insignias, niveles, campañas, GPS de proximidad y similitud perceptual | Fuera del MVP | No planificado |

## Pendientes operativos y técnicos al 20/10/2026

| Ítem | Estado y siguiente paso |
|---|---|
| Redis tras rotación de credenciales | Hecho en PR #46: normalización automática; `CERT_NONE` permanece como deuda de configuración |
| Uso de IA y pruebas de prompts | Hecho en PR #47/#49: tokens, `ai_usage_summary`, v2 y comandos manuales; IA activa en Heroku v22 |
| Correo Brevo | Configurado en v23/v24; las dos aprobaciones `ENVIADO` son del 23/08 y 29/08 (previas a Heroku); el primer envío con esta configuración aún no se observó. Plantillas actuales: identidad aprobada/rechazada y canje |
| Medición v2 independiente | Pendiente: nuevas fotos de la planilla del usuario, incluida la foto del bache; los 13 casos conocidos tienen sesgo optimista |
| Dominio propio para correo | Pendiente: autenticar el dominio para resolver advertencias de DKIM/DMARC y reducir riesgo de spam del remitente Gmail |
| EX-10 | Tres tests corregidos en PR #48; **en PR #50**, abierto/CI corriendo, se corrige descriptor compartido tras fork. Nueva corrida requiere reaprobación de fuentes; no cambia el resultado histórico |
| Edición manual de prioridad | Función inexistente: definir permisos, contrato, UI y auditoría antes de implementarla |
| Notificaciones S13 | Cerrado: desplegadas el 20/10 (v25); primer envío real observado pendiente (CT-59) |
| Motivo del rechazo visible en el panel | Pendiente (09 V1 del plan S13): el detalle de un reporte `RECHAZADO` responde 404 y el correo no puede prometer el motivo |
| Pasada UX del portal (B4 de S13) | Recortada por S13-D17; queda para S14/S15. Hallazgo menor del smoke: la tipografía del gráfico de tendencia escala mucho en escritorio |
| Smoke S12 autenticado | Pendiente del usuario con Google; no se generaron canjes/recompensas para el smoke público |
| Rotación de credenciales heredadas de Vercel | Retirada ya ejecutada en #41; rotaciones pendientes según [runbook §§12–13](runbooks/HEROKU_MIGRATION.md#12-rotación-de-secretos), sin evidencia de cierre posterior |
| Ramas remotas S05–S09 | Unas 25 sin mergear; limpieza opcional, revisar aportes antes de borrar |
| Plan S13 | Fuera del repo en `~/project-s13-plan-20261019/`; arrancar desde `origin/main` |

## Riesgos (Anexo E.2)

| Riesgo | Nivel | Mitigación | Seguimiento |
|---|---|---|---|
| Alcance excesivo | Alto | Recompensas reales, insignias, campañas y antifraude avanzado quedan fuera del MVP | Todos los sprints |
| Farming de puntos | Alto | Puntos pendientes, una confirmación por usuario/reporte, autor excluido, validación institucional | S2 · S7 · S9 |
| Reportes duplicados | Medio | Advertencia de proximidad (`ST_DWithin`) y opción de confirmar el reporte existente | S6 |
| Fotos repetidas | Medio | Hash exacto (`file_hash` SHA-256) en el MVP; similitud perceptual queda como mejora futura | S6 |
| Errores de IA | Medio | Revisión humana y auditoría; sin rechazo automático | S8 · S9 |
| Privacidad del ranking | Medio | Visibilidad configurable (`FULL_NAME` por defecto), modo "Usuario privado" | S4 · S12 |
| Costos o cambio de proveedor de IA | Medio | Arquitectura desacoplada (`ai_client_app`) y mocks de prueba | S8 |
| Privacidad de cédulas/selfies | Alto | HMAC-SHA-256, almacenamiento privado y acceso restringido | S4 |
| Presupuesto real de recompensas | Alto | El MVP usa vales simulados; convenios reales quedan fuera de alcance | S12 |
| Inconsistencias de saldos | Medio | Ledger transaccional inmutable y reversiones auditables | S2 · S10 |
| Rendimiento del mapa/ranking | Medio | Índices GIST/B-tree, bbox máximo y canónico, caché Redis generacional con TTL de 60 s y agregaciones acotadas al mes en curso | S5 · S11 · S15 |
| Dependencia de dos integrantes | Medio | Documentación por sprint, PR con revisión cruzada, este tablero | Todos los sprints |
| Sprint 16 de solo dos días | Medio | Todo debe cerrarse en el Sprint 15; el 16 es exclusivamente cierre | S15 · S16 |
