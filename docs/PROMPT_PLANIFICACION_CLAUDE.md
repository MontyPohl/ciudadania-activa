> **Archivo histórico — nota del 20/10/2026.** Lo que sigue es el prompt original de
> planificación previo a S1; no describe el estado del repositorio ni son instrucciones
> operativas actuales. Para preparar S13, consultá [PROGRESO_SPRINTS.md](PROGRESO_SPRINTS.md)
> y [BACKLOG.md](BACKLOG.md); el plan original se conserva sin reescribir.

Quiero que actúes como arquitecto de software y desarrollador senior para este proyecto.

Estoy comenzando desde cero la implementación del proyecto **Ciudadanía Activa**, correspondiente a un Trabajo de Conclusión de Carrera.

El repositorio acaba de ser creado y actualmente contiene principalmente la documentación dentro de la carpeta:

`docs/`

Antes de escribir código necesito que estudies profundamente toda la documentación disponible y generes un plan técnico completo de desarrollo.

## IMPORTANTE: NO IMPLEMENTES CÓDIGO TODAVÍA

En esta primera etapa:

- NO crees el frontend.
- NO crees el backend.
- NO instales dependencias.
- NO generes migraciones.
- NO implementes endpoints.
- NO implementes modelos.
- NO modifiques requisitos del proyecto por decisión propia.

Tu tarea inicial es únicamente:

1. analizar;
2. comprender;
3. detectar inconsistencias;
4. proponer arquitectura;
5. planificar los 16 sprints;
6. documentar el plan.

Una vez revisemos y aprobemos el plan, te pediré explícitamente que comencemos con el Sprint 1.

---

# 1. DOCUMENTACIÓN DEL PROYECTO

Primero inspecciona todos los archivos existentes dentro de `docs/`.

Deberías encontrar documentos equivalentes a:

- tesis/documento original de Ciudadanía Activa;
- versión revisada de la tesis con el módulo de recompensas;
- matriz de modificaciones;
- cronograma revisado del Anexo F;
- `decisiones-incentivos.md`.

Los nombres exactos pueden variar ligeramente. Identifica los archivos por su contenido.

Debes leerlos completamente antes de diseñar el plan.

---

# 2. ORDEN DE PRIORIDAD DE LOS DOCUMENTOS

Algunos documentos pueden contener información contradictoria porque el proyecto evolucionó después de redactar la tesis original.

Cuando encuentres contradicciones, utiliza este orden de prioridad:

1. **Versión revisada de la tesis con recompensas**
2. **Cronograma revisado del Anexo F**
3. **`decisiones-incentivos.md`**
4. **Matriz de modificaciones**
5. **Tesis/PDF original**

El PDF original sigue siendo la base académica y conceptual del proyecto, pero las decisiones posteriores reemplazan las partes que fueron modificadas.

Por ejemplo:

Si el documento original indica que la confirmación ciudadana es una mejora futura, pero los documentos revisados indican que ahora forma parte del MVP obligatorio, debes considerar que:

**la confirmación ciudadana forma parte del MVP obligatorio.**

No intentes reconciliar silenciosamente contradicciones.

Si encontrás alguna contradicción que no pueda resolverse mediante este orden de prioridad, documentala claramente y solicitá una decisión antes de implementarla.

---

# 3. OBJETIVO GENERAL DEL SISTEMA

Ciudadanía Activa es una plataforma web para el reporte, seguimiento y gestión de incidencias urbanas.

Debe conectar a ciudadanos con instituciones públicas responsables y permitir, entre otras funcionalidades:

- autenticación;
- verificación de identidad ciudadana;
- creación de reportes urbanos;
- fotografía y ubicación geográfica;
- clasificación asistida mediante IA;
- asignación institucional;
- seguimiento de estados;
- confirmación ciudadana de reportes existentes;
- gestión institucional;
- evidencia de resolución;
- mapa público de transparencia;
- dashboard y métricas;
- notificaciones;
- auditoría;
- sistema de incentivos y recompensas.

El objetivo no es solamente aumentar la cantidad de reportes, sino favorecer **participación ciudadana útil y verificable**.

---

# 4. STACK TECNOLÓGICO DEFINIDO

Respeta, salvo contradicción explícita en los documentos revisados, la arquitectura tecnológica definida en el proyecto.

Frontend:

- Next.js
- React
- Tailwind CSS

Backend:

- Python
- Django
- Django REST Framework

Base de datos:

- PostgreSQL
- PostGIS

Otros componentes definidos:

