# G0-A3 — Runtime profile y contrato operacional seguro de EX-10

## 1. Estado

Borrador documental congelado para revisión del operador. El contrato propuesto, sujeto a aprobación explícita del operador, adopta conceptualmente la opción B: RUNTIME_PROFILE. Esta entrega no constituye aprobación del contrato ni autorización de implementación u operación.

G0_A3_DRAFTED=YES
G0_A3_APPROVED=NO
G0_A3_OPERATOR_APPROVAL_REQUIRED=YES
IMPLEMENTATION_AUTHORIZED=NO
RECOMMENDED_DESIGN=B

La autoridad de este borrador es el encargo documental del operador y su aceptación de la auditoría read-only anterior. Las reglas expresadas como requisitos describen la implementación futura condicionada a aprobación; no describen capacidades ya implementadas.

Baseline leído: main, HEAD 8f324eccf8e8af4998a48b3f9cd95c16c51dadd8, TREE 306efddaceef7056fa57c46127125e4a4a6dd22b, working tree CLEAN.

## 2. Motivación

El launcher v1 fija el project ciudadania-s11-ex10-official y las cuatro roots host del checkout: var/s11-runs, var/s11-backup, var/s11-restore y var/s11-objects. Esas ubicaciones forman parte del entorno histórico del run 1. El bootstrap seguro attempt7 demostró la necesidad de roots externas y un project independiente.

El aparato nativo no puede ligar ese deployment seguro a su sesión: faltan contratos para project, roots y override. El preflight actual tampoco acredita conjuntamente esas ubicaciones host. Una root nueva vacía, además, pierde el conocimiento histórico si se conserva exclusivamente el escaneo local del store. Parametrizar sólo paths no resuelve el problema.

## 3. Evidencia del blocker

NATIVE_LAUNCHER_SAFE_STACK_CONTRACT_BLOCKER=CONFIRMED
PRODUCT_CONTRACT_CHANGE_REQUIRED=YES
PRODUCT_DEFECT_CLASSIFICATION=OPERATIONAL_SAFETY_CONTRACT_GAP
ATTEMPT7_NATIVE_START_COMPATIBLE=NO

Fuentes versionadas en el baseline:

- backend/scripts/s11_ex10_official_launcher.py: PROJECT, official_roots, HEADER_KEYS, load_session y preflight_report_problems fijan o verifican el contrato v1 sin los bindings requeridos. evidence_snapshot mira sólo var/s11-runs/runs.
- backend/scripts/s11_ex10_preflight_launcher.py: COMPOSE_FILES fija los dos archivos Compose; secret_masks recorre el checkout sin excluir var.
- backend/apps/experiments_app/ex10_executor.py: prior_ex10_runs explora store.root/runs; binding_refusal depende de ese resultado; _checkpoint usa el lock de ese store. Una root ausente produce una lista vacía y dos roots no comparten por ello coordinación global.
- docs/tesis/S11A_POLITICA_CUSTODIA_Y_ACCESO.md, §5: aprueba ubicaciones host concretas bajo repo/var.
- docs/PROGRESO_SPRINTS.md, G0/MD-09: exige conservar todos los intentos y hace vinculante el primer PASS o FAIL válido.
- docs/runbooks/S11_EX10_G0_A2_POST_RUN_BROKER_SEMANTICS.md, §12: exige reconocer el run 1 finalizado y su ACK exacto.

Attempt7 se conserva como TECHNICALLY_QUALIFIED_SAFE_DEPLOYMENT según el contexto aceptado; NATIVE_START_COMPATIBLE=NO. No se reutiliza para START con launcher v1. Este borrador no inspecciona su estado vivo ni afirma que será reutilizable automáticamente con una implementación futura. Su record de bootstrap externo no se convierte en una sesión nativa.

## 4. Scope

El contrato propuesto cubre exclusivamente el aparato operacional y su adecuación custodial: runtime profile; sesión v2; bindings de project, roots, Compose, override y source; envelope operacional; autoridad histórica común; coordinación de START; preservación de ACK, compatibilidad histórica y aislamiento de cleanup.

Las autoridades previas permanecen:

