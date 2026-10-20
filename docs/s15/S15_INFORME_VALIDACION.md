# Sprint 15 — validación y estado actual

**Corte:** 20/10/2026. **Estado:** S15 en ejecución sobre `3d89182` más los cambios
de la rama `feat/s15-validation`. Esta segunda etapa continúa la
[preparación local](S15_INFORME_PREPARACION_LOCAL.md), cuyos resultados y fallos se
conservan con su alcance original. No se actualizan retroactivamente los experimentos
ni los veredictos de S11.

## Resultados de la segunda etapa

| Carril | Estado observado | Evidencia y límite |
|---|---|---|
| Dataset y restauración | Verificador reforzado con ubicación, SRID e institución; regresiones de corrupción detectadas | La copia restaurada del carril API pasó 1.329/1.329 controles antes de consumirla. SQL y bytes sintéticos; no son datos de ciudadanos reales |
| API = SQL y reglas | Recorrido final 005: 180 comprobaciones y 72 solicitudes correctas, sobre otra restauración 1.329/1.329 | [Informe API](S15_API_SQL_Y_FLUJOS.md). Fuente final con proxies heredados bloqueados; no acredita navegador ni proveedores reales |
| Interfaz y usabilidad | Cuatro hallazgos corregidos; 67 E2E y 417 pruebas de componentes correctas, con build, tipos, lint y formato | [Auditoría UX](S15_AUDITORIA_UX.md). HTTP interceptado en esa suite; no acredita por sí solo el backend real |
| Carga representativa local | Tres ventanas con gthread/4 correctas: 50 persistencias y drenaje de 100 tareas por ventana; P50 234–377 ms, P99 470–1.008 ms | [Informe de rendimiento](S15_INFORME_RENDIMIENTO.md). Conserva tres baselines, una incumplida, 360 lecturas y tres negativas. Perfil local con sustitutos; configuración de despliegue preparada, sin publicar |
| Heroku ↔ Supabase | 30 consultas constantes de solo lectura, mediana 66,18 ms; cinco aperturas, mediana 409,30 ms | [Diagnóstico remoto](S15_DIAGNOSTICO_REMOTO.md). Dyno temporal, TLS y rollback; excluye HTTP, BFF y capacidad sostenida |
| Flujos desde navegador | **9/9 PASS**, F1…F8 y dashboard; conciliación SQL **63/63 PASS** | [Recorrido nativo](S15_FLUJOS_NAVEGADOR.md). UI/BFF/API real local, Storage sintético; HMAC/canario, historial, evidencia y canje comprobados |
| Navegación descriptiva | **18/18 observadas**, tres rutas × dos perfiles × tres repeticiones | [Medición de pantallas](S15_RENDIMIENTO_FRONTEND.md). Ventana 8 s y proxies explícitos; datos móviles visibles a 2,14–3,16 s, sin certificación de campo |
| Canjes positivos complementarios | **3/3 HTTP + SQL**, saldo 1.010→10; tres duplicados rechazados y diez tablas sin cambios | [Suplemento de rendimiento](S15_INFORME_RENDIMIENTO.md). Mundo nuevo, N=3 exploratorio, impacto intacto y notificaciones pendientes |
| Identidad y privacidad | 220 casos de identidad dentro de la suite global; esquema y canario local comprobados | [Matriz técnica](S15_INFORME_IDENTIDAD.md). Alcance de superficies explícito; no readjudica HT-3 |
| IA | Formatos, ejecutor offline y recuperación implementados; sin llamadas de pago | Fotos inéditas y oro humano aún pendientes; no existe resultado nuevo de concordancia |
| Seguridad y operación | Revisión ampliada de instrumentos; proxy heredado corregido | [Informe de seguridad](S15_REVISION_SEGURIDAD.md). Rotaciones de proveedor, Google y correo reales siguen pendientes |

## Compuertas del árbol de implementación

