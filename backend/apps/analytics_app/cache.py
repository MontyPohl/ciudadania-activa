"""Caché de agregados con cálculo en vivo si Redis no está disponible."""

import logging
from collections.abc import Callable

from django.core.cache import cache
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


def cached(key: str, ttl: int, compute: Callable[[], dict]) -> dict:
    try:
        value = cache.get(key)
        if value is not None:
            return value
    except (RedisError, OSError):
        logger.warning("La caché de métricas no está disponible; se consulta la base de datos.")
        return compute()
    value = compute()
    try:
        cache.set(key, value, timeout=ttl)
    except (RedisError, OSError):
        logger.warning("No se pudieron guardar las métricas en caché.")
    return value
