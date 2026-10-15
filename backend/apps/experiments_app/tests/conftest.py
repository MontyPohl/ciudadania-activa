"""Private, disposable roots for the S11 offline contract suites."""

import os
import tempfile
from pathlib import Path

import pytest


def _default_fixture_parent():
    parent = Path.home().resolve(strict=True)
    checkout = Path(__file__).resolve().parents[4]
    if not parent.is_dir() or any(
        parent == denied or parent.is_relative_to(denied)
        for denied in (checkout, Path("/tmp"), Path("/var/tmp"))
    ):
        raise RuntimeError(
            "S11 default fixture parent must be outside checkout and temporary roots"
        )
    return parent


@pytest.fixture(scope="session", autouse=True)
def s11_synthetic_roots():
    names = ("S11_B1_TEST_BASE", "S11_B2_TEST_BASE", "S11_B3_TEST_BASE")
    originals = {name: os.environ.get(name) for name in names}
    missing = [name for name in names if name not in os.environ]
    if not missing:
        yield None
        return
    # Preserve explicit roots exactly. Only newly allocated children are owned.
    parent = _default_fixture_parent()
    with tempfile.TemporaryDirectory(prefix="s11-offline-fixtures-", dir=parent) as base:
        root = Path(base)
        root.chmod(0o700)
        for name in missing:
            child = root / name.lower()
            child.mkdir(mode=0o700)
            os.environ[name] = str(child)
        try:
            yield root
        finally:
            for name, previous in originals.items():
                if previous is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = previous
