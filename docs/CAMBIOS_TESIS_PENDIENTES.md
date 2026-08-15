# Cambios pendientes de incorporar a la tesis

Matriz de trazabilidad entre lo que la tesis documenta hoy, las decisiones técnicas aprobadas
durante la planificación, lo que efectivamente se implementa en cada sprint y lo que en
consecuencia deberá modificarse en el TCC.

## Para qué sirve este documento

- **La tesis y sus anexos no se modifican después de cada cambio técnico.** Editar el `.docx`
  sprint a sprint fragmenta el documento, rompe la numeración de tablas y figuras y multiplica el
  riesgo de introducir incoherencias. La actualización se hace por hitos, no de forma continua.
- **Aquí solo se registran diferencias relevantes para el documento académico**: arquitectura,
  modelo de datos, reglas de negocio, contrato de API, seguridad, metodología, cronograma,
  alcance y resultados. Nada más.
- **Lo puramente interno de implementación no entra en esta matriz.** Configuración de
  herramientas, estructura de Dockerfiles, manejo de excepciones, incidentes circunstanciales del
  entorno y ajustes de linters viven en el código, el `README.md`, los ADR o
  [PROGRESO_SPRINTS.md](PROGRESO_SPRINTS.md).
- **Esta matriz es el checklist con el que se actualizará la tesis** cuando llegue el momento de
  hacerlo. Cada fila debe poder cerrarse marcando su estado como `INCORPORADO_EN_TESIS`.
