# 02 — Contrato del Sprint 13

Es la fuente común de los cuatro carriles. El backend (A, C y D) lo implementa y el frontend (B) lo consume con mocks. Cualquier cambio se acuerda acá **antes** de tocar el código de un carril.

## 0. Convenciones heredadas (no se redefinen)

- Prefijo `/api/v1/`, JWT `Bearer` y permiso por defecto `DenyAll`: cada vista declara el suyo.
- Errores con la forma de S7/S9:
  - `{"detail": "..."}`;
  - una entrada inválida devuelve 400 `{"detail": {"<clave>": ["…"]}}`, **sin `code`**, a través de `apps.core.exceptions.api_exception_handler`.
- **Entrada estricta** con `StrictInputMixin` (`apps.reports_app.serializers`): una clave desconocida o repetida devuelve 400.
- **Contexto institucional del operador:** query `institution_id`, resuelto con `apps.admin_app.selectors.resolve_operator_institution`:
  - un solo vínculo activo: el parámetro puede omitirse;
  - varios: es obligatorio y omitirlo da 400;
  - un UUID ajeno da un 403 genérico.
- **Institución efectiva** de un reporte: `report.institution ?? report.category.default_institution`. Es la misma regla de `reports_app.selectors.operator_reports_for_institution` (API §7.1).
- **IA activa (v3):** con confianza ≥ 0,75, `ai_client_app.orchestration.process_report_classification` reescribe `category`, `institution` y `priority` y pasa el reporte de `CREADO` a `CLASIFICADO` antes de cualquier validación. Las métricas usan siempre los valores **vigentes** al calcular: un reporte reclasificado cuenta en su nueva categoría e institución. `CLASIFICADO` es una de las 7 claves de `by_status`. Los tokens y el costo de la IA **no** forman parte de este contrato: los mide `ai_usage_summary` (CT-58).
- Todas las fechas en ISO-8601 con zona. El **mes local** se calcula en `America/Asuncion` con `rewards_app.periods` (`current_period`, `month_bounds`, `format_period`) y se expone como `"YYYY-MM"`.
- `Cache-Control: private, no-store` en todo lo autenticado.
- Throttles: `dashboard_metrics: "60/min"` y `public_metrics: "120/min"`, cargados en el paso 0.
- **Ninguna respuesta existente cambia de forma** (S13-D14).

## 1. Objetos compartidos

```jsonc
// InstitutionRef (= SessionInstitution del frontend)
{ "id": "uuid", "name": "ANDE", "slug": "ande" }
// CategoryRef (en TS se reutiliza OperatorCategory, que ya tiene este shape)
{ "id": "uuid", "name": "Alumbrado público", "slug": "alumbrado-publico" }
```

## 2. Definición de las métricas (S13-D06, S13-D07)

Sea **P** el mes local `[inicio, fin)` y **E** el alcance: una institución o todas. Un reporte está en **E** si su institución efectiva es la de E. Todas las agregaciones llevan `.order_by()` vacío, para que el `ordering` del `Meta` no se cuele en el `GROUP BY` (lección de S12).

