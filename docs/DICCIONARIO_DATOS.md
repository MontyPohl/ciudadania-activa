# Diccionario de datos — Ciudadanía Activa

Estado del modelo de datos **tal como está implementado**, contrastado con el Anexo A de
`Tesis_Ciudadania_Activa_Revisada_Recompensas.docx`. Las 18 entidades se crearon en el
Sprint 2; el Sprint 4 añadió una única columna, `citizen_profiles.rejection_reason`.

- **Fuente rectora:** Anexo A (A.1 entidades · A.2 enumeraciones · A.4 diccionario · A.5 índices · A.6 SQL de referencia).
- **Plan:** [PLAN_DESARROLLO.md](PLAN_DESARROLLO.md) §6.4, §6.5 y §12.
- **Divergencias respecto del Anexo A:** consolidadas en la fila **CT-42** de [CAMBIOS_TESIS_PENDIENTES.md](CAMBIOS_TESIS_PENDIENTES.md).
- **Alcance del Sprint 2:** estructura, restricciones, índices y catálogos semilla. Ninguna
  regla de negocio: los servicios de incentivos, confirmación, validación, ranking y canje
  llegan en los Sprints 6 a 12.

## Convenciones transversales

| Convención | Detalle |
|---|---|
| Clave primaria | `UUID` generada en el cliente (`uuid4`), no editable. Definida una sola vez en `apps.core.models.BaseModel` |
| Marcas temporales | `created_at` (`auto_now_add`) y `updated_at` (`auto_now`) en las 17 entidades nuevas |
| `users` | Conserva su estructura del Sprint 1 (`date_joined`, PK `UUID`) y **no** hereda de `BaseModel`: `AUTH_USER_MODEL` y `auth_app.0001_initial` no se tocan (ADR-0014) |
| Nombres de tabla | `db_table` explícito, idéntico al del Anexo A (`users`, `institutions`, `reward_transactions`, …) |
| Enumeraciones | `models.TextChoices` sobre `varchar`. **No** se usan tipos `ENUM` nativos de PostgreSQL: ampliar un `ENUM` exige `ALTER TYPE` en migración manual. Cambiar `choices` genera un `AlterField` que no emite DDL sobre la columna |
| Claves foráneas a usuario | Siempre contra `settings.AUTH_USER_MODEL` |
| Índices de claves foráneas | Los crea Django automáticamente; no se declaran otra vez para no duplicarlos |

## Enumeraciones

| Enumeración | Módulo | Valores |
|---|---|---|
| `ReportStatus` | `core.enums` | `CREADO` · `CLASIFICADO` · `ASIGNADO` · `EN_PROCESO` · `RESUELTO` · `CERRADO` · `RECHAZADO` |
| `ReportPriority` | `core.enums` | `BAJA` · `MEDIA` · `ALTA` |
| `ReportSource` | `core.enums` | `platform` · `telegram` · `x_mock` |
| `RejectionReason` | `core.enums` | `fuera_de_competencia` · `duplicado` · `informacion_insuficiente` · `contenido_inapropiado` |
| `ActorType` | `core.enums` | `human` · `ai` · `system` |
| `UserRole` | `auth_app.enums` | `citizen` · `operator` · `admin` |
| `VerificationStatus` | `auth_app.enums` | `PENDIENTE` · `APROBADO` · `RECHAZADO` |
| `RankingVisibility` | `auth_app.enums` | `FULL_NAME` · `FIRST_INITIAL` · `ALIAS` · `PRIVATE` |
| `MediaType` | `reports_app.enums` | `FOTO` |
| `RewardTxStatus` | `rewards_app.enums` | `PENDIENTE` · `ACREDITADO` · **`ANULADO`** · `REVERTIDO` |
| `RewardEventType` | `rewards_app.enums` | `REPORT_CREATED` · `CONFIRMATION_CREATED` · `VALIDATION_BONUS` · `RESOLUTION_BONUS` · `REDEMPTION` · `REVERSAL` |
| `NotificationType` | `notifications_app.enums` | `VERIFICACION_APROBADA` · `VERIFICACION_RECHAZADA` · `REPORTE_CREADO` · `VALIDACION_INSTITUCIONAL` · `REPORTE_RESUELTO` · `REPORTE_RECHAZADO` · `CANJE_REALIZADO` · `RECOMPENSA_DISPONIBLE` |
| `NotificationChannel` | `notifications_app.enums` | `EMAIL` |
| `NotificationStatus` | `notifications_app.enums` | `PENDIENTE` · `ENVIADO` · `FALLIDO` |

