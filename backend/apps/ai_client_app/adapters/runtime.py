"""Frontera de resolución del proveedor en tiempo de ejecución (S8-C).

**Deliberadamente sin proveedor configurado.** `get_runtime_adapter()` devuelve
`None`, y por tanto `ai.classify_report` no clasifica nada en ninguna instalación.

El motivo es concreto y no es una formalidad. La creación de reportes ya encola
`ai.classify_report` en producción desde el Sprint 6. Si esta función devolviera
el adaptador falso por defecto, integrar S8-C bastaría para empezar a clasificar
incidencias ciudadanas reales con datos inventados, aplicar esas sugerencias a
`Report` y auditarlas como decisiones. Devolver `None` hace que eso sea
imposible por construcción, no por disciplina.

Las pruebas inyectan un adaptador sustituyendo esta función. Habilitar un
proveedor real —y decidir de dónde sale su configuración— es trabajo de S8-D.

No se añade ningún ajuste ni variable de entorno aquí: mientras no haya proveedor
que configurar, un interruptor sería una decisión tomada antes de tiempo.
"""

from __future__ import annotations

from dataclasses import dataclass

from .base import AiClassificationAdapter


@dataclass(frozen=True, slots=True)
class RuntimeAdapter:
    """Adaptador resuelto junto al modelo con el que responde.

    El `model_name` viaja aquí y no dentro de la respuesta del proveedor: es un
    dato de configuración, no algo que el modelo deba declarar sobre sí mismo.
    Mantenerlo fuera conserva intacta la interfaz `classify(request) -> str` que
    cerró S8-B.
    """

    adapter: AiClassificationAdapter
    model_name: str


def get_runtime_adapter() -> RuntimeAdapter | None:
    """Devuelve el proveedor configurado, o `None` si no hay ninguno.

    En S8-C siempre `None`. Sin red, sin base de datos y sin efectos.
    """
    return None
