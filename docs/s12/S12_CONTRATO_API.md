# 02 — Contrato de API del Sprint 12

Este contrato es la fuente común de los tres carriles. El backend (A y C) lo implementa y el frontend (B) lo consume con mocks. Cualquier cambio se acuerda acá **antes** de tocar el código de un carril.

## 0. Convenciones heredadas (no se redefinen)

- Prefijo `/api/v1/`. Autenticación JWT `Bearer`. El permiso por defecto es `DenyAll`, así que cada vista declara el suyo.
- La forma de error es `{"detail": "...", "code": "..."}`, igual que en S7 y S9. El handler `apps.core.exceptions.api_exception_handler` normaliza `detail`.
- Las entradas son estrictas con `StrictInputMixin` (`apps.reports_app.serializers`). Una clave desconocida devuelve **400** con la forma normalizada de S7, `{"detail": {"<clave>": ["Este campo no está permitido."]}}`, **sin `code`**: así lo produce el handler y así responde hoy `confirm`. Los campos `read_only` declarados se aceptan y se **ignoran**, porque el mixin los cuenta como permitidos. Así se cumple D-02 para `credit_cost`.
- Contexto institucional del operador: query `institution_id`. Es obligatorio si el operador tiene varias instituciones activas y se resuelve como `_resolve_operator_institution` de S9.
- La paginación es `PageNumberPagination` con `PAGE_SIZE = 20`: `{"count", "next", "previous", "results"}`.
- Toda respuesta con saldos, códigos o datos propios lleva `Cache-Control: private, no-store`.
- Fechas en ISO-8601 con zona horaria. El período mensual se calcula en `America/Asuncion` y se expone como `"YYYY-MM"`.
- Throttles nuevos en `DEFAULT_THROTTLE_RATES`: `reward_redeem: "10/hour"` y `public_ranking: "120/min"`. Se agregan **en el paso 0**, así los carriles A y C no editan el mismo diccionario. Un scope sin tarifa hace fallar la vista con `ImproperlyConfigured`.

## 1. Objetos compartidos

```jsonc
// InstitutionRef: mismo shape que SessionInstitution del frontend
{ "id": "uuid", "name": "ANDE", "slug": "ande" }

// RewardPublic
{ "id": "uuid", "name": "Vale de descuento simulado", "description": "texto",
  "credit_cost": 1000, "simulated": true }

// Redemption: el vale del ciudadano
{ "id": "uuid",
  "code": "CA-7K3M-9QXP-2D4F-HJ8R",
  "period": "2026-10",
  "credits_spent": 1000,
  "simulated": true,
  "created_at": "2026-10-19T15:04:05-03:00",
  "reward": { "id": "uuid", "name": "Vale de descuento simulado" },
  "institution": InstitutionRef }
```

## 2. Ciudadano: `IsVerifiedCitizen`

### 2.1 `GET /api/v1/rewards/summary/`

```json
{
  "impact": { "historical": 230, "current_month": 80, "pending": 20 },
  "credits_by_institution": [
    { "institution": { "id": "…", "name": "ANDE", "slug": "ande" }, "balance": 1130 },
    { "institution": { "id": "…", "name": "ESSAP", "slug": "essap" }, "balance": -870 }
  ],
  "period": "2026-10"
}
```

| Campo | Cálculo |
|---|---|
| `historical` | Σ `impact_delta` `ACREDITADO` |
| `current_month` | ídem con `credited_at` dentro del mes local |
| `pending` | Σ `impact_delta` `PENDIENTE` |
| `balance` | Σ `credit_delta` `ACREDITADO`, agrupado por institución. Puede ser negativo (D-01) |

`credits_by_institution` se ordena por `institution.name` y solo incluye instituciones que tienen alguna fila `ACREDITADO`.

### 2.2 `GET /api/v1/rewards/transactions/?page=N`

Lista paginada y ordenada por `-created_at, -id`:

