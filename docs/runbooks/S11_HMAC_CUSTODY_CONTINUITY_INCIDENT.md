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

La nueva época entró en vigor para la repetición P02/P03 y se mantuvo durante la repetición C1
cuando el runtime cargó la
`S11_CUSTODY_HMAC_KEY` estable configurada mediante el mecanismo local aprobado y comprobó su
fingerprint no secreto `7205a04f93cc3da9`. Las repeticiones no generaron la clave ni modificaron
`.env`.

La clave de la nueva época debe:

- permanecer fuera de Git, artefactos, logs, informes y salidas de terminal;
- almacenarse en `.env` con permisos `0600`;
- ser la misma durante la repetición de P02/P03, la repetición de C1 y todos los runs S11
  posteriores;
- conservarse hasta terminar la retención aprobada de todos los artefactos que autentique.

La nueva época no resigna ni reautentica la evidencia histórica y no introduce un keyring en el
runtime. Para las repeticiones P02/P03 y C1, `S11_HOST_GID=1003` se exportó sólo al proceso a
partir de `id -g`, como permite el runbook; no se versionó ni se escribió en `.env`.

## 5. Gates y secuencia obligatoria

| Componente | Estado actual |
|---|---|
| S11-A | `PASS` |
| S11-B | `PASS` |
| Metodología Opción A | `PASS` |
| Diagnóstico HMAC | `COMPLETE` |
| P02/P03 | `PASS` |
| C1 | `PASS` |
| EX-02 | `NOT_EXECUTED — REQUIERE PRE-GATE SEPARADO` |
| EX-03 | `NOT_EXECUTED — REQUIERE PRE-GATE SEPARADO` |

La secuencia ya completada fue:

1. configurar la nueva `S11_CUSTODY_HMAC_KEY` estable;
2. configurar localmente `S11_HOST_GID=1003`;
3. repetir P02/P03;
4. verificar la nueva evidencia P02/P03;
5. repetir C1 con la misma clave custodial;
6. verificar la nueva evidencia C1.

La secuencia de rebaseline de custodia queda completa. Este cierre no ejecuta ni autoriza EX-02 o
EX-03, no declara listo su pre-gate y no avanza a S11-C3.

## 6. Preservación de alcance

Este registro no cambia objetivos, hipótesis, indicadores, fórmulas, escenarios, denominadores,
criterios `PASS`/`FAIL`/`NOT_DEMONSTRATED`, la matriz de eventos, el harness ni la independencia
de EX-02 y EX-03. Las repeticiones utilizaron únicamente Compose local aislado para P02/P03 y C1;
no ejecutaron escenarios funcionales, no crearon evidencia C2 y no modificaron DB, Redis, storage
ni despliegues de producción.

## 7. Gate documental

`S11_HMAC_CUSTODY_REBASELINED`

## 8. Repetición P02/P03 bajo la nueva época

**Fecha:** 21/09/2026

**Baseline de ejecución:** `main@5895329c2587704da4af321624082d6b9b97c38d`

**Verification/run ID:** `s11-custody-20260921-p02p03-v6`

**Ruta:** `var/s11-runs/custody-verifications/s11-custody-20260921-p02p03-v6/`

**Fingerprint custodial:** `7205a04f93cc3da9`

| Registro | SHA-256 |
|---|---|
| `prepare.json` | `8dcce577f19bb0d7b72db3015b2b460b20df54a12a84831ae640cf256e35a53b` |
| `complete.json` | `d92490f5bb1c04eaefd6795d070adfd468ccc103e4329d5c9f477d01049279c2` |
| `attestation.json` | `bb929a390171899c948bbaaeb2c1faea49397bcd15114ad09d12adace94840d2` |
| `custody-epoch.json` | `5282c4b4d5dafacfb1782b286f8f3c4e4117b876a8da42e1daf540a189af15aa` |
| `destruction.intent.json` | `d61007a41b6325a7cfe4e8b2f9cb811b5806cbaa3615fc141e193acd3b37f0e3` |
| `destruction.json` | `69995c04b65a96d92cfb4c0ba89842993e067f72918ccd6143e14ce214639eb6` |

Los seis registros y sus sidecars SHA-256/HMAC/commit fueron releídos con la clave actual. La
frontera `container-recreated`, backup, restore, comparación byte a byte, matriz nominal,
retención, gate `PUBLIC`, rechazo de `SENSITIVE` y no persistencia de `SECRET` quedaron en
`PASS`. El canario sintético fue destruido de primary, backup y restore según el procedimiento,
mientras el paquete de verificación se conservó con permisos privados.

**Evidencia nueva:** `VERIFIABLE_WITH_CURRENT_CUSTODY_KEY`

**Resultado P02:** `PASS`

**Resultado P03:** `PASS`

**Gate operativo:** `S11_P02_P03_REPEATED_PASS`

## 9. Repetición C1 bajo la nueva época

**Fecha:** 21/09/2026

**Baseline de ejecución:** `main@f0bce178a22f3a80f15619caab7a3aeaa7c63d14`

**Run ID:** `s11-8448491d-3347-5c00-9f44-d8ae110e9095`

**Verification ID:** `s11-c1-20260921-isolation-repro-reset-v2`

**Fingerprint custodial:** `7205a04f93cc3da9`

La repetición usó el entorno `isolated-experiment`, PostgreSQL
`ciudadania_s11_experiment`, Redis DB `11`, storage `/s11-objects` y un puerto host dinámico. El
dataset `s11-functional-v1`, versión `v1`, seed `20260921`, fue generado dos veces con bytes y
cantidades idénticos al artefacto versionado; su SHA-256 fue
`66074d1617c4e1b7a32b854090d26556faa9b35a000452787ff3b47d6f0edc1c`.

Los canarios controlados en DB, Redis y storage desaparecieron mediante el reset oficial. Un
segundo reset produjo nuevamente el estado aislado esperado. El manifest final quedó
`COMPLETED`, con SHA-256
`8a2c809839336d4c3c2ba5cd70bc649e2a2a6e72077ff549da40954bc1cc9a9a`, sin escenarios
funcionales ejecutados.

Primary, backup y restore conservaron los mismos siete registros, clasificación, retención,
SHA-256 y HMAC. El cierre verificable está en
`var/s11-runs/custody-verifications/s11-c1-20260921-isolation-repro-reset-v2/complete.json`, cuyo
SHA-256 es `18170777ea896e3ef8bd6b752aac40d695f84c3a652282c5c69433c18fd748f0`.
Tras la verificación se limpiaron DB, Redis y storage experimentales y se desmontaron los
contenedores, la red y el volumen de Compose S11. No se ejecutaron EX-02, EX-03 ni otro escenario
funcional; producción permaneció intacta.

**Evidencia histórica:** `HISTORICAL_UNVERIFIABLE — PRESERVED`

**Evidencia nueva:** `VERIFIABLE_WITH_CURRENT_CUSTODY_KEY`

**Resultado C1:** `PASS`

**Gate operativo:** `S11C1_REPEATED_PASS`
