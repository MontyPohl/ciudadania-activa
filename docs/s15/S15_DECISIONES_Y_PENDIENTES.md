# Sprint 15 Decisiones y pendientes actualizados

**Actualización:** 20/10/2026. Estas decisiones del usuario actualizan el plan v1.0;
no alteran los resultados históricos ni constituyen nuevas mediciones.

## Decisiones confirmadas

- El usuario entregará el Excel y ZIP preparados con Claude cuando complete el material.
  No se recreará el paquete sin revisar primero el existente.
- Presupuesto API total disponible: USD 5–10, compartido entre evaluación, pruebas
  personales y demostración. Esta conversación no ejecuta llamadas de pago.
- Se utilizará el entorno desplegado existente para las comprobaciones remotas;
  no se exige provisionar un segundo entorno. El volumen se ensayará localmente.
- Defensa prevista aproximadamente para el **16/12/2026**. Los resultados técnicos
  deben estar disponibles antes, para redactar y entregar el documento.
- El usuario preparará cuentas institucionales, de administrador y de ciudadanos.
- La actualización consolidada del `.docx` comenzará **después de completar los
  16 sprints**, según instrucción del usuario. Preparar y preservar evidencia sigue
  formando parte de S15/S16; incorporar el documento deja de bloquear su cierre.
- S15 sigue abierto hasta completar sus verificaciones técnicas. Se interpreta
  la mención final a «S5» como S15 por el contexto de la conversación.
- El usuario autorizó continuar todo el trabajo independiente disponible sin nuevas
  decisiones ni material. Se adoptan las opciones técnicas documentadas como base de
  trabajo; las fotos, etiquetas humanas y cuentas reales siguen siendo entradas externas.

## Propuesta de colección IA

Objetivo práctico: **120 reportes distintos**, cada uno con foto principal y
descripción factual. Reservar **100 para evaluación** y **20 para piloto, pruebas
y preparación de la demo**. Los casos de evaluación no se usan para ajustar el prompt.
Esta distribución se adopta como objetivo de colección para preparar el trabajo sin
pedir otra decisión de planificación. La composición efectiva y el protocolo se congelan
antes de observar predicciones; no es una cuota ya completada ni una muestra
representativa de la ciudad.

| Categoría del catálogo | BAJA | MEDIA | ALTA | Evaluación |
|---|---:|---:|---:|---:|
| Baches | 3 | 4 | 3 | 10 |
| Basura acumulada | 5 | 5 | 0 | 10 |
| Alumbrado público | 4 | 4 | 2 | 10 |
| Agua potable / pérdidas | 2 | 5 | 3 | 10 |
| Drenaje / desagüe / cloaca | 0 | 5 | 5 | 10 |
| Semáforos | 0 | 5 | 5 | 10 |
| Escombros | 5 | 5 | 0 | 10 |
| Árbol caído | 3 | 4 | 3 | 10 |
| Cables expuestos / riesgo eléctrico | 2 | 2 | 6 | 10 |
| Otros: veredas, inundación pluvial y otros casos municipales | 4 | 2 | 4 | 10 |
| **Total** | **28** | **41** | **31** | **100** |

Piloto: dos casos adicionales por categoría, de escenas diferentes a los cien reservados.
Dentro de «Otros», incluir cuatro casos de veredas BAJA y al menos dos inundaciones
pluviales ALTA que impidan transitar o afecten viviendas; las demás etiquetas dependen
de los hechos. Prioridades de semáforos/cloacas y excepciones de basura/escombros
respetan [la rúbrica v2](../../backend/apps/ai_client_app/prompts/classification_v2.txt).
Alumbrado con cables expuestos se clasifica como riesgo eléctrico. La foto de un
medidor no demuestra por sí sola su defecto: la descripción debe aportar hechos ciertos.

Se pueden elegir casos del piloto para preparar la demo; no se requieren fotos
adicionales solo para mostrarla. Su disponibilidad como nuevas altas en la aplicación debe revisarse
antes del ensayo para evitar duplicados de reportes ya registrados. Las cargas de volumen
usan medios sintéticos y no aumentan esta cuota de fotos reales.

Dos ángulos de una misma incidencia no son dos casos independientes. Si una cuota no es
viable, se revisa antes de congelar N y ejecutar la evaluación; no se inventan prioridades
para llenar casillas. Diez casos por categoría permiten describir fallos, con precisión
limitada por categoría. En N=100, el objetivo observado del 85 % exige 85 aciertos exactos.
Las cuotas se cubren con material disponible y autorizado; no se recrean ni se buscan
situaciones peligrosas para completarlas.

## Qué significa oro

Es la respuesta humana de referencia de cada caso: categoría, institución y prioridad,
fijadas con la rúbrica antes de consultar el modelo evaluado. No agrega consumo de API.
Si el Excel ya tiene esas columnas, pueden servir; las sugerencias de Claude requieren
validación humana. El protocolo de doble revisión humana queda por acordar y completar
antes de la corrida reservada; el validador actual exige registrar dos revisores.
No se deducen las etiquetas de las prioridades predeterminadas del catálogo.

