"""Throttle de lecturas que no deben caerse si cae Redis (S14-D14).

El fallback en vivo de mapa, ranking y métricas es inalcanzable si el propio
throttle, que usa la misma caché, lanza antes de llegar a la vista. Solo las
lecturas que el contrato promete calcular en vivo dejan pasar sin contador; las
escrituras siguen con ScopedRateThrottle y fallan cerrado.
"""

import logging

from redis.exceptions import RedisError
from rest_framework.throttling import ScopedRateThrottle

logger = logging.getLogger(__name__)


class FailOpenScopedRateThrottle(ScopedRateThrottle):
    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except (RedisError, OSError):
            logger.warning("Throttle %s sin caché; se deja pasar la lectura.", self.scope)
            return True
