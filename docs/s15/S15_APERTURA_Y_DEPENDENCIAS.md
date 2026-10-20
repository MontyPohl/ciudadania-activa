# Sprint 15 Apertura y dependencias

**Seguimiento vigente:** [validación y estado actual](S15_INFORME_VALIDACION.md). Este enlace distingue la segunda etapa de los cortes anteriores conservados en este documento.

**Inicio real:** 20/10/2026. **Base:** `origin/main@3d89182`.
**Rama:** `feat/s15-validation`. **Actualización vigente:** decisiones del usuario del 20/10/2026.

El usuario confirmó el cierre de S14 y pidió continuar S15. Se volvió a revisar el plan
completo sobre el árbol integrado. Al abrir la etapa confirmó que no disponía todavía de
fotos inéditas con etiquetas de oro ni de un entorno de ensayo remoto; pidió preparar
formatos, ejecutor y herramientas con pruebas locales. La tabla de entradas conserva ese
estado inicial. La actualización siguiente prevalece para el trabajo pendiente.

## Cierre de S14 revisado

| Control | Evidencia registrada en la base final | Consecuencia |
|---|---|---|
| Integración | PR #54 mergeado como `4bd05fb`; PR #55 incorpora el registro productivo en `3d89182` | El plan deja de depender de ramas S14 sin integrar |
| Backend | 2.325 pruebas de dominio; producto 95,62 % y HT5-01 96,91 %, con denominadores separados | Reutilizar compuertas y volver a verificar los cambios S15 |
| CI del candidato | 6/6 checks verdes en `f9a9da0`, incluidos dominio, experimentos, build y E2E | Los fallos locales del aparato tienen diagnóstico documentado; no se presentan como CI verde local |
| Frontend | 417 pruebas Vitest en 44 archivos, build y 58 E2E con build de producción | Conservar contratos y separar API interceptada de dominio real |
| Flujos locales | Ocho flujos y captura, 9/9; Storage falso, sesiones sembradas, sin IA/correo reales | Herramientas reutilizables con los límites declarados |
| Incentivos y controles | S-01…S-12, LED-01 y SEC-01…SEC-11 registrados en verde | Dataset final y trazabilidad completa siguen siendo trabajo S15 |
| Publicación | Heroku v26, reconciliación `a7f775d`; árbol backend `51a9352` igual al de main, smoke público registrado | No se requiere volver a desplegar para preparar S15 |
| Revisión y riesgos | S14 conserva límites de auditoría, `CERT_NONE`, secretos pendientes y protección de main | Revisar los instrumentos nuevos y mantener abiertos los riesgos que no se hayan resuelto |