- G0: 1693d0ae562bfe777c496b616c9f8c8d5fa8a591.
- G0-A1: db7fe6649213a8d26f4ee60d36a797ee54f80322.
- G0-A2: 2bab6cabe4108e1eddbb319c3f859ed75c914004.
- HEAD técnico de partida: 8f324eccf8e8af4998a48b3f9cd95c16c51dadd8.

La implementación futura sólo podrá realizarse mediante una autorización distinta y en un checkout aislado del que usa attempt7.

## 5. No-scope

No cambia hipótesis, HT1-01..04, umbrales, población, dataset, plan, pacing, retry=0, endpoint, fake AI, worker concurrency=2, Redis DB contractual, reglas del broker, first-valid, ACK ni run 1. No cambia el sistema bajo prueba ni su fingerprint aprobado.

Se preservan s11-ex10-v1 y S11A-v1.1; 10 usuarios × 5 requests = 50, ventana de 60 segundos, dataset s11-load-v1 y seed 20260921, endpoint /api/v1/reports/, fake s11-deterministic-fake-v1, worker prefork concurrency=2 y broker redis://redis:6379/11. Permanecen los hashes congelados del dataset y del plan:

Dataset SHA-256: c6883b59dc861fb07fae730594c929fdd572a9ed71aee6d8b85c87f71ede0acb.
Plan SHA-256: 2b50aa48cfa975029ab38a4c0e17430f67469b185fce5aaed89414728e6934d7.

No altera la clasificación de transporte, precedencia de NOT_DEMONSTRATED, percentiles ni otras aclaraciones de G0-A1. No produce resultados experimentales, sesión oficial, run_id, t0, POST ni tareas.

## 6. Clasificación metodológica

G0_A3_CLASSIFICATION=OPERATIONAL_APPARATUS_AND_CUSTODY
PROPOSED_CHANGE_CLASSIFICATION=OPERATIONAL_APPARATUS_ONLY

La primera clasificación comprende la adecuación documental de custodia; la segunda describe el alcance técnico frente al experimento y al SUT. No son clasificaciones contradictorias.

G0/MD-09 permite corregir el aparato entre intentos mediante commit y pruebas, conservando protocolo y fingerprint del SUT. Este borrador no ejecuta esa facultad. La sesión v2 es una versión explícita del registro operacional; no reemplaza el schema experimental del run ni extiende retroactivamente G0-A2. Cualquier cambio no aditivo fuera de esta sesión operacional requiere una decisión específica antes de implementarse.

## 7. Runtime profile

Se propone schema_version s11-ex10-runtime-profile-v1, cerrado y versionado. El profile será explícito, canónico, de sólo datos, fail-closed y ligado por SHA-256 a la sesión. No permitirá comandos, shell, environment arbitrario, campos desconocidos, claves duplicadas ni valores no finitos. Se fijarán representación UTF-8, orden de claves y reglas de canonicalización; se rechazará una representación que no coincida con los bytes canónicos aprobados.

Campos obligatorios:

- schema_version y profile_id únicos dentro de su ámbito.
- approved_at y decision_reference, ligados a la aprobación real del operador.
- source_revision: revisión exacta del checkout.
- project: valor explícito validado.
- compose_files: lista ordenada de paths canónicos con blobs Git y hashes de contenido.
- override_path canónico y override_sha256.
- source_host_root, source_container_target y source_read_only=true.
- host_roots: evidence, backup, restore y objects; fixed_targets correspondientes.
- Identidad de owner esperada y modos privados aprobados para roots y archivos.
- masks: var histórico del checkout, .env y cualquier máscara adicional explícitamente aprobada.
- expected_services: db, redis, backend, worker; beat_policy=disabled.
- deployment_verification_reference.
- historical_lifecycle_authority_reference.
- external_source_anchor_reference.

Las referencias deben ser resolubles, verificables y ligadas a su contenido y revisión; un nombre de archivo o una cadena declarativa no constituye aprobación ni evidencia por sí solo. No contendrá secretos. El environment necesario de la aplicación conservará únicamente el contrato permitido existente, sin permitir valores secretos dentro del profile.

Este paquete no instancia un profile aprobado: approved_at y decision_reference son requisitos futuros, no placeholders de una aprobación ficticia. Un profile sin aprobación verificable debe ser rechazado.

