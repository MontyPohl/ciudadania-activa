# S11 — Incidente de continuidad HMAC y rebaseline de custodia

**Fecha del incidente:** 21/09/2026

**Baseline documental:** `main@08262a25221b631abf9a55ade57d40494d567571`

**Clasificación:** `KEY_CONTINUITY_FAILURE`

**Decisión humana:** `DECISIÓN C`, emitida por Monty Emmanuel Pohl Amarilla como autor y
custodio.

**Alcance:** registro documental y operativo. No es un resultado experimental, no cambia
`S11A-v1.0`, no autoriza un run y no modifica producción.

## 1. Contexto y diagnóstico

El diagnóstico `S11_HMAC_DIAGNOSTIC_COMPLETE`, realizado antes de continuar S11-C2, comprobó
que la clave configurada actualmente como `S11_CUSTODY_HMAC_KEY` no verifica las firmas HMAC de
la evidencia histórica de P02/P03 ni de C1. El secreto documental
`S11_DOCUMENT_HMAC_SECRET` tampoco las verifica. No existe un `key_id` histórico para la clave
custodial, ni una copia de esa clave en el repositorio, la configuración local o los metadatos
autorizados que fueron inspeccionados. El runtime vigente admite una sola clave y no implementa
keyring ni versionado histórico.

La clave pública Ed25519 de aprobación permanece válida y su fingerprint no secreto es
`8c1e5a8ad7390f4d`. Este incidente afecta la continuidad de autenticación HMAC custodial; no
demuestra corrupción ni falsedad de los archivos.

## 2. Artefactos históricos afectados

Los artefactos permanecen en sus rutas originales y no deben regenerarse, resignarse,
sobrescribirse, eliminarse ni moverse destructivamente.

| Evidencia | Ruta original | SHA-256 del JSON | Integridad de bytes | Autenticidad HMAC histórica | Estado documental |
|---|---|---|---|---|---|
| P02/P03, cierre | `var/s11-runs/custody-verifications/s11-custody-20260921-p02p03-v5/complete.json` | `6891300d407d2666c25c3eedfcc4b4a5d7057a5ea9d6e299df0b4f0baf6fbd32` | Verificable | No verificable con las claves autorizadas disponibles | `HISTORICAL_UNVERIFIABLE` |
| P02/P03, atestación | `var/s11-runs/custody-verifications/s11-custody-20260921-p02p03-v5/attestation.json` | `91a5a5b5f02a7f4d60d77f1034b6ad50141508d2761a0a5811b0dd66d0c9b617` | Verificable | No verificable con las claves autorizadas disponibles | `HISTORICAL_UNVERIFIABLE` |
| C1, cierre | `var/s11-runs/custody-verifications/s11-c1-20260921-isolation-repro-reset-v1/complete.json` | `c5297c05a79a8a95e704edf55719b456de2fd14cbea33e72b09b5d1d26085276` | Verificable | No verificable con las claves autorizadas disponibles | `HISTORICAL_UNVERIFIABLE` |

Los SHA-256 y sus sidecars permiten comprobar la identidad de bytes preservada. No sustituyen
la autenticidad HMAC histórica. Por ello, estos archivos se conservan como registro del incidente,
pero no pueden presentarse como evidencia criptográficamente atestada para los gates actuales de
S11.

## 3. Decisión C

Monty Emmanuel Pohl Amarilla decide:

1. declarar la evidencia anterior de P02/P03 y C1 como `HISTORICAL_UNVERIFIABLE` por
   `KEY_CONTINUITY_FAILURE`;
2. conservarla sin modificación como registro histórico;
3. establecer una nueva época de custodia HMAC para S11;
4. repetir primero P02/P03 y después C1 con una nueva clave custodial estable;
5. mantener EX-02 y EX-03 bloqueados hasta verificar la nueva evidencia.

No se inventa una firma criptográfica para esta decisión. La metodología `S11A-v1.0` permanece
intacta y continúa vigente la **OPCIÓN A**: EX-02 y EX-03 son runs independientes, cada uno con
su propio reset, reporte y evidencia; EX-03 no reutiliza el reporte de EX-02.

El intento previo de S11-C2/EX-02 queda registrado como
`S11C2_EX02_NOT_DEMONSTRATED`. Su causa es de infraestructura/custodia y no constituye `PASS` ni
`FAIL` funcional. No se creó un run C2 y el experimento no comenzó.

## 4. Nueva época de custodia

La nueva época no entra en vigor hasta que Monty configure una nueva
`S11_CUSTODY_HMAC_KEY` estable mediante el mecanismo local aprobado. Esta tarea documental no
genera la clave ni modifica `.env`.

La clave de la nueva época debe:

- permanecer fuera de Git, artefactos, logs, informes y salidas de terminal;
- almacenarse en `.env` con permisos `0600`;
- ser la misma durante la repetición de P02/P03, la repetición de C1 y todos los runs S11
  posteriores;
- conservarse hasta terminar la retención aprobada de todos los artefactos que autentique.

La nueva época no resigna ni reautentica la evidencia histórica y no introduce un keyring en el
runtime. Antes de la próxima ejecución también queda pendiente que Monty agregue manualmente
`S11_HOST_GID=1003` a `.env`; el valor proviene de `id -g`, no se versiona y no fue escrito por
este registro.

## 5. Gates y secuencia obligatoria

| Componente | Estado posterior al registro |
|---|---|
| S11-A | `PASS` |
| S11-B | `PASS` |
| Metodología Opción A | `PASS` |
| Diagnóstico HMAC | `COMPLETE` |
| P02/P03 | `REPEAT_REQUIRED` |
| C1 | `REPEAT_REQUIRED` |
| EX-02 | `BLOCKED_PENDING_CUSTODY_REBASELINE` |
| EX-03 | `BLOCKED_PENDING_CUSTODY_REBASELINE` |

La secuencia obligatoria siguiente es:

1. configurar la nueva `S11_CUSTODY_HMAC_KEY` estable;
2. configurar localmente `S11_HOST_GID=1003`;
3. repetir P02/P03;
4. verificar la nueva evidencia P02/P03;
5. repetir C1;
6. verificar la nueva evidencia C1;
7. sólo entonces evaluar el desbloqueo de EX-02.

No se avanza a S11-C3 mientras esta secuencia siga incompleta.

## 6. Preservación de alcance

Este registro no cambia objetivos, hipótesis, indicadores, fórmulas, escenarios, denominadores,
criterios `PASS`/`FAIL`/`NOT_DEMONSTRATED`, la matriz de eventos, el harness ni la independencia
de EX-02 y EX-03. No ejecuta experimentos, no levanta Compose, no crea evidencia C2 y no modifica
DB, Redis, storage ni despliegues productivos.

## 7. Gate documental

`S11_HMAC_CUSTODY_REBASELINED`
