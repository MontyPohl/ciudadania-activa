# S15-UI · Fase B1 · backend público

Fecha: 20/10/2026. Worktree `ciudadania-activa-s15-ui`, rama `feat/s15-ui`, base
`856afc4`. Alcance exclusivo de esta sesión: `backend/` y `docs/`.

## Implementación

- Foto pública desde `ASIGNADO` y evidencia en `RESUELTO`/`CERRADO`, con proxies
  independientes de los tokens privados. Solo usan claves persistidas y el bucket de
  reportes; no exponen selfies ni documentos. 404 uniforme, `nosniff`, `no-store`,
  scope `public_report_media: 120/min`. Estado consultado en cada descarga.
- Lista blanca de mapa y detalle ampliada con `photo_url`; el detalle agrega
  `resolution_evidences`. La selección de existencia de foto usa `Exists` para evitar
  una consulta por elemento en el listado. Evidencias por `created_at, id`, todos los
  ciclos del reporte, sin descripción ni datos del operador.
- Métricas: seis meses locales de recibidos/primera resolución y categorías globales
  del mes actual; k=5 en cada celda nueva, con `null` y `suppressed`, también para cero.
  Supresión complementaria frente a los totales de ventana y mes actual. Los totales
  existentes mantienen sus reglas y fórmulas.
- Cachés públicas de listado y métricas pasan a `v2`; sin migraciones ni dependencias
  nuevas. ADR-0017 sustituye parcialmente ADR-0007; enmienda explícita a S13-D08/D14.
- No hay tratamiento retroactivo: fotos existentes sintéticas/de prueba, confirmado
  por el usuario. El aviso de publicación se incorporará en Fase 5.

## Contrato para Fase 5

Fuente completa, ejemplos y errores: [docs/API.md, enmienda S15-B1](../API.md#s15-b1-medios-y-agregados-publicos).

- Elemento de listado: exactamente `id, category, institution, description, status,
  priority, latitude, longitude, created_at, photo_url`.
- Detalle: esos diez campos más `confirmation_count` y `resolution_evidences`.
- `photo_url: string | null`, ruta relativa al origen de la API:
  `/api/v1/public/reports/{id}/photo/`. Nulo sin foto o fuera de estados publicables.
- `resolution_evidences: {id: UUID, evidence_url: string, created_at: ISODateTime}[]`,
  ruta `/api/v1/public/reports/{id}/evidence/{evidence_id}/`; vacío fuera de
  `RESUELTO`/`CERRADO` o sin evidencia.
- Métricas conserva `window, generated_at, min_cell_size, totals, institutions` y agrega:
  `trend: {period: YYYY-MM, received: number|null, resolved: number|null,
  suppressed: ("received"|"resolved")[]}[]` (exactamente seis puntos cronológicos);
  `current_month_by_category: {category: {id,name,slug}, received: number|null,
  suppressed: "received"[]}[]` (catálogo completo, por nombre/PK).
- `resolved` cuenta la primera transición histórica, no el estado actual ni los ciclos
  posteriores. El mes actual termina en `generated_at`, límite exclusivo.
- No convertir `null` a cero ni reconstruir celdas suprimidas. La supresión adicional
  puede ocultar un conteo ≥5. Las categorías incluyen las inactivas para conservar
  conteos históricos completos; no hay cruce categoría × institución.

**Integración pendiente:** frontend no modificado por B1. Sus parsers estrictos deben
actualizarse en Fase 5 antes del despliegue. Coordinar backend y frontend: publicar B1
contra el cliente anterior rompe la lectura de mapa/métricas. También quedan para Fase 5
el aviso, carga diferida de imágenes y visualización accesible de evidencia/gráficos.

## Verificación y limitaciones del entorno

- Intentado desde `backend/`: `pytest apps/map_app apps/analytics_app apps/reports_app -q`.
  Salida: `/bin/bash: line 93: pytest: command not found` (exit 127). El intérprete
  del sistema tampoco tiene Django: `ModuleNotFoundError: No module named 'django'`.
  No se llegó a PostGIS/Redis; no se instalaron dependencias ni se insistió con la suite.
- `ruff check .` y `ruff format --check .`: inicialmente no disponibles en `PATH`
  (exit 127, `ruff: command not found`). Se utilizó el ejecutable existente cacheado
  `/home/monty/.cache/uv/archive-v0/_8qkFePyC5Is6q-G/ruff-0.6.9.data/scripts/ruff`,
  sin instalación. Verificación del árbol final: lint pasa; formato pasa.
- Revisión independiente mediante `requesting-code-review`: sin hallazgos críticos o
  importantes confirmados; revisión estática, sin afirmar ejecución de pruebas.
- Sin build, Playwright, capturas, commit, push ni cambios de esta sesión en frontend.
  Los cambios concurrentes de Fase 1 en frontend pertenecen a otra sesión.
- Capturas generadas: ninguna (B1 es solo backend/documentación).

Pruebas nuevas: `map_app/tests/test_public_media.py`,
`analytics_app/tests/test_public_breakdowns.py` y GPS IFD JPEG/PNG en
`reports_app/tests/test_photos.py`. Se actualizan las listas blancas existentes y la
clave de caché en las pruebas de métricas.

Comandos pendientes para Claude, desde el worktree correcto y con su entorno backend:

```bash
cd /home/monty/proyectos/ciudadania-activa-s15-ui/backend
pytest apps/map_app apps/analytics_app apps/reports_app -q
ruff check .
ruff format --check .
```

E2E B1: ninguna spec cambia aquí. En Fase 5 revisar y ejecutar las specs afectadas de
mapa (`e2e/s15-ux.spec.ts`), métricas (`e2e/metrics.spec.ts`) y foto de creación
(`e2e/create-report.spec.ts`), junto con sus fixtures/parsers actualizados. La suite
completa sigue reservada para Fase 6.

Mensaje de commit propuesto por el usuario, para después de revisión e integración:
`feat(public): public report photos, evidence and trends`.
