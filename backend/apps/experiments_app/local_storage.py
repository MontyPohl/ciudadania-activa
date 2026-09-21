from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from apps.core.services.storage import StorageObjectNotFoundError, StorageUnavailableError

from .isolation import initialize_object_root
from .runtime import isolation_config


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
    root = initialize_object_root(isolation_config())
    target = (root / bucket / key).resolve()
    if root not in target.parents:
        raise StorageUnavailableError("La clave de objeto sintética no es segura.")
    return target