**Dos precisiones que el Anexo A todavía no recoge:**

1. **`ANULADO` es el cuarto estado del ledger** (corrección C-01, fila CT-06): describe la
   transacción que estuvo pendiente y **nunca** llegó a acreditarse porque el reporte fue
   rechazado. `REVERTIDO` queda reservado para la que **sí** estuvo acreditada y luego se
   revirtió. No son sinónimos y permiten auditar por separado las pérdidas por rechazo y
   por reversión.
2. **No existe un estado `VALIDADO` del reporte.** La validación institucional es un evento
   humano auditable que se registra en `reports.validated_at` / `validated_by` (RF-28). El
   tipo de notificación se llama por eso `VALIDACION_INSTITUCIONAL`, aunque su etiqueta
   visible para el ciudadano sea «Reporte validado».

## Las 18 entidades

### `auth_app`

**`users`** — usuario autenticado. Del Sprint 1: `id` (UUID PK), `email` (único,
`USERNAME_FIELD`), `full_name`, `is_active`, `is_staff`, `is_superuser`, `password`,
`last_login`, `date_joined`. **Ampliada en el Sprint 2** por `auth_app.0002`:

| Campo | Tipo | Restricción | Descripción |
|---|---|---|---|
| `google_oauth_id` | `varchar(255)` | único, nullable | Identidad de Google (Sprint 3). Nullable porque el superusuario y las cuentas semilla no la tienen; PostgreSQL admite varios `NULL` bajo índice único |
| `role` | `varchar(20)` | not null, `default 'citizen'` | `UserRole` (RF-05) |
| `verification_status` | `varchar(20)` | not null, `default 'PENDIENTE'` | `VerificationStatus` (RF-03) |

**`citizen_profiles`** — identidad verificada y privacidad del ranking. **No se crea junto
con el usuario**: nace cuando el ciudadano envía su verificación (Sprint 4).

| Campo | Tipo | Restricción | `on_delete` | Descripción |
|---|---|---|---|---|
| `user_id` | UUID | 1:1, único | `CASCADE` | El perfil es una extensión del usuario |
| `document_hmac` | `varchar(64)` | **único, not null** | — | HMAC-SHA-256 del **número textual** de la cédula (RF-02/RF-04). Ese número no se persiste en claro en ninguna parte |
| `verification_selfie_url` | `text` | blank | — | **Clave del objeto** en el bucket privado, no una URL. No serializable |
| `verified_at` | `timestamptz` | nullable | — | Fecha de la revisión administrativa, se haya aprobado o rechazado |
| `verified_by_id` | UUID | nullable | `SET_NULL` | Administrador revisor. **No figura en el Anexo A.1** → CT-42 |
| `rejection_reason` | `text` | **not null** (Sprint 4) | — | Motivo del rechazo en texto libre, **sin catálogo**. Vacío salvo rechazo vigente. **No figura en el Anexo A.1** → CT-44 |
| `ranking_visibility` | `varchar(20)` | not null, `default 'FULL_NAME'` | — | `RankingVisibility` (RF-07) |
| `ranking_alias` | `varchar(60)` | blank | — | Alias del ranking. **No es único**: dos ciudadanos pueden elegir el mismo (ADR-0011) → CT-46 |

**Tres precisiones del Sprint 4 sobre esta tabla:**

1. **`rejection_reason` es `NOT NULL` en PostgreSQL y `default=""` en el modelo Django.**
   La columna **no conserva un `DEFAULT` persistente** en el motor: Django lo aplica
   durante el `ALTER` de `auth_app.0004` y a continuación lo retira, que es su
   comportamiento habitual en un `AddField`. La introspección esperada en producción es
   por tanto `text · is_nullable NO · sin default`, igual que `verification_selfie_url` y
   `ranking_alias`. El valor por defecto vive en el modelo, no en el esquema.

