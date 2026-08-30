"""Adaptadores de proveedor de clasificación IA.

Frontera del proveedor (ADR-0008): lo que hay dentro de este paquete es lo único
que puede conocer a un proveedor concreto. El dominio no importa ningún SDK.

S8-B solo contiene el adaptador falso. El adaptador real de Gemini es de S8-C.
"""

from .base import AiClassificationAdapter
from .fake import FakeAiClassificationAdapter

__all__ = ["AiClassificationAdapter", "FakeAiClassificationAdapter"]
