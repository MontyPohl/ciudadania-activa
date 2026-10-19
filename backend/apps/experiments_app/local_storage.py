from __future__ import annotations

import base64
import contextlib
import fcntl
import mimetypes
import os
import stat
import threading
from collections.abc import Iterator
from pathlib import Path

from django.conf import settings

from apps.core.services.storage import StorageObjectNotFoundError, StorageUnavailableError

from .isolation import initialize_object_root, verify_isolation
from .runtime import isolation_config

#: El marcador de la raíz aislada y el contenido exacto que escribe `initialize_object_root`
#: (`isolation.py`, fuente atestada que no se modifica).
OBJECT_ROOT_MARKER = ".s11-isolated-object-root"
OBJECT_ROOT_MARKER_CONTENT = b"S11 isolated synthetic objects only\n"
_FIRST_INITIALIZATION = threading.Lock()


def upload(bucket: str, key: str, content: bytes, content_type: str) -> None:
    target = _target(bucket, key)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as destination:
            destination.write(content)
    except FileExistsError as exc:
        raise StorageUnavailableError("El objeto sintético ya existe.") from exc


def download(bucket: str, key: str) -> bytes:
    target = _target(bucket, key)
    try:
        return target.read_bytes()
    except FileNotFoundError as exc:
        raise StorageObjectNotFoundError("El objeto sintético no existe.") from exc


def signed_url(bucket: str, key: str, expires_in: int) -> str:
    del expires_in
    content = download(bucket, key)
    mime = mimetypes.guess_type(key)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"


def delete(bucket: str, key: str) -> None:
    target = _target(bucket, key)
    try:
        target.unlink()
    except FileNotFoundError:
        return


def _target(bucket: str, key: str) -> Path:
    root = object_root()
    target = (root / bucket / key).resolve()
    if root not in target.parents:
        raise StorageUnavailableError("La clave de objeto sintética no es segura.")
    return target


def object_root() -> Path:
    """La raíz aislada de objetos. Sólo se inicializa si falta; un marcador válido no se toca.

    Cada guardado de imagen pasaba por `initialize_object_root`, que reescribe el marcador
    aunque ya sea correcto (cambian mtime y ctime). Ahora, con raíz y marcador válidos no hay
    `mkdir`, `chmod` ni escritura. Si falta la raíz o el marcador, la inicialización canónica
    corre una sola vez bajo un candado de primera inicialización y se revalida. Un symlink, un
    tipo inesperado o un contenido distinto falla cerrado: nunca se repara ni se sobrescribe.
    """
    config = isolation_config()
    report = verify_isolation(config)
    state, root = _root_state(Path(config.object_root))
    if state == "valid":
        return root
    with _first_initialization(Path(config.object_root), Path(report.object_root)):
        # Otro proceso pudo inicializarla mientras se esperaba el candado.
        state, root = _root_state(Path(config.object_root))
        if state == "absent":
            initialize_object_root(config)
            state, root = _root_state(Path(config.object_root))
    if state != "valid":
        raise StorageUnavailableError(f"La raíz de objetos sintética no es válida: {state}.")
    return root


def _root_state(configured: Path) -> tuple[str, Path]:
    """`valid`, `absent` o el motivo del rechazo, sin escribir ni seguir symlinks."""
    try:
        info = os.lstat(configured)
    except FileNotFoundError:
        return "absent", configured
    except NotADirectoryError:
        return "parent-unsafe", configured
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return "root-unsafe", configured
    root = configured.resolve()
    marker = root / OBJECT_ROOT_MARKER
    try:
        marker_info = os.lstat(marker)
    except FileNotFoundError:
        return "absent", root
    if not stat.S_ISREG(marker_info.st_mode):
        return "marker-unsafe", root
    try:
        descriptor = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return "marker-unsafe", root
    with os.fdopen(descriptor, "rb") as handle:
        content = handle.read(len(OBJECT_ROOT_MARKER_CONTENT) + 1)
    if content != OBJECT_ROOT_MARKER_CONTENT:
        return "marker-invalid", root
    return "valid", root


@contextlib.contextmanager
def _first_initialization(configured: Path, approved: Path) -> Iterator[None]:
    """Serializa la primera inicialización entre hilos y procesos.

    El candado de proceso se toma sobre el directorio padre de la raíz (`flock` de un
    descriptor de directorio): no escribe nada ni cambia sus metadatos. Sólo crea el padre si
    falta, como hacía el inicializador canónico.
    """
    with _FIRST_INITIALIZATION:
        descriptor = _initialization_parent(configured, approved)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)


def _initialization_parent(configured: Path, approved: Path) -> int:
    """Un descriptor del padre de la raíz, creado si falta. Sólo en la primera inicialización.

    En un checkout limpio el padre no existe todavía: el inicializador canónico lo creaba con
    `mkdir(parents=True)` y el candado tiene que crearlo antes, con la misma semántica. Después
    debe ser un directorio real (no un symlink), el padre de la raíz que aprobó
    `verify_isolation`, y la raíz no puede solaparse con la evidencia.
    """
    parent = configured.parent
    for evidence_root in (
        settings.S11_ARTIFACT_ROOT,
        settings.S11_BACKUP_ROOT,
        settings.S11_RESTORE_ROOT,
    ):
        evidence = Path(evidence_root).resolve()
        if approved == evidence or evidence in approved.parents or approved in evidence.parents:
            raise StorageUnavailableError("La raíz de objetos sintética solapa la evidencia.")
    try:
        # Antes y después del mkdir: no crear nada a través de un ancestro cambiado.
        if parent.resolve() != approved.parent:
            raise StorageUnavailableError("El padre de la raíz de objetos no es el aprobado.")
        parent.mkdir(parents=True, exist_ok=True)
        info = os.lstat(parent)
        if not stat.S_ISDIR(info.st_mode):  # con `lstat`, un symlink nunca es directorio
            raise StorageUnavailableError("El padre de la raíz de objetos no es un directorio.")
        if parent.resolve() != approved.parent:
            raise StorageUnavailableError("El padre de la raíz de objetos no es el aprobado.")
        descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise StorageUnavailableError("El padre de la raíz de objetos no es seguro.") from exc
    opened = os.fstat(descriptor)
    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
        os.close(descriptor)
        raise StorageUnavailableError("El padre de la raíz de objetos cambió al abrirlo.")
    return descriptor
