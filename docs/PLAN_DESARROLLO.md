# Plan de Desarrollo — Ciudadanía Activa

> Documento técnico de planificación del prototipo funcional correspondiente al Trabajo de Conclusión de Carrera **“Ciudadanía Activa: prototipo de plataforma digital para el reporte y seguimiento de incidencias urbanas”** (Universidad Columbia del Paraguay).
>
> **Estado:** planificación aprobada, implementación no iniciada. Sin decisiones pendientes.
> **Ventana de desarrollo:** 01/08/2026 → 15/11/2026 (16 sprints semanales, Anexo F revisado).
> **Autores:** Monty Emmanuel Pohl Amarilla (desarrollo) · Guadalupe Galeano (documentación, pruebas y coherencia funcional). **Tutor:** Prof. Edward Vázquez.

---

## Índice

1. [Resumen del sistema](#1-resumen-del-sistema)
2. [Fuentes documentales analizadas](#2-fuentes-documentales-analizadas)
3. [Requisitos consolidados](#3-requisitos-consolidados)
4. [MVP obligatorio](#4-mvp-obligatorio)
5. [Mejoras futuras](#5-mejoras-futuras)
6. [Arquitectura propuesta](#6-arquitectura-propuesta)
7. [Estructura propuesta del repositorio](#7-estructura-propuesta-del-repositorio)
8. [Modelo conceptual de módulos](#8-modelo-conceptual-de-módulos)
9. [Dependencias entre módulos](#9-dependencias-entre-módulos)
10. [Estrategia de desarrollo](#10-estrategia-de-desarrollo)
11. [Sprint 1 — Entorno, repositorio y CI/CD](#11-sprint-1--entorno-repositorio-y-cicd)
12. [Sprint 2 — Modelo de datos e incentivos](#12-sprint-2--modelo-de-datos-e-incentivos)
13. [Sprint 3 — Autenticación, roles y permisos](#13-sprint-3--autenticación-roles-y-permisos)
14. [Sprint 4 — Verificación de identidad y privacidad](#14-sprint-4--verificación-de-identidad-y-privacidad)
15. [Sprint 5 — Mapa público y geolocalización](#15-sprint-5--mapa-público-y-geolocalización)
16. [Sprint 6 — Creación de reportes y duplicados](#16-sprint-6--creación-de-reportes-y-duplicados)
17. [Sprint 7 — Panel ciudadano y confirmaciones](#17-sprint-7--panel-ciudadano-y-confirmaciones)
18. [Sprint 8 — Clasificación asistida por IA](#18-sprint-8--clasificación-asistida-por-ia)
19. [Sprint 9 — Panel institucional y validación](#19-sprint-9--panel-institucional-y-validación)
20. [Sprint 10 — Estados, evidencias, auditoría y reversión](#20-sprint-10--estados-evidencias-auditoría-y-reversión)
21. [Sprint 11 — Créditos, ranking del mes en curso y privacidad](#21-sprint-11--créditos-ranking-del-mes-en-curso-y-privacidad)
22. [Sprint 12 — Catálogo de recompensas y canje simulado](#22-sprint-12--catálogo-de-recompensas-y-canje-simulado)
23. [Sprint 13 — Dashboards, integración e ingesta social](#23-sprint-13--dashboards-integración-e-ingesta-social)
24. [Sprint 14 — Pruebas unitarias, integración, seguridad y E2E](#24-sprint-14--pruebas-unitarias-integración-seguridad-y-e2e)
25. [Sprint 15 — Carga, dataset definitivo y documentación](#25-sprint-15--carga-dataset-definitivo-y-documentación)
26. [Sprint 16 — Cierre y preparación de la defensa](#26-sprint-16--cierre-y-preparación-de-la-defensa)
27. [Dependencias entre sprints](#27-dependencias-entre-sprints)
28. [Estrategia de pruebas](#28-estrategia-de-pruebas)
29. [Seguridad](#29-seguridad)
30. [Auditoría](#30-auditoría)
31. [Riesgos técnicos](#31-riesgos-técnicos)
32. [ADR recomendados](#32-adr-recomendados)
33. [Inconsistencias o dudas encontradas](#33-inconsistencias-o-dudas-encontradas)
34. [Criterios generales de finalización del MVP](#34-criterios-generales-de-finalización-del-mvp)

---

## 1. Resumen del sistema

Ciudadanía Activa es un prototipo web responsive que conecta a la ciudadanía del área metropolitana de Asunción con las entidades públicas responsables de la gestión urbana (Municipalidad de Asunción, ANDE, ESSAP). El sistema expone tres superficies:

- **Portal Ciudadano** — crear, confirmar y seguir reportes; consultar puntos, créditos y ranking; canjear recompensas simuladas.
- **Panel Institucional** — revisar, validar, atender y resolver casos de la propia institución; cargar evidencias; administrar la recompensa institucional.
- **Mapa Público de transparencia** — accesible sin autenticación, con filtros Activos / Resueltos / Todos, métricas agregadas y ranking mensual.

La propuesta se articula sobre **cuatro ejes**:

1. **Identidad verificada pero privada** — Google OAuth + HMAC-SHA-256 de la cédula con clave secreta del servidor; la cédula nunca se almacena en texto plano.
2. **Clasificación asistida por IA** — Google Gemini vía API con *prompt engineering* zero-shot para categoría, institución, prioridad, inconsistencia y apoyo a la detección de duplicados. La IA **nunca** rechaza automáticamente un reporte.
3. **Transparencia** — mapa público, métricas de eficiencia institucional y ranking.
4. **Incentivos verificables** — confirmación ciudadana, puntos de impacto (pendientes → acreditados), créditos por institución 1:1, ranking del mes en curso con privacidad configurable y canje simulado.

El objetivo no es maximizar la cantidad de reportes sino favorecer **participación ciudadana útil y verificable**: los puntos nacen *pendientes* y solo se acreditan cuando una persona —un operador institucional— valida que la incidencia es legítima.

---

## 2. Fuentes documentales analizadas

Se leyeron íntegramente los cinco documentos disponibles en `docs/`. Cuando existieron contradicciones se aplicó el orden de prioridad definido por el proyecto.

| Prioridad | Documento | Contenido relevante | Rol en este plan |
|---|---|---|---|
| 1 | `Tesis_Ciudadania_Activa_Revisada_Recompensas.docx` | Capítulos 1–4 completos + Anexos A–H + §4.9 Módulo de Incentivos | **Fuente rectora.** Modelo de datos (18 entidades), API (Anexo B), casos de uso CU-01…CU-18, criterios de validación, alcance del MVP |
| 2 | `Cronograma_Ciudadania_Activa_Anexo_F.docx` | 16 sprints entre 01/08/2026 y 15/11/2026 con entregable semanal | **Fechas y objetivos de sprint.** No se reorganiza |
| 3 | `decisiones-incentivos.md` | Reglas 20/30/50, confirmación +10, créditos 1:1, canje, ranking, antiabuso | **Decisiones cerradas** del módulo de incentivos |
| 4 | `Matriz_Modificaciones_Ciudadania_Activa.docx` | 45 apartados del TCC a modificar + 5 correcciones de consistencia detectadas | Trazabilidad de los cambios y resolución de ambigüedades terminológicas |
| 5 | `Tesis_Ciudadania_Activa.pdf` (77 páginas) | Versión original del TCC | **Base académica y conceptual.** Superada en todo lo que los documentos posteriores modificaron |

Adicionalmente, `docs/PROMPT_PLANIFICACION_CLAUDE.md` fija las reglas de trabajo de esta etapa (no implementar, respetar el stack, no redefinir el producto).

---

## 3. Requisitos consolidados

### 3.1 Identidad y acceso

| ID | Requisito | Fuente |
|---|---|---|
| RF-01 | Registro e inicio de sesión mediante Google OAuth 2.0; emisión de JWT | 4.7 · Anexo B |
| RF-02 | Envío de número de cédula y selfie sosteniendo la cédula; la cédula se persiste **solo** como HMAC-SHA-256 con clave secreta del servidor | 4.7 · HT-3 · Anexo A |
| RF-03 | Estados de verificación: `PENDIENTE`, `APROBADO`, `RECHAZADO`; la aprobación es una revisión humana administrativa | Anexo A.2 |
| RF-04 | Unicidad del ciudadano garantizada por índice único sobre `citizen_profiles.document_hmac` | Anexo A.5 |
| RF-05 | Roles: `citizen`, `operator`, `admin`. Los operadores se vinculan a una institución mediante `institution_operators` | Anexo A.1 |
| RF-06 | Un usuario **no verificado** puede iniciar sesión y consultar el mapa público, pero no crear reportes, no confirmar, no generar puntos ni créditos y no canjear | 4.7 · Anexo B.1 |
| RF-07 | Preferencia de visibilidad en ranking: `FULL_NAME`, `FIRST_INITIAL`, `ALIAS`, `PRIVATE` | 4.9.5 · Anexo A.2 |

### 3.2 Reportes

| ID | Requisito | Fuente |
|---|---|---|
| RF-08 | Creación de reporte con categoría, descripción, **fotografía obligatoria** y ubicación (GPS o selección manual en mapa) | 4.6 · CU-03 |
| RF-09 | Ubicación persistida como `geometry(Point, 4326)` con índice GIST | Anexo A.4/A.5 |
| RF-10 | Estados: `CREADO`, `CLASIFICADO`, `ASIGNADO`, `EN_PROCESO`, `RESUELTO`, `CERRADO`, `RECHAZADO`. Prioridades: `BAJA`, `MEDIA`, `ALTA` | Anexo A.2 |
| RF-11 | Historial completo de cambios de estado en `report_status_history` con actor y motivo | Anexo A.1 |
| RF-12 | Detección de **archivo de foto exactamente idéntico** mediante hash SHA-256 (`report_media.file_hash`) | 4.9.7 · Anexo A.4 |
| RF-13 | Sugerencia de **posible duplicado** por proximidad antes de crear; **no bloquea** la creación. El ciudadano puede abrir el reporte existente y confirmarlo, o continuar | 4.9.7 · §10 del prompt |
| RF-14 | Motivos de rechazo tipificados: `fuera_de_competencia`, `duplicado`, `informacion_insuficiente`, `contenido_inapropiado` | Anexo A.2 |
| RF-15 | Evidencia de resolución (descripción + archivo) al pasar a `RESUELTO` | CU-07 |
| RF-16 | Cierre automático `RESUELTO → CERRADO` tras plazo configurable (por defecto 72 h) | Anexo A.2 |
| RF-17 | `reports.source` ∈ {`platform`, `telegram`, `x_mock`} para distinguir el origen | Anexo A.4 (versión original, se conserva) |

### 3.3 Confirmación ciudadana

| ID | Requisito | Fuente |
|---|---|---|
| RF-18 | Un ciudadano **verificado** puede confirmar un reporte activo de otra persona, declarando “Acabo de verificar que este problema todavía sigue ahí” | 4.9.4 |
| RF-19 | Una sola confirmación por usuario y reporte (`UNIQUE(report_id, citizen_id)`) | Anexo A.6 |
| RF-20 | El autor no puede confirmar su propio reporte | 4.9.7 |
| RF-21 | Solo reportes **activos** admiten confirmación. `RESUELTO`, `CERRADO` y `RECHAZADO` no | 4.4.3 · 4.9.4 |

### 3.4 Clasificación asistida por IA

| ID | Requisito | Fuente |
|---|---|---|
| RF-22 | La IA sugiere categoría, institución, prioridad, marca de inconsistencia y confianza; el resultado se persiste en `ai_classifications` | Anexo A.1 · CU-08 |
| RF-23 | La IA **no** declara falso un reporte ni lo rechaza automáticamente; los casos dudosos pasan a revisión humana institucional | 2.4.3 · 4.9.7 |
| RF-24 | Toda decisión de IA queda auditada en `audit_logs` con `actor_type` diferenciado | 2.4.3 |
| RF-25 | Ejecución asíncrona vía Celery, desacoplada del ciclo HTTP | 4.2.2 |
| RF-26 | No se envían datos identificatorios del ciudadano a la API externa, solo el contenido descriptivo del reporte | 2.4.3 |

### 3.5 Gestión institucional

| ID | Requisito | Fuente |
|---|---|---|
| RF-27 | El operador solo ve y gestiona reportes de **su** institución | 4.7 · Anexo B.1 |
| RF-28 | **Validación institucional**: el operador acepta la incidencia como legítima y confirma la institución responsable. Es una decisión humana, se registra como evento auditable (`validated_at`, `validated_by`) y **no** es un estado nuevo | 4.4.3 · CU-15 |
| RF-29 | El operador administra únicamente la recompensa de su institución | 4.9.8 |
| RF-30 | El administrador global gestiona instituciones, categorías, operadores, revisión de identidades y auditoría | CU-09 · CU-10 |

### 3.6 Incentivos, créditos, ranking y recompensas

| ID | Requisito | Fuente |
|---|---|---|
| RF-31 | Crear reporte → **+20 puntos PENDIENTES** al autor | `decisiones-incentivos.md` · Tabla 9 |
| RF-32 | Validación institucional → se **acreditan** los 20 pendientes **y** se agregan **+30 acreditados** (total 50) | ídem |
| RF-33 | Reporte `RESUELTO` → **+50 acreditados** al autor. Máximo por reporte propio: **100** | ídem |
| RF-34 | Confirmar reporte ajeno → **+10 puntos PENDIENTES**, que se acreditan cuando el reporte es validado | ídem |
| RF-35 | Conversión **1 punto acreditado = 1 crédito** de la institución que validó el reporte | 4.9.3 |
| RF-36 | Los puntos de impacto son globales, sirven para reputación/ranking y **no se gastan** al canjear | §7 del prompt |
| RF-37 | Los créditos se acumulan por institución, **no vencen** y sí se consumen al canjear; el remanente permanece | 4.9.3 · 4.9.6 |
| RF-38 | Si el reporte se reasigna después de validado, los créditos ya acreditados permanecen asociados a la institución que validó | 4.9.3 |
| RF-39 | Los reportes rechazados no generan puntos acreditados, y sus transacciones pendientes pasan a `ANULADO`: no pueden quedar pendientes indefinidamente | 4.9.7 + decisión del proyecto |
| RF-40 | Reversión formal de una validación → se revierten puntos y créditos del autor **y de los confirmadores**, sin penalización adicional. La reversión siempre puede ejecutarse; si los créditos ya fueron canjeados, el saldo institucional queda negativo y el vale emitido se conserva | Tabla 9 · §10 del prompt · decisión D-01 |
| RF-41 | Ranking **Top 10 del mes en curso** calculado solo con puntos **acreditados** del mes; los pendientes no participan; el impacto histórico del ciudadano no se borra. El MVP no publica rankings de meses anteriores | 4.9.5 |
| RF-42 | El usuario privado conserva posición y puntaje, mostrándose como `Usuario privado` | 4.9.5 |
| RF-43 | Catálogo institucional de recompensas **simuladas**; costo **fijo de 1.000 créditos** en el MVP, no editable por el operador; el canje genera un código único de vale | 4.9.6 · decisión D-02 |
| RF-44 | Máximo **un canje por institución, por usuario y por mes** | 4.9.6 |
| RF-45 | Reportes provenientes de Telegram o X simulado **no generan puntos para su fuente** no verificada; un ciudadano verificado sí puede confirmarlos y ganar puntos | 4.1 · CU-12 · CU-13 |

### 3.7 Transversales

| ID | Requisito | Fuente |
|---|---|---|
| RF-46 | Mapa público con filtros **Activos / Resueltos / Todos**, priorizando activos por defecto; los resueltos siguen visibles por transparencia pero no admiten confirmación | §12 del prompt · 4.1 |
| RF-47 | Dashboard institucional y público con métricas por período, categoría, estado, prioridad y SLA | CU-11 |
| RF-48 | Notificaciones por email simple sobre verificación, estados del reporte y recompensas | 4.6 |
| RF-49 | Auditoría transversal en `audit_logs` de acciones humanas y de IA, con payload estructurado | 4.9.8 |
| RF-50 | Trazabilidad del **100 %** de cambios de estado, validaciones y transacciones de incentivos | Tabla 6 |

### 3.8 Requisitos no funcionales

| ID | Requisito | Umbral |
|---|---|---|
| RNF-01 | Latencia P50 de endpoints críticos sin IA síncrona | ≤ 800 ms |
| RNF-02 | Latencia P99 bajo carga | ≤ 2.000 ms |
| RNF-03 | Throughput de creación de reportes | ≥ 50 reportes/min |
| RNF-04 | Cobertura de pruebas del backend crítico | ≥ 80 % |
| RNF-05 | Concordancia de la IA contra etiquetas de oro | ≥ 85 % |
| RNF-06 | Flujos E2E críticos exitosos | 100 % |
| RNF-07 | Reglas de incentivos correctas en escenarios sintéticos | 100 % |
| RNF-08 | Comunicaciones sobre HTTPS/TLS; secretos por variables de entorno | — |

---

## 4. MVP obligatorio

Alcance **cerrado** del prototipo, según la tabla 4.6 de la tesis revisada. Nada de esta lista puede posponerse.

| # | Componente | Sprint principal |
|---|---|---|
| 1 | Registro/login con Google OAuth | S3 |
| 2 | Cédula + selfie con revisión administrativa manual | S4 |
| 3 | Preferencias de privacidad del ranking | S4 |
| 4 | Mapa público con filtros Activos / Resueltos / Todos | S5 |
| 5 | Geolocalización GPS o selección manual | S5 |
| 6 | Creación de reportes con fotografía obligatoria | S6 |
| 7 | Detección de archivo de foto idéntico (hash) | S6 |
| 8 | Sugerencia de posibles duplicados sin bloqueo | S6 |
| 9 | Confirmación ciudadana de reportes activos | S7 |
| 10 | Clasificación IA de categoría / institución / prioridad + inconsistencia | S8 |
| 11 | Panel institucional, validación, cambio de estados y evidencias | S9 · S10 |
| 12 | Puntos pendientes / acreditados, bonus de validación y de resolución, reversión | S6 · S7 · S9 · S10 |
| 13 | Créditos institucionales 1:1 | S9 · S11 |
| 14 | Top 10 del mes en curso con privacidad configurable e impacto histórico | S11 |
| 15 | Catálogo institucional y canje simulado de 1.000 créditos (costo fijo en el MVP) | S12 |
| 16 | Administración de recompensa por institución (nombre, descripción y estado; el costo no es editable) | S12 |
| 17 | Notificaciones por email simple | S4 (mínimo) · S12–S13 (completo) |
| 18 | Dashboard institucional y público con métricas | S13 |
| 19 | Auditoría y trazabilidad completas | S10 (transversal) |

**Condicionales** (subordinados a la estabilidad del núcleo, S13): bot de Telegram que genera pre-reportes y simulación de X/Twitter con datos sintéticos.

---

## 5. Mejoras futuras

**No** forman parte del MVP y **no** deben adelantarse:

- Insignias, niveles y campañas temporales.
- Proximidad GPS obligatoria para confirmar.
- Similitud perceptual avanzada de imágenes (el MVP solo detecta archivos idénticos por hash).
- Recompensas económicas reales, convenios institucionales e integración con facturación.
- Antifraude avanzado.
- PWA y aplicación móvil nativa.
- Validación contra padrón oficial de cédulas.
- Notificaciones push (FCM) — el MVP entrega solo email.
- Confirmación o disconformidad del ciudadano sobre el cierre del reporte.
- Rankings de meses anteriores, selector de período e histórico de rankings mensuales cerrados — el MVP publica solo el Top 10 del mes en curso y el impacto histórico del ciudadano.
- Costo de recompensa configurable por el operador — en el MVP es fijo en 1.000 créditos.

---

## 6. Arquitectura propuesta

### 6.1 Stack tecnológico (sin cambios)

| Capa | Tecnología |
|---|---|
| Frontend | Next.js (React) + TailwindCSS |
| Backend | Python 3.11 + Django + Django REST Framework |
| Autenticación | Google OAuth 2.0 + JWT |
| Base de datos | PostgreSQL 17 + PostGIS, alojado en **Supabase** (instancia verificada: 17.6; el minor lo administra Supabase) |
| Almacenamiento | Supabase Storage (buckets privados para verificación, públicos-firmados para fotos) |
| Caché y broker | Redis 7 |
| Asincronía | Celery + Celery Beat |
| IA | Google Gemini vía API (modelo por variable de entorno) |
| Notificaciones | Email por **API HTTPS** de servicio transaccional (Brevo). **SMTP no es viable**: Railway no lo permite en los planes Free/Trial/Hobby (verificado el 23/08/2026, ADR-0015) |
| Hosting | Vercel (frontend) · Railway (backend + workers) · Supabase (DB + storage) |
| Pruebas | pytest + pytest-cov · Playwright · k6 |
| Calidad | Ruff (Python) · ESLint (TypeScript) · GitHub Actions |
| Observabilidad | Logs estructurados + Celery Flower |

> Estas tecnologías están fijadas por la documentación del TCC. Cualquier alternativa se registra como observación, nunca como sustitución.

> **Alojamiento de la base de datos (D-03, resuelta).** PostgreSQL + PostGIS se aloja en **Supabase**, que también provee el almacenamiento de archivos. El backend Django y los workers Celery se despliegan en **Railway** y se conectan mediante `DATABASE_URL`. Fundamento: coherencia con la arquitectura documentada en la tesis (4.2.3), soporte nativo de PostGIS, simplificación operativa del prototipo, centralización de base de datos y almacenamiento en un mismo proveedor, y facilidad para inspeccionar y documentar los datos durante el TCC. Ver §33.2.

> **Versiones reales de la base de datos (verificadas el 16/08/2026, ADR-0003).** La instancia provisionada por Supabase es **PostgreSQL 17.6**; el major productivo es **17** y el minor lo administra Supabase, que puede actualizarlo sin intervención del equipo. El desarrollo local y el CI usan también **PostgreSQL 17** (`postgis/postgis:17-3.5`) para evitar deriva de major entre entornos. PostGIS productivo es **3.3.7**, instalado en el esquema **`gis`**; el entorno local puede usar una versión posterior (hoy 3.5.2), manteniendo **3.3.7 como piso de compatibilidad**: el código no debe depender de funciones espaciales introducidas después de 3.3. La diferencia de esquemas se neutraliza con `DB_SEARCH_PATH=public,gis,extensions`, versionado en `config/settings/base.py`. La divergencia respecto de «PostgreSQL 15», que declara la tesis, queda registrada en [CAMBIOS_TESIS_PENDIENTES.md](CAMBIOS_TESIS_PENDIENTES.md) (CT-39, CT-40, CT-41).

### 6.2 Vista lógica

```text
┌────────────────────────────────────────────────────────────┐
│  Next.js — Portal Ciudadano · Panel Institucional · Mapa   │
└───────────────────────────┬────────────────────────────────┘
                            │ HTTPS / JSON / JWT
┌───────────────────────────▼────────────────────────────────┐
│              Django REST Framework  /api/v1/               │
│  auth_app · reports_app · rewards_app · map_app            │
│  analytics_app · notifications_app · admin_app             │
│  ai_client_app · social_ingestion_app · core               │
└──────┬───────────────┬────────────────┬────────────────────┘
       │               │                │
┌──────▼──────┐  ┌─────▼─────┐   ┌──────▼───────┐
│ PostgreSQL  │  │   Redis   │   │   Supabase   │
│  + PostGIS  │  │ caché/brk │   │   Storage    │
│  (Supabase) │  │           │   │              │
└─────────────┘  └─────┬─────┘   └──────────────┘
                       │
                 ┌─────▼──────┐        ┌──────────────┐
                 │  Celery    ├───────►│ Gemini API   │
                 │  + Beat    ├───────►│ Email        │
                 └────────────┘        └──────────────┘
```

### 6.3 Vista de procesos — qué es asíncrono y qué no

Esta es la decisión arquitectónica más importante del proyecto y condiciona el cumplimiento de HT-6.

**Asíncrono (Celery):**
- Clasificación por IA de un reporte recién creado.
- Envío de correos.
- Ingesta desde Telegram y carga de X simulado.
- Agregación de métricas del dashboard y precálculo/caché del ranking.
- Cierre automático `RESUELTO → CERRADO` a las 72 h (Celery Beat).

**Síncrono y transaccional (nunca por cola):**
- **Toda escritura sobre el ledger de incentivos.** `reports_app` invoca a `rewards_app` dentro de un mismo `transaction.atomic()`.

Motivo: la acreditación de puntos y la emisión de créditos deben ser atómicas respecto de la validación institucional. Si se delegaran a una cola, una falla del worker dejaría reportes validados sin puntos acreditados, lo que rompería el criterio de trazabilidad del 100 % y la hipótesis HT-6. La persistencia del reporte y la creación del evento `+20 PENDIENTE` ocurren igualmente en la misma transacción; solo la clasificación IA se encola.

### 6.4 Modelo de datos — 18 entidades

| Entidad | Propósito |
|---|---|
| `users` | Usuarios autenticados por Google OAuth, rol y estado de verificación |
| `citizen_profiles` | Identidad verificada (`document_hmac`), selfie, y preferencias de ranking |
| `institutions` | Entidades responsables y SLA |
| `institution_operators` | Vínculo operador ↔ institución |
| `categories` | Categorías urbanas, institución y prioridad por defecto |
| `reports` | Reporte georreferenciado, estado, prioridad, origen y datos de validación |
| `report_media` | Fotos y evidencias, con `file_hash` SHA-256 |
| `ai_classifications` | Salida de la IA, confianza y marca de inconsistencia |
| `report_status_history` | Historial de transiciones |
| `resolution_evidences` | Evidencias de resolución |
| `report_confirmations` | Confirmación ciudadana única |
| `reward_transactions` | **Ledger** de puntos de impacto y créditos |
| `rewards` | Catálogo institucional de recompensas simuladas |
| `reward_redemptions` | Canjes y códigos emitidos |
| `audit_logs` | Auditoría transversal humana y de IA |
| `notifications` | Correos enviados o pendientes |
| `telegram_ingested_messages` | Mensajes del bot |
| `x_mock_messages` | Dataset social simulado |

Enumeraciones: `user_role`, `verification_status`, `report_status`, `report_priority`, `report_source`, `reward_tx_status` (`PENDIENTE`/`ACREDITADO`/`ANULADO`/`REVERTIDO`), `ranking_visibility` (`FULL_NAME`/`FIRST_INITIAL`/`ALIAS`/`PRIVATE`).

> `ANULADO` y `REVERTIDO` **no** son sinónimos y no deben usarse indistintamente: `ANULADO` corresponde a una transacción que estuvo pendiente y nunca llegó a acreditarse porque el reporte fue rechazado; `REVERTIDO` corresponde a una transacción que sí estuvo acreditada y posteriormente fue revertida. Ver §6.5.

### 6.5 Diseño del ledger de incentivos

Requisito explícito del proyecto: **no** existe un contador mutable del tipo `user.points += 50`. Todo saldo es derivado del ledger.

**Tipos de evento (`reward_transactions.event_type`):**

| `event_type` | `impact_delta` | `credit_delta` | Status inicial | Disparador |
|---|---|---|---|---|
| `REPORT_CREATED` | +20 | +20 | `PENDIENTE` | Creación del reporte por ciudadano verificado |
| `CONFIRMATION_CREATED` | +10 | +10 | `PENDIENTE` | Confirmación válida |
| `VALIDATION_BONUS` | +30 | +30 | `ACREDITADO` | Validación institucional |
| `RESOLUTION_BONUS` | +50 | +50 | `ACREDITADO` | Transición a `RESUELTO` |
| `REDEMPTION` | 0 | −1.000 | `ACREDITADO` | Canje de recompensa |
| `REVERSAL` | negativo | negativo | `REVERTIDO` | Reversión de validación (solo admin) |

**Los valores económicos de una transacción son inmutables.** `impact_delta` y `credit_delta` se fijan en el momento de crear la fila y **no vuelven a modificarse nunca**. Una transacción nace ya con su valor definitivo —`REPORT_CREATED` con 20/20 y `CONFIRMATION_CREATED` con 10/10— y lo conserva durante toda su vida. Lo único que cambia después es su **estado**, su **institución** y sus **marcas temporales**.

Esto no adelanta ningún beneficio: **mientras una transacción está `PENDIENTE` no participa de ningún saldo**, ni de los puntos de impacto ni de los créditos disponibles, porque todas las fórmulas de saldo filtran por `status = 'ACREDITADO'`. Los 20 y los 10 créditos existen desde el inicio como valor potencial de la contribución, no como saldo utilizable.

La ventaja de este diseño es que la conversión 1:1 queda determinada en el origen y no depende de una operación posterior que reescriba importes: un `UPDATE` sobre `credit_delta` durante la acreditación convertiría al ledger en un registro mutable en su contenido económico, que es exactamente lo que la tesis exige evitar. Con valores inmutables, cualquier auditoría puede releer la fila original y obtener el mismo importe que se calculó el primer día.

**Estados de la transacción (`reward_tx_status`):**

| Estado | Significado | Participa en los saldos |
|---|---|---|
| `PENDIENTE` | Generada por una acción ciudadana, todavía sin validación institucional | Solo en “puntos pendientes” |
| `ACREDITADO` | Confirmada por decisión humana; genera impacto y créditos | Sí |
| `ANULADO` | Estuvo pendiente y **nunca** llegó a acreditarse porque el reporte fue rechazado | No |
| `REVERTIDO` | **Sí estuvo acreditada** y posteriormente fue revertida | No |

`ANULADO` y `REVERTIDO` describen situaciones distintas y no son intercambiables. El primero cierra una expectativa que nunca se materializó; el segundo deshace un derecho que ya existía. Distinguirlos es lo que permite auditar por separado cuántos puntos se perdieron por rechazo y cuántos por reversión.

**Acreditación.** Al validar el reporte, dentro de una única transacción:
1. Las filas `PENDIENTE` asociadas al reporte (el `REPORT_CREATED` del autor y todos los `CONFIRMATION_CREATED`) pasan a `ACREDITADO`, fijando **únicamente** `status`, `institution_id` (la institución validante) y `credited_at`. **Los deltas no se tocan**: ya nacieron con 20/20 y 10/10 respectivamente.
2. Se inserta `VALIDATION_BONUS` (+30 / +30) para el autor.
3. Se escribe una entrada en `audit_logs` con el detalle completo del movimiento.

**Anulación por rechazo.** Al rechazar un reporte, dentro de la misma transacción, todas sus filas `PENDIENTE` —la del autor y las de todos los confirmadores— pasan a `ANULADO` fijando `voided_at`. Ninguna transacción puede quedar pendiente de forma indefinida: un reporte rechazado cierra el ciclo de sus puntos. No se insertan filas compensatorias porque esos puntos nunca formaron parte de ningún saldo. La anulación se audita con el motivo de rechazo.

**Reversión.** Solo el administrador global, con motivo obligatorio:
1. Las filas `ACREDITADO` del reporte pasan a `REVERTIDO` (`reversed_at`).
2. Se inserta, por cada una, una fila `REVERSAL` con deltas negativos y `related_transaction_id` apuntando a la original.
3. Si el reporte tuviera además filas `PENDIENTE` sin acreditar, pasan a `ANULADO`.
4. Nunca se borra ni se sobrescribe historial; el rastro queda completo y reconstruible.

La reversión **siempre puede ejecutarse**, incluso si los créditos derivados ya fueron canjeados. Ver más abajo la regla de saldo negativo.

**Saldos derivados (siempre por agregación, nunca almacenados):**

| Magnitud | Cálculo |
|---|---|
| Puntos de impacto históricos | `Σ impact_delta WHERE status = 'ACREDITADO'` |
| Puntos pendientes | `Σ impact_delta WHERE status = 'PENDIENTE'` |
| Créditos disponibles por institución | `Σ credit_delta WHERE status = 'ACREDITADO' GROUP BY institution_id` (los canjes son filas negativas) |
| Ranking del mes en curso | `Σ impact_delta WHERE status = 'ACREDITADO' AND credited_at ∈ [mes actual]`, orden descendente, límite 10 |

Todas las fórmulas filtran por estado, nunca por tipo de evento. Las filas `PENDIENTE`, `ANULADO` y `REVERTIDO` quedan fuera del conjunto `ACREDITADO`, de modo que la suma es siempre correcta sin necesidad de compensaciones aritméticas ni de reescribir importes; las filas `REVERSAL` existen exclusivamente como rastro de auditoría.

**Saldo institucional negativo.** El saldo de créditos de una institución es siempre la **diferencia entre los créditos válidos y los créditos consumidos**:

```text
saldo(institución) = Σ créditos acreditados vigentes − Σ créditos consumidos en canjes
```

Ambos términos salen de la misma agregación, porque los canjes se registran como filas negativas. Cuando una reversión retira del conjunto `ACREDITADO` las filas que respaldaban un canje ya realizado, el minuendo disminuye mientras el sustraendo permanece, y la diferencia puede resultar **negativa**. Ese es el comportamiento correcto y deliberado: el saldo expresa que se consumieron créditos que dejaron de estar respaldados. Consecuencias:

- La reversión nunca se bloquea por la existencia de un canje previo.
- **No se aplica ninguna penalización adicional** al ciudadano: el resultado es solo la diferencia aritmética entre créditos válidos y consumidos, sin recargo, multa ni descuento extra de ningún tipo. Su magnitud depende de cuántos créditos vigentes le queden en esa institución y no coincide necesariamente con el importe canjeado.
- El **vale simulado ya emitido permanece emitido y válido**; no se anula ni se invalida su código.
- El ciudadano **no puede volver a canjear** en esa institución hasta que la diferencia vuelva a alcanzar el costo requerido, lo que ocurre naturalmente al acumular nuevas acreditaciones.
- La interfaz muestra el saldo negativo de forma explícita y explicada, sin ocultarlo.

**Ranking y reversiones.** El ranking se calcula siempre en vivo sobre el estado actual del ledger: una anulación o una reversión retira automáticamente las filas afectadas del conjunto `ACREDITADO` y el resultado se corrige solo. Como el MVP publica únicamente el **Top 10 del mes en curso**, no existen rankings de meses anteriores que preservar ni que recalcular, y por tanto tampoco se requiere ninguna regla de imputación de reversiones a períodos cerrados.

**Campos de soporte añadidos al Anexo A:** `credited_at`, `voided_at` y `reversed_at` sobre `reward_transactions`. Son columnas técnicas necesarias para el cálculo del ranking del mes en curso y la trazabilidad temporal; no alteran el alcance funcional ni las reglas del módulo.

### 6.6 Vista de despliegue

Frontend Next.js en Vercel (CDN + SSR); backend Django, `rewards_app` y workers Celery en Railway; almacenamiento de archivos en Supabase Storage; Redis gestionado como caché y broker. Comunicación exclusivamente sobre HTTPS/TLS; secretos por variables de entorno (`.env.example` versionado, `.env` nunca).

**PostgreSQL/PostGIS reside en Supabase**, junto con el almacenamiento de archivos. El backend Django y los workers Celery corren en Railway y se conectan mediante `DATABASE_URL`; el desarrollo local usa siempre el contenedor de `infra/docker-compose.yml`, de modo que ningún código distingue entre uno y otro entorno.

**Latencia entre Railway y Supabase.** Al residir el backend y la base en proveedores distintos, cada consulta atraviesa la red pública. Se trata como un **riesgo de infraestructura que se mide durante las pruebas de rendimiento del Sprint 15** (P50 ≤ 800 ms, P99 ≤ 2.000 ms, ≥ 50 reportes/min), no como un motivo para modificar la arquitectura. Mitigaciones ya previstas en el diseño: caché en Redis de las lecturas frecuentes del mapa y del ranking, índices dedicados, `select_related`/`prefetch_related` para evitar consultas N+1 y agrupación de las operaciones del ledger en una única transacción. Si alguna medición incumpliera un umbral, se optimizan consultas y caché antes de considerar cualquier cambio de infraestructura.

**Respaldos.** Si el plan de Supabase contratado **no incluye respaldos automáticos**, es obligatorio realizar **respaldos lógicos periódicos** durante todo el desarrollo (`pg_dump` semanal como mínimo, y siempre antes de aplicar migraciones destructivas o de regenerar el dataset sintético). Los volcados se guardan fuera del repositorio y nunca se versionan, ya que pueden contener datos de las cuentas de prueba. La política de respaldos vigente se documenta en `ADR-0003` y su cumplimiento se registra en `PROGRESO_SPRINTS.md`.

> **Ejecución real (verificada el 20/08/2026, ADR-0003).** La condición anterior quedó resuelta: el plan **Free** de Supabase **no ofrece respaldos programados** accesibles para este proyecto, de modo que la rutina de respaldo lógico manual está **activa y es obligatoria**. El procedimiento se comprobó de extremo a extremo —volcado en formato *custom* con `--no-owner --no-acl` fuera del repositorio, validado con `pg_restore --list` y con una **restauración real** sobre una instancia PostgreSQL/PostGIS 17 temporal— con dos límites documentados: la extensión `supabase_vault` no existe en PostgreSQL vanilla, y la versión de PostGIS del destino debe ser compatible con **3.3.7**. Detalle en `ADR-0003` y en el Sprint 1 de [PROGRESO_SPRINTS.md](PROGRESO_SPRINTS.md).

> **Despliegue efectivo (verificado el 20/08/2026).** La vista de despliegue descrita arriba está operativa: backend Django y un servicio separado de **worker de Celery** en Railway, Redis gestionado en Railway, frontend Next.js en Vercel y PostgreSQL/PostGIS más almacenamiento en Supabase, con `CORS_ALLOWED_ORIGINS` restringido al dominio del frontend. **Celery Beat todavía no se despliega**: la primera tarea periódica está prevista en el Sprint 10.

---

## 7. Estructura propuesta del repositorio

Monorepo con dos aplicaciones desplegables independientes.

```text
ciudadania-activa/
├── backend/
│   ├── config/
│   │   ├── settings/           base.py · dev.py · prod.py · test.py
│   │   ├── urls.py             enrutado /api/v1/
│   │   ├── celery.py           app Celery + Beat schedule
│   │   └── asgi.py · wsgi.py
│   ├── apps/
│   │   ├── core/               modelo base (UUID + timestamps), permisos reutilizables,
│   │   │                       paginación, manejo de errores, servicio de auditoría,
│   │   │                       cliente de Supabase Storage
│   │   ├── auth_app/           users, citizen_profiles, Google OAuth, JWT,
│   │   │                       verificación de identidad, visibilidad de ranking
│   │   ├── reports_app/        reports, report_media, report_confirmations,
│   │   │                       report_status_history, resolution_evidences,
│   │   │                       máquina de estados, detección de duplicados
│   │   ├── rewards_app/        reward_transactions (ledger), rewards,
│   │   │                       reward_redemptions, saldos, ranking, canjes
│   │   ├── map_app/            consultas geoespaciales y endpoints públicos del mapa
│   │   ├── analytics_app/      métricas agregadas, dashboards institucional y público
│   │   ├── notifications_app/  notifications, plantillas, envío por email
│   │   ├── admin_app/          catálogos, operadores, revisión de identidad,
│   │   │                       consulta de auditoría, reversión de validación
│   │   ├── ai_client_app/      cliente Gemini, prompts, parser JSON, mock de pruebas
│   │   └── social_ingestion_app/  Telegram + X simulado (patrón Strategy)
│   ├── scripts/                seed de catálogos · generador del dataset sintético
│   ├── tests/                  fixtures y utilidades compartidas
│   ├── pyproject.toml · requirements*.txt · manage.py
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── app/                App Router
│   │   │   ├── (public)/       inicio · mapa · reporte/[id] · ranking
│   │   │   ├── (citizen)/      panel · reportes/nuevo · mis-reportes ·
│   │   │   │                   recompensas · perfil
│   │   │   ├── (operator)/     bandeja · reporte/[id] · recompensas · dashboard
│   │   │   └── (admin)/        identidades · catálogos · auditoría
│   │   ├── features/           auth · identity · reports · map · confirmations ·
│   │   │                       rewards · ranking · dashboard
│   │   ├── components/ui/      componentes compartidos (Tailwind)
│   │   ├── lib/                cliente API tipado · sesión · hooks · formateadores
│   │   └── types/              contratos de la API
│   ├── e2e/                    Playwright
│   └── package.json · tailwind.config.ts · next.config.mjs
├── docs/
│   ├── PLAN_DESARROLLO.md
│   ├── PROGRESO_SPRINTS.md
│   ├── adr/                    ADR-0001…ADR-0015
│   └── (documentación del TCC)
├── infra/
│   ├── docker-compose.yml      postgres+postgis · redis · backend · worker
│   └── k6/                     escenarios de carga
├── .github/workflows/          ci-backend.yml · ci-frontend.yml
├── .env.example · .gitignore · README.md
```

**Convención interna de cada app Django:**

```text
apps/<nombre>_app/
├── models/          o models.py
├── serializers.py   validación y forma de entrada/salida
├── services/        lógica de dominio (única capa que escribe)
├── selectors.py     consultas de lectura
├── permissions.py   reglas de acceso
├── tasks.py         tareas Celery
├── urls.py · views.py
└── tests/
```

Regla transversal: **las vistas no contienen reglas de negocio**. Toda escritura pasa por `services/`, lo que hace testeables las reglas de incentivos de forma unitaria e independiente del transporte HTTP.

**Sobre la división en apps.** La estructura de nueve aplicaciones documentada en 4.2.4 se sostiene y se conserva sin modificar el alcance funcional. Dos precisiones:

- Se agrega `core/` como paquete de utilidades compartidas (modelo base, auditoría, permisos, storage). No es un dominio nuevo ni introduce funcionalidad; evita dependencias circulares entre apps.
- `map_app` y `analytics_app` son **capas de lectura** sobre los modelos de `reports_app` y `rewards_app`; no poseen modelos propios. Se mantienen como apps separadas porque encapsulan responsabilidades distintas (consulta geoespacial y agregación de métricas) y porque así están declaradas en la tesis.

---

## 8. Modelo conceptual de módulos

| Módulo | Responsabilidad | Modelos que posee |
|---|---|---|
| `core` | Utilidades transversales, auditoría, storage | — |
| `auth_app` | Identidad, roles, verificación, privacidad | `users`, `citizen_profiles` |
| `admin_app` | Catálogos, operadores, revisión y reversión | `institutions`, `institution_operators`, `categories`, `audit_logs` |
| `reports_app` | Ciclo de vida del reporte y confirmaciones | `reports`, `report_media`, `report_confirmations`, `report_status_history`, `resolution_evidences` |
| `ai_client_app` | Clasificación asistida | `ai_classifications` |
| `rewards_app` | Ledger, saldos, ranking, catálogo y canjes | `reward_transactions`, `rewards`, `reward_redemptions` |
| `map_app` | Consultas geoespaciales públicas | — |
| `analytics_app` | Métricas y dashboards | — |
| `notifications_app` | Avisos por email | `notifications` |
| `social_ingestion_app` | Telegram y X simulado | `telegram_ingested_messages`, `x_mock_messages` |

**Contrato entre `reports_app` y `rewards_app`.** `reports_app` no conoce las reglas de puntuación; invoca funciones de dominio explícitas expuestas por `rewards_app`:

```text
rewards_app.services.ledger.on_report_created(report)
rewards_app.services.ledger.on_confirmation_created(confirmation)
rewards_app.services.ledger.on_report_validated(report, institution, actor)
rewards_app.services.ledger.on_report_resolved(report, actor)
rewards_app.services.ledger.revert_validation(report, actor, reason)
```

Todas se ejecutan dentro de la transacción del llamador. Esto mantiene las reglas de incentivos en un único lugar auditable y testeable.

---

## 9. Dependencias entre módulos

```text
core
 └── auth_app
      ├── admin_app  (revisión de identidad, catálogos, operadores)
      └── reports_app  (requiere ciudadano verificado)
           ├── ai_client_app        (clasifica el reporte creado)
           ├── map_app              (lee ubicaciones y estados)
           ├── social_ingestion_app (crea reportes de origen no verificado)
           └── rewards_app          (ledger: creación, confirmación, validación,
                │                     resolución, reversión)
                ├── ranking mensual
                ├── créditos por institución
                └── catálogo y canjes
                     └── notifications_app
           └── analytics_app  (agrega reportes + transacciones)
```

Ningún módulo puede desarrollarse aisladamente: `rewards_app` carece de sentido sin la validación institucional de `reports_app`, y esta a su vez requiere la verificación de identidad de `auth_app`.

---

## 10. Estrategia de desarrollo

- **Metodología:** SCRUM adaptado, sprints semanales con planificación, desarrollo, revisión por pares y retrospectiva breve. Registro semanal en `docs/PROGRESO_SPRINTS.md`.
- **Control de versiones:** Git + GitHub. Rama `main` protegida. Una rama por funcionalidad: `feat/s06-creacion-reportes`, `fix/…`, `docs/…`. Pull request obligatoria con revisión del otro autor antes de integrar.
- **Commits:** convención `tipo(alcance): descripción` (`feat(rewards): acreditar puntos pendientes al validar`).
- **TDD en el dominio crítico:** reglas de incentivos, permisos por rol y máquina de estados se escriben primero como prueba. El resto sigue desarrollo convencional con pruebas posteriores.
- **CI (GitHub Actions):** en cada PR — Ruff + `pytest` con servicios PostGIS y Redis para backend; ESLint + `tsc --noEmit` + `next build` para frontend. La PR no se integra con CI en rojo.
- **Definición de “hecho” por sprint:** funcionalidad implementada · pruebas del sprint en verde · cobertura del dominio nuevo ≥ 80 % · documentación actualizada · demo reproducible sobre datos semilla · entrada completada en `PROGRESO_SPRINTS.md`.
- **Entornos:** desarrollo local con `infra/docker-compose.yml` (PostGIS + Redis) en todos los sprints; despliegue continuo desde `main` a partir del Sprint 1, con el frontend en Vercel, el backend y los workers en Railway, y la base de datos PostgreSQL/PostGIS junto con el almacenamiento en Supabase. La conexión se configura siempre mediante `DATABASE_URL`.
- **Respaldos:** si el plan de Supabase contratado no incluye respaldos automáticos, se ejecuta un respaldo lógico (`pg_dump`) semanal y antes de cada migración destructiva, guardado fuera del repositorio. *Condición confirmada el 20/08/2026: el plan Free no los incluye, por lo que la rutina manual está activa (§6.6).*
- **Datos:** `scripts/seed_catalogs.py` (instituciones, categorías, operadores) desde el Sprint 2 y `scripts/generate_dataset.py` con semilla fija para el dataset sintético, ampliado sprint a sprint hasta su versión definitiva en el Sprint 15.
- **Gestión del alcance:** cada sprint declara explícitamente qué queda **fuera**. Ninguna mejora futura se adelanta, aunque sea técnicamente sencilla.

---
## 11. Sprint 1 — Entorno, repositorio y CI/CD

**Fechas:** 01/08/2026 – 07/08/2026

### Objetivo
Dejar el monorepo operativo de punta a punta: ambos proyectos arrancan localmente, la integración continua corre en cada PR y existe un despliegue vacío pero funcional. Al cerrar el sprint, cualquier funcionalidad posterior debe poder construirse sin tocar infraestructura, con `AUTH_USER_MODEL` ya fijado y la base PostgreSQL/PostGIS aprovisionada en Supabase.

### Funcionalidades
Ninguna funcionalidad de negocio. Se entrega la base técnica, el backlog trasladado del Anexo E y la definición técnica del MVP actualizado.

### Backend
Proyecto Django con `config/settings/{base,dev,prod,test}.py`; DRF configurado con paginación, manejo de errores y versionado `/api/v1/`; las nueve apps más `core` creadas y registradas en `INSTALLED_APPS`; `config/celery.py` con app Celery y Beat; conexión a PostGIS mediante `django.contrib.gis`; endpoint `GET /api/v1/health/`; Ruff y `pytest` configurados.

**Modelo de usuario personalizado desde la primera migración.** El proyecto usa un `User` propio, y Django solo permite fijar `AUTH_USER_MODEL` **antes** de aplicar la primera migración: cambiarlo después obliga a recrear la base o a una migración manual de datos larga y frágil. Por eso el Sprint 1 no puede migrar con el usuario estándar. Se entrega:

- `AUTH_USER_MODEL = "auth_app.User"` declarado en `config/settings/base.py` desde el primer commit.
- `apps/auth_app` con un **esqueleto mínimo** de `User`: `AbstractBaseUser` + `PermissionsMixin`, PK `UUID`, `email` único como `USERNAME_FIELD`, `full_name`, `is_active`, `is_staff`, `date_joined` y su `UserManager` con `create_user` / `create_superuser`.
- La migración inicial de `auth_app` aplicada **antes** que cualquier otra migración que referencie usuarios.

El resto de campos del dominio (`google_oauth_id`, `role`, `verification_status`) y el modelo `citizen_profiles` se agregan en el **Sprint 2** mediante migraciones incrementales sobre este mismo modelo. Ninguna app declara todavía claves foráneas a `User`: cuando lo hagan, deben referenciar `settings.AUTH_USER_MODEL`, nunca la clase directamente.

### Frontend
Proyecto Next.js con App Router y TypeScript; TailwindCSS con la paleta y tipografía del proyecto; estructura `app/(public|citizen|operator|admin)`, `features/`, `components/ui/`, `lib/`; cliente HTTP tipado con lectura de `NEXT_PUBLIC_API_URL`; página de inicio que consulta `/health/` y muestra el estado del backend; ESLint y Prettier.

### Base de datos
Sin modelos de dominio salvo el esqueleto de `auth_app.User`. Se aprovisiona el proyecto de **Supabase** y su instancia PostgreSQL 17, se habilita PostGIS con `CREATE EXTENSION postgis` —que en Supabase queda instalada en el esquema `gis`, de modo que la aplicación declara `DB_SEARCH_PATH=public,gis,extensions` para resolver tipos y funciones espaciales— y se conecta el backend de Railway mediante `DATABASE_URL`. Se ejecutan las migraciones de Django (`contenttypes`, `auth`, `sessions`, `admin`) junto con **`auth_app.0001_initial`**, que crea la tabla del usuario personalizado. A partir de este punto `AUTH_USER_MODEL` queda fijado y no vuelve a cambiar durante todo el proyecto.

Se verifica si el plan de Supabase contratado incluye respaldos automáticos. **Si no los incluye**, se documenta y se pone en marcha desde este sprint la rutina de **respaldos lógicos periódicos** (`pg_dump` semanal y antes de cada migración destructiva), con los volcados almacenados fuera del repositorio.

### API
`GET /api/v1/health/` → `{status, version, db, redis}`.

### Seguridad y permisos
`.env` fuera de control de versiones y `.env.example` versionado con todas las claves (sin valores). `DEBUG=False` en producción, `ALLOWED_HOSTS` y CORS restringidos al dominio del frontend. Secretos de Supabase, Gemini y Google OAuth cargados como variables de entorno del proveedor, nunca en el repositorio. HTTPS forzado.

### Pruebas
Unitaria de humo sobre `/health/`. Unitarias del modelo de usuario: `create_user` y `create_superuser` funcionan, el `email` es único y `get_user_model()` devuelve `auth_app.User`. Verificación de que `manage.py migrate` sobre una base vacía aplica `auth_app.0001_initial` sin errores y de que `manage.py makemigrations --check` no detecta cambios pendientes. Verificación de que el workflow de CI falla ante un lint roto. Comprobación manual del despliegue en Vercel y Railway.

### Dependencias
Ninguna.

### Criterios de aceptación
- `docker compose up` levanta PostGIS y Redis; `manage.py runserver` y `next dev` funcionan.
- El backend desplegado en Railway se conecta por `DATABASE_URL` a la instancia PostgreSQL/PostGIS de Supabase, con PostGIS habilitado.
- Consta si el plan de Supabase incluye respaldos automáticos y, si no los incluye, la rutina de respaldos lógicos está documentada y en marcha.
- `AUTH_USER_MODEL` apunta a `auth_app.User` y la migración inicial se aplica sobre una base vacía sin usar el usuario estándar de Django.
- `manage.py createsuperuser` crea una cuenta con el modelo personalizado.
- La PR de prueba ejecuta CI de backend y frontend y ambos pasan.
- El frontend desplegado consulta correctamente el backend desplegado.
- `.env.example` documenta todas las variables previstas del proyecto.
- El backlog del Anexo E está trasladado al tablero con prioridades Alta/Media/Baja.

### Resultado demostrable
Página pública desplegada que muestra “backend conectado · base de datos OK · redis OK”, y un PR con los checks de CI en verde.

### Documentación
`README.md` con requisitos, instalación local, variables de entorno y comandos. `docs/adr/ADR-0001-monorepo.md`, `ADR-0002-django-modular-por-apps.md`, `ADR-0003-postgis-y-alojamiento-en-supabase.md` —con el fundamento de la decisión y la política de respaldos vigente— y `ADR-0014-custom-user-desde-la-primera-migracion.md`. Entrada del Sprint 1 en `PROGRESO_SPRINTS.md`, indicando si el plan de Supabase incluye respaldos automáticos.

### Riesgos
**Riesgo bloqueante si se omite:** migrar con el usuario estándar de Django haría imposible introducir el `User` propio en el Sprint 2 sin recrear la base. Por eso `AUTH_USER_MODEL` se fija en este sprint y no después. Otros riesgos: instalación de GDAL/GEOS para GeoDjango en Windows (mitigación: desarrollo sobre el contenedor de `infra/docker-compose.yml`); límites del plan gratuito de Vercel, Railway y Supabase; ausencia de respaldos automáticos según el plan de Supabase contratado (mitigación: rutina de respaldos lógicos desde este mismo sprint); latencia de red entre Railway y Supabase, que se mide en el Sprint 15 y no altera la arquitectura; configuración inicial de OAuth en Google Cloud Console, que conviene iniciar ya aunque se use en el Sprint 3.

### Fuera de alcance del sprint
Cualquier otro modelo de dominio, los campos de negocio del usuario (`google_oauth_id`, `role`, `verification_status`), `citizen_profiles`, autenticación real, endpoints de negocio, pantallas del portal e integración con Gemini.

> **Ejecución real.** Esta sección es **planificación y no se reescribe**. El Sprint 1 se ejecutó entre el 12/08/2026 y el 20/08/2026 y quedó **Completado**: todos los criterios de aceptación anteriores están cumplidos, incluidos el despliegue en Railway y Vercel, la verificación del plan de respaldos de Supabase y la puesta en marcha de la rutina manual de `pg_dump`. El desvío de fechas respecto del 01/08–07/08 planificado, junto con las evidencias de cada criterio, se registra en el Sprint 1 de [PROGRESO_SPRINTS.md](PROGRESO_SPRINTS.md) y en [CAMBIOS_TESIS_PENDIENTES.md](CAMBIOS_TESIS_PENDIENTES.md) (CT-29).

---

## 12. Sprint 2 — Modelo de datos e incentivos

**Fechas:** 08/08/2026 – 14/08/2026

### Objetivo
Materializar las 18 entidades del Anexo A, incluidas las del módulo de incentivos, con sus enumeraciones, restricciones e índices, y dejar cargados los catálogos semilla de instituciones y categorías.

### Funcionalidades
Modelo de datos completo y datos semilla. Sin interfaz de usuario propia.

### Backend
Modelos Django distribuidos según §8. Modelo base en `core` con PK `UUID` y `created_at`/`updated_at`. Enumeraciones como `TextChoices`. Servicio de auditoría `core.services.audit.log(actor, action, payload)` disponible desde el inicio. Esqueleto de `rewards_app.services.ledger` con las funciones de dominio declaradas y sus reglas todavía sin conectar a `reports_app`. Script `scripts/seed_catalogs.py`.

El modelo `auth_app.User` **ya existe desde el Sprint 1**; aquí se **amplía** mediante migraciones incrementales con `google_oauth_id`, `role` y `verification_status`, y se crea `citizen_profiles`. No se redefine el modelo ni se toca `AUTH_USER_MODEL`. Todas las claves foráneas a usuarios se declaran contra `settings.AUTH_USER_MODEL`.

### Frontend
Definición de los tipos TypeScript espejo de los modelos en `src/types/` para que los sprints siguientes trabajen contra contratos estables. Sin pantallas nuevas.

### Base de datos
Migraciones de las 18 tablas: 17 nuevas más la ampliación incremental de `users`, creada en el Sprint 1. Las tres **columnas de soporte** de `reward_transactions` se definen aquí desde el principio, no se agregan más adelante:

| Columna | Se fija cuando | Estado resultante |
|---|---|---|
| `credited_at` | El reporte es validado institucionalmente | `ACREDITADO` |
| `voided_at` | El reporte es rechazado y la transacción seguía pendiente | `ANULADO` |
| `reversed_at` | Una validación acreditada es revertida por el administrador | `REVERTIDO` |

Restricciones e índices:
- Índice **GIST** sobre `reports.location`.
- B-tree sobre `reports.status`, `reports.priority`, `reports.created_at`, `reports.institution_id`.
- Único sobre `citizen_profiles.document_hmac`.
- Único sobre `report_confirmations(report_id, citizen_id)`.
- **No se define ningún `CHECK` de autoconfirmación.** La regla “el autor no puede confirmar su propio reporte” exige comparar `report_confirmations.citizen_id` con `reports.citizen_id`, es decir, consultar otra tabla, lo que una restricción `CHECK` no puede hacer en PostgreSQL sin recurrir a un *trigger* o a una función marcada como inmutable de forma incorrecta. La regla vive en el servicio de dominio (Sprint 7), donde además puede devolver un mensaje de error comprensible.
- Índices sobre `reward_transactions(user_id, institution_id, status, created_at)` y `reward_transactions(status, credited_at)` para el ranking.
- Índice sobre `reward_redemptions(user_id, reward_id, created_at)` y único sobre `reward_redemptions.code`.
- `reports.citizen_id` **nullable** (reportes ingestados sin ciudadano verificado) y `reports.source` obligatorio.

Semilla: instituciones (Municipalidad de Asunción, ANDE, ESSAP) y las diez categorías con su institución y prioridad por defecto, según el mapeo del Anexo A.3.

### API
Ninguna ruta pública nueva. Registro de todos los modelos en el admin de Django para inspección interna.

### Seguridad y permisos
El admin de Django queda restringido a superusuarios y no se expone como interfaz de operación del sistema. Los campos sensibles (`document_hmac`, `verification_selfie_url`) se marcan como no serializables por defecto.

### Pruebas
Unitarias de creación de cada modelo; pruebas de que las restricciones únicas efectivamente rechazan (incluida `UNIQUE(report_id, citizen_id)` en confirmaciones); prueba de que el enumerado `reward_tx_status` acepta los cuatro estados `PENDIENTE`, `ACREDITADO`, `ANULADO` y `REVERTIDO`; verificación de que el índice GIST existe y una consulta `ST_DWithin` responde; ejecución idempotente del seed.

### Dependencias
Sprint 1 (proyecto y base de datos operativos).

### Criterios de aceptación
- `manage.py migrate` desde cero crea las 18 tablas sin errores, partiendo del `auth_app.User` del Sprint 1 y sin recrear la base.
- `reward_transactions` incluye `credited_at`, `voided_at` y `reversed_at` desde su migración inicial.
- El seed carga 3 instituciones y 10 categorías y es idempotente.
- Las restricciones de unicidad están cubiertas por pruebas que las verifican.
- El enumerado de estados del ledger incluye `ANULADO` además de `PENDIENTE`, `ACREDITADO` y `REVERTIDO`.
- El diagrama ER generado coincide con el Anexo A.

### Resultado demostrable
Recorrido por el admin de Django mostrando las 18 entidades y los catálogos cargados, más el diagrama ER exportado.

### Documentación
Diccionario de datos en `docs/` actualizado respecto del Anexo A, señalando los tres campos de soporte añadidos —`credited_at`, `voided_at` y `reversed_at`— y el cuarto estado `ANULADO`. `ADR-0003-postgis.md` y `ADR-0004-ledger-auditable.md`.

### Riesgos
Cambios posteriores en el modelo que obliguen a migraciones correctivas; se mitiga fijando ahora las enumeraciones y las claves foráneas. Riesgo de sobre-modelar: no se agregan campos no documentados salvo los dos de soporte justificados.

### Fuera de alcance del sprint
Lógica de puntuación, endpoints, autenticación, carga de archivos y cualquier cálculo de saldos.

---

## 13. Sprint 3 — Autenticación, roles y permisos

**Fechas:** 15/08/2026 – 21/08/2026

### Objetivo
Que un usuario real pueda iniciar sesión con Google, recibir un JWT y que el sistema aplique correctamente las restricciones por rol, incluida la distinción entre usuario verificado y no verificado.

### Funcionalidades
Login con Google OAuth, cierre de sesión, consulta del perfil propio, asignación de roles y matriz de permisos aplicada.

### Backend
`auth_app`: intercambio del token de Google por JWT propio (access + refresh), creación o recuperación del usuario a partir de `google_oauth_id`, rol por defecto `citizen`. Permisos DRF reutilizables en `core.permissions`: `IsAuthenticated`, `IsVerifiedCitizen`, `IsInstitutionOperator`, `IsAdmin`, `IsOperatorOfReportInstitution`. Comando de gestión para promover un usuario a `operator` (vinculándolo a una institución) o a `admin`.

### Frontend
Botón “Ingresar con Google”, manejo de sesión y almacenamiento seguro del token, renovación automática con el refresh token, guardas de ruta por rol para los grupos `(citizen)`, `(operator)` y `(admin)`, y cabecera con el estado de la sesión y el estado de verificación.

### Base de datos
Sin tablas nuevas. Se completan `users.role`, `users.verification_status`, `users.google_oauth_id` y se crean registros en `institution_operators` para los operadores de prueba.

### API
`POST /api/v1/auth/google/` · `POST /api/v1/auth/refresh/` · `GET /api/v1/auth/me/`.

### Seguridad y permisos
Validación del token de Google contra el emisor y el `audience`. JWT de vida corta con refresh. Matriz de permisos aplicada por defecto en modo denegar: toda vista nueva debe declarar explícitamente su permiso. Se comprueba desde ahora que un `citizen` no verificado no puede alcanzar rutas de creación, y que un `operator` no puede acceder a rutas de `admin`.

### Pruebas
Unitarias del flujo de intercambio de token con el proveedor mockeado. Integración: acceso permitido y denegado para cada combinación rol × endpoint disponible. Seguridad: token expirado, token de otro `audience`, ausencia de token, escalamiento de rol por payload manipulado.

### Dependencias
Sprints 1 y 2.

### Criterios de aceptación
- Un usuario nuevo inicia sesión con Google y queda creado con rol `citizen` y verificación `PENDIENTE`.
- `GET /auth/me/` devuelve rol, estado de verificación e institución cuando corresponde.
- Las pruebas de la matriz de permisos cubren los tres roles y pasan.
- El frontend redirige correctamente según rol y estado de verificación.

### Resultado demostrable
Inicio de sesión real con una cuenta de Google, visualización del perfil y bloqueo visible de una ruta restringida.

### Documentación
`ADR-0005-autenticacion-oauth-jwt.md`. Sección de autenticación en la documentación de API. Matriz rol × permiso inicial.

### Riesgos
Configuración de credenciales OAuth y URIs de redirección entre entornos local y desplegado. Gestión del token en el cliente sin exponerlo a XSS.

### Fuera de alcance del sprint
Verificación de identidad con cédula y selfie (Sprint 4), creación de reportes, y cualquier lógica de puntos.

---

## 14. Sprint 4 — Verificación de identidad y privacidad

**Fechas:** 22/08/2026 – 28/08/2026

### Objetivo
Completar el ciclo de identidad verificada-privada: el ciudadano envía cédula y selfie, un administrador revisa manualmente y aprueba o rechaza, y el ciudadano recibe un correo con el resultado. Además, el ciudadano configura cómo desea aparecer en el ranking.

### Funcionalidades
Envío de verificación, consulta del estado propio, bandeja administrativa de revisión, aprobación/rechazo con motivo, notificación por email y preferencias de privacidad del ranking.

### Backend
`auth_app`: cálculo del `document_hmac` con HMAC-SHA-256 y clave secreta del servidor; la cédula en claro **nunca** se persiste ni se registra en logs. Subida de la selfie a un bucket **privado** de Supabase con URL firmada de vida corta accesible solo a administradores. Transiciones de `verification_status`. Endpoint de preferencia de visibilidad con validación de alias (obligatorio y único-por-usuario si el modo es `ALIAS`).

`notifications_app` **mínima**: modelo `notifications`, servicio de envío por email y tarea Celery de despacho, con dos plantillas (verificación aprobada / rechazada). Las plantillas restantes y los reintentos se completan en los Sprints 12 y 13.

### Frontend
Formulario de verificación con carga de imagen y validación de tipo/tamaño en cliente; pantalla de estado (“pendiente de revisión”, “aprobado”, “rechazado con motivo”); bandeja de revisión para administradores con visor de la selfie y botones de aprobar/rechazar; sección de perfil con las cuatro opciones de visibilidad del ranking y vista previa de cómo se verá el nombre.

### Base de datos
Se completan `citizen_profiles.document_hmac`, `verification_selfie_url`, `verified_at`, `verified_by`, `ranking_visibility` y `ranking_alias`. Primeras filas reales en `notifications` y `audit_logs`.

### API
`POST /api/v1/identity/submit/` · `GET /api/v1/identity/status/` · `GET /api/v1/admin/identity/` · `PATCH /api/v1/admin/identity/{id}/review/` · `PATCH /api/v1/profile/ranking-visibility/`.

### Seguridad y permisos
Solo el propio usuario envía su verificación; solo `admin` revisa. El bucket de selfies es privado y sin acceso anónimo. Validación de archivos por extensión, MIME real y tamaño máximo. El `document_hmac` nunca se expone en ninguna respuesta. Toda aprobación o rechazo se registra en `audit_logs` con actor y motivo. Rechazo de cédulas duplicadas por conflicto de índice único, con mensaje que no revela a quién pertenece.

### Pruebas
Unitarias del cálculo del HMAC (mismo insumo → mismo hash; clave distinta → hash distinto; irreversibilidad). Integración del ciclo completo enviar → revisar → aprobar → estado `APROBADO`. Seguridad: usuario intentando revisar identidades ajenas, acceso anónimo a la URL de la selfie, carga de archivo no imagen o excedido de tamaño, intento de registrar una cédula ya verificada. Verificación de que un ciudadano `APROBADO` habilita `IsVerifiedCitizen` y uno `PENDIENTE` no.

### Dependencias
Sprint 3 (identidad autenticada y roles).

### Criterios de aceptación
- La cédula no aparece en texto plano en base de datos, logs ni respuestas.
- Un ciudadano aprobado pasa a `verification_status = APROBADO` con `verified_at` y `verified_by`.
- El correo de resultado se envía y queda registrado en `notifications`.
- Las cuatro modalidades de visibilidad se guardan y se recuperan correctamente.
- La selfie no es accesible sin firma administrativa.

### Resultado demostrable
Flujo completo en pantalla: un ciudadano envía su verificación, un administrador la aprueba desde su bandeja, el ciudadano recibe el correo y ve su cuenta verificada.

### Documentación
`ADR-0006-identidad-hmac-y-almacenamiento-privado.md`. `ADR-0011-privacidad-configurable-del-ranking.md`. Actualización de la documentación de API y del apartado de privacidad.

### Riesgos
Manejo del secreto HMAC entre entornos: si cambia, todos los hashes existentes dejan de coincidir (mitigación: documentarlo como secreto crítico e inmutable). Peso de las imágenes y límites de subida. Entregabilidad del correo en el plan gratuito del proveedor.

### Fuera de alcance del sprint
Validación contra padrón oficial (mejora futura), notificaciones de reportes o recompensas, plantillas HTML elaboradas y reintentos automáticos de envío.

---

## 15. Sprint 5 — Mapa público y geolocalización

**Fechas:** 29/08/2026 – 04/09/2026

### Objetivo
Publicar el mapa de transparencia accesible sin autenticación, con los filtros Activos / Resueltos / Todos, y resolver la selección de ubicación (GPS o manual) que consumirá el Sprint 6.

### Funcionalidades
Mapa público con marcadores por estado y categoría, filtros de estado, filtro por categoría, consulta por área visible (bbox), detalle público del reporte y selector de ubicación reutilizable.

### Backend
`map_app`: endpoint público de listado con filtros `state_filter` ∈ {`active`, `resolved`, `all`} (por defecto **`active`**), `bbox`, `category` y paginación. Consulta geoespacial con índice GIST. Serializador **público** que excluye por completo los datos personales del autor. Caché en Redis con TTL corto e invalidación por evento de cambio de estado.

Mapeo de estados a filtros:
- **Activos:** `CREADO`, `CLASIFICADO`, `ASIGNADO`, `EN_PROCESO`.
- **Resueltos:** `RESUELTO`, `CERRADO`.
- `RECHAZADO` no se publica en el mapa.

### Frontend
Página pública `/mapa` con Leaflet y OpenStreetMap; agrupación de marcadores; leyenda por estado; panel de filtros con “Activos” preseleccionado; ficha lateral del reporte; página `/reporte/[id]` pública. Componente `LocationPicker` reutilizable con geolocalización del navegador, arrastre del marcador y búsqueda manual, incluido el manejo del permiso denegado de GPS.

### Base de datos
Sin tablas nuevas. Se ejercita el índice GIST y se amplía el seed con reportes de ejemplo distribuidos en el Gran Asunción para poder demostrar el mapa antes de que exista la creación de reportes.

### API
`GET /api/v1/public/reports/?bbox&category&state_filter&page` · `GET /api/v1/public/reports/{id}/`.

### Seguridad y permisos
Endpoints anónimos de solo lectura. Nunca se expone `citizen_id`, correo, nombre ni ningún dato del autor. Límite máximo de resultados por página y validación del `bbox` para evitar consultas que barran toda la tabla. Rate limiting básico sobre las rutas públicas.

### Pruebas
Unitarias del filtro por estado (los tres valores) y del filtrado por bbox. Integración: un usuario anónimo obtiene resultados; un reporte `RECHAZADO` no aparece. Seguridad: la respuesta pública no contiene campos personales. Rendimiento: la consulta por bbox usa el índice GIST (revisión del plan de ejecución).

### Dependencias
Sprint 2 (modelo `reports` e índice espacial). No depende del Sprint 6: se demuestra con datos semilla.

### Criterios de aceptación
- Sin iniciar sesión se ve el mapa con los reportes semilla.
- El filtro por defecto muestra solo activos y los tres filtros funcionan.
- La respuesta pública no incluye ningún dato personal.
- El selector de ubicación devuelve coordenadas válidas por GPS y por selección manual.

### Resultado demostrable
Mapa público desplegado, navegable en escritorio y móvil, con los tres filtros y el detalle de un reporte.

### Documentación
Contrato del endpoint público. Nota de diseño sobre el mapeo estados → filtros. Actualización del wireframe del mapa.

### Riesgos
Rendimiento con muchos marcadores (mitigación: clustering y bbox). Diferencias de precisión del GPS en móviles. Políticas de uso de los *tiles* de OpenStreetMap.

### Fuera de alcance del sprint
Creación de reportes, confirmaciones, métricas del dashboard, filtros por prioridad o institución y capas adicionales del mapa.

---

## 16. Sprint 6 — Creación de reportes y duplicados

**Fechas:** 05/09/2026 – 11/09/2026

### Objetivo
Habilitar la creación de reportes por ciudadanos verificados con fotografía obligatoria y ubicación, aplicando los controles antiabuso del MVP y generando el primer evento del ledger: **+20 puntos pendientes**.

### Funcionalidades
Formulario de creación, carga de foto obligatoria, ubicación por GPS o manual, detección de imagen exactamente idéntica, sugerencia de posibles duplicados cercanos y acreditación pendiente de 20 puntos.

### Backend
`reports_app.services.reports.create_report()` como única vía de creación: valida ciudadano verificado, persiste el reporte en estado `CREADO` con `source='platform'`, sube la foto a Supabase, calcula y almacena el `file_hash` SHA-256, registra en `audit_logs`, encola la clasificación IA (que llegará en el Sprint 8; por ahora la tarea queda declarada y sin efecto) e invoca `rewards_app.services.ledger.on_report_created()` dentro de la misma transacción.

Detección de duplicados (`selectors.find_possible_duplicates`): `ST_DWithin` sobre `reports.location` con **radio 100 m**, **misma categoría** y **solo estados activos**, parametrizado por `DUPLICATE_RADIUS_M` y `DUPLICATE_SAME_CATEGORY`. Se expone como consulta previa, **no bloquea** la creación.

Detección de imagen idéntica: si el `file_hash` ya existe en `report_media`, se rechaza la carga indicando que esa fotografía ya fue utilizada.

### Frontend
Asistente de creación en pasos: categoría → descripción → foto → ubicación → confirmación. Tras fijar categoría y ubicación se consulta la API de duplicados y, si hay candidatos, se muestra el aviso **“Ya existe un reporte similar”** con dos acciones: *ver el reporte existente* (donde en el Sprint 7 podrá confirmarlo) o *no es el mismo problema, continuar*. Pantalla de éxito indicando que se otorgaron 20 puntos pendientes y que se acreditarán cuando la institución valide el reporte.

### Base de datos
Primeras filas en `reports`, `report_media` y `reward_transactions`. Se ejercita el índice GIST para la consulta de proximidad.

### API
`POST /api/v1/reports/` (multipart) · `GET /api/v1/reports/possible-duplicates/?lat&lng&category_id` · `GET /api/v1/reports/mine/`.

### Seguridad y permisos
Solo `IsVerifiedCitizen` crea reportes; un usuario no verificado recibe 403 con mensaje explicativo. Validación de archivos por extensión, MIME real, tamaño máximo y unicidad de hash. Sanitización de la descripción para evitar HTML/script. Límite de creación por usuario y ventana temporal como control básico de abuso. El `citizen_id` se toma siempre del token, nunca del cuerpo de la petición.

### Pruebas
Unitarias: cálculo del hash de archivo; `create_report` genera exactamente una transacción `REPORT_CREATED` en estado `PENDIENTE` con `impact_delta = 20` y `credit_delta = 20`; prueba de que esa fila no aporta ni puntos de impacto ni créditos disponibles mientras siga pendiente; la detección de duplicados respeta radio, categoría y estados activos. Integración: creación completa con archivo real; rechazo de foto duplicada; el aviso de duplicado no impide crear. Seguridad: creación con usuario no verificado, archivo inválido, archivo sobredimensionado, descripción con `<script>`. E2E: recorrido del asistente completo.

### Dependencias
Sprints 2, 3, 4 y 5 (modelo, autenticación, verificación y selector de ubicación).

### Criterios de aceptación
- Un ciudadano verificado crea un reporte con foto y ubicación y este aparece en el mapa.
- Un ciudadano no verificado no puede crear y recibe un mensaje claro.
- Subir una foto ya usada se rechaza por hash idéntico.
- Ante un reporte cercano de la misma categoría se muestra el aviso, y el ciudadano puede continuar igualmente.
- Cada creación produce una única transacción `PENDIENTE` con `impact_delta = 20` y `credit_delta = 20`, que no suma a ningún saldo mientras siga pendiente.

### Resultado demostrable
Creación de un reporte de punta a punta, aparición inmediata en el mapa público y visualización de los 20 puntos pendientes en el panel del ciudadano.

### Documentación
`ADR-0007-almacenamiento-de-fotografias.md`. `ADR-0012-deteccion-de-duplicados-por-proximidad.md` con el criterio 100 m / misma categoría / activos y su justificación. Actualización de la documentación de API y del diccionario de datos.

### Riesgos
Tamaño y orientación EXIF de las fotos desde móvil. Latencia de subida a Supabase dentro del ciclo de la petición (mitigación: límite de tamaño y compresión en cliente). Falsos positivos de duplicado en zonas densas como el Mercado 4, aceptables porque el aviso no bloquea.

### Fuera de alcance del sprint
Confirmación ciudadana (Sprint 7), clasificación IA real (Sprint 8), validación institucional, acreditación de puntos, similitud perceptual de imágenes y GPS obligatorio.

---

## 17. Sprint 7 — Panel ciudadano y confirmaciones

**Fechas:** 12/09/2026 – 18/09/2026

### Objetivo
Cerrar el ciclo de participación ciudadana: el ciudadano sigue sus reportes y puede confirmar reportes activos de otras personas, generando **+10 puntos pendientes** bajo reglas estrictas.

### Funcionalidades
Panel ciudadano con listado y seguimiento de reportes propios, resumen de puntos pendientes, detalle de reporte con botón de confirmación y contador de confirmaciones.

### Backend
`reports_app.services.confirmations.confirm_report()` con las cuatro validaciones obligatorias, en este orden: ciudadano verificado → el reporte está en un estado **activo** → el solicitante **no** es el autor → no existe confirmación previa de ese usuario para ese reporte. Superadas todas, crea `report_confirmations` e invoca `rewards_app.services.ledger.on_confirmation_created()` —que inserta la fila con `impact_delta = 10`, `credit_delta = 10` y estado `PENDIENTE`, valores que ya no volverán a modificarse— en la misma transacción y registra en `audit_logs`. Cada violación devuelve un código de error distinguible para que el frontend explique el motivo.

La regla de **autoconfirmación se implementa aquí y solo aquí**: se compara el `citizen_id` del solicitante con `report.citizen_id`. No existe restricción equivalente en base de datos, porque una `CHECK` de PostgreSQL no puede consultar otra tabla. La restricción `UNIQUE(report_id, citizen_id)` sigue cubriendo el caso distinto de la confirmación duplicada, incluso ante peticiones concurrentes.

Endpoint de resumen del ciudadano: reportes propios por estado, confirmaciones realizadas y total de puntos pendientes.

### Frontend
Ruta `(citizen)/panel` con tarjetas de estado, línea de tiempo del reporte propio y bloque “Tus puntos” separando pendientes de acreditados (los acreditados aún serán cero). En el detalle público de un reporte, botón **“Confirmo que el problema sigue ahí”**, visible solo para ciudadanos verificados distintos del autor y solo en reportes activos; deshabilitado con explicación en los demás casos. Contador de confirmaciones visible para todos.

### Base de datos
Primeras filas en `report_confirmations` y transacciones `CONFIRMATION_CREATED` en el ledger. Se ejercita la restricción única `(report_id, citizen_id)`, que actúa como última defensa ante confirmaciones duplicadas. La regla de autoconfirmación **no** tiene respaldo en base de datos: se aplica exclusivamente en el servicio de dominio.

### API
`POST /api/v1/reports/{id}/confirm/` · `GET /api/v1/reports/{id}/` (detalle con número de confirmaciones y si el usuario actual ya confirmó) · `GET /api/v1/citizen/summary/`.

### Seguridad y permisos
`IsVerifiedCitizen` obligatorio. Las cuatro reglas se validan **en el servicio de dominio**, no solo en la interfaz: ocultar el botón no es un control de seguridad. La restricción única de base de datos actúa como última defensa ante peticiones concurrentes. Toda confirmación queda auditada.

### Pruebas
Unitarias de las cuatro reglas, cada una de forma aislada. Integración: confirmar un reporte válido genera exactamente una fila y una transacción `CONFIRMATION_CREATED` en estado `PENDIENTE` con `impact_delta = 10` y `credit_delta = 10`, que no aporta a ningún saldo mientras siga pendiente; dos peticiones simultáneas del mismo usuario producen una sola confirmación. Seguridad (Anexo H.5): confirmar sin verificar, confirmar el propio reporte, confirmar dos veces, confirmar un reporte `RESUELTO`/`CERRADO`/`RECHAZADO`. E2E: dos usuarios, uno crea y el otro confirma.

### Dependencias
Sprint 6 (existencia de reportes) y Sprint 4 (ciudadanos verificados).

### Criterios de aceptación
- Un ciudadano verificado confirma el reporte de otro y obtiene 10 puntos pendientes.
- Los cuatro bloqueos funcionan y devuelven mensajes distinguibles.
- El detalle del reporte muestra el número de confirmaciones.
- El panel ciudadano muestra correctamente los puntos pendientes acumulados.

### Resultado demostrable
Demostración con dos cuentas: una crea el reporte, la otra lo confirma; el panel de cada una refleja 20 y 10 puntos pendientes respectivamente, y los cuatro intentos inválidos se rechazan en vivo.

### Documentación
Documentación de los códigos de error de confirmación. Actualización de CU-14 y de la matriz de permisos.

### Riesgos
Condición de carrera en confirmaciones concurrentes (mitigada por la restricción única). Confusión del usuario entre puntos pendientes y acreditados (mitigada con textos explícitos en la interfaz).

### Fuera de alcance del sprint
Acreditación de puntos (Sprint 9), validación institucional, créditos, ranking, proximidad GPS para confirmar y cualquier recompensa.

---

## 18. Sprint 8 — Clasificación asistida por IA

**Fechas:** 19/09/2026 – 25/09/2026

### Objetivo
Integrar Google Gemini para sugerir categoría, institución y prioridad, detectar inconsistencias y apoyar la identificación de duplicados, dejando siempre la decisión final en manos humanas y todo el proceso auditado.

### Funcionalidades
Clasificación asíncrona del reporte tras su creación, persistencia del resultado con nivel de confianza, marca de inconsistencia, transición `CREADO → CLASIFICADO` y marcado de casos dudosos para revisión humana.

### Backend
`ai_client_app`: cliente de Gemini con tiempo de espera, reintentos con retroceso exponencial y modelo configurable por variable de entorno. Prompt base del Anexo H.3, que exige devolver **solo JSON válido** con `categoria_final`, `institucion_final`, `prioridad`, `hubo_cambio`, `inconsistencia`, `justificacion` y `confianza`. Parser estricto con esquema; si la respuesta no valida, se marca el reporte para revisión humana y **nunca** se rechaza el reporte.

Tarea Celery `classify_report(report_id)` encolada por `create_report`: persiste `ai_classifications`, aplica la sugerencia cuando la confianza supera el umbral configurado y no hay inconsistencia, y en caso contrario deja los valores del ciudadano y marca `inconsistency_flag=True` para que el operador decida. Registro en `audit_logs` con `actor_type='ai'` incluyendo prompt utilizado, respuesta cruda y decisión aplicada.

Modo **mock** activable por configuración para desarrollo, pruebas y CI, sin consumir la API.

### Contrato cerrado (S8-A)
El contrato completo está versionado en [AI_CLASSIFICATION_CONTRACT.md](AI_CLASSIFICATION_CONTRACT.md) (v1, cerrado el 29/08/2026) y es normativo para el resto del sprint. Sus puntos vinculantes:

- **Salida por slugs del catálogo cerrado**, no por nombres libres: `category_slug`, `institution_slug`, `priority`, `confidence`, `inconsistency` y `justification`. Un esquema con campos desconocidos se rechaza. La IA **no crea** categorías ni instituciones: un valor fuera del catálogo deja el campo en `NULL` y marca revisión humana.
- **`AI_CONFIDENCE_THRESHOLD = 0.75`.** La sugerencia solo es elegible para aplicación automática asistida si la confianza alcanza el umbral, no hay inconsistencia y todos los valores mapean. Es una métrica por caso y no debe confundirse con el 85 % de concordancia agregada de RNF-05.
- **`CLASIFICADO` significa «hay un resultado de IA persistido y auditable»**, se aplique o no la sugerencia. Baja confianza e inconsistencia son desenlaces legítimos, no fallos. Si el proveedor no responde tras los reintentos, o falla el broker, el reporte permanece `CREADO`.
- **`hubo_cambio` lo deriva el backend** comparando la sugerencia con el estado del reporte; no se pide al modelo ni se persiste. Es la única desviación respecto del Anexo H.3, junto con el uso de slugs.
- **Prompt versionado** en `backend/apps/ai_client_app/prompts/classification_v1.txt` como `s8-classification-v1`. La auditoría guarda `prompt_version`, `model_name`, `raw_response` y la decisión aplicada, **no** una copia del prompt renderizado: replicarlo duplicaría la descripción del ciudadano en `audit_logs` en cada invocación y cada reproceso.
- **`raw_response` va al payload de `audit_logs`, no a una columna de `ai_classifications`**, con un máximo de 8.000 caracteres. La migración futura de `ai_classifications` añade exactamente `justification` y `model_name`, sin unicidad por reporte para no impedir el reproceso.
- **Reintentos acotados**: `AI_MAX_RETRIES = 3` con retroceso exponencial y *jitter* solo para errores transitorios; los no transitorios no se reintentan. `AI_REQUEST_TIMEOUT_SECONDS = 20`.
- **Idempotencia obligatoria** de la clasificación inicial: `select_for_update` sobre el reporte y guarda de estado, de modo que una reentrega de Celery no aplique la sugerencia dos veces ni duplique historial o auditoría.

### Frontend
En el detalle del reporte visible para operadores y administradores: bloque “Clasificación asistida por IA” con la sugerencia, la confianza, la justificación y, si corresponde, el aviso destacado **“Requiere revisión humana”**. El ciudadano no ve el detalle interno de la clasificación.

**Diferido al Sprint 9 (S8-A).** Ese bloque vive dentro del detalle de reporte para operadores, que es entregable del Sprint 9 junto con la bandeja institucional. El Sprint 8 entrega backend, persistencia, auditoría, API y evaluación, y no adelanta pantallas de operador; mientras tanto, el resultado se inspecciona por el admin de Django. **No cuenta como deuda de S8.**

### Base de datos
Filas en `ai_classifications` y entradas de IA en `audit_logs`. Reportes que avanzan a `CLASIFICADO` con su historial correspondiente.

### API
`POST /api/v1/ai/reports/{id}/classify/` (reproceso manual, sistema/admin) · `GET /api/v1/reports/{id}/ai-classification/` (operador/admin).

### Seguridad y permisos
A la API externa se envían **únicamente** descripción, categoría, institución elegida e imagen: ningún dato identificatorio del ciudadano. La clave de Gemini vive solo en variables de entorno del backend. Solo operadores y administradores consultan el detalle de la clasificación. La IA no puede cambiar el estado a `RECHAZADO` bajo ninguna circunstancia.

### Pruebas
Unitarias del parser con respuestas válidas, JSON malformado, campos faltantes y valores fuera de dominio. Unitarias de la regla de umbral de confianza y del marcado de inconsistencia. Integración con el cliente mockeado sobre el flujo completo de creación. Concordancia ≥ 85 % contra las etiquetas de oro del dataset sintético. Prueba explícita de que un resultado de IA nunca produce un rechazo automático. Prueba de que un fallo de la API externa no impide crear el reporte ni pierde los 20 puntos pendientes. **El proveedor real nunca se invoca desde la CI**: las pruebas usan el cliente falso, y la concordancia contra Gemini se mide en un gate separado, fuera de la suite.

### Dependencias
Sprint 6 (reportes con foto y descripción) y Sprint 2 (`ai_classifications`).

### Criterios de aceptación
- Al crear un reporte se encola y ejecuta la clasificación sin bloquear la respuesta HTTP.
- El resultado queda persistido con confianza y justificación, y auditado.
- Un caso inconsistente se marca para revisión humana y conserva los datos del ciudadano.
- La concordancia sobre el dataset sintético alcanza al menos el 85 %, **medida contra el proveedor real** en un gate propio antes del cierre del sprint. Alcanzar ese valor con el cliente falso valida el medidor, no el modelo.
- Con la API caída, la creación de reportes sigue funcionando.

### Resultado demostrable
Creación de un reporte mal categorizado a propósito y visualización de cómo la IA sugiere la corrección, y de un caso incoherente que queda marcado para revisión humana.

### Documentación
[ADR-0008](adr/ADR-0008-integracion-gemini-desacoplada.md) y [ADR-0009](adr/ADR-0009-clasificacion-asincrona-celery.md), ambos escritos en S8-A. Contrato en [AI_CLASSIFICATION_CONTRACT.md](AI_CLASSIFICATION_CONTRACT.md) y prompt versionado en `backend/apps/ai_client_app/prompts/classification_v1.txt`. Registro del modelo y los umbrales utilizados para el informe de resultados.

### Riesgos
Costo y cuotas de la API (mitigación: dataset de 20 reportes, modo mock en CI y caché de resultados). Variabilidad de la salida del LLM (mitigación: parser estricto y `temperature` baja). Latencia del worker.

### Fuera de alcance del sprint
Validación institucional y acreditación de puntos (Sprint 9), rechazo automático de reportes (prohibido por diseño), detección perceptual de imágenes y reentrenamiento o *fine-tuning* de modelos.

---

## 19. Sprint 9 — Panel institucional y validación

**Fechas:** 26/09/2026 – 02/10/2026

### Objetivo
Entregar el panel institucional y, con él, la pieza central del sistema de incentivos: la **validación institucional**, que acredita los puntos pendientes del autor y de todos los confirmadores, agrega el bonus de 30 y emite los créditos 1:1 de la institución validante.

### Funcionalidades
Bandeja de reportes de la institución con filtros, detalle del caso con la sugerencia de IA, validación de la incidencia como legítima, rechazo con motivo tipificado y anulación de los puntos pendientes asociados, y visualización de puntos acreditados por el ciudadano.

### Backend
`reports_app.services.validation.validate_report(report, operator, institution)`, ejecutado en una única transacción atómica:
1. Verifica que el operador pertenece a la institución responsable y que el reporte no está ya validado.
2. Fija `validated_at`, `validated_by` e `institution_id` definitiva y avanza el reporte a `ASIGNADO`.
3. Llama a `rewards_app.services.ledger.on_report_validated()`, que:
   - acredita el `REPORT_CREATED` (+20) del autor;
   - acredita **todos** los `CONFIRMATION_CREATED` (+10) pendientes del reporte;
   - inserta `VALIDATION_BONUS` (+30 / +30) para el autor;
   - fija en cada fila acreditada **solo** `status`, `institution_id` y `credited_at`. **No modifica `impact_delta` ni `credit_delta`**: esos valores nacieron con la transacción (20/20 y 10/10) y la conversión 1:1 quedó determinada desde el origen. La acreditación cambia la validez de la fila, nunca su importe.
4. Registra el movimiento completo en `audit_logs` y encola las notificaciones.

Rechazo: `reject_report()` pasa el reporte a `RECHAZADO` con motivo tipificado, **no acredita nada** e invoca `rewards_app.services.ledger.on_report_rejected()`, que pasa a **`ANULADO`** todas las transacciones `PENDIENTE` del reporte —la del autor y las de todos los confirmadores— fijando `voided_at`. Ninguna transacción puede quedar pendiente de forma indefinida: un reporte rechazado cierra el ciclo de sus puntos. No se generan filas compensatorias, porque esos puntos nunca integraron ningún saldo. La anulación se audita junto con el motivo del rechazo y se notifica al ciudadano.

**Regla confirmada:** los reportes de origen `telegram` o `x_mock` sin ciudadano verificado asociado no generan puntos para su fuente, pero sí acreditan los +10 de los ciudadanos verificados que los confirmaron.

### Frontend
Ruta `(operator)/bandeja` con filtros por estado, prioridad, categoría y rango de fechas, y orden por prioridad y antigüedad. Detalle del caso con foto, ubicación en mapa, descripción, confirmaciones acumuladas y bloque de IA. Acciones **Validar** (con selección/confirmación de la institución responsable) y **Rechazar** (con motivo obligatorio). Confirmación previa que advierte del efecto en puntos y créditos, indicando en el rechazo que los puntos pendientes del autor y de los confirmadores quedarán anulados. En el panel del ciudadano, los puntos migran visiblemente de pendientes a acreditados, aparece el saldo de créditos por institución y los puntos anulados se muestran como tales, con el motivo del rechazo.

### Base de datos
Actualización de `reports.validated_at`, `validated_by`, `institution_id`; transiciones en `report_status_history`; transacciones acreditadas y bonus en `reward_transactions`, y primeras filas en estado `ANULADO` con `voided_at` al rechazar.

### API
`GET /api/v1/operator/reports/` · `GET /api/v1/operator/reports/{id}/` · `POST /api/v1/operator/reports/{id}/validate/` · `POST /api/v1/operator/reports/{id}/reject/`.

### Seguridad y permisos
`IsInstitutionOperator` combinado con `IsOperatorOfReportInstitution`: un operador de ANDE no puede ver ni validar reportes de ESSAP. La institución acreditada es siempre la que efectivamente validó, tomada del servidor y nunca del cuerpo de la petición. Doble validación del mismo reporte bloqueada por idempotencia. Toda validación y todo rechazo quedan auditados con actor y motivo.

### Pruebas
Unitarias de `on_report_validated` cubriendo los escenarios S-01, S-02, S-03 y S-10 del Anexo H.2: autor con 50 puntos y 50 créditos tras validar; confirmador con sus 10 acreditados; reporte de Telegram que no acredita a su fuente pero sí al confirmador. Unitarias del rechazo (escenario adicional **S-11**): no acredita nada, deja en `ANULADO` la transacción del autor y las de todos los confirmadores, y ninguna transacción del reporte queda en `PENDIENTE` después de rechazar. Prueba de que los puntos anulados no computan en ningún saldo ni en el ranking. **Prueba de inmutabilidad:** tras acreditar, los `impact_delta` y `credit_delta` de cada fila son idénticos a los que tenían al crearse; la validación solo modificó `status`, `institution_id` y `credited_at`. Integración: la acreditación es atómica —si falla el ledger, la validación se revierte por completo. Seguridad: operador de otra institución intentando validar; validación repetida; validación por un ciudadano. Consistencia: la suma del ledger coincide con los saldos expuestos por la API.

### Dependencias
Sprints 6 y 7 (reportes y confirmaciones pendientes), Sprint 3 (roles de operador) y Sprint 8 (sugerencia de IA, deseable pero no bloqueante).

### Criterios de aceptación
- Validar un reporte deja al autor con exactamente 50 puntos acreditados y 50 créditos de la institución validante, sin que ningún importe del ledger haya sido reescrito.
- Cada confirmador pasa de 10 pendientes a 10 acreditados y 10 créditos.
- Un reporte rechazado no genera ningún punto acreditado y deja todas sus transacciones en `ANULADO`, ninguna en `PENDIENTE`.
- Un operador no puede operar sobre reportes de otra institución.
- La operación completa es atómica y queda auditada.

### Resultado demostrable
Demostración encadenada: reporte creado (20 pendientes) + confirmación (10 pendientes) → el operador valida → ambos ciudadanos ven sus puntos acreditados y sus créditos institucionales, con el detalle del ledger visible en el admin.

### Documentación
`ADR-0010-creditos-por-institucion-y-conversion-1-1.md`. Actualización de CU-15 y de la documentación de API. Registro de la regla de acreditación en el diccionario de datos.

### Riesgos
Es el sprint con mayor densidad de reglas de negocio; se aborda con TDD estricto. Riesgo de doble acreditación por peticiones concurrentes (mitigación: bloqueo de fila sobre el reporte durante la transacción). Riesgo de acreditar a la institución equivocada si el reporte fue reasignado antes de validar (mitigación: la institución acreditada es la vigente en el instante de la validación, y queda congelada en cada fila del ledger).

### Fuera de alcance del sprint
Bonus de resolución (+50), reversión de validaciones, evidencias de resolución, ranking, catálogo y canje de recompensas.

---

## 20. Sprint 10 — Estados, evidencias, auditoría y reversión

**Fechas:** 03/10/2026 – 09/10/2026

### Objetivo
Completar el ciclo de vida del reporte —gestión de estados, evidencia de resolución y cierre— con el bonus de **+50 puntos** al resolver, habilitar la **reversión** de validaciones y consolidar la trazabilidad completa exigida por la tesis.

### Funcionalidades
Cambio de estados con transiciones controladas, carga de evidencia de resolución, bonus de resolución, cierre automático a las 72 h, consulta de auditoría por parte del administrador y reversión de validación con sus reversas de puntos y créditos.

### Backend
Máquina de estados explícita en `reports_app.services.state_machine` con las transiciones permitidas:

```text
CREADO      → CLASIFICADO | RECHAZADO
CLASIFICADO → ASIGNADO | RECHAZADO
ASIGNADO    → EN_PROCESO | RECHAZADO
EN_PROCESO  → RESUELTO | RECHAZADO
RESUELTO    → CERRADO            (manual o automático a las 72 h)
CERRADO     → (terminal)
RECHAZADO   → (terminal)
```

**Regla decidida para este proyecto:** la transición a `RESUELTO` exige que el reporte tenga `validated_at`. No existe acreditación implícita: los puntos solo se acreditan por la decisión humana explícita de validar. El intento de resolver un reporte no validado se rechaza con un error específico que indica que primero debe validarse.

Al pasar a `RESUELTO` se exige al menos una evidencia y se invoca `on_report_resolved()`, que inserta `RESOLUTION_BONUS` (+50 impacto / +50 créditos, `ACREDITADO`) para el autor, completando el máximo de 100 puntos por reporte propio. Los confirmadores no reciben bonus de resolución.

`admin_app.services.revert_validation(report, admin, reason)`, **exclusivo del administrador global** y con motivo obligatorio: marca como `REVERTIDO` todas las filas acreditadas del reporte —del autor y de los confirmadores, incluido el bonus de resolución si existiera—, inserta las filas `REVERSAL` compensatorias con `related_transaction_id`, pasa a `ANULADO` cualquier fila que siguiera `PENDIENTE`, limpia `validated_at`/`validated_by`, deja el reporte en revisión y audita todo. **No se aplica ninguna penalización adicional** al ciudadano.

**La reversión siempre puede ejecutarse**, incluso si los créditos derivados ya fueron canjeados (decisión D-01). Como el saldo se deriva del ledger, al salir del conjunto `ACREDITADO` las filas que respaldaban esos créditos y permanecer acreditada la fila `REDEMPTION` de −1.000, la diferencia entre créditos válidos y créditos consumidos puede quedar **negativa**. Es el comportamiento correcto: refleja que se consumieron créditos que dejaron de estar respaldados. En consecuencia:

- No existe ninguna comprobación que bloquee la reversión por la existencia de un canje previo.
- El resultado es la **diferencia entre créditos válidos y créditos consumidos**, sin recargo ni penalización adicional. Su magnitud depende de los créditos vigentes que le queden al ciudadano en esa institución y no equivale necesariamente al importe canjeado: si conserva acreditaciones de otros reportes validados de la misma institución, la diferencia puede seguir siendo positiva.
- El **vale simulado ya emitido permanece emitido y válido**; su código no se anula.
- El ciudadano **no puede volver a canjear** en esa institución hasta que la diferencia vuelva a alcanzar el costo requerido, lo que ocurre al acumular nuevas acreditaciones.
- No se requiere ninguna transacción de ajuste ni compensación manual: la aritmética del ledger produce el resultado correcto por sí sola.

Tarea de Celery Beat para el cierre automático `RESUELTO → CERRADO` tras 72 h configurables. Ampliación de `notifications_app` con los avisos de cambio de estado y de resolución.

### Frontend
En el panel institucional: control de cambio de estado que solo ofrece transiciones válidas, formulario de evidencia con descripción y archivo, y línea de tiempo del caso. En el panel del ciudadano: seguimiento con el estado actual y el bonus recibido. En el panel de administración: consulta de auditoría con filtros y acción de reversión con confirmación explícita que advierte de sus consecuencias.

### Base de datos
`resolution_evidences`, historial completo en `report_status_history`, `audit_logs` poblada en todas las acciones sensibles y filas `RESOLUTION_BONUS` y `REVERSAL` en el ledger.

### API
`PATCH /api/v1/reports/{id}/status/` · `POST /api/v1/reports/{id}/evidence/` · `GET /api/v1/admin/audit-logs/` · `POST /api/v1/admin/reports/{id}/revert-validation/`.

### Seguridad y permisos
Las transiciones se validan en el servidor contra la máquina de estados; ninguna transición arbitraria es posible aunque se manipule la petición. Solo el operador de la institución responsable cambia estados de sus reportes. La reversión es **exclusiva del administrador global**, exige motivo y queda auditada con detalle completo. Solo el administrador consulta la auditoría; un ciudadano u operador recibe 403.

### Pruebas
Unitarias de cada transición válida e inválida. Unitaria del bloqueo de `RESUELTO` sin `validated_at`. Unitaria del escenario S-04: el autor alcanza exactamente 100 puntos y 100 créditos. Unitaria del escenario S-07: la reversión retira la totalidad de los puntos y créditos derivados del reporte, tanto del autor como de los confirmadores, sin restar nada adicional; si esas eran sus únicas acreditaciones, el saldo vuelve a cero. Unitaria del escenario **S-12** (reversión posterior a un canje): la reversión se ejecuta sin bloqueo, el saldo institucional resultante es la diferencia entre créditos válidos y créditos consumidos y puede quedar negativo, el vale emitido conserva su código y su validez, y un nuevo intento de canje en esa institución se rechaza hasta que la diferencia vuelva a alcanzar el costo requerido. Unitaria complementaria: si al ciudadano le quedan acreditaciones vigentes de otros reportes de la misma institución, la diferencia se calcula sobre ellas y puede seguir siendo positiva. Prueba de que una reversión deja también en `ANULADO` cualquier transacción que siguiera pendiente. Integración del cierre automático simulando el paso del tiempo. Seguridad: reversión intentada por un operador o un ciudadano; consulta de auditoría por usuario común; cambio de estado sobre un reporte de otra institución. Auditoría: 100 % de los cambios de estado y movimientos del ledger registrados.

### Dependencias
Sprint 9 (validación y acreditación).

### Criterios de aceptación
- Solo se ejecutan transiciones válidas y todas quedan en el historial.
- Resolver exige evidencia y validación previa, y otorga los +50 al autor.
- El total por reporte propio nunca supera los 100 puntos.
- La reversión retira los puntos y créditos del autor y de los confirmadores, sin penalización, y deja rastro completo.
- La reversión se ejecuta también cuando los créditos ya fueron canjeados; el saldo resultante es la diferencia entre créditos válidos y consumidos, puede quedar negativo, y el vale emitido se conserva.
- El administrador puede reconstruir cualquier movimiento desde `audit_logs` y el ledger.

### Resultado demostrable
Ciclo completo de un reporte hasta `CERRADO` con el ciudadano alcanzando 100 puntos, seguido de una reversión administrativa que devuelve los saldos a cero y muestra el rastro íntegro en la auditoría.

### Documentación
`ADR-0013-politica-de-reversion.md`, incluyendo la regla de saldo negativo y la distinción entre `ANULADO` y `REVERTIDO`. Diagrama de estados actualizado con los eventos de incentivo. Documentación del endpoint de reversión y de la regla “no se resuelve sin validar”.

### Riesgos
Saldo institucional negativo tras revertir créditos ya canjeados: es un resultado esperado y aceptado (D-01), pero debe explicarse con claridad en la interfaz para que el ciudadano no lo interprete como una sanción. Complejidad de mantener consistente el ledger ante reversiones parciales (mitigación: la reversión siempre opera sobre el reporte completo, nunca sobre transacciones sueltas). Cierre automático disparándose en entornos de prueba (mitigación: plazo configurable y desactivable).

### Fuera de alcance del sprint
Ranking, catálogo de recompensas, canjes, dashboards y cualquier penalización o antifraude avanzado.

---

## 21. Sprint 11 — Créditos, ranking del mes en curso y privacidad

**Fechas:** 10/10/2026 – 16/10/2026

### Objetivo
Exponer al ciudadano su impacto: puntos históricos, puntos del mes en curso y créditos por institución, y publicar el **Top 10 del mes en curso** respetando la preferencia de privacidad de cada persona.

### Funcionalidades
Resumen de impacto del ciudadano, saldos de créditos por institución, ranking público del mes en curso y aplicación efectiva de las cuatro modalidades de visibilidad.

### Backend
`rewards_app.selectors`: cálculo por agregación sobre el ledger de puntos históricos, puntos del mes en curso, puntos pendientes y créditos disponibles por institución. Servicio de ranking que agrupa `impact_delta` de filas `ACREDITADO` por usuario dentro del **mes calendario en curso**, ordena de forma descendente y limita a diez, resolviendo empates por fecha de acreditación más antigua.

Resolución de nombre según `ranking_visibility`: `FULL_NAME` → nombre y apellido; `FIRST_INITIAL` → nombre e inicial del apellido; `ALIAS` → alias configurado; `PRIVATE` → literal **`Usuario privado`**, conservando posición y puntaje. La resolución ocurre en el serializador del ranking; los datos personales nunca salen del backend para usuarios privados.

Caché en Redis del ranking con TTL corto e invalidación ante nuevas acreditaciones.

**Alcance del ranking en el MVP.** El sistema publica exclusivamente el Top 10 del mes en curso y el impacto histórico acumulado del ciudadano. No se conservan, materializan ni exponen rankings de meses anteriores. Esto simplifica la lógica de forma sustantiva: como el ranking se recalcula siempre en vivo sobre el estado actual del ledger, una anulación o una reversión retira automáticamente las filas afectadas del conjunto `ACREDITADO` y el resultado se corrige solo. No existe ninguna regla de imputación de reversiones a períodos cerrados, porque no hay períodos cerrados que preservar. El impacto histórico del ciudadano tampoco se borra: simplemente refleja en todo momento la suma de sus transacciones acreditadas vigentes.

### Frontend
Ruta `(citizen)/recompensas` (sección de impacto): tarjetas con impacto histórico, impacto del mes en curso, puntos pendientes y una tarjeta por institución con su saldo de créditos, más el historial de movimientos del ledger en lenguaje claro (“Reporte validado por ANDE · +30 puntos · 30 créditos”), distinguiendo visualmente los movimientos `ANULADO` y `REVERTIDO`. Ruta pública `/ranking` con el Top 10 del mes en curso y resalte de la posición del usuario autenticado, **sin selector de mes**. Enlace directo a la configuración de privacidad.

### Base de datos
Sin tablas nuevas. Se ejercitan los índices `reward_transactions(status, credited_at)` y `(user_id, institution_id, status)`.

### API
`GET /api/v1/rewards/summary/` · `GET /api/v1/rewards/transactions/` · `GET /api/v1/ranking/monthly/` (sin parámetros; devuelve siempre el mes en curso).

### Seguridad y permisos
`GET /rewards/summary/` y `/rewards/transactions/` solo devuelven datos del usuario autenticado; no existe forma de consultar el saldo de otra persona. El ranking es público pero jamás expone correo, identificador ni datos de contacto. Un usuario `PRIVATE` no es reidentificable a partir de la respuesta. Los saldos siempre se calculan desde el ledger; no existe un campo mutable de saldo que pueda manipularse.

### Pruebas
Unitarias del cálculo de saldos con mezcla de transacciones pendientes, acreditadas, anuladas y revertidas. Unitarias de que ni los puntos `PENDIENTE`, ni los `ANULADO`, ni los `REVERTIDO` participan del ranking ni del impacto histórico. Unitaria de las cuatro modalidades de visibilidad, verificando que `PRIVATE` conserva posición y puntaje. Unitaria del corte mensual con el dataset de ventana de 60 días: solo las acreditaciones del mes en curso aparecen en el Top 10. Unitaria de que una reversión ejecutada sobre puntos acreditados en el mes en curso los retira del ranking en el siguiente cálculo. Unitaria de que el saldo institucional puede ser negativo y se expone como tal. Integración: tras una validación, el ranking refleja el cambio una vez expirada la caché. Consistencia: el saldo expuesto coincide siempre con la suma del ledger.

### Dependencias
Sprints 9 y 10 (transacciones acreditadas, anuladas y revertidas), Sprint 4 (preferencia de visibilidad).

### Criterios de aceptación
- Los saldos coinciden exactamente con la agregación del ledger en todos los escenarios de prueba.
- El ranking usa solo puntos acreditados dentro del mes en curso.
- Un usuario privado aparece como `Usuario privado` sin perder posición ni puntaje.
- El impacto histórico refleja siempre la suma de las transacciones acreditadas vigentes.
- Un saldo institucional negativo se calcula y se muestra correctamente.

### Resultado demostrable
Panel de impacto de un ciudadano con puntos y créditos por institución, y ranking público del mes en curso mostrando simultáneamente entradas con nombre completo, con inicial, con alias y privadas.

### Documentación
Documentación de la fórmula de cálculo de cada saldo, del alcance del ranking limitado al mes en curso y del tratamiento de saldos negativos. Actualización de CU-16 y de los wireframes de ranking.

### Riesgos
Rendimiento de la agregación al crecer el ledger (mitigación: índices dedicados y caché). Interpretación de zonas horarias en el corte mensual (mitigación: fijar la zona horaria del proyecto y calcular el mes en esa zona). Confusión entre puntos y créditos, y entre movimientos anulados y revertidos (mitigación: separación visual explícita y etiquetas distintas).

### Fuera de alcance del sprint
Catálogo y canje de recompensas (Sprint 12); rankings de meses anteriores, selector de período e histórico de rankings cerrados; insignias, niveles, campañas y cualquier ranking distinto del Top 10 del mes en curso.

---

## 22. Sprint 12 — Catálogo de recompensas y canje simulado

**Fechas:** 17/10/2026 – 23/10/2026

### Objetivo
Cerrar el ciclo de incentivos con el canje: cada institución administra su recompensa simulada y el ciudadano con saldo suficiente obtiene un vale con código único, respetando el límite de un canje por institución y por mes.

### Funcionalidades
Administración de la recompensa institucional por parte del operador, catálogo visible para el ciudadano, canje con descuento de 1.000 créditos, generación de código, historial de canjes y notificación asociada.

### Backend
`rewards_app.services.rewards`: alta, edición y activación/desactivación de la recompensa de la propia institución, con `simulated=True` fijo. **El costo es fijo en 1.000 créditos en el MVP** (decisión D-02): el campo `credit_cost` se conserva en el modelo con `DEFAULT 1000` por extensibilidad futura, pero **no es editable por el operador**. El serializador ignora cualquier `cost` recibido y el servicio impone la constante `MVP_CREDIT_COST = 1000`; el formulario muestra el costo como valor fijo, no como campo de entrada.

`rewards_app.services.redemptions.redeem(user, reward)`. **No existen “filas de saldo” que bloquear**: el saldo es derivado del ledger por agregación, de modo que no hay ninguna fila que represente el saldo y sobre la que aplicar un bloqueo. Para impedir el doble canje concurrente se bloquea la fila del **`User`** como punto de serialización:

```text
with transaction.atomic():
    user = User.objects.select_for_update().get(pk=user_id)   # serializa los canjes de este usuario
    # dentro del bloqueo, y solo dentro:
    #   1. verifica ciudadano verificado y recompensa activa
    #   2. recalcula el saldo institucional agregando el ledger
    #   3. verifica saldo >= MVP_CREDIT_COST
    #   4. verifica que no exista otro canje del mismo usuario e institución en el mes en curso
    #   5. genera el código único de vale
    #   6. inserta reward_redemptions y la transacción REDEMPTION (credit_delta = −1000, ACREDITADO)
    #   7. audita y encola la notificación
```

El punto clave es que el saldo se **recalcula dentro del bloqueo**, nunca antes: dos peticiones simultáneas del mismo usuario se serializan sobre la fila de `User`, de modo que la segunda vuelve a agregar el ledger ya con la transacción `REDEMPTION` de la primera incluida, encuentra saldo insuficiente o el límite mensual consumido, y se rechaza. El bloqueo es por usuario, así que no interfiere con los canjes de otras personas.

El remanente de créditos permanece disponible. Los **puntos de impacto no se descuentan nunca**.

**Saldo negativo.** Si una reversión posterior deja la diferencia entre créditos válidos y créditos consumidos en negativo (ver Sprint 10 y decisión D-01), el vale ya emitido conserva su código y su validez, y el ciudadano no puede volver a canjear en esa institución hasta que esa diferencia vuelva a alcanzar los 1.000 créditos. La verificación del paso 3 produce ese bloqueo de forma natural, sin necesidad de ninguna regla adicional.

Ampliación de `notifications_app`: plantillas de canje realizado y de recompensa disponible, y reintento de envíos fallidos.

### Frontend
Ruta `(operator)/recompensas`: formulario de la recompensa institucional con nombre, descripción y estado activo. El **costo se muestra como valor fijo de 1.000 créditos, no editable**, con una nota que aclara que es una constante del MVP. Listado de canjes emitidos por su institución. Ruta `(citizen)/recompensas`: catálogo por institución mostrando saldo actual, costo y una barra de progreso hacia los 1.000 créditos; botón de canje habilitado solo con saldo suficiente y sin canje previo en el mes, con explicación del motivo cuando está deshabilitado; si el saldo es negativo se indica explícitamente cuántos créditos faltan para volver a canjear y por qué; pantalla de vale con el código y aviso explícito de que **es una simulación sin valor económico real**; historial de canjes, en el que un vale emitido permanece visible y válido aunque el saldo haya quedado negativo por una reversión posterior.

### Base de datos
Filas en `rewards` y `reward_redemptions`, y transacciones `REDEMPTION` en el ledger. Índice único sobre `reward_redemptions.code` e índice sobre `(user_id, reward_id, created_at)` para la verificación del límite mensual.

### API
`GET /api/v1/rewards/catalog/` · `POST /api/v1/rewards/{id}/redeem/` · `GET /api/v1/rewards/redemptions/` · `GET|POST /api/v1/operator/rewards/` · `PATCH /api/v1/operator/rewards/{id}/`.

### Seguridad y permisos
Solo `IsVerifiedCitizen` canjea. Un operador solo administra recompensas de **su** institución; el intento sobre otra devuelve 403. Las tres validaciones del canje (saldo, límite mensual, recompensa activa) se ejecutan en el servidor **dentro del bloqueo `select_for_update()` sobre el `User`**, recalculando el saldo allí mismo, lo que impide el doble canje por peticiones concurrentes. El costo se toma siempre de la constante del servidor y nunca del cuerpo de la petición: un `cost` enviado por el cliente se descarta. El código del vale es aleatorio, no adivinable y único. El texto de simulación es obligatorio en la interfaz y en el correo, por coherencia con el marco legal declarado.

### Pruebas
Unitarias de los escenarios S-08 y S-09 del Anexo H.2: canje válido con 1.000 créditos y generación de código; segundo canje de la misma institución en el mismo mes bloqueado conservando el saldo restante. Unitarias de canje sin saldo suficiente, de canje de recompensa inactiva y de canje en distinta institución dentro del mismo mes (permitido). Prueba de que los puntos de impacto no disminuyen tras canjear. Prueba de que el costo es siempre 1.000 aunque el operador o el cliente envíen otro valor. Prueba de concurrencia con transacciones reales: dos peticiones simultáneas del mismo usuario producen un único canje, verificando que el bloqueo sobre `User` y el recálculo del saldo dentro de la transacción funcionan. Escenario **S-12**: tras una reversión que deja el saldo negativo, el vale emitido sigue siendo válido y un nuevo canje se rechaza hasta recuperar los 1.000 créditos. Seguridad: operador administrando una recompensa ajena, operador intentando modificar el costo, canje por usuario no verificado. E2E del flujo completo de canje.

### Dependencias
Sprint 11 (saldos de créditos por institución) y Sprint 9 (emisión de créditos).

### Criterios de aceptación
- Con 1.000 créditos de una institución el canje se realiza, descuenta el costo y emite un código único.
- El remanente de créditos se conserva y los puntos de impacto no varían.
- Un segundo canje de la misma institución en el mismo mes se bloquea con mensaje claro.
- Un canje de otra institución en el mismo mes sí se permite.
- Cada operador administra exclusivamente la recompensa de su institución y no puede alterar el costo de 1.000 créditos.
- Dos peticiones de canje simultáneas del mismo usuario producen un único canje.
- Con saldo negativo tras una reversión, el vale emitido permanece válido y no se permite un nuevo canje hasta recuperar el saldo.

### Resultado demostrable
Ciudadano con más de 1.000 créditos de ANDE canjeando su vale, viendo el código emitido y el saldo restante, y recibiendo el bloqueo al intentar un segundo canje de ANDE en el mismo mes.

### Documentación
Documentación de las reglas de canje, del formato del código, del costo fijo de 1.000 créditos en el MVP y del comportamiento ante saldo negativo. Actualización de CU-17 y CU-18. Nota explícita sobre el carácter simulado de la recompensa para el marco legal del TCC.

### Riesgos
Doble canje por concurrencia (mitigado con `select_for_update()` sobre `User` y recálculo del saldo dentro de la transacción). Expectativa de un beneficio real por parte de usuarios de prueba (mitigado con avisos explícitos). Saldo negativo tras una reversión: es un resultado aceptado (D-01), pero requiere un mensaje claro en la interfaz para que no se lea como una sanción.

### Fuera de alcance del sprint
Recompensas económicas reales, convenios institucionales, integración con facturación, costos configurables por el operador, catálogos con múltiples recompensas por institución con reglas diferenciadas y campañas temporales.

---

## 23. Sprint 13 — Dashboards, integración e ingesta social

**Fechas:** 24/10/2026 – 30/10/2026

> **Alcance revisado el 20/08/2026.** La integración social del MVP se limita a una **simulación
> interna de publicaciones de X/Twitter**. **Telegram queda fuera del alcance** y **no habrá
> integración real con la API de X**. La eliminación técnica de `TelegramIngestedMessage` se
> realizará al inicio del Sprint 13, mediante migración controlada y respaldo previo. Ver **CT-43**
> en [CAMBIOS_TESIS_PENDIENTES.md](CAMBIOS_TESIS_PENDIENTES.md). El texto restante de esta sección
> conserva la planificación original y no se reescribe.

### Objetivo
Cerrar el MVP funcional con los dashboards institucional y público, dejar el sistema integrado de punta a punta y, **solo si el núcleo obligatorio está estable**, incorporar la ingesta desde Telegram y la simulación de X/Twitter.

### Funcionalidades
Dashboard institucional con métricas y SLA, panel público de transparencia, notificaciones completas y —condicionalmente— bot de Telegram que genera pre-reportes e inyección de mensajes simulados de X.

### Backend
`analytics_app`: agregaciones por período, categoría, estado, prioridad e institución; tiempo medio de resolución contrastado con `institutions.sla_hours`; tasa de validación y de rechazo; volumen de confirmaciones; métricas del módulo de incentivos (puntos acreditados, créditos emitidos y canjes). Precálculo mediante Celery Beat y caché en Redis.

`social_ingestion_app` (condicional): patrón Strategy con dos adaptadores. Telegram recibe mensajes por webhook, los persiste en `telegram_ingested_messages` y genera un reporte con `source='telegram'` y `citizen_id` nulo. X simulado carga mensajes desde el dataset a `x_mock_messages` con `source='x_mock'`. **Ningún reporte de estas fuentes genera puntos para su origen**, pero ambos son confirmables y validables con normalidad.

Cierre de `notifications_app`: todas las plantillas (verificación, reporte creado, validado, resuelto, rechazado, canje), reintentos y registro de estado de envío.

### Frontend
Ruta `(operator)/dashboard` con indicadores, series temporales y distribución por categoría y estado. Panel público de transparencia integrado en el mapa con métricas agregadas por institución. Revisión integral de la navegación, estados vacíos, mensajes de error y responsividad de todo el portal.

### Base de datos
`telegram_ingested_messages` y `x_mock_messages` pobladas si se ejecuta la parte condicional. Sin cambios estructurales.

### API
`GET /api/v1/dashboard/metrics/?period&institution` · `GET /api/v1/public/metrics/` · `POST /api/v1/telegram/webhook/` · `POST /api/v1/x-mock/messages/`.

### Seguridad y permisos
El dashboard institucional filtra por la institución del operador; el administrador ve el consolidado. Las métricas públicas son exclusivamente agregadas y nunca permiten reidentificar a un ciudadano. El webhook de Telegram se autentica mediante *secret token* y valida el origen. La inyección de mensajes de X está restringida a administradores. Los reportes ingestados quedan marcados con su `source` y no acreditan puntos a ninguna cuenta no verificada.

### Pruebas
Unitarias de cada métrica contra un conjunto de datos conocido. Integración: el dashboard de un operador solo agrega reportes de su institución. Escenario S-10: un reporte de Telegram validado no acredita a su fuente pero sí a su confirmador verificado. Seguridad: webhook sin token, inyección de X por usuario no administrador, métricas públicas sin datos personales.

### Dependencias
Sprints 9 a 12 (datos de validación, resolución, créditos y canjes).

### Criterios de aceptación
- El dashboard institucional muestra métricas correctas y acotadas a la institución del operador.
- El panel público expone métricas agregadas sin datos personales.
- Todas las notificaciones del MVP se envían y quedan registradas.
- Si se implementa la ingesta social, los reportes ingestados son confirmables y no acreditan a su fuente.

### Resultado demostrable
Sistema completo recorrido de punta a punta en una sola sesión: registro, verificación, reporte, confirmación, clasificación IA, validación, resolución, ranking, canje y dashboard.

### Documentación
Documentación de las métricas y sus fórmulas. Registro explícito en `PROGRESO_SPRINTS.md` de si la parte condicional se ejecutó o se pospuso, con su justificación.

### Riesgos
**Riesgo principal de alcance:** Telegram y X son secundarios y solo se abordan si el núcleo obligatorio está estable. Si al iniciar el sprint quedan pendientes de los sprints 9 a 12, se posterga la ingesta social y se documenta la decisión. Costo de las agregaciones sobre el ledger (mitigación: precálculo y caché).

### Fuera de alcance del sprint
Cualquier funcionalidad nueva del núcleo, exportación avanzada de reportes, integración con X/Twitter real y notificaciones push.

---

## 24. Sprint 14 — Pruebas unitarias, integración, seguridad y E2E

**Fechas:** 31/10/2026 – 06/11/2026

### Objetivo
Elevar la suite de pruebas al nivel exigido por las hipótesis técnicas: cobertura ≥ 80 % del dominio crítico, verificación del 100 % de las reglas de incentivos y ejecución completa de las pruebas de seguridad y de los flujos end-to-end.

### Funcionalidades
No se agrega funcionalidad. Se consolida, completa y sistematiza la verificación del sistema.

### Backend
Revisión y complemento de la cobertura por módulo, priorizando `rewards_app`, `reports_app` y `auth_app`. Suite dedicada que ejecuta los **diez escenarios S-01 a S-10** del Anexo H.2 más los **dos escenarios adicionales** derivados de las decisiones de este proyecto —**S-11** anulación de pendientes por rechazo y **S-12** reversión posterior a un canje con saldo negativo resultante— como pruebas de aceptación del módulo de incentivos. Pruebas de integración entre `reports_app`, `rewards_app`, `notifications_app` y base de datos. Pruebas de consistencia del ledger: para cada usuario, los saldos expuestos por la API coinciden con la agregación directa en SQL. `pytest-cov` con umbral mínimo configurado que hace fallar la CI si se incumple.

### Frontend
Suite Playwright con los flujos críticos: (1) registro y verificación de identidad; (2) creación de reporte con aviso de duplicado; (3) confirmación por un segundo ciudadano; (4) validación institucional y acreditación; (5) resolución con evidencia; (6) actualización del ranking; (7) consulta de saldos; (8) canje de recompensa y bloqueo del segundo canje mensual. Pruebas de componentes de los formularios críticos.

### Base de datos
Sin cambios. Se emplean *fixtures* reproducibles y una base de pruebas aislada.

### API
Sin endpoints nuevos. Se cierra la documentación de contratos y se verifica que cada endpoint documentado exista y responda según lo especificado.

### Seguridad y permisos
Ejecución completa de la lista del Anexo H.5: crear reporte sin verificar; confirmar sin verificar; confirmar el propio reporte; confirmar dos veces; confirmar un reporte `RESUELTO`/`CERRADO`/`RECHAZADO`; operador accediendo o administrando recompensas de otra institución; usuario común consultando auditoría; canje sin saldo o dos veces en el mismo mes; carga de archivo inválido, excesivo o exactamente repetido; entradas con HTML/script para verificar la sanitización. Se añade la verificación de que la reversión solo la ejecuta el administrador.

### Pruebas
Ver arriba: este sprint **es** la estrategia de pruebas ejecutada en su totalidad.

### Dependencias
Sprints 1 a 13 (todo el sistema implementado).

### Criterios de aceptación
- Cobertura del backend crítico ≥ 80 % reportada por `pytest-cov`.
- Los doce escenarios de incentivos (S-01 a S-12) pasan al 100 %.
- Las once pruebas de seguridad del Anexo H.5 pasan.
- Los ocho flujos E2E se ejecutan sin errores no controlados.
- La CI ejecuta la suite completa y falla si la cobertura baja del umbral.

### Resultado demostrable
Reporte de cobertura y salida de la suite completa en verde, más la grabación de los flujos E2E ejecutados por Playwright.

### Documentación
Informe de pruebas con la tabla de la matriz “hipótesis técnica → prueba → resultado”. Actualización del Anexo H con los resultados reales obtenidos.

### Riesgos
Aparición tardía de defectos de fondo en las reglas de incentivos (mitigado por el TDD aplicado desde el Sprint 6). Fragilidad de las pruebas E2E (mitigada con selectores estables y datos sembrados).

### Fuera de alcance del sprint
Pruebas de carga (Sprint 15), nuevas funcionalidades y refactorizaciones estructurales no motivadas por un defecto detectado.

---

## 25. Sprint 15 — Carga, dataset definitivo y documentación

**Fechas:** 07/11/2026 – 13/11/2026

### Objetivo
Verificar el comportamiento del sistema bajo carga, congelar el dataset sintético definitivo, ejecutar los criterios de validación de la tesis y dejar la documentación técnica y las evidencias listas para la defensa.

### Funcionalidades
Sin funcionalidad nueva. Se corrigen defectos detectados y se optimiza lo que las mediciones indiquen.

### Backend
Escenarios k6 sobre los endpoints críticos: creación de reportes, listado público del mapa, ranking y canje. Medición de P50, P99 y throughput. **Medición explícita de la latencia entre el backend en Railway y la base de datos en Supabase**, aislando el tiempo de red del tiempo de aplicación, para cuantificar el riesgo asumido en D-03 y documentarlo en el informe de resultados. Optimización guiada por evidencia: revisión de planes de ejecución, ajuste de índices, verificación de la caché del mapa y del ranking, y corrección de consultas N+1. Congelación del generador del dataset sintético con semilla fija.

### Frontend
Revisión de accesibilidad básica, tiempos de carga, comportamiento en conexiones lentas y captura sistemática de pantallas para el documento del TCC.

### Base de datos
Dataset sintético definitivo: ≥ 20 reportes distribuidos en el Gran Asunción con las categorías del Anexo A.3, ≥ 10 ciudadanos verificados, ≥ 30 confirmaciones, transacciones en los **cuatro** estados (`PENDIENTE`, `ACREDITADO`, `ANULADO` y `REVERTIDO`), al menos un caso de saldo institucional negativo por reversión posterior a un canje, al menos una recompensa de 1.000 créditos por institución, canjes válidos e intento de segundo canje mensual, y una ventana temporal simulada de 60 días para verificar que el Top 10 solo considera las acreditaciones del mes en curso.

### API
Sin cambios. Se publica la documentación final de la API alineada con el Anexo B.

### Seguridad y permisos
Revisión final de configuración: `DEBUG=False`, cabeceras de seguridad, CORS, rate limiting, permisos de los buckets de Supabase, restricción de acceso a la base de datos de Supabase, rotación de secretos de producción y confirmación de que ningún secreto ni volcado de respaldo quedó versionado.

### Pruebas
Carga con k6 verificando ≥ 50 reportes/min, P50 ≤ 800 ms y P99 ≤ 2.000 ms. Reejecución de la suite completa sobre el dataset definitivo. Medición final de la concordancia de la IA contra las etiquetas de oro. Verificación de trazabilidad por inspección directa de la base: 100 % de cambios de estado, validaciones y movimientos del ledger registrados.

### Dependencias
Sprint 14 (suite de pruebas consolidada).

### Criterios de aceptación
- Los umbrales RNF-01 a RNF-07 se cumplen y quedan documentados con evidencia.
- El dataset definitivo permite reproducir todos los escenarios S-01 a S-12.
- La documentación técnica está completa y las capturas de pantalla recopiladas.
- No quedan defectos abiertos de severidad alta.

### Resultado demostrable
Informe de rendimiento con las gráficas de k6, tabla de verificación de HT-1 a HT-6 y carpeta de evidencias organizada.

### Documentación
Informe técnico final, todos los ADR cerrados, documentación de API publicada, diagramas actualizados (arquitectura, componentes, ER, casos de uso, estados y ciclo de incentivos) y actualización de los Anexos A, B, C, D y H de la tesis con los resultados reales.

### Riesgos
Que las mediciones incumplan un umbral y obliguen a optimizar con poco margen de tiempo (mitigación: medir de forma preliminar ya en el Sprint 13). Que la latencia Railway ↔ Supabase resulte mayor de lo previsto: la respuesta es optimizar consultas, índices y caché, no cambiar la infraestructura en esta etapa. Costo de la API de IA al regenerar el dataset (mitigación: cachear las clasificaciones del dataset congelado).

### Fuera de alcance del sprint
Nuevas funcionalidades, refactorizaciones no justificadas por una medición y cualquier mejora futura.

---

## 26. Sprint 16 — Cierre y preparación de la defensa

**Fechas:** 14/11/2026 – 15/11/2026

### Objetivo
Consolidar resultados, empaquetar evidencias y dejar el proyecto en condiciones de ser defendido y reproducido por un tercero.

### Funcionalidades
Ninguna. Cierre integral.

### Backend
Congelamiento del código, etiqueta de versión `v1.0.0-tcc`, verificación de que el despliegue en Railway reproduce exactamente esa etiqueta y prueba de instalación limpia desde cero siguiendo el `README.md`.

### Frontend
Congelamiento y despliegue final en Vercel desde la misma etiqueta; verificación visual de todas las pantallas del guion de demostración en escritorio y móvil.

### Base de datos
Respaldo del dataset sintético definitivo y del estado final de demostración, con script de restauración reproducible.

### API
Documentación final publicada y congelada.

### Seguridad y permisos
Última revisión de secretos, permisos de los buckets y accesos de las cuentas de demostración utilizadas en la defensa.

### Pruebas
Ejecución final completa de la suite sobre la etiqueta de cierre y ensayo del guion de demostración de punta a punta.

### Dependencias
Sprint 15.

### Criterios de aceptación
- El repositorio está etiquetado y la instalación desde cero funciona siguiendo únicamente el `README.md`.
- Todas las evidencias están empaquetadas y referenciadas desde la documentación.
- `PROGRESO_SPRINTS.md` está completo para los 16 sprints.
- El guion de demostración se ejecuta sin incidentes.

### Resultado demostrable
Demostración integral ensayada y carpeta de evidencias completa para la defensa.

### Documentación
Cierre de `PROGRESO_SPRINTS.md`, resumen ejecutivo de resultados, tabla final de verificación de hipótesis y material de apoyo para la defensa.

### Riesgos
Sprint de solo dos días: no admite trabajo pendiente. Todo lo no cerrado en el Sprint 15 debe considerarse fuera de alcance.

### Fuera de alcance del sprint
Cualquier cambio de código que no sea una corrección crítica y bloqueante.

---

## 27. Dependencias entre sprints

```text
S1  Entorno
 └─ S2  Modelo de datos
     └─ S3  Autenticación y roles
         ├─ S4  Verificación de identidad ─────────────┐
         └─ S5  Mapa y geolocalización ────────────┐   │
                                                   ▼   ▼
                                          S6  Creación de reportes
                                           ├─ S7  Confirmaciones
                                           ├─ S8  Clasificación IA (no bloqueante)
                                           └─────────┬──────────────┐
                                                     ▼              │
                                          S9  Validación y acreditación
                                                     │
                                                     ▼
                                          S10 Estados, resolución y reversión
                                                     │
                                                     ▼
                                          S11 Créditos y ranking
                                                     │
                                                     ▼
                                          S12 Catálogo y canje
                                                     │
                                                     ▼
                                          S13 Dashboards e ingesta social (condicional)
                                                     │
                                          S14 → S15 → S16
```

**Dependencias críticas (no negociables):**

| Sprint | Requiere de forma bloqueante |
|---|---|
| S6 | S4 (ciudadano verificado) y S5 (selector de ubicación) |
| S7 | S6 (reportes existentes) |
| S9 | S6 y S7 (transacciones pendientes que acreditar) |
| S10 | S9 (no se puede resolver sin validar) |
| S11 | S9 y S10 (transacciones acreditadas y revertidas) |
| S12 | S11 (saldos de créditos) |
| S13 | S9–S12 (datos para las métricas) |

**Dependencias no bloqueantes:** S5 no depende de S6 (se demuestra con datos semilla). S8 no bloquea el núcleo: si la integración con Gemini falla, el sistema sigue operando con la categorización del ciudadano. S13 en su parte de ingesta social está subordinado a la estabilidad del núcleo.

**Ruta crítica:** S2 → S3 → S4 → S6 → S7 → S9 → S10 → S11 → S12. Un retraso en cualquiera de estos sprints desplaza todo lo posterior; los sprints 5, 8 y 13 admiten reducción de alcance sin comprometer el MVP.

---

## 28. Estrategia de pruebas

| Tipo | Herramienta | Alcance | Criterio |
|---|---|---|---|
| Unitarias backend | pytest + pytest-cov | Reglas de incentivos, permisos, máquina de estados, HMAC, parser de IA, duplicados | Cobertura ≥ 80 % del dominio crítico |
| Integración API | DRF test client | Endpoints principales y eventos entre `reports_app`, `rewards_app` y `notifications_app` | Códigos y efectos esperados |
| Incentivos | pytest | Escenarios S-01 a S-10 del Anexo H.2, más S-11 (anulación por rechazo) y S-12 (reversión con canje previo) | 100 % |
| Confirmaciones | pytest | Autor, repetido, no verificado, reporte inactivo | 100 % de bloqueos esperados |
| Ranking | pytest | Solo acreditados del mes en curso; las cuatro visibilidades; anulados y revertidos excluidos | Correcto en todos los casos |
| Canje | pytest + Playwright | 1.000 créditos fijos; un canje por institución/mes; remanente conservado; concurrencia serializada; saldo negativo | 100 % |
| Seguridad | pytest | Lista completa del Anexo H.5 más la reversión restringida a admin | 100 % |
| Clasificación IA | Dataset sintético | Concordancia contra etiquetas de oro | ≥ 85 % |
| E2E | Playwright | Ocho flujos críticos | 100 % sin errores no controlados |
| Carga | k6 | Creación de reportes, mapa, ranking, canje | ≥ 50 reportes/min · P50 ≤ 800 ms · P99 ≤ 2.000 ms |
| Auditoría | Inspección de base de datos | Cambios de estado, validaciones y movimientos del ledger | 100 % registrados |

**Principios:** las reglas de negocio se prueban en la capa de servicios, no a través de HTTP; el cliente de Gemini se mockea en CI; cada prueba de seguridad corresponde a un ítem explícito de la documentación; los *fixtures* son deterministas y usan semilla fija.

**Matriz hipótesis → verificación:**

| Hipótesis | Se verifica con |
|---|---|
| HT-1 Arquitectura asíncrona | k6 (throughput y latencia) + inspección de la cola Celery |
| HT-2 Clasificación IA ≥ 85 % | Contraste contra etiquetas de oro del dataset |
| HT-3 Identidad HMAC | Pruebas unitarias de unicidad e irreversibilidad + inspección de base |
| HT-4 Flujos E2E con trazabilidad | Playwright + inspección de `audit_logs` |
| HT-5 Cobertura ≥ 80 % | Reporte de `pytest-cov` |
| HT-6 Reglas de incentivos al 100 % | Suite de escenarios S-01 a S-12 |

---

## 29. Seguridad

**Autenticación y autorización.** Google OAuth 2.0 con validación de emisor y `audience`; JWT de vida corta con refresh; RBAC de tres roles; permisos declarados explícitamente en cada vista, con denegación por defecto.

**Matriz rol × capacidad (resumen):**

| Capacidad | Anónimo | Ciudadano no verificado | Ciudadano verificado | Operador | Admin |
|---|:--:|:--:|:--:|:--:|:--:|
| Ver mapa público y ranking | ✅ | ✅ | ✅ | ✅ | ✅ |
| Enviar verificación de identidad | ❌ | ✅ | — | — | — |
| Crear reporte | ❌ | ❌ | ✅ | ❌ | ❌ |
| Confirmar reporte ajeno activo | ❌ | ❌ | ✅ | ❌ | ❌ |
| Ganar puntos y créditos | ❌ | ❌ | ✅ | ❌ | ❌ |
| Canjear recompensa | ❌ | ❌ | ✅ | ❌ | ❌ |
| Validar / rechazar reporte de su institución | ❌ | ❌ | ❌ | ✅ | ✅ |
| Cambiar estados y cargar evidencia | ❌ | ❌ | ❌ | ✅ | ✅ |
| Administrar recompensa de su institución | ❌ | ❌ | ❌ | ✅ | ✅ |
| Revisar verificaciones de identidad | ❌ | ❌ | ❌ | ❌ | ✅ |
| Consultar auditoría | ❌ | ❌ | ❌ | ❌ | ✅ |
| Revertir una validación | ❌ | ❌ | ❌ | ❌ | ✅ |
| Gestionar instituciones, categorías y operadores | ❌ | ❌ | ❌ | ❌ | ✅ |

**Datos personales.** La cédula se persiste solo como HMAC-SHA-256 con clave secreta del servidor y nunca aparece en respuestas ni en logs. Las selfies de verificación se guardan en un bucket privado accesible únicamente mediante URL firmada para administradores. Ninguna respuesta pública expone datos del autor de un reporte. El ranking respeta la visibilidad configurada y el modo privado no es reidentificable.

**Archivos.** Validación por extensión, MIME real y tamaño máximo; hash SHA-256 obligatorio; rechazo de archivos exactamente repetidos.

**Entradas.** Sanitización de todo texto libre; serializadores estrictos; el `citizen_id` y la institución acreditada se derivan siempre del servidor, nunca del cuerpo de la petición.

**Infraestructura.** HTTPS/TLS extremo a extremo; secretos exclusivamente en variables de entorno; `DEBUG=False` y CORS restringido en producción; rate limiting sobre endpoints públicos y de creación.

**Operaciones sensibles.** Validación, rechazo, reversión, canje y revisión de identidad quedan auditados con actor, motivo y payload. La reversión es exclusiva del administrador global.

---

## 30. Auditoría

La trazabilidad es un requisito académico central: la tesis exige poder reconstruir el 100 % de las acciones importantes. El sistema la implementa en tres capas complementarias.

**1. `report_status_history`** — toda transición de estado con estado previo, nuevo, actor y motivo.

**2. `reward_transactions` (ledger)** — todo movimiento de puntos y créditos como fila inmutable en su contenido económico. Los saldos **nunca** se almacenan: se derivan por agregación. Las reversiones se registran como eventos adicionales con referencia a la transacción original, de modo que el historial nunca se borra ni se sobrescribe. Los cuatro estados (`PENDIENTE`, `ACREDITADO`, `ANULADO`, `REVERTIDO`) permiten auditar por separado cuántos puntos se perdieron por rechazo del reporte y cuántos por reversión de una validación. Esto es exactamente lo contrario de un `user.points += 50` sin historial.

**3. `audit_logs`** — registro transversal de acciones humanas y de IA, con `actor_user_id`, `actor_type` (`human` / `ai` / `system`), `action`, `payload` en JSONB y marca temporal.

**Acciones auditadas obligatoriamente:** envío y revisión de verificación de identidad · creación de reporte · confirmación ciudadana · clasificación de IA (con prompt, respuesta cruda y decisión aplicada) · validación institucional · rechazo con motivo y anulación de las transacciones pendientes asociadas · cambios de estado · carga de evidencia · acreditación de puntos y emisión de créditos · reversión de validación, incluyendo el saldo resultante cuando queda negativo · canje de recompensa · administración de catálogos, operadores y recompensas.

**Verificación:** el criterio de la Tabla 6 exige 100 % de trazabilidad, comprobado por inspección directa de la base en el Sprint 15. Para cualquier ciudadano debe poder reconstruirse su saldo actual sumando exclusivamente sus filas del ledger.

---

## 31. Riesgos técnicos

| Riesgo | Nivel | Sprint más expuesto | Mitigación |
|---|---|---|---|
| Alcance excesivo | Alto | S13 | Sección “Fuera de alcance” obligatoria en cada sprint; Telegram y X condicionados a la estabilidad del núcleo; ninguna mejora futura se adelanta |
| *Farming* de puntos | Alto | S7, S9 | Puntos pendientes hasta validación humana, una confirmación por usuario y reporte, autor excluido, reportes rechazados sin puntos |
| Inconsistencia de saldos | Alto | S9–S12 | Ledger transaccional, saldos derivados, bloqueo de fila en operaciones concurrentes, pruebas de consistencia contra SQL directo |
| Privacidad de cédulas y selfies | Alto | S4 | HMAC-SHA-256, bucket privado, URL firmada, acceso restringido a administradores |
| Complejidad de las reglas de incentivos | Alto | S9, S10 | TDD estricto, servicios de dominio aislados, escenarios S-01…S-12 como pruebas de aceptación |
| Saldo institucional negativo tras revertir créditos ya canjeados | Medio | S10, S12 | Resultado aceptado y deliberado (D-01): el saldo es la diferencia entre créditos válidos y consumidos y puede ser negativa. La reversión nunca se bloquea, no hay penalización, el vale emitido se conserva y el bloqueo de nuevos canjes surge de la propia verificación de saldo. Se explica en la interfaz para que no se lea como sanción |
| Migración inicial con el usuario estándar de Django | Alto | S1 | `AUTH_USER_MODEL = "auth_app.User"` y el esqueleto mínimo del modelo se definen **antes** de la primera migración. Cambiarlo después obligaría a recrear la base |
| Latencia de red entre Railway (backend) y Supabase (base de datos) | Medio | S1, S15 | Consecuencia aceptada de D-03. Se **mide** en las pruebas de rendimiento del Sprint 15 contra P50 ≤ 800 ms, P99 ≤ 2.000 ms y ≥ 50 reportes/min. Mitigaciones de diseño ya previstas: caché Redis del mapa y del ranking, índices dedicados, eliminación de consultas N+1 y agrupación transaccional del ledger. No justifica modificar la arquitectura en esta etapa |
| Ausencia de respaldos automáticos según el plan de Supabase contratado | Medio | S1 en adelante | Verificación del plan en el Sprint 1 y, si no los incluye, rutina obligatoria de **respaldos lógicos** (`pg_dump` semanal y antes de cada migración destructiva), almacenados fuera del repositorio y registrados en `PROGRESO_SPRINTS.md` |
| Transacciones pendientes huérfanas de reportes rechazados | Medio | S9 | Estado `ANULADO`: el rechazo cierra el ciclo de todas las transacciones pendientes del reporte. Prueba dedicada que verifica que ninguna queda en `PENDIENTE` |
| Doble canje por concurrencia | Medio | S12 | `select_for_update()` sobre `User` y recálculo del saldo dentro de la transacción; el saldo es derivado y no admite bloqueo de “fila de saldo” |
| Costos y cuotas de la API de IA | Medio | S8 | Dataset de 20 reportes, modo mock en CI, caché de clasificaciones, modelo configurable |
| Errores de clasificación de IA | Medio | S8 | Revisión humana obligatoria, prohibición de rechazo automático, auditoría de decisiones |
| Duplicados y falsos positivos | Medio | S6 | Aviso no bloqueante, radio y categoría parametrizables |
| Fotos repetidas | Medio | S6 | Hash SHA-256 exacto en el MVP; similitud perceptual como mejora futura |
| Rendimiento de mapa y ranking | Medio | S5, S11, S15 | Índice GIST, bbox, paginación, caché Redis con TTL, índices B-tree dedicados |
| Privacidad del ranking | Medio | S11 | Visibilidad opt-in, modo `Usuario privado`, resolución de nombre en el backend |
| Entregabilidad de correos | Bajo | S4, S12 | Registro en `notifications`, reintentos, proveedor transaccional |
| Dependencia de dos integrantes | Medio | Todos | Documentación por sprint, PR con revisión cruzada, tablero de tareas |
| Sprint 16 de solo dos días | Medio | S16 | Todo debe cerrarse en el Sprint 15; el 16 es exclusivamente cierre |

---

## 32. ADR recomendados

| ADR | Título | Por qué debe documentarse |
|---|---|---|
| ADR-0001 | Monorepo frontend + backend | Condiciona CI/CD, versionado y despliegue en tres proveedores distintos |
| ADR-0002 | Django modular por apps de dominio | Justifica las nueve apps más `core`, y la regla de que las vistas no contienen lógica de negocio |
| ADR-0003 | PostgreSQL + PostGIS y alojamiento en Supabase | Explica el índice GIST, el uso de `ST_DWithin`, por qué no se usa una solución no espacial y por qué la base reside en **Supabase** junto con el almacenamiento (D-03), con sus consecuencias: latencia frente a Railway —medida, no asumida— y política de respaldos lógicos si el plan contratado no los automatiza |
| ADR-0004 | Ledger auditable con importes inmutables | Decisión central de la tesis: los saldos se derivan y nunca se almacenan, y los `impact_delta` / `credit_delta` no se reescriben nunca; la acreditación cambia estado, institución y timestamps, no importes |
| ADR-0014 | Modelo de usuario personalizado desde la primera migración | Por qué `AUTH_USER_MODEL` se fija en el Sprint 1 y no puede posponerse |
| ADR-0005 | Google OAuth 2.0 + JWT | Estrategia de autenticación delegada y manejo de sesión |
| ADR-0006 | Identidad HMAC-SHA-256 y almacenamiento privado de documentos | Cómo se logra identidad verificada pero privada y qué implica la clave secreta |
| ADR-0007 | Almacenamiento de fotografías en Supabase Storage | Buckets, URLs firmadas, validación y hash de archivos |
| ADR-0008 | Integración desacoplada con Gemini | Cliente, prompt versionado, parser estricto, modo mock y prohibición de rechazo automático |
| ADR-0009 | Tareas asíncronas con Celery y qué **no** es asíncrono | Justifica que la acreditación sea síncrona y transaccional, y no un evento en cola |
| ADR-0010 | Créditos por institución y conversión 1:1 | Diferencia entre puntos globales no gastables y créditos consumibles por institución |
| ADR-0011 | Privacidad configurable del ranking | Cómo se preserva la posición sin revelar identidad |
| ADR-0012 | Detección de duplicados por proximidad | Criterio 100 m / misma categoría / activos y por qué no bloquea la creación |
| ADR-0013 | Política de reversión, anulación y saldo negativo | Quién puede revertir, qué se revierte, por qué no hay penalización adicional, distinción entre `ANULADO` y `REVERTIDO`, y por qué se admite saldo institucional negativo |
| ADR-0015 | Correo transaccional por API HTTPS, no por SMTP | Restricción real del alojamiento contratado: Railway no permite SMTP saliente fuera de Pro. Explica por qué el envío no usa `django.core.mail` y por qué SMTP deja de ser requisito de producción |

Formato sugerido: contexto · decisión · alternativas consideradas · consecuencias · estado. Se escriben en el sprint en que la decisión se toma, no al final.

---

## 33. Inconsistencias o dudas encontradas

### 33.1 RESUELTAS mediante el orden de prioridad documental

| # | Contradicción | Resolución |
|---|---|---|
| 1 | La tesis original clasifica la **confirmación ciudadana** como mejora futura (Anexo E, prioridad Baja); la versión revisada la incorpora al alcance obligatorio | **MVP obligatorio.** Se implementa en el Sprint 7 |
| 2 | El resumen original menciona **SHA-256** de la cédula; el cuerpo, HT-3 y el Anexo A usan **HMAC-SHA-256** | **HMAC-SHA-256** con clave secreta del servidor (confirmado por la matriz de modificaciones) |
| 3 | El resumen original habla de **200 reportes sintéticos**; la metodología y los anexos trabajan con **20** | **≥ 20 reportes**, ampliados con ciudadanos, confirmaciones y transacciones |
| 4 | La versión original describe **8 apps Django**; la revisada, **9** | **9 apps** (se incorpora `rewards_app`), más el paquete `core` de utilidades |
| 5 | La versión original describe **14 tablas**; la revisada formaliza **18 entidades** | **18 entidades.** Desaparece `institution_categories`: el mapeo categoría → institución vive en `categories.default_institution_id` (Anexo A.3) |
| 6 | El cuerpo original menciona una tabla `citizens`; el Anexo A del mismo documento usa `users` + `citizen_profiles` | **`users` + `citizen_profiles`** |
| 7 | El término “validación IA” del original se confunde con la nueva “validación institucional” | **“Clasificación asistida por IA”** para la IA; **“validación institucional”** exclusivamente para la decisión humana que acredita puntos |
| 8 | La tesis original ubica las **notificaciones** en el Sprint 11 y el dashboard en el 12; los documentos revisados los reorganizan | Manda el **cronograma revisado del Anexo F** |
| 9 | Divergencia entre los **dos Anexos F revisados**: el cronograma independiente ubica el dashboard en S13 y las notificaciones de recompensas en S12; el Anexo F embebido en la tesis ubica el dashboard en S12 y las notificaciones en S13 | Se aplica el criterio del prompt (§14), que designa el **cronograma revisado del Anexo F** como fuente principal de objetivos: **dashboard en S13, notificaciones de recompensas en S12**. Las fechas son idénticas en ambos documentos, de modo que la contradicción no afecta al calendario |
| 10 | No estaba explícito qué estados se consideran “activos” a efectos de confirmación | **Activos:** `CREADO`, `CLASIFICADO`, `ASIGNADO`, `EN_PROCESO`. `RESUELTO`, `CERRADO` y `RECHAZADO` no admiten confirmación (4.4.3 y 4.9.4) |
| 11 | La validación institucional podía interpretarse como un estado nuevo del reporte | Es un **evento auditable**, no un estado: se registra en `validated_at` / `validated_by` y normalmente coincide con la aceptación y asignación del caso |

### 33.2 RESUELTAS por decisión expresa del responsable del proyecto

Consultadas y confirmadas durante esta etapa de planificación. **No queda ninguna decisión pendiente.**

| # | Duda | Decisión |
|---|---|---|
| 12 | ¿Puede un reporte llegar a `RESUELTO` sin validación institucional previa? | **No.** La transición se bloquea si no existe `validated_at`. No hay acreditación implícita: los puntos solo se acreditan por decisión humana explícita |
| 13 | ¿Quién puede revertir una validación y por qué vía? | **Solo el administrador global**, mediante `POST /api/v1/admin/reports/{id}/revert-validation/` con motivo obligatorio y auditoría completa |
| 14 | El correo es MVP obligatorio pero el cronograma ubica las notificaciones en S12–S13, mientras la verificación de identidad (S4) ya necesita avisar | **`notifications_app` mínima en el Sprint 4** (email de aprobación/rechazo de verificación); consolidación completa en S12–S13 según el cronograma |
| 15 | ¿Qué criterio define un “posible duplicado”? | **`ST_DWithin` a 100 m + misma categoría + solo estados activos**, parametrizable por variable de entorno. No bloquea la creación |
| 16 | ¿Qué ocurre con los puntos pendientes de un reporte rechazado? | **No pueden permanecer pendientes.** Se incorpora el estado **`ANULADO`** para las transacciones que estuvieron pendientes y nunca llegaron a acreditarse por rechazo del reporte. `REVERTIDO` queda **reservado** para las que sí estuvieron acreditadas y luego fueron revertidas. Ver §6.5 y Sprint 9 |
| 17 | ¿Cómo se impide la autoconfirmación a nivel de base de datos? | **No se impide en base de datos.** Se elimina el `CHECK` que comparaba con el autor del reporte: una restricción `CHECK` de PostgreSQL no puede consultar otra tabla. Se conserva `UNIQUE(report_id, citizen_id)` para la confirmación duplicada, y la regla de autoconfirmación se aplica en el servicio de dominio (Sprint 7) |
| 18 | ¿Qué alcance tiene el ranking en el MVP? | **Solo Top 10 del mes en curso e impacto histórico del ciudadano.** Se eliminan el selector de mes, el histórico de rankings de meses anteriores y cualquier requisito de conservar rankings mensuales cerrados. Ver Sprint 11 |
| 19 | ¿Cómo se evita el doble canje concurrente si el saldo es derivado? | **No existen “filas de saldo” que bloquear.** Se bloquea la fila del `User` con `select_for_update()` dentro de `transaction.atomic()`, se **recalcula el saldo institucional dentro del bloqueo** y allí mismo se ejecuta el canje. Ver Sprint 12 |

**D-01 · Reversión de una validación cuyos créditos ya fueron canjeados.** ✅ **RESUELTA.**
Una reversión **siempre debe poder ejecutarse**, aunque los créditos ya hayan sido canjeados. El saldo institucional es la **diferencia entre los créditos válidos y los créditos consumidos**, y **se permite que esa diferencia resulte negativa**. No se aplica penalización adicional —el resultado es solo aritmética del ledger— y el **vale simulado ya emitido permanece emitido**. El ciudadano **no puede realizar otro canje** en esa institución hasta que la diferencia vuelva a cumplir el saldo requerido. Implementada en el Sprint 10 (reversión) y verificada en el Sprint 12 (canje). Ver §6.5.

**D-02 · ¿Puede el operador fijar un `credit_cost` distinto de 1.000?** ✅ **RESUELTA.**
**No.** En el MVP el costo de toda recompensa es **fijo en 1.000 créditos**. El campo `credit_cost` se conserva en el modelo con `DEFAULT 1000` por extensibilidad futura, pero **no es editable por el operador**: el serializador descarta cualquier `cost` recibido y el servicio impone la constante del servidor. Implementada en el Sprint 12.

**D-03 · ¿Dónde reside la instancia de PostgreSQL/PostGIS?** ✅ **RESUELTA.**
**En Supabase.** PostgreSQL + PostGIS se aloja en Supabase, que además continúa proveyendo el almacenamiento de archivos según la arquitectura definida. La instancia real provisionada es **PostgreSQL 17.6** con **PostGIS 3.3.7** en el esquema `gis` (verificado el 16/08/2026); el major productivo es 17 y Supabase administra el minor. El backend Django y los workers Celery se despliegan en **Railway** y se conectan mediante `DATABASE_URL`. Fundamento de la decisión:

- coherencia con la arquitectura documentada en la tesis (4.2.3 y Figura 2);
- soporte de PostGIS;
- simplificación operativa del prototipo;
- centralización de base de datos y almacenamiento en Supabase;
- facilidad para inspeccionar y documentar los datos durante el TCC.

La **latencia entre Railway y Supabase** se asume como riesgo de infraestructura: se **mide** durante las pruebas de rendimiento del Sprint 15 y no justifica modificar la arquitectura en esta etapa. Si el plan de Supabase contratado **no incluye respaldos automáticos**, es obligatorio realizar **respaldos lógicos periódicos** durante el desarrollo (`pg_dump` semanal y antes de cada migración destructiva), almacenados fuera del repositorio. Reflejada en §6.1, §6.2, §6.6, §10, Sprint 1, §31 y `ADR-0003`.

### 33.3 REQUIERE DECISIÓN

*Ninguna decisión pendiente.* Las tres que estuvieron abiertas —D-01, D-02 y D-03— fueron resueltas y se registran en §33.2. La planificación queda aprobada y habilitada para iniciar el Sprint 1.

Si durante la implementación aparece una nueva contradicción que el orden de prioridad documental no resuelva, se documenta aquí y se detiene el trabajo dependiente hasta obtener una decisión.

### 33.4 OBSERVACIÓN NO BLOQUEANTE

| # | Observación | Tratamiento |
|---|---|---|
| O-01 | La sección **3.4** de la tesis revisada sitúa la construcción del prototipo en el primer y segundo trimestre de 2026, mientras el **Anexo F** fija 01/08/2026 – 15/11/2026 | Contradicción interna del documento. Manda el Anexo F. Corresponde una **corrección editorial en Word**; sin impacto técnico |
| O-02 | **FCM** aparece en la tabla de stack (4.5) pero no figura en la tabla de alcance del MVP (4.6), donde solo consta “notificaciones por email simple” | Se planifica **solo email**. Push queda como mejora futura. Conviene aclararlo en la redacción de 4.5 |
| O-03 | `reports.source` (`platform` / `telegram` / `x_mock`) está en el Anexo A de la versión original y no figura entre los campos clave de la revisada, pese a ser imprescindible para la regla “las fuentes no verificadas no generan puntos” | **Se conserva.** Además, `reports.citizen_id` debe ser **nullable** para admitir reportes ingestados sin ciudadano verificado |
| O-04 | El Sprint 5 (mapa) precede al Sprint 6 (creación de reportes): el mapa se construye antes de que existan reportes reales | Se demuestra con **datos semilla**. No es un defecto del cronograma |
| O-05 | El modelo concreto de Gemini se cita en la versión original (`gemini-2.0-flash-lite`) y no en la revisada | Se define por **variable de entorno**, y el modelo efectivamente utilizado se documenta en el informe de resultados |
| O-06 | El Anexo B no especifica un endpoint para revertir validaciones, pese a que el escenario S-07 lo exige | Se agrega `POST /admin/reports/{id}/revert-validation/`. Debe incorporarse al Anexo B de la tesis |
| O-06b | El Anexo B define `GET /ranking/monthly/` con parámetro `month`, y `POST /operator/rewards/` con parámetro `cost` | Ambos parámetros quedan **fuera del MVP** por las decisiones 18 y D-02: el ranking devuelve siempre el mes en curso y el costo es fijo. Conviene reflejarlo en el Anexo B de la tesis |
| O-06c | El Anexo A.2 solo contempla tres estados de `reward_transactions` | Se agrega **`ANULADO`** como cuarto estado (decisión 16) y las columnas de soporte `voided_at` y `reversed_at`. Debe incorporarse al Anexo A de la tesis |
| O-07 | La regla de cierre automático `RESUELTO → CERRADO` a las 72 h es “recomendada” y configurable | Se implementa como tarea de Celery Beat con plazo configurable y desactivable en entornos de prueba |
| O-08 | El ranking mensual no define si el mes se imputa por fecha de la acción o de la acreditación | Se adopta la **fecha de acreditación** (`credited_at`), coherente con “solo participan los puntos acreditados”. Como el MVP publica únicamente el mes en curso y el ranking se recalcula en vivo, no existe ninguna regla de imputación de reversiones a períodos cerrados: una anulación o reversión retira sus filas del conjunto `ACREDITADO` y el resultado se corrige solo |
| O-09 | El módulo de incentivos podría implementarse mediante eventos asíncronos, tal como sugiere la redacción de 4.2.1 (“`reports_app` emite eventos de negocio”) | Se implementa como **llamada de dominio síncrona dentro de la misma transacción**. La separación conceptual entre apps se conserva; lo que no se acepta es la asincronía, porque comprometería la consistencia de saldos y el criterio de trazabilidad del 100 % |
| O-10 | La matriz de modificaciones recomienda incorporar bibliografía específica de gamificación si el tutor lo solicita | Fuera del alcance técnico; corresponde a la redacción del TCC |

---

## 34. Criterios generales de finalización del MVP

El prototipo se considera terminado cuando se cumplen simultáneamente:

**Funcionales**
1. Un ciudadano se registra con Google, verifica su identidad con cédula y selfie, y un administrador la aprueba.
2. Un ciudadano no verificado no puede crear reportes, confirmar, ganar puntos ni canjear, y el sistema se lo explica.
3. Un ciudadano verificado crea un reporte con fotografía obligatoria y ubicación, y recibe 20 puntos pendientes.
4. El sistema detecta fotografías exactamente idénticas y sugiere posibles duplicados sin bloquear la creación.
5. La IA clasifica categoría, institución y prioridad, marca inconsistencias y nunca rechaza automáticamente.
6. Otro ciudadano verificado confirma el reporte activo una sola vez y recibe 10 puntos pendientes; los cuatro bloqueos funcionan.
7. Un operador valida el reporte: el autor pasa a 50 puntos y 50 créditos, y cada confirmador a 10 y 10.
8. El operador resuelve el reporte con evidencia y el autor alcanza 100 puntos y 100 créditos, sin superarlos nunca.
9. Un reporte rechazado no acredita nada y deja todas sus transacciones pendientes en `ANULADO`; ninguna queda pendiente de forma indefinida.
10. El administrador revierte una validación y el sistema retira la totalidad de los puntos y créditos derivados de ese reporte, sin penalización adicional y con rastro completo. El saldo institucional resultante es la diferencia entre créditos válidos y créditos consumidos: vuelve a cero si no había otras acreditaciones ni canjes, y queda negativo si los créditos ya habían sido canjeados. La reversión se ejecuta en ambos casos, el vale emitido se conserva y no se permite un nuevo canje hasta que la diferencia alcance de nuevo el costo requerido.
11. El mapa público muestra los filtros Activos / Resueltos / Todos, prioriza activos y no permite confirmar resueltos.
12. El Top 10 del mes en curso usa solo puntos acreditados y respeta las cuatro modalidades de privacidad, incluida `Usuario privado`; el impacto histórico del ciudadano se mantiene.
13. Un ciudadano con 1.000 créditos canja su vale simulado al costo fijo del MVP, conserva el remanente y no puede repetir el canje de esa institución en el mismo mes; dos peticiones simultáneas producen un único canje.
14. El dashboard institucional y el panel público muestran métricas correctas sin exponer datos personales.
15. Los reportes de Telegram o X simulado no acreditan a su fuente, pero sí a los ciudadanos verificados que los confirman.

**Técnicos**
16. Cobertura del backend crítico ≥ 80 %.
17. Concordancia de la IA ≥ 85 % contra etiquetas de oro.
18. P50 ≤ 800 ms, P99 ≤ 2.000 ms y throughput ≥ 50 reportes/min.
19. 100 % de los flujos E2E críticos exitosos.
20. 100 % de las reglas de incentivos verificadas en los escenarios S-01 a S-12.
21. 100 % de las pruebas de seguridad del Anexo H.5 en verde.
22. 100 % de trazabilidad: todo cambio de estado, validación y movimiento del ledger registrado y reconstruible.

**Documentales**
23. Los 16 sprints registrados en `PROGRESO_SPRINTS.md` con evidencias.
24. Los 14 ADR escritos y cerrados.
25. Documentación de API, diagramas y diccionario de datos actualizados y coherentes con el código.
26. Anexos A, B, C, D y H de la tesis actualizados con los resultados reales, incluidos el estado `ANULADO` y los parámetros retirados del Anexo B.
27. Instalación desde cero reproducible siguiendo únicamente el `README.md`.
28. Sin decisiones pendientes en §33.3 al cierre del proyecto.

**Ninguna mejora futura del §5 debe estar implementada.** Su presencia indicaría desvío de alcance, no avance.

---

*Documento generado en la etapa de planificación del proyecto. No implica implementación de código. Las decisiones aquí registradas derivan de la documentación del TCC y no redefinen el producto.*




