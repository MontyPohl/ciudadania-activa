# Decisión G0-A2 — semántica post-run del broker de EX-10

- **Identificador:** G0-A2. Es un addendum de la Decisión G0
  (`1693d0ae562bfe777c496b616c9f8c8d5fa8a591`) y de G0-A1
  (`db7fe6649213a8d26f4ee60d36a797ee54f80322`).
- **Estado:** `APPROVED / CLOSED`. Entra en vigor con el commit documental que incorpora este
  archivo. Su SHA no se escribe aquí, porque sería autorreferencial: lo registra la
  implementación futura como `G0_A2_DECISION_COMMIT`.
- **Autoridad:** Monty Emmanuel Pohl Amarilla, autor y custodio.
- **Alcance:** sólo los runs de EX-10 que se inicien **después** del commit técnico que incorpore
  G0-A2 a la identidad del protocolo.
- **Fecha:** 03/10/2026.

## 1. Origen: run oficial 1

El hallazgo procede del primer run oficial de EX-10. Sus datos históricos son inmutables:

```text
Run:                s11-0ff9a5d1-2309-5f40-b4a7-dabad703e43a
Revisión ejecutada: 3bc3a37aa90abffd3b020eb0f9a7d92157cc545f
Terminal:           NOT_DEMONSTRATED
Invalidez:          broker:dynamic-residual
Criterios fallidos: ninguno
HT1-01 … HT1-04:    PASS (los cuatro); postcondición de auditoría: PASS
Binding:            false
```

Los cuatro criterios HT1 dieron `PASS`, pero el run quedó `NOT_DEMONSTRATED` por una invalidez
del aparato según la semántica vigente en `3bc3a37`. Ese resultado **no cambia**.

## 2. No retroactividad

1. El run 1 se ejecutó con la implementación y la semántica vigentes en `3bc3a37`.
2. Su terminal `NOT_DEMONSTRATED` es definitivo.
3. Ninguna implementación futura puede recalcular, reemplazar ni reinterpretar su decisión, su
   reconciliación, su manifest final ni su sello.
4. G0-A2 sólo rige runs iniciados después del commit técnico que lo incorpore a la identidad.
5. La evidencia del run 1 sigue siendo válida y demuestra el defecto del aparato vigente.

```text
G0_A2_RETROACTIVE_EFFECT=NONE
OFFICIAL_RUN_1_REINTERPRETED=NO
```

## 3. Causa raíz: regla pre-run reutilizada post-run

`PRE_RUN_ZERO_STATE_RULE_REUSED_POST_RUN` — `CONFIRMED` por auditoría read-only de `3bc3a37`.

### 3.1 Antes del run (correcto, se mantiene)

```text
s11_ex10_prepare / launcher inspect
  → ex10_state.inspect_ready_state()
      celery_queue_length, celery_unacked, celery_unacked_index,
      celery_result_keys, throttle_keys, throttle_actor_keys      → deben ser 0
s11_ex10_preflight
  → ex10_preflight.classify_broker()
      checks broker_dynamic_before/after (dynamic + task_messages = 0)
      checks broker_classification_before/after (unknown = 0)
ex10_executor._pre_emission_problems()
  → probes.ready_state() → READY_STATE/PASS antes del marcador STARTED
```

`classify_broker()` define como “dinámicas de carga” las colas Kombu, `unacked`,
`unacked_index`, las claves de resultado de Celery y las de throttle. Antes del run todas deben ser
cero: es el `READY_STATE` aislado.

### 3.2 Después del run (defecto)

```text
execute → run_drain_step() → observe_drain(): drain por telemetría
          (http_missing, queued_not_terminal, accepted_not_queued)
reconcile / finalize
  → ex10_executor.assess()
      → probes.broker_dynamic()                  # ex10_executor.broker_dynamic()
          = classify_broker().dynamic + task_messages + len(unclassifiable)
      → ex10_reconcile.reconcile(broker_dynamic=…)
          broker_dynamic is None → "broker:not-inspected"
          broker_dynamic > 0     → "broker:dynamic-residual"
      → ex10_load.decide(): invalidity_reasons ≠ ∅ → NOT_DEMONSTRATED
```

El docstring de `broker_dynamic()` declara la expectativa de “0 tras un drain completo”. Pero el
drain no vacía el broker: se resuelve con la telemetría. Una ejecución correcta deja residuos que
genera ella misma. En el run 1, tras un drain `COMPLETE`, el broker tenía:

| Clase | Cantidad | Naturaleza |
|---|---|---|
| Colas y colas de prioridad | 0 | sin trabajo pendiente |
| `unacked` / `unacked_index` | 0 | sin entregas sin confirmar |
| Mensajes de tarea (control plane) | 0 | — |
| `celery-task-meta-*` | 50 | resultados de las 50 tareas exitosas (TTL ≈ 24 h) |
| Throttle `report_create` | 10 | una por actor del dataset (TTL 1 h) |
| Caché `ciudadania_activa:1:public-reports:v1:generation` | 1 | caché de la aplicación, sin TTL |
| Inclasificables | 0 | — |

