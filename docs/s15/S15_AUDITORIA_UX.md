# S15 — Auditoría de usabilidad y accesibilidad del frontend

Fecha: **20/10/2026** (America/Asuncion). Base: `3d89182` con los cambios
locales de `feat/s15-validation`. Evaluación heurística y recorridos automatizados
en Chromium sobre **Next.js en modo producción**, con HTTP interceptado y datos
sintéticos. No se utilizaron cuentas reales ni proveedores externos.

## Resultado y alcance

Se identificó **un problema de severidad 3** y **tres de severidad 2**. Los cuatro
se corrigieron con cambios acotados de contraste, orientación y navegación por
teclado. No se identificaron problemas de severidad 4 en los recorridos cubiertos.

El Quick Diagnostic de `ux-heuristics` pasa de **6/10 a 10/10 dentro del alcance
observado**: se resolvieron las filas afectadas por legibilidad de errores (−2),
orientación de pestañas (−1) y mensaje contradictorio sobre ranking (−1). Este
puntaje es una valoración heurística del recorrido, no una certificación WCAG ni
una medida estadística de satisfacción o facilidad de uso.

Se aplicaron las skills versionadas `ux-heuristics`, `ui-ux-pro-max` y
`refactoring-ui-design`. Se revisaron también los cambios con el checklist
`react-best-practices`: no se añadieron efectos, dependencias, peticiones,
almacenamiento en navegador ni nuevas reglas de autorización. No se ejecutaron
scripts incluidos en las skills.

## Hallazgos y correcciones

| ID | Severidad | Evidencia reproducible antes de corregir | Efecto y corrección |
|---|---:|---|---|
| UX-01 | **3 — mayor** | `/verificacion`, formulario vacío: error a 14 px `rgb(243,112,67)` sobre blanco: **2,913:1**. `Mapa público`, 14 px `rgb(0,178,107)`: **2,770:1**. El patrón se repite en mensajes, acciones y estados. | Personas con baja visión pueden perder el motivo de error o las acciones. Se añadieron tonos de texto `brand-orange-ink` y `brand-green-ink`; se mantuvieron los colores de marca en superficies y elementos decorativos. También se oscurecieron los contornos naranjas de foco afectados. |
| UX-02 | **2 — menor** | `document.title` en `/verificacion` era solo `Ciudadanía Activa`. Las rutas heredadas de perfil, acceso, bandeja, identidad y ciertos detalles no declaraban un título propio. Otras rutas recientes ya tenían metadata correcta. | Se dificulta distinguir pestañas y volver al contexto. Se incorporaron títulos mediante metadata estática de Next, con layouts de servidor para las páginas de cliente existentes. No se movió su lógica interactiva. |
| UX-03 | **2 — menor** | La cabecera y navegación de sección se repiten antes del formulario; no había enlace de salto para quien navega secuencialmente con teclado. Captura de 320 px y `before-facts.json`. | Se añadió como primer enlace `Saltar al contenido principal`, visible al recibir foco. Los landmarks principales tienen un destino estable y `tabIndex=-1`; también las pantallas de carga y acceso denegado. Los landmarks y encabezados previos ya ofrecían mecanismos para tecnologías que navegan por regiones: el hallazgo no equivale por sí solo a declarar incumplimiento WCAG 2.4.1. |
| UX-04 | **2 — menor** | `/perfil` decía `El ranking público todavía no está disponible` aunque `/ranking` está implementado y enlazado en la cabecera. | Genera una expectativa incorrecta al configurar privacidad. El texto ahora explica que la preferencia se aplica al ranking público y puede cambiarse. Se conserva la modalidad obtenida del servidor. |

Severidades según frecuencia, impacto y persistencia de Nielsen: 0 sin problema,
1 cosmético, 2 demora/frustración, 3 fallo importante de tarea, 4 bloqueo de tarea.
Los problemas descritos fueron identificados por inspección de código y navegador;
no se atribuye esta observación a participantes humanos.

### Contraste observado después

El ejecutor calcula luminancia sRGB sobre colores computados por Chromium, con
composición de fondos transparentes. No compara píxeles de letras suavizadas ni
redondea antes de aplicar el umbral.

| Elemento comprobado | Contraste después | Resultado frente a 4,5:1 |
|---|---:|---|
| Error de cédula sobre blanco | 6,364:1 | Pasa |
| Navegación `Mapa público` sobre blanco | 5,623:1 | Pasa |
| Insignia de identidad sobre tinte verde | 5,060:1 | Pasa |
| Acción `Salir` sobre blanco | 6,364:1 | Pasa |
| Error de categoría sobre blanco | 6,364:1 | Pasa |
| Enlace de salto con foco | 6,436:1 | Pasa |

La regresión de contraste falló contra el build anterior por el valor 2,913:1 y
pasó con los tonos corregidos. Esta comprobación cubre los elementos anteriores;
no constituye un análisis automático de todos los píxeles, estados y fondos.

## Recorridos y observaciones verificadas