```json
{ "count": 7, "next": null, "previous": null, "results": [
  { "id": "uuid", "event_type": "REVERSAL", "status": "REVERTIDO",
    "impact_delta": -30, "credit_delta": -30,
    "institution": InstitutionRef,
    "report_id": "uuid",
    "redemption_id": null,
    "created_at": "…", "credited_at": null, "voided_at": null, "reversed_at": "…" }
]}
```

- `report_id` es `null` para `REDEMPTION`. En `CONFIRMATION_CREATED` es el reporte confirmado (`confirmation.report_id`).
- `institution` es `null` para las filas que todavía están `PENDIENTE`.
- `redemption_id` solo viene en `REDEMPTION`.
- El texto ("Reporte validado por ANDE · +30") lo arma el **frontend** a partir de `event_type`, `status` e `institution`.

### 2.3 `GET /api/v1/rewards/catalog/`

```json
{
  "period": "2026-10",
  "results": [
    { "reward": RewardPublic,
      "institution": InstitutionRef,
      "balance": 1130,
      "can_redeem": true,
      "blocked_reason": null,
      "missing_credits": 0,
      "redemption_this_month": null }
  ]
}
```

Lista las recompensas `active` de instituciones `is_active`, a lo sumo una por institución, ordenadas por nombre de institución. No se pagina.

| `blocked_reason` | Cuándo | `missing_credits` | `redemption_this_month` |
|---|---|---|---|
| `null` | saldo ≥ 1000 y sin canje en el mes | 0 | `null` |
| `"MONTHLY_LIMIT_REACHED"` | ya hay un canje en (usuario, institución, mes) | 0 | `{ "id", "code", "created_at" }` |
| `"INSUFFICIENT_BALANCE"` | saldo < 1000, incluido un saldo negativo | `1000 − balance` (con −870 da 1870) | `null` |

Si se cumplen las dos condiciones, gana `MONTHLY_LIMIT_REACHED`. Es el mismo orden que sigue el servicio.

### 2.4 `POST /api/v1/rewards/{reward_id}/redeem/`

- **Body:** vacío. El frontend envía el `POST` sin body, como `confirmReport`. Es un contrato vacío con `StrictInputMixin`, así que **cualquier clave**, incluida `cost` o `credit_cost`, devuelve `400` (forma de S7). El costo nunca viene del cliente.
- **Throttle:** `reward_redeem`.

**201**

```json
{ "redemption": Redemption, "balance_after": 130 }
```

**409**: el `code` es estable y se evalúa en este orden.

```jsonc
{ "detail": "La recompensa no está disponible.", "code": "REWARD_INACTIVE" }
// recompensa inactiva o institución inactiva

{ "detail": "Ya canjeaste la recompensa de ANDE este mes.", "code": "MONTHLY_LIMIT_REACHED",
  "redemption_id": "uuid" }

{ "detail": "Te faltan 870 créditos de ANDE para canjear.", "code": "INSUFFICIENT_BALANCE",
  "balance": 130, "missing_credits": 870 }
```

Otros errores:

| Código | Cuándo |
|---|---|
| 401 | sin sesión |
| 403 | rol incorrecto o ciudadano no verificado |
| 404 | `reward_id` inexistente |
| 429 | se superó el throttle |

### 2.5 `GET /api/v1/rewards/redemptions/?page=N` · `GET /api/v1/rewards/redemptions/{id}/`

- La lista devuelve `Redemption` del propio usuario, ordenados por `-created_at`.
- El detalle de un canje ajeno o inexistente da **404** (no 403), para no confirmar que existe.
- Un vale sigue visible y válido aunque el saldo actual sea negativo (D-01).

## 3. Operador: `IsInstitutionOperator` + contexto `institution_id`

```jsonc
// OperatorReward
{ "id": "uuid", "institution": InstitutionRef, "name": "…", "description": "…",
  "active": true, "credit_cost": 1000, "simulated": true,
  "created_at": "…", "updated_at": "…" }
```

### 3.1 `GET /api/v1/operator/rewards/?institution_id=`