## 8. Session v2

RECOMMENDED_SESSION_SCHEMA=s11c3-ex10-official-launcher-session-v2

El HEADER y su digest, que siembra la cadena de acciones, incorporarán obligatoriamente runtime_profile_schema, runtime_profile_id, runtime_profile_sha256, project, roots canónicas y targets fijos, Compose files/orden/blobs/hashes, override path/SHA, binding source host/contenedor/read-only, masks y referencias de deployment, autoridad histórica y anchor externo.

Se preservarán los bindings previos pertinentes de revisión, branch, repo, identidad de sesión, fecha, modo oficial e integridad de launchers. Ningún campo nuevo de seguridad podrá quedar fuera del hash del header. Las salidas de acciones y sus hashes seguirán ligados a una cadena verificable.

No podrán variar project, roots, profile, override, masks ni autoridad durante la sesión. Una diferencia entre header, profile, archivos actuales o deployment real bloquea la acción, sin actualizar automáticamente el header. Rechazar sustitución o corrupción preserva el estado; no repara ni reintenta.

## 9. Project contract

El project será un string explícito, sin defaults silenciosos, ligado al profile y al header. Se propone allowlist ASCII ^[a-z0-9][a-z0-9_-]{0,62}$, longitud de 1 a 63 caracteres; no se interpreta como shell ni se concatena a comandos ejecutables.

Todas las acciones usarán exactamente el mismo project. Se verificará su identidad en recursos reales, labels y deployment aprobado. El nombre por sí solo no autoriza adoptar contenedores, redes o volúmenes preexistentes de otra sesión o procedencia. Una colisión o recurso ajeno bloquea.

Cleanup sólo podrá actuar sobre el project y los recursos demostrablemente ligados a esta sesión; el profile no permite cambiarlo durante el lifecycle.

## 10. Roots contract

Cada root host será absoluta, igual a su canonical realpath, exterior al checkout completo, a repo/var y a /tmp; un directorio real sin componentes symlink, con owner esperado y modos privados aprobados: directorios 0700 y archivos custodiales 0600. La validación cubrirá sustituciones y carreras TOCTOU entre verificación y uso, y se repetirá antes de cada acción que dependa de ella.

Las cuatro roots serán distintas, no anidadas ni solapadas entre sí, sin solapamiento con roots históricas ni con roots o registros de otra sesión oficial. Se ligarán por valor canónico al profile y al header. Una root inválida o cambiada bloquea; no se crea, reubica ni repara silenciosamente.

Una estructura bajo el directorio de un bootstrap externo puede evaluarse sólo cuando esa relación esté declarada y aprobada; no implica adoptar su record como sesión nativa ni autoriza compartir roots con otra sesión oficial. El directorio de registro de una nueva sesión nativa será nuevo e independiente. Attempt7 no obtiene aprobación automática por esta excepción contractual.

El único cambio permitido es la ubicación host. Targets exactos:

- evidence → /s11-evidence.
- backup → /s11-backup.
- restore → /s11-restore.
- objects → /s11-objects.

Primary, backup y restore son persistentes frente a recreación de contenedores y conservan retención y custodia; “roots desechables de preparación” no permite borrar evidencia tras START o finalización. Objects sigue siendo almacenamiento técnico temporal, separado de la evidencia custodial.

## 11. Compose/source contract

La secuencia aprobada de Compose se ligará por orden, path, blob y hash, junto al override externo canónico y su SHA-256. El Compose resuelto deberá corresponder al profile, conservando servicios db, redis, backend y worker; beat deshabilitado y sin procesos adicionales que emitan tareas.

Los dos Compose versionados del baseline no se modifican como atajo: están incluidos en el fingerprint del SUT. El override sólo expresará el aislamiento permitido y no alterará comportamiento experimental, broker, fake, concurrencia ni parámetros congelados.

El checkout completo se montará read-only en /s11-full-source. Var histórico y .env quedarán enmascarados; máscaras adicionales necesitarán aprobación explícita y no ocultarán fuentes de RUNNING_SOURCES. Los mounts efectivos deberán demostrar que ningún bind, volumen, alias o submount expone roots históricas. OFFICIAL_VAR_ROOTS_MOUNTED=NO será un invariante.

