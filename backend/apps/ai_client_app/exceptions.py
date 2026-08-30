"""Errores de la integración con el proveedor de IA (S8-D).

La jerarquía existe para una sola decisión: **si conviene reintentar o no**. Un
`except Exception: retry` gastaría cuota indefinidamente ante una credencial
inválida, y un fallo de red aislado que no se reintenta pierde una clasificación
recuperable. Separar ambos casos por tipo hace que la tarea no tenga que
adivinar.
"""

from __future__ import annotations


class AiIntegrationError(Exception):
    """Raíz de los fallos de integración con servicios externos de clasificación."""


class TransientAiError(AiIntegrationError):
    """Fallo pasajero: el mismo intento, más tarde, puede funcionar."""


class PermanentAiError(AiIntegrationError):
    """Fallo que no cambia por insistir. Reintentarlo solo consume cuota."""


class TransientAiProviderError(TransientAiError):
    """Timeout, 408, 429, 5xx o corte de transporte contra el proveedor."""


class PermanentAiProviderError(PermanentAiError):
    """Credencial, permisos, modelo inexistente, petición malformada o interacción fallida."""


class TransientAiMediaError(TransientAiError):
    """El almacenamiento no respondió, pero el objeto debería seguir ahí."""


class PermanentAiMediaError(PermanentAiError):
    """El objeto no existe o su referencia es inválida."""