```json
{ "institution": InstitutionRef, "reward": OperatorReward }
```

`reward` es `null` si la institución todavía no creó la suya.

### 3.2 `POST /api/v1/operator/rewards/?institution_id=`

- **Body:** `{ "name": "…", "description": "…", "active": true }`.
- `credit_cost` y `simulated` son `read_only`: si llegan, se ignoran.
- `name`: de 3 a 160 caracteres, recortado y sin caracteres de control.
- `description`: hasta 1000 caracteres, puede quedar vacía.
- `active` es opcional y vale `true` por defecto.

| Respuesta | Contenido |
|---|---|
| 201 | `OperatorReward` |
| 409 | `{ "code": "REWARD_ALREADY_EXISTS", "reward_id": "uuid" }`, porque hay una recompensa por institución (D-02-4) |
| 403 | `{ "code": "INSTITUTION_OPERATOR_REQUIRED" }` |

### 3.3 `PATCH /api/v1/operator/rewards/{reward_id}/?institution_id=`

- **Body parcial:** `name`, `description`, `active`. Con las mismas reglas, `credit_cost` se ignora.
- Responde **200** con `OperatorReward`.
- **403** `INSTITUTION_OPERATOR_REQUIRED` si la recompensa pertenece a otra institución o el operador no tiene vínculo activo con ella (plan §22). Inexistente: 404.

### 3.4 `GET /api/v1/operator/rewards/redemptions/?institution_id=&page=N`

```json
{ "count": 2, "next": null, "previous": null, "results": [
  { "id": "uuid", "code": "CA-…", "period": "2026-10", "created_at": "…",
    "reward": { "id": "uuid", "name": "…" },
    "citizen_display": "María G." }
]}
```

`citizen_display` se arma con la primera palabra de `full_name` y la inicial de la última. Si `full_name` está vacío, se muestra `"Ciudadano"`. **Nunca** se devuelven email, cédula ni el ID del ciudadano (S12-D04).

## 4. Público: ranking (carril C, recortable)

### `GET /api/v1/ranking/monthly/`

- Usa `AllowAny` y throttle `public_ranking`. **No admite parámetros**: cualquier query devuelve `400`.

```json
{ "period": "2026-10",
  "entries": [
    { "position": 1, "display_name": "María G.", "points": 180, "is_private": false, "is_me": false },
    { "position": 2, "display_name": "Usuario privado", "points": 150, "is_private": true, "is_me": true }
  ] }
```

**Reglas:**

- Como máximo hay 10 entradas, y `position` es correlativa del 1 al 10.
- `display_name` según `CitizenProfile.ranking_visibility`:

  | Modo | Qué se muestra |
  |---|---|
  | `FULL_NAME` | `full_name` |
  | `FIRST_INITIAL` | la primera palabra y la inicial de la última |
  | `ALIAS` | el alias; si está vacío, `Usuario privado` |
  | `PRIVATE` | `Usuario privado` |
  | sin perfil | `Usuario privado` |

- `is_me` solo puede ser `true` si la petición trae un JWT válido de ese ciudadano.
- Cache-Control: **siempre `private, no-store`**. Corrige la versión anterior, que proponía `public, max-age=60` para anónimos: el navegador podía guardar la respuesta anónima y servírsela a la misma URL después del login (sin `Vary: Authorization`), con `is_me` en `false`. La carga ya la absorbe la caché de Redis del backend.
- No expone IDs, emails ni el modo de visibilidad elegido.

## 5. Tipos TypeScript espejo (`frontend/src/types/api.ts`, sección S12)

Se **reutilizan** los tipos que ya existen:

- de `@/types/domain`: `UUID`, `ISODateTime`, `RewardTxStatus` y `RewardEventType`;
- de `@/types/api`: `SessionInstitution` y `PaginatedResponse<T>`.

Para la lista de movimientos, el import de `domain` en `api.ts` ya incluye `RewardTxStatus`; hay que **agregar `RewardEventType`** a ese import. No se crean `LedgerStatus` ni `LedgerEventType`, que estaban en la versión anterior de este bloque y duplicaban tipos existentes.