El escaneo host de secretos excluirá var histórico antes de recorrerlo, sin abrir sus roots ni su contenido. No se persistirán valores secretos, environment completo ni Compose resuelto con secretos: se conservará evidencia saneada y compromisos de integridad compatibles con esa restricción.

## 12. Operational preflight envelope

Se exigirá un envelope operacional, de schema cerrado y verificable, que ligue HEAD, SHA del profile, project, roots host, targets, override, Compose resuelto, mounts reales, RUNNING_SOURCES, READY_STATE, identidad del protocolo e identidad del deployment. Identificará instancias de contenedor, fuente, revisión y referencias de verificación suficientes para detectar reemplazos.

El application preflight podrá mantenerse internamente con su semántica experimental. START requerirá APPLICATION_PREFLIGHT=PASS y OPERATIONAL_DEPLOYMENT_ENVELOPE=PASS, ambos frescos y coherentes entre sí, con la sesión y con el deployment real. No basta un PASS de aplicación desligado de host paths.

checked_at y expires_at serán UTC verificables; se rechazarán timestamps futuros, expirados, inconsistentes o de otro deployment. Se preservará el máximo vigente de 15 minutos, sin ampliación tácita, y se comprobará nuevamente la identidad al consumirlos. La vigencia no sustituye la comprobación de que mounts, fuentes y READY_STATE sigan iguales.

El profile y el header ligan la autoridad histórica estable; cada comprobación y reserva ligará también su estado vigente. Un envelope fresco no autoriza usar una vista histórica stale.

## 13. Historical lifecycle authority

La autoridad común será independiente de la root local de escritura y estará ligada al profile y a la sesión. Debe demostrar integridad, autenticidad, ámbito completo y actualidad de la historia oficial de EX-10; no basta una lista autocontenida, aunque esté firmada, porque su firma no demuestra que no haya omitido runs.

Su contrato establecerá autoridad emisora autorizada, dominio de autenticación, cobertura de todas las ubicaciones históricas y futuras pertinentes, revisión monotónica verificable y reglas de vigencia. No reutiliza automáticamente la autoridad de publicación PUBLIC como autoridad de lifecycle. Se requiere una fuente independiente que permita verificar completitud y detectar omisión, rollback y contradicción.

Debe conservar run 1 NOT_DEMONSTRATED, binding=false, ACK pendiente; todos los futuros PASS/FAIL y NOT_DEMONSTRATED; runs activos o ilegibles; reservas y aperturas concurrentes. Una root nueva nunca se toma como historia vacía. La migración o lectura certificada de historia futura no modifica evidencia histórica ni exige que Codex abra repo/var.

Stale, omitted, contradictory o unreadable history bloquea. Un PASS/FAIL oficial válido previo bloquea definitivamente otro intento; un run activo o ilegible bloquea mientras persista esa condición. Los ND oficiales exigen reconocimiento explícito de cada run conforme a la regla existente.

El estado actualizado de la autoridad se comprobará al iniciar y se mantendrá coherente con aperturas, decisiones y cierre posteriores. Una exportación histórica aislada no sustituye la coordinación ni las actualizaciones de lifecycle.

## 14. First-valid coordination

GLOBAL_OFFICIAL_START_COORDINATION=YES es un requisito de la implementación futura, no una capacidad ejecutada en esta entrega.

La comprobación de historia, reserva exclusiva y apertura deberán tener coordinación común para EX-10, independiente de project y root local. Dos START concurrentes sobre roots diferentes no pueden abrir dos runs oficiales elegibles; cambiar project, profile, ubicación o revisión no evade el primer resultado vinculante.

No se prescribe todavía un mecanismo concreto. Se exige que la comprobación/reserva/apertura no deje una ventana donde ambos participantes puedan superar el gate, y que se autentiquen y conserven sus resultados. Una reserva incompleta, proceso interrumpido o apertura incierta mantiene el bloqueo; no se libera ni repite automáticamente para posibilitar otro START. Cualquier recuperación requerirá evidencia y autorización separadas.

