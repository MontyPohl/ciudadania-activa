"""Raíz de objetos aislada: un marcador válido no se reescribe al guardar imágenes.

`initialize_object_root` (`isolation.py`, fuente atestada) reescribía el marcador en cada
guardado. `local_storage.object_root` sólo inicializa lo que falta y falla cerrado ante un
marcador o una raíz inseguros, sin repararlos.
"""

import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from apps.core.services.storage import StorageUnavailableError
from apps.experiments_app import isolation, local_storage
from apps.experiments_app.runtime import isolation_config
from apps.experiments_app.tests import local_storage_race

CONTENT = b"S11 isolated synthetic objects only\n"
BUCKET = "s11-reportes"


def marker(settings) -> Path:
    return Path(settings.S11_OBJECT_ROOT) / local_storage.OBJECT_ROOT_MARKER


def metadata(path: Path) -> dict:
    info = os.lstat(path)
    return {
        "inode": info.st_ino,
        "mode": info.st_mode,
        "uid": info.st_uid,
        "gid": info.st_gid,
        "size": info.st_size,
        "nlink": info.st_nlink,
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


@pytest.fixture
def counted(monkeypatch):
    """Cuenta las llamadas al inicializador canónico sin cambiar su comportamiento."""
    calls = []
    real = local_storage.initialize_object_root

    def wrapper(config):
        calls.append(config.object_root)
        return real(config)

    monkeypatch.setattr(local_storage, "initialize_object_root", wrapper)
    return calls


@pytest.fixture
def initialized(s11_local_storage):
    isolation.initialize_object_root(isolation_config())
    return s11_local_storage


def forbid_initializer(monkeypatch):
    def forbidden(config):
        raise AssertionError("un marcador válido no se reinicializa")

    monkeypatch.setattr(local_storage, "initialize_object_root", forbidden)


def test_the_marker_content_is_the_one_the_attested_initializer_writes(s11_local_storage):
    isolation.initialize_object_root(isolation_config())
    assert (
        marker(s11_local_storage).read_bytes()
        == CONTENT
        == (local_storage.OBJECT_ROOT_MARKER_CONTENT)
    )


def test_an_absent_root_is_initialized_once(s11_local_storage, counted):
    assert not Path(s11_local_storage.S11_OBJECT_ROOT).exists()
    local_storage.upload(BUCKET, "reports/a/one.png", b"png", "image/png")
    assert marker(s11_local_storage).read_bytes() == CONTENT
    assert len(counted) == 1
    local_storage.upload(BUCKET, "reports/a/two.png", b"png", "image/png")
    assert len(counted) == 1


def test_an_absent_marker_in_an_existing_root_is_initialized(s11_local_storage, counted):
    Path(s11_local_storage.S11_OBJECT_ROOT).mkdir(parents=True)
    local_storage.upload(BUCKET, "reports/a/one.png", b"png", "image/png")
    assert marker(s11_local_storage).read_bytes() == CONTENT and len(counted) == 1


@pytest.mark.parametrize("mode", [0o600, 0o644])
def test_a_valid_marker_is_never_rewritten_or_chmodded(initialized, monkeypatch, mode):
    path = marker(initialized)
    os.chmod(path, mode)
    before = metadata(path)
    forbid_initializer(monkeypatch)
    for index in range(50):
        local_storage.upload(BUCKET, f"reports/{index}/img.png", b"x" * index, "image/png")
    assert local_storage.download(BUCKET, "reports/7/img.png") == b"x" * 7
    local_storage.delete(BUCKET, "reports/7/img.png")
    assert metadata(path) == before
    assert before["mode"] & 0o777 == mode


@pytest.mark.parametrize(
    "corrupt",
    [
        "marker-symlink",
        "marker-content",
        "marker-empty",
        "marker-directory",
        "root-symlink",
        "root-file",
    ],
)
def test_an_unsafe_root_or_marker_fails_closed_without_repair(
    initialized, monkeypatch, tmp_path, request, corrupt
):
    root = Path(initialized.S11_OBJECT_ROOT)
    path = marker(initialized)
    decoy = tmp_path / "decoy"
    decoy.write_bytes(CONTENT)
    if corrupt == "marker-symlink":
        path.unlink()
        path.symlink_to(decoy)
    elif corrupt == "marker-content":
        path.write_bytes(b"something else\n")
    elif corrupt == "marker-empty":
        path.write_bytes(b"")
    elif corrupt == "marker-directory":
        path.unlink()
        path.mkdir()
    elif corrupt == "root-file":
        shutil.rmtree(root)
        root.write_bytes(CONTENT)
        request.addfinalizer(lambda: root.unlink(missing_ok=True))
        path = root
    else:
        real = root.with_name(root.name + "-real")
        shutil.rmtree(real, ignore_errors=True)
        root.rename(real)
        root.symlink_to(real, target_is_directory=True)
        request.addfinalizer(lambda: (root.unlink(), shutil.rmtree(real, ignore_errors=True)))
    snapshot = {p: os.lstat(p).st_mtime_ns for p in (path, decoy) if os.path.lexists(p)}
    forbid_initializer(monkeypatch)
    with pytest.raises(StorageUnavailableError):
        local_storage.upload(BUCKET, "reports/x/img.png", b"png", "image/png")
    assert {p: os.lstat(p).st_mtime_ns for p in snapshot} == snapshot
    assert decoy.read_bytes() == CONTENT
    assert not root.is_dir() or not (root / BUCKET).exists()


def test_concurrent_saves_on_a_valid_root_never_touch_the_marker(initialized, monkeypatch):
    path = marker(initialized)
    before = metadata(path)
    forbid_initializer(monkeypatch)
    errors = []

    def save(worker):
        try:
            for index in range(10):
                local_storage.upload(BUCKET, f"reports/{worker}/{index}.png", b"png", "image/png")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=save, args=(w,)) for w in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [] and metadata(path) == before