## Presupuesto y comprobación de costo

La configuración local selecciona `gpt-6-luna`; todavía se debe confirmar el modelo
desplegado y congelar su configuración para la corrida. Las tarifas oficiales consultadas
el 20/10/2026 son USD 0,10 por millón de tokens de entrada y USD 0,50 por millón de salida
en procesamiento estándar. [Fuente oficial del modelo](https://developers.openai.com/api/docs/models/gpt-6-luna).

Ejemplos de cálculo, **no consumo medido**: con 8.000 tokens de entrada total —texto e
imagen— y 2.000 de salida por caso, cien casos cuestan USD 0,18. Con 15.000 de entrada
y 10.000 de salida, cuestan USD 0,65. Los tokens de razonamiento forman parte de la
salida facturada; el uso real depende de imagen, configuración y respuesta.
[Documentación oficial](https://developers.openai.com/api/docs/guides/reasoning#how-reasoning-works).

USD 5 parece suficiente para esta propuesta con ese modelo; USD 10 añade margen, sin
garantizar llamadas ilimitadas. Antes de los cien casos se medirá un piloto separado
de 5–10 casos y se proyectará costo completo, reintentos y uso conocido/desconocido.
Propuesta de reserva con USD 5: hasta USD 1 para evaluación, USD 1 para pruebas y
USD 3 para demo. Son asignaciones máximas de planificación, no topes automáticos ya
implementados. Si la proyección excede la asignación, se revisa antes de iniciar la
corrida completa. Cambiar de modelo exige recalcular; el precio/saldo se revisará
de nuevo antes de diciembre.

## Mediciones y entorno existente

Completado localmente: perfil DEBUG=False/Gunicorn, tres ventanas finales de 50
altas, caché observada, tareas correlacionadas, 360 lecturas, tres canjes positivos
y controles negativos, SQL independiente, API 180/180, navegador 9/9 + SQL 63/63,
auditoría UX y 18 navegaciones descriptivas. Se conservan baselines y fallos.
Los objetivos siguen P50 ≤800 ms, P99 ≤2.000 ms y ≥50 altas persistidas en 60 s;
los límites de extrapolación constan en el [informe](S15_INFORME_RENDIMIENTO.md).
La clasificación y el correo de esos ensayos fueron desactivados/sustituidos.

También terminó el [diagnóstico Heroku ↔ Supabase de solo lectura](S15_DIAGNOSTICO_REMOTO.md):
30 consultas constantes, cinco aperturas y seis rutas públicas. No equivale a un
recorrido autenticado. No se requiere infraestructura remota adicional.
Antes de mutar el despliegue: respaldo coordinado DB+objetos, cuentas y casos de
ensayo identificados y ventana acotada. El [runbook operativo](S15_OPERACION_Y_DEMO.md)
está preparado; no se prevé reset general. Los harnesses de volumen siguen locales.

## Trabajo técnico restante

1. **Con material del usuario:** recibir ZIP/Excel, verificar autorización, independencia
   y cuotas; completar doble revisión humana y resolver desacuerdos antes de predecir.
2. **Con esas entradas:** congelar oro/N/configuración, ejecutar piloto para medir tokens
   y costo, reservar saldo de demo y correr la evaluación independiente con informe.
3. **Con cuentas reales:** comprobar Google, roles institucionales/admin, autor y
   confirmante, recorrido remoto acotado y correo. Preparar respaldo/ventana según
   el runbook ya escrito; el diagnóstico DB y los recorridos locales están terminados.
4. **Operación de consolas:** ejecutar las rotaciones asignadas al usuario en S14-D19
   y verificarlas. No rotar rutinariamente HMAC de identidad conservando perfiles.
5. **Entrega técnica:** registrar integración/CI y revisar los pendientes anteriores
   antes del cierre formal de S15. La evidencia local ya se consolidó y no espera fotos.
6. **S16 y diciembre:** crear snapshot propio si cambian período/cupo, ensayar demo
   con cuentas preparadas y respaldo, completar S16; después actualizar el `.docx`.
   La fecha límite de entrega del documento sigue por concretar.

## Entradas concretas del usuario

- ZIP/Excel y las fotos autorizadas; participar en las etiquetas y acordar su segunda revisión.
- Cuentas previstas; incluir ESSAP además de Municipalidad/ANDE y al menos dos ciudadanos
  verificados para autor/confirmante. Los diez ciudadanos sintéticos de carga los prepara
  la herramienta: no se exigen diez cuentas Google para la carga local.
- Confirmar la fecha límite de entrega del documento, distinta de la defensa si corresponde.
- Definir ventana de las pruebas sobre el despliegue existente cuando el procedimiento
  concreto esté preparado. No hace falta contratar ahora infraestructura adicional.

Los resultados previos permanecen en el [informe local](S15_INFORME_PREPARACION_LOCAL.md).
S15 puede cerrarse cuando termine su trabajo técnico; no se espera a diciembre para medir
ni se declara cerrado por el solo hecho de aprobar estas decisiones.