Se preserva first-valid: el primer PASS o FAIL válido es vinculante; tras ND se admite un nuevo intento completo bajo el mismo protocolo y ACK explícito, sin reruns selectivos.

## 15. ACK

ACK_RUN_ID=s11-0ff9a5d1-2309-5f40-b4a7-dabad703e43a
ACK_SEMANTICS_CHANGED=NO

Un nuevo START sin el ACK exacto del run 1 debe rechazarse. Con ese ACK sólo podrá continuar si pasan todos los demás gates, incluida autorización operacional, historia común, coordinación, custodia y ambos preflights.

El reconocimiento seguirá siendo un parámetro explícito del START futuro autorizado; no se deduce del profile, una aprobación documental, el bootstrap ni un ACK histórico. No se introduce un reconocimiento persistente que dispense START posteriores de la regla vigente. ACK desconocidos se rechazan.

ACK no consume first-valid, no modifica ni reinterpreta run 1 y no compensa un deployment inválido. No se usa ACK en esta tarea.

## 16. G0-A2 continuity

G0_A2_SEMANTICS_AFFECTED=NO
G0_A2_RETROACTIVE_EFFECT=NONE
POST_RUN_BROKER_SEMANTICS=UNCHANGED
PRE_RUN_BROKER_SEMANTICS=UNCHANGED

G0-A2 sigue rigiendo únicamente los runs posteriores a su incorporación técnica a la identidad del protocolo. Se conserva la exigencia pre-run del broker y la semántica post-run aprobada, sin aplicar esta última retroactivamente al run 1.

Cambiar ubicación host, project o schema de sesión no altera el significado de validez, quiescencia, fallo ni resultado. No se reduce el conjunto de checks ni se reclasifican residuos para facilitar START.

## 17. Run 1 continuity

RUN1_ID=s11-0ff9a5d1-2309-5f40-b4a7-dabad703e43a
RUN1_RESULT=NOT_DEMONSTRATED
RUN1_BINDING=false
RUN1_REINTERPRETED=NO
FIRST_VALID_CONSUMED=NO

El run 1 conserva revisión ejecutada, identidad, decisión, reconciliación, manifest final, sello y evidencia. La autoridad histórica lo reconoce como finalizado, oficial y verificable sin exigirle identidad de una revisión futura.

Los PASS individuales de HT1-01..04 no convierten su terminal en PASS: permanece NOT_DEMONSTRATED. Esta entrega no abre sus roots, compara snapshots nuevamente, copia evidencia ni modifica metadatos históricos.

## 18. Custody / P02-P03 v10

ATTESTED_FILES_TOUCHED_MINIMUM=1
P02_P03_V10_REQUIRED=YES
P02_P03_V10_EXECUTED_IN_THIS_TASK=NO

El archivo atestado previsto es docs/tesis/S11A_POLITICA_CUSTODIA_Y_ACCESO.md: su §5 aprueba hoy ubicaciones exactas bajo repo/var. La implementación futura debe adecuar esa política a ubicaciones host aprobadas mediante profile, conservando targets, privacidad, retención, backup/restore, separación de objects y autoridad de acceso.

El mínimo de una fuente atestada es una previsión del alcance aprobado para diseño, no un archivo modificado en esta tarea. Antes de implementar se auditarán los 28 ATTESTED_SOURCE_PATHS de custody.py; si el alcance toca más fuentes atestadas, se actualizará el conteo y la revisión.

P02/P03 v10 será obligatorio después de congelar los blobs finales y deberá atestar esos mismos blobs en el HEAD final limpio. No se ejecuta v10 ahora, no se reutiliza una atestación anterior para fuentes nuevas y no se rota una clave sólo por cambiar HEAD. Toda adecuación preserva S11-CUSTODY-v1.0 y la prohibición de persistir SENSITIVE o SECRET.

## 19. Backward compatibility

HISTORICAL_SESSION_COMPATIBILITY=PRESERVE_V1_NO_REWRITE

V1 histórica conserva su semántica original: nunca se reescribe, convierte ni “completa” como v2. Inspect histórico puede leer v1, validando su contrato original sin nuevos defaults. Los mutadores nuevos no aplicarán defaults v2 sobre v1; las nuevas sesiones operativas usarán exclusivamente v2.

