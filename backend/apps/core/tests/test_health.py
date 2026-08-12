import pytest
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_health_endpoint_returns_ok_status():
    response = APIClient().get("/api/v1/health/")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] is True
    assert body["redis"] is True
