import tomllib
from pathlib import Path


def test_el_manifiesto_ht5_tiene_los_33_archivos_y_todos_existen():
    """El manifiesto congelado HT-5 conserva 33 archivos existentes de dominio."""
    backend_dir = Path(__file__).resolve().parents[3]
    with (backend_dir / "coverage-ht5.toml").open("rb") as manifest:
        paths = tomllib.load(manifest)["tool"]["coverage"]["report"]["include"]

    assert len(paths) == len(set(paths)) == 33
    for path in paths:
        relative_path = Path(path)
        assert (backend_dir / relative_path).is_file(), path
        assert not {"tests", "migrations"}.intersection(relative_path.parts), path
        assert relative_path.name not in {
            "admin.py",
            "apps.py",
            "__init__.py",
            "views.py",
            "serializers.py",
        }, path
