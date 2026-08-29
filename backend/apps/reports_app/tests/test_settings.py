from django.urls import resolve


def test_configuracion_central_s6(settings):
    assert settings.REPORT_DUPLICATE_RADIUS_METERS == 100
    assert settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] == {
        "identity_submit": "5/hour",
        "public_map": "120/min",
        "report_create": "5/hour",
        "possible_duplicates": "60/min",
        "reports_mine": "120/hour",
        "report_confirm": "30/hour",
    }
    assert "REPORT_DUPLICATE_SAME_CATEGORY" not in dir(settings)


def test_vistas_declaran_scopes_separados():
    from apps.reports_app.views import (
        ConfirmReportView,
        MyReportsView,
        PossibleDuplicatesView,
        ReportCreateView,
    )

    assert ReportCreateView.throttle_scope == "report_create"
    assert PossibleDuplicatesView.throttle_scope == "possible_duplicates"
    assert MyReportsView.throttle_scope == "reports_mine"
    assert ConfirmReportView.throttle_scope == "report_confirm"


def test_routing_resuelve_las_tres_rutas_exactas_sin_colision():
    from apps.reports_app.views import MyReportsView, PossibleDuplicatesView, ReportCreateView

    assert resolve("/api/v1/reports/").func.view_class is ReportCreateView
    assert resolve("/api/v1/reports/possible-duplicates/").func.view_class is PossibleDuplicatesView
    assert resolve("/api/v1/reports/mine/").func.view_class is MyReportsView
