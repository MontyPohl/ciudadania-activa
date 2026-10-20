# Decisiones del módulo de incentivos

## Objetivo

Incentivar la participación ciudadana y aumentar el impacto urbano real mediante reportes válidos, confirmaciones y resolución de incidencias.

## Acciones que generan puntos

- Crear reporte: +20 puntos pendientes.
- Validación institucional: se acreditan los 20 pendientes y se agregan +30 puntos.
- Resolución del reporte: +50 puntos.
- El ciclo inicial puede aportar 100 puntos por reporte propio (20 + 30 + 50). Tras
  reversión/revalidación S10 puede generar bonus nuevos por ciclo, sin reactivar
  originales; el valor acreditado se deriva del ledger ([ADR-0013](adr/ADR-0013-politica-de-reversion.md)).
- Confirmar reporte de otro usuario: +10 puntos pendientes.

## Reglas de confirmación

- Solo ciudadanos verificados.
- Una confirmación por usuario por reporte.
- No se puede confirmar un reporte propio.
- Solo reportes activos pueden confirmarse.
- La confirmación significa: “Acabo de verificar que todavía sigue ahí”.

## Puntos y créditos

- Los puntos de impacto son globales y no se gastan.
- Los créditos son por institución.
- Conversión 1:1 entre punto acreditado y crédito institucional.
- Los créditos no vencen.

## Recompensas

- Costo fijo del canje MVP: 1.000 créditos; el servidor lo impone.
- Máximo un canje por institución por usuario por mes.
- En el MVP la recompensa es simulada mediante un código de vale.
- Los descuentos reales quedan para una implementación futura.

## Ranking

- Top 10 mensual.
- Solo usa puntos acreditados.
- El ranking consulta el mes civil actual en `America/Asuncion` por `credited_at`; no
  borra filas ni conserva snapshots de períodos cerrados.
- El impacto histórico no se borra.
- El usuario puede mostrarse con nombre completo, nombre + inicial, alias o como “Usuario privado”.

## Validación y reversión

- Un reporte se considera validado cuando un operador institucional lo acepta como incidencia legítima.
- Si una validación se revierte, también se revierten los puntos y créditos derivados.
- Los reportes rechazados no generan puntos.

## Antiabuso MVP

- Detección de archivos de imagen idénticos.
- Posible duplicado sugerido, pero el ciudadano puede continuar si no corresponde.
- IA dudosa o inconsistente → revisión humana.
- Sin GPS obligatorio para confirmar en el MVP.

## Mejoras futuras

- Insignias y niveles.
- Campañas temporales.
- Validación por proximidad GPS.
- Similitud avanzada de imágenes.
- Recompensas reales.
- Antifraude avanzado.