| Comprobación fresca | Resultado |
|---|---|
| Backend de dominio | **2.349/2.349 PASS**, 225,49 s; producto **94,46 %**, 7.351 sentencias y 407 sin cubrir |
| Cobertura crítica HT5-01 | **96,91 %**, 2.233 sentencias y 69 sin cubrir; mismo archivo de cobertura, universo congelado separado |
| Ruff backend / formato / migraciones | PASS; 462 archivos formateados; sin cambios de migración pendientes |
| Custodia GOV-T054 | **1/1 PASS** con selección exacta; no se presenta como repetición completa del aparato experimental |
| Auxiliares S15 / IA offline / carga Node | **43 / 28 / 37 PASS**, sin omisiones y sin llamadas a proveedores |
| Ruff y formato de herramientas Python | PASS; 27 archivos formateados |
| Comandos Gunicorn | Dockerfile y Heroku equivalentes tras normalizar puerto; parser real Gunicorn 22 confirmó gthread/4, un proceso por defecto y timeout 30 s en contenedor sin red |
| Revisión independiente | [Sin Critical/Important pendientes](S15_REVISION_IMPLEMENTACION.md) en los ámbitos revisados; ejecución de navegador y proveedores se comprueba aparte |

El ajuste de formato de una prueba Django conservó su AST exacto; no cambió reglas ni
expectativas durante la verificación. Las suites y sus denominadores permanecen separados;
no se suman los controles de los revisores como nuevos casos de producto. Los comandos
Gunicorn se comprobaron con una aplicación WSGI estándar: ese control no equivale a
reconstruir o publicar la imagen de producción.

La revisión independiente cerró dos hallazgos importantes del instrumento: el reporte de
carga ahora conserva el SHA-256 de los bytes exactos desde preflight hasta k6/SQL; el
verificador detecta alteraciones de ubicación/SRID/institución. Las pruebas rojas y las
corregidas quedan separadas. Las negativas API comparan contenido de nueve tablas, para
detectar también modificaciones y borrados con igual cantidad de filas.

## Consolidación técnica

Los carriles locales previstos cuentan con resultados e índices privados por ejecución.
La revisión final reprodujo el fallo JSONB del postverificador y contrastó las 18
muestras de navegador con el informe, sin Critical/Important nuevos. El CI de auxiliares
cubre cambios en cualquier módulo backend, incluidas sus dependencias de fotos/IA.
El [índice de validación](S15_INDICE_VALIDACION.md) conserva 143 artefactos comprobados
y la huella de 810 fuentes. Documenta un timeout de worker posterior al corte de
carga: las series medidas conservan sus hashes, pero la estabilidad prolongada del
ajuste de runtime debe comprobarse antes de publicarlo. No se desplegó.
La [matriz de evidencia](S15_MATRIZ_REQUISITOS_EVIDENCIA.md) conserva los denominadores
separados, los intentos fallidos y los límites. La integración de la rama y su CI se
registran en el índice técnico; S15 permanece abierto por las entradas siguientes.

## Entradas y comprobaciones que siguen pendientes

- Excel/ZIP y fotos autorizadas que el usuario ya preparó. Objetivo de colección:
  **120 incidencias distintas, 100 reservadas y 20 de piloto/pruebas/demo**; reparto
  por categoría y prioridad en [decisiones](S15_DECISIONES_Y_PENDIENTES.md).
- Etiquetas humanas de referencia de categoría/institución/prioridad, con dos revisores
  y resolución de desacuerdos antes de observar la IA. Las sugerencias de Claude se
  verifican; dos fotos de una misma incidencia no cuentan como dos casos independientes.
- Piloto separado para observar tokens/costo y después evaluación reservada. Presupuesto
  total USD 5–10, con reserva para pruebas y demo; esta etapa no lo consume. La suficiencia
  se comprueba con uso real del piloto y precios vigentes antes de la corrida.
- Cuentas reales para Google, roles institucionales/admin y dos ciudadanos, más un
  recorrido remoto acotado, respaldo coordinado y observación de correo real. El
  [procedimiento operativo y demo](S15_OPERACION_Y_DEMO.md) deja preparado el orden.
- Rotaciones de consola asignadas al usuario en S14-D19 y sus comprobaciones posteriores;
  no cambiar rutinariamente la clave HMAC de identidad al conservar perfiles existentes.
- Ensayo de diciembre con un snapshot propio si cambian mes, ranking o cupo; fecha límite
  de entrega del documento aún por concretar. La defensa se prevé aproximadamente el
  **16/12/2026**.

S15 se cerrará cuando termine su trabajo técnico y sus pendientes se resuelvan o se
registren mediante una decisión explícita de alcance. S16 y la actualización de la tesis
conservan su orden: **incorporar el `.docx` después de completar los 16 sprints**.
La falta actual de fotos o cuentas no bloquea los carriles independientes anteriores.