No se cambia un loader v1 de forma que reinterprete registros antiguos. La sesión v2 no altera schemas sellados del run 1. El record de bootstrap attempt7 seguirá siendo externo, sin fabricar una sesión nativa equivalente.

Un HEAD futuro requerirá nueva evidencia de vigencia:

CURRENT_CI_VALID_FOR_NEW_HEAD=NO
CURRENT_BUNDLE_VALID_FOR_NEW_HEAD=NO
CURRENT_EXTERNAL_ANCHOR_VALID_FOR_NEW_HEAD=NO
CURRENT_PREFLIGHT_VALID_FOR_NEW_HEAD=NO
CURRENT_PREEXECUTION_PACKAGE_VALID_FOR_NEW_HEAD=NO

Los artefactos anteriores conservan su valor histórico para 8f324eccf8e8af4998a48b3f9cd95c16c51dadd8; eso no renueva preflights expirados ni autoriza reutilizarlos operativamente.

## 20. Cleanup

Cleanup v2 sólo actuará sobre el project ligado y recursos de procedencia demostrada de esa sesión. No hará prune, no adoptará ni borrará recursos ajenos, no borrará volúmenes salvo una acción separada, demostrada y autorizada; no tocará roots históricas, safe roots de otra sesión ni runtime profile.

El teardown de contenedores no implica destrucción de evidencia. Retención y destrucción custodial conservan sus procedimientos independientes. El cleanup se validará contra el profile y el deployment actual; una discrepancia o estado incierto provoca bloqueo, preservación y detención, sin retries automáticos.

## 21. Security invariants

Se mantienen fail-closed; una acción por invocación; ausencia de retries automáticos; registro de incertidumbre; protección de integridad de header, cadena, referencias y salidas; owner monty y modos privados para este paquete.

En la implementación futura: source read-only, masks verificadas, OFFICIAL_VAR_ROOTS_MOUNTED=NO, beat deshabilitado y ningún acceso del contenedor a evidencia histórica. No se recorrerá var histórico para descubrir secretos ni se registrarán valores de .env, credenciales, emails personales, datos de red privada, imágenes o coordenadas.

Hashes prueban integridad, no sustituyen autorización, completitud histórica ni custodia autenticada. No se aceptan autoaprobación, permisos implícitos por usar un path o defaults que oculten un cambio de identidad. Ante fallo mutante se conserva estado y se detiene; no se avanza a otra fase como recuperación automática.

## 22. Required tests

Plan obligatorio para una implementación futura autorizada; no ejecutado en esta entrega:

- PROJECT: válido, inválido, inyección, discrepancia entre acciones y recursos ajenos.
- ROOTS: checkout, repo/var, /tmp, componentes symlink, nesting, overlap histórico/entre roots/otra sesión, owner o modos incorrectos, TOCTOU y cambio entre acciones.
- PROFILE: canonicalización, hash, sustitución, tampering, campos desconocidos, claves duplicadas, comandos y environment prohibidos, aprobación ausente o no verificable.
- SESSION: v1 histórica sin reescritura, v2 nueva, tampering de project, roots, override, profile y cadena.
- PREFLIGHT: discrepancia de project, roots, override y deployment; stale, timestamp futuro, fuente o mount cambiado después del chequeo.
- FIRST-VALID: run 1 reconocido; falta de ACK rechazada; ACK exacto aceptable sólo con los demás gates; historia omitted, stale, contradictoria o ilegible rechazada; PASS/FAIL previo bloquea; run activo bloquea; dos START concurrentes en roots distintas, interrupción de reserva y rollback de historia.
- CLEANUP: sólo project ligado, ninguna eliminación de volúmenes en cleanup ordinario, ningún recurso ajeno ni evidencia histórica afectada.
- LIFECYCLE: acción única, sin retry, PREPARED sin emisión, status de sólo lectura respecto del dominio experimental, preservación de incertidumbre y compatibilidad de evidencia histórica.

Los tests serán offline o con transportes inyectados cuando corresponda; no emitirán requests experimentales fuera de un run oficial. Status podrá registrar su invocación en la cadena operacional conforme al contrato, sin mutar el dominio experimental ni emitir tareas.

