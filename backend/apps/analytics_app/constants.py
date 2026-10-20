"""Constantes de las métricas del Sprint 13 (contrato S13 §2 y §5)."""

from datetime import date

#: Primer mes que acepta `period`; la tendencia rellena con 0 los meses anteriores.
MIN_PERIOD = date(2026, 1, 1)
#: Meses de la tendencia del dashboard, incluido el período consultado.
TREND_MONTHS = 6
#: Ventana móvil de las métricas públicas, en meses locales.
PUBLIC_WINDOW_MONTHS = 6
#: Umbral de supresión pública (S13-D08): ciudadanos distintos o casos que respaldan
#: una celda. Por debajo, la celda no se publica.
MIN_CELL_SIZE = 5
