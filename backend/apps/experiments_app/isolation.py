from __future__ import annotations

import ipaddress
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

PRODUCTION_MARKERS = ("supabase", "heroku", "railway", "vercel", "production", "prod")


class IsolationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IsolationConfig:
    enabled: bool
    environment: str
    database_url: str
    redis_url: str
    object_root: Path


@dataclass(frozen=True, slots=True)
class IsolationReport:
    safe: bool
    environment: str
    database_host: str
    database_name: str
    redis_host: str
    redis_db: int
    object_root: str


def verify_isolation(config: IsolationConfig) -> IsolationReport:
    if not config.enabled:
        raise IsolationError("S11_EXPERIMENTS_ENABLED debe estar activado explícitamente.")
    if config.environment != "isolated-experiment":
        raise IsolationError("S11_ENVIRONMENT debe ser exactamente isolated-experiment.")

    database = urlparse(config.database_url)
    db_host = database.hostname or ""
    db_name = database.path.lstrip("/")
    if not db_name.startswith("ciudadania_s11_"):
        raise IsolationError("La base debe tener el prefijo exclusivo ciudadania_s11_.")
    _reject_production_target(db_host, db_name)
    _require_local_host(db_host, "base de datos")

    redis = urlparse(config.redis_url)
    redis_host = redis.hostname or ""
    _reject_production_target(redis_host, redis.path)
    _require_local_host(redis_host, "Redis")
    try:
        redis_db = int(redis.path.lstrip("/") or "0")
    except ValueError as exc:
        raise IsolationError("La base lógica de Redis no es válida.") from exc
    if redis_db != 11:
        raise IsolationError("S11 exige la base lógica Redis 11, separada del desarrollo normal.")

    object_root = config.object_root.resolve()
    if object_root == Path("/tmp") or Path("/tmp") in object_root.parents:
        raise IsolationError("El storage de objetos S11 no puede vivir en /tmp.")
    if object_root in (Path("/"), Path.home().resolve()):
        raise IsolationError("La raíz de objetos S11 es demasiado amplia.")

    return IsolationReport(
        safe=True,
        environment=config.environment,
        database_host=db_host,
        database_name=db_name,
        redis_host=redis_host,
        redis_db=redis_db,
        object_root=str(object_root),
    )


def initialize_object_root(config: IsolationConfig) -> Path:
    verify_isolation(config)
    root = config.object_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / ".s11-isolated-object-root"
    marker.write_text("S11 isolated synthetic objects only\n", encoding="utf-8")
    return root


def reset_object_root(config: IsolationConfig) -> Path:
    """Reset idempotente limitado a una raíz previamente marcada."""
    verify_isolation(config)
    root = config.object_root.resolve()
    marker = root / ".s11-isolated-object-root"
    if not marker.is_file():
        raise IsolationError("La raíz de objetos no tiene el marcador de aislamiento S11.")
    for child in root.iterdir():
        if child == marker:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    return root


def _reject_production_target(*values: str) -> None:
    joined = " ".join(values).lower()
    if any(marker in joined for marker in PRODUCTION_MARKERS):
        raise IsolationError("El target contiene un marcador asociado a producción.")


def _require_local_host(host: str, service: str) -> None:
    if host in {"localhost", "db", "redis", "127.0.0.1", "::1"}:
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise IsolationError(f"El host de {service} no está en la allowlist local S11.")
