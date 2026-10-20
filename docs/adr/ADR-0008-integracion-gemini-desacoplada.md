# ADR-0008 — Integración Gemini desacoplada mediante adapter de IA

> **Evolución al 20/10/2026.** El contexto siguiente corresponde a S8-A. La integración
> ya está implementada y OpenAI es el proveedor activo ([ADR-0016](ADR-0016-proveedor-de-ia-openai.md)).
> Producción usa prompt v2; v1 permanece intacta para EX-09. `AI_PROMPT_VERSION`
> (default v1) selecciona la versión del adapter OpenAI y falla cerrado si es desconocida.
> `0003` registra tokens por clasificación persistida. La frontera/parser y el esquema v1
> de seis campos se mantienen; el costo ya incluye clasificación productiva habilitada,
> además de gates y comandos manuales. Gemini queda como antecedente histórico.

**Estado:** Aceptada
**Sprint:** 8
**Fecha:** 2026-08-29

## Contexto

El Sprint 8 incorpora la clasificación asistida por IA. El proveedor previsto es Google
Gemini, un servicio externo de pago, con cuotas, latencia variable y salida no determinista.

Tres condiciones del proyecto restringen cómo puede integrarse. Primera: la creación de un
reporte no puede depender de él. `create_report` es transaccional y arrastra el asiento de
20 puntos pendientes del ledger; un proveedor caído no puede impedir que un ciudadano
reporte una incidencia (CA-5). Segunda: la IA no es autoridad final. Sugiere; la decisión
la toma un operador humano, y `RECHAZADO` le está prohibido. Tercera: el proveedor debe ser
sustituible. El backlog lo registra como riesgo explícito —«Costos o cambio de proveedor de
IA»— y su mitigación es precisamente la arquitectura desacoplada.

A esto se añade una restricción de pruebas: la CI no puede llamar a un proveedor de pago.
Sería costoso, no determinista y dependiente de una credencial en el runner.

`ai_client_app` existe desde el Sprint 2 con la tabla `ai_classifications` y una tarea
`ai.classify_report` deliberadamente no-op. La frontera ya está trazada; este ADR fija qué
puede cruzarla.

## Decisión

### 1. `ai_client_app` es la frontera del proveedor

Todo lo relativo a Gemini vive dentro de `ai_client_app`: cliente HTTP o SDK, prompt,
parser, esquema, mapeo a catálogo y política de reintentos. Ningún otro módulo conoce al
proveedor.

`reports_app` sigue viendo lo que ve hoy: una tarea Celery con la firma
`classify_report(report_id)`. Esa firma es el contrato entre el dominio y la IA, y no
cambia al implementarse.

### 2. El dominio no importa el SDK

`reports_app`, `rewards_app`, `admin_app` y `core` **no importan** el SDK del proveedor ni
ninguno de sus tipos. Un `ImportError` del SDK no puede romper la creación de reportes.

La única importación existente hoy —`from apps.ai_client_app import tasks as ai_tasks` en
`reports_app.services.reports`— se conserva tal cual.

### 3. El adapter encapsula al proveedor y el fake implementa su interfaz

Se define una interfaz de clasificación con entrada y salida propias del dominio, descritas
en el contrato v1: entra la lista blanca de D1, sale el JSON validado de D3. El adapter de
Gemini y el fake determinista implementan **la misma interfaz**.

El fake no es un stub de conveniencia: es la implementación por defecto en pruebas y CI, y
debe cubrir los casos que el parser tiene que resistir —salida válida, JSON malformado,
campo faltante, valor fuera de dominio, slug inexistente y campo desconocido.

### 4. El proveedor real solo se usa en gates explícitos

La CI **nunca** llama a Gemini. La medición de concordancia contra el proveedor real
(CA-4) ocurre en un gate separado, fuera de los tests, con dataset acotado y presupuesto
controlado.

Esto separa tres cosas que suelen mezclarse y que aquí no se mezclan: el código, la
configuración de secretos productivos y la prueba real contra el proveedor.

### 5. Los secretos viven solo en el backend

`GEMINI_API_KEY` y `GEMINI_MODEL` son variables de entorno del backend y del worker. No
llegan al frontend, no viajan en respuestas de API, no se registran en logs y no se
auditan.

Se replica la disciplina ya establecida para Brevo en ADR-0015: la clave no sale del módulo
que la usa, y hay un test que verifica que no aparece en los mensajes de error.

### 6. La salida del proveedor es siempre sospechosa

Ninguna respuesta del proveedor se persiste sin validar contra el esquema estricto y contra
el catálogo real. El parser del backend es la frontera de seguridad, no las instrucciones
del prompt.

El prompt se versiona en el repositorio (`prompts/classification_v1.txt`) y se audita por
identificador de versión, no por copia renderizada.

## Alternativas consideradas

| Alternativa | Motivo de descarte |
| ------------------------------------------ | ------------------------------------------------------------------------------------- |
| Llamar a Gemini desde `create_report` | Acopla una transacción de dominio a un servicio externo; una caída impediría reportar y rompería CA-5 |
| Importar el SDK desde `reports_app` | Invierte el grafo de dependencias del §9 del plan y hace que un fallo del SDK afecte al dominio |
| Llamar a Gemini desde el frontend | Expondría la credencial en el navegador y dejaría la clasificación fuera de toda auditoría |
| Usar el proveedor real en CI | Coste por ejecución, cuota compartida, no determinismo y una credencial de pago en el runner |
| Adapter sin interfaz común con el fake | El fake dejaría de probar el camino real; las pruebas validarían un código que producción no ejecuta |
| Persistir la respuesta cruda sin validar | Convierte la salida de un LLM en dato de dominio y permite contaminar el catálogo |

## Consecuencias

- La suite completa se ejecuta sin red y sin credenciales.
- Cambiar de proveedor afecta a un módulo, no al dominio.
- El coste de la API queda acotado a gates explícitos y medibles.
- Aparece una capa adicional —adapter e interfaz— que hay que mantener aunque solo exista
  un proveedor.
- El fake debe mantenerse fiel al comportamiento real del proveedor; si diverge, las
  pruebas dan una confianza que no corresponde. Es el riesgo principal de esta decisión.
- La medición de CA-4 no puede cerrarse dentro de la CI y exige un gate propio.