def test_the_first_initialization_is_serialized(s11_local_storage, counted):
    barrier = threading.Barrier(8)
    errors = []

    def save(worker):
        barrier.wait()
        try:
            local_storage.upload(BUCKET, f"reports/{worker}/img.png", b"png", "image/png")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=save, args=(w,)) for w in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(counted) == 1
    assert marker(s11_local_storage).read_bytes() == CONTENT


def test_object_keys_stay_inside_the_root(initialized):
    with pytest.raises(StorageUnavailableError):
        local_storage.upload(BUCKET, "../../escape.png", b"png", "image/png")


def test_isolation_is_still_verified_on_every_call(initialized, monkeypatch):
    calls = []
    real = local_storage.verify_isolation
    monkeypatch.setattr(local_storage, "verify_isolation", lambda c: calls.append(c) or real(c))
    local_storage.upload(BUCKET, "reports/a/img.png", b"png", "image/png")
    local_storage.download(BUCKET, "reports/a/img.png")
    assert len(calls) == 2
    initialized.S11_ENVIRONMENT = "production"
    with pytest.raises(isolation.IsolationError):
        local_storage.download(BUCKET, "reports/a/img.png")


def test_a_marker_swapped_for_a_symlink_after_the_check_is_not_followed(
    initialized, monkeypatch, tmp_path
):
    # Carrera: el marcador era un archivo regular al hacer `lstat` y es un symlink al abrirlo.
    path = marker(initialized)
    decoy = tmp_path / "decoy"
    decoy.write_bytes(CONTENT)
    real_lstat = os.lstat
    regular = real_lstat(decoy)

    def racing_lstat(target, *args, **kwargs):
        if Path(target) == path:
            if not path.is_symlink():
                path.unlink()
                path.symlink_to(decoy)
            return regular
        return real_lstat(target, *args, **kwargs)

    monkeypatch.setattr(local_storage.os, "lstat", racing_lstat)
    forbid_initializer(monkeypatch)
    with pytest.raises(StorageUnavailableError):
        local_storage.object_root()
    assert path.is_symlink() and decoy.read_bytes() == CONTENT


# --- Padre ausente: checkout limpio ------------------------------------------------------


@pytest.fixture
def absent_parent(s11_local_storage, request):
    """La raíz bajo dos niveles que no existen, como `.s11-test-artifacts` en un checkout limpio."""
    top = Path.cwd() / ".s11-test-artifacts" / f"absent-parent-{request.node.name}"
    clean(top)
    s11_local_storage.S11_OBJECT_ROOT = top / "artifacts" / "objects"
    yield s11_local_storage
    clean(top)


@pytest.fixture
def elsewhere(request):
    """Un directorio fuera de la raíz pero dentro de lo que aprueba `verify_isolation`."""
    path = Path.cwd() / ".s11-test-artifacts" / f"elsewhere-{request.node.name}"
    clean(path)
    yield path
    clean(path)


