# EX-10 — Operación del runtime profile y launcher v2

## Estado del candidato y procedencia

`IMPLEMENTATION_CANDIDATE` · `VALIDATED=NO` · `PENDING_GLOBAL_VALIDATION`.
Este runbook describe las rutas de código del candidato. No es una autorización para instalar
confianza, crear autoridad, calificar un deployment ni ejecutar acciones oficiales.
La validación offline registra resultados de componentes, mutantes, estrés y revisión de
seguridad por bytes y alcance en el paquete autónomo. La suite S11, la suite completa del
repositorio y la CI del árbol final siguen pendientes.
P02/P03 v10 y los anchors finales quedan pendientes de HEAD/blobs finales y autorización aplicable.

La [copia G0-A3](S11_EX10_G0_A3_RUNTIME_PROFILE_AND_CUSTODY.md) conserva **los bytes originales**
del borrador congelado, incluidos sus marcadores históricos de no aprobación. Su SHA-256 es
`bb9678bf37ee16e922c4d62322119cb455e85c6570671e63c0b85e8f4559fcc6`.
La aprobación posterior se registra separadamente; no se reescribe el estado de aquel borrador:

| Registro externo | SHA-256 | Alcance |
|---|---|---|
| G0-A3, `operator-approval.json`, 04/10/2026 06:18:42 UTC | `ccbca5333a886b5e128ca3a0719c10f0ae53a0080237ce902bbb9dad24a56c47` | Aprobación del contrato; no autorización operacional |
| Phase B, `operator-decisions.json`, 04/10/2026 06:58:02 UTC | `2f960abd9762d7a759ac6287db1ef3d30b32b279c975ec359b969f2c71d8e14f` | B01–B10; decisiones y permisos de ejecución separados |
| B4, `b4-design-manifest.json`, paquete `20261004T134618Z` | `5dd12b626864a821dc5f0e60fb762bae36a1e1941956599272e01ab49c693b1e` | Schemas, bridge, ventanas de crash, mutantes y nueve escenarios |
| GOV, `governance-design-manifest.json`, paquete `20261004T154246Z` | `30a410ec04f2b6eab2cc981e1539ff65de31982dabbf1c688b64cf2ff198d424` | Contrato del certificado y referencias B3; procedencia de diseño, sin autorización operacional |

Los archivos externos y sus registros de aprobación se conservan en los paquetes originales.
La evidencia autónoma conserva autoridad de implementación, checkpoints y `VALIDATION_BACKLOG.json`;
no se incorpora al repositorio ni se deriva aprobación de que un archivo exista.

## Componentes y dominios de confianza

B1 (`s11-ex10-runtime-profile-v1`) liga proyecto, roots, Compose/override, masks, fuente,
owner y referencias externas aprobadas. B2 (`s11c3-ex10-official-launcher-session-v2`) conserva
el header cerrado de 27 campos, snapshots encadenados y checkpoint independiente. Un checkpoint
no puede reconstruirse desde los bytes que pretende verificar. No hay conversión automática de v1.

B3 conserva autoridad histórica completa, génesis aprobado, witness/floor y certificado de
coverage/GOV. B4 usa coordinación SQLite externa, `flock` global, transacción `BEGIN IMMEDIATE`,
claims duraderos, fencing tokens y receipts. Roots o proyectos distintos comparten el slot de
la misma autoridad; la ausencia de proceso o el vencimiento de una reserva no liberan ese slot.

El bridge admite operaciones cerradas mediante frames tipados sobre pipes privados ligados a
un único `docker exec` y una instancia one-off propia. START intercambia nueve frames; SINGLE
intercambia cinco. Los perfiles, headers, DTOs y proofs se publican exclusivamente en un namespace
nuevo ligado al exchange, sin adoptar ni sobrescribir uno existente. El cierre exige ambos EOF.
El DTO de proceso liga CID one-off, profile/header, fuentes y **los 17 campos** del bootstrap;
el compromiso de deployment también liga los bytes completos del envelope. El backend observado
por B5 es parte del SUT de cuatro servicios y no se sustituye por el one-off.

