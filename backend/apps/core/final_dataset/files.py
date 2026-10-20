"""Objetos persistentes y manifiestos privados. Nunca contienen tokens congelados."""

import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath

from .isolation import DatasetError
from .recipe import digest


def write_json(path: Path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".s15-", delete=False
        ) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), 0o600)
            json.dump(value, output, ensure_ascii=False, sort_keys=True, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def seal(document: dict) -> dict:
    return {**document, "manifest_sha256": digest(document)}


def read_manifest(path: Path) -> dict:
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise DatasetError("El manifiesto debe ser un archivo privado sin symlinks.")
    document = json.loads(path.read_text())
    expected = document.pop("manifest_sha256", None)
    if expected != digest(document):
        raise DatasetError("Hash de manifiesto divergente; reconstruya un entorno vacío separado.")
    document["manifest_sha256"] = expected
    if document.get("state") != "complete":
        raise DatasetError("El manifiesto no está completo; no se permite continuar ni reparar.")
    return document


class LocalObjects:
    def __init__(self, root: Path, journal=None):
        self.root = root
        self.objects = []
        self.journal = journal

    def path(self, bucket, key):
        parts = PurePosixPath(key).parts
        if (
            bucket not in {"s15-private", "s15-reports"}
            or not parts
            or key.startswith("/")
            or any(p in {"..", "."} for p in parts)
        ):
            raise DatasetError("Objeto fuera del almacenamiento S15.")
        path = self.root / "objects" / bucket / key
        if path.resolve() != path.absolute() or any(p.is_symlink() for p in (path, *path.parents)):
            raise DatasetError("Objeto local inseguro.")
        return path

    def upload(self, bucket, key, content, content_type):
        path = self.path(bucket, key)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(content)
        entry = {
            "bucket": bucket,
            "key": key,
            "mime": content_type,
            "stored_sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }
        self.objects.append(entry)
        if self.journal:
            self.journal("uploaded", self.objects)

    def download(self, bucket, key):
        return self.path(bucket, key).read_bytes()

    def delete(self, bucket, key):
        self.path(bucket, key).unlink(missing_ok=True)
        self.objects = [o for o in self.objects if (o["bucket"], o["key"]) != (bucket, key)]

    def signed_url(self, *args, **kwargs):
        raise DatasetError("Las capacidades firmadas se regeneran en un servidor local separado.")
