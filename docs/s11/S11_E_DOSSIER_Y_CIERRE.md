# S11-E — Dossier y cierre

Estado: **S11 cerrado el 19/10/2026**, con EX-10 `NOT_DEMONSTRATED`. Adjudicación completa en [S11-D](S11_D_ANALISIS_Y_DECISIONES.md).

## Paquete de referencia

- Raíz local de ejecución autónoma `R` = `s11-autonomous-execution-20261005T075433Z` (ledger, telemetría, checkpoints, candidatos y readbacks). No se publica en el repositorio.
- Runs históricos: `var/s11-runs/runs/<run-id>/` (local, no versionado).
- Revisión fuente del aparato EX-10 usada en W22: `main@28b417982e9e04be5b7e50e164732c8e6cf45e03`, árbol `939e8f6d0adb372f76ccda08e5b6be16f2a86b3b`.
- Primera medición HT5: `c45a78c949fbc70b67cef2eb762ee2916f0257ea`.
- Clasificación de confianza: `LOCAL_SINGLE_HOST_LIMITED_ASSURANCE`.

## Resultado de cierre

| Elemento | Estado final |
| --- | --- |
| S11-A | Congelada en `main@35f850eb24df552805fe8694f50fcbb3588a4141` |
| S11-B | Instrumentación preparada |
| S11-C2 | Cerrado: EX-02…EX-08 en PASS |
| S11-C3 | EX-09 FAIL válido (19/28); EX-10 `NOT_DEMONSTRATED` (run 1 no vinculante; W22 sin run nuevo) |
| S11-D | Cerrado: HT5 PASS; HT2 FAIL; HE, HT1, HT3, HT4, HT6 `NOT_DEMONSTRATED`; HTP no PASS |
| S11-E | Cerrado con este dossier |

## Evidencia W22 de EX-10

Ubicaciones relativas a `R/`.

| Evidencia | Ubicación | SHA-256 | Alcance |
| --- | --- | --- | --- |
| c47 CP2 incierto | `01-work-units/checkpoint-c47-recovery-uncertain-v3347/checkpoint.json` | `2ac95c3d0c47f1d89ded4ea43831c3409280b63aa1e8ff23db22e75709a4dabb` | v3346: envelope y preflight PASS, bridge `native-attachment-local-client`; CP2, sin run ID. Registra además una exposición de valores de entorno en salida de herramienta (ver Limitaciones). |
| c51 v3399, fallo previo a la acción nativa | `09-preexecution/W22-c51-official-candidate-v3399/INCIDENT_LATE_GRAPH_PIN.json` | `b42cf7e174ef675aa629eb9ce33b4e98348688a9eef80bac6a69ece8abe0e551` | Pin obsoleto de la policy instalada; CP1 intacto, sin efectos. |
| c51 v3400, incidente nativo | `09-preexecution/W22-c51-official-candidate-v3400/INCIDENT_NATIVE_ENVELOPE_BINDING.json` | `eee56ea17caf8fb44fa4acb6bac6e3e00aff3ebdc4633b5be4b5cdd027cc750d` | `fixed-native-envelope-binding-invalid`. |
| c51 v3400, relectura | `09-preexecution/W22-c51-official-candidate-v3400/POST_ATTEMPT_READBACK.json` | `c52ffa0ca0d45c1ce3a383b2a4654451534dcc548b27a55d6631c157c170c2b1` | CP2 `UNCERTAIN`; B5 con 38 raíces frente a 40 registradas; autoridad intacta. |
| c52 v3403, propuesta y manifiesto | `09-preexecution/W22-c52-official-candidate-v3403/REVIEWABLE_PROPOSAL.md`; `static-manifest.json` | `ca4bdc4a218615e9733905586b1d315eaa3b86e6d02e091c65a78f55e40cf474`; `d8ee2b9114134f0b62c2f4b3490abd1a2f54e1fd903e1224ddc934cdc4f70797` | Sesión técnica nueva c52 y candidato con prechequeo de contexto en el host. |
| c52 v3403, relectura | `09-preexecution/W22-c52-official-candidate-v3403/POST_ATTEMPT_READBACK.json` | `3e46ffbc065537e28777531582198d5552dc04c534e19958c92548021469f0ff` | `EXPIRED_OBSERVATION` → `native-bridge-composition-denied`; CP2 `UNCERTAIN`; autoridad intacta. |

En las tres sesiones c47, c51 y c52 la autoridad conserva los bytes de la génesis: B3 `056e5a7a…`, B4 `1cc05aa4…` (sin reserva) y witness `e16b46b1…`. Las sesiones se conservan en CP2 y solo admiten `inspect`.

## Índice de evidencias HT5 y OD07-A

Ubicaciones relativas a `R/09-preexecution/`.

