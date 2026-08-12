import redis
from django.conf import settings
from django.db import connections
from django.db.utils import InterfaceError, OperationalError
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


def _database_is_up() -> bool:
    try:
        connections["default"].cursor()
    except (InterfaceError, OperationalError):
        return False
    return True


def _redis_is_up() -> bool:
    try:
        return bool(redis.from_url(settings.REDIS_URL).ping())
    except redis.RedisError:
        return False


@api_view(["GET"])
@permission_classes([AllowAny])
def health_view(request):
    db_ok = _database_is_up()
    redis_ok = _redis_is_up()

    return Response(
        {
            "status": "ok" if db_ok and redis_ok else "degraded",
            "version": settings.API_VERSION,
            "db": db_ok,
            "redis": redis_ok,
        }
    )
