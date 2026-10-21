# S15 — Rendimiento descriptivo del frontend local

Estado: **18/18 navegaciones observadas**, con datos visibles y sin incidencias registradas.
Fecha: 20/10/2026. Esta pasada usa Chromium/Playwright ya instalados y CDP. No se
instala Chrome DevTools MCP ni Lighthouse. Se toma el enfoque de inspección de la
skill `web-perf` con la alternativa local disponible en este entorno.

## Protocolo fijado antes de ejecutar

El script `frontend/scripts/s15_page_performance.mjs` realiza **18 navegaciones**:
tres rutas públicas × dos perfiles × tres repeticiones. Orden fijo: ruta, perfil,
repetición. Cada navegación tiene un contexto anónimo nuevo, service workers
bloqueados y caché de navegador deshabilitada. No se modifica caché de API.

| Perfil | Viewport CSS | Latencia CDP | Descarga | Subida | CPU |
|---|---|---:|---:|---:|---:|
| Escritorio | 1280 × 800 | 0 ms añadidos | Sin límite | Sin límite | 1× |
| Móvil emulado | 390 × 844 | 150 ms | 200.000 bytes/s | 75.000 bytes/s | 4× |

Rutas: `/mapa`, `/ranking` y `/transparencia`. Next sirve un build de producción
en `127.0.0.1:3115`; la API real local está en `127.0.0.1:58115`. El Storage REST
sintético está en `127.0.0.1:18515`. Los demás orígenes, incluidas teselas OSM, se
bloquean. No hay respuestas HTTP simuladas, cuentas de usuario ni llamadas pagas.

Se ejecuta **después** de completar F1…F8 y su verificación SQL, sobre ese mundo.
El programa exige ambos JSON privados aprobados y conserva sus hashes. No acepta
una salida existente. El protocolo, su hash de fuente, build ID y perfiles se
escriben antes de lanzar Chromium. Cada muestra se guarda por separado, sin
sobrescribir corridas parciales. También se registra la versión del navegador.

El perfil backend mantiene TTL de mapa 60 s, ranking 60 s y transparencia 300 s.
Esas cachés pueden estar calientes o vencer durante la serie: no se promete una
comparación frío/caliente. La emulación de red afecta Chromium; no simula el
enlace Heroku ↔ Supabase ni convierte el host en un dispositivo físico.

## Datos recogidos

- Navigation Timing: TTFB desde inicio de navegación, `responseEnd`,
  `DOMContentLoaded` y `load`, cuando ocurren dentro de la ventana.
- FCP y último candidato LCP observado en una ventana fija de **8 segundos**.
  Se conserva la serie mínima de candidatos, sin texto de elementos.
- Entradas de cambios de layout y suma de los que no siguen a una interacción
  reciente. Se etiqueta **suma proxy**, no CLS por ventanas de sesión.
- Tiempo observado hasta `main`, encabezado h1 y un marcador DOM de datos listo:
  región de reportes visibles, lista del ranking o totales de transparencia. Es una
  observación automatizada de DOM visible, no el instante exacto de hidratación.
- Conteos y respuestas de recursos por origen/path/método/tipo, errores de página
  y Resource Timing disponible. No se guardan cookies, headers de autorización ni
  cuerpos de respuestas. El refresh anónimo 401 del BFF es esperado.

Un evento posterior a 8 s o ausente se registra como `null`, nunca como cero.
Los errores y marcadores DOM que no llegan dentro de la ventana se conservan;
el script no reintenta esa muestra ni la sustituye por otra más rápida.

## Reproducción en otra carpeta

Primero terminar y comprobar el recorrido de navegador y SQL. Mantener el mismo
build y levantar una sola vez `next start` en el puerto 3115; no ejecutar cargas
o builds en paralelo. Desde `frontend/`:

```bash
umask 077
export NEXT_TELEMETRY_DISABLED=1
node --experimental-strip-types scripts/s15_page_performance.mjs \
  --journey "$HOME/.local/share/ciudadania-activa/s15/20261020/browser/corrida/journey-result.json" \
  --postcheck "$HOME/.local/share/ciudadania-activa/s15/20261020/browser/verificacion-sql.json" \
  --output "$HOME/.local/share/ciudadania-activa/s15/20261020/browser/perf-nueva"
```

Las rutas son canónicas y pertenecen al árbol privado S15. Al concluir, conservar
protocolo, 18 archivos de muestras, metadata de versiones y resumen con un índice
SHA-256. Revisar las series individuales antes de calcular medianas o rangos.

## Resultado observado

