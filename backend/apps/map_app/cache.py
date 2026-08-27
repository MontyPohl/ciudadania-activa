"""Caché de listados públicos con invalidación generacional O(1)."""

import hashlib
import json
import logging
import uuid

from django.conf import settings
from django.core.cache import cache
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

GENERATION_KEY = "public-reports:v1:generation"
LIST_KEY_PREFIX = "public-reports:v1:g"


def build_public_list_cache_key(request, params) -> str | None:
    """Clave opaca basada únicamente en parámetros públicos canonizados."""
    generation = _get_generation_token()
    if generation is None:
        return None

    category = params.get("category")
    material = {
        "origin": f"{request.scheme}://{request.get_host()}",
        "bbox": params["bbox"].as_cache_value(),
        "state_filter": params["state_filter"],
        "category": category.slug if category is not None else "",
        "page": params["page"],
    }
    encoded = json.dumps(material, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return f"{LIST_KEY_PREFIX}:{generation}:list:{digest}"


def get_public_list(key: str | None):
    if key is None:
        return None
    try:
        return cache.get(key)
    except (RedisError, OSError):
        _warn_cache_unavailable()
        return None


def set_public_list(key: str | None, payload) -> None:
    if key is None:
        return
    try:
        cache.set(key, payload, timeout=settings.PUBLIC_MAP_CACHE_TTL_SECONDS)
    except (RedisError, OSError):
        _warn_cache_unavailable()


def invalidate_public_reports_cache() -> None:
    """Rota la generación; no recorre, borra por patrón ni vacía Redis."""
    try:
        cache.set(GENERATION_KEY, uuid.uuid4().hex, timeout=None)
    except (RedisError, OSError):
        _warn_cache_unavailable()


def _get_generation_token() -> str | None:
    try:
        generation = cache.get(GENERATION_KEY)
        if generation is not None:
            return generation

        candidate = uuid.uuid4().hex
        if cache.add(GENERATION_KEY, candidate, timeout=None):
            return candidate
        return cache.get(GENERATION_KEY)
    except (RedisError, OSError):
        _warn_cache_unavailable()
        return None


def _warn_cache_unavailable() -> None:
    # Deliberadamente sin clave, host, bbox, coordenadas ni URL de Redis.
    logger.warning("La caché del mapa público no está disponible; se consulta la base de datos.")
