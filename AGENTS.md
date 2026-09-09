# Ciudadanía Activa — instrucciones para agentes

## Skills versionadas

Las skills de `.agents/skills/` forman parte del repositorio. Úsalas según el
tipo de tarea y no las actualices ni ejecutes scripts incluidos en ellas salvo
que la tarea lo requiera expresamente. Su procedencia y versión fijada están en
`.agents/skills/SOURCES.md`.

- `ui-ux-pro-max`: diseño o refactorización de UI en Next.js/Tailwind.
- `refactoring-ui-design`: pulido de jerarquía, espaciado, tipografía y estados
  de componentes existentes.
- `ux-heuristics`: auditorías de usabilidad y accesibilidad con hallazgos
  priorizados.
- `cro`: optimización de la portada o de formularios públicos no obligatorios.
- `requesting-code-review`: revisión independiente tras cambios importantes o
  antes de integrar.
- `verification-before-completion`: antes de afirmar que un cambio está listo,
  ejecutar las verificaciones que lo demuestren.

## Restricciones del producto

Ciudadanía Activa maneja identidad, fotografías/evidencia y ubicación. No uses
`cro` para eliminar, ocultar o rebajar campos, validaciones, consentimientos o
pasos necesarios para seguridad, privacidad, trazabilidad o requisitos
institucionales. Evita patrones engañosos o de retención; prioriza claridad,
accesibilidad y control de la persona usuaria.

Para cambios de frontend, conserva los contratos de API y prueba los flujos
afectados. Para cambios de seguridad, revisa autenticación, autorización,
datos personales, API, almacenamiento y secretos. Antes de completar trabajo,
ejecuta los comandos aplicables del README (lint, tipos, pruebas y build).
