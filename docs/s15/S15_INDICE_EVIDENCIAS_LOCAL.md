# Sprint 15 Índice de evidencias locales

**Corte:** 20/10/2026. **Alcance:** preparación local, con datos sintéticos.
El [informe](S15_INFORME_PREPARACION_LOCAL.md) define los resultados y sus límites.
Este índice identifica bytes preservados; sus hashes detectan cambios, no constituyen
una firma ni certifican por sí solos la procedencia de las mediciones.

Los localizadores son relativos a destinos privados fuera del checkout:

- **O:** `~/.local/share/ciudadania-activa/s15/20261020/out/s15/`.
- **R:** `~/.local/share/ciudadania-activa/s15/20261020/restoration/out/s15/`.

No se publican JWT, claves, fotos, filas SQL ni URLs firmadas. Los objetos se enumeran
por hash en el manifiesto privado. Los exports de sesiones y el archivo de entorno
sintético se conservan privados y se excluyen de este índice.

Las corridas de creación y restauración preceden al último refuerzo del instrumento;
se mantienen sus versiones y alcances observados. La huella final se registra por
separado: no se atribuirá retroactivamente a todas las corridas anteriores.

| Raíz | Artefacto relativo | Alcance | SHA-256 |
|---|---|---|---|
| O | `s15-functional-v1-2026-10-20-15-36/manifest.json` | Manifiesto del mundo funcional de 36 reportes | `5270cc85378ae9ddb681199da16e660f3d5fdcf9ee7e4dfc0e2ea97ed0d71f3a` |
| O | `verification-final-hardened.json` | Verificador final: 1.257 controles | `eca0a503a6b213728ad761fcdd2d2054198b94cfa39d369634be62b7af33b7cb` |
| O | `replay-final.json` | Replay previo readonly; conservar versión/alcance de su salida | `2f2431e01ba9dadae8c6c4c92aded06d0b086bf0d73ea35a4ef71f9b354244a6` |
| O | `dataset-final.dump` | Respaldo lógico coordinado con los objetos privados | `69f659a257feab17f9de94a7dc0fbd3c6d0c7557b4de0ea97730a34a448f19c7` |
| R | `s15-functional-v1-2026-10-20-15-36/manifest.json` | Manifiesto copiado al restaurar; después se consumió esa copia | `5270cc85378ae9ddb681199da16e660f3d5fdcf9ee7e4dfc0e2ea97ed0d71f3a` |
| R | `restored-verification.json` | Restauración antes del HTTP: verificador anterior, 1.225 controles | `bbdbc18a420013f1a756c3be798b64496c32cd3ca85314a36ac620ae581ad0f3` |
| O | `junit-domain.xml` | Suite de dominio final: 2.347 pruebas | `1f7dade8f8b4c5fd32b8025216e75613793c09424a8a60b9f1ebb743b5421fa9` |
| O | `domain.log` | Suite final y cobertura de producto | `7acafe5c25d20d77f9f0b011f6fdba5f0d904222fd7baaf54179442ff4f8cd68` |
| O | `coverage-ht5.log` | Mismos datos; reporte del universo crítico congelado | `90e2a36256803180559481b2821ccedbfaa49342413e069c92dd70b02fa90634` |
| O | `junit-gov-t054.xml` | Custodia: selección exacta de GOV-T054 | `c646b35a4a644505b008740e5f6b58e7db7f7e5ed1ebeb09e2b805032e1d7251` |
| O | `dataset-spatial-regression.xml` | Regresión de fixtures: 31 pruebas | `b53c5888ae6eafea43b2c2330abbdfa757563c08563e39e676b4b60169aac6d8` |
| O | `unittest-ia.log` | 28 pruebas offline de evaluación | `7ce2d52cf6ea0d8d95d6e3030dda3beafbddada50578de084c002b06c47cea59` |
| O | `unittest-storage-sql.log` | 11 pruebas Storage y reconciliación | `e5f2b394e5bcf1961da9b56539658920d35bb7515d046f348b93dac22cf13910` |
| O | `unittest-performance.log` | Pruebas finales de contratos Node; cantidad en log | `f703c9c821a55335c97204a4f78421d06968c7896d1f8405cf7efcf212068f96` |
| O | `ai-cli-final-21194ea091ba/cli-smoke.json` | CLI IA final sin red, fixture sintético 1/4 REPLAY_ONLY | `c8df8a57a17378899de7c658dd712869f18fa74c05c0a7c058d7520016d2e416` |
| O | `source-fingerprint-before-read-fix.json` | Huella del código antes de corregir igualdad JSON de las lecturas | `717194490c19ebabfc9a46fd94d5b307cec6a1fee278d088747d653806f6483b` |
| R | `http-smoke-1/manifest.json` | Entradas congeladas del ensayo de creación | `d2525757528523e6d3cb5d8fa6de83b3a3bf7daef500267e093c1725bace4dbe` |
| R | `http-smoke-1/window-1/execution.json` | Versión k6 y alcance local | `240ca9311d066033f20baff9d1a1e95f4f131dd293950981013b14d11bb0c8be` |
| R | `http-smoke-1/window-1/series.jsonl` | Serie íntegra de creación | `022ca77f1d1dfc32482ce32d624f0cb40329fec328bd8d70336bc492ab7c735c` |
| R | `http-smoke-1/sql-before.json` | Baseline SQL readonly | `3dd1a9aab0af04b947594d4eea84591e8c770347818ff50e7a3eb4586cca42da` |
| R | `http-smoke-1/sql-after.json` | Observaciones SQL readonly posteriores | `841837b7f11155a1db5da2c2f7a3d888370726dcfb3d08fde2e4623a617ac9b6` |
| R | `http-smoke-1/reconciled-report.json` | 50 persistencias reconciliadas dentro de ventana | `5471754fae44273edfe74365851560a69fa2c70d73de47cb7ae71fd0d0b0047d` |
| O | `domain-first-attempt.log` | Intento fallido conservado: fixtures y montaje readonly | `3385aa1584ddd40a3ae54769cff8c127ed7df4deb53c57d44bc5494593fe0f41` |
| O | `unittest-performance-umask-first.log` | Intento fallido conservado: fixture de permisos dependía de umask | `d00a9be9959c766f51b546e73a0bf066a78d13c40be9cbe1641a5e8e921aaea8` |
| R | `initial-readiness-race.log` | Fallo inicial de readiness del destino de restauración | `b803b0f9054628be73e183c54c705f307c4b54f84bcebaaa7ad3f416a12630eb` |
| O | `source-fingerprint.json` | Huella final de 54 archivos de instrumentos, comandos, tests y CI | `004eba5b7e18de50101f7c8e8343690ac6ed790dbd50341f8b726b978a09c391` |
| R | `read-smoke-20261020/sql-before.json` | Oráculo SQL de lecturas congelado antes del HTTP | `e12d8369f24344acbce69ccbad37c689e7b244b007dc7fe3926807bd24658591` |
| R | `read-smoke-20261020/map-manifest-fixed.json` | Entradas del mapa corregido, mismo oráculo | `3d5cb687208faec9114dc5d9a6acf02d990e52bda27b108ea53d05b9b83e0eaa` |
| R | `read-smoke-20261020/map-window-2/report.json` | Mapa final: 90/90, cero errores | `91dc2d1acc376f1d4f9e23b99e52de6f4d9fb2b75fa10bcd949bc65f09d16f1d` |
| R | `read-smoke-20261020/map-window-2/series.jsonl` | Serie íntegra de mapa final | `00085fd58fbcb419a40d45ff5eca99be5223c0bfc478ff2ac4ca94793d5f1400` |
| R | `read-smoke-20261020/ranking-manifest.json` | Entradas de ranking | `f363dadf8281d93e8f567f1a230119f35cd0f67dd3d3a4baf3586e61c42dc7f1` |
| R | `read-smoke-20261020/ranking-window-2/report.json` | Ranking final: 90/90, cero errores | `a8c95c73a13bb329ae7df9e3eaa8e8b757b99604980c332b4e237c99e8260348` |
| R | `read-smoke-20261020/ranking-window-2/series.jsonl` | Serie íntegra de ranking final | `47731c2c24a02d5721a131192077f3ef007fa5d994d3a6537cf6632c574a2fd6` |
| R | `read-smoke-20261020/READ_SMOKE_REPORT.json` | Comandos, SQL antes/después, privacidad, presupuestos y límites de lecturas | `bafae9f2c3e04435a4446a83fd6b928864f3caeda42ebd7f9bbe589edb2e149b` |
| R | `read-smoke-20261020/ARTIFACT_INDEX.json` | Índice privado de las lecturas, incluidos los intentos excluidos | `4a28ccba3fa5b034546cec67d90b4af1481763f3a2fd01ab83f0bbc83a48e620` |
| R | `read-smoke-20261020/instrument-invalidity.json` | Mapa inicial INVALID_INSTRUMENT por igualdad JSON | `71a7811317a4ba10b341c702e976fb719587c83a8cf24186625d4d0acc1c1af1` |
| R | `read-smoke-20261020/diagnostic-object-equality.json` | Diagnóstico del orden de claves sobre el mismo body | `3530d91e3577c882358420b432425396fa5f76812cb8c642f398e6ca1d4e7ce4` |
| R | `read-smoke-20261020/ranking-k6.log` | Aborto previo a HTTP por wrapper sin permiso de ejecución | `37fec732943c5355ed36b5b2e93c0e81ca88bf0193162819523560e670563db7` |

Se conserva el mundo original de 36 reportes. El destino restaurado contiene después
las 50 creaciones del smoke; no se presenta su estado posterior como una restauración
intacta del snapshot original. Los checkpoints lógicos del generador no son respaldos.

La evidencia no demuestra proveedores externos, Google/Brevo reales, workers drenados,
concordancia IA, carga productiva ni cierre de S15. La suite experimental completa y
las verificaciones frontend no se ejecutaron como parte de estos cambios de herramientas.