| Campo | Fórmula |
|---|---|
| `reports.received` | Reportes en E con `created_at ∈ P` |
| `reports.by_status` | Esos mismos reportes, por su estado **actual**. Siempre incluye las 7 claves de `ReportStatus`, en 0 si no hay |
| `reports.by_priority` | Ídem por `priority`. Las 3 claves siempre presentes |
| `reports.by_category` | Ídem por categoría: `[{category, count}]`, `count` ↓ y nombre ↑. Solo categorías con `count > 0` |
| `reports.resolution_rate` | De los recibidos en P y no `RECHAZADO`, la fracción que hoy está `RESUELTO` o `CERRADO`. `null` si el denominador es 0 |
| `decisions.validated` | Reportes en E cuya **primera** validación (la fila más vieja de `ReportStatusHistory` con `new_status = ASIGNADO`) cae en P. `ASIGNADO` solo se alcanza validando (verificado en `lifecycle.ALLOWED_PREVIOUS_STATUS`). Una revalidación tras una reversión no vuelve a contar |
| `decisions.rejected` | Reportes en E con una fila `new_status = RECHAZADO` en P. El rechazo solo ocurre una vez |
| `decisions.validation_rate` / `rejection_rate` | `validated / (validated + rejected)` y su complemento. `null` si la suma es 0 |
| `resolution.resolved` | Reportes en E cuya **primera** resolución (la fila más vieja con `new_status = RESUELTO`) cae en P. Es estable ante reversiones y reaperturas |
| `resolution.mean_hours` / `median_hours` | Sobre esos reportes: `(primera resolución − report.created_at)` en horas, con 1 decimal. `null` si n = 0. La mediana se calcula en Python sobre la lista, que es chica |
| `sla.hours` | `institution.sla_hours`. En alcance "todas" es `null` |
| `sla.configured` | `sla_hours` no nulo. En "todas", si **alguna** institución lo tiene |
| `sla.within` / `breached` | Entre los `resolved`, cuántos tardaron ≤ `sla_hours` y cuántos más. En "todas", la suma solo sobre instituciones con SLA. `null` si `configured = false` |
| `sla.compliance_rate` | `within / (within + breached)`. `null` sin SLA o con denominador 0 |
| `sla.open_over_sla` | **Foto del momento**, no del período: reportes en E en estado activo (`CREADO`, `CLASIFICADO`, `ASIGNADO`, `EN_PROCESO`) con `now − created_at > sla_hours`. `null` sin SLA |
| `confirmations.count` | `ReportConfirmation` con `created_at ∈ P` sobre reportes en E |
| `incentives.points_credited` / `credits_issued` | Σ `impact_delta` / Σ `credit_delta` de `RewardTransaction` con `status = ACREDITADO`, `event_type ≠ REDEMPTION`, `institution` en E y `credited_at ∈ P`. Son netos y vigentes: lo revertido deja de ser `ACREDITADO`. Por la conversión 1:1 (ADR-0010) hoy coinciden |
| `incentives.credits_reverted` | Σ `credit_delta` de filas originales `status = REVERTIDO` (`event_type ∉ {REVERSAL, REDEMPTION}`), `institution` en E y `reversed_at ∈ P` |
| `incentives.redemptions` / `credits_redeemed` | Cantidad y Σ `credits_spent` de `RewardRedemption` con `institution` en E y `created_at ∈ P` |
| `series.points[]` | Un punto por día local de P (en el mes en curso, hasta hoy): `received` y `resolved` del día, con las mismas reglas |
| `trend[]` | Los 6 meses que terminan en P: `{period, received, resolved}`, del más viejo al más nuevo. Siempre 6 puntos: los meses sin datos (incluso anteriores a `2026-01`) van en 0 |

**Redondeo:** las tasas van como `float` en [0, 1] con 3 decimales; las horas, con 1 decimal.

## 3. Operador: `GET /api/v1/operator/metrics/`

- **Permiso:** `IsInstitutionOperator`, más el contexto `institution_id`.
- **Query** (estricta): `institution_id` (UUID, con las reglas de §0) y `period` (`YYYY-MM`, opcional; por defecto, el mes local en curso). `period` responde 400 si:
  - no matchea `^\d{4}-(0[1-9]|1[0-2])$`;
  - es anterior a `2026-01`;
  - es posterior al mes en curso.
- **Throttle:** `dashboard_metrics`. **Cache-Control:** `private, no-store`.

**200: `InstitutionMetrics`**

```jsonc
{
  "scope": "institution",
  "institution": InstitutionRef,
  "period": "2026-10",
  "period_start": "2026-10-01T00:00:00-03:00",
  "period_end": "2026-11-01T00:00:00-03:00",
  "is_current_period": true,
  "generated_at": "2026-10-24T10:15:02-03:00",
  "reports": {
    "received": 42,
    "by_status": { "CREADO": 3, "CLASIFICADO": 5, "ASIGNADO": 8, "EN_PROCESO": 6,
                   "RESUELTO": 4, "CERRADO": 12, "RECHAZADO": 4 },
    "by_priority": { "ALTA": 10, "MEDIA": 25, "BAJA": 7 },
    "by_category": [ { "category": CategoryRef, "count": 12 } ],
    "resolution_rate": 0.421
  },
  "decisions": { "validated": 30, "rejected": 4, "validation_rate": 0.882, "rejection_rate": 0.118 },
  "resolution": { "resolved": 16, "mean_hours": 52.4, "median_hours": 40.0 },
  "sla": { "hours": null, "configured": false, "within": null, "breached": null,
           "compliance_rate": null, "open_over_sla": null },
  "confirmations": { "count": 57 },
  "incentives": { "points_credited": 1480, "credits_issued": 1480, "credits_reverted": 30,
                  "redemptions": 1, "credits_redeemed": 1000 },
  "series": { "granularity": "day",
              "points": [ { "date": "2026-10-01", "received": 2, "resolved": 0 } ] },
  "trend": [ { "period": "2026-05", "received": 10, "resolved": 6 } ],
  "by_institution": null
}
```

