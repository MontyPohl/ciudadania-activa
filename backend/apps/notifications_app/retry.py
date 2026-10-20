"""Política de reintentos automáticos del correo (Sprint 13, S13-D11).

Convive con CT-51: los reintentos automáticos solo ocurren **mientras la notificación
sigue PENDIENTE** y solo ante fallos transitorios del proveedor. FALLIDO sigue siendo
terminal para todo lo automático; recuperarlo exige `resend_notification`.
"""

from datetime import timedelta

#: Intentos totales, el primero incluido. Al agotarlos con un fallo transitorio, FALLIDO.
MAX_ATTEMPTS = 3
#: Espera antes del intento 2 y del intento 3.
BACKOFF = (timedelta(minutes=5), timedelta(minutes=30))
#: Una PENDIENTE sin intentos más vieja que esto perdió su encolado: el barrido la rescata.
RESCUE_AFTER = timedelta(minutes=15)
#: …pero solo dentro de esta ventana. Nunca se envía un correo de hace más de un día.
RESCUE_WINDOW = timedelta(hours=24)
#: Tope de notificaciones que encola una corrida del barrido.
SWEEP_LIMIT = 200