def clean(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    shutil.rmtree(path, ignore_errors=True)


def test_an_absent_parent_is_created_on_the_first_save(absent_parent, counted):
    root = Path(absent_parent.S11_OBJECT_ROOT)
    assert not root.parent.exists() and not root.parent.parent.exists()
    local_storage.upload(BUCKET, "reports/a/one.png", b"png", "image/png")
    assert root.parent.is_dir() and not root.parent.is_symlink()
    assert marker(absent_parent).read_bytes() == CONTENT and len(counted) == 1
    assert local_storage.download(BUCKET, "reports/a/one.png") == b"png"
    before = metadata(marker(absent_parent))
    for index in range(50):
        local_storage.upload(BUCKET, f"reports/{index}/two.png", b"png", "image/png")
    assert len(counted) == 1 and metadata(marker(absent_parent)) == before


def test_the_first_initialization_with_an_absent_parent_is_serialized(absent_parent, counted):
    barrier = threading.Barrier(8)
    errors = []

    def save(worker):
        barrier.wait()
        try:
            local_storage.upload(BUCKET, f"reports/{worker}/img.png", b"png", "image/png")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=save, args=(w,)) for w in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [] and len(counted) == 1
    root = Path(absent_parent.S11_OBJECT_ROOT)
    expected = sorted([local_storage.OBJECT_ROOT_MARKER, BUCKET])
    assert sorted(p.name for p in root.iterdir()) == expected
    assert marker(absent_parent).read_bytes() == CONTENT


# Las carreras entre procesos no hacen `fork` desde pytest: su proceso hereda el estado de los
# tests anteriores (p. ej. un canal de kombu con un BRPOP pendiente bloqueaba a los hijos en sus
# hooks after-fork). La principal usa `spawn`; el control de `fork` corre en un proceso nuevo.


def assert_initialized_once(settings, report, *, inherited_parent_memory):
    root = Path(settings.S11_OBJECT_ROOT)
    outcomes = report["outcomes"]
    assert [o["worker"] for o in outcomes] == list(range(6))  # ni faltan ni se repiten
    assert [o["error"] for o in outcomes] == [None] * 6
    assert report["exitcodes"] == [0] * 6 and report["alive_after"] == []
    assert sum(o["initialized"] for o in outcomes) == 1
    assert {o["inherited_parent_memory"] for o in outcomes} == {inherited_parent_memory}
    path = marker(settings)
    info = os.lstat(path)
    assert stat.S_ISREG(info.st_mode) and path.read_bytes() == CONTENT
    # Cada hijo vio, tras guardar, el mismo marcador: nadie lo reescribió después.
    assert {tuple(o["marker"]) for o in outcomes} == {(info.st_ino, info.st_mtime_ns)}
    expected = sorted([local_storage.OBJECT_ROOT_MARKER, BUCKET])
    assert sorted(p.name for p in root.iterdir()) == expected
    assert not root.is_symlink() and not root.parent.is_symlink()
    before = metadata(path)
    local_storage.upload(BUCKET, "reports/after/img.png", b"png", "image/png")
    assert metadata(path) == before


def test_processes_racing_on_an_absent_parent_initialize_once(absent_parent):
    root = Path(absent_parent.S11_OBJECT_ROOT)
    assert not root.parent.exists()  # ni padre, ni raíz, ni marcador
    values = local_storage_race.settings_values(absent_parent)
    report = local_storage_race.race(values, start_method="spawn")
    assert_initialized_once(absent_parent, report, inherited_parent_memory=False)