La reconciliación con base y telemetría fue completa: 50 tareas recibidas y exitosas, 0 fallidas
y 0 reintentadas. `dynamic = 60 > 0` produjo `broker:dynamic-residual`. Borrar esas claves para
obtener otro resultado habría sido manipulación.

Los tests no cubrían este caso: el arnés usa `broker_dynamic = 0` por defecto, y
`test_ex10_decision.py` fija que un residual dinámico invalida el run.

```text
BROKER_POST_RUN_DYNAMIC_RESIDUAL_POLICY_DEFECT=CONFIRMED
```

## 4. Política pre-run: sin cambios

Antes de START sigue exigiéndose:

- colas, colas de prioridad, `unacked`, `unacked_index` y mensajes de tarea: 0;
- claves de resultado de Celery: 0;
- claves de throttle de los actores: 0;
- claves desconocidas no contractuales e inclasificables: 0;
- storage: 0 objetos de reportes; DB dinámica: 0.

Sólo se admiten los bindings estáticos y el control plane ya aprobados.

```text
PRE_RUN_BROKER_POLICY_CHANGED=NO
```

## 5. Semántica post-run: quiescencia y coherencia

Después de un run, el broker se evalúa por **quiescencia y coherencia**, no por vacío absoluto.
La implementación introduce un check post-run propio (nombre orientativo:
`POST_RUN_BROKER_QUIESCENCE`). `broker_dynamic() > 0` deja de ser, por sí solo, una invalidez
global post-run.

### 5.1 Trabajo pendiente — invalida

- mensajes de tarea en colas o colas de prioridad no vacías;
- `unacked` o `unacked_index` no vacíos;
- mensajes de tarea en el control plane;
- tareas pendientes, o fallidas sin reconciliar;
- tareas reintentadas (el protocolo prohíbe el retry);
- claves inclasificables;
- claves desconocidas no adjudicadas;
- residuos que correspondan a otro run.

### 5.2 Residuos esperados del run — no invalidan por sí solos

- claves `celery-task-meta-*` de los task IDs observados en el run (§6);
- claves de throttle de los diez actores del dataset (§7);
- bindings estáticos `_kombu.binding.*`;
- control plane permitido sin mensajes de tarea;
- la caché de aplicación adjudicada (§8).

## 6. Claves de resultado de Celery

Una clave de resultado sólo es residuo esperado si cumple todo lo siguiente:

- usa el prefijo contractual del result backend de Celery;
- su task ID pertenece al conjunto de tareas observado en la telemetría del run;
- no pertenece a otro run;
- no representa una tarea pendiente, fallida ni reintentada;
- la base y la telemetría demuestran la finalización contractual de esa tarea.

Si una clave de resultado ya expiró de forma natural, eso no invalida el run por sí solo, siempre
que la tarea esté reconciliada, la telemetría muestre `received`/`succeeded`, la base contenga el
resultado esperado y no haya trabajo pendiente. El TTL nunca es criterio de éxito.

## 7. Claves de throttle

- Pueden existir después del run y pueden haber expirado en parte de forma natural.
- Deben corresponder exclusivamente a los diez actores del dataset y al scope contractual
  (`report_create`).
- No indican trabajo pendiente ni son, por sí mismas, invalidez del aparato.
- Una clave de throttle de un actor ajeno o de un scope inesperado es un hallazgo post-run.

## 8. Caché de aplicación adjudicada

La clave lógica `public-reports:v1:generation`, materializada en Redis como
`ciudadania_activa:1:public-reports:v1:generation`, se clasifica como
`EXPECTED_APPLICATION_CACHE`. No es tarea pendiente, ni desconocida no adjudicada, ni invalidez del
aparato.

- La adjudicación se hace por igualdad exacta con `cache.make_key("public-reports:v1:generation")`,
  calculado con la configuración real. Nunca por sufijo o prefijo aproximado.
- Su valor no se lee y el veredicto no depende de él.

## 9. Desconocidas e inclasificables

- Post-run, `unknown` sólo puede ser distinto de cero si **todas** las claves desconocidas están
  adjudicadas por nombre exacto y categoría congelada.
- La única adjudicación inicial de G0-A2 es la clave del §8.
- Cualquier otra clave desconocida invalida el run.
- Cualquier clave inclasificable invalida el run.

## 10. Prohibición de limpieza manipulativa

No forman parte del método, ni antes ni durante reconcile o finalize:

- `DEL`, `UNLINK`, `FLUSHDB`, `PURGE`, `GETDEL` ni manipulación de TTL (`EXPIRE`, `PERSIST`);
- esperar deliberadamente a que expiren claves;
- limpiar claves de resultado o de throttle antes de reconcile;
- filtrar claves para forzar un `PASS`.

La clasificación opera sobre el estado observado.

