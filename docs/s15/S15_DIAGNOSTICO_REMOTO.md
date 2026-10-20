# Sprint 15 — diagnóstico Heroku ↔ Supabase

**Fecha observada:** 20/10/2026, 16:54:43 America/Asuncion. **Resultado:** ejecución correcta de un diagnóstico acotado de solo lectura en el despliegue existente. No requiere un segundo entorno remoto y no cierra por sí solo los requisitos de rendimiento de S15.

El instrumento [remote_db_probe.py](../../tools/s15/remote_db_probe.py) se ejecutó en un dyno temporal de la aplicación existente, con los ajustes de producción. Antes de conectar exigió un destino Supabase, `DEBUG=False`, timeout de conexión de cinco segundos y TLS al menos `require`. Cada bloque abrió una transacción `READ ONLY`, usó `statement_timeout` local de cinco segundos, confirmó el estado de solo lectura y terminó con rollback. La revisión independiente corrigió previamente el uso de configuración de sesión, incompatible con el pooler en modo transacción.

Sólo se consultaron constantes, versiones y configuración de la propia sesión: `SELECT 1`, versiones PostgreSQL/PostGIS y confirmación de `transaction_read_only`. No se consultaron filas de negocio, no se cambiaron datos ni configuración persistente y no se invocaron proveedores de IA o correo. El script no imprime credenciales, hosts, DSN ni datos de personas.

| Indicador | Resultado observado |
|---|---|
| Consultas de calentamiento excluidas | 3 |
| Consultas medidas `SELECT 1` | 30 |
| Mediana de consulta | 66.18 ms |
| P99 descriptivo de consulta | 66.42 ms |
| Aperturas de conexión medidas | 5 |
| Mediana de apertura | 409.30 ms |
| Solo lectura confirmado | Sí, en cada transacción |
| TLS del cliente | `require` |
| Versiones observadas | PostgreSQL 17.6; PostGIS 3.3.7 |
| Salida del proceso remoto | 0 |

El percentil usa interpolación lineal sobre `(N−1)×p`. Las series completas quedan en el recibo privado. Con 30 consultas consecutivas, ese P99 describe esta muestra y no estima una cola poblacional bajo carga. La apertura incluye negociación y autenticación del cliente y puede alcanzar un pooler; no equivale necesariamente a abrir un nuevo proceso PostgreSQL.

La medición incluye driver, red/pooler y trabajo de la base: **no es RTT puro de red**. Procede de un dyno temporal, no del proceso web activo, y excluye navegador, BFF, HTTP y consultas funcionales. No demuestra capacidad sostenida ni adjudica RNF-01/02 o HT-1. Las tres ventanas locales y los flujos API/SQL se documentan por separado.

## Custodia

Artefactos privados duraderos, fuera del repositorio, bajo `~/.local/share/ciudadania-activa/s15/20261020/remote/`. No se publica el log bruto.

| Artefacto | SHA-256 |
|---|---|
| Instrumento ejecutado | `699b930bc3117406b2dafa6413015e2287d13d4aceae1d3cbbc7f606ebe9be88` |
| `heroku-db-probe-001.json` | `9757fd5377d95d3f08ba7b24f26b762b9739cdf531c4392fc947d0b651d323fc` |
| `heroku-db-probe-001.log` | `0afdcacb55689623ed47c41dad5de8a88cf8d2c0c071b0e781c5def956cbcaf7` |

La revisión estática independiente no halló Critical/Important pendientes en el alcance del instrumento después de la corrección. El recibo acredita la ejecución real y su guard de solo lectura; no sustituye una auditoría completa del despliegue ni pruebas de proveedores externos.

## Smoke HTTP público del despliegue existente

Se efectuó además un GET HTTPS acotado por ruta: health y categorías de la API,
login, mapa, ranking y transparencia del frontend. Las seis respuestas fueron HTTP 200;
health confirmó `status=ok`, DB y Redis disponibles, y el catálogo devolvió diez
categorías. Las páginas entregaron HTML. No hubo autenticación, escrituras de negocio,
Google, IA o correo. Estas respuestas corresponden al árbol que ya estaba desplegado,
no publican ni verifican los cambios locales S15.

El primer control comparó health con un objeto de tres campos y omitió el campo
`version` del contrato existente: produjo un falso fallo del instrumento. Se conservó
`public-http-smoke-001.json`; se revisó la vista de producto y la corrida 002 comprobó
los cuatro campos, sin modificar el endpoint. La comprobación corregida pasó las seis
rutas. No se agrupan tiempos de una solicitud por ruta para presentar P50/P99.

Recibo privado `public-http-smoke-002.json`: `d2c46c32e6fde4e6a64828d56bc9de7bfd9cc451887d12240f571a8c83d8f8fe`. Instrumento privado de ese smoke: `44550bead3e95698d1e376d2cae048f2dc9609f2505e077864d7bd082e785ff1`.
