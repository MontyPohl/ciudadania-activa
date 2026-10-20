"""Guard cerrado y explícito antes de leer datos o tocar artefactos S15."""

from pathlib import Path
from urllib.parse import urlsplit


class DatasetError(RuntimeError):
    pass


def guard_environment(settings, artifact_root: Path) -> Path:
    if settings.DEBUG is not True:
        raise DatasetError("S15 requiere DEBUG=True y un entorno exclusivamente local.")
    db = settings.DATABASES["default"]
    if db.get("NAME") not in {"ciudadania_s15", "test_ciudadania_s15"}:
        raise DatasetError("La base no pertenece a la lista sintética S15.")
    if db.get("HOST") not in {"localhost", "127.0.0.1", "ca-s15-db"}:
        raise DatasetError("Host de base S15 no permitido.")
    if "postgis" not in db.get("ENGINE", ""):
        raise DatasetError("S15 requiere PostgreSQL/PostGIS.")
    redis = getattr(settings, "REDIS_URL", "")
    parsed = urlsplit(redis)
    if (
        parsed.scheme != "redis"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path != "/0"
        or (parsed.hostname, parsed.port)
        not in {
            ("ca-s15-redis", 6379),
            ("localhost", 6395),
            ("127.0.0.1", 6395),
        }
    ):
        raise DatasetError("Redis debe ser una instancia S15 dedicada, no otra base lógica.")
    if (
        any(
            getattr(settings, name, None) != redis
            for name in (
                "CELERY_BROKER_URL",
                "CELERY_RESULT_BACKEND",
            )
        )
        or settings.CACHES.get("default", {}).get("LOCATION") != redis
    ):
        raise DatasetError("Cache, broker y resultados deben apuntar a la misma instancia S15.")
    if getattr(settings, "S11_LOCAL_STORAGE_ENABLED", False):
        raise DatasetError("El backend histórico S11 no puede usarse para S15.")
    if getattr(settings, "SUPABASE_URL", "") != "http://127.0.0.1:18515":
        raise DatasetError("Storage debe declarar el destino local S15 reservado.")
    if (
        getattr(settings, "SUPABASE_STORAGE_BUCKET_PRIVATE", "") != "s15-private"
        or getattr(settings, "SUPABASE_STORAGE_BUCKET_REPORTS", "") != "s15-reports"
    ):
        raise DatasetError("Buckets S15 privados no permitidos.")
    root = Path(artifact_root)
    resolved = root.resolve()
    allowed = (Path("/tmp/ciudadania-s15"), Path("/out/s15"))
    if not root.is_absolute() or not any(resolved == p or p in resolved.parents for p in allowed):
        raise DatasetError("Artefactos privados permitidos solo en /tmp/ciudadania-s15 o /out/s15.")
    for component in (root, *root.parents):
        if component.is_symlink():
            raise DatasetError("La ruta de artefactos no admite enlaces simbólicos.")
    if root.exists() and (not root.is_dir() or root.stat().st_mode & 0o077):
        raise DatasetError("El directorio de artefactos debe ser privado (0700).")
    return resolved