| Evidencia | SHA-256 verificado |
| --- | --- |
| `W24-prospective-OD07-A-freeze-v3118/decision.json` | `c8792cfeb6a608600cf48ad7f83e9aeed90db1d3c9107837754ddcda5c25cded` |
| `W24-prospective-OD07-A-freeze-v3118/approved-ht5-manifest.json` | `567a030370cb2cd4a1bf84739a7359dc22586de03f02aa6e097c09f691ce5c8d` |
| `W25-prospective-critical-test-selection-v3125/test-selection.json` | `20dbfd919bb26ba57d02cd558ecf7622440c2a0a704c1ff0f3fa84f4d90d153c` |
| `W25-critical-coverage-measurement-v3128/measurement-result.json` | `0a272067259c510b8a8de47cbc21c42f61d4e823580fec8a4de2d269f38a387e` |
| `W25-coverage-and-fixture-adjudication-v3135/adjudication.json` | `6104634ec5331d90877df241d9f9d14d84372f29514ca3aeff9adb5a2e20c4a3` |
| `W22-c46-network-candidate-and-custody-v3130/verification.json` | `7e1142af3ad14ce284f2d16e10567a0af563a180de3a51f88765eecc3fbf3dc5` |

## Manifiestos históricos conservados

Hashes verificados contra los archivos locales el 19/10/2026. El índice acredita los bytes; los resultados se apoyan en las decisiones históricas y no constituyen una nueva ejecución.

| Experimento | Resultado | Manifiesto final (`var/s11-runs/runs/…/run-manifest.final.json`) | SHA-256 |
| --- | --- | --- | --- |
| EX-02 | PASS histórico C2 | `s11-4df882ed-9dfe-59a2-888b-ca1efc98d6af` | `7f2df5fa98324d5c6b3783ad52ded4989aa2ff4bb19b0af087446e7f70eb8225` |
| EX-03 | PASS histórico C2 | `s11-6dc3ea66-08c9-51ef-9fe3-70e7dcc67959` | `09355cba0c1fc533caef48e9a2aa8cce2fc988041ec3489d825644c73f71c79a` |
| EX-04 | PASS histórico C2 | `s11-03adb9bc-c9e4-5f8f-b64c-c4360d0c2085` | `6cb9cccc5b69af0e02625ad4d87396649e5a5ef0a51a3869bb74f8e188dc9322` |
| EX-05 | PASS histórico C2 | `s11-0e2feac0-6583-513c-a6aa-33430394d14a` | `39d930f729777a32be8ca031231c21d5724ce8113c244975840baf9ee837931b` |
| EX-06 | PASS histórico C2 | `s11-989218f3-4808-5c6f-ac23-6e8870fec153` | `9682ff0b3245338c52a2dfc794740ec3118f417cac9cc53a7042b8dfd66098f7` |
| EX-07 | PASS histórico C2 | `s11-545491b8-01fe-55e6-8f08-1a0ebbdca8d3` | `a6d551a43c5e96312827532099998303008f03cc74127c6fc8c399361e202bb0` |
| EX-08 | PASS histórico C2 | `s11-b567289c-9fe2-5349-a384-e9284bdde863` | `a7cbf70f65f199e09ac721a67e0b73741fe74644d18f5d1028ba294e0b515226` |
| EX-09 | FAIL válido, 19/28 | `s11-a156a68c-c422-5e57-ae2b-3336d3b55e6c` | `06eea245e8ce1e08413c3e194be69727ea7894e5e1a3d09984a758fb21b55c58` |
| EX-10 | `NOT_DEMONSTRATED`; `binding=false` | `s11-0ff9a5d1-2309-5f40-b4a7-dabad703e43a` | `ed6322beb3177468595ea014b2b79614fbebfe19643edb8619f52c9213933384` |

## Limitaciones y pendientes posteriores al cierre

- **Confianza local.** Toda la autoridad, witness y copia del ancla residen en una sola máquina (`LOCAL_SINGLE_HOST_LIMITED_ASSURANCE`).
- **Validación global.** HT5 mide 33 fuentes críticas con 81 archivos de tests; no equivale a una CI completa en PASS. La CI actual ejecuta las pruebas backend en un único job, y las suites del aparato EX-10 no caben en el límite de 6 h: hay que separarlas antes de publicar estos commits.
- **Rotación de secretos.** El 19/10/2026 los valores de `S11_CUSTODY_HMAC_KEY` y `DOCUMENT_HMAC_SECRET` del entorno local de contenedores aparecieron en la salida de una herramienta de diagnóstico. No se copiaron al repositorio ni a la evidencia. Con la custodia de S11 cerrada, corresponde evaluar y rotar ambos valores (y comprobar si alguno coincide con el de producción).
- **Recursos locales.** Quedan contenedores detenidos de diagnóstico y las sesiones CP2 conservadas; cualquier limpieza debe inspeccionar cada recurso y nunca borrar la autoridad W18, sus claves ni volúmenes.
- **EX-10 futuro.** El rediseño de la apertura (refrescar la observación firmada antes de componer el bridge) queda como trabajo futuro; un nuevo intento requiere sesión nueva y aprobación exacta.

Los resultados FAIL válidos, las hipótesis no demostradas y las limitaciones de confianza se informan explícitamente. No se afirma PASS para rellenar evidencia faltante. No se modifican el prerregistro congelado, la tesis DOCX ni el Anexo G.
