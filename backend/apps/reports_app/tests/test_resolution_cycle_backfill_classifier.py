"""Casos negativos del clasificador histórico compartido por comando y migración."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from apps.reports_app.services.resolution_cycle_backfill import (
    SEED_STATUS,
    AuditSnapshot,
    Classification,
    ConfirmationSnapshot,
    HistorySnapshot,
    ReportSnapshot,
    RewardSnapshot,
    classify_universe,
    parse_cycle,
    parse_uuid_reference,
)

NOW = datetime.now(UTC)


def _report(**overrides):
    values = {
        "id": uuid.uuid4(),
        "status": "CREADO",
        "citizen_id": uuid.uuid4(),
        "institution_id": uuid.uuid4(),
        "created_at": NOW,
        "validated_at": None,
        "validated_by_id": None,
        "resolved_at": None,
        "description": "Reporte histórico real.",
        "resolution_cycle": None,
    }
    values.update(overrides)
    return ReportSnapshot(**values)


def _history(report, **overrides):
    values = {
        "id": uuid.uuid4(),
        "report_id": report.id,
        "previous_status": "",
        "new_status": "CREADO",
        "created_at": NOW + timedelta(seconds=1),
        "resolution_cycle": None,
    }
    values.update(overrides)
    return HistorySnapshot(**values)


def _reward(report, **overrides):
    values = {
        "id": uuid.uuid4(),
        "event_type": "REPORT_CREATED",
        "user_id": report.citizen_id,
        "report_id": report.id,
        "confirmation_id": None,
        "institution_id": None,
        "status": "PENDIENTE",
        "impact_delta": 20,
        "credit_delta": 20,
        "created_at": NOW + timedelta(seconds=2),
        "credited_at": None,
        "voided_at": None,
        "reversed_at": None,
        "related_transaction_id": None,
        "resolution_cycle": None,
    }
    values.update(overrides)
    return RewardSnapshot(**values)


def _classify(*, reports, histories=(), confirmations=(), rewards=(), audits=()):
    return classify_universe(
        reports=list(reports),
        histories=list(histories),
        confirmations=list(confirmations),
        evidences=[],
        rewards=list(rewards),
        audits=list(audits),
    )


def _reason_codes(result):
    return {reason.code for reason in result.global_reasons}.union(
        reason.code for item in result.reports for reason in item.reasons
    )


def test_cycle_zero_explicito_es_aceptado():
    report = _report()
    result = _classify(reports=[report], histories=[_history(report)], rewards=[_reward(report)])

    assert result.invalid is False
    assert result.reports[0].classification == Classification.CYCLE_0


def test_cycle_zero_admite_confirmacion_ciudadana():
    """Una confirmación no abre ciclo, así que el reporte sigue en el ciclo 0.

    Reproduce el caso real que abortó la auditoría de producción: un reporte en CREADO,
    sin validar, con una confirmación ciudadana registrada en auditoría.
    """
    report = _report()
    created = AuditSnapshot(
        id=uuid.uuid4(),
        action="report.created",
        report_id=report.id,
        institution_id=None,
        cycle=None,
        created_at=NOW + timedelta(seconds=1),
    )
    confirmed = AuditSnapshot(
        id=uuid.uuid4(),
        action="report.confirmed",
        report_id=report.id,
        institution_id=None,
        cycle=None,
        created_at=NOW + timedelta(seconds=3),
    )

    result = _classify(
        reports=[report],
        histories=[_history(report)],
        rewards=[_reward(report)],
        audits=[created, confirmed],
    )

    assert result.invalid is False
    assert result.reports[0].classification == Classification.CYCLE_0


def test_accion_de_ciclo_avanzado_sigue_siendo_unknown():
    """`report.resolution_evidence_created` exige ciclo >= 1: no puede colarse en la lista.

    Se usa esta acción y no `report.closed` porque las acciones con transición de estado
    asociada se rechazan antes, como INCONSISTENT por `AUDIT_TRANSITION_MISSING`. Esta
    llega hasta el filtro de acciones permitidas, que es justo lo que se quiere cubrir.
    """
    report = _report()
    created = AuditSnapshot(
        id=uuid.uuid4(),
        action="report.created",
        report_id=report.id,
        institution_id=None,
        cycle=None,
        created_at=NOW + timedelta(seconds=1),
    )
    evidence_created = AuditSnapshot(
        id=uuid.uuid4(),
        action="report.resolution_evidence_created",
        report_id=report.id,
        institution_id=None,
        cycle=None,
        created_at=NOW + timedelta(seconds=3),
    )

    result = _classify(
        reports=[report],
        histories=[_history(report)],
        rewards=[_reward(report)],
        audits=[created, evidence_created],
    )

    assert result.invalid is True
    assert result.reports[0].classification == Classification.UNKNOWN


def test_report_created_sin_report_id_es_inconsistente():
    report = _report()
    reward = replace(_reward(report), report_id=None)

    result = _classify(reports=[report], histories=[_history(report)], rewards=[reward])

    assert result.invalid is True
    assert "REPORT_CREATED_OWNERSHIP_INVALID" in _reason_codes(result)


def test_reward_con_report_inexistente_es_inconsistente():
    report = _report()
    reward = replace(_reward(report), report_id=uuid.uuid4())

    result = _classify(reports=[report], histories=[_history(report)], rewards=[reward])

    assert result.invalid is True
    assert "REWARD_REPORT_MISSING" in _reason_codes(result)


def test_ownership_directo_y_confirmacion_contradictorios_es_inconsistente():
    first = _report()
    second = _report()
    confirmation = ConfirmationSnapshot(
        id=uuid.uuid4(),
        report_id=second.id,
        citizen_id=uuid.uuid4(),
        created_at=NOW + timedelta(seconds=1),
    )
    reward = replace(
        _reward(first),
        event_type="CONFIRMATION_CREATED",
        user_id=confirmation.citizen_id,
        confirmation_id=confirmation.id,
        impact_delta=10,
        credit_delta=10,
    )

    result = _classify(
        reports=[first, second],
        histories=[_history(first), _history(second)],
        confirmations=[confirmation],
        rewards=[reward],
    )

    assert result.invalid is True
    assert "REWARD_OWNERSHIP_CONTRADICTORY" in _reason_codes(result)


def test_report_created_no_admite_linkage_de_confirmacion():
    report = _report()
    confirmation = ConfirmationSnapshot(
        id=uuid.uuid4(),
        report_id=report.id,
        citizen_id=uuid.uuid4(),
        created_at=NOW + timedelta(seconds=1),
    )
    reward = replace(_reward(report), confirmation_id=confirmation.id)

    result = _classify(
        reports=[report],
        histories=[_history(report)],
        confirmations=[confirmation],
        rewards=[reward],
    )

    assert result.invalid is True
    assert "REPORT_CREATED_OWNERSHIP_INVALID" in _reason_codes(result)


def test_report_created_debe_pertenecer_al_autor_del_reporte():
    report = _report()
    reward = replace(_reward(report), user_id=uuid.uuid4())

    result = _classify(reports=[report], histories=[_history(report)], rewards=[reward])

    assert result.invalid is True
    assert "REPORT_CREATED_USER_MISMATCH" in _reason_codes(result)


def test_confirmation_created_debe_pertenecer_al_autor_de_la_confirmacion():
    report = _report()
    confirmation = ConfirmationSnapshot(
        id=uuid.uuid4(),
        report_id=report.id,
        citizen_id=uuid.uuid4(),
        created_at=NOW + timedelta(seconds=1),
    )
    reward = replace(
        _reward(report),
        event_type="CONFIRMATION_CREATED",
        user_id=uuid.uuid4(),
        report_id=None,
        confirmation_id=confirmation.id,
        impact_delta=10,
        credit_delta=10,
    )

    result = _classify(
        reports=[report],
        histories=[_history(report)],
        confirmations=[confirmation],
        rewards=[reward],
    )

    assert result.invalid is True
    assert "CONFIRMATION_CREATED_USER_MISMATCH" in _reason_codes(result)


def test_institucion_del_reward_debe_coincidir_con_report():
    report = _report()
    reward = replace(_reward(report), institution_id=uuid.uuid4())

    result = _classify(reports=[report], histories=[_history(report)], rewards=[reward])

    assert result.invalid is True
    assert "REWARD_INSTITUTION_MISMATCH" in _reason_codes(result)


def test_timestamp_anterior_al_report_es_inconsistente():
    report = _report()
    history = _history(report, created_at=NOW - timedelta(seconds=1))

    result = _classify(reports=[report], histories=[history])

    assert result.invalid is True
    assert "HISTORY_BEFORE_REPORT" in _reason_codes(result)


def test_ciclo_negativo_o_prepopulado_es_inconsistente():
    report = _report()
    reward = replace(_reward(report), resolution_cycle=-1)

    result = _classify(reports=[report], histories=[_history(report)], rewards=[reward])

    assert result.invalid is True
    assert "REWARD_CYCLE_PREPOPULATED" in _reason_codes(result)


def test_bonus_acreditado_sin_validacion_es_inconsistente():
    report = _report()
    reward = replace(
        _reward(report),
        event_type="VALIDATION_BONUS",
        impact_delta=30,
        credit_delta=30,
        status="ACREDITADO",
        institution_id=report.institution_id,
        credited_at=NOW + timedelta(seconds=2),
    )

    result = _classify(reports=[report], histories=[_history(report)], rewards=[reward])

    assert result.invalid is True
    assert "BONUS_WITHOUT_VALIDATION" in _reason_codes(result)
    assert "VALIDATION_SIGNAL_CONTRADICTION" in _reason_codes(result)


def test_reversal_de_transaccion_de_otro_report_es_inconsistente():
    first = _report()
    second = _report()
    original = _reward(first)
    reversal = replace(
        _reward(second),
        event_type="REVERSAL",
        user_id=original.user_id,
        status="REVERTIDO",
        impact_delta=-20,
        credit_delta=-20,
        institution_id=second.institution_id,
        reversed_at=NOW + timedelta(seconds=3),
        related_transaction_id=original.id,
    )

    result = _classify(
        reports=[first, second],
        histories=[_history(first), _history(second)],
        rewards=[original, reversal],
    )

    assert result.invalid is True
    assert "REVERSAL_OWNERSHIP_MISMATCH" in _reason_codes(result)


def test_auditoria_con_institucion_incompatible_es_inconsistente():
    report = _report()
    audit_entry = AuditSnapshot(
        id=uuid.uuid4(),
        action="report.validated",
        report_id=report.id,
        institution_id=uuid.uuid4(),
        cycle=1,
        created_at=NOW + timedelta(seconds=2),
    )

    result = _classify(reports=[report], histories=[_history(report)], audits=[audit_entry])

    assert result.invalid is True
    assert "AUDIT_INSTITUTION_MISMATCH" in _reason_codes(result)


def test_auditoria_de_clasificacion_exige_transicion_correspondiente():
    report = _report()
    audit_entry = AuditSnapshot(
        id=uuid.uuid4(),
        action="ai.report.classified",
        report_id=report.id,
        institution_id=None,
        cycle=None,
        created_at=NOW + timedelta(seconds=2),
    )

    result = _classify(reports=[report], histories=[_history(report)], audits=[audit_entry])

    assert result.invalid is True
    assert "AUDIT_TRANSITION_MISSING" in _reason_codes(result)


def test_estado_actual_debe_coincidir_con_cadena_historica_ordenada():
    report = _report(
        status="CREADO",
        validated_at=NOW + timedelta(seconds=1),
        validated_by_id=uuid.uuid4(),
    )
    histories = [
        _history(report),
        _history(
            report,
            previous_status="CREADO",
            new_status="ASIGNADO",
            created_at=NOW + timedelta(seconds=2),
        ),
    ]
    audit_entry = AuditSnapshot(
        id=uuid.uuid4(),
        action="report.validated",
        report_id=report.id,
        institution_id=report.institution_id,
        cycle=1,
        created_at=NOW + timedelta(seconds=2),
    )

    result = _classify(reports=[report], histories=histories, audits=[audit_entry])

    assert result.invalid is True
    assert "HISTORY_FINAL_STATUS_MISMATCH" in _reason_codes(result)


def test_cycle_one_coherente_es_aceptado():
    report = _report(
        status="ASIGNADO",
        validated_at=NOW + timedelta(seconds=1),
        validated_by_id=uuid.uuid4(),
    )
    histories = [
        _history(report),
        _history(
            report,
            previous_status="CREADO",
            new_status="ASIGNADO",
            created_at=NOW + timedelta(seconds=2),
        ),
    ]
    audit_entry = AuditSnapshot(
        id=uuid.uuid4(),
        action="report.validated",
        report_id=report.id,
        institution_id=report.institution_id,
        cycle=1,
        created_at=NOW + timedelta(seconds=2),
    )

    result = _classify(reports=[report], histories=histories, audits=[audit_entry])

    assert result.invalid is False
    assert result.reports[0].classification == Classification.CYCLE_1


def test_senales_de_validacion_no_pueden_anteceder_validated_at():
    report = _report(
        status="ASIGNADO",
        validated_at=NOW + timedelta(seconds=3),
        validated_by_id=uuid.uuid4(),
    )
    histories = [
        _history(report),
        _history(
            report,
            previous_status="CREADO",
            new_status="ASIGNADO",
            created_at=NOW + timedelta(seconds=2),
        ),
    ]
    audit_entry = AuditSnapshot(
        id=uuid.uuid4(),
        action="report.validated",
        report_id=report.id,
        institution_id=report.institution_id,
        cycle=1,
        created_at=NOW + timedelta(seconds=2),
    )

    result = _classify(reports=[report], histories=histories, audits=[audit_entry])

    assert result.invalid is True
    assert {"VALIDATION_HISTORY_BEFORE_MARKER", "VALIDATION_AUDIT_BEFORE_MARKER"}.issubset(
        _reason_codes(result)
    )


def test_reversal_exige_original_marcada_como_revertida():
    report = _report(
        status="ASIGNADO",
        validated_at=NOW + timedelta(seconds=1),
        validated_by_id=uuid.uuid4(),
    )
    histories = [
        _history(report),
        _history(
            report,
            previous_status="CREADO",
            new_status="ASIGNADO",
            created_at=NOW + timedelta(seconds=2),
        ),
    ]
    original = replace(
        _reward(report),
        status="ACREDITADO",
        institution_id=report.institution_id,
        credited_at=NOW + timedelta(seconds=2),
    )
    reversal = replace(
        _reward(report),
        event_type="REVERSAL",
        status="REVERTIDO",
        impact_delta=-20,
        credit_delta=-20,
        institution_id=report.institution_id,
        created_at=NOW + timedelta(seconds=4),
        reversed_at=NOW + timedelta(seconds=3),
        related_transaction_id=original.id,
    )
    audit_entry = AuditSnapshot(
        id=uuid.uuid4(),
        action="report.validated",
        report_id=report.id,
        institution_id=report.institution_id,
        cycle=1,
        created_at=NOW + timedelta(seconds=2),
    )

    result = _classify(
        reports=[report], histories=histories, rewards=[original, reversal], audits=[audit_entry]
    )

    assert result.invalid is True
    assert "REVERSAL_ORIGINAL_NOT_REVERTED" in _reason_codes(result)


def test_reporte_no_reconocido_es_unknown():
    report = _report(status="CLASIFICADO")

    result = _classify(reports=[report])

    assert result.invalid is True
    assert result.reports[0].classification == Classification.UNKNOWN


def test_snapshot_sintetico_alterado_es_inconsistent():
    report_id, status = next(iter(SEED_STATUS.items()))
    report = _report(
        id=report_id,
        status=status,
        citizen_id=None,
        description="Firma sintética alterada",
    )

    result = _classify(reports=[report])

    assert result.invalid is True
    assert result.reports[0].classification == Classification.INCONSISTENT
    assert "SYNTHETIC_SIGNATURE_MISMATCH" in _reason_codes(result)


def test_parseo_de_auditoria_conserva_uuid_invalido_para_rechazarlo():
    assert parse_uuid_reference("no-es-uuid") == "no-es-uuid"
    assert parse_cycle(True) == -1
    assert parse_cycle(-0.5) == -1
    assert parse_cycle(1.5) == -1
    assert parse_cycle("1") == -1
