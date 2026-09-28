"""Punto de registro de la app experimental en el worker Celery. Aquí no hay tareas.

`config.celery` llama a `app.autodiscover_tasks()`: el worker importa `<app>.tasks` de
cada app instalada al arrancar, antes de atender el pidbox. Registrar aquí el probe de
EX-10 lo deja disponible en el proceso worker sin modificar `apps.py` ni `config.celery`.
El registro es idempotente y no escribe, no crea hilos ni publica nada.
"""

from . import ex10_worker_probe

ex10_worker_probe.register()