Corrida privada `browser/performance-001`, posterior a F8 y al postchequeo SQL
003 aprobado. Chromium **151.0.7922.34**, Node **v22.23.2**, Linux; build Next
`PF2rq1xfE8A1--Op20U6M`. No se ejecutaron builds ni cargas de volumen durante la
serie. Las 18 muestras se conservaron: ningún reemplazo, error de página, incidencia
HTTP inesperada, observador no soportado ni marcador de datos ausente.

Valores en **milisegundos, mediana (mínimo–máximo)**; tres repeticiones por fila.

| Ruta | Perfil | FCP | Último candidato LCP, proxy | Datos visibles, observación DOM |
|---|---|---:|---:|---:|
| `/mapa` | Escritorio | 100.0 (72.0–228.0) | 200.0 (188.0–392.0) | 930.8 (922.3–1676.0) |
| `/mapa` | Móvil emulado | 660.0 (648.0–664.0) | 1776.0 (1756.0–1828.0) | 3157.7 (3144.4–3163.5) |
| `/ranking` | Escritorio | 92.0 (92.0–96.0) | 184.0 (180.0–188.0) | 293.4 (287.9–327.1) |
| `/ranking` | Móvil emulado | 680.0 (676.0–688.0) | 1844.0 (1824.0–1852.0) | 2191.2 (2165.0–2192.2) |
| `/transparencia` | Escritorio | 100.0 (68.0–100.0) | 100.0 (68.0–100.0) | 220.3 (217.7–326.1) |
| `/transparencia` | Móvil emulado | 676.0 (660.0–680.0) | 676.0 (660.0–680.0) | 2142.6 (2141.8–2157.8) |

La suma proxy de cambios de layout fue constante entre las tres repeticiones de cada
perfil/ruta: mapa 0,026278 escritorio / 0,007902 móvil; ranking 0,005384 / 0,027118;
transparencia 0,002345 / 0,009670. No se convierte esa suma en CLS ni en un veredicto
de Core Web Vitals. La primera muestra de mapa escritorio también se conserva:
datos a 1.676 ms, frente a 922–931 ms en las otras dos.

**Limitación observada de la emulación:** aunque CDP aceptó la configuración de
150 ms, el TTFB móvil registrado fue 5,5–9,8 ms y `responseEnd` 174,4–176,0 ms.
La serie muestra transferencia ralentizada, pero no acredita 150 ms de RTT ni una
red móvil real. La interacción entre interceptación de peticiones y emulación es
una causa posible, no demostrada. Se conservan ambas observaciones y no se corrigen
los tiempos sumándoles una latencia supuesta.

Artefactos privados: `protocol.json`, 18 muestras, `browser.json`, `summary.json`
e `artifact-index.json`. El resumen coincide con las muestras individuales; el
hash de fuente permaneció igual durante la ejecución. El protocolo vincula por
hash el journey, el postchequeo y la fuente; el build ID identifica el build servido.

- Protocolo SHA-256: `42e1bb460719be8ca5a33c6cd19efa395a6ec830571be6bdcbde5516a77e368e`.
- Resumen SHA-256: `751fc9156a77ecfa12facdf36fc9deffe7c5313062c320eadc1da8dc31f88d71`.
- Índice SHA-256: `b380b07ec8354a58211c427d772d15d5d9dc2eb2957086558ac3b765bcc4a8f1`.

## Interpretación y límites

No se publican INP, TBT ni Speed Index: esta instrumentación no los calcula.
Tampoco se calcula P99 con tres repeticiones ni se certifica el P75 de usuarios.
El LCP está limitado a una ventana y se llama **proxy LCP local**. La suma de
shifts no se etiqueta como CLS. Los valores de Resource Timing de otro origen
pueden estar restringidos por `Timing-Allow-Origin`.

Las referencias de Core Web Vitals pertenecen a mediciones con definiciones y
poblaciones concretas; estos ensayos pequeños describen el prototipo y ayudan a
detectar regresiones. No justifican por sí solos modificar una UI ni pronosticar
rendimiento de producción. Cualquier optimización requiere un problema
reproducible y una comparación posterior con el mismo protocolo.

## Fuentes primarias consultadas

- [web.dev: Web Vitals](https://web.dev/articles/vitals), criterios de campo y P75.
- [Chrome DevTools: limitación de red](https://developer.chrome.com/docs/devtools/network/reference#throttling).
- [Chrome DevTools Protocol: Network](https://chromedevtools.github.io/devtools-protocol/tot/Network/).
- [Chrome DevTools Protocol: Emulation](https://chromedevtools.github.io/devtools-protocol/tot/Emulation/).