2. **`verification_selfie_url` guarda una clave de objeto**, con la forma
   `verifications/{user_id}/{uuid4}.{ext}`. **No** es una URL pública —el bucket es
   privado— ni una URL firmada —expiraría en minutos y la columna quedaría con un valor
   muerto—. La URL firmada la emite el backend en el momento, con 300 segundos de vida y
   solo para el detalle administrativo. El nombre de la columna se conserva porque es el
   del Anexo A.

3. **Qué es sensible, con precisión.** El **número de cédula recibido como dato textual**
   nunca se persiste en claro: solo existe como `document_hmac`. La **selfie es otra
   cosa**: muestra al ciudadano sosteniendo su cédula, de modo que puede contener
   visualmente ese número. Que el número textual no se guarde no vuelve inocuo el
   archivo, y por eso vive en un bucket privado con acceso solo administrativo
   (ADR-0006).

### `admin_app`

**`institutions`** — entidad responsable. `name` (único), `slug` (único, clave natural del
seed), `contact_email` (blank), `sla_hours` (`integer`, **nullable**, `CHECK > 0`),
`is_active` (`default true`). El `CHECK` no afecta a las filas con `NULL`.

**`categories`** — categoría urbana. `name` (único), `slug` (único),
`default_institution_id` (FK **not null**, `PROTECT`), `default_priority`
(`default 'MEDIA'`), `is_active` (`default true`). El mapeo categoría → institución vive
aquí: la tabla `institution_categories` del documento original fue eliminada (§33.1,
contradicción 5).

**`institution_operators`** — `user_id` (FK, `CASCADE`), `institution_id` (FK, `PROTECT`),
`active` (`default true`), `UNIQUE(user_id, institution_id)`.

**`audit_logs`** — `actor_user_id` (FK nullable, `SET_NULL`), `actor_type` (`ActorType`),
`action` (`varchar(120)`), `payload` (`jsonb`, `default {}`), `created_at`. Se escribe
únicamente desde `core.services.audit.log`; el admin la expone en modo lectura.

### `reports_app`

**`reports`** — reporte georreferenciado.

| Campo | Tipo | Restricción | `on_delete` | Notas |
|---|---|---|---|---|
| `citizen_id` | UUID | **nullable** | `SET_NULL` | Reportes ingestados sin ciudadano verificado (CT-19) |
| `category_id` | UUID | **not null** | `PROTECT` | El ciudadano la elige en el paso 1 del asistente (CU-03, RF-08) |
| `institution_id` | UUID | **nullable** | `PROTECT` | La responsable definitiva se fija al validar (Sprint 9). Nunca se rellena con una institución ficticia |
| `description` | `text` | not null | — | RF-08 |
| `status` | `varchar(20)` | not null, `default 'CREADO'`, **índice B-tree** | — | `ReportStatus` |
| `priority` | `varchar(10)` | not null, `default 'MEDIA'`, **índice B-tree** | — | El servicio del Sprint 6 tomará el valor de `category.default_priority` |
| `source` | `varchar(20)` | **not null**, `default 'platform'` | — | RF-17/RF-45 (CT-19) |
| `location` | `geometry(Point,4326)` | not null, **índice GIST** | — | El GIST lo crea GeoDjango (`spatial_index=True`) |
| `validated_at` / `validated_by_id` | `timestamptz` / UUID | nullable | `SET_NULL` | Evento de validación, no estado (RF-28) |
| `resolved_at` | `timestamptz` | nullable | — | — |
| `rejection_reason` | `varchar(30)` | blank | — | `RejectionReason` (RF-14). **No figura en el Anexo A** → CT-42 |
| `created_at` | `timestamptz` | **índice B-tree** | — | A.5 |

