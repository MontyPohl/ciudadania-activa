import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

pytestmark = pytest.mark.django_db


def test_get_user_model_returns_custom_user():
    assert get_user_model().__module__ == "apps.auth_app.models"
    assert get_user_model()._meta.label == "auth_app.User"


def test_create_user_sets_expected_defaults():
    User = get_user_model()
    user = User.objects.create_user(email="ciudadano@example.com", password="clave-segura-123")

    assert user.email == "ciudadano@example.com"
    assert user.check_password("clave-segura-123")
    assert user.is_active is True
    assert user.is_staff is False
    assert user.is_superuser is False


def test_create_superuser_sets_staff_and_superuser_flags():
    User = get_user_model()
    admin = User.objects.create_superuser(email="admin@example.com", password="clave-segura-123")

    assert admin.is_staff is True
    assert admin.is_superuser is True


def test_email_is_unique():
    User = get_user_model()
    User.objects.create_user(email="dup@example.com", password="clave-segura-123")

    with pytest.raises(IntegrityError):
        User.objects.create_user(email="dup@example.com", password="otra-clave-123")