- Redis
- Celery / Celery Beat
- almacenamiento de archivos mediante Supabase
- API de Gemini para asistencia de IA
- Google OAuth
- notificaciones por correo
- posible FCM según alcance
- despliegue compatible con Vercel / Railway según corresponda

No reemplaces tecnologías por conveniencia.

Por ejemplo:

NO reemplaces Django por Supabase Backend.

NO reemplaces Google OAuth por otro sistema de autenticación.

NO reemplaces PostgreSQL/PostGIS por Firebase.

Si considerás que existe una alternativa técnicamente mejor, podés mencionarla en una sección de observaciones, pero NO cambies la arquitectura del proyecto.

---

# 5. ROLES PRINCIPALES

Identifica y planifica correctamente los permisos de al menos:

- Ciudadano
- Operador institucional
- Administrador

Los permisos deben seguir la documentación.

Particularmente:

- usuarios no verificados no deben poder crear reportes;
- solamente ciudadanos verificados pueden obtener puntos y créditos;
- operadores trabajan sobre los reportes correspondientes a su institución;
- administradores tienen funciones de administración global y revisión que corresponda.

---

# 6. MÓDULO DE INCENTIVOS

Lee especialmente `decisiones-incentivos.md`.

Las reglas definidas allí son decisiones ya tomadas del proyecto y NO deben modificarse arbitrariamente.

Como mínimo debes respetar:

### Reporte creado

El ciudadano obtiene:

`+20 puntos pendientes`

### Reporte validado institucionalmente

Cuando un operador institucional acepta el reporte como una incidencia legítima:

- los 20 puntos pendientes pasan a estar acreditados;
- se agregan +30 puntos acreditados.

Total acumulado:

`50 puntos`

### Reporte resuelto

Si la institución marca posteriormente el problema como resuelto:

`+50 puntos`

Máximo para el autor de un reporte:

`100 puntos`

### Confirmación ciudadana

Un ciudadano verificado puede confirmar un reporte activo creado por otra persona declarando conceptualmente:

“Acabo de verificar que este problema todavía sigue ahí.”

Genera:

`+10 puntos pendientes`

Esos puntos se acreditan cuando el reporte es validado institucionalmente.

Reglas:

- una confirmación por ciudadano por reporte;
- el autor no puede confirmar su propio reporte;
- solamente reportes activos pueden confirmarse.

---

# 7. PUNTOS Y CRÉDITOS

El sistema utiliza dos conceptos diferentes.

## Puntos de impacto ciudadano

Son globales.

Sirven para:

- reputación;
- impacto histórico;
- ranking mensual.

NO se gastan cuando el usuario canjea una recompensa.

## Créditos institucionales

Se acumulan por institución.

Conversión:

`1 punto acreditado = 1 crédito de la institución responsable`

Ejemplo:

Un reporte validado por ANDE que genera 50 puntos también genera:

`50 créditos ANDE`

Estos créditos sí son consumibles mediante canjes.

---

# 8. RECOMPENSAS

Para el MVP:

- la recompensa es simulada;
- no existe integración real con facturación institucional;
- el canje genera un código/vale demostrativo;
- el costo estándar es de 1.000 créditos institucionales;
- los créditos no vencen;
- el saldo sobrante permanece;
- existe como máximo un canje por institución por usuario por mes.

Los descuentos o beneficios económicos reales quedan para una futura implementación productiva.

---

# 9. RANKING

Debe existir:

**Top 10 mensual**

Reglas:

- utiliza únicamente puntos acreditados;
- los puntos pendientes no participan;
- el ranking mensual considera lo obtenido durante ese mes;
- al cambiar de mes comienza un nuevo período;
- el impacto histórico del ciudadano no se elimina.

Privacidad:

El ciudadano puede elegir cómo aparecer:

- nombre y apellido;
- nombre + inicial;
- alias;
- privado.

Si el usuario elige privacidad total debe conservar su posición y mostrarse como:

`Usuario privado`

---

# 10. REGLAS IMPORTANTES DE VALIDACIÓN

Debes contemplar en la arquitectura:

### Posibles duplicados

Antes de crear un nuevo reporte, el sistema puede detectar un reporte similar cercano.

Debe sugerir:

“Ya existe un reporte similar”.

El ciudadano puede:

- acceder al reporte existente y confirmarlo;
- indicar que no corresponde al mismo problema y continuar.

La detección NO debe bloquear automáticamente la creación.

### Fotografías repetidas

Para el MVP solo se requiere detectar imágenes exactamente idénticas mediante hash.

Detección perceptual o similitud avanzada:

**mejora futura**.

### Inteligencia artificial

La IA ayuda con:

- categoría;
- institución;
- prioridad;
- análisis de consistencia;
- posible duplicado.