Errores:

| Código | Cuándo |
|---|---|
| 400 | Parámetro inválido, desconocido o repetido; `institution_id` omitido con varios vínculos |
| 401 | Sin sesión |
| 403 | Rol incorrecto, sin vínculo activo o `institution_id` ajeno (genérico, como S9). Un admin también recibe 403 |
| 429 | Throttle |

## 4. Admin: `GET /api/v1/admin/metrics/`

- **Permiso:** `IsAdmin` (rol de dominio). Un operador recibe 403.
- **Query** (estricta): `institution_id` (UUID, opcional) y `period`, con las mismas reglas.
  - Sin `institution_id`: `scope = "all"`, `institution = null` y `by_institution` con una fila por institución (activas e inactivas), ordenadas por nombre.
  - Con `institution_id`: el mismo shape que el operador, con `by_institution = null`. Si la institución no existe, **404**.
- **Throttle:** `dashboard_metrics`. **Cache-Control:** `private, no-store`.

```jsonc
// fila de by_institution
{ "institution": InstitutionRef, "received": 20, "validated": 15, "rejected": 2, "resolved": 9,
  "median_hours": 41.5,
  "sla": { "hours": null, "configured": false, "compliance_rate": null } }
```

## 5. Público: `GET /api/v1/public/metrics/` (S13-D08)

- `AllowAny`, **`authentication_classes = []`** (mismo patrón que las vistas de `map_app`) y throttle `public_metrics` (por IP). **No admite parámetros**: cualquier query da 400, igual que el ranking.
- **Cache-Control: `public, max-age=60`.** Sin autenticación, la respuesta no puede depender de quién la pide: un token inválido o vencido se ignora en lugar de dar el 401 que da el ranking (API §8.5). El cliente igual la consume **sin** `Authorization` (`publicApiFetch`).
- **Ventana:** de `inicio del mes local − 5 meses` a `ahora` (6 meses móviles). Las mismas fórmulas de §2, con E igual a cada institución **activa** (orden por nombre) y `totals` sobre todo.

```jsonc
{
  "window": { "from": "2026-05-01", "to": "2026-10-24", "months": 6 },
  "generated_at": "2026-10-24T10:15:02-03:00",
  "min_cell_size": 5,
  "totals": {
    "received": 120, "validated": 80, "rejected": 10, "resolved": 60,
    "resolution_rate": 0.545, "median_resolution_hours": 40.0,
    "confirmations": 210, "redemptions": null, "credits_issued": 5000,
    "suppressed": ["redemptions"]
  },
  "institutions": [
    { "institution": InstitutionRef,
      "received": 40, "validated": 30, "rejected": 2, "resolved": 20,
      "resolution_rate": 0.526, "median_resolution_hours": 38.5,
      "sla": { "configured": false, "hours": null, "compliance_rate": null },
      "confirmations": 60, "redemptions": null, "credits_issued": 900,
      "suppressed": ["redemptions"] }
  ]
}
```

**Supresión (k = `MIN_CELL_SIZE` = 5):**

| Celda | Se publica si… | Si no |
|---|---|---|
| `confirmations` | ≥ 5 **ciudadanos distintos** confirmaron en la ventana y el alcance | `null`, y el nombre va en `suppressed` |
| `redemptions` | ≥ 5 ciudadanos distintos canjearon | ídem |
| `credits_issued` | ≥ 5 ciudadanos distintos recibieron créditos | ídem |
| `resolution_rate` | denominador ≥ 5 | ídem |
| `median_resolution_hours` | n ≥ 5 | ídem |
| `sla.compliance_rate` | `within + breached` ≥ 5 | `null` (y `sla.compliance_rate` en `suppressed`) |
| `received`, `validated`, `rejected`, `resolved` | siempre | — |