**Semántica de decisión S9-B2.** Validar y rechazar no crean tablas ni columnas nuevas. Ambas
operaciones bloquean el agregado con `select_for_update()` y escriben `reports`,
`report_status_history`, `audit_logs` y el conjunto correspondiente de `reward_transactions`
dentro de una sola transacción. La validación fija `institution_id`, `validated_at` y
`validated_by_id`; el rechazo conserva `institution_id` como estaba y fija `rejection_reason`.
El contexto institucional enviado por la API se comprueba, bajo el mismo límite transaccional y
con bloqueo de `users` e `institution_operators`, contra un rol operador y una membresía que sigan
activos; después se contrasta con
`reports.institution_id ?? categories.default_institution_id`. Nunca se deriva de
`ai_classifications.suggested_institution_id`.

**`report_media`** — `report_id` (`CASCADE`), `file_url` (`text`), `file_hash`
(`varchar(64)`, SHA-256 de los bytes originales), `media_type` (`default 'FOTO'`),
`uploaded_by_id` (nullable, `SET_NULL`). El índice único parcial
`report_media_foto_hash_unico` cubre `file_hash` únicamente cuando `media_type = 'FOTO'`:
impide reutilizar una fotografía exacta sin imponer unicidad a tipos de media futuros.

**`report_status_history`** — `report_id` (`CASCADE`), `previous_status` (blank en el
asiento inicial), `new_status`, `changed_by_id` (nullable, `SET_NULL`: el cierre automático
a 72 h no tiene actor humano), `reason` (`text`, blank). S9-B2 añade una fila por decisión
institucional confirmada; un conflicto o rollback no deja historial parcial.

**`resolution_evidences`** — `report_id` (`CASCADE`), `description`, `evidence_url`,
`uploaded_by_id` (nullable, `SET_NULL`).

**`report_confirmations`** — `report_id` (`CASCADE`), `citizen_id` (`PROTECT`),
**`UNIQUE(report_id, citizen_id)`**. El instante de la confirmación es `created_at`: **no
existe la columna `confirmed_at`** del Anexo A.6 → CT-42. **No hay ningún `CHECK` de
autoconfirmación**: la regla compara con otra tabla y PostgreSQL no puede expresarla sin un
trigger; vive en el servicio de dominio del Sprint 7 (C-02, CT-16).

### `ai_client_app`

**`ai_classifications`** — `report_id` (`CASCADE`, sin unicidad: el Sprint 8 admite
reproceso), `suggested_category_id` y `suggested_institution_id` (nullables, `PROTECT`),
`suggested_priority` (blank), `confidence` (`numeric(4,3)`, nullable, `CHECK 0 ≤ x ≤ 1`),
`inconsistency_flag` (`default false`). Estos son **los campos que existen hoy**: solo los
del Anexo A.

**Previsto para S8-B/S8-C, aún no implementado.** El contrato cerrado en S8-A
([AI_CLASSIFICATION_CONTRACT.md](AI_CLASSIFICATION_CONTRACT.md), D17) resuelve la
evaluación que este diccionario dejaba abierta y fija una migración aditiva con
exactamente dos columnas nuevas:

| Campo previsto | Tipo previsto | Semántica | Filas existentes |
|---|---|---|---|
| `justification` | `TextField` | justificación normalizada de la clasificación (CA-2) | `blank` / vacío |
| `model_name` | `CharField(100)` | modelo del proveedor efectivamente usado (CT-32, O-05) | `blank` / vacío |

**`raw_response` y `prompt_version` no serán columnas de esta tabla**: viajan en el payload
de `audit_logs` (D15 y D16 del contrato), con `raw_response` truncada a 8.000 caracteres. La
tabla **no** recibe unicidad por `report_id`, porque el reproceso manual debe seguir siendo
posible. Tampoco se añaden `hubo_cambio` —lo deriva el backend—, estado de clasificación,
error de IA, `task_id` ni `provider_request_id`.

La migración no está creada: S8-A es un gate documental.

### `rewards_app`

**`reward_transactions`** — ledger. Todas las claves foráneas son `PROTECT`: un movimiento
económico no puede perder su sujeto, su origen ni su institución.