Fuentes: [informe S14](../s14/S14_INFORME_PRUEBAS.md),
[decisiones S14](../s14/S14_DECISIONES.md),
[guía de los flujos](../s14/S14_E2E_FULLSTACK.md) y
[runbook productivo](../runbooks/HEROKU_MIGRATION.md#15-operación-vigente--corte-20102026-0125-utc).
Son antecedentes de cierre revisados en el repositorio; esta apertura no repite sus pruebas
ni afirma una observación nueva de producción.

## Preparación local y mediciones finales

**P0-L** permite ejecutar ahora trabajo reversible con datos sintéticos: generador y
verificador, acceso de demo local, Storage persistente de ensayo, formatos de carga/IA,
replay offline, escenarios k6 y pruebas de contrato. Se usa una red Docker interna,
PostGIS y Redis dedicados y artefactos privados fuera del repositorio.

**P0-M** habilita cada medición final con sus entradas y su protocolo. No se declara cerrado
en conjunto; se resuelve por carril y no bloquea tareas independientes. La tabla siguiente
registra las condiciones al abrir S15, antes de las nuevas decisiones del usuario.

| Entrada | Estado al abrir S15 | Trabajo que puede avanzar | Condición para la medición final |
|---|---|---|---|
| Fotos inéditas y oro | No disponibles, confirmado por el usuario | Plantilla, validación, rúbrica, ejecutor y puntuador con respuestas sintéticas | Casos autorizados, etiquetas adjudicadas y hashes congelados antes de predecir |
| Entorno Heroku ↔ Supabase de ensayo | No disponible, confirmado por el usuario | Harness y smoke local identificados como tales | Destino separado, capacidad y procedimiento definidos antes de medir red entre proveedores |
| Protocolo académico | Propuesta técnica preparada; uso decisorio aún por fijar | Matriz de requisitos y evidencia de ingeniería | Decisión prospectiva de métricas/síntesis; no readjudicar S11 retrospectivamente |
| Ancla de demostración | Usar 20/10/2026 para comprobar herramientas locales | Dataset reproducible de ensayo con fecha explícita | Confirmar mes de defensa, mantener versión o crear otra con manifiesto propio |
| Secretos y operación | Arrastre S14-D19 abierto | Preparar procedimiento y consumidores afectados | Ventana concreta y verificación antes de ejecutar cambios operativos |

## Actualización de decisiones — 20/10/2026

Estas decisiones sustituyen las condiciones de la tabla de apertura que hayan quedado
superadas. El detalle y el reparto propuesto de fotografías están en el
[registro de decisiones y pendientes](S15_DECISIONES_Y_PENDIENTES.md).

| Tema | Decisión vigente | Pendiente concreto |
|---|---|---|
| Fotos | El usuario entregará el Excel/ZIP que ya preparó con Claude; todavía no se ha recibido | Validar el formato y adaptar la importación al material existente; no recrear otra plantilla |
| Cantidad IA | Objetivo de colección adoptado para continuar: 100 reportes reservados y 20 distintos para piloto/pruebas/demo, 120 en total | Validar el material recibido y congelar composición, autorización e independencia antes de predecir |
| Oro | Etiquetas humanas de referencia de categoría, institución y prioridad antes de observar la IA; Claude puede auxiliar, sin sustituir adjudicación humana | Completar rúbrica, revisión de casos y desacuerdos; congelar etiquetas y hashes |
| Presupuesto API | USD 5–10 compartidos entre evaluación, pruebas personales y demostración en vivo | Estimar con modelo/precios vigentes y piloto acotado; reservar saldo para pruebas/demo antes de las llamadas finales |
| Entorno remoto | No se exige crear ni contratar un entorno adicional; el usuario permite usar el existente | Empezar con observación acotada; antes de cualquier mutación, preparar ventana, respaldo y recuperación de sus datos |
| Rendimiento final | Se puede completar localmente con servidor de aplicación adecuado, DEBUG=false, TTL representativos y repeticiones | Congelar perfil, recursos, cuotas, worker/cola y reconciliación; publicar límites de extrapolación |
| Heroku ↔ Supabase | Medición separada en el entorno existente; una corrida local no la sustituye | Diagnóstico SQL constante de solo lectura ejecutado; su informe conserva alcance y límites. Los harnesses de carga siguen locales |
| Defensa | Prevista aproximadamente para el 16/12/2026; los datos técnicos se necesitan antes para redactar | Conservar resultados con fecha real y preparar una versión de demo de diciembre con manifiesto propio |
| Cuentas | El usuario preparará municipalidad, ANDE, administrador y ciudadano | Confirmar ESSAP y segundo ciudadano; completar login real, permisos y flujos con cuentas segregadas |
| Documento académico | Actualizar nuevamente el `.docx` después de terminar los 16 sprints y sus pruebas | Preparar evidencias y cambios durante S15/S16; la incorporación posterior no bloquea el cierre de ninguno de ellos |

P0-M local no depende de recibir fotos ni de otro entorno remoto. P0-M IA espera el paquete,
el oro humano y el protocolo/presupuesto congelados. Para la comprobación remota se usará el
destino existente con un plan proporcionado al alcance; las pruebas de escritura se preparan
con respaldo y ventana antes de ejecutarlas. Esta actualización documental no ejecuta
llamadas con cuota ni operaciones remotas y no cambia los resultados históricos S14/S11.

## Reparto y límites

El carril de datos trabaja en `backend/apps/core/final_dataset/` y comandos de gestión.
El carril de carga trabaja en `tools/performance/`; el de IA, en `tools/evaluation/`.
La integración mantiene `tools/s15/`, protocolos, matriz y evidencia de verificaciones.
Ningún carril modifica los archivos congelados por GOV-T054 ni los contratos del producto.

El cierre de P0-L exige herramientas locales ejecutables, pruebas frescas y una revisión
independiente de sus cambios. La implementación del sprint queda en curso después de ese
hito: la existencia de herramientas no cumple por sí sola RNF-01…RNF-07 ni cierra la tesis.

## Estado posterior de la etapa

Las condiciones de apertura anteriores se conservan como antecedente. El
[informe vigente](S15_INFORME_VALIDACION.md) registra como ejecutados el rendimiento
local representativo, diagnóstico Heroku ↔ Supabase de solo lectura, UX, API = SQL,
navegador con conciliación y medición descriptiva de pantallas. No se mantienen como
trabajo pendiente esas ejecuciones ya comprobadas.

Quedan fotos/oro y evaluación IA, cuentas/proveedores reales, operación de consolas
y cierre/integración técnica según el registro vigente. El smoke Google y correo real
Brevo siguen separados del ensayo sintético. Preparar evidencia no actualiza el
`.docx`: esa incorporación se realizará después de terminar los 16 sprints.
No se reinician experimentos S11 ni se sustituyen sus veredictos por estas pruebas.