La IA NO debe decidir automáticamente que un problema es falso.

Ante un caso dudoso o inconsistente:

`IA → revisión humana institucional`

Las decisiones de IA deben poder auditarse.

### Reportes rechazados

No generan puntos.

### Reversión

Si una validación institucional se revierte posteriormente:

- se revierten los puntos derivados;
- se revierten los créditos derivados;
- también se revierten las acreditaciones relacionadas con confirmaciones;

pero NO se aplica una penalización adicional al ciudadano.

---

# 11. INTEGRACIONES EXTERNAS

Telegram y X/Twitter aparecen en la documentación original.

Consideralos secundarios frente al núcleo obligatorio.

Reportes provenientes de fuentes que no tengan un ciudadano verificado asociado NO generan puntos para la fuente.

Sin embargo, un ciudadano verificado sí puede confirmar posteriormente uno de esos reportes y generar puntos si se cumplen las reglas correspondientes.

En la planificación debes mantener estas integraciones subordinadas a la estabilidad del MVP principal.

---

# 12. MAPA

El mapa debe permitir al menos los filtros:

- Activos
- Resueltos
- Todos

Por defecto deben priorizarse los activos.

Los reportes resueltos pueden continuar visibles por transparencia, pero:

NO pueden confirmarse.

---

# 13. AUDITORÍA

Quiero que prestes especial atención a la trazabilidad.

No diseñes el sistema de puntos simplemente utilizando algo como:

`user.points += 50`

sin historial.

La arquitectura debe considerar movimientos o transacciones auditables para:

- puntos pendientes;
- acreditaciones;
- bonus;
- créditos institucionales;
- reversión;
- canjes.

La tesis requiere trazabilidad de las acciones importantes.

---

# 14. PLANIFICACIÓN POR SPRINTS

Utiliza el cronograma revisado del Anexo F como fuente principal para las fechas y objetivos.

Existen 16 sprints entre:

01/08/2026 y 15/11/2026.

NO reorganices arbitrariamente el cronograma.

Para cada Sprint quiero una sección detallada que contenga:

## Objetivo

Qué resultado debe producir el sprint.

## Funcionalidades

Qué funcionalidades se desarrollarán.

## Backend

Trabajo específico de backend.

## Frontend

Trabajo específico de frontend.

## Base de datos

Modelos, migraciones y relaciones necesarias.

## API

Endpoints o contratos que deberían incorporarse.

No hace falta escribir todavía la implementación exacta.

## Seguridad y permisos

Qué reglas de acceso deben considerarse.

## Pruebas

Qué pruebas deben hacerse durante el sprint:

- unitarias;
- integración;
- E2E;
- seguridad;

según corresponda.

## Dependencias

Qué funcionalidades de sprints anteriores necesita.

## Criterios de aceptación

Condiciones concretas que indiquen que el sprint puede considerarse terminado.

## Resultado demostrable

Qué debería poder mostrar funcionando al finalizar el sprint.

## Documentación

Qué documentación debe actualizarse.

## Riesgos

Problemas técnicos probables de ese sprint.

## Fuera de alcance del sprint

Indica expresamente qué funcionalidades NO deben adelantarse todavía.

Esto es importante para evitar crecimiento descontrolado del alcance.

---

# 15. DEPENDENCIAS ENTRE SPRINTS

Además del análisis individual, identifica claramente las dependencias.

Ejemplo conceptual:

modelo de datos
↓
autenticación
↓
usuarios verificados
↓
creación de reportes
↓
confirmaciones
↓
validación institucional
↓
acreditación de puntos
↓
créditos
↓
ranking y recompensas

No asumas que todos los módulos pueden desarrollarse independientemente.

---

# 16. ESTRUCTURA DEL PROYECTO

Después de analizar la documentación, proponé una estructura inicial del repositorio.

Actualmente es un repositorio nuevo.

Quiero evaluar una estructura conceptual semejante a:

```text
ciudadania-activa/
├── frontend/
├── backend/
├── docs/
├── .env.example
├── .gitignore
├── README.md
└── ...
```

Pero NO asumas que esa estructura interna es definitiva.

Proponé detalladamente cómo organizarías:

### Frontend

- rutas;
- componentes;
- features;
- servicios/API;
- autenticación;
- mapa;
- reportes;
- dashboard;
- recompensas;
- ranking.

### Backend

Revisa especialmente la organización de aplicaciones Django.

La documentación menciona conceptos equivalentes a:

- auth_app
- reports_app
- map_app
- analytics_app
- notifications_app
- admin_app
- ai_client_app
- social_ingestion_app
- rewards_app