## 23. Implementation phases

Propuesta secuencial A–M; todas pendientes de autorización posterior. Aprobar el documento no ejecuta automáticamente ninguna fase:

A. Aprobar decisión, schema, ubicaciones y autoridad histórica; resolver cualquier alcance que altere contratos previos.
B. Implementar en checkout aislado, sin modificar el bind source vivo de attempt7; limitar cambios al aparato y política prevista.
C. Ejecutar tests dirigidos del contrato.
D. Ejecutar mutantes y stress, incluida concurrencia entre roots y fallos de coordinación.
E. Ejecutar suite global aplicable y comprobar fingerprint del SUT inalterado.
F. Obtener CI 3/3 para el candidato.
G. Crear los commits autorizados y congelar blobs finales.
H. Verificar CI post-commit y P02/P03 v10 sobre esos mismos blobs y HEAD limpio; ningún cambio posterior queda cubierto implícitamente.
I. Calificar un deployment seguro y su preflight, sin emisión experimental; no asumir reutilización automática de attempt7.
J. Crear un bundle nuevo de la revisión final.
K. Obtener verificación externa del operador y nuevo source anchor.
L. Crear paquete pre-ejecución nuevo con evidencias coherentes de esa revisión.
M. Crear una sesión nativa v2 y obtener preflight fresco y envelope operacional del deployment calificado, con historia y coordinación comunes verificadas.

Cada fase exige su autorización aplicable y evidencia de salida; cualquier fallo mutante detiene, preserva estado y no se repite automáticamente. Después de M tampoco hay ACK o START automáticos: requieren autorización operacional separada. Este borrador no implementa el mecanismo de coordinación ni crea recursos para probarlo.

## 24. Acceptance criteria

Antes de declarar lista una implementación futura deben demostrarse: schema de profile cerrado y aprobación verificable; sesión v2; bindings de project, roots, Compose, override y source; masks efectivas; application preflight y envelope operacional PASS frescos/coherentes; autoridad histórica completa, vigente, autenticada y coordinación global; ACK preservado; lectura histórica v1 sin reescritura; una acción por invocación, sin retries; PREPARED sin emisión; status experimental read-only; incertidumbre conservada y cleanup aislado.

Se requieren los ocho grupos de tests anteriores, revisión de los 28 paths atestados, conteo real de fuentes modificadas, P02/P03 v10 posterior a blobs finales, fingerprint del SUT inalterado y continuidad documentada de G0/G0-A1/G0-A2/run 1. Cualquier cambio del SUT, protocolo experimental, esquema fuera del alcance o reglas metodológicas exige detenerse y obtener una decisión distinta.

Para esta entrega documental sólo se verifica: tres archivos exactos; directory 0700; archivos 0600; owner monty; cero symlinks; manifest JSON canónico que lista únicamente Markdown y SUMMARY con basename, size, SHA-256, mode y owner, sin autorreferencia; hashes correctos; escaneo de privacidad sin valores privados/secretos adjudicados; consistencia documental y checkout sin cambios. Estos checks no equivalen a tests de implementación ni aprobación operacional.

## 25. Explicit prohibitions

Esta tarea no autoriza código, documentación del repo, tests nuevos, Compose nuevo operativo, branches, worktrees, stash, reset, checkout, commits, push, fetch o pull. No modifica el checkout actual ni attempt7 ni records anteriores.

No Docker, sudo, migrate, prepare, initialize, preflight, ACK, START, execute, drain, reconcile, finalize o cleanup. No sesión oficial nueva, run_id, t0, POST, tareas, snapshots históricos nuevos ni lectura directa de roots históricas. El único material nuevo es el paquete documental externo solicitado.

DOCKER_COMMANDS_EXECUTED=0
REPO_MODIFIED=NO
COMMITS_CREATED=0
PUSH_EXECUTED=NO
G0_A3_DRAFTED=YES
G0_A3_APPROVED=NO
G0_A3_OPERATOR_APPROVAL_REQUIRED=YES
IMPLEMENTATION_AUTHORIZED=NO

El operador revisará el documento antes de aprobarlo. Su aprobación futura del contrato tampoco constituirá por sí sola autorización de implementación, ACK o START.