`suppressed` distingue "suprimido" de "no aplica": un SLA no configurado da `null` **sin** figurar en `suppressed`.

**Nunca aparecen:** IDs de usuario, nombres, emails, coordenadas, descripciones, ni desgloses más finos que la institución, como categoría × institución.

## 6. Tipos TypeScript (`frontend/src/types/api.ts`, sección S13)

Se reutilizan `UUID`, `ISODateTime` y `ReportStatus`/`ReportPriority` de `@/types/domain` (ya importados en `api.ts`), y `SessionInstitution` y **`OperatorCategory`** (`{id, name, slug}`, S9) de `@/types/api`. No se crea un `CategoryRef` nuevo: duplicaría un tipo existente (lección C4 de S12). Se verificó que ninguno de los nombres nuevos choca con los 55 exportados de `api.ts`.

```ts
export type MetricsPeriod = string; // "YYYY-MM"

export interface SlaMetrics {
  hours: number | null; configured: boolean;
  within: number | null; breached: number | null;
  compliance_rate: number | null; open_over_sla: number | null;
}
export interface MetricsSeriesPoint { date: string; received: number; resolved: number }
export interface MetricsTrendPoint { period: MetricsPeriod; received: number; resolved: number }
export interface InstitutionMetricsRow {
  institution: SessionInstitution; received: number; validated: number; rejected: number;
  resolved: number; median_hours: number | null;
  sla: { hours: number | null; configured: boolean; compliance_rate: number | null };
}
export interface InstitutionMetrics {
  scope: "institution" | "all";
  institution: SessionInstitution | null;
  period: MetricsPeriod; period_start: ISODateTime; period_end: ISODateTime;
  is_current_period: boolean; generated_at: ISODateTime;
  reports: {
    received: number;
    by_status: Record<ReportStatus, number>;
    by_priority: Record<ReportPriority, number>;
    by_category: Array<{ category: OperatorCategory; count: number }>;
    resolution_rate: number | null;
  };
  decisions: { validated: number; rejected: number;
               validation_rate: number | null; rejection_rate: number | null };
  resolution: { resolved: number; mean_hours: number | null; median_hours: number | null };
  sla: SlaMetrics;
  confirmations: { count: number };
  incentives: { points_credited: number; credits_issued: number; credits_reverted: number;
                redemptions: number; credits_redeemed: number };
  series: { granularity: "day"; points: MetricsSeriesPoint[] };
  trend: MetricsTrendPoint[];
  by_institution: InstitutionMetricsRow[] | null;
}

export type PublicSuppressible =
  | "confirmations" | "redemptions" | "credits_issued"
  | "resolution_rate" | "median_resolution_hours" | "sla.compliance_rate";
export interface PublicMetricsCells {
  received: number; validated: number; rejected: number; resolved: number;
  resolution_rate: number | null; median_resolution_hours: number | null;
  confirmations: number | null; redemptions: number | null; credits_issued: number | null;
  suppressed: PublicSuppressible[];
}
export interface PublicInstitutionMetrics extends PublicMetricsCells {
  institution: SessionInstitution;
  sla: { configured: boolean; hours: number | null; compliance_rate: number | null };
}
export interface PublicMetrics {
  window: { from: string; to: string; months: number };
  generated_at: ISODateTime;
  min_cell_size: number;
  totals: PublicMetricsCells;
  institutions: PublicInstitutionMetrics[];
}
```

## 7. Notificaciones

### 7.1 Eventos y destinatarios (S13-D09, S13-D10)

| Tipo | Disparador (dentro de la transacción) | Destinatario | `dedup_key` | `report` | `institution` |
|---|---|---|---|---|---|
| `REPORTE_CREADO` | `reports._persist_report` | autor | `REPORTE_CREADO:{report_id}` | sí | `null` |
| `VALIDACION_INSTITUCIONAL` | `validation.validate_report` | autor | `VALIDACION_INSTITUCIONAL:{report_id}:{cycle}` | sí | la que valida |
| `REPORTE_RECHAZADO` | `validation.reject_report` | autor | `REPORTE_RECHAZADO:{report_id}` | sí | la que rechaza |
| `REPORTE_RESUELTO` | `lifecycle.transition_report_status` → `RESUELTO` | autor | `REPORTE_RESUELTO:{report_id}:{cycle}` | sí | `report.institution` |
| `RECOMPENSA_DISPONIBLE` | tarea diaria `rewards.notify_available_rewards` | ciudadano elegible | `RECOMPENSA_DISPONIBLE:{user_id}:{institution_id}:{YYYY-MM}` | `null` | la de la recompensa |
| `VERIFICACION_*`, `CANJE_REALIZADO` | sin cambios (S4, S12) | — | `null` | — | — |