```text
POST_RUN_BROKER_MANUAL_CLEANUP_ALLOWED=NO
```

## 11. Categorías de invalidez post-run

Siguen la convención vigente `dominio:motivo`, con detalles saneados. `broker:not-inspected` se
conserva tal cual.

| Código | Motivo |
|---|---|
| `broker:queues-not-empty` | colas o colas de prioridad con mensajes |
| `broker:pending-task-messages` | mensajes de tarea en el control plane |
| `broker:unacked` | `unacked` o `unacked_index` no vacíos |
| `broker:unexpected-result-key` | clave de resultado ajena al run o sin reconciliar |
| `broker:unexpected-throttle-key` | throttle de un actor o scope ajeno |
| `broker:unknown-residual` | clave desconocida no adjudicada |
| `broker:inclassifiable-residual` | clave inclasificable |
| `broker:task-failure` | tarea fallida sin reconciliar |
| `broker:task-retry` | tarea reintentada |

`broker:dynamic-residual` no se reutiliza con la nueva semántica: queda como código histórico del
run 1. La implementación puede reutilizar códigos existentes equivalentes y no cambia los schemas
persistidos.

## 12. Primer run válido y ACK

- El run 1 es `NOT_DEMONSTRATED` oficial con `binding=false`: **no** consume el primer run válido.
- El próximo START oficial exige reconocerlo explícitamente:
  `--acknowledge-not-demonstrated s11-0ff9a5d1-2309-5f40-b4a7-dabad703e43a`.
- El escaneo de runs previos debe seguir leyendo el run 1 como finalizado y reconocible. En
  `3bc3a37` no revalida la identidad del protocolo de runs ya sellados, y la implementación debe
  preservar esa propiedad.

## 13. Identidad futura y versionado

- La implementación añadirá a la identidad del protocolo el commit de G0-A2 (por ejemplo,
  `G0_A2_DECISION_COMMIT`, junto a `DECISION_COMMIT` y `G0_A1_DECISION_COMMIT`), y el check
  `protocol_decision_commits` del preflight lo exigirá en la historia.
- La nueva identidad sólo aplica a runs futuros. El run 1 conserva la suya.
- Igual que G0-A1, este addendum no cambia hipótesis, población, dataset, umbrales, HT1-01…HT1-04,
  la regla HT-1 ni los veredictos `PASS` / `FAIL` / `NOT_DEMONSTRATED`. Sólo precisa qué estado
  post-run del broker constituye invalidez del aparato.

```text
Protocol:    s11-ex10-v1   (sin cambio)
Methodology: S11A-v1.1     (sin cambio)
Schemas:     sin cambio; la implementación sólo añade campos o valores compatibles
PROTOCOL_OR_SCHEMA_VERSION_BUMP_REQUIRED=NO
```

Si la implementación necesitara un cambio de schema no aditivo, debe detenerse y pedir una
decisión explícita antes de comprometerlo.

## 14. Defectos técnicos separados (abiertos)

**Reescritura del marcador del object root.** `OBJECT_ROOT_MARKER_REWRITE_DURING_EMISSION=CONFIRMED`.

- `local_storage.py` llama a `initialize_object_root()` (`isolation.py`) en cada escritura de imagen,
  y el marcador se reescribe aunque su contenido ya sea correcto.
- Cambian mtime y ctime; contenido, SHA, inode, modo y owner quedan iguales.
- Clasificación: `TECHNICAL_METADATA_SIDE_EFFECT`. No es causa de la decisión ni de la invalidez
  del run 1.
- La corrección preferida es una verificación no-op cuando el marcador ya es válido.
- `isolation.py` es una fuente atestada. Si la corrección exige modificarla, se evaluará P02/P03
  `v10` antes de comprometerla. No se asume todavía que sea necesario.

**Comentario obsoleto de `READ_ONLY_ACTIONS`.** `READ_ONLY_ACTIONS_COMMENT_STALE=CONFIRMED`.

- El comentario de `backend/scripts/s11_ex10_official_launcher.py` afirma que `reconcile` cambia el
  ctime del run.
- Desde `856c899`, `reconcile_run` es estrictamente de sólo lectura, y la etapa 5 del run 1 lo
  verificó con una comparación estricta.
- Clasificación: `COMMENT_ONLY`, sin efecto en ejecución ni en la decisión. Se corrige junto al
  commit técnico del launcher.

## 15. Estado resultante

```text
OFFICIAL_RUN_1:                  NOT_DEMONSTRATED (definitivo, sin reinterpretación)
G0-A2:                           APPROVED / CLOSED — sólo runs futuros
PRE_RUN_BROKER_POLICY_CHANGED:   NO
POST_RUN_BROKER_MANUAL_CLEANUP:  PROHIBIDO
Retroactividad:                  ninguna
Marcador del object root:        defecto técnico abierto
Comentario READ_ONLY_ACTIONS:    defecto de comentario abierto
Fuentes atestadas:               sin cambios (P02/P03 v9 vigente)
```