```ts
export type RewardPeriod = string; // "YYYY-MM"

export interface RewardPublic {
  id: UUID; name: string; description: string; credit_cost: 1000; simulated: true;
}
export interface Redemption {
  id: UUID; code: string; period: RewardPeriod; credits_spent: 1000; simulated: true;
  created_at: ISODateTime; reward: { id: UUID; name: string }; institution: SessionInstitution;
}
export interface RewardsSummary {
  impact: { historical: number; current_month: number; pending: number };
  credits_by_institution: Array<{ institution: SessionInstitution; balance: number }>;
  period: RewardPeriod;
}
export interface LedgerMovement {
  id: UUID; event_type: RewardEventType; status: RewardTxStatus;
  impact_delta: number; credit_delta: number;
  institution: SessionInstitution | null; report_id: UUID | null; redemption_id: UUID | null;
  created_at: ISODateTime; credited_at: ISODateTime | null;
  voided_at: ISODateTime | null; reversed_at: ISODateTime | null;
}
export type RewardBlockedReason = "MONTHLY_LIMIT_REACHED" | "INSUFFICIENT_BALANCE";
export interface CatalogEntry {
  reward: RewardPublic; institution: SessionInstitution; balance: number;
  can_redeem: boolean; blocked_reason: RewardBlockedReason | null; missing_credits: number;
  redemption_this_month: { id: UUID; code: string; created_at: ISODateTime } | null;
}
export interface RewardsCatalog { period: RewardPeriod; results: CatalogEntry[] }
export interface RedeemResponse { redemption: Redemption; balance_after: number }
export type RedeemConflictCode = "REWARD_INACTIVE" | "MONTHLY_LIMIT_REACHED" | "INSUFFICIENT_BALANCE";

export interface OperatorReward {
  id: UUID; institution: SessionInstitution; name: string; description: string;
  active: boolean; credit_cost: 1000; simulated: true;
  created_at: ISODateTime; updated_at: ISODateTime;
}
export interface OperatorRewardContext { institution: SessionInstitution; reward: OperatorReward | null }
export interface OperatorRedemption {
  id: UUID; code: string; period: RewardPeriod; created_at: ISODateTime;
  reward: { id: UUID; name: string }; citizen_display: string;
}

export interface MonthlyRanking {
  period: RewardPeriod;
  entries: Array<{ position: number; display_name: string; points: number;
                   is_private: boolean; is_me: boolean }>;
}
```

## 6. Correo `CANJE_REALIZADO` (S12-D10)

- **Asunto:** "Registramos tu canje simulado".
- **Cuerpo:** texto plano con el nombre del ciudadano, la institución y la recompensa. Debe incluir:
  - "Podés ver tu vale en Recompensas → Mis canjes";
  - el aviso fijo: "Esta recompensa es una **simulación** del prototipo académico Ciudadanía Activa y **no tiene valor económico real**."
- **No lleva el código del vale.** Sigue la política vigente de `dispatch._enviar_email`: el correo no transporta nada que otorgue algo, y el vale se consulta en la plataforma autenticada.
- Una `CANJE_REALIZADO` **sin `redemption`** no se envía: queda `FALLIDO`, el mismo criterio de "mejor `FALLIDO` visible que un correo inventado" que aplica hoy `dispatch`. No debería ocurrir, porque el servicio siempre la crea con el canje.

## 7. Acciones de auditoría

| Acción | Actor | Payload (sin el código del vale) |
|---|---|---|
| `reward.created` | operador | `reward_id`, `institution_id`, `name`, `active` |
| `reward.updated` | operador | `reward_id`, `institution_id`, `changes: {campo: [antes, después]}` |
| `reward.redeemed` | ciudadano | `redemption_id`, `reward_id`, `institution_id`, `period`, `credits_spent`, `balance_before`, `balance_after`, `ledger_transaction_id` |
