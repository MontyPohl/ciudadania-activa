import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_no_quedan_migraciones_pendientes():
    """Los modelos y las migraciones del repositorio están sincronizados."""
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)


@pytest.mark.django_db
def test_la_migracion_inicial_de_auth_app_no_fue_reescrita():
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(None)
    inicial = loader.disk_migrations[("auth_app", "0001_initial")]

    # ADR-0014: la migración que fija AUTH_USER_MODEL crea el modelo y no se toca.
    assert [op.__class__.__name__ for op in inicial.operations] == ["CreateModel"]
    assert loader.disk_migrations[("auth_app", "0002_user_domain_fields")].dependencies == [
        ("auth_app", "0001_initial")
    ]
