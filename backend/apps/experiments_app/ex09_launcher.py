"""Lanzador de host de EX-09 (S11-C3, D2).

Se ejecuta en el host, sin Django. Verifica el commit y el working tree antes de cada
paso y lanza el comando `s11_ex09` dentro de un contenedor efímero
(`docker compose run --rm`) definido en `infra/docker-compose.s11-ex09.yml`, que no
carga ningún `env_file`, no tiene base de datos ni Redis y sólo monta el backend en
solo lectura y la raíz de evidencia S11.

El entorno del proceso hijo se construye desde cero: sólo lleva lo que Docker
necesita, las dos claves de custodia y, **únicamente para `run-lot`**, la variable
`S11_EX09_PROVIDER_KEY`, que se pasa por nombre y nunca se escribe en disco ni en la
línea de comandos.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from .harness import HarnessError, verify_repository

PROVIDER_KEY_ENV = "S11_EX09_PROVIDER_KEY"
COMPOSE_FILE = "infra/docker-compose.s11-ex09.yml"
COMPOSE_PROJECT = "ciudadania-s11-ex09"
SERVICE = "ex09"
ACTIONS = ("build", "preflight", "start", "record", "run-lot", "finalize", "status")
_GIT_ACTIONS = {"preflight", "start", "run-lot", "finalize"}
_CUSTODY_ENV = ("S11_CUSTODY_HMAC_KEY", "S11_PUBLICATION_APPROVAL_PUBLIC_KEY")
_DOCKER_ENV = ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CONTEXT")

Runner = Callable[..., subprocess.CompletedProcess]


class LauncherError(RuntimeError):
    """El paso no se lanza. El mensaje nunca contiene un secreto."""


def compose_base() -> tuple[str, ...]:
    return ("docker", "compose", "-p", COMPOSE_PROJECT, "-f", COMPOSE_FILE)


def child_environment(environ: Mapping[str, str], *, include_key: bool) -> dict[str, str]:
    env = {name: environ[name] for name in _DOCKER_ENV if environ.get(name)}
    for name in _CUSTODY_ENV:
        if not environ.get(name, "").strip():
            raise LauncherError(f"{name} debe exportarse en la sesión del operador.")
        env[name] = environ[name]
    env["S11_HOST_UID"] = str(os.getuid())
    env["S11_HOST_GID"] = str(os.getgid())
    if include_key:
        key = environ.get(PROVIDER_KEY_ENV, "")
        if not key.strip():
            raise LauncherError(f"run-lot exige {PROVIDER_KEY_ENV} con la clave temporal del lote.")
        env[PROVIDER_KEY_ENV] = key
    return env


def build_command(action: str, arguments: Sequence[str], *, include_key: bool) -> tuple[str, ...]:
    if action == "build":
        return (*compose_base(), "build", SERVICE)
    command = (*compose_base(), "run", "--rm", "--no-TTY")
    if include_key:
        # Sólo el nombre: Docker toma el valor del entorno del proceso hijo.
        command += ("-e", PROVIDER_KEY_ENV)
    return (*command, SERVICE, "python", "manage.py", "s11_ex09", action, *arguments)


def containers_present(
    repo_root: Path, env: Mapping[str, str], *, runner: Runner = subprocess.run
) -> bool:
    completed = runner(
        (*compose_base(), "ps", "-a", "-q"),
        cwd=repo_root,
        env=dict(env),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise LauncherError("No se pudo consultar el estado del contenedor efímero.")
    return bool(completed.stdout.strip())


def launch(
    action: str,
    arguments: Sequence[str],
    *,
    repo_root: Path,
    expected_git_sha: str,
    environ: Mapping[str, str],
    runner: Runner = subprocess.run,
) -> int:
    if action not in ACTIONS:
        raise LauncherError(f"Acción desconocida: {action!r}.")
    arguments = list(arguments)
    if "--container-absent" in arguments or "--git-clean" in arguments:
        raise LauncherError("--container-absent y --git-clean los añade sólo el lanzador.")
    try:
        verify_repository(repo_root, expected_git_sha)
    except HarnessError as exc:
        raise LauncherError(str(exc)) from exc

    include_key = action == "run-lot"
    if not include_key and environ.get(PROVIDER_KEY_ENV, "").strip():
        raise LauncherError(f"Retira {PROVIDER_KEY_ENV} de la sesión: sólo run-lot la admite.")
    env = child_environment(environ, include_key=include_key)

    if action in _GIT_ACTIONS:
        arguments += ["--git-sha", expected_git_sha, "--git-clean"]
    if action in {"run-lot", "record"} and containers_present(repo_root, env, runner=runner):
        raise LauncherError("Existe un contenedor EX-09 previo; no se lanza otro.")
    if action == "record" and _event(arguments) == "cleanup":
        # Atestación verificada por el propio lanzador, no declarada a mano.
        arguments.append("--container-absent")

    completed = runner(
        build_command(action, arguments, include_key=include_key),
        cwd=repo_root,
        env=env,
        check=False,
    )
    if include_key and containers_present(repo_root, env, runner=runner):
        raise LauncherError("El contenedor efímero del lote sigue presente tras terminar.")
    return completed.returncode


def _event(arguments: Sequence[str]) -> str | None:
    for index, value in enumerate(arguments):
        if value == "--event" and index + 1 < len(arguments):
            return arguments[index + 1]
        if value.startswith("--event="):
            return value.split("=", 1)[1]
    return None