- **No se crea** una notificación de reporte si `report.citizen_id` es `null` (reportes de X) o si el autor está inactivo.
- **No hay correo** para `EN_PROCESO`, cierre manual o automático, reversión, confirmación, canje revertido **ni `CLASIFICADO`** (v3): la clasificación de la IA no es una decisión institucional. Validar y rechazar se aceptan desde `CREADO` o `CLASIFICADO`, y el correo sale igual en los dos casos.
- **Sin backfill (v3):** se notifican solo los eventos que ocurren **después** del despliegue. Validar o resolver un reporte viejo es un evento nuevo y notifica; un reporte creado antes del despliegue nunca recibe `REPORTE_CREADO`.
- **Elegibilidad de `RECOMPENSA_DISPONIBLE`:**
  - el usuario es `role = citizen`, `is_active` y `verification_status = APROBADO`;
  - tiene Σ `credit_delta` `ACREDITADO` ≥ `MVP_CREDIT_COST` (1000) en la institución;
  - la institución está activa y tiene una `Reward` activa;
  - no hay ningún `RewardRedemption` (usuario, institución, mes en curso).
- **Idempotencia:** `dispatch.create(..., dedup_key=…)` inserta dentro de un *savepoint* (`transaction.atomic()` anidado). Si choca con `notification_dedup_key_unica`, devuelve `None` y no encola nada, sin abortar la transacción del llamador. Cualquier otro `IntegrityError` se relanza.

### 7.2 Plantillas (texto plano, `{% autoescape off %}`, como `canje_realizado.txt`)

Todas cierran con el pie existente ("--", "Ciudadanía Activa" y "Este es un mensaje automático. No respondas a esta dirección."). **Ninguna lleva** la descripción del reporte, ubicación, fotos, puntos, motivo ni enlaces.

| Tipo | Asunto | Cuerpo (variables) |
|---|---|---|
| `REPORTE_CREADO` | "Recibimos tu reporte" | "Recibimos tu reporte del {fecha}. La institución responsable lo va a revisar. Podés seguir su estado en Ciudadanía Activa → Panel." **v3: sin `{categoria}`.** La creación encola a la vez la IA y este correo; si la IA autoaplica, el correo podría nombrar la categoría del ciudadano o la de la IA según qué tarea termine primero |
| `VALIDACION_INSTITUCIONAL` | "Tu reporte fue validado" | "{institucion} validó tu reporte de {categoria} del {fecha}. Podés ver tus puntos y créditos en Recompensas." |
| `REPORTE_RESUELTO` | "Tu reporte figura como resuelto" | "{institucion} marcó como resuelto tu reporte de {categoria} del {fecha}. Podés ver el detalle en Panel." |
| `REPORTE_RECHAZADO` | "Sobre tu reporte" | "{institucion} no pudo dar curso a tu reporte de {categoria} del {fecha}. Podés ver el estado de tu reporte en Ciudadanía Activa → Panel." **No promete el motivo:** hoy el panel no puede mostrarlo (el detalle de un reporte `RECHAZADO` responde 404, API §6.2, y el seguimiento falla). Es una limitación existente, fuera del alcance de S13 (09 §6) |
| `RECOMPENSA_DISPONIBLE` | "Tenés una recompensa simulada disponible" | "Con tus créditos de {institucion} ya podés canjear su recompensa simulada este mes. Entrá a Ciudadanía Activa → Recompensas." + el aviso fijo de simulación de `canje_realizado.txt` |

**Contexto** (`dispatch._contexto`):

- los tipos de S4 siguen con **exactamente** `{"nombre"}`;
- los de reporte suman `fecha` (`report.created_at` local, `dd/mm/aaaa`). Salvo `REPORTE_CREADO`, también `categoria` (`report.category.name`, la vigente después de la decisión humana) e `institucion` (`notification.institution.name`);
- `RECOMPENSA_DISPONIBLE` suma `institucion`.

