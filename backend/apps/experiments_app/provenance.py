from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from redis import Redis

from apps.admin_app.models import AuditLog, Category, Institution
from apps.ai_client_app.models import AiClassification
from apps.reports_app.models import Report, ResolutionEvidence
from apps.rewards_app.models import RewardTransaction

from .canonical import canonical_json_bytes, sha256_bytes, sha256_file
from .runtime import isolation_config


def collect_provenance() -> dict:
    """Snapshot no secreto del runtime aislado inmediatamente antes del run."""
    with connection.cursor() as cursor:
        cursor.execute("SHOW server_version")
        postgres_version = cursor.fetchone()[0]
        cursor.execute("SELECT PostGIS_Lib_Version()")
        postgis_version = cursor.fetchone()[0]
    redis_info = Redis.from_url(isolation_config().redis_url).info("server")
    migrations = sorted(
        f"{app}.{name}" for app, name in MigrationRecorder(connection).applied_migrations()
    )
    requirements = {}
    for path in sorted((settings.BASE_DIR / "requirements").glob("*.txt")):
        requirements[path.name] = sha256_file(path)
    return {
        "declared_images": {
            "database": "postgis/postgis:17-3.5",
            "redis": "redis:7-alpine",
            "backend": "infra-backend:local-build",
        },
        "engines": {
            "postgresql": postgres_version,
            "postgis": postgis_version,
            "redis": redis_info.get("redis_version", "unknown"),
        },
        "dependencies": {
            package: _version(package)
            for package in ("celery", "Django", "djangorestframework", "psycopg", "redis")
        },
        "requirements_sha256": requirements,
        "migrations": {
            "count": len(migrations),
            "sha256": sha256_bytes(canonical_json_bytes(migrations)),
        },
        "initial_counts": {
            "users": get_user_model().objects.count(),
            "institutions": Institution.objects.count(),
            "categories": Category.objects.count(),
            "reports": Report.objects.count(),
            "resolution_evidence": ResolutionEvidence.objects.count(),
            "reward_transactions": RewardTransaction.objects.count(),
            "ai_classifications": AiClassification.objects.count(),
            "audit_logs": AuditLog.objects.count(),
        },
    }


def _version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "not-installed"
