from __future__ import annotations

from apps.ai_client_app.adapters.runtime import RuntimeAdapter

from .synthetic_ai import S11SyntheticAiAdapter


def get_experimental_runtime_adapter() -> RuntimeAdapter:
    """Proveedor determinista exclusivo del proceso aislado S11."""
    return RuntimeAdapter(
        adapter=S11SyntheticAiAdapter(),
        model_name="s11-deterministic-fake-v1",
    )