Si falta el objeto que el tipo necesita, se lanza `ValueError`, que es un fallo **permanente**: queda `FALLIDO`, el mismo criterio que `CANJE_REALIZADO` sin canje.

### 7.3 Reintentos (S13-D11)

| Resultado de un intento | Clasificación | Efecto |
|---|---|---|
| 2xx | éxito | `ENVIADO`, `next_attempt_at = null` |
| Red o timeout (`BrevoUnavailableError` sin `status_code`), 5xx o 429 (límite de tasa de Brevo) | **transitorio** | `attempts += 1`. Si `attempts < 3`: sigue `PENDIENTE`, con `next_attempt_at = now + (5 min, 30 min)[attempts − 1]`. Si `attempts == 3`: `FALLIDO` |
| Otro 4xx, `BrevoConfigurationError`, tipo sin plantilla, contexto incompleto u otra excepción | **permanente** | `attempts += 1` y `FALLIDO`. Incluye un eventual 402/403 por **cupo diario** del plan de Brevo: queda `FALLIDO` visible y se recupera al día siguiente con `resend_notification`, sin reintentar contra un cupo agotado |

- **`send()`:** bloquea la fila (como hoy). Si no está `PENDIENTE`, o si `next_attempt_at > now` (todavía no toca), sale sin enviar. Así, dos tareas sobre la misma fila nunca duplican.
- **Barrido `notifications.dispatch_due`** (Beat, cada 5 min). Encola con `send_notification.delay`, a lo sumo 200 por corrida, las `PENDIENTE` que cumplen alguna de estas condiciones:
  - `attempts > 0` y `next_attempt_at ≤ now`;
  - `attempts = 0`, `created_at ≤ now − 15 min` **y** `created_at ≥ now − 24 h` (rescate de un encolado perdido).

  Si encolar falla, solo lo registra (con el id y el tipo de error), y la siguiente corrida lo vuelve a intentar.
- **`FALLIDO` es terminal** para todo lo automático. `resend_notification` la devuelve a `PENDIENTE` con `attempts = 0` y `next_attempt_at = null`, y la auditoría no cambia.
- **El encolado inicial no cambia:** si el broker falla en `on_commit`, la notificación queda `FALLIDO`, igual que hoy en identidad y canje.
- `brevo.BrevoUnavailableError` gana el atributo `status_code: int | None`. El mensaje y los logs no cambian.
- Los logs registran el id de la notificación y el **tipo** del error, nunca el destinatario ni el cuerpo de la respuesta, como hoy.

### 7.4 Esquema: `notifications_app.0003_notification_retry_and_dedup` (aditiva)

```python
institution = models.ForeignKey("admin_app.Institution", on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="notifications")
dedup_key = models.CharField(max_length=160, null=True, blank=True)
attempts = models.PositiveSmallIntegerField(default=0)
next_attempt_at = models.DateTimeField(null=True, blank=True)
# Meta.indexes += [models.Index(fields=["status", "next_attempt_at"], name="notification_due_idx")]
# Meta.constraints += [models.UniqueConstraint(fields=["dedup_key"], name="notification_dedup_key_unica")]
```

Depende de `notifications_app.0002_notification_redemption` (S12) y de `admin_app.0002_seed_catalogs`. Las columnas nuevas son nulas o tienen `default`, así que en PostgreSQL 17 `ADD COLUMN` no reescribe la tabla.

- **`UniqueConstraint` con nombre, no `unique=True`:** `dispatch.create` identifica el choque por `exc.__cause__.diag.constraint_name == "notification_dedup_key_unica"`, como hace `redemptions._constraint_name` en S12. Con `unique=True` el nombre lo genera PostgreSQL y no se puede comparar de forma estable.
- Varios `NULL` no chocan en PostgreSQL, así que las notificaciones sin clave (identidad y canje) no se ven afectadas.

**Espejo TS (`domain.ts`, paso 0):** `Notification` suma `redemptionId: UUID | null`, `institutionId: UUID | null`, `attempts: number` y `nextAttemptAt: ISODateTime | null`. `dedup_key` no se refleja, porque es interno.

## 8. Carga del dataset de X (carril D, S13-D12)

### Comando

```text
python manage.py load_x_mock_dataset apps/social_ingestion_app/data/x_mock_dataset_v1.json [--dry-run]
```