Analiza si esa división se sostiene y proponé una estructura coherente SIN cambiar el alcance funcional.

---

# 17. DECISIONES ARQUITECTÓNICAS

Quiero que identifiques decisiones que deberían documentarse como ADR (Architecture Decision Records).

Por ejemplo:

- monorepo frontend/backend;
- Django modular por apps;
- PostGIS;
- transacciones de recompensas auditables;
- almacenamiento de fotografías;
- estrategia de autenticación;
- integración Gemini;
- tareas asíncronas;
- sistema de créditos;
- privacidad del ranking.

No hace falta crear todos los ADR todavía.

Incluí cuáles recomendarías documentar y por qué.

---

# 18. DETECTAR INCONSISTENCIAS

Antes de generar el plan definitivo, crea una sección:

## Inconsistencias o dudas encontradas

Enumera cualquier contradicción entre:

- requisitos;
- arquitectura;
- cronograma;
- entidades;
- casos de uso;
- anexos;
- decisiones posteriores.

Clasifícalas como:

- RESUELTA mediante prioridad documental;
- REQUIERE DECISIÓN;
- OBSERVACIÓN NO BLOQUEANTE.

No inventes una solución para los casos que realmente requieran una decisión humana.

---

# 19. MVP VS MEJORAS FUTURAS

Debes distinguir claramente ambas categorías.

Entre las mejoras futuras ya definidas están:

- insignias;
- niveles;
- campañas temporales;
- proximidad GPS obligatoria para confirmar;
- similitud perceptual avanzada de imágenes;
- recompensas económicas reales;
- antifraude avanzado.

No las incluyas como requisitos obligatorios del MVP.

---

# 20. ARCHIVO QUE DEBES GENERAR

Después del análisis crea:

`docs/PLAN_DESARROLLO.md`

Debe ser suficientemente detallado para utilizarlo como guía durante toda la implementación.

Estructura sugerida:

```text
# Plan de Desarrollo — Ciudadanía Activa

## 1. Resumen del sistema

## 2. Fuentes documentales analizadas

## 3. Requisitos consolidados

## 4. MVP obligatorio

## 5. Mejoras futuras

## 6. Arquitectura propuesta

## 7. Estructura propuesta del repositorio

## 8. Modelo conceptual de módulos

## 9. Dependencias entre módulos

## 10. Estrategia de desarrollo

## 11. Sprint 1
...

## 26. Sprint 16

## 27. Dependencias entre sprints

## 28. Estrategia de pruebas

## 29. Seguridad

## 30. Auditoría

## 31. Riesgos técnicos

## 32. ADR recomendados

## 33. Inconsistencias o dudas encontradas

## 34. Criterios generales de finalización del MVP
```

Podés mejorar esta estructura si encontrás una organización más clara, pero no elimines información importante.

---

# 21. TAMBIÉN CREA

`docs/PROGRESO_SPRINTS.md`

Inicialmente debe contener los 16 sprints en estado pendiente.

Ejemplo:

```text
Sprint 1 — Pendiente
Sprint 2 — Pendiente
...
Sprint 16 — Pendiente
```

Más adelante lo utilizaremos para registrar:

- estado;
- fecha;
- funcionalidades terminadas;
- pruebas realizadas;
- problemas encontrados;
- commits relevantes.

---

# 22. NO IMPLEMENTAR TODAVÍA

Después de generar los documentos:

DETENTE.

No empieces automáticamente Sprint 1.

No instales paquetes.

No ejecutes comandos destructivos.

No generes proyectos Next.js o Django todavía.

No crees base de datos.

No realices commits automáticamente.

Al finalizar quiero que me presentes un resumen que indique:

1. qué documentos analizaste;
2. cómo entendiste el sistema;
3. arquitectura propuesta;
4. estructura de repositorio propuesta;
5. principales dependencias;
6. inconsistencias encontradas;
7. archivos de planificación generados;
8. cualquier decisión que necesites que yo confirme.

Después de revisar esos resultados te indicaré cuándo comenzar el Sprint 1.

---

# PRINCIPIO GENERAL

No quiero simplemente “hacer que funcione”.

Quiero construir un proyecto:

- organizado;
- modular;
- testeable;
- documentado;
- auditable;
- defendible académicamente;
- consistente con la tesis;
- implementable dentro del cronograma.

La documentación del TCC define los requisitos.

Tu función es ayudarme a convertir esos requisitos en una implementación técnicamente sólida, no redefinir el producto sin autorización.

Comenzá inspeccionando y leyendo todo el contenido de `docs/`.
