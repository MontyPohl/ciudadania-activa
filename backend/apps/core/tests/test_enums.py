from apps.core.enums import (
    ActorType,
    RejectionReason,
    ReportPriority,
    ReportSource,
    ReportStatus,
)


def test_estados_del_reporte_son_los_siete_del_anexo_a2():
    assert ReportStatus.values == [
        "CREADO",
        "CLASIFICADO",
        "ASIGNADO",
        "EN_PROCESO",
        "RESUELTO",
        "CERRADO",
        "RECHAZADO",
    ]


def test_no_existe_un_estado_validado():
    """La validación institucional es un evento auditable, no un estado (RF-28)."""
    assert "VALIDADO" not in ReportStatus.values
    assert "VALIDADO" not in ReportStatus.names


def test_prioridades():
    assert ReportPriority.values == ["BAJA", "MEDIA", "ALTA"]


def test_origenes_del_reporte():
    assert ReportSource.values == ["platform", "x_mock"]


def test_motivos_de_rechazo_tipificados():
    assert RejectionReason.values == [
        "fuera_de_competencia",
        "duplicado",
        "informacion_insuficiente",
        "contenido_inapropiado",
    ]


def test_tipos_de_actor_de_auditoria():
    assert ActorType.values == ["human", "ai", "system"]