- **Solo por consola:** no hay endpoint ni acción en el admin.
- Sin red, sin Celery y sin IA. Lee un archivo local; la ruta es relativa a `backend/`, que es el directorio de trabajo tanto en el contenedor de pruebas (`/work/backend`) como en Heroku (`/app`, el `Dockerfile` copia todo `backend/`).
- **Atómico:** primero valida todo el archivo; si un ítem es inválido, no escribe nada y sale con `CommandError`, que nombra el índice y el campo, nunca el texto.
- **Idempotente:** un ítem cuyo (`source_handle`, `simulated_at`) ya existe en `x_mock_messages` se omite.
- La salida muestra las cantidades creadas, omitidas y totales.

### Formato del archivo (`backend/apps/social_ingestion_app/data/x_mock_dataset_v1.json`)

```json
{
  "dataset": "x_mock_v1",
  "notice": "Publicaciones ficticias creadas por el proyecto. No corresponden a cuentas reales.",
  "items": [
    { "source_handle": "@ficticio_barrio01",
      "simulated_at": "2026-09-14T08:30:00-03:00",
      "raw_text": "Hace tres días que no anda el alumbrado de la cuadra.",
      "category": "alumbrado-publico",
      "latitude": -25.2905, "longitude": -57.6120 }
  ]
}
```

| Campo | Regla |
|---|---|
| `source_handle` | `^@ficticio_[a-z0-9_]{2,24}$`. Garantiza que nunca se use una cuenta real |
| `simulated_at` | ISO-8601 con zona, no futura respecto del momento de la carga |
| `raw_text` | De 1 a 280 caracteres, sin caracteres de control (`apps.reports_app.services.reports.validate_report_description`) |
| `category` | `slug` de una `Category` activa |
| `latitude` / `longitude` | Finitas y dentro del Gran Asunción: lat ∈ [−25.45, −25.15], lon ∈ [−57.75, −57.40] |
| `items` | De 1 a 200. Cada par (`source_handle`, `simulated_at`) es **único dentro del archivo**: es la clave de idempotencia |

### Efecto por ítem (en una sola transacción)

1. `Report`:
   - `source = x_mock`, `citizen = null`, `institution = null` y `status = CREADO`;
   - `priority = category.default_priority`;
   - `location` sale de las coordenadas;
   - `description = "[Publicación simulada de X · {source_handle}] {raw_text}"`.
2. `ReportStatusHistory` (`"" → CREADO`, `changed_by = null`).
3. `XMockMessage(source_handle, raw_text, simulated_at, report)`.
4. `audit.log(None, "report.ingested", {"report_id", "x_mock_message_id", "category_id", "source": "x_mock"}, actor_type=SYSTEM)`.
5. `on_commit`: `apps.map_app.cache.invalidate_public_reports_cache`, una vez por corrida y solo si hubo altas.

**No** se llama a `ledger.on_report_created`, porque lanza `ValueError` sin ciudadano y RF-45 prohíbe acreditar a la fuente. **Tampoco se encola `ai.classify_report`** (v3, con la IA activa en producción). El único encolado de la clasificación está en `reports._persist_report` → `_enqueue_ai_classification` (verificado en `9a36975`), y la carga de X no pasa por ahí. Además, la IA necesita la foto del reporte (`ai_client_app.media.load_report_image`) y los reportes de X no tienen. Tampoco se crea ninguna notificación. El reporte entra a la bandeja desde `CREADO`, que es operable (`OPERABLE_REPORT_STATUSES`), y se confirma y valida con normalidad.

## 9. Acciones de auditoría nuevas

| Acción | Actor | Payload |
|---|---|---|
| `report.ingested` | sistema (`actor_type = system`) | `report_id`, `x_mock_message_id`, `category_id`, `source` |

Los correos, los reintentos y el barrido **no** se auditan: igual que hoy, solo `notification.resent` lo hace. Las métricas son de solo lectura y tampoco.

## 10. Tareas de Celery nuevas

| Nombre | Beat | Qué hace |
|---|---|---|
| `notifications.dispatch_due` | cada 5 min | El barrido de §7.3 |
| `rewards.notify_available_rewards` | todos los días a las 09:00 locales | El lote de §7.1 |

Ninguna tiene `autoretry_for`: el estado vive en PostgreSQL. Beat corre en **exactamente 1** proceso (runbook de Heroku §6).
