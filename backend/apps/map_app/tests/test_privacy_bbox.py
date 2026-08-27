import pytest
from django.contrib.gis.geos import Point

from apps.map_app.bbox import parse_and_canonicalize_bbox

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/public/reports/"


def test_dos_puntos_de_una_celda_publica_son_indistinguibles_por_bbox_adaptativo(
    api_client, public_report_factory
):
    first = public_report_factory(location=Point(-57.6359, -25.2639, srid=4326))
    second = public_report_factory(location=Point(-57.6356, -25.2636, srid=4326))
    attacks = (
        "-57.63595,-25.26395,-57.63575,-25.26375",
        "-57.63585,-25.26385,-57.63555,-25.26355",
    )

    responses = [api_client.get(LIST_URL, {"bbox": attack}).json() for attack in attacks]
    result_sets = [{row["id"] for row in response["results"]} for response in responses]

    assert result_sets == [{str(first.pk), str(second.pk)}] * 2
    assert {(row["longitude"], row["latitude"]) for row in responses[0]["results"]} == {
        (-57.636, -25.264)
    }
    assert parse_and_canonicalize_bbox(attacks[0]) == parse_and_canonicalize_bbox(attacks[1])
