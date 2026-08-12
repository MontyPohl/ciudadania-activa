import logging

from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is None:
        logger.error("Excepción no manejada en la API.", exc_info=exc)
        return Response(
            {"detail": "Ha ocurrido un error inesperado en el servidor."},
            status=500,
        )

    if isinstance(response.data, dict) and "detail" not in response.data:
        response.data = {"detail": response.data}

    return response