OPEN_ONE, OPEN_EVIDENCE firmado/PoP, reserve request, ACK exacto y first-valid se verifican por
sus componentes reales antes de commit-open. La identidad de run se deriva del reloj del worker
y del nonce instalado; no se calcula de antemano desde el reloj del host. El permiso de emisión
se liga a ARMED/custodia y contexto actual antes de t0. LOCAL_TEST conserva un camino separado.

Publicación PUBLIC, HMAC custodial, firmas B3/B4, firma bridge y PoP del executor son dominios
separados. Un archivo de aprobación, una firma o un DTO no conceden por sí solos otra capacidad.
No se generan claves reales ni se instala confianza mediante este runbook. Los callbacks de
aprobación y recursos se fijan en el constructor del host, nunca en un frame, perfil, CLI o env.

## Instalación fija y comandos

Una instalación autorizada construye `NativeActionFactory` con proveedores fijos de datos crudos
para one-off/envelope/bridge/domain/cleanup y aprobadores independientes. La fábrica construye
internamente qualifiers, policy, Sources, journal, signer y broker; no recibe esos objetos ya
ensamblados. Se conecta con `NativeLauncherBootstrap(action_factory=...)` y con
`main(argv, native_bootstrap=bootstrap)` o `execute(Invocation(..., native_bootstrap=bootstrap))`.
El entrypoint ordinario sin instalación deniega acciones v2 antes de Docker/efectos.
No hay flag CLI, import dinámico por request ni variable de entorno que seleccione confianza.

Todos los comandos usan `--expected-git-sha <HEAD_APROBADO>` y `--session-root <SESION_NUEVA>`.
Los placeholders deben sustituirse únicamente con el contexto calificado de la instalación.
Cada invocación consume una sola acción; no concatena START/execute, no respawnea y no reintenta.

| Acción | Flag explícito | Gates y comportamiento |
|---|---|---|
| `initialize-session` | Ninguno | Nueva sesión v2, B1/B2/coverage/B3/checkpoint; sin Docker ni PF de aplicación |
| `inspect` | Ninguno | Host RO, header/chain/checkpoint/refs/hashes de outputs; v1 permite sólo inspect |
| `start-official` | `--confirm-official-start --preflight-report <PF> --nonce <HEX64>`; ACK exactos por `--acknowledge-not-demonstrated <RUN_ID>` | PF/envelope actuales, autoridad/coverage/GOV, first-valid y coordinación; prepara run sin emisión |
| `status` | Ninguno | Lectura Docker acotada del run ligado; fase/custodia/ownership; discrepancia con B2 falla, sin reparación ni outputs/history writes |
| `execute-official` | `--confirm-emission` | Run/instancia/permisos actuales, envelope/PF attached vigentes y permiso pre-emisión; sin nuevo START ni READY cero post-START |
| `drain` | `--confirm-drain` | Run/fase autorizada, ownership; sin otro START ni frescura de PF de START |
| `reconcile` | `--confirm-reconciliation` | Dominio RO y decisión provisional; no consume first-valid ni inventa evidencia |
| `finalize` | `--confirm-finalization` | Sello/hashes/decisión G0-A2, receipt B3 terminal y después RETIRE B4 |
| `abandon-official-pre-t0` | `--confirm-official-abandon-pre-t0` | Prueba actual pre-t0, motivo fijo `operator-stop`, terminal ND no binding |
| `declare-executor-gone` | `--confirm-declare-executor-gone` | Prueba de la instancia concreta, no mero TTL, cierre autorizado sin emisión nueva |
| `cleanup-stack` | `--confirm-cleanup` | Fence global→sesión, B3/B4 actuales sin activo/incierto, IDs/labels/networks/endpoints exactos; ningún recurso ajeno ni evidencia eliminada |

Confirmación ausente, schema desconocido o contexto incorrecto deniegan la acción. La sesión v1
no admite mutadores, status de sesión v1 ni fallback a scanner local para una acción OFFICIAL.
La lectura histórica de run1 mediante el comando de dominio sigue separada de inspect de v1.
Los códigos públicos se conservan: 0 coherente, 1 rechazo, 2 rechazo terminal, 3 uso/config,
4 fallo operacional y 5 abort/timeout; un timeout conserva incertidumbre y no demuestra
que el worker remoto haya dejado de escribir.

## Envelope y continuidad de las pruebas