| Campo | Tipo | Restricción | Notas |
|---|---|---|---|
| `user_id` | UUID | not null | `PROTECT` |
| `report_id` | UUID | nullable | `PROTECT` |
| `confirmation_id` | UUID | nullable | `PROTECT`; solo `CONFIRMATION_CREATED` |
| `institution_id` | UUID | **nullable** | Se fija **al acreditar**, con la institución validante (RF-35/RF-38) |
| `event_type` | `varchar(40)` | not null | `RewardEventType` |
| `impact_delta` | `integer` | not null | **Inmutable** tras la inserción |
| `credit_delta` | `integer` | not null | **Inmutable** tras la inserción |
| `status` | `varchar(20)` | not null | Único campo económicamente relevante que muta |
| `related_transaction_id` | UUID | nullable | Autorreferencia `PROTECT`: enlaza `REVERSAL` con su original |
| `credited_at` · `voided_at` · `reversed_at` | `timestamptz` | nullable | **Columnas de soporte creadas en la migración inicial** (C-09, CT-07) |

Índices: `(user_id, institution_id, status, created_at)` para los saldos y
`(status, credited_at)` para el Top 10 del mes en curso, que imputa el mes por
`credited_at` (O-08).

Restricciones:

- `reward_tx_status_timestamps_coherentes` — coherencia estado ↔ marca temporal:

  | `status` | `credited_at` | `voided_at` | `reversed_at` |
  |---|---|---|---|
  | `PENDIENTE` | NULL | NULL | NULL |
  | `ACREDITADO` | NOT NULL | NULL | NULL |
  | `ANULADO` | NULL | NOT NULL | NULL |
  | `REVERTIDO` | **indiferente** | NULL | NOT NULL |

  `REVERTIDO` no exige `credited_at IS NULL` a propósito: la fila original acreditada
  conserva su `credited_at` como rastro de auditoría, y una fila `REVERSAL` puede nacer
  `REVERTIDO` sin haber estado nunca acreditada.

- `reward_tx_report_created_unico` — índice único parcial: **un solo `REPORT_CREATED` por
  reporte**.
- `reward_tx_confirmation_created_unico` — índice único parcial: **un solo
  `CONFIRMATION_CREATED` por confirmación**.
- No se restringe `VALIDATION_BONUS` ni `RESOLUTION_BONUS`: tras una reversión, una
  revalidación legítima genera una fila nueva.

**`rewards`** — `institution_id` (`PROTECT`), `name`, `description`, `credit_cost`
(`integer`, `default 1000`, `CHECK > 0`), `active`, `simulated`,
`UNIQUE(institution_id, name)`. El costo es fijo en el MVP (D-02): el campo se conserva
estructuralmente normal y la prohibición de editarlo se aplica en la capa de aplicación
—`readonly_fields` en el admin ahora, serializador y servicio en el Sprint 12—, nunca con
`editable=False`.

**`reward_redemptions`** — `user_id` (`PROTECT`), `reward_id` (`PROTECT`), `code`
(`varchar(80)`, **único**), `credits_spent` (`default 1000`, `CHECK > 0`), índice
`(user_id, reward_id, created_at)`. El límite de un canje por institución, usuario y mes no
es expresable en una sola tabla: se aplica en el servicio del Sprint 12 con
`select_for_update()` (C-04).

### `notifications_app`

**`notifications`** — `user_id` (`CASCADE`), `report_id` (nullable, `SET_NULL`: los correos
de verificación no se refieren a un reporte), `notification_type`, `channel`
(`default 'EMAIL'`), `status` (`default 'PENDIENTE'`).

### `social_ingestion_app`

**`telegram_ingested_messages`** — `telegram_chat_id`, `sender_alias`, `raw_text`,
`report_id` (nullable, `SET_NULL`).

**`x_mock_messages`** — `source_handle`, `raw_text`, `simulated_at` (fecha **simulada** del
dataset, distinta de `created_at`), `report_id` (nullable, `SET_NULL`).

### Sin modelos propios

`map_app` y `analytics_app` son capas de lectura sobre `reports_app` y `rewards_app`
(ADR-0002); `core` solo aporta el modelo base abstracto, las enumeraciones transversales y
el servicio de auditoría.

## Saldos derivados

No existe ninguna tabla de saldos, contadores ni rankings materializados. Todo se agrega
sobre el ledger (§6.5 del plan):

