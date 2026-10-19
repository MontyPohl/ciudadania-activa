"""B5 standalone synthetic host observations; no Docker or application action."""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "w07_b2_fixture_" + __name__.replace(".", "_"),
    Path(__file__).with_name("test_ex10_session_v2.py"),
)
bf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bf)
module_path = Path(os.environ.get("S11_B5_MODULE_PATH", str(APP / "ex10_deployment_envelope.py")))
spec = importlib.util.spec_from_file_location(
    bf.PACKAGE_NAME + ".ex10_deployment_envelope", module_path
)
b5 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = b5
spec.loader.exec_module(b5)


def encoded(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()


def hashed(raw):
    return hashlib.sha256(raw).hexdigest()


def reference(path, value, schema):
    raw = encoded(value)
    path.write_bytes(raw)
    path.chmod(0o600)
    return {"path": str(path), "sha256": hashed(raw), "schema_version": schema}


@pytest.fixture
def rig():
    generator = bf.fixture.__wrapped__()
    p, header, _, _, home = next(generator)
    try:
        clock = [datetime(2026, 10, 5, 19, tzinfo=UTC)]
        repo = Path(p["source_host_root"])
        (repo / "backend").mkdir(mode=0o700)
        src = repo / "backend/module.py"
        src.write_bytes(b'EXPERIMENT_SOURCE = "fixed synthetic"\n')
        src.chmod(0o644)
        host = repo / "backend/host.py"
        host.write_bytes(b'HOST_SOURCE = "fixed synthetic"\n')
        host.chmod(0o644)
        profile_ref = reference(home / "profile.json", p, b5.rp.SCHEMA_VERSION)
        header_ref = reference(home / "header.json", header, header["schema_version"])
        check_names = frozenset(
            {
                "repository_source_complete",
                "protocol_identity",
                "no_side_effects",
                "ready_state:fixture_actors",
            }
        )
        pf = {
            "schema_version": b5.evidence.ARTIFACT_SCHEMAS["preflight_reference"],
            "protocol_version": b5.ex10_load.PROTOCOL_ID,
            "methodology_version": b5.ex10_load.METHODOLOGY_VERSION,
            "scenario_id": "EX-10",
            "result": "PASS",
            "gate": "READY_FOR_EXECUTOR_INTEGRATION",
            "ready_for_execution": False,
            "run_id": None,
            "evidence_written": False,
            "report_posts": 0,
            "provider_calls": 0,
            "task_publishes": 0,
            "health_requests": 1,
            "control_probes": 2,
            "expected_git_sha": p["source_revision"],
            "git_sha": p["source_revision"],
            "checked_at": b5._utc(clock[0]),
            "checks": [
                {
                    "check": n,
                    "result": "PASS",
                    "expected": True,
                    "observed_sanitized": True,
                    "detail_code": None,
                }
                for n in sorted(check_names)
            ],
        }
        pf_ref = reference(home / "preflight.json", pf, pf["schema_version"])
        head_ref = reference(
            home / "head.json", {"synthetic_signed_view": 1}, "s11-ex10-synthetic-head-v1"
        )
        ready_ref = reference(
            home / "ready.json", {"fixture_only_zero_reports": True}, "s11-ex10-synthetic-ready-v1"
        )
        ready = {
            "report_reference": ready_ref,
            "checks": [{"check": "zero_reports", "result": "PASS"}],
        }
        containers = []
        mounts = []
        raw_compose = {
            "name": p["project"],
            "services": {},
            "secrets": {"ignored": "password=NEVER_PUBLISH"},
        }
        projection = {"project": p["project"], "services": {}}
        for service in p["expected_services"]:
            cid = hashed(service.encode())
            item = {
                "container_id": cid,
                "service": service,
                "role": service,
                "image_digest": "sha256:" + hashed(("image:" + service).encode()),
                "running": True,
                "mounts": [],
                "Config": {
                    "Env": ["DATABASE_URL=postgres://private/password", "TOKEN=NEVER_PUBLISH"]
                },
            }
            if service in ("backend", "worker"):
                pairs = [
                    (p["source_host_root"], p["source_container_target"], True),
                    *[
                        (root["path"], p["fixed_targets"][role], False)
                        for role, root in p["host_roots"].items()
                    ],
                    *[
                        (
                            mask["source_path"],
                            p["source_container_target"] + "/" + mask["relative_path"],
                            True,
                        )
                        for mask in p["masks"]
                    ],
                ]
                for source, dest, ro in pairs:
                    mount = {
                        "container_id": cid,
                        "type": "bind",
                        "source_canonical": source,
                        "destination": dest,
                        "read_only": ro,
                        "ownership_reference": p["deployment_verification_reference"],
                    }
                    mounts.append(mount)
                    item["mounts"].append(copy.deepcopy(mount))
            containers.append(item)
            volumes = sorted(
                [
                    {
                        "type": m["type"],
                        "source": m["source_canonical"],
                        "target": m["destination"],
                        "read_only": m["read_only"],
                    }
                    for m in item["mounts"]
                ],
                key=lambda m: m["target"],
            )
            raw_compose["services"][service] = {
                "image": service + ":synthetic",
                "volumes": volumes,
                "environment": {"DB_PASSWORD": "NEVER_PUBLISH"},
                "env_file": ["PRIVATE.env"],
                "command": "echo password=NEVER_PUBLISH",
            }
            projection["services"][service] = {
                "image": service + ":synthetic",
                "volumes": copy.deepcopy(volumes),
            }
        identities = [{k: v[k] for k in b5.CONTAINER_FIELDS} for v in containers]
        context = {
            "repo_root": p["source_host_root"],
            "historical_roots": [],
            "prior_session_roots": [],
            "running_sources": ["backend/module.py", "backend/host.py"],
            "approved_reference_schemas": {
                n: [p[n]["schema_version"]] for n in b5.rp.REFERENCE_FIELDS
            },
        }
        calls = []

        def approved(**kw):
            calls.append(sorted(kw))
            if "profile" in kw:
                assert kw["profile"].to_dict() == p
            if "header" in kw:
                assert kw["header"] == header
            # Fixed test-only external approval of this synthetic context;
            # no production trust root, signature default or actual Docker.
            return None

        def running(container_id, relative_path):
            assert container_id in [
                i["container_id"] for i in containers if i["service"] in ("backend", "worker")
            ]
            assert relative_path == "backend/module.py"
            return (repo / relative_path).read_bytes()

        options = {
            "profile_reference": profile_ref,
            "header_reference": header_ref,
            "application_preflight_reference": pf_ref,
            "historical_authority_view_reference": head_ref,
            "path_context": context,
            "running_sources": {"backend/module.py": hashed(src.read_bytes())},
            "host_operational_sources": {"backend/host.py": hashed(host.read_bytes())},
            "container_identities": identities,
            "mounts": mounts,
            "compose_projection": projection,
            "ready_state": ready,
            "application_required_checks": check_names,
            "compose_observer": lambda: copy.deepcopy(raw_compose),
            "container_observer": lambda: copy.deepcopy(containers),
            "running_source_reader": running,
            "bootstrap_verifier": approved,
            "source_verifier": approved,
            "session_verifier": approved,
            "authority_verifier": approved,
            "ready_verifier": approved,
            "clock": lambda: clock[0],
        }
        qualifier = b5.EnvelopeQualifier(**options)
        yield types.SimpleNamespace(
            p=p,
            header=header,
            home=home,
            repo=repo,
            src=src,
            host=host,
            clock=clock,
            pf=pf,
            pf_ref=pf_ref,
            raw_compose=raw_compose,
            projection=projection,
            containers=containers,
            mounts=mounts,
            options=options,
            q=qualifier,
            calls=calls,
        )
    finally:
        try:
            next(generator)
        except StopIteration:
            pass


def test_actual_b5_observations_bind_all26_fields_and_exact_application_bytes(rig):
    x = rig
    env = x.q.build()
    v = env.to_dict()
    assert set(v) == b5.FIELDS and len(v) == 26 and v["result"] == "PASS"
    assert v["application_preflight_sha256"] == hashed(Path(x.pf_ref["path"]).read_bytes())
    assert v["resolved_compose_projection_sha256"] == hashed(encoded(x.projection))
    assert v["protocol_identity"] == b5.ex10_load.protocol_identity()
    assert (
        x.q.qualify(env._bytes)._bytes == env._bytes
        and x.header["runtime_profile_sha256"] == v["runtime_profile_sha256"]
    )
    assert "NEVER_PUBLISH" not in env._bytes.decode() and "postgres://" not in env._bytes.decode()


@pytest.mark.parametrize("field", sorted(b5.FIELDS))
def test_envelope_all26_required_no_missing_field(rig, field):
    v = rig.q.build().to_dict()
    del v[field]
    with pytest.raises(b5.EnvelopeError):
        b5.parse_envelope_bytes(encoded(v))


@pytest.mark.parametrize("mutation", ["extra", "duplicate", "pretty", "nan"])
def test_envelope_closed_canonical_bounded_parser(rig, mutation):
    env = rig.q.build()
    v = env.to_dict()
    if mutation == "extra":
        v["environment"] = {"password": "private"}
        raw = encoded(v)
    elif mutation == "duplicate":
        raw = env._bytes.replace(b"{", b'{"result":"PASS",', 1)
    elif mutation == "pretty":
        raw = json.dumps(v, indent=2).encode()
    else:
        raw = env._bytes.replace(b'"PASS"', b"NaN", 1)
    with pytest.raises(b5.EnvelopeError):
        b5.parse_envelope_bytes(raw)


@pytest.mark.parametrize("age", [0, 900, 900.000001, -0.000001])
def test_b5_age_boundary_no_future_or_automatic_refresh(rig, age):
    x = rig
    env = x.q.build()
    v = env.to_dict()
    original = x.clock[0]
    x.clock[0] = original + timedelta(seconds=age)
    if age in (0, 900):
        assert x.q.qualify(env._bytes).sha256 == env.sha256
    else:
        with pytest.raises(b5.EnvelopeError):
            x.q.qualify(env._bytes)
    assert env.to_dict() == v


@pytest.mark.parametrize(
    "change",
    [
        "identity",
        "image",
        "running",
        "mount-source",
        "read-only",
        "mount-extra",
        "compose-project",
        "compose-image",
        "running-bytes",
        "host-bytes",
        "override",
        "profile",
        "header",
        "preflight",
    ],
)
def test_b5_reobservation_rejects_replacement_and_binding_substitution(rig, change):
    x = rig
    env = x.q.build()
    item = next(i for i in x.containers if i["service"] == "backend")
    if change == "identity":
        item["container_id"] = "f" * 64
    elif change == "image":
        item["image_digest"] = "sha256:" + "f" * 64
    elif change == "running":
        item["running"] = False
    elif change == "mount-source":
        item["mounts"][0]["source_canonical"] = str(x.home / "foreign-source")
    elif change == "read-only":
        item["mounts"][0]["read_only"] = False
    elif change == "mount-extra":
        item["mounts"].append({**item["mounts"][0], "destination": "/var/run/docker.sock"})
    elif change == "compose-project":
        x.raw_compose["name"] = "foreign-project"
    elif change == "compose-image":
        x.raw_compose["services"]["backend"]["image"] = "foreign:synthetic"
    elif change == "running-bytes":
        x.q.running_reader = lambda **kw: b"different code"
    elif change == "host-bytes":
        x.host.write_bytes(b"changed code")
    elif change == "override":
        Path(x.p["override_path"]).write_bytes(b"changed override")
    elif change == "profile":
        Path(x.options["profile_reference"]["path"]).write_bytes(b"{}\n")
    elif change == "header":
        Path(x.options["header_reference"]["path"]).write_bytes(b"{}\n")
    else:
        Path(x.pf_ref["path"]).write_bytes(b"{}\n")
    with pytest.raises(b5.EnvelopeError):
        x.q.qualify(env._bytes)
    assert env.to_dict()["result"] == "PASS"


def test_compose_and_docker_projection_never_returns_environment_or_dsn(rig):
    x = rig
    projection = b5.sanitized_compose_projection(x.raw_compose)
    assert projection == x.projection
    raw = encoded(
        b5.collect_observations(
            compose_observer=x.q.compose_observer, container_observer=x.q.container_observer
        )
    )
    assert (
        b"NEVER_PUBLISH" not in raw
        and b"postgres://" not in raw
        and b"Config" not in raw
        and b"environment" not in raw
    )


def test_absent_observation_is_incomplete_never_pass_and_no_automatic_preflight(rig):
    x = rig
    x.q.compose_observer = lambda: None
    env = x.q.build()
    assert env.to_dict()["result"] == "INCOMPLETE"
    with pytest.raises(b5.EnvelopeError, match="ENVELOPE_NOT_PASS"):
        x.q.qualify(env._bytes)


@pytest.mark.parametrize("alias", ["/source/../source", "//source", "/source/./leaf"])
def test_compose_mount_alias_is_rejected_without_normalization(rig, alias):
    raw = copy.deepcopy(rig.raw_compose)
    raw["services"]["backend"]["volumes"][0]["source"] = alias
    with pytest.raises(b5.EnvelopeError):
        b5.sanitized_compose_projection(raw)


@pytest.mark.parametrize("provider", ["bootstrap", "source", "session", "authority", "ready"])
def test_fixed_approval_provider_cannot_be_serialized_true(rig, provider):
    x = rig
    env = x.q.build()
    setattr(x.q, provider, lambda **kw: True)
    with pytest.raises(b5.EnvelopeError):
        x.q.qualify(env._bytes)


def test_container_changed_during_source_observation_cannot_pass(rig):
    x = rig
    original = x.q.running_reader

    def replaced(**kw):
        raw = original(**kw)
        item = next(i for i in x.containers if i["service"] == "backend")
        item["image_digest"] = "sha256:" + "f" * 64
        return raw

    x.q.running_reader = replaced
    env = x.q.build()
    assert env.to_dict()["result"] == "FAIL"


def test_bootstrap_reference_is_preexisting_and_profile_header_never_rewritten(rig):
    x = rig
    paths = [Path(x.options[k]["path"]) for k in ("profile_reference", "header_reference")]
    before = [p.read_bytes() for p in paths]
    env = x.q.build()
    assert (
        env.to_dict()["deployment_verification_reference"]
        == x.p["deployment_verification_reference"]
    )
    assert before == [p.read_bytes() for p in paths]


@pytest.mark.parametrize("action", ["build", "qualify"])
@pytest.mark.parametrize("resource", ["container", "running-source"])
def test_late_ready_approval_cannot_replace_deployment_after_observation(rig, action, resource):
    x = rig
    existing = x.q.build()
    calls = []
    original = x.q.ready

    def changes_after_observations(**kw):
        original(**kw)
        calls.append(None)
        if len(calls) == 2:
            if resource == "container":
                item = next(i for i in x.containers if i["service"] == "backend")
                item["container_id"] = "f" * 64
                for mount in item["mounts"]:
                    mount["container_id"] = "f" * 64
            else:
                x.src.write_bytes(b"replacement during final READY approval\n")

    x.q.ready = changes_after_observations
    if action == "build":
        assert x.q.build().to_dict()["result"] == "FAIL"
    else:
        with pytest.raises(b5.EnvelopeError):
            x.q.qualify(existing._bytes)


@pytest.mark.parametrize(
    "image",
    [
        "postgres://fixture-user:fixture-pass@db",
        "https://fixture-user:fixture-pass@registry/image",
        "docker://registry/image",
        "fixture-user:fixture-pass@registry/image",
    ],
)
def test_compose_image_allowlist_rejects_dsn_uri_and_userinfo_before_return(rig, image):
    raw = copy.deepcopy(rig.raw_compose)
    raw["services"]["backend"]["image"] = image
    with pytest.raises(b5.EnvelopeError, match="COMPOSE_INVALID"):
        b5.sanitized_compose_projection(raw)


def test_resolved_compose_build_only_service_uses_closed_safe_projection(rig):
    raw = copy.deepcopy(rig.raw_compose)
    backend = raw["services"]["backend"]
    del backend["image"]
    backend["build"] = {
        "context": str(rig.repo / "backend"),
        "dockerfile": "Dockerfile.dev",
        "args": {"PRIVATE_TOKEN": "NEVER_PUBLISH"},
        "secrets": ["NEVER_PUBLISH"],
    }
    expected = copy.deepcopy(rig.projection)
    del expected["services"]["backend"]["image"]
    expected["services"]["backend"]["build"] = {
        "context": str(rig.repo / "backend"),
        "dockerfile": "Dockerfile.dev",
    }
    assert b5.sanitized_compose_projection(raw) == expected
    assert b"NEVER_PUBLISH" not in encoded(expected)


@pytest.mark.parametrize("which", ["profile_reference", "header_reference"])
def test_missing_private_context_never_leaks_os_path_or_exception(rig, which):
    Path(rig.options[which]["path"]).unlink()
    with pytest.raises(b5.EnvelopeError) as error:
        rig.q.build()
    assert error.value.code in ("FILE_UNAVAILABLE", "REFERENCE_CHANGED")
    assert str(rig.home) not in str(error.value)


def test_original_application_serializer_zero_fraction_timestamp_is_consumed_unchanged(rig):
    # Compile only the exact frozen producer method; no Django import or PF execution.
    tree = ast.parse((APP / "ex10_preflight.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PreflightReport")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "to_json")
    method.decorator_list = []
    namespace = {
        "Any": object,
        "SCHEMA_VERSION": rig.pf["schema_version"],
        "ex10_load": b5.ex10_load,
    }
    exec(
        compile(ast.Module(body=[method], type_ignores=[]), "frozen-pf-serializer", "exec"),
        namespace,
    )
    report = types.SimpleNamespace(
        result="PASS",
        gate="READY_FOR_EXECUTOR_INTEGRATION",
        health_requests=1,
        control_probes=2,
        expected_git_sha=rig.p["source_revision"],
        git_sha=rig.p["source_revision"],
        checked_at=rig.clock[0],
        checks=tuple(
            types.SimpleNamespace(to_json=lambda c=c: copy.deepcopy(c)) for c in rig.pf["checks"]
        ),
    )
    actual = namespace["to_json"](report)
    assert actual["checked_at"] == "2026-10-05T19:00:00Z"
    options = dict(rig.options)
    options["application_preflight_reference"] = reference(
        Path(rig.pf_ref["path"]), actual, actual["schema_version"]
    )
    q = b5.EnvelopeQualifier(**options)
    envelope = q.build()
    assert envelope.to_dict()["result"] == "PASS"
    assert envelope.to_dict()["application_preflight_sha256"] == hashed(encoded(actual))
    assert q.qualify(envelope._bytes).sha256 == envelope.sha256


def test_launcher_prunes_historical_var_before_descent_or_file_stat(rig, monkeypatch):
    path = APP.parents[1] / "scripts" / "s11_ex10_preflight_launcher.py"
    name = "w07_owned_launcher_prune_fixture"
    spec = importlib.util.spec_from_file_location(name, path)
    launcher = importlib.util.module_from_spec(spec)
    sys.modules[name] = launcher
    spec.loader.exec_module(launcher)
    secret = rig.repo / "backend/.env.fixture"
    secret.write_bytes(b"synthetic mask name only")
    visited = []

    def walk(root):
        assert root == rig.repo
        names = ["var", "backend", ".git", "node_modules"]
        yield str(root), names, []
        assert names == ["backend"]  # checked before any descent or var stat
        visited.append("backend")
        yield str(root / "backend"), [], [secret.name]

    monkeypatch.setattr(launcher.os, "walk", walk)
    try:
        assert launcher.secret_masks(rig.repo, set()) == ("backend/.env.fixture",)
        assert visited == ["backend"]
    finally:
        sys.modules.pop(name, None)


def with_preflight(rig, value):
    options = dict(rig.options)
    options["application_preflight_reference"] = reference(
        Path(rig.pf_ref["path"]), value, value["schema_version"]
    )
    return b5.EnvelopeQualifier(**options)


@pytest.mark.parametrize(
    "mutation",
    [
        "expired",
        "future",
        "false-ready",
        "report-post",
        "task-publish",
        "missing-check",
        "duplicate-check",
        "failed-check",
        "protocol",
        "coverage-name-list",
    ],
)
def test_actual_preflight_bytes_reject_invalid_flags_checks_and_freshness(rig, mutation):
    value = copy.deepcopy(rig.pf)
    if mutation == "expired":
        value["checked_at"] = b5._utc(rig.clock[0] - timedelta(seconds=900.000001))
    elif mutation == "future":
        value["checked_at"] = b5._utc(rig.clock[0] + timedelta(microseconds=1))
    elif mutation == "false-ready":
        value["ready_for_execution"] = True
    elif mutation == "report-post":
        value["report_posts"] = 1
    elif mutation == "task-publish":
        value["task_publishes"] = 1
    elif mutation == "missing-check":
        value["checks"].pop()
    elif mutation == "duplicate-check":
        value["checks"].append(copy.deepcopy(value["checks"][0]))
    elif mutation == "failed-check":
        value["checks"][0]["result"] = "FAIL"
    elif mutation == "protocol":
        value["protocol_version"] = "foreign"
    else:
        value["checks"][0]["check"] = []
    q = with_preflight(rig, value)
    # Actual rehashed artifact, not a flag or a stale digest mismatch.
    envelope = q.build()
    assert envelope.to_dict()["result"] != "PASS"
    with pytest.raises(b5.EnvelopeError):
        q.qualify(envelope._bytes)


@pytest.mark.parametrize(
    "change",
    [
        "image",
        "readonly",
        "extra-container",
        "duplicate-container",
        "duplicate-mount",
        "host-source",
        "masked-source",
    ],
)
def test_build_requires_actual_container_mount_and_all_source_observations(rig, change):
    backend = next(v for v in rig.containers if v["service"] == "backend")
    if change == "image":
        backend["image_digest"] = "sha256:" + "f" * 64
    elif change == "readonly":
        backend["mounts"][0]["read_only"] = False
    elif change == "extra-container":
        rig.containers.append(
            {
                **copy.deepcopy(backend),
                "service": "foreign",
                "role": "foreign",
                "container_id": "f" * 64,
                "mounts": [],
            }
        )
    elif change == "duplicate-container":
        rig.containers.append(copy.deepcopy(backend))
    elif change == "duplicate-mount":
        backend["mounts"].append(copy.deepcopy(backend["mounts"][0]))
    elif change == "host-source":
        rig.host.write_bytes(b"foreign host code")
    else:
        # Source beneath a declared mask must not be silently treated as readable.
        context = copy.deepcopy(rig.options["path_context"])
        context["running_sources"].append(rig.p["masks"][0]["relative_path"])
        options = {**rig.options, "path_context": context}
        q = b5.EnvelopeQualifier(**options)
        assert q.build().to_dict()["result"] != "PASS"
        return
    assert rig.q.build().to_dict()["result"] != "PASS"


@pytest.mark.parametrize("replacement", ["same-bytes-inode", "symlink"])
def test_late_ready_source_identity_replacement_cannot_pass_even_with_same_hash(rig, replacement):
    old = rig.q.ready
    calls = []

    def replace(**kw):
        old(**kw)
        calls.append(None)
        if len(calls) == 2:
            raw = rig.src.read_bytes()
            twin = rig.src.with_name("replacement.py")
            twin.write_bytes(raw)
            twin.chmod(0o644)
            if replacement == "symlink":
                rig.src.unlink()
                rig.src.symlink_to(twin)
            else:
                os.replace(twin, rig.src)

    rig.q.ready = replace
    assert rig.q.build().to_dict()["result"] != "PASS"


@pytest.mark.parametrize(
    "field", ["container_observer", "compose_observer", "running_reader", "source", "authority"]
)
def test_provider_exception_is_redacted_in_incomplete_observation(rig, field):
    def unavailable(**kw):
        raise RuntimeError("postgres://fixture-user:fixture-pass@secret NEVEr_PUBLISH")

    setattr(rig.q, field, unavailable)
    envelope = rig.q.build()
    assert envelope.to_dict()["result"] == "INCOMPLETE"
    assert b"fixture-pass" not in envelope._bytes and b"postgres://" not in envelope._bytes


@pytest.mark.parametrize(
    "dockerfile",
    ["../Dockerfile", "/private/Dockerfile", "./Dockerfile", "http://foreign/Dockerfile"],
)
def test_compose_build_dockerfile_alias_or_remote_identity_rejected(rig, dockerfile):
    raw = copy.deepcopy(rig.raw_compose)
    raw["services"]["backend"]["build"] = {
        "context": str(rig.repo / "backend"),
        "dockerfile": dockerfile,
    }
    with pytest.raises(b5.EnvelopeError):
        b5.sanitized_compose_projection(raw)


@pytest.mark.parametrize(
    "image",
    ["registry.example:5000/team/backend:tag", "registry.example/team/backend@sha256:" + "a" * 64],
)
def test_oci_image_registry_and_digest_identity_remain_supported(rig, image):
    raw = copy.deepcopy(rig.raw_compose)
    raw["services"]["backend"]["image"] = image
    assert b5.sanitized_compose_projection(raw)["services"]["backend"]["image"] == image


def test_preflight_valid_pass_flag_never_substitutes_actual_fixed_byte_hash(rig):
    actual = copy.deepcopy(rig.pf)
    actual["checked_at"] = b5._utc(rig.clock[0] - timedelta(microseconds=1))
    Path(rig.pf_ref["path"]).write_bytes(encoded(actual))
    # Same valid PASS report semantics, fresh, but a different original artifact.
    assert actual["result"] == "PASS"
    assert hashed(encoded(actual)) != rig.pf_ref["sha256"]
    assert rig.q.build().to_dict()["result"] != "PASS"


@pytest.mark.parametrize("age", [901, 3600, 86400])
def test_attached_lifecycle_stale_keeps_original_bytes_and_default_freshness(rig, age):
    envelope = rig.q.build()
    raw, original_pf = envelope._bytes, Path(rig.pf_ref["path"]).read_bytes()
    rig.clock[0] += timedelta(seconds=age)
    observed = rig.q.qualify_attached(raw, fresh_required=False)
    assert observed._bytes == raw and observed.sha256 == envelope.sha256
    assert Path(rig.pf_ref["path"]).read_bytes() == original_pf
    with pytest.raises(b5.EnvelopeError):
        rig.q.qualify(raw)
    with pytest.raises(b5.EnvelopeError):
        rig.q.qualify_attached(raw, fresh_required=True)


@pytest.mark.parametrize("fresh", [False, True])
def test_attached_future_original_observation_is_rejected_even_without_freshness(rig, fresh):
    envelope = rig.q.build()
    rig.clock[0] -= timedelta(microseconds=1)
    with pytest.raises(b5.EnvelopeError):
        rig.q.qualify_attached(envelope._bytes, fresh_required=fresh)


@pytest.mark.parametrize("change", ["before-pf", "beyond-original-pf-window"])
def test_attached_original_pf_expiry_coherence_survives_lifecycle_mode(rig, change):
    envelope = rig.q.build()
    value = envelope.to_dict()
    if change == "before-pf":
        value["checked_at"] = b5._utc(rig.clock[0] - timedelta(seconds=1))
        value["expires_at"] = b5._utc(rig.clock[0] + timedelta(seconds=899))
    else:
        value["checked_at"] = b5._utc(rig.clock[0] + timedelta(seconds=10))
        value["expires_at"] = b5._utc(rig.clock[0] + timedelta(seconds=905))
    raw = encoded(value)
    # Valid closed schema and <=900 envelope span; the missing property would
    # accept these only after ignoring the original application PF bound.
    assert b5.parse_envelope_bytes(raw)._bytes == raw
    rig.clock[0] += timedelta(seconds=3600)
    with pytest.raises(b5.EnvelopeError):
        rig.q.qualify_attached(raw, fresh_required=False)


@pytest.mark.parametrize("change", ["source-bytes", "profile-inode", "container", "mount"])
def test_attached_stale_observation_still_requires_original_current_context(rig, change):
    envelope = rig.q.build()
    rig.clock[0] += timedelta(seconds=3600)
    if change == "source-bytes":
        rig.src.write_bytes(b"SUBSTITUTED_SOURCE = True\n")
    elif change == "profile-inode":
        path = Path(rig.options["profile_reference"]["path"])
        original = rig.q.ready
        calls = []

        def replace_during_final_ready(**facts):
            original(**facts)
            calls.append(len(calls) + 1)
            if len(calls) == 2:
                replacement = path.with_name("replaced-profile.json")
                replacement.write_bytes(path.read_bytes())
                replacement.chmod(0o600)
                assert replacement.stat().st_ino != path.stat().st_ino
                replacement.replace(path)

        rig.q.ready = replace_during_final_ready
    elif change == "container":
        next(item for item in rig.containers if item["service"] == "backend")["container_id"] = (
            "e" * 64
        )
    else:
        next(item for item in rig.containers if item["service"] == "backend")["mounts"][0][
            "read_only"
        ] = False
    with pytest.raises(b5.EnvelopeError) as raised:
        rig.q.qualify_attached(envelope._bytes, fresh_required=False)
    if change == "profile-inode":
        assert calls == [1, 2]
        assert raised.value.code == "FILE_CHANGED"


@pytest.mark.parametrize("flag", [None, 0, 1, "false", [], {}])
def test_attached_freshness_selector_requires_exact_boolean(rig, flag):
    envelope = rig.q.build()
    with pytest.raises(b5.EnvelopeError):
        rig.q.qualify_attached(envelope._bytes, fresh_required=flag)
