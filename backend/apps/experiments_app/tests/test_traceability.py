import pytest

from apps.experiments_app.traceability import EXPECTED, reconcile


def export_for(scenario_id):
    expected = EXPECTED[scenario_id]
    return {
        "scenario_id": scenario_id,
        "source": {
            "audit_logs": [{"action": value} for value in sorted(expected["audit"])],
            "reward_transactions": [{"event_type": value} for value in sorted(expected["reward"])],
            "reports": [{"status": sorted(expected["states"])[0]}],
        },
    }


@pytest.mark.parametrize("scenario_id", sorted(EXPECTED))
def test_complete_expected_trace_is_pass(scenario_id):
    result = reconcile(export_for(scenario_id), complete_input=True)
    assert result.status == "PASS"
    assert result.missing == ()


def test_complete_but_missing_event_is_fail():
    payload = export_for("EX-03")
    payload["source"]["audit_logs"] = []
    result = reconcile(payload, complete_input=True)
    assert result.status == "FAIL"
    assert "audit:report.confirmed" in result.missing


@pytest.mark.parametrize("complete", [False, True])
def test_incomplete_or_invalid_export_is_not_demonstrated(complete):
    payload = {} if complete else export_for("EX-04")
    result = reconcile(payload, complete_input=complete)
    assert result.status == "NOT_DEMONSTRATED"