- **Las fechas planificadas y las fechas reales se mantienen separadas.** El Anexo F es
  planificación; la ejecución real se registra en `PROGRESO_SPRINTS.md`. Ver
  [Tratamiento del cronograma](#tratamiento-del-cronograma).

## Documentos de referencia

Orden de prioridad usado para determinar si algo constituye o no un cambio pendiente:

1. `Tesis_Ciudadania_Activa_Revisada_Recompensas.docx` — versión vigente del TCC
2. `Cronograma_Ciudadania_Activa_Anexo_F.docx` — cronograma revisado (fuente principal de objetivos por sprint)
3. [decisiones-incentivos.md](decisiones-incentivos.md)
4. `Matriz_Modificaciones_Ciudadania_Activa.docx`
5. [PLAN_DESARROLLO.md](PLAN_DESARROLLO.md) — en particular §33 (contradicciones, decisiones y observaciones)
6. [PROGRESO_SPRINTS.md](PROGRESO_SPRINTS.md)
7. [ADR vigentes](adr/)
8. `Tesis_Ciudadania_Activa.pdf` — versión histórica, solo como referencia del original

Una decisión técnica **no** es un cambio pendiente si la tesis revisada ya la contiene
correctamente. Cada fila de la matriz fue contrastada contra el texto de la versión revisada
antes de asignarle un estado.

## Estados

| Estado | Significado |
|---|---|
| `PENDIENTE` | Diferencia confirmada. La tesis debe modificarse |
| `REVISAR` | Requiere una decisión o un dato que todavía no existe (resultado real, criterio del tutor, confirmación en la ejecución) |
| `NO_REQUIERE_CAMBIO` | Verificado contra la tesis revisada: ya está contenido, o es un detalle interno que no corresponde al documento académico |
| `INCORPORADO_EN_TESIS` | Ya trasladado al `.docx`. Ninguna fila puede tener este estado hasta que la tesis se edite |

**Tipos de cambio:** Arquitectura · Modelo de datos · Regla de negocio · API · Seguridad ·
Metodología · Cronograma · Pruebas/resultados · Despliegue · Alcance

---

## Matriz de cambios

| ID | Fecha detectada | Sprint | Cambio o decisión real | Situación en la tesis actual | Documento/sección afectada | Tipo de cambio | Acción futura | Estado |
|---|---|---|---|---|---|---|---|---|
| CT-01 | 2026-08-01 | S1 | PostgreSQL 15 + PostGIS alojado en **Supabase**; backend Django y workers Celery en Railway, conectados por `DATABASE_URL` (D-03 · ADR-0003 · C-10) | **Ya contenida.** 4.2.3 lo describe explícitamente ("PostgreSQL/PostGIS y almacenamiento de objetos en Supabase") y la Tabla 8 registra PostgreSQL 15 + PostGIS y Railway como hosting del backend | 4.2.3 · Tabla 8 (4.5) · Figura 2 | Arquitectura | Ninguna. Verificar únicamente que la Figura 2 dibuje la base dentro de Supabase al regenerar los diagramas | NO_REQUIERE_CAMBIO |
| CT-02 | 2026-08-01 | S1 · S4 · S6 | **Supabase Storage** como almacenamiento de fotos de reportes, evidencias y documentos privados de verificación | **Ya contenida.** Tabla 8 ("Almacenamiento: Supabase Storage · Fotos, evidencias y documentos privados"), 4.2.3 y encabezado del Anexo A | Tabla 8 (4.5) · 4.2.3 · Anexo A | Arquitectura | Ninguna | NO_REQUIERE_CAMBIO |
| CT-03 | 2026-08-12 | S1 · S15 | La **latencia de red Railway ↔ Supabase** se mide como indicador propio dentro de las pruebas de carga, aislándola del tiempo de aplicación (C-12) | **Ausente.** La Tabla 6 fija P50, P99 y throughput, pero no contempla un indicador de latencia de red entre proveedores | Tabla 6 (3.10) · Anexo H.4 · Anexo H.6 | Pruebas/resultados | Agregar el indicador y su medición real al plan de pruebas y al informe de resultados | PENDIENTE |
| CT-04 | 2026-08-01 | S1 y siguientes | Respaldos lógicos obligatorios (`pg_dump` semanal y antes de cada migración destructiva) si el plan de Supabase contratado no incluye respaldos automáticos (C-11 · ADR-0003) | **Ausente.** El Anexo E.2 no registra ningún riesgo de pérdida de datos ni política de respaldo | Anexo E.2 | Despliegue | Evaluar si se incorpora a E.2 como riesgo operativo con su mitigación. El procedimiento operativo permanece en ADR-0003 | REVISAR |
| CT-05 | 2026-08-01 | S1 · S2 | `AUTH_USER_MODEL = "auth_app.User"` fijado **antes** de la primera migración, con esqueleto mínimo que se amplía de forma incremental en el Sprint 2 (C-06 · ADR-0014) | **Ya contenida en lo académicamente relevante.** El Anexo A.1 ya modela `users` + `citizen_profiles` como entidades propias. El momento de fijar el modelo es una restricción técnica de Django, no una decisión de diseño del producto | Anexo A.1 | Modelo de datos | Ninguna. Queda documentado en ADR-0014 y en el Sprint 1 de `PROGRESO_SPRINTS.md` | NO_REQUIERE_CAMBIO |
| CT-06 | 2026-08-01 | S2 · S9 | Cuarto estado **`ANULADO`** en `reward_transactions`, para las transacciones que estuvieron pendientes y nunca llegaron a acreditarse por rechazo del reporte. `REVERTIDO` queda reservado para las que sí estuvieron acreditadas (decisión 16 · C-01 · O-06c) | **Contradice.** A.2 y el `CREATE TYPE` de A.6 definen solo `PENDIENTE`/`ACREDITADO`/`REVERTIDO`. 4.9.7 afirma que los reportes rechazados no generan puntos acreditados, pero no dice qué ocurre con los pendientes | Anexo A.2 · Anexo A.6 · 4.9.7 | Modelo de datos | Agregar `ANULADO` al enumerado y al SQL, y una frase en 4.9.7: un reporte rechazado cierra el ciclo de sus puntos pendientes; ninguna transacción queda pendiente de forma indefinida | PENDIENTE |
| CT-07 | 2026-08-01 | S2 | Columnas `credited_at`, `voided_at` y `reversed_at` en `reward_transactions` desde la migración inicial, más el índice `(status, credited_at)` para el ranking (C-09 · O-06c) | **Ausentes.** No figuran en los campos clave de A.1, ni en el diccionario A.4, ni en el SQL de A.6. El índice tampoco está en A.5 | Anexo A.1 · A.4 · A.5 · A.6 | Modelo de datos | Agregar las tres columnas de soporte y el índice. Son técnicas y no alteran el alcance funcional ni las reglas del módulo | PENDIENTE |
| CT-08 | 2026-08-01 | S6 · S7 | `REPORT_CREATED` nace con 20/20 y `CONFIRMATION_CREATED` con 10/10, ambos en `PENDIENTE`, y mientras estén pendientes no participan de ningún saldo | **Ya contenida.** La Tabla 9 (4.9.2) establece +20 pendiente al crear y +10 pendiente al confirmar, con "no genera crédito disponible hasta validación", y 4.9.3 fija la conversión 1:1 | Tabla 9 · 4.9.2 · 4.9.3 | Regla de negocio | Ninguna | NO_REQUIERE_CAMBIO |
| CT-09 | 2026-08-01 | S2 · S9 | Catálogo cerrado de `event_type` (`REPORT_CREATED`, `CONFIRMATION_CREATED`, `VALIDATION_BONUS`, `RESOLUTION_BONUS`, `REDEMPTION`, `REVERSAL`) e **inmutabilidad de los valores económicos**: la acreditación solo cambia estado, institución y marcas temporales (C-07 · ADR-0004) | **Parcial.** A.1 declara el campo `event_type` pero A.2 no enumera sus valores, siendo el único enumerado sin desarrollar. Ninguna sección afirma que `impact_delta`/`credit_delta` sean inmutables, aunque A.5 ya exige un ledger sin sobrescritura de historial | Anexo A.2 · 4.9.8 | Modelo de datos | Enumerar los seis `event_type` en A.2 y añadir en 4.9.8 que los importes se fijan al crear la fila y no se reescriben nunca | REVISAR |
| CT-10 | 2026-08-01 | S10 · S12 | **Política definitiva de reversión.** La reversión siempre puede ejecutarse aunque los créditos ya hayan sido canjeados; el saldo institucional es la diferencia entre créditos válidos y créditos consumidos y **puede resultar negativo**; el vale emitido conserva su código y validez; no hay penalización adicional; no se admite un nuevo canje hasta que la diferencia vuelva a alcanzar el costo (D-01 · C-08 · ADR-0013) | **Parcial y potencialmente contradictoria.** La Tabla 9 contempla la reversión sin penalización adicional, pero 4.9.3 afirma que se evitan "movimientos posteriores sobre saldos ya utilizados", lo que se lee como lo opuesto. La tesis no define qué ocurre si los créditos revertidos ya fueron canjeados ni admite saldo negativo en ninguna sección | 4.9.3 · 4.9.6 · Tabla 9 · Anexo A.2 | Regla de negocio | Redactar la regla completa en 4.9.3/4.9.6 y ajustar la frase de 4.9.3 sobre saldos ya utilizados, que actualmente sugiere lo contrario a la decisión tomada | PENDIENTE |
| CT-11 | 2026-08-01 | S12 | **Costo fijo de 1.000 créditos** en el MVP: el operador no puede modificarlo. `credit_cost` se conserva con `DEFAULT 1000` por extensibilidad, pero el serializador descarta cualquier `cost` recibido (D-02) | **Parcial.** 4.9.6 ("costo estándar de 1.000 créditos") y A.4 (`credit_cost default 1000`) son correctos. El **Anexo B contradice**: expone `cost` como parámetro de entrada de `POST /operator/rewards/` | Anexo B | API | Retirar el parámetro `cost` del Anexo B y aclarar que el costo lo impone el servidor durante el MVP | PENDIENTE |
| CT-12 | 2026-08-01 | S11 | **Ranking limitado al Top 10 del mes calendario en curso.** Se eliminan el selector de mes, el histórico de rankings anteriores y la conservación de períodos cerrados (decisión 18 · C-03) | **Parcial.** 4.9.5 es correcto y coherente con la decisión. El **Anexo B contradice**: expone `month` como parámetro de `GET /ranking/monthly/` | Anexo B | API | Retirar el parámetro `month`; el endpoint devuelve siempre el mes en curso | PENDIENTE |
| CT-13 | 2026-08-01 | S11 | El mes del ranking se imputa por **fecha de acreditación** (`credited_at`), no por fecha de la acción. Como solo se publica el mes en curso y el cálculo es en vivo, no existe regla de imputación de reversiones a períodos cerrados (O-08) | **No especificada.** 4.9.5 dice "puntos acreditados obtenidos dentro del mes" sin definir si el mes se imputa por la acción o por la acreditación | 4.9.5 | Regla de negocio | Añadir la frase que fija `credited_at` como criterio de imputación | PENDIENTE |
| CT-14 | 2026-08-01 | S10 | Endpoint de reversión `POST /api/v1/admin/reports/{id}/revert-validation/`, exclusivo del administrador global y con motivo obligatorio (decisión 13 · O-06) | **Ausente.** El Anexo B no incluye ningún endpoint de reversión, pese a que el escenario S-07 del Anexo H.2 lo exige | Anexo B | API | Agregar el endpoint con su rol, entrada y salida | PENDIENTE |
| CT-15 | 2026-08-15 | S3–S13 · S15 | Endpoints operativos que la implementación requiere y el Anexo B no recoge: `auth/refresh`, `auth/me`, `admin/identity` + `review`, `reports/mine`, `reports/possible-duplicates`, `citizen/summary`, `reports/{id}/ai-classification`, `operator/reports` y `operator/reports/{id}`, **`operator/reports/{id}/reject`**, `rewards/transactions`, `public/metrics` | **Ausentes.** El Anexo B especifica 24 endpoints y no contempla estos | Anexo B | API | Consolidar el Anexo B con la API real al cierre (S15). El de **rechazo** es el más relevante: materializa la anulación de transacciones pendientes de CT-06 | REVISAR |
| CT-16 | 2026-08-01 | S2 · S7 | Se **elimina el `CHECK` de autoconfirmación** en base de datos: una restricción `CHECK` de PostgreSQL no puede consultar otra tabla. Se conserva `UNIQUE(report_id, citizen_id)` y la regla de autoconfirmación se aplica en el servicio de dominio (decisión 17 · C-02) | **Contradice.** A.5 lista entre las restricciones recomendadas la "validación de que `citizen_id` de confirmación sea distinto del `citizen_id` autor del reporte", que no puede implementarse como restricción de base de datos | Anexo A.5 | Modelo de datos | Sustituir esa línea: unicidad en base de datos, regla de autoconfirmación en la capa de servicios, con la justificación técnica | PENDIENTE |
| CT-17 | 2026-08-01 | S10 | Un reporte **no puede alcanzar `RESUELTO` sin `validated_at`**: la transición se bloquea. No existe acreditación implícita (decisión 12) | **Ausente.** Ni 4.4.3 ni las reglas de A.2 condicionan la transición a una validación previa | 4.4.3 · Figura 7 · Anexo A.2 | Regla de negocio | Documentar la precondición en el diagrama de estados y en las reglas de A.2 | PENDIENTE |
| CT-18 | 2026-08-01 | S6 | Criterio de posible duplicado: **`ST_DWithin` a 100 m + misma categoría + solo estados activos**, parametrizable por variable de entorno y sin bloquear la creación (decisión 15) | **Parcial.** 4.9.7 menciona que "se muestran posibles reportes similares cercanos" sin definir radio, categoría ni estados considerados | 4.9.7 · Anexo A.5 | Regla de negocio | Explicitar el criterio y su parametrización. Es necesario para la reproducibilidad de la validación técnica | PENDIENTE |
| CT-19 | 2026-08-01 | S2 · S13 | `reports.source` (`platform`/`telegram`/`x_mock`) se conserva, y `reports.citizen_id` pasa a **nullable** para admitir reportes ingestados sin ciudadano verificado (O-03) | **Ausentes en la revisada.** `source` figuraba en el Anexo A del documento original y no aparece entre los campos clave de la versión revisada, pese a sostener la regla "las fuentes no verificadas no generan puntos" (4.9.7). La nulabilidad de `citizen_id` tampoco está declarada | Anexo A.1 · A.4 | Modelo de datos | Reincorporar `source` al Anexo A y declarar `citizen_id` como nullable | PENDIENTE |
| CT-20 | 2026-08-01 | S9 | El módulo de incentivos se ejecuta como **llamada de dominio síncrona dentro de la misma transacción**, no como evento asíncrono. Solo las notificaciones son asíncronas (O-09) | **Contradice parcialmente.** 4.2.1 afirma que "`reports_app` emite eventos de negocio", lo que admite lectura asíncrona. 4.2.2 ya aclara que la acreditación es una operación atómica de backend, de modo que la contradicción es de redacción entre dos subsecciones | 4.2.1 · Figura 1 | Arquitectura | Precisar que el evento es una llamada de dominio síncrona, y que la asincronía se limita a clasificación IA y notificaciones | PENDIENTE |
| CT-21 | 2026-08-01 | S4 · S12 · S13 | En el MVP solo se implementan **notificaciones por email**; push/FCM queda como mejora futura (O-02) | **Contradicción interna.** La Tabla 8 lista "Email / Firebase Cloud Messaging" como tecnología de notificaciones, mientras 4.6 solo admite "notificaciones por email simple" como MVP obligatorio | Tabla 8 (4.5) · 4.6 | Alcance | Retirar FCM de la Tabla 8 o marcarlo explícitamente como mejora futura | PENDIENTE |
| CT-22 | 2026-08-01 | S9 · S12 · S14 | Escenarios de aceptación **S-11** (anulación de pendientes por rechazo) y **S-12** (reversión posterior a un canje con saldo institucional negativo resultante) (C-05) | **Ausentes.** El Anexo H.2 llega hasta S-10 | Anexo H.2 | Pruebas/resultados | Agregar ambos escenarios con su acción y resultado esperado | PENDIENTE |
| CT-23 | 2026-08-15 | S15 | Dataset sintético definitivo con transacciones en los **cuatro** estados y al menos un caso de saldo institucional negativo por reversión posterior a un canje | **Parcial.** La Tabla 4 (3.7.2) ya cubre volúmenes, confirmaciones, recompensas, canjes y la ventana de 60 días, pero declara solo "casos PENDIENTE, ACREDITADO y REVERTIDO" y no contempla el saldo negativo. El Anexo H.1 tabula únicamente 10 reportes (R-001 a R-010) frente a los ≥ 20 comprometidos | Tabla 4 (3.7.2) · Anexo H.1 | Pruebas/resultados | Agregar `ANULADO` y el caso de saldo negativo a la Tabla 4, y completar la tabla de H.1 al generar el dataset definitivo | PENDIENTE |
| CT-24 | 2026-08-15 | Todos | Riesgos incorporados durante la planificación: transacciones pendientes huérfanas de reportes rechazados; dependencia de dos integrantes; Sprint 16 de solo dos días | **Ausentes.** El Anexo E.2 registra 11 riesgos y no incluye estos tres, ya trasladados a [BACKLOG.md](BACKLOG.md) y a §32 del plan | Anexo E.2 | Metodología | Agregar los tres riesgos con nivel, mitigación y sprint de seguimiento | PENDIENTE |
| CT-25 | 2026-08-15 | S1 y siguientes | La implementación se está realizando con **Claude Code** como herramienta de asistencia (commits con `Co-Authored-By: Claude`) | **Contradice el hecho.** 4.8 declara nominalmente "ChatGPT/Codex" como herramienta de apoyo al desarrollo. La Declaración de uso de IA es genérica ("modelos de lenguaje a gran escala accedidos vía API y/o interfaz web") y sigue siendo válida sin cambios | 4.8 · Declaración de uso de IA | Metodología | Actualizar 4.8 con la herramienta efectivamente utilizada. Es una cuestión de integridad académica declarativa, no de redacción | PENDIENTE |
| CT-26 | 2026-08-01 | S12 · S13 | El cronograma efectivo es el del **Anexo F independiente**: dashboard en S13 y notificaciones de recompensas en S12 (contradicción 9 del plan) | **Contradice.** El Anexo F embebido en la tesis ubica el dashboard en S12 y Telegram/notificaciones en S13. Las **fechas coinciden** en ambos documentos: la divergencia es solo de objetivos | Anexo F (embebido en la tesis) | Cronograma | Unificar el Anexo F de la tesis con el cronograma independiente. Las fechas no se modifican | PENDIENTE |
| CT-27 | 2026-08-01 | — | La construcción del prototipo transcurre entre el 01/08/2026 y el 15/11/2026 (O-01) | **Contradicción interna.** La sección 3.4 sitúa la construcción y validación del prototipo "durante el primer y segundo trimestre" de 2026, incompatible con la ventana del Anexo F | 3.4 | Metodología | Corrección editorial: alinear 3.4 con la ventana del Anexo F | PENDIENTE |
| CT-28 | 2026-08-01 | S4 · S12 · S13 | `notifications_app` mínima adelantada al **Sprint 4** para los correos de aprobación/rechazo de verificación de identidad; consolidación completa en S12–S13 (decisión 14) | El Anexo F concentra las notificaciones en S12–S13 y no menciona el adelanto | Anexo F | Cronograma | Reflejarlo en el Anexo F solo si la ejecución confirma el adelanto. No modificar fechas | REVISAR |
| CT-29 | 2026-08-12 | S1 | **Desvío real de ejecución:** el Sprint 1 estaba planificado para el 01/08–07/08 y comenzó el 12/08/2026; queda abierto a la espera del despliegue en Vercel/Railway/Supabase | El Anexo F es planificación y no contempla, ni debe contemplar, fechas reales | Anexo F · 3.5 | Cronograma | **No reescribir el Anexo F.** Consolidar el desvío acumulado al cierre (S15–S16) y decidir entonces si amerita una nota metodológica sobre la ejecución real | REVISAR |
| CT-30 | 2026-08-15 | S15 | Versiones efectivas del stack: Django 5.2, DRF 3.15, Celery 5.4, Next.js 16, React 19, TailwindCSS 4, PostGIS 3.4 | **Parcial.** La Tabla 8 fija Python 3.11, PostgreSQL 15 y Redis 7, pero no versiona el framework backend, el frontend ni las bibliotecas | Tabla 8 (4.5) | Despliegue | Completar las versiones reales al cierre, por reproducibilidad del prototipo | REVISAR |
| CT-31 | 2026-08-15 | Todos | Los **14 ADR** funcionan como registro formal de decisiones arquitectónicas del proyecto | **Ausente.** 3.5 describe SCRUM, feature branching, pull requests con revisión obligatoria, TDD, CI y análisis estático, pero no menciona el registro de decisiones | 3.5 | Metodología | Evaluar mencionar los ADR como artefacto del método. Refuerza la trazabilidad entre decisión y código | REVISAR |
| CT-32 | 2026-08-01 | S8 · S15 | El modelo concreto de Gemini se define por variable de entorno; el efectivamente usado se documenta con los resultados (O-05) | La Tabla 8 cita "Google Gemini" sin versión, y el Anexo H.3 define el prompt sin fijar modelo | Tabla 8 · Anexo H.3 · resultados | Pruebas/resultados | Documentar el modelo usado junto a la medición real de concordancia IA (S15) | REVISAR |
| CT-33 | 2026-08-01 | S1 | Paquete transversal `core` (modelo base, auditoría, permisos, cliente de storage) además de las nueve apps de dominio (ADR-0002) | **No es un cambio de alcance.** La tesis describe nueve aplicaciones de dominio; `core` no introduce un dominio nuevo, solo evita dependencias circulares | 4.2.4 · Figura 3 | Arquitectura | Ninguna. Puede mencionarse al regenerar la Figura 3 si se busca fidelidad literal con el repositorio | NO_REQUIERE_CAMBIO |
| CT-34 | 2026-08-01 | S12 | El canje se serializa con `select_for_update()` sobre la fila de `User` dentro de `transaction.atomic()`, recalculando el saldo dentro del bloqueo (decisión 19 · C-04) | La tesis no describe mecanismos de concurrencia y no corresponde que lo haga: es una técnica de implementación | — | Regla de negocio | Ninguna. Queda en el ADR correspondiente y en el registro del Sprint 12 | NO_REQUIERE_CAMBIO |
| CT-35 | 2026-08-01 | S10 | Cierre automático `RESUELTO → CERRADO` como tarea de Celery Beat, con plazo configurable y desactivable en entornos de prueba (O-07) | **Ya contenida.** A.2 lo describe como cierre automático "tras plazo configurable, por ejemplo 72 h" | Anexo A.2 | Regla de negocio | Ninguna | NO_REQUIERE_CAMBIO |
| CT-36 | 2026-08-01 | S5 · S6 | El mapa (S5) se construye antes de la creación de reportes (S6) y se demuestra con datos semilla (O-04) | El Anexo F ya fija ese orden; no constituye un defecto del cronograma | Anexo F | Cronograma | Ninguna | NO_REQUIERE_CAMBIO |
| CT-37 | 2026-08-15 | — | Correcciones de consistencia señaladas por la Matriz de Modificaciones: HMAC-SHA-256 uniforme, 20 reportes sintéticos, 9 apps, 18 entidades, `users` + `citizen_profiles`, "clasificación asistida por IA" en lugar de "validación IA" | **Ya aplicadas.** Verificado sobre el texto de la versión revisada: no aparecen "200 reportes" ni "validación IA", `institution_categories` fue eliminada y HMAC-SHA-256 se usa de forma consistente | Resumen · 2.4.3 · 4.2.4 · 4.3 · Anexo A | Metodología | Ninguna | NO_REQUIERE_CAMBIO |
| CT-38 | 2026-08-01 | — | Bibliografía específica de gamificación (O-10) | La Matriz de Modificaciones la recomienda "si el tutor lo solicita"; la versión revisada no la incorpora | 2.4.4 · Referencias | Metodología | Depende del criterio del tutor. Fuera del alcance técnico del prototipo | REVISAR |

---

## Regla de actualización durante el desarrollo

Al cerrar cada sprint se revisa si existe alguna diferencia entre:

1. lo planificado en [PLAN_DESARROLLO.md](PLAN_DESARROLLO.md);
2. lo decidido en los [ADR](adr/);
3. lo efectivamente implementado;
4. lo que la tesis vigente documenta.

**Solo si la diferencia es académicamente relevante se agrega una fila nueva a esta matriz.** Una
diferencia es académicamente relevante cuando afecta a la arquitectura, al modelo de datos, a una
regla de negocio, al contrato de API publicado, a la seguridad, a la metodología, al alcance del
MVP, al cronograma o a los resultados que se reportarán.

No son cambios de tesis, y por lo tanto no se registran aquí: la configuración de pytest, Ruff,
ESLint o Prettier; la estructura interna de los Dockerfiles; el manejo o el registro de
excepciones; los incidentes circunstanciales del entorno de desarrollo; y cualquier ajuste que no
altere ninguna de las dimensiones anteriores. Si conviene conservarlos, su lugar es un ADR, el
`README.md` o [PROGRESO_SPRINTS.md](PROGRESO_SPRINTS.md).

**La tesis no se modifica como parte del cierre normal de un sprint.** La actualización
consolidada del documento se realiza por hitos:

- **Hito 1 — núcleo funcional e incentivos implementados** (al terminar el Sprint 12): se
  incorporan las filas de modelo de datos, reglas de negocio, API y alcance que a esa altura ya
  estén cerradas y estabilizadas.
- **Hito 2 — Sprints 15 y 16**: se incorporan los resultados reales, las mediciones de
  rendimiento, las pruebas ejecutadas, las capturas, los diagramas regenerados y las evidencias
  finales, junto con todas las filas que sigan `PENDIENTE`.

Al trasladar una fila al `.docx`, su estado pasa a `INCORPORADO_EN_TESIS` sin borrarla: la matriz
conserva el rastro completo de qué se cambió y por qué.

---

## Tratamiento del cronograma

**Las fechas originales del Anexo F representan la planificación y no se reescriben
retroactivamente.** Ajustar el cronograma planificado para que coincida con lo realmente ocurrido
eliminaría la evidencia del desvío, que es justamente parte del resultado metodológico del
trabajo.

[PROGRESO_SPRINTS.md](PROGRESO_SPRINTS.md) mantiene por separado, para cada sprint:

- fecha planificada (según el Anexo F);
- fecha real de inicio;
- fecha real de cierre;
- desvíos y observaciones respecto del plan.

Si al cierre del proyecto el desvío acumulado obliga a ajustar la redacción de la metodología
(3.4, 3.5) o de los resultados, ese ajuste se registra como una fila nueva de esta matriz —no se
aplica directamente sobre el documento. La fila **CT-29** ya sigue el primer desvío detectado.

---

*Documento de trazabilidad académica. No modifica la tesis, los anexos ni el código: registra qué
deberá modificarse y cuándo.*