START exige PF de aplicación y B5 PASS del mismo HEAD/deployment, edad máxima 900 segundos,
UTC no futuro, READY aprobado, mounts y RUNNING_SOURCES efectivos. El report debe ser el
argumento explícito de la invocación. No se acepta otro PF como recuperación automática.
Execute conserva el envelope attached y revalida su vigencia antes de permiso de emisión;
READY histórico se conserva sin exigir otra vez cero runs tras START.
Lifecycle/status/cleanup no exigen un nuevo PF fresco de START ni reconstruyen B5. La observación
attached conserva bindings, fuente/instancias, referencias originales y coherencia original:
`PF.checked_at <= B5.checked_at <= B5.expires_at <= PF.checked_at + 900s`.

Preparación conserva sus deadlines originales de 900s/READY y las observaciones auténticas
B3/B4; una observación vencida no se renueva para rescatar la misma prueba. Después del bridge,
la ciencia puede continuar sin reabrir los pipes. La finalización exige exit real 0 de PID1,
CLI propia reaped, resultado privado del exchange y leases de bytes actuales. Retiene el
control original y los compromisos completos I/D sin fingir que el worker terminado siga vivo.
La propuesta B2 exacta se aprueba de forma independiente antes y después de su checkpoint.
Los últimos providers preceden las últimas lecturas pasivas y fences puros del contexto retenido.

## Incertidumbre, cierre y recuperación

Antes de crear/start un one-off se registra un intent B2 duradero, incierto y bloqueante. La
pérdida de respuesta, EOF incompleto, witness/floor retrasado, perfil/fuente reemplazado, exit
no observado o publicación incompleta retienen intent/reserva/evidencia. No hay rollback que
finja que una apertura o una retirada no ocurrieron; inspeccionar no equivale a aprobar recovery.

La recuperación es una acción separada, con autorización humana exacta y pruebas de no emisión,
writer closure, CAS/floors y provenance existentes según el caso. No borra claims/tombstones,
no adopta recursos por nombre/proyecto y no rebaja secuencias. Un ledger firmado o completo
no demuestra por sí solo que writers antiguos estén cerrados en el OS/deployment real.

Cleanup compara inventarios completos del proyecto y rechaza CIDs/network IDs ajenos incluso
con labels coincidentes. Cada `stop/rm/network rm` tiene su propio head B4/B3 retenido y recheck
final después del último I/O. Sólo retira IDs aprobados; no usa remove-orphans, prune, rm de
volúmenes/imágenes, ni elimina roots de evidencia, autoridad, witness, receipts o sesiones.
Una eliminación parcial deja el intent; no se repite automáticamente.

## Validación y siguiente gate

La evidencia offline conserva por separado schemas, componentes, once acciones, regresiones,
mutantes, estrés B4-S01…S09/crash y revisión de seguridad. Cada resultado identifica sus bytes,
contexto, comando y oráculos; la reutilización exige una clausura revisada de esos elementos.
Los primeros fallos, supervivientes y límites de cada comprobación permanecen en la evidencia
externa. El cierre del candidato exige la suite S11, la suite completa del repositorio y tres
runs consecutivos de CI backend sobre el mismo árbol final y todos los jobs frontend.
Según la decisión D5 del operador sobre validación paralela y replay post-commit del
12/10/2026, el replay después de los commits se acredita por identidad del hash del árbol
y reutilización de evidencia cuando el árbol comprometido es idéntico en bytes al candidato
validado. Si existe cualquier diferencia, se exige una corrida completa repartida.

`PENDING_VALIDATION`: freeze final, commits según allowlist, tree limpio, P02/P03 v10 y anchors
externos posteriores. El set atestado conserva 28 paths; el cambio autorizado de la política
custodial obliga una nueva atestación de bytes finales. Los artefactos v9 no califican este nuevo
HEAD. Los resultados offline se registran en evidencia externa; P02/P03 v10, CI y START
oficial conservan sus gates separados.

Run1 `s11-0ff9a5d1-2309-5f40-b4a7-dabad703e43a` permanece `NOT_DEMONSTRATED`, binding false,
ACK requerido. G0-A2 no es retroactivo. Un resultado experimental FAIL puede ser válido;
no se cambia metodología, dataset, thresholds, pacing, first-valid ni semántica de ACK.