def test_a_clean_single_threaded_parent_racing_with_fork_initializes_once(absent_parent):
    root = Path(absent_parent.S11_OBJECT_ROOT)
    assert not root.parent.exists()
    values = json.dumps(local_storage_race.settings_values(absent_parent))
    process = subprocess.Popen(
        [sys.executable, "-m", "apps.experiments_app.tests.local_storage_race", values],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        pytest.fail("el control de fork no terminó a tiempo")
    finally:
        with pytest.raises(ProcessLookupError):  # ningún proceso del grupo sigue vivo
            os.killpg(process.pid, 0)
    assert process.returncode == 0, stderr[-2000:]
    report = json.loads(stdout.strip().splitlines()[-1])
    assert report["parent_threads_at_fork"] == 1 and report["start_method"] == "fork"
    assert_initialized_once(absent_parent, report, inherited_parent_memory=True)


def test_a_race_that_does_not_finish_fails_and_leaves_no_process(absent_parent):
    values = local_storage_race.settings_values(absent_parent)
    with pytest.raises(local_storage_race.RaceTimeout) as raised:
        # Una barrera de tres partes con dos procesos no se completa nunca.
        local_storage_race.race(values, start_method="spawn", workers=2, parties=3, deadline=5)
    assert len(raised.value.pids) == 2
    assert not any(Path(f"/proc/{pid}").exists() for pid in raised.value.pids)


@pytest.mark.parametrize("unsafe", ["symlink", "file", "dangling-symlink"])
def test_an_unsafe_parent_fails_closed_without_creating_anything(
    absent_parent, monkeypatch, elsewhere, unsafe
):
    root = Path(absent_parent.S11_OBJECT_ROOT)
    root.parent.parent.mkdir(parents=True)
    if unsafe == "symlink":
        elsewhere.mkdir()
        root.parent.symlink_to(elsewhere, target_is_directory=True)
    elif unsafe == "file":
        root.parent.write_bytes(b"not a directory\n")
    else:
        root.parent.symlink_to(elsewhere)
    forbid_initializer(monkeypatch)
    with pytest.raises(StorageUnavailableError):
        local_storage.upload(BUCKET, "reports/x/img.png", b"png", "image/png")
    assert not elsewhere.exists() or list(elsewhere.iterdir()) == []
    assert os.path.lexists(root.parent) and not os.path.lexists(root)


@pytest.mark.parametrize("moment", ["after-approval", "before-mkdir"])
def test_an_ancestor_swapped_for_a_symlink_is_rejected(
    absent_parent, monkeypatch, elsewhere, moment
):
    # Carrera: el abuelo no existía al aprobar la raíz y aparece como symlink antes de crearla.
    root = Path(absent_parent.S11_OBJECT_ROOT)
    elsewhere.mkdir()

    def swap():
        root.parent.parent.parent.mkdir(parents=True, exist_ok=True)
        root.parent.parent.symlink_to(elsewhere, target_is_directory=True)

    if moment == "after-approval":
        real = local_storage.verify_isolation
        monkeypatch.setattr(local_storage, "verify_isolation", lambda c: (real(c), swap())[0])
    else:
        real_mkdir = Path.mkdir

        def swapping_mkdir(self, *args, **kwargs):
            if self == root.parent and not os.path.lexists(root.parent.parent):
                swap()
            return real_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", swapping_mkdir)
    forbid_initializer(monkeypatch)
    with pytest.raises(StorageUnavailableError):
        local_storage.upload(BUCKET, "reports/x/img.png", b"png", "image/png")
    # Antes del mkdir nada se crea fuera; después, como mucho el padre vacío, nunca la raíz.
    created = sorted(p.relative_to(elsewhere).as_posix() for p in elsewhere.rglob("*"))
    assert created == ([] if moment == "after-approval" else [root.parent.name])


def test_the_first_initialization_holds_an_exclusive_lock_on_the_parent(absent_parent, monkeypatch):
    root = Path(absent_parent.S11_OBJECT_ROOT)
    events = []
    real_flock, real_init = local_storage.fcntl.flock, local_storage.initialize_object_root

    def spy_flock(descriptor, operation):
        events.append(("flock", os.fstat(descriptor).st_ino, operation))
        return real_flock(descriptor, operation)

    monkeypatch.setattr(local_storage.fcntl, "flock", spy_flock)
    monkeypatch.setattr(
        local_storage, "initialize_object_root", lambda c: events.append(("init",)) or real_init(c)
    )
    local_storage.upload(BUCKET, "reports/a/img.png", b"png", "image/png")
    assert events == [
        ("flock", os.lstat(root.parent).st_ino, local_storage.fcntl.LOCK_EX),
        ("init",),
    ]


def test_a_symlinked_parent_is_rejected_before_it_is_opened(absent_parent, monkeypatch, elsewhere):
    root = Path(absent_parent.S11_OBJECT_ROOT)
    root.parent.parent.mkdir(parents=True)
    elsewhere.mkdir()
    root.parent.symlink_to(elsewhere, target_is_directory=True)
    opened = []
    real_open = os.open

    def spy_open(target, flags, *args, **kwargs):
        if Path(target) == root.parent:
            opened.append(target)
        return real_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(local_storage.os, "open", spy_open)
    forbid_initializer(monkeypatch)
    with pytest.raises(StorageUnavailableError, match="no es un directorio"):
        local_storage.upload(BUCKET, "reports/x/img.png", b"png", "image/png")
    assert opened == []


def test_a_parent_swapped_for_a_symlink_to_itself_before_open_is_not_followed(
    absent_parent, monkeypatch
):
    # Carrera: tras validarlo, el padre se renombra y se sustituye por un symlink al mismo
    # directorio. El inode coincide, así que sólo `O_NOFOLLOW` lo detiene.
    root = Path(absent_parent.S11_OBJECT_ROOT)
    moved = root.parent.with_name(root.parent.name + "-moved")
    real_open = os.open

    def swapping_open(target, flags, *args, **kwargs):
        if Path(target) == root.parent and not root.parent.is_symlink():
            root.parent.rename(moved)
            root.parent.symlink_to(moved, target_is_directory=True)
        return real_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(local_storage.os, "open", swapping_open)
    forbid_initializer(monkeypatch)
    with pytest.raises(StorageUnavailableError):
        local_storage.upload(BUCKET, "reports/x/img.png", b"png", "image/png")
    assert list(moved.iterdir()) == []


def test_a_parent_replaced_between_check_and_open_is_rejected(absent_parent, monkeypatch):
    real_fstat = os.fstat

    def other_directory(descriptor):
        info = real_fstat(descriptor)
        return os.stat_result((info.st_mode, info.st_ino + 1, *tuple(info)[2:]))

    monkeypatch.setattr(local_storage.os, "fstat", other_directory)
    forbid_initializer(monkeypatch)
    with pytest.raises(StorageUnavailableError):
        local_storage.upload(BUCKET, "reports/x/img.png", b"png", "image/png")


@pytest.mark.parametrize("evidence", ["S11_ARTIFACT_ROOT", "S11_BACKUP_ROOT", "S11_RESTORE_ROOT"])
@pytest.mark.parametrize("relation", ["same", "evidence-inside-root", "root-inside-evidence"])
def test_a_root_overlapping_the_evidence_is_never_initialized(
    absent_parent, monkeypatch, evidence, relation
):
    root = Path(absent_parent.S11_OBJECT_ROOT)
    target = {
        "same": root,
        "evidence-inside-root": root / "runs",
        "root-inside-evidence": root.parent,
    }[relation]
    setattr(absent_parent, evidence, target)
    forbid_initializer(monkeypatch)
    with pytest.raises(StorageUnavailableError):
        local_storage.upload(BUCKET, "reports/x/img.png", b"png", "image/png")
    assert not root.parent.exists()


# --- Ruta rápida: marcador válido -----------------------------------------------------------


def test_the_valid_marker_fast_path_does_not_mkdir_lock_chmod_or_write(initialized, monkeypatch):
    root = Path(initialized.S11_OBJECT_ROOT)
    path = marker(initialized)
    before = metadata(path)
    calls = []
    real_mkdir, real_open = Path.mkdir, os.open

    def spy_mkdir(self, *args, **kwargs):
        if self in (root, root.parent, root.parent.parent):
            calls.append(("mkdir", self))
        return real_mkdir(self, *args, **kwargs)

    def spy_open(target, flags, *args, **kwargs):
        if flags & (os.O_CREAT | os.O_WRONLY | os.O_RDWR | os.O_TRUNC) and Path(target) == path:
            calls.append(("open-write", target))
        if Path(target) == root.parent:
            calls.append(("open-parent", target))
        return real_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", spy_mkdir)
    monkeypatch.setattr(local_storage.os, "open", spy_open)
    monkeypatch.setattr(local_storage.os, "chmod", lambda *a, **k: calls.append(("chmod", a)))
    monkeypatch.setattr(local_storage.fcntl, "flock", lambda *a: calls.append(("flock", a)))
    monkeypatch.setattr(
        local_storage, "_initialization_parent", lambda *a: calls.append(("parent", a))
    )
    forbid_initializer(monkeypatch)
    for index in range(50):
        local_storage.upload(BUCKET, f"reports/{index}/img.png", b"png", "image/png")
    assert calls == [] and metadata(path) == before
