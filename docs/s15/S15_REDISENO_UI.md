# S15-UI — Rediseño institucional

Fecha: 20/10/2026. Carril del Sprint 15, rama `feat/s15-ui`, worktree
`ciudadania-activa-s15-ui`, creado desde `feat/s15-validation@856afc4`.
Fases 0–5 y B1 ya registradas; esta entrega corresponde únicamente a la Fase 6.
No implica cierre del Sprint 15 ni despliegue.

## Decisiones aplicadas

- Referencia visual: Lovable `f997849`, Anexo C.1 de la tesis. Paleta azul,
  Inter, tarjetas claras, navegación superior y secciones privadas laterales.
  No se incorporan datos mock del prototipo ni nuevas dependencias.
- Primitivas compartidas para tarjetas, botones y controles en identidad,
  creación y seguimiento de reportes, confirmación, operación y auditoría.
  Recompensas ya migradas en fases anteriores; se revisaron sus tokens restantes.
- Se retiran los alias `brand-*` y sus usos residuales en otras pantallas.
  Texto secundario en `muted` (`#536466`); estados de texto en variantes `*-ink`.
- Categorías como tarjetas con radios nativos e íconos Lucide decorativos,
  generadas únicamente desde el catálogo real. La selección sigue enviando
  el mismo identificador. El grupo conserva `report-category` para enfocar errores.
- Confirmación con resumen de foto, categoría y ubicación derivado de las
  validaciones existentes; publicación de foto tras validación institucional,
  sin publicar selfie ni documento de identidad. Se conserva el aviso del paso foto.
- Paginadores con salto de línea y filtros de fechas apilados en móvil.
  Tablas anchas mantienen desplazamiento interno con foco visible.
- Se preservan URLs, API, permisos, consentimientos, validaciones, estados y orden
  de pasos: categoría → descripción → foto → ubicación → confirmación.
  Los cinco componentes más sensibles se migraron al final.
- Leaflet conserva sus cargas dinámicas con `ssr: false`; no se importa desde
  navegación ni primitivas. Sin cambios de backend en esta fase.

## Verificación de la Fase 6

Entorno sin red: por instrucción del usuario no se ejecutan build, Playwright
ni capturas en esta sesión. No se instala ninguna dependencia ni se hace commit.
Se aplicaron las skills versionadas de diseño, revisión y verificación sin
actualizarlas ni ejecutar sus scripts. Revisión independiente de solo lectura:
se corrigieron los dos hallazgos (selectores de métricas dependientes de
`.audit-input` y selector de categoría del E2E full-stack anterior).

| Comando desde `frontend/` | Resultado de esta sesión |
| --- | --- |
| `npm run lint` | PASS, código 0 |
| `npm run format:check` | PASS, código 0 |
| `npm run typecheck` | PASS, código 0 |
| `npm test` | PASS, código 0; 53 archivos, 477 pruebas; 21,82 s |

Los cuatro comandos se ejecutaron una vez. Emitieron el aviso del entorno
`Failed to create stream fd: Operation not permitted`, sin impedir la ejecución
ni alterar sus códigos de salida. El grep final insensible a mayúsculas detectó
cuatro líneas residuales con el morado anterior en login y `RoleGuard`; se
sustituyeron únicamente las clases literales por `primary` después de esa corrida.
Lint y formato de esos dos archivos se comprobaron aparte; no se repitió Vitest
ni tipos tras esa sustitución de clases.

Pruebas nuevas: selección de tarjeta desde catálogo, conservación/bloqueo,
resumen basado en campos válidos y aviso factual de privacidad. Se adaptan
Vitest y selectores de Playwright al grupo de radios. Las specs de identidad y
auditoría comprueban contención a 320, 375, 768 y 1280 px; la suite S15 añade 768 px
para identidad. Son pruebas preparadas, no resultados de navegador.

Comprobación de retiro de colores y alias (desde la raíz):

```bash
rg -n -i 'brand-|#(6950a1|f17db1|00b26b|f37043|d79b00|007749|a63d15)' frontend/src frontend/e2e frontend/e2e-fullstack frontend/e2e-s15-fullstack frontend/e2e-ui-captures
```

Resultado: sin coincidencias (código 1 de `rg`). Alcance: código y pruebas del frontend.
Los informes históricos y las skills versionadas quedan fuera de este chequeo;
no se modifican para borrar evidencia histórica. No se requieren excepciones
para colores antiguos en código de producción.

### Build y E2E (verificado por Claude)

Completar tras revisar el diff, sin atribuir estos resultados a Codex:

- `npm run build`: **OK** (Next.js en producción, 20/10/2026).
- `PLAYWRIGHT_PRODUCTION=1 npm run test:e2e` (suite completa una sola vez):
  **86/86 aprobadas** (`PLAYWRIGHT_PRODUCTION=1`, Chromium, 2 workers, ~1,2 min; sin fallos en la corrida final), cantidad, duración y fallos.
- `npm run test:s15:paths`: **6/6 aprobadas**.
- `npm run test:e2e:s15:fullstack`, solo con preparación local disponible:
  **no ejecutado**: requiere la preparación de API/Storage sintéticos del carril S15; queda para la verificación integrada después del rebase sobre `main`.

Specs afectadas o de regresión para revisar dentro de la suite completa:
`identity.spec.ts`, `create-report.spec.ts`, `operator-reports.spec.ts`,
`admin-audit.spec.ts`, `confirm-report.spec.ts`, `rewards.spec.ts`,
`incentives-journey.spec.ts`, `metrics.spec.ts` y `s15-ux.spec.ts`.
Helpers actualizados: `report-create-fixtures.ts`; flujos externos a esa suite:
`e2e-fullstack/flows.spec.ts` y `e2e-s15-fullstack/flows.spec.ts`.

### Evidencia visual (verificada por Claude)

Capturas generadas en esta sesión: **ninguna**. Usar el
[procedimiento de Fase 0](../../frontend/e2e-ui-captures/README.md), con
`S15_UI_CAPTURE_STAGE=despues`, conservando las capturas anteriores.
Destino excluido de Git: `.s15-ui-captures/despues/`, 14 rutas × 375/1280 px.

- Generación y cantidad de PNG: **28/28 PNG** en `.s15-ui-captures/{antes,despues}/` (14 rutas × 375/1280 px; fuera de git).
- Revisión visual de antes/después a 375/1280 px: revisadas por Claude la portada, el mapa, el detalle, Estadísticas y el mapa móvil; el fondo negro del mapa y de las fotos corresponde a las teselas e imágenes sintéticas del arnés.
- Comprobación de foco, contraste y ausencia de scroll horizontal de página a
  320/375/768/1280 px: cubierto por las E2E de S15-UX y de identidad a 320/375/768/1280 px (sin scroll horizontal de la página; corregido el desborde de la cola de identidades).

Mensaje de commit propuesto (sin ejecutar):
`feat(ui): migrate private flows and finish S15-UI`.