| Magnitud | Cálculo |
|---|---|
| Puntos de impacto históricos | `Σ impact_delta WHERE status = 'ACREDITADO'` |
| Puntos pendientes | `Σ impact_delta WHERE status = 'PENDIENTE'` |
| Créditos por institución | `Σ credit_delta WHERE status = 'ACREDITADO' GROUP BY institution_id` |
| Top 10 del mes en curso | `Σ impact_delta WHERE status = 'ACREDITADO' AND credited_at ∈ mes actual`, orden descendente, límite 10 |

Las filas `REVERSAL` existen solo como rastro: nacen `REVERTIDO`, de modo que quedan fuera
del conjunto `ACREDITADO` y **no** compensan por segunda vez.

## Catálogos semilla

Cargados por la migración de datos `admin_app.0002_seed_catalogs` (idempotente y
reversible) y reproducibles con `python manage.py seed_catalogs`.

**Instituciones (3)** — sin `contact_email` ni `sla_hours`, porque la tesis no documenta
ningún valor:

| `slug` | `name` |
|---|---|
| `municipalidad-de-asuncion` | Municipalidad de Asunción |
| `ande` | ANDE |
| `essap` | ESSAP |

**Categorías (10)** — mapeo literal del Anexo A.3, todas con `default_priority = MEDIA`:

| `slug` | `name` | Institución por defecto |
|---|---|---|
| `baches` | Baches | Municipalidad de Asunción |
| `basura-acumulada` | Basura acumulada | Municipalidad de Asunción |
| `alumbrado-publico` | Alumbrado público | ANDE |
| `agua-potable-perdidas` | Agua potable / pérdidas | ESSAP |
| `drenaje-desague-cloaca` | Drenaje / desagüe / cloaca | ESSAP |
| `semaforos` | Semáforos | Municipalidad de Asunción |
| `escombros` | Escombros | Municipalidad de Asunción |
| `arbol-caido` | Árbol caído | Municipalidad de Asunción |
| `cables-expuestos-riesgo-electrico` | Cables expuestos / riesgo eléctrico | ANDE |
| `otros` | Otros | Municipalidad de Asunción |

Los `slug` son la slugificación determinista del nombre (`django.utils.text.slugify`) y
funcionan como clave natural de la carga idempotente.

## Grafo de migraciones

```text
auth_app.0001_initial            (Sprint 1 — intacta)
   └── auth_app.0002_user_domain_fields      +google_oauth_id, +role, +verification_status
          └── auth_app.0003_citizen_profile  citizen_profiles
                 └── auth_app.0004_citizen_profile_rejection_reason   (Sprint 4)
                        +rejection_reason · AddField aditivo, no destructivo

admin_app.0001_initial           institutions · categories · institution_operators · audit_logs
   └── admin_app.0002_seed_catalogs          migración de datos (3 + 10), reversible

reports_app.0001_initial         reports · report_media · report_status_history ·
                                 resolution_evidences · report_confirmations
   ├── ai_client_app.0001_initial            ai_classifications
   ├── rewards_app.0001_initial              rewards · reward_transactions · reward_redemptions
   ├── notifications_app.0001_initial        notifications
   └── social_ingestion_app.0001_initial     telegram_ingested_messages · x_mock_messages
```

`reports_app`, `rewards_app`, `notifications_app` y `admin_app` declaran además
`swappable_dependency(AUTH_USER_MODEL)`; `ai_client_app` y `rewards_app` dependen de
`admin_app.0001`.

**Inventario acumulado: 11 archivos de migración.** El Sprint 4 aporta exactamente uno,
`auth_app.0004`; ni `admin_app` ni `notifications_app` añadieron ninguno pese a
implementar la revisión administrativa y las notificaciones, porque sus tablas ya
existían desde el Sprint 2 con la forma necesaria.

---

*Documento de implementación. Se actualiza cuando el modelo de datos cambia; las
diferencias académicamente relevantes se registran en
[CAMBIOS_TESIS_PENDIENTES.md](CAMBIOS_TESIS_PENDIENTES.md).*
