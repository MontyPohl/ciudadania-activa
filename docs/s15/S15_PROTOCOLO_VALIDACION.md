# Sprint 15 Protocolo de preparación y validación local

**Identidad:** `s15-local-v1`. **Fecha:** 20/10/2026.
**Base:** `3d89182`. **Estado:** preparación local; no habilita mediciones finales externas.

**Actualización posterior:** las [decisiones del usuario](S15_DECISIONES_Y_PENDIENTES.md)
definen presupuesto, defensa en diciembre, uso del despliegue existente e incorporación
del `.docx` después de S16. Este documento conserva el alcance `s15-local-v1` de las
corridas ya ejecutadas. Las mediciones finales necesitarán su configuración/protocolo
identificados; no se amplía retroactivamente la evidencia de las corridas locales.

## Entornos y entradas

La preparación utiliza PostGIS 17/PostGIS 3.5 y Redis 7 dedicados en `ca-s15-local`,
una red Docker interna. No se carga el `.env` del checkout en los procesos de prueba.
IA, correo y S11 permanecen desactivados; los sustitutos y sesiones son sintéticos.
Los artefactos viven bajo `~/.local/share/ciudadania-activa/s15/20261020/out/` con acceso
restringido. Las instrucciones reproducibles están en [tools/s15](../../tools/s15/README.md).

Separar el mundo funcional, las cargas que lo consumen y los casos IA. La semilla y fecha
de referencia fijan entradas del dataset. Los IDs lógicos se vinculan con PK y códigos
generados en un manifiesto privado; no se promete igualdad byte a byte de los volcados.

## Reglas de resultados

Cada ejecución conservará comando, fecha UTC, base Git, árbol con cambios locales, versiones,
configuración no secreta, hashes de entradas, resultado y límites. Publicar únicamente el
índice minimizado. No almacenar claves, tokens ni URLs firmadas en logs o reportes públicos.

Se distinguirán:

- **Verificado localmente:** comportamiento que una prueba o corrida completa haya observado.
- **Pendiente:** herramienta o entrada aún no ejecutada/disponible.
- **Inválido:** corrida sin integridad, correspondencia de entradas o contrato suficiente.
- **Incumplido:** medición válida que no alcanza su objetivo.

Los porcentajes históricos de S14 son antecedentes con su fuente. Las pruebas frescas S15
se registrarán con su propio comando y alcance. No se suman corridas parciales para inventar
una suite completa ni se selecciona el mejor intento de un conjunto de mediciones.

## Dataset y restauración

La verificación debe comparar expectativas congeladas con SQL independiente, saldos,
relaciones, ciclos, estados y objetos persistidos. Cubrir hashes originales y almacenados,
pre/postcanje, límite mensual, reversión y saldo negativo. El reloj de generación no se usa
para emitir sesiones: estas se renuevan a hora real en un archivo privado separado.

El ensayo de restauración necesita DB y objetos vacíos de destino, procesos detenidos,
versiones compatibles y colas/cachés propias. Después se verifican datos, bytes, capacidades
de acceso y flujo real. Enumerar un dump no constituye ese ensayo. Los comandos y resultados
concretos se registrarán cuando se ejecute cada paso.

## Carga

El smoke del harness local comprueba payloads, autenticación, estado/shape y contadores.
Debe identificarse como diagnóstico si usa servidor de desarrollo, datos pequeños o
sustitutos. No adjudica por sí solo P50/P99, capacidad sostenida ni rendimiento productivo.

El protocolo de medición final conservará P50 ≤ 800 ms, P99 ≤ 2.000 ms y ≥ 50 reportes/min.
Creación: diez usuarios con cinco requests secuenciales cada uno, barrera y ventana monotónica
de 60 s. Contar respuestas 201 antes del corte y reconciliación posterior por SQL. Conservar
tardíos, errores y persistencias tras timeout. Fijar tasas de lectura por IP, calentamiento,
TTLs, N, estimador, repeticiones y plazo de drenaje antes de medir.

Los tres canjes funcionales solo permiten un smoke exploratorio. El presupuesto de una
población mayor requiere financiación legítima y cupos suficientes; no reducir el umbral
P99 por disponer de pocas muestras ni producir éxito con respuestas 429 rápidas.

## IA

La etapa actual valida formato y ejecutor offline. La plantilla vacía y las imágenes
sintéticas de transporte no son el conjunto independiente de evaluación. Deben fallar
como evidencia de RNF-05 cuando falten fotos, oro o protocolo congelado.

Propuesta primaria: coincidencia exacta simultánea categoría/institución/prioridad antes
de policy. Publicar categoría/prioridad como indicador secundario identificado. N permanece
fijo, los nulos comparan solo con nulos y se decide sin redondeo por `100 × aciertos ≥ 85 × N`.
El protocolo final fijará errores, reintentos, catalogación, revisión humana y uso académico
antes de llamar al proveedor. No consumir cuota en esta etapa.

## Verificaciones de integración

Backend: Ruff, formato, migraciones sin cambios, dominio con la compuerta de cobertura y
reporte HT5-01 separado. Comprobar GOV-T054 si corresponde por las fronteras congeladas.
Herramientas: pruebas unitarias offline Python/Node y smoke local cuando estén preparadas.
Una modificación frontend requerirá además los controles y flujos aplicables del README.

El cierre completo de S15 exige también las mediciones finales, trazabilidad, revisión de
configuración y documentación técnica para la tesis. La incorporación al `.docx` se hará
después de S16, según la decisión posterior del usuario. Este protocolo no declara
satisfechas las condiciones de cierre; la segunda etapa tiene resultados y límites en
el [informe vigente](S15_INFORME_VALIDACION.md).