| Área | Comprobación y evidencia |
|---|---|
| Identidad | Navegación de sección actual, campos etiquetados, error vacío visible, contraste y ausencia de desbordamiento de página a 320 y 1280 px. Se conservan explicaciones sobre cédula, foto privada y revisión humana. |
| Crear reporte | Enter en continuar sin categoría lleva el foco al campo inválido; al avanzar se enfoca el encabezado del paso; volver conserva categoría y descripción. Las specs existentes cubren foto, coordenadas, duplicados, errores de envío y resultado incierto. |
| Canje | Diálogo con nombre/descripción accesible; foco inicial en cancelar, Tab permite confirmar, Escape cancela y devuelve el foco al disparador. Cancelar produce cero POST; aviso de simulación visible. |
| Operador | Selector mantiene el contexto institucional y el enlace de detalle lo incluye; ruta actual identificada; tabla amplia contenida en su propio scroll a 320 px. No se ocultaron columnas del contrato. |
| Métricas | Alternativa de tabla activable por teclado, estado expandido, encabezados de tabla y ausencia de desbordamiento de página a 320 px. |
| Transparencia | El dato suprimido permanece oculto y se muestra la explicación de privacidad. La fixture anónima no muestra sesión de operador. |
| Mapa | Lista como alternativa al uso del marcador; Enter abre ficha y enfoca el panel, Escape cierra y devuelve foco al disparador; sin desbordamiento de página a 320 px. Las teselas se sustituyen por un PNG sintético: el fondo negro de la captura es esa fixture y no evidencia sobre el mapa real. |
| Administración | Las specs existentes verifican revisión de identidad y auditoría/reversión con motivo y consecuencias, además del desbordamiento móvil a 375 px. |
| Orientación | Títulos específicos comprobados en ocho rutas heredadas, incluso sin sesión. El primer Tab alcanza el enlace de salto; al activarlo durante carga y en perfil se enfoca el único contenido principal. |
| Privacidad del ranking | La preferencia privada del perfil se conserva al cargar; copy actualizado. La protección de datos que realmente devuelve la API se verifica en otro carril: el DOM con fixture no demuestra la política SQL. |

No se añadió búsqueda global: las tareas observadas disponen de filtros o selectores
en su contexto y esa función no forma parte del alcance solicitado. Las tablas
institucionales amplias conservan scroll interno; `scrollWidth` de la página no
supera el viewport en los casos probados.

## Verificación

Archivo de regresiones: `frontend/e2e/s15-ux.spec.ts`, **9 casos nuevos**.
La suite anterior tenía 58 casos. Los comandos se ejecutan desde `frontend/`:

```bash
npm run format:check
npm run lint
npm run typecheck
npm test
NEXT_PUBLIC_API_URL=http://127.0.0.1:3106 \
  NEXT_PUBLIC_GOOGLE_CLIENT_ID=e2e-ficticio.apps.googleusercontent.com npm run build
PLAYWRIGHT_PRODUCTION=1 npx playwright test --project=chromium --reporter=json \
  --output=/ruta/privada/ux/accessibility-all > /ruta/privada/ux/accessibility-all.json
```

Resultado observado de las comprobaciones previas al informe: formato, ESLint,
tipos, build y **417 pruebas en 44 archivos de Vitest** aprobados. La corrida
focalizada aprobó **9/9**, sin omitidos ni inestabilidad. La suite completa final
aprobó **67/67**, con **0 omitidos, 0 fallos y 0 inestables**, en aproximadamente
41 segundos. El tipo, build y controles de código corresponden a los cambios
de accesibilidad descritos en este informe.

Evidencia privada, fuera del repositorio:

```text
~/.local/share/ciudadania-activa/s15/20261020/ux/
  before-facts.json
  before-verification-mobile.png
  red.log                         # fallo real previo de contraste
  red/                            # captura, vídeo y trace del fallo
  accessibility-focused.json      # 9 regresiones y adjuntos
  accessibility-all.json          # suite completa final
  capturas-y-contraste/             # 8 capturas y contraste.json extraídos
  build-accessibility.log
  vitest-accessibility.log
  lint-accessibility.log
  typecheck-accessibility.log
  format-accessibility.log
```

La carpeta también conserva la primera corrida completa de **65/65** posterior al
arreglo de contraste y anterior a las mejoras de títulos/salto/copy. Dos fallos de
la primera iteración focalizada se debieron a supuestos del test: el nombre
accesible de tabla incluía un espacio antes de `:` y Shift+Tab del diálogo nativo
no debía asumirse como salto directo al botón final. Se corrigieron los tests;
no se alteró el comportamiento de esos componentes para acomodarlos.

## Límites y comprobaciones posteriores

- Falta observar lectores de pantalla reales (por ejemplo NVDA), zoom de navegador
  y espaciado de texto ampliado. Los nombres, roles y foco de Playwright no
  demuestran cómo se anuncia cada actualización en esas herramientas.
- Esta corrida no emplea personas usuarias, dispositivos físicos ni motores
  WebKit/Firefox. La muestra visual es Chromium, 320/1280 px y las vistas que ya
  cubría la suite existente.
- No es una verificación API = SQL, de permisos del backend ni de Google, Brevo,
  Supabase o workers reales. Los contratos no se modificaron.
- Las capturas son evidencia de ensayo con datos sintéticos. Las capturas finales
  y el vídeo de defensa se tomarán sobre la versión final y su dataset congelado.
- Antes de presentar conformidad integral o un puntaje global del producto, debe
  completarse una revisión manual de tecnologías de asistencia y el recorrido real.

## Referencias primarias

- [W3C: contraste mínimo, criterio 1.4.3](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html).
- [W3C: saltar bloques repetidos, criterio 2.4.1](https://www.w3.org/WAI/WCAG22/Understanding/bypass-blocks.html).
- [W3C: títulos de página, criterio 2.4.2](https://www.w3.org/WAI/WCAG22/Understanding/page-titled.html).
- [Next.js: metadata en páginas y layouts de servidor](https://nextjs.org/docs/app/api-reference/functions/generate-metadata).

Las referencias se consultaron el 20/10/2026. El código conserva Next.js 16.3.0
fijado en el proyecto; no se actualizaron dependencias ni skills.
