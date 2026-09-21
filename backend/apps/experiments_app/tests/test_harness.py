from types import SimpleNamespace

import pytest

from apps.experiments_app.harness import (
    FullStackHarness,
    HarnessError,
    build_plan,
    verify_repository,
)

RUN_ID = "s11-00000000-0000-5000-8000-000000000001"


def test_plan_has_setup_execute_collect_validate_cleanup_for_each_scenario(tmp_path):
    for number in range(2, 9):
        scenario = f"EX-{number:02d}"
        plan = build_plan(
            scenario_id=scenario,
            execution_nonce="test-plan",
            expected_git_sha="a" * 40,
        )
        assert plan.status == "READY"
        assert plan.setup and plan.execute and plan.collect and plan.validate and plan.cleanup
        assert any("create" in command for command in plan.setup)
        assert any("reset" in command for command in plan.setup)
        assert f"--grep=@{scenario}" in plan.execute
        assert "--concurrency=2" not in " ".join(plan.execute)


def test_harness_requires_explicit_custody_gate(tmp_path):
    plan = build_plan(
        scenario_id="EX-02",
        execution_nonce="test-gate",
        expected_git_sha="a" * 40,
    )
    harness = FullStackHarness(tmp_path, runner=lambda *args, **kwargs: None)
    with pytest.raises(HarnessError, match="S11_CUSTODY_APPROVED"):
        harness.execute(
            plan,
            environment={
                "S11_EXPERIMENTS_ENABLED": "true",
                "S11_ENVIRONMENT": "isolated-experiment",
                "S11_DOCUMENT_HMAC_SECRET": "synthetic-test-secret",
            },
        )


def test_harness_always_runs_cleanup_and_surfaces_failure(tmp_path):
    calls = []

    def runner(command, *, cwd, env):
        calls.append(command)
        stdout = f'{{"run_id": "{RUN_ID}"}}' if "s11_prepare_run" in command else ""
        return SimpleNamespace(
            returncode=1 if "playwright" in command else 0,
            stdout=stdout,
            stderr="synthetic failure" if "playwright" in command else "",
        )

    plan = build_plan(
        scenario_id="EX-03",
        execution_nonce="test-failure",
        expected_git_sha="a" * 40,
    )
    with pytest.raises(HarnessError, match="sin ocultarlo"):
        FullStackHarness(tmp_path, runner=runner).execute(
            plan,
            environment={
                "S11_EXPERIMENTS_ENABLED": "true",
                "S11_ENVIRONMENT": "isolated-experiment",
                "S11_CUSTODY_APPROVED": "true",
                "S11_DOCUMENT_HMAC_SECRET": "synthetic-test-secret",
            },
        )
    assert any("s11_environment" in command and "reset" in command for command in calls)
    assert any("s11_finalize_run" in command and "FAILED" in command for command in calls)
    assert any(
        "s11_record_harness_log" in command and RUN_ID in " ".join(command) for command in calls
    )


def test_success_produces_run_id_timings_and_associated_artifacts(tmp_path):
    calls = []

    def runner(command, *, cwd, env):
        calls.append((command, env.copy()))
        stdout = f'{{"run_id": "{RUN_ID}"}}' if "s11_prepare_run" in command else ""
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    plan = build_plan(
        scenario_id="EX-08",
        execution_nonce="test-success",
        expected_git_sha="a" * 40,
    )
    result = FullStackHarness(tmp_path, runner=runner).execute(
        plan,
        environment={
            "S11_EXPERIMENTS_ENABLED": "true",
            "S11_ENVIRONMENT": "isolated-experiment",
            "S11_CUSTODY_APPROVED": "true",
            "S11_DOCUMENT_HMAC_SECRET": "synthetic-test-secret",
        },
    )
    assert result.run_id == RUN_ID
    assert result.status == "COMPLETED"
    assert (tmp_path / "var" / "s11-runs" / "control" / "scopes").is_dir()
    assert all(phase.duration_ms >= 0 for phase in result.phases)
    assert any(
        "s11_export_evidence" in command and RUN_ID in " ".join(command) for command, _ in calls
    )
    assert any("s11_finalize_run" in command and "COMPLETED" in command for command, _ in calls)
    playwright_environment = next(env for command, env in calls if "playwright" in command)
    assert playwright_environment["S11_RUN_ID"] == RUN_ID


def test_repository_gate_requires_expected_clean_commit(tmp_path, monkeypatch):
    answers = iter(["a" * 40, ""])
    monkeypatch.setattr("apps.experiments_app.harness._git", lambda repo_root, *args: next(answers))
    verify_repository(tmp_path, "a" * 40)

    dirty_answers = iter(["a" * 40, " M tracked.py"])
    monkeypatch.setattr(
        "apps.experiments_app.harness._git", lambda repo_root, *args: next(dirty_answers)
    )
    with pytest.raises(HarnessError, match="working tree"):
        verify_repository(tmp_path, "a" * 40)
