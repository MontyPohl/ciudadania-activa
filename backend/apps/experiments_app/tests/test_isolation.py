import shutil
from pathlib import Path

import pytest

from apps.experiments_app.isolation import (
    IsolationConfig,
    IsolationError,
    initialize_object_root,
    reset_object_root,
    verify_isolation,
)


def config(**overrides):
    values = {
        "enabled": True,
        "environment": "isolated-experiment",
        "database_url": "postgis://u:p@db:5432/ciudadania_s11_experiment",
        "redis_url": "redis://redis:6379/11",
        "object_root": Path.cwd() / "var" / "s11-objects",
    }
    values.update(overrides)
    return IsolationConfig(**values)


def test_accepts_only_explicit_isolated_targets():
    report = verify_isolation(config())
    assert report.safe is True
    assert report.database_name == "ciudadania_s11_experiment"
    assert report.redis_db == 11


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"enabled": False}, "activado"),
        ({"environment": "development"}, "exactamente"),
        ({"database_url": "postgis://u:p@db/x"}, "prefijo"),
        ({"database_url": "postgis://u:p@supabase.co/ciudadania_s11_x"}, "producción"),
        ({"database_url": "postgis://u:p@remote/ciudadania_s11_x"}, "allowlist"),
        ({"redis_url": "redis://redis:6379/0"}, "Redis 11"),
        ({"object_root": Path("/tmp/s11")}, "/tmp"),
    ],
)
def test_rejects_unsafe_targets(overrides, message):
    with pytest.raises(IsolationError, match=message):
        verify_isolation(config(**overrides))


def test_reset_is_reproducible_and_idempotent():
    root = Path.cwd() / ".s11-test-artifacts" / "isolated-object-reset"
    shutil.rmtree(root, ignore_errors=True)
    isolated = config(object_root=root)
    try:
        initialize_object_root(isolated)
        (root / "synthetic-object.bin").write_bytes(b"synthetic")
        reset_object_root(isolated)
        reset_object_root(isolated)
        assert sorted(path.name for path in root.iterdir()) == [".s11-isolated-object-root"]
    finally:
        shutil.rmtree(root, ignore_errors=True)
