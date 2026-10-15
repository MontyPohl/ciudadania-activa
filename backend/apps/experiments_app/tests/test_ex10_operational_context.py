"""Private pipe transport before native host deployment qualification (no Docker)."""

from __future__ import annotations

import copy
import importlib.util
import os
import stat
import threading
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "w06_coord_fixture_" + __name__.replace(".", "_"),
    Path(__file__).with_name("test_ex10_start_coordination.py"),
)
cf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cf)
h = cf.h
op = h.load("ex10_operational_context")
wire = op.wire
spec = importlib.util.spec_from_file_location(
    "w06_builder_" + __name__.replace(".", "_"),
    Path(__file__).with_name("test_ex10_bridge_protocol.py"),
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def frame(exchange, position):
    key = "handshake_frame" if exchange == "START" else "single_rpc_frame"
    schema = wire.schema_definitions()[key]["oneOf"][position - 1]
    value = builder.fill(schema)
    value["exchange_id"] = builder.UUID
    if "exchange_id" in value["body"]:
        value["body"]["exchange_id"] = builder.UUID
    raw = builder.wire(value)
    parser = (
        wire.parse_handshake_frame_bytes
        if exchange == "START"
        else wire.parse_single_rpc_frame_bytes
    )
    return parser(raw)


@pytest.fixture
def pair():
    owned = []

    def channels(exchange="START", budget=2):
        left_read, left_write = os.pipe()
        right_read, right_write = os.pipe()
        host = op.PipeFrameChannel(
            read_fd=left_read,
            write_fd=right_write,
            exchange=exchange,
            exchange_id=builder.UUID,
            budget=budget,
        )
        executor = op.PipeFrameChannel(
            read_fd=right_read,
            write_fd=left_write,
            exchange=exchange,
            exchange_id=builder.UUID,
            role="EXECUTOR",
            budget=budget,
        )
        for fd in (left_read, left_write, right_read, right_write):
            os.close(fd)
        owned.extend((host, executor))
        return host, executor

    yield channels
    for channel in owned:
        channel.close()


@pytest.mark.parametrize("exchange", ["START", "SINGLE_RPC"])
def test_actual_private_pipes_exact_complete_exchange_and_eof(pair, exchange):
    host, executor = pair(exchange)
    errors = []

    def peer():
        try:
            for position in range(1, len(executor.steps) + 1):
                message = frame(exchange, position)
                if position % 2:
                    assert executor.receive().canonical == message.canonical
                else:
                    executor.send(message.canonical)
            executor.finish()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=peer)
    thread.start()
    try:
        for position in range(1, len(host.steps) + 1):
            message = frame(exchange, position)
            if position % 2:
                host.send(message.canonical)
            else:
                assert host.receive().canonical == message.canonical
        host.finish()
    finally:
        thread.join(3)
    assert not thread.is_alive() and errors == [] and host.closed and executor.closed
    assert host.finished and executor.finished
    assert len(host.retired_identities) == len(executor.retired_identities) == 2


@pytest.mark.parametrize("length", [0, wire.MAX_REQUEST_BYTES + 1, 2**32 - 1])
def test_length_prefix_rejected_before_body_allocation_or_wait(pair, length):
    host, executor = pair()
    os.write(executor.write_fd, length.to_bytes(4, "big"))
    with pytest.raises(op.OperationalError, match="FRAME_LIMIT"):
        host.receive()
    assert host.position == 0


@pytest.mark.parametrize("fragment", [b"", b"\x00\x00", b"\x00\x00\x00\x08{}"])
@pytest.mark.parametrize("dispatched", [False, True])
def test_actual_eof_or_partial_frame_never_becomes_success_or_retry(pair, fragment, dispatched):
    host, executor = pair()
    if dispatched:
        host.mark_dispatched()
    if fragment:
        os.write(executor.write_fd, fragment)
    executor.close()
    with pytest.raises(
        op.OperationalError, match="UNCERTAIN_PUBLICATION" if dispatched else "FRAME_INCOMPLETE"
    ):
        host.receive()
    assert host.position == 0 and host.dispatched is dispatched


@pytest.mark.parametrize("change", ["order", "role", "exchange", "second-operation"])
def test_valid_closed_frame_with_wrong_order_role_or_exchange_denied(pair, change):
    host, executor = pair()
    message = frame("START", 2).to_dict()
    if change == "order":
        message = frame("START", 4).to_dict()
    elif change == "role":
        message = frame("START", 1).to_dict()
    elif change == "exchange":
        message["exchange_id"] = "22222222-2222-4222-8222-222222222222"
        message["body"]["exchange_id"] = message["exchange_id"]
    else:
        host.position = len(host.steps)
    raw = builder.wire(message)
    os.write(executor.write_fd, len(raw).to_bytes(4, "big") + raw)
    with pytest.raises(op.OperationalError, match="FRAME_ORDER_INVALID|SECOND_OPERATION_DENIED"):
        host.receive()


@pytest.mark.parametrize("dispatched", [False, True])
def test_deadline_is_bounded_and_unknown_after_mutating_dispatch(pair, dispatched):
    host, _ = pair(budget=0.03)
    if dispatched:
        host.mark_dispatched()
    with pytest.raises(
        op.OperationalError, match="UNCERTAIN_PUBLICATION" if dispatched else "EXCHANGE_EXPIRED"
    ):
        host.receive()


@pytest.mark.parametrize("dispatched", [False, True])
def test_extra_stdout_after_final_step_is_rejected(pair, dispatched):
    host, executor = pair()
    host.position = len(host.steps)
    if dispatched:
        host.mark_dispatched()
    os.write(executor.write_fd, b"extra stdout must never be parsed as success\n")
    with pytest.raises(
        op.OperationalError, match="UNCERTAIN_PUBLICATION" if dispatched else "EXTRA_STDOUT"
    ):
        host.finish()
    assert not host.finished


def test_fixed_docker_argv_has_no_request_shell_env_options_or_retry():
    identity = op.ExecutorProcessIdentity("a" * 64, "b" * 64, "c" * 64)
    calls = []

    def unavailable(argv, **kwargs):
        calls.append((argv, kwargs))
        raise OSError("secret endpoint not echoed")

    adapter = op.FixedDockerAdapter(
        identity=identity, observer=lambda: identity, process_factory=unavailable
    )
    with pytest.raises(op.OperationalError, match="TRANSPORT_UNAVAILABLE"):
        adapter.open(exchange="START", exchange_id=builder.UUID)
    argv, kwargs = calls[0]
    assert argv == (
        "/usr/bin/docker",
        "exec",
        "--interactive",
        "a" * 64,
        "/usr/local/bin/python",
        "-B",
        "/app/manage.py",
        "s11_ex10",
        "bridge",
    )
    assert kwargs["shell"] is False and kwargs["close_fds"] is True
    assert "-e" not in argv and "-t" not in argv and "--privileged" not in argv
    with pytest.raises(op.OperationalError, match="SECOND_OPERATION_DENIED"):
        adapter.open(exchange="START", exchange_id=builder.UUID)
    assert len(calls) == 1


def test_fixed_docker_observed_identity_changed_denied_before_process_launch():
    identity = op.ExecutorProcessIdentity("a" * 64, "b" * 64, "c" * 64)
    current = [identity]
    calls = []
    adapter = op.FixedDockerAdapter(
        identity=identity,
        observer=lambda: current[0],
        process_factory=lambda *args, **kwargs: calls.append(1),
    )
    current[0] = op.ExecutorProcessIdentity("d" * 64, "b" * 64, "c" * 64)
    with pytest.raises(op.OperationalError, match="PROCESS_IDENTITY_CHANGED"):
        adapter.open(exchange="START", exchange_id=builder.UUID)
    assert calls == []


def test_actual_owned_process_pipe_handoff_allows_eof_without_original_fd_leak():
    import subprocess
    import sys

    identity = op.ExecutorProcessIdentity("a" * 64, "b" * 64, "c" * 64)

    def factory(argv, **kwargs):
        assert argv[0] == "/usr/bin/docker"
        return subprocess.Popen(
            [sys.executable, "-B", "-c", "import sys; sys.stdin.buffer.read()"], **kwargs
        )

    adapter = op.FixedDockerAdapter(
        identity=identity, observer=lambda: identity, process_factory=factory
    )
    channel = adapter.open(exchange="SINGLE_RPC", exchange_id=builder.UUID, budget=1)
    try:
        assert all(
            stream.closed
            for stream in (adapter.process.stdin, adapter.process.stdout, adapter.process.stderr)
        )
        channel.position = len(channel.steps)
        channel.finish()
        assert adapter.process.wait(timeout=1) == 0
    finally:
        channel.close()
        if adapter.process.poll() is None:
            adapter.process.terminate()
        adapter.process.wait(timeout=1)
        for stream in (adapter.process.stdin, adapter.process.stdout, adapter.process.stderr):
            stream.close()


@pytest.mark.parametrize("stage", ["premature-finish", "second-frame"])
def test_every_stage_failure_after_dispatch_remains_uncertain(pair, stage):
    host, _ = pair()
    host.mark_dispatched()
    with pytest.raises(op.OperationalError, match="UNCERTAIN_PUBLICATION"):
        if stage == "premature-finish":
            host.finish()
        else:
            host.position = len(host.steps)
            host.send(frame("START", 1).canonical)


def test_named_fifo_is_not_admitted_as_private_anonymous_process_pipe():
    import tempfile

    with tempfile.TemporaryDirectory(
        prefix="s11-b3-test-w06-fifo-", dir=os.environ["S11_B3_TEST_BASE"]
    ) as root:
        fifo = Path(root) / "named.fifo"
        os.mkfifo(fifo, 0o600)
        named = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        read, write = os.pipe()
        channel = None
        try:
            with pytest.raises(op.OperationalError, match="PRIVATE_PIPE_REQUIRED"):
                channel = op.PipeFrameChannel(
                    read_fd=named, write_fd=write, exchange="START", exchange_id=builder.UUID
                )
        finally:
            if channel is not None:
                channel.close()
            for fd in (named, read, write):
                os.close(fd)


def test_wrong_access_end_of_anonymous_pipe_is_denied_before_protocol_io():
    read, write = os.pipe()
    other_read, other_write = os.pipe()
    channel = None
    try:
        with pytest.raises(op.OperationalError, match="PRIVATE_PIPE_REQUIRED"):
            channel = op.PipeFrameChannel(
                read_fd=write, write_fd=other_read, exchange="START", exchange_id=builder.UUID
            )
    finally:
        if channel is not None:
            channel.close()
        for fd in (read, write, other_read, other_write):
            os.close(fd)


def test_failed_pipe_flag_configuration_sanitized_and_no_duplicate_fd_leak(monkeypatch):
    read, write = os.pipe()
    other_read, other_write = os.pipe()
    before = set(os.listdir("/proc/self/fd"))

    def fault(fd, value):
        raise OSError("private arbitrary OS details must be sanitized")

    monkeypatch.setattr(op.os, "set_inheritable", fault)
    try:
        with pytest.raises(op.OperationalError, match="PRIVATE_PIPE_REQUIRED"):
            op.PipeFrameChannel(
                read_fd=read, write_fd=other_write, exchange="START", exchange_id=builder.UUID
            )
        assert set(os.listdir("/proc/self/fd")) == before
    finally:
        for fd in (read, write, other_read, other_write):
            os.close(fd)


def test_all_owned_fds_close_even_if_one_close_fails_and_primary_unknown_preserved(
    pair, monkeypatch
):
    host, _ = pair()
    owned = set(host.identities)
    closed = []
    original = os.close

    def fault(fd):
        original(fd)
        closed.append(fd)
        if len(closed) == 1:
            raise OSError("simulated close error after Linux consumed FD")

    monkeypatch.setattr(op.os, "close", fault)
    with pytest.raises(op.OperationalError, match="UNCERTAIN_PUBLICATION"):
        with host:
            raise op.OperationalError("UNCERTAIN_PUBLICATION")
    assert owned <= set(closed) and host.closed
    monkeypatch.setattr(op.os, "close", original)


@pytest.mark.parametrize("dispatched", [False, True])
def test_bounded_stderr_is_discarded_and_overflow_never_exposes_contents(dispatched):
    in_read, in_write = os.pipe()
    out_read, out_write = os.pipe()
    error_read, error_write = os.pipe()
    channel = op.PipeFrameChannel(
        read_fd=in_read,
        write_fd=out_write,
        error_fd=error_read,
        exchange="START",
        exchange_id=builder.UUID,
        budget=1,
    )
    for fd in (in_read, out_write, error_read):
        os.close(fd)
    try:
        if dispatched:
            channel.mark_dispatched()
        os.write(error_write, b"S" * 4097)
        with pytest.raises(
            op.OperationalError, match="UNCERTAIN_PUBLICATION" if dispatched else "DIAGNOSTIC_LIMIT"
        ) as raised:
            channel.receive()
        assert "SSSS" not in str(raised.value) and channel.error_count == 4097
    finally:
        channel.close()
        for fd in (in_write, out_read, error_write):
            os.close(fd)


@pytest.fixture
def native_session(*, evidence_root=None):
    """Actual external B1/B2 files, test-only approved OS/source/deployment provider."""
    import types

    spec = importlib.util.spec_from_file_location(
        "w06_b2_files_" + __name__.replace(".", "_"),
        Path(__file__).with_name("test_ex10_session_v2.py"),
    )
    bf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bf)
    original_mkdir = bf.mkdir

    def integration_mkdir(path):
        if evidence_root is None or path.name != "evidence" or path.parent.name != "roots":
            return original_mkdir(path)
        assert isinstance(evidence_root, Path) and evidence_root.is_absolute()
        assert evidence_root.is_dir() and not evidence_root.is_symlink()
        info = evidence_root.lstat()
        assert (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (
            os.getuid(),
            os.getgid(),
            0o700,
        )
        assert not any(evidence_root.iterdir())
        return evidence_root

    bf.mkdir = integration_mkdir
    generator = bf.fixture.__wrapped__()
    try:
        value, header, context, parent, home = next(generator)
    finally:
        bf.mkdir = original_mkdir
    stores = {}

    def store(path):
        root = str(path.parent)
        if root not in stores:
            stores[root] = op.durable.PrivateStore(root, h.identity(path.parent))
        return stores[root]

    bindings = []

    def bind(name, path, raw, schema):
        if not path.exists():
            path.write_bytes(raw)
            path.chmod(0o600)
        reference = {
            "path": str(path),
            "sha256": op.hashlib.sha256(raw).hexdigest(),
            "schema_version": schema,
        }
        bindings.append(
            op.HostArtifactBinding(name, op.sql.canonical_bytes(reference), store(path))
        )

    profile = op.profile_codec.parse_runtime_profile_bytes(bf.encoded(value))
    session = op.session_codec.new_official_session_v2(header, profile=profile)
    bind("profile", home / "profile.json", profile._bytes, op.profile_codec.SCHEMA_VERSION)
    bind("session", parent / "session.json", session._bytes, op.session_codec.SESSION_V2_SCHEMA)
    bind(
        "header",
        home / "header.json",
        op.session_codec.canonical_session_header_bytes(header),
        "s11-ex10-session-v2-header-projection-v1",
    )
    for name in op.profile_codec.REFERENCE_FIELDS:
        ref = value[name]
        bind(name, Path(ref["path"]), Path(ref["path"]).read_bytes(), ref["schema_version"])
    ref = header["runtime_profile_approval_reference"]
    bind(
        "runtime_profile_approval_reference",
        Path(ref["path"]),
        Path(ref["path"]).read_bytes(),
        ref["schema_version"],
    )
    path_context = {
        "repo_root": value["source_host_root"],
        "historical_roots": [],
        "prior_session_roots": [],
        "running_sources": ["backend/module.py"],
        "approved_reference_schemas": {
            name: [value[name]["schema_version"]] for name in op.profile_codec.REFERENCE_FIELDS
        },
    }
    identity = op.ExecutorProcessIdentity(
        "a" * 64, "b" * 64, value["deployment_verification_reference"]["sha256"]
    )
    calls = []

    def approval(**kw):
        # This constructor-installed fixture trust provider admits only the exact
        # external synthetic source/approval closure. It never issues real policy.
        assert kw["profile"].to_dict() == value and kw["session"].header == header
        assert dict(kw["approvals"]) == {
            name: b"{}\n"
            for name in (*op.profile_codec.REFERENCE_FIELDS, "runtime_profile_approval_reference")
        }
        assert kw["expected_process"] == identity
        calls.append("approved")

    def observer(**kw):
        assert kw["profile"].to_dict() == value and kw["session"].header == header
        calls.append("observed")
        return identity

    registry = op.HostArtifactRegistry(tuple(bindings))
    options = dict(
        registry=registry,
        path_context=op.sql.canonical_bytes(path_context),
        source_revision=value["source_revision"],
        launcher_blobs=op.sql.canonical_bytes(header["launcher_blobs"]),
        expected_tip=session._expected_tip,
        expected_count=session._expected_count,
        process=identity,
        approval_verifier=approval,
        deployment_observer=observer,
    )
    qualifier = op.NativeSessionQualifier(**options)
    try:
        yield types.SimpleNamespace(
            value=value,
            header=header,
            home=home,
            parent=parent,
            stores=stores,
            registry=registry,
            qualifier=qualifier,
            options=options,
            calls=calls,
            profile=profile,
            session=session,
            bindings=bindings,
        )
    finally:
        for item in stores.values():
            item.close()
        generator.close()


def test_native_b1_files_full_b2_history_and_27_header_projection(native_session):
    a = native_session
    result = a.qualifier.qualify()
    assert result.profile.to_dict() == a.value and result.session.header == a.header
    assert len(result.session.header) == 27 and a.calls == ["approved", "observed"]
    assert result.process == a.options["process"] and result.path_identities


@pytest.mark.parametrize(
    "name",
    [
        "profile",
        "session",
        "header",
        *op.profile_codec.REFERENCE_FIELDS,
        "runtime_profile_approval_reference",
    ],
)
def test_actual_approved_artifact_tamper_denies_before_approval(native_session, name):
    a = native_session
    Path(a.registry.reference(name)["path"]).write_bytes(b'{"changed":true}\n')
    with pytest.raises(op.OperationalError):
        a.qualifier.qualify()
    assert a.calls == []


@pytest.mark.parametrize("replacement", ["alias", "unapproved", "symlink", "hardlink"])
def test_fixed_artifact_registry_cannot_be_selected_or_aliased_by_rpc(native_session, replacement):
    a = native_session
    expected = a.registry.reference("header")
    if replacement == "alias":
        duplicate = op.HostArtifactBinding("second", a.bindings[0].reference, a.bindings[0].store)
        with pytest.raises(op.OperationalError, match="ARTIFACT_ALIAS"):
            op.HostArtifactRegistry(tuple(a.bindings + [duplicate]))
    elif replacement == "unapproved":
        wrong = dict(expected, path=str(a.home / "unapproved.json"))
        with pytest.raises(op.OperationalError, match="ARTIFACT_UNAPPROVED"):
            a.registry.read("header", reference=wrong)
    else:
        path = Path(expected["path"])
        other = a.home / "preserved-header.json"
        path.rename(other)
        if replacement == "symlink":
            path.symlink_to(other)
        else:
            os.link(other, path)
        with pytest.raises(op.OperationalError, match="ARTIFACT_CHANGED"):
            a.registry.read("header")


@pytest.mark.parametrize(
    "change",
    ["head", "launcher", "tip", "count", "deployment", "approval", "compose", "path-inode"],
)
def test_native_source_session_deployment_and_writer_policy_substitutions_denied(
    native_session, change
):
    a = native_session
    options = dict(a.options)
    if change == "head":
        options["source_revision"] = "f" * 40
    elif change == "launcher":
        blobs = dict(a.header["launcher_blobs"])
        blobs[next(iter(blobs))] = "f" * 40
        options["launcher_blobs"] = op.sql.canonical_bytes(blobs)
    elif change == "tip":
        options["expected_tip"] = "f" * 64
    elif change == "count":
        options["expected_count"] = 1
    elif change == "deployment":
        options["deployment_observer"] = lambda **kw: op.ExecutorProcessIdentity(
            "f" * 64, "b" * 64, "c" * 64
        )
    elif change == "approval":
        options["approval_verifier"] = lambda **kw: False
    elif change == "compose":
        Path(a.value["compose_files"][0]["path"]).write_bytes(b"altered compose\n")
    else:
        path = Path(a.value["host_roots"]["backup"]["path"])
        path.rename(path.with_name("backup-preserved"))
        path.mkdir(mode=0o700)
    with pytest.raises(op.OperationalError):
        op.NativeSessionQualifier(**options).qualify()


def test_host_provider_errors_do_not_disclose_private_source_or_deployment_details(native_session):
    a = native_session

    def reject(**kw):
        raise RuntimeError("private host source/token/location")

    a.options["approval_verifier"] = reject
    with pytest.raises(op.OperationalError, match="^HOST_APPROVAL_REQUIRED$"):
        op.NativeSessionQualifier(**a.options).qualify()


def test_native_accepts_approved_0755_source_directory_by_role(native_session):
    a = native_session
    root = Path(a.value["source_host_root"])
    root.chmod(0o755)
    header = copy.deepcopy(a.header)
    next(x for x in header["path_identity_binding"] if x["path"] == str(root))["mode"] = "0755"
    session = op.session_codec.new_official_session_v2(header, profile=a.profile)
    bindings = []
    for item in a.bindings:
        raw = None
        if item.name == "header":
            raw = op.session_codec.canonical_session_header_bytes(header)
        if item.name == "session":
            raw = session._bytes
        if raw is not None:
            ref = op.sql.parse_canonical(item.reference)
            Path(ref["path"]).write_bytes(raw)
            ref["sha256"] = op.hashlib.sha256(raw).hexdigest()
            item = op.HostArtifactBinding(item.name, op.sql.canonical_bytes(ref), item.store)
        bindings.append(item)
    options = dict(
        a.options,
        registry=op.HostArtifactRegistry(tuple(bindings)),
        expected_tip=session._expected_tip,
        approval_verifier=lambda **kw: None,
        deployment_observer=lambda **kw: a.options["process"],
    )
    assert op.NativeSessionQualifier(**options).qualify().session.header == header


def test_native_compose_changed_during_external_approval_cannot_be_qualified(native_session):
    a = native_session
    approve = a.options["approval_verifier"]

    def changed(**kw):
        approve(**kw)
        Path(a.value["compose_files"][0]["path"]).write_bytes(b"changed during approval\n")

    options = dict(a.options, approval_verifier=changed)
    with pytest.raises(op.OperationalError, match="SOURCE_CHANGED"):
        op.NativeSessionQualifier(**options).qualify()


@pytest.mark.parametrize("provider", ["approval_verifier", "deployment_observer"])
def test_same_type_provider_exception_is_redacted_at_host_boundary(native_session, provider):
    a = native_session

    def reject(**kw):
        raise op.OperationalError("private arbitrary host token/path")

    options = dict(a.options)
    options[provider] = reject
    with pytest.raises(op.OperationalError) as caught:
        op.NativeSessionQualifier(**options).qualify()
    assert "private" not in str(caught.value)


def test_host_artifact_binding_cannot_be_rebound_during_qualification(native_session):
    a = native_session
    original = a.registry.bindings["profile"]
    raw = a.registry.read("profile")
    path = a.home / "alternate-profile.json"
    path.write_bytes(raw)
    path.chmod(0o600)
    ref = a.registry.reference("profile")
    ref["path"] = str(path)
    different = op.HostArtifactBinding("profile", op.sql.canonical_bytes(ref), original.store)

    def replace_binding(**kw):
        a.registry.bindings["profile"] = different

    with pytest.raises(op.OperationalError):
        op.NativeSessionQualifier(**dict(a.options, approval_verifier=replace_binding)).qualify()
    assert a.registry.bindings["profile"] is original


def test_native_header_full_stat_projection_rechecked_after_deployment_observer(native_session):
    a = native_session
    observer = a.options["deployment_observer"]

    def changed(**kw):
        result = observer(**kw)
        Path(a.value["compose_files"][0]["path"]).chmod(0o600)
        return result

    with pytest.raises(op.OperationalError, match="PATH_IDENTITY_CHANGED"):
        op.NativeSessionQualifier(**dict(a.options, deployment_observer=changed)).qualify()


@pytest.fixture
def native_start_rig(native_session):
    """Real B3/B4 CAS on disposable stores plus native B1/B2/EVD/GOV files."""
    import types
    from dataclasses import replace

    # Load all native modules in the same frozen isolated package as the W06 module.
    assert cf.coord is op.coordination
    generator = cf.rig.__wrapped__(types.SimpleNamespace(param=("ND",)))
    a = next(generator)
    n = native_session
    artifact_generator = cf.initial_artifacts.__wrapped__()
    sample = next(artifact_generator)
    try:
        # Actual descriptor replaces the earlier dummy approval, before any read.
        dref = n.value["historical_lifecycle_authority_reference"]
        Path(dref["path"]).write_bytes(op.sql.canonical_bytes(a.f.descriptor))
        dref["sha256"] = op.sql.digest(a.f.descriptor)
        dref["schema_version"] = a.f.descriptor["schema_version"]
        a.f.descriptor_ref = copy.deepcopy(dref)
        n.header["historical_lifecycle_authority_reference"] = copy.deepcopy(dref)
        n.header["runtime_profile_sha256"] = op.profile_codec.runtime_profile_sha256(n.value)
        profile = op.profile_codec.parse_runtime_profile_bytes(op.sql.canonical_bytes(n.value))
        session = op.session_codec.new_official_session_v2(n.header, profile=profile)
        rebuilt = []
        for binding in n.bindings:
            ref = op.sql.parse_canonical(binding.reference)
            if binding.name == "profile":
                Path(ref["path"]).write_bytes(profile._bytes)
            if binding.name == "session":
                Path(ref["path"]).write_bytes(session._bytes)
            if binding.name == "header":
                Path(ref["path"]).write_bytes(
                    op.session_codec.canonical_session_header_bytes(n.header)
                )
            if binding.name == "historical_lifecycle_authority_reference":
                ref = copy.deepcopy(dref)
            ref["sha256"] = op.hashlib.sha256(Path(ref["path"]).read_bytes()).hexdigest()
            rebuilt.append(
                op.HostArtifactBinding(binding.name, op.sql.canonical_bytes(ref), binding.store)
            )

        def approve(**kw):
            assert kw["profile"].to_dict() == n.value and kw["session"].header == n.header
            assert dict(kw["approvals"])[
                "historical_lifecycle_authority_reference"
            ] == op.sql.canonical_bytes(a.f.descriptor)

        path_context = op.sql.parse_canonical(n.options["path_context"])
        path_context["approved_reference_schemas"]["historical_lifecycle_authority_reference"] = [
            dref["schema_version"]
        ]
        n.options.update(
            registry=op.HostArtifactRegistry(tuple(rebuilt)),
            approval_verifier=approve,
            expected_tip=session._expected_tip,
            path_context=op.sql.canonical_bytes(path_context),
        )
        n.qualifier = op.NativeSessionQualifier(**n.options)
        native = n.qualifier.qualify()

        def store_file(name, raw, schema):
            path = n.home / (name + ".json")
            # All outputs are synthetic; request capabilities remain in private
            # fixture/journal files and are never copied to execution evidence.
            path.write_bytes(raw)
            path.chmod(0o600)
            return op.HostArtifactBinding(
                name,
                op.sql.canonical_bytes(op._artifact_reference(str(path), raw, schema)),
                n.stores[str(n.home)],
            )

        base_receipt = store_file(
            "base_receipt",
            op.sql.canonical_bytes(a.f.receipt),
            a.f.receipt["witness_schema_version"],
        )
        policy = store_file("authorization", b"{}\n", "s11-ex10-synthetic-authorization-v1")
        # Entire policy/approval set is disposable constructor-fixed test material.
        policy_bindings = [
            store_file(name, b"{}\n", "s11-ex10-synthetic-approval-v1")
            for name in (
                "host_identity",
                "bridge_approval",
                "witness_transport",
                "writer_closure",
                "recovery_policy",
            )
        ]
        policy_bindings.append(
            store_file("coordinator_registry", a.journal.binding, "s11-ex10-test-host-binding-v1")
        )
        ledger_store = op.durable.PrivateStore(str(a.f.root), h.identity(a.f.root))
        n.stores[str(a.f.root)] = ledger_store

        def approved_policy(**kw):
            assert (
                kw["actual_root_binding_sha256"] == "a" * 64
                and kw["native"].profile.to_dict() == n.value
            )
            assert kw["canonical_policy"] == op.sql.canonical_bytes(
                host_policy.facts(native=kw["native"], journal=a.journal)
            )

        def approved_envelope(**kw):
            assert (
                kw["canonical_envelope"] == b"{}\n"
            )  # W07 replaces this explicit test-only B5 provider.
            assert kw["native"].profile.to_dict() == n.value

        policy_options = dict(
            registry=op.HostArtifactRegistry(tuple(policy_bindings)),
            ledger_store=ledger_store,
            session_qualifier=n.qualifier,
            ledger_name="ledger.sqlite3",
            ledger_identity=h.identity(a.f.database),
            approved_root_binding_sha256="a" * 64,
            approval_verifier=approved_policy,
            envelope_verifier=approved_envelope,
            required_preflight_checks=sample["context"].preflight_required_checks,
        )
        host_policy = op.NativeHostPolicy(**policy_options)
        policy_binding = store_file(
            "policy",
            op.sql.canonical_bytes(host_policy.facts(native=native, journal=a.journal)),
            "s11-ex10-synthetic-host-policy-v1",
        )
        policy_options["registry"] = op.HostArtifactRegistry(
            tuple(policy_bindings + [policy_binding])
        )
        host_policy = op.NativeHostPolicy(**policy_options)
        policy = op.HostArtifactBinding(
            "authorization", policy_binding.reference, policy_binding.store
        )
        envelope_binding = store_file("envelope", b"{}\n", "s11-ex10-synthetic-envelope-v1")
        raw, record_raw = cf.proposal(a, label="native-full")
        request = wire.parse_request_bytes(raw).to_dict()
        identity = request["payload"]["run_identity"]
        identity["git_sha"] = n.value["source_revision"]
        identity["run_id"] = wire.make_run_id(
            started_at=wire._time(identity["started_at"]),
            git_sha=identity["git_sha"],
            dataset_id=identity["dataset_id"],
            scenario_id=identity["scenario_id"],
            seed=identity["seed"],
            execution_nonce=identity["execution_nonce"],
        )
        identity["identity_digest"] = op.sql.digest(
            {k: v for k, v in identity.items() if k != "identity_digest"}
        )
        request.update(
            session_id=n.header["session_id"],
            session_header_sha256=op.sql.parse_canonical(native.header_reference)["sha256"],
            runtime_profile_sha256=op.sql.parse_canonical(native.profile_reference)["sha256"],
            source_revision=identity["git_sha"],
            project=n.value["project"],
            authorization_reference=op.sql.parse_canonical(policy.reference),
        )
        request["payload"]["expected_head"]["receipt_reference"] = op.sql.parse_canonical(
            base_receipt.reference
        )
        cap = request["capability"]
        for field in (
            "session_id",
            "session_header_sha256",
            "runtime_profile_sha256",
            "source_revision",
            "authorization_reference",
        ):
            cap[field] = copy.deepcopy(request[field])
        cap.update(
            expected_run_id=identity["run_id"],
            deployment_verification_sha256=n.options["process"].deployment_verification_sha256,
            container_identity_digest=n.options["process"].identity_digest,
        )
        pf = copy.deepcopy(sample["payloads"]["preflight_reference"])
        pf.update(
            git_sha=identity["git_sha"],
            expected_git_sha=identity["git_sha"],
            checked_at=identity["started_at"],
        )
        preflight_binding = store_file(
            "preflight",
            op.sql.canonical_bytes(pf),
            op.evidence.ARTIFACT_SCHEMAS["preflight_reference"],
        )
        request["payload"].update(
            deployment_verification_reference=n.value["deployment_verification_reference"],
            descriptor_reference=dref,
            operational_envelope_reference=op.sql.parse_canonical(envelope_binding.reference),
            profile_reference=op.sql.parse_canonical(native.profile_reference),
            session_reference=op.sql.parse_canonical(native.header_reference),
            preflight_reference=op.sql.parse_canonical(preflight_binding.reference),
        )
        raw = builder.signed_request(request, a.bridge, a.holder)
        request = wire.parse_request_bytes(raw).to_dict()
        old_context = next(iter(a.sources.approved.values()))
        a.sources.approved[op.hashlib.sha256(raw).hexdigest()] = replace(
            old_context,
            session_id=request["session_id"],
            session_header_sha256=request["session_header_sha256"],
            runtime_profile_sha256=request["runtime_profile_sha256"],
            source_revision=request["source_revision"],
            project=request["project"],
            authorization_reference=policy.reference,
            deployment_verification_sha256=cap["deployment_verification_sha256"],
            container_identity_digest=cap["container_identity_digest"],
            expected_run_id=identity["run_id"],
            expected_payload=op.sql.canonical_bytes(request["payload"]),
        )
        record = wire.qualify_reservation(record_raw).to_dict()
        for field in (
            "session_id",
            "session_header_sha256",
            "runtime_profile_sha256",
            "source_revision",
            "project",
            "request_digest",
        ):
            record[field] = request[field]
        record.update(
            run_identity=identity,
            run_id=identity["run_id"],
            deployment_verification_reference=n.value["deployment_verification_reference"],
            coordination_policy_reference=host_policy.reference,
            host_root_binding_sha256=host_policy.root_binding,
            operator_authorization_reference=host_policy.reference,
        )
        record = cf.coord._seal_record(record)
        record_raw = op.sql.canonical_bytes(record)
        a.sources.record_inputs[request["request_id"]] = record
        yield types.SimpleNamespace(
            a=a,
            n=n,
            sample=sample,
            raw=raw,
            record_raw=record_raw,
            store_file=store_file,
            base_receipt=base_receipt,
            policy=policy,
            dref=dref,
            old_context=old_context,
            host_policy=host_policy,
            policy_options=policy_options,
            envelope_binding=envelope_binding,
        )
    finally:
        artifact_generator.close()
        generator.close()


def prepare_native_open(rig, attempt, *, supplied_permit=None):
    import types
    import uuid
    from dataclasses import replace

    a, n, sample, record_raw, store_file, base_receipt, policy, dref, old_context = (
        getattr(rig, k)
        for k in (
            "a",
            "n",
            "sample",
            "record_raw",
            "store_file",
            "base_receipt",
            "policy",
            "dref",
            "old_context",
        )
    )
    raw = attempt.request.request.canonical
    request = wire.parse_request_bytes(raw).to_dict()
    identity = request["payload"]["run_identity"]
    cap = request["capability"]
    record = wire.qualify_reservation(record_raw).to_dict()
    commit = cf.existing_open_sources(a, attempt, sample)
    # Retain fixed durable backend setup/sign_response but replace W05's
    # deliberately partial fixture qualifier with actual W06 publication.
    root = Path(n.value["host_roots"]["evidence"]["path"])
    directory = root / "runs" / identity["run_id"] / "metadata" / "EX-10"
    directory.mkdir(mode=0o700, parents=True)
    for path in (
        root / "runs",
        root / "runs" / identity["run_id"],
        root / "runs" / identity["run_id"] / "metadata",
        directory,
    ):
        path.chmod(0o700)
    evd = copy.deepcopy(a.existing_evidence.opening)
    for field, filename in zip(
        op.evidence.REFERENCE_FIELDS,
        ("run-manifest.initial.json", "ex10-start.json", "ex10-preflight.json", "ex10-plan.json"),
        strict=True,
    ):
        source = Path(evd[field]["path"])
        path = directory / filename
        path.write_bytes(source.read_bytes())
        path.chmod(0o600)
        evd[field]["path"] = str(path)
    preflight_binding = store_file(
        "preflight",
        Path(evd["preflight_reference"]["path"]).read_bytes(),
        evd["preflight_reference"]["schema_version"],
    )
    now = a.now()
    permit = builder.fill(wire.schema_definitions()["open_one_permit"])
    permit.update(
        issued_at=cf.coord._utc(now),
        expires_at=record["freshness_deadline"],
        permit_id=str(uuid.uuid4()),
        permit_nonce="e" * 64,
        namespace="S11C3-EX10",
        authority_id=request["authority_id"],
        authority_epoch=request["authority_epoch"],
        authorization_reference=request["authorization_reference"],
        base_authority_revision=request["authority_revision"],
        expected_open_revision=request["authority_revision"] + 1,
        bridge_key_id=a.bridge_pin.key_id,
        bridge_key_epoch=a.bridge_pin.key_epoch,
        container_identity_digest=cap["container_identity_digest"],
        deployment_verification_sha256=cap["deployment_verification_sha256"],
        exchange_id=request["exchange_id"],
        holder_public_key_hex=a.holder.public_key().public_bytes_raw().hex(),
        attempt_nonce=record["nonce"],
        acknowledgment_digest=record["acknowledgment_digest"],
        preflight_reference=op.sql.parse_canonical(preflight_binding.reference),
        reserve_request_id=request["request_id"],
        reserve_request_digest=request["request_digest"],
        **{
            k: attempt.record[k]
            for k in (
                "fencing_token",
                "project",
                "reservation_digest",
                "reservation_id",
                "reservation_receipt_reference",
                "run_identity",
                "runtime_profile_sha256",
                "session_header_sha256",
                "session_id",
                "source_revision",
            )
        },
    )
    permit["permit_digest"] = op.sql.digest(
        {k: v for k, v in permit.items() if k not in ("permit_digest", "signature")}
    )
    permit_raw = builder.signed(permit, a.bridge, wire.DOMAINS["open_one_permit"])
    if supplied_permit is not None:
        permit_raw = supplied_permit.canonical
        permit = supplied_permit.to_dict()
    evd.update(open_permit_digest=permit["permit_digest"], opened_at=cf.coord._utc(a.now()))
    evd_raw = cf.ebuilder.sign(evd, a.holder)
    evd = op.evidence.parse_open_evidence_bytes(evd_raw).to_dict()
    payload = wire.parse_request_bytes(commit).to_dict()["payload"]
    payload.update(open_evidence=evd, open_permit_digest=permit["permit_digest"])
    commit = cf.bound_request(a, attempt, "commit-open", payload, "native-full-commit")
    # Capabilities must be distinct across reserve and commit.
    cv = wire.parse_request_bytes(commit).to_dict()
    cv["capability"]["capability_id"] = str(uuid.uuid4())
    commit = builder.signed_request(cv, a.bridge, a.holder)
    cv = wire.parse_request_bytes(commit).to_dict()
    a.sources.approved[op.hashlib.sha256(commit).hexdigest()] = replace(
        old_context,
        request_id=cv["request_id"],
        nonce=cv["nonce"],
        exchange_id=cv["exchange_id"],
        operation="commit-open",
        session_id=cv["session_id"],
        session_header_sha256=cv["session_header_sha256"],
        runtime_profile_sha256=cv["runtime_profile_sha256"],
        source_revision=cv["source_revision"],
        project=cv["project"],
        authorization_reference=policy.reference,
        deployment_verification_sha256=cap["deployment_verification_sha256"],
        container_identity_digest=cap["container_identity_digest"],
        expected_run_id=identity["run_id"],
        reservation_id=record["reservation_id"],
        reservation_digest=record["reservation_digest"],
        fencing_token=record["fencing_token"],
        expected_payload=op.sql.canonical_bytes(payload),
    )
    gov_root = n.home / "governance"
    gov_root.mkdir(mode=0o700)
    gov_store = op.durable.PrivateStore(str(gov_root), h.identity(gov_root))
    n.stores[str(gov_root)] = gov_store
    extra = [
        base_receipt,
        policy,
        preflight_binding,
        rig.envelope_binding,
        store_file(
            "record", op.sql.canonical_bytes(attempt.record), attempt.record["schema_version"]
        ),
        store_file("reserve_request", raw, request["schema_version"]),
        store_file("commit_request", commit, cv["schema_version"]),
        store_file("permit", permit_raw, permit["schema_version"]),
        store_file("evidence", evd_raw, evd["schema_version"]),
    ]
    extra.append(
        op.HostArtifactBinding("descriptor", op.sql.canonical_bytes(dref), n.stores[str(n.home)])
    )
    extra.append(
        op.HostArtifactBinding(
            "reservation_receipt",
            op.sql.canonical_bytes(attempt.record["reservation_receipt_reference"]),
            a.stores["receipts"],
        )
    )
    publisher = op.NativeOpenPublisher(
        ledger_store=a.stores["ledger"],
        ledger_name="ledger.sqlite3",
        ledger_identity=h.identity(a.f.database),
        candidate_store=a.stores["candidate"],
        floor=a.b3_floor,
        transport=a.b3_transport,
        signer_pin=a.f.pin,
        clock=a.now,
        clock_health=lambda: True,
    )
    original_reader = a.sources.intent_reader

    def read_candidate(path):
        if path.startswith(a.stores["candidate"].root + "/"):
            return a.stores["candidate"].read(path)
        return original_reader(path)

    a.sources.intent_reader = read_candidate
    result = types.SimpleNamespace(
        a=a,
        n=n,
        attempt=attempt,
        raw=raw,
        record_raw=record_raw,
        commit=commit,
        publisher=publisher,
        qualifier=None,
        cert=None,
        prepared=None,
        extra=extra,
        store_file=store_file,
        permit=permit,
        permit_raw=permit_raw,
        evd=evd,
        gov_store=gov_store,
    )

    def make_qualifier(opening):
        head_binding = store_file(
            "base_head",
            opening.snapshot.canonical_current_head,
            "s11-ex10-authority-witness-head-v1",
        )
        registry = op.HostArtifactRegistry(tuple(extra + [head_binding]))
        qualifier = op.NativeGovernanceQualifier(
            session_qualifier=n.qualifier,
            registry=registry,
            certificate_store=gov_store,
            bridge_pin=a.bridge_pin,
            holder_public_key=a.holder.public_key(),
            reservation_id=record["reservation_id"],
            executor_fingerprint=sample["context"].executor_fingerprint,
            required_preflight_checks=sample["context"].preflight_required_checks,
            protocol="s11-ex10-v1",
            methodology="S11A-v1.1",
            host_policy=rig.host_policy,
        )
        result.qualifier = qualifier
        return qualifier

    result.make_qualifier = make_qualifier

    def qualify_open(*, authenticated_request, opening):
        qualifier = make_qualifier(opening)
        result.prepared = publisher.prepare(
            qualifier=qualifier, opening=opening, authenticated_request=authenticated_request
        )
        result.cert = result.prepared.governance_certificate
        return result.prepared

    a.sources.qualify_open = qualify_open
    return result


@pytest.fixture
def native_open(native_start_rig):
    rig = native_start_rig
    with rig.a.journal.opening(raw_request=rig.raw, raw_record=rig.record_raw) as attempt:
        yield prepare_native_open(rig, attempt)


def test_full_native_gov_all31_claims_and_actual_b3_b4_commit(native_open):
    a = native_open
    response = a.attempt.complete_open(raw_request=a.commit)
    assert wire.parse_response_bytes(response).to_dict()["status"] == "OK"
    assert len(a.cert.value) == 31 and a.cert.value["protocol"] == "s11-ex10-v1"
    assert a.a.b3_floor.current().revision == 1 and a.a.floor.current()["sequence"] == 2
    assert a.a.b3_transport.calls.count("B3_CAS") == 1
    assert a.a.transport.calls.count("RESERVE") == a.a.transport.calls.count("COMMIT_OPEN") == 1
    assert (
        Path(op.sql.parse_canonical(a.prepared.governance_reference)["path"]).read_bytes()
        == a.cert.canonical_bytes
    )
    assert len(a.cert.value["acknowledgments"]) == 1


def replay_native_open(a):
    snapshot = a.a.sources.read_authority()
    ref = op.sql.parse_canonical(a.prepared.governance_reference)
    inspector = op.NativeGovernanceHistory(((ref["reference_id"], a.qualifier),))
    return inspector.qualify(snapshot=snapshot, journal=a.a.journal)


def test_native_historical_gov_replays_original_timestamp_with_expired_permit(native_open):
    from datetime import timedelta

    a = native_open
    a.attempt.complete_open(raw_request=a.commit)
    permit = wire.parse_open_one_bytes(a.permit_raw)
    excluded = {
        "signature",
        "permit_digest",
        "issued_at",
        "expires_at",
        "permit_id",
        "permit_nonce",
        "kind",
        "schema_version",
    }
    context = wire.PermitContext(
        op.sql.canonical_bytes({k: v for k, v in permit.to_dict().items() if k not in excluded}),
        wire._time(a.permit["expires_at"]),
        wire._time(a.permit["expires_at"]) + timedelta(seconds=1),
    )
    with pytest.raises(wire.BridgeProtocolError, match="CAPABILITY_EXPIRED"):
        wire.verify_open_one(
            a.permit_raw,
            expected_context=context,
            bridge_pin=a.a.bridge_pin,
            holder_public_key=a.a.holder.public_key(),
        )
    before = a.a.journal.read()
    calls = tuple(a.a.transport.calls)
    assert replay_native_open(a) == ((a.cert.value["run_id"], a.cert.canonical_bytes),)
    assert a.a.journal.read() == before and tuple(a.a.transport.calls) == calls


@pytest.mark.parametrize("field", list(op.governance.certificate_schema()["properties"]))
def test_committed_gov_tamper_of_each31_root_claim_blocks_full_inspect(native_open, field):
    a = native_open
    a.attempt.complete_open(raw_request=a.commit)
    path = Path(op.sql.parse_canonical(a.prepared.governance_reference)["path"])
    cert = copy.deepcopy(a.cert.value)
    value = cert[field]
    cert[field] = (
        value + 1
        if type(value) is int
        else value + "-tampered"
        if type(value) is str
        else {**value, "tampered": True}
        if type(value) is dict
        else value + [{}]
    )
    path.write_bytes(op.sql.canonical_bytes(cert))
    before = a.a.journal.read()
    calls = tuple(a.a.transport.calls)
    with pytest.raises(op.OperationalError, match="HISTORICAL_GOV_INVALID"):
        replay_native_open(a)
    assert a.a.journal.read() == before and tuple(a.a.transport.calls) == calls


@pytest.mark.parametrize(
    "name",
    [
        "record",
        "descriptor",
        "base_receipt",
        "base_head",
        "reserve_request",
        "commit_request",
        "reservation_receipt",
        "permit",
        "evidence",
    ],
)
def test_committed_native_gov_source_tamper_detected_without_republication(native_open, name):
    a = native_open
    a.attempt.complete_open(raw_request=a.commit)
    path = Path(a.qualifier.registry.reference(name)["path"])
    path.write_bytes(b'{"tampered":true}\n')
    calls = tuple(a.a.transport.calls)
    with pytest.raises((op.OperationalError, op.sql.AuthorityError, wire.BridgeProtocolError)):
        replay_native_open(a)
    assert tuple(a.a.transport.calls) == calls


def test_full_history_denies_native_run_without_fixed_context_and_preserves_imported_history(
    native_open,
):
    a = native_open
    inspector = op.NativeGovernanceHistory(())
    before = a.a.sources.read_authority()
    assert inspector.qualify(snapshot=before, journal=a.a.journal) == ()
    a.attempt.complete_open(raw_request=a.commit)
    with pytest.raises(op.OperationalError, match="HISTORICAL_GOV_UNQUALIFIED"):
        inspector.qualify(snapshot=a.a.sources.read_authority(), journal=a.a.journal)


def test_native_gov_create_exclusive_keeps_existing_orphan_bytes_and_no_b3_intent(native_open):
    a = native_open
    cid = op.governance.certificate_identity(
        {
            "namespace": "S11C3-EX10",
            "authority_id": a.attempt.record["authority_id"],
            "authority_epoch": a.attempt.record["authority_epoch"],
            "reservation": {"reservation_id": a.attempt.record["reservation_id"]},
        }
    )
    path = a.gov_store.root + "/" + cid + ".json"
    a.gov_store.create_exact(path, b"retained synthetic orphan\n")
    before = a.a.f.database.read_bytes()
    calls = a.a.b3_transport.calls.count("B3_CAS")
    with pytest.raises(op.sql.AuthorityError):
        a.attempt.complete_open(raw_request=a.commit)
    assert (
        a.a.f.database.read_bytes() == before
        and a.gov_store.read(path) == b"retained synthetic orphan\n"
    )
    assert a.a.b3_transport.calls.count("B3_CAS") == calls


@pytest.mark.parametrize(
    "field",
    [
        "source_revision",
        "protocol",
        "methodology",
        "profile_reference_id",
        "session_reference_id",
        "opened_at",
        "created_at",
    ],
)
def test_historical_replay_requires_actual_original_run_all_bindings(native_open, field):
    a = native_open
    a.attempt.complete_open(raw_request=a.commit)
    snapshot = a.a.sources.read_authority()
    state = op.history._verified(snapshot)["state"]
    run = copy.deepcopy(
        next(
            r
            for r in state["official_runs"]
            if r["run_id"] == a.cert.value["run_id"] and r["run_version"] == 0
        )
    )
    event = next(
        e
        for e in state["authority_events"]
        if e["event_kind"] == "RUN_OPENED" and e["run_id"] == run["run_id"]
    )
    if field in ("created_at", "opened_at"):
        run[field] = "2026-10-04T00:00:00.000000Z"
    elif field in ("source_revision", "profile_reference_id", "session_reference_id"):
        run[field] = "f" * len(run[field])
    else:
        run[field] = "substituted"
    with pytest.raises(op.OperationalError, match="HISTORICAL_GOV_INVALID"):
        a.qualifier.replay(
            snapshot=snapshot,
            journal=a.a.journal,
            original_run=run,
            original_event=event,
            reference=op.sql.parse_canonical(a.prepared.governance_reference),
        )


@pytest.mark.parametrize("field", ["protocol", "methodology"])
def test_native_gov_protocol_and_methodology_cannot_disagree_with_verified_manifest(
    native_open, field
):
    a = native_open
    q = a.make_qualifier(a.attempt)
    options = dict(
        session_qualifier=q.sessions,
        registry=q.registry,
        certificate_store=q.store,
        bridge_pin=q.pin,
        holder_public_key=q.holder,
        reservation_id=q.reservation_id,
        executor_fingerprint=q.fingerprint,
        required_preflight_checks=q.checks,
        protocol=q.protocol,
        methodology=q.methodology,
        host_policy=q.policy,
    )
    options[field] = "substituted-v1"
    with pytest.raises(op.OperationalError, match="HOST_CONTEXT_INVALID"):
        op.NativeGovernanceQualifier(**options)


def test_native_seal_uses_exact_current_pending_record_not_prior_pending_snapshot(native_open):
    a = native_open
    q = a.make_qualifier(a.attempt)
    state = a.a.journal.read()
    a.attempt._append_local(state, target="PENDING")
    a.attempt.check()
    assert a.attempt.record["record_sequence"] == 3
    with pytest.raises(op.OperationalError, match="RESERVATION_CONTEXT_INVALID"):
        q.seal(
            opening=a.attempt,
            authenticated_request=a.a.journal.authenticate(a.commit, "commit-open"),
            issued_at=a.a.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        )
    assert not list(Path(q.store.root).iterdir())


def test_preexisting_permit_preflight_and_confined_initial_copy_have_distinct_paths_same_bytes(
    native_open,
):
    a = native_open
    a.attempt.complete_open(raw_request=a.commit)
    pf = a.qualifier.registry.reference("preflight")
    assert pf["path"] != a.evd["preflight_reference"]["path"]
    assert Path(pf["path"]).read_bytes() == Path(a.evd["preflight_reference"]["path"]).read_bytes()
    assert a.permit["preflight_reference"] == pf
    assert replay_native_open(a) == ((a.cert.value["run_id"], a.cert.canonical_bytes),)


@pytest.mark.parametrize("elapsed_microseconds", [6_000_001, 121_000_000])
def test_native_critical_clock_and_permit_window_rechecked_after_all_qualification(
    native_open, elapsed_microseconds
):
    from datetime import timedelta

    a = native_open
    q = a.make_qualifier(a.attempt)
    approve = q.sessions.approve

    def clock_advanced(**kw):
        result = approve(**kw)
        a.a.journal.clock = lambda: a.a.now() + timedelta(microseconds=elapsed_microseconds)
        return result

    q.sessions.approve = clock_advanced
    authenticated = a.a.journal.authenticate(a.commit, "commit-open")
    issued = a.a.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with pytest.raises(op.OperationalError, match="HOST_CONTEXT_INVALID"):
        q.seal(opening=a.attempt, authenticated_request=authenticated, issued_at=issued)
    assert not list(Path(q.store.root).iterdir())


def test_native_sole_publication_checks_current_window_before_transport_dispatch(native_open):
    from datetime import timedelta

    a = native_open
    authenticated = a.a.journal.authenticate(a.commit, "commit-open")
    prepared = a.a.sources.qualify_open(authenticated_request=authenticated, opening=a.attempt)
    leg = prepared.publication["adapter"].transport
    before = a.a.b3_transport.calls.count("B3_CAS")
    a.a.journal.clock = lambda: a.a.now() + timedelta(seconds=6, microseconds=1)
    with pytest.raises(op.OperationalError, match="HOST_CONTEXT_INVALID"):
        leg.publish_b3(
            {"operation_id": a.cert.value["commit_request_id"]},
            a.attempt.record["previous_witness_digest"],
        )
    assert not leg.sent and a.a.b3_transport.calls.count("B3_CAS") == before


def test_clock_expiry_during_gov_fsync_keeps_orphan_and_prevents_b3_intent(
    native_open, monkeypatch
):
    from datetime import timedelta

    a = native_open
    create = a.gov_store.create_exact

    def slow(path, raw):
        create(path, raw)
        a.a.journal.clock = lambda: a.a.now() + timedelta(seconds=6, microseconds=1)

    monkeypatch.setattr(a.gov_store, "create_exact", slow)
    before = a.a.f.database.read_bytes()
    calls = a.a.b3_transport.calls.count("B3_CAS")
    with pytest.raises(op.sql.AuthorityError):
        a.attempt.complete_open(raw_request=a.commit)
    assert a.a.f.database.read_bytes() == before and a.a.b3_transport.calls.count("B3_CAS") == calls
    artifacts = list(Path(a.gov_store.root).iterdir())
    assert len(artifacts) == 1 and op.governance.parse_governance_certificate_bytes(
        artifacts[0].read_bytes()
    )


@pytest.fixture
def native_broker(native_open):
    import types

    x = native_open
    a = x.a
    provider = types.SimpleNamespace(
        authorization_reference=x.qualifier.registry.bindings["authorization"].reference
        if x.qualifier
        else next(i.reference for i in x.extra if i.name == "authorization")
    )
    broker = op.NativeBridgeBroker(
        journal=a.journal,
        session_qualifier=x.n.qualifier,
        signer=a.bridge,
        host_sources=provider,
        holder_admitter=lambda **kw: a.holder.public_key(),
    )
    return x, broker, provider


def test_native_broker_own_host_challenge_signature_and_native_bindings(native_broker, pair):
    x, broker, _ = native_broker
    host, executor = pair(budget=5)
    host.exchange_id = executor.exchange_id = wire.parse_request_bytes(x.raw).to_dict()[
        "exchange_id"
    ]
    challenge = broker._challenge(host, x.n.qualifier.qualify())
    received = executor.receive().to_dict()["body"]
    assert received == challenge.to_dict()
    assert received["container_identity_digest"] == x.n.options["process"].identity_digest
    assert received["runtime_profile_sha256"] == x.attempt.record["runtime_profile_sha256"]


def test_native_broker_mints_capability_for_independently_admitted_body(native_broker):
    x, broker, _ = native_broker
    a = x.a
    request = wire.parse_request_bytes(x.raw).to_dict()
    body = {
        k: v
        for k, v in request.items()
        if k not in ("capability", "sender_signature", "request_digest")
    }
    context = a.sources.request_context(x.raw, operation="reserve-start")
    approval = op.BridgeBodyApproval(op.sql.canonical_bytes(body), context, x.record_raw)
    minted = broker._capability(
        approval, native=x.n.qualifier.qualify(), holder=a.holder.public_key()
    )
    request["capability"] = minted.to_dict()
    raw = builder.signed_request(request, a.bridge, a.holder)
    authenticated = wire.verify_request(
        raw,
        expected_context=context,
        bridge_pin=a.bridge_pin,
        holder_public_key=a.holder.public_key(),
    )
    assert authenticated.capability.canonical == minted.canonical
    assert (
        minted.to_dict()["capability_id"]
        != wire.parse_request_bytes(x.raw).to_dict()["capability"]["capability_id"]
    )


def test_native_broker_actual_grant_permit_and_saved_reserve_response(native_broker):
    x, broker, provider = native_broker
    a = x.a
    seen = []
    provider.remember_permit = lambda **kw: seen.append(kw["permit"].canonical)
    request = wire.parse_request_bytes(x.raw).to_dict()
    body = {
        k: v
        for k, v in request.items()
        if k not in ("capability", "sender_signature", "request_digest")
    }
    preflight = next(i.reference for i in x.extra if i.name == "preflight")
    approval = op.BridgeBodyApproval(
        op.sql.canonical_bytes(body),
        a.sources.request_context(x.raw, operation="reserve-start"),
        x.record_raw,
        preflight,
    )
    permit, response = broker._reserve_response(approval, x.attempt, a.holder.public_key())
    assert seen == [permit.canonical]
    saved = a.journal.read()["responses"][request["request_id"]]
    assert saved == response
    assert a.transport.calls.count("RESERVE") == 1
    result = wire.parse_response_bytes(response).to_dict()
    assert result["authority_revision_before"] == result["authority_revision_after"] == 0
    assert result["result"]["open_permit"] == permit.to_dict()


def holder_request(body, capability, key):
    value = {
        **body,
        "capability": capability,
        "request_digest": op.hashlib.sha256(op.sql.canonical_bytes(body)).hexdigest(),
        "sender_signature": "0" * 128,
    }
    parsed = wire.parse_request_bytes(op.sql.canonical_bytes(value))
    value["sender_signature"] = key.sign(wire.signing_preimage(parsed)).hex()
    return wire.parse_request_bytes(op.sql.canonical_bytes(value))


def native_broker_exchange(rig, pair, *, replace_body=False, expire_after_register=False):
    """Synthetic fixed host providers; actual signatures, files, CAS and pipes.

    Provider policy admission is tested separately; this fixture isolates the
    broker driver and never invokes Docker, experimental load or official START.
    """
    import types
    from dataclasses import replace

    a = rig.a
    request = wire.parse_request_bytes(rig.raw).to_dict()
    body = {
        k: v
        for k, v in request.items()
        if k not in ("capability", "sender_signature", "request_digest")
    }
    context = a.sources.request_context(rig.raw, operation="reserve-start")
    pf = copy.deepcopy(rig.sample["payloads"]["preflight_reference"])
    pf.update(
        git_sha=request["source_revision"],
        expected_git_sha=request["source_revision"],
        checked_at=body["payload"]["run_identity"]["started_at"],
    )
    preflight = rig.store_file(
        "preflight", op.sql.canonical_bytes(pf), op.evidence.ARTIFACT_SCHEMAS["preflight_reference"]
    )
    host, executor = pair(budget=30)
    host.exchange_id = executor.exchange_id = request["exchange_id"]
    captured = {}
    peer_errors = []
    received = []

    def approve_reserve(**kw):
        assert kw["body"] == op.sql.canonical_bytes(body)
        proposed = copy.deepcopy(body)
        approved = context
        if replace_body:
            proposed["nonce"] = "9" * 64
            approved = replace(context, nonce=proposed["nonce"])
        return op.BridgeBodyApproval(
            op.sql.canonical_bytes(proposed), approved, rig.record_raw, preflight.reference
        )

    def remember_permit(**kw):
        captured.update(kw)

    def approve_commit(**kw):
        x = captured["prepared"]
        cv = wire.parse_request_bytes(x.commit).to_dict()
        cb = {
            k: v
            for k, v in cv.items()
            if k not in ("capability", "sender_signature", "request_digest")
        }
        assert kw["body"] == op.sql.canonical_bytes(cb)
        return op.BridgeBodyApproval(
            op.sql.canonical_bytes(cb), a.sources.request_context(x.commit, operation="commit-open")
        )

    def register_request(**kw):
        raw = kw["raw"]
        approval = kw["approval"]
        a.sources.approved[op.hashlib.sha256(raw).hexdigest()] = replace(
            approval.context, now=a.now()
        )
        if wire.parse_request_bytes(raw).to_dict()["operation"] == "commit-open":
            x = captured["prepared"]
            x.commit = raw
            item = rig.store_file(
                "commit_request", raw, wire.parse_request_bytes(raw).to_dict()["schema_version"]
            )
            x.extra[:] = [item if i.name == "commit_request" else i for i in x.extra]
            if expire_after_register:
                host.deadline = op.time.monotonic() - 1

    providers = types.SimpleNamespace(
        authorization_reference=rig.policy.reference,
        approve_reserve=approve_reserve,
        remember_permit=remember_permit,
        approve_commit=approve_commit,
        register_request=register_request,
    )
    broker = op.NativeBridgeBroker(
        journal=a.journal,
        session_qualifier=rig.n.qualifier,
        signer=a.bridge,
        host_sources=providers,
        holder_admitter=lambda **kw: a.holder.public_key(),
    )

    def send(kind, value):
        executor.send(
            op.sql.canonical_bytes(
                {
                    "schema_version": "s11-ex10-b4-frame-v1",
                    "exchange_id": executor.exchange_id,
                    "sequence": executor.position + 1,
                    "kind": kind,
                    "direction": "EXECUTOR_TO_HOST",
                    "body": value,
                }
            )
        )

    def peer():
        try:
            ch = wire.parse_host_challenge_bytes(
                op.sql.canonical_bytes(executor.receive().to_dict()["body"])
            )
            proposal = builder.fill(wire.schema_definitions()["open_proposal"])
            proposal.update(
                exchange_id=executor.exchange_id,
                host_challenge_digest=ch.sha256,
                holder_public_key_hex=a.holder.public_key().public_bytes_raw().hex(),
                reserve_request_body=body,
            )
            send(
                "PROPOSE_OPEN",
                op.sql.parse_canonical(
                    builder.signed(proposal, a.holder, wire.DOMAINS["open_proposal"])
                ),
            )
            cap = executor.receive().to_dict()["body"]
            if replace_body:
                captured["substituted_authorization"] = True
                executor.close()
                return
            send("RPC_REQUEST", holder_request(body, cap, a.holder).to_dict())
            granted = executor.receive().to_dict()["body"]
            received.append(5)
            assert granted["status"] == "OK"
            x = prepare_native_open(rig, captured["opening"], supplied_permit=captured["permit"])
            captured["prepared"] = x
            cv = wire.parse_request_bytes(x.commit).to_dict()
            cb = {
                k: v
                for k, v in cv.items()
                if k not in ("capability", "sender_signature", "request_digest")
            }
            evidence_frame = builder.fill(wire.schema_definitions()["open_evidence_frame"])
            evidence_frame.update(
                exchange_id=executor.exchange_id,
                open_permit_digest=granted["result"]["open_permit"]["permit_digest"],
                commit_request_body=cb,
            )
            send(
                "OPEN_EVIDENCE",
                op.sql.parse_canonical(
                    builder.signed(evidence_frame, a.holder, wire.DOMAINS["open_evidence_frame"])
                ),
            )
            commit_cap = executor.receive().to_dict()["body"]
            assert commit_cap["capability_id"] != cap["capability_id"]
            send("RPC_REQUEST", holder_request(cb, commit_cap, a.holder).to_dict())
            final = executor.receive().to_dict()["body"]
            received.append(9)
            assert final["status"] == "OK" and captured["opening"].writer.fd is None
            captured["final"] = final
            executor.finish()
        except Exception as exc:
            import traceback

            peer_errors.append(
                (
                    type(exc).__name__,
                    [(f.name, f.lineno) for f in traceback.extract_tb(exc.__traceback__)],
                )
            )
            executor.close()

    thread = threading.Thread(target=peer)
    thread.start()
    error = None
    try:
        captured["response"] = broker.serve_start(host)
    except Exception as exc:
        error = exc
        host.close()
    finally:
        thread.join(5)
    assert not thread.is_alive()
    return types.SimpleNamespace(
        rig=rig,
        broker=broker,
        host=host,
        executor=executor,
        captured=captured,
        error=error,
        peer_errors=peer_errors,
        received=received,
    )


def test_native_broker_nine_signed_frames_actual_two_cas_and_release_before_delivery(
    native_start_rig, pair
):
    x = native_broker_exchange(native_start_rig, pair)
    assert x.error is None, x.peer_errors
    assert x.peer_errors == [] and x.received == [5, 9] and x.host.closed and x.executor.closed
    a = x.rig.a
    assert a.transport.calls.count("RESERVE") == a.transport.calls.count("COMMIT_OPEN") == 1
    assert a.b3_transport.calls.count("B3_CAS") == 1
    state = a.journal.read()
    assert len(state["claims"]) == 2 and state["sequence"] == 2
    request = wire.parse_request_bytes(x.captured["prepared"].commit).to_dict()
    assert state["responses"][request["request_id"]] == x.captured["response"]
    qualifier = x.captured["prepared"].qualifier
    certificate = x.captured["prepared"].cert
    assert op.NativeGovernanceHistory(((certificate.value["certificate_id"], qualifier),)).contexts


def test_native_broker_approval_cannot_change_signed_proposal_body(native_start_rig, pair):
    x = native_broker_exchange(native_start_rig, pair, replace_body=True)
    assert not x.captured.get("substituted_authorization", False)
    assert x.rig.a.transport.calls.count("RESERVE") == 0
    assert x.rig.a.journal.read()["claims"] == {}


def test_native_broker_register_callback_cannot_dispatch_after_exchange_deadline(
    native_start_rig, pair
):
    x = native_broker_exchange(native_start_rig, pair, expire_after_register=True)
    assert type(x.error) is op.OperationalError and str(x.error) == "UNCERTAIN_PUBLICATION"
    assert x.rig.a.b3_transport.calls.count("B3_CAS") == 0
    assert x.rig.a.transport.calls.count("COMMIT_OPEN") == 0
    assert x.captured["opening"].writer.fd is None
    assert x.rig.a.journal.read()["active"] is not None


def test_native_broker_recovery_request_uses_separately_admitted_operator_key(native_open, pair):
    import types

    x = native_open
    a = x.a
    raw = cf.approved_cancel(a, x.attempt.record)
    context = a.sources.recovery_context(raw, operation="cancel-reservation-manual")
    key = op.Ed25519PublicKey.from_public_bytes(bytes.fromhex(context.holder_public_key_hex))
    authenticated = wire.verify_recovery_request(
        raw, expected_context=context, bridge_pin=a.bridge_pin, operator_public_key=key
    )
    request = authenticated.request.to_dict()
    body = {
        k: v
        for k, v in request.items()
        if k not in ("capability", "sender_signature", "request_digest")
    }
    approval = op.BridgeBodyApproval(op.sql.canonical_bytes(body), context)
    cap = wire._parse(
        "capability", op.sql.canonical_bytes(request["capability"]), maximum=wire.MAX_REQUEST_BYTES
    )
    broker = op.NativeBridgeBroker(
        journal=a.journal,
        session_qualifier=x.n.qualifier,
        signer=a.bridge,
        host_sources=types.SimpleNamespace(register_request=lambda **kw: None),
        holder_admitter=lambda **kw: key,
        role="RECOVERY_OPERATOR",
    )
    host, executor = pair("SINGLE_RPC", budget=5)
    host.exchange_id = executor.exchange_id = request["exchange_id"]
    for position in (1, 2, 3):
        value = frame("SINGLE_RPC", position).to_dict()
        value["exchange_id"] = request["exchange_id"]
        if "exchange_id" in value["body"]:
            value["body"]["exchange_id"] = request["exchange_id"]
        if position == 3:
            value["body"] = cap.to_dict()
        if position % 2:
            host.send(op.sql.canonical_bytes(value))
            executor.receive()
        else:
            executor.send(op.sql.canonical_bytes(value))
            host.receive()
    value = {
        "schema_version": "s11-ex10-b4-frame-v1",
        "exchange_id": request["exchange_id"],
        "sequence": 4,
        "kind": "RPC_REQUEST",
        "direction": "EXECUTOR_TO_HOST",
        "body": request,
    }
    executor.send(op.sql.canonical_bytes(value))
    assert broker._request(host, approval, cap, key).request.canonical == raw


def test_native_broker_last_live_artifact_read_cannot_cross_cas_deadline(
    native_open, pair, monkeypatch
):
    x = native_open
    a = x.a
    authenticated = a.journal.authenticate(x.commit, "commit-open")
    q = x.make_qualifier(x.attempt)
    prepared = x.publisher.prepare(
        qualifier=q, opening=x.attempt, authenticated_request=authenticated
    )
    host, _ = pair(budget=30)
    host.mark_dispatched()
    x.attempt._bridge_exchange_guard = host._check
    read = q.registry.read

    def delayed(name, **kw):
        raw = read(name, **kw)
        if name == "commit_request":
            host.deadline = op.time.monotonic() - 1
        return raw

    monkeypatch.setattr(q.registry, "read", delayed)
    transport = op._NativePublicationTransport(
        a.b3_transport, q, x.attempt, prepared.governance_certificate
    )
    error = None
    try:
        transport.publish_b3(
            {"operation_id": wire.parse_request_bytes(x.commit).to_dict()["request_id"]}, None
        )
    except Exception as exc:
        error = exc
    assert a.b3_transport.calls.count("B3_CAS") == 0
    assert type(error) is op.OperationalError and str(error) == "UNCERTAIN_PUBLICATION"
    assert transport.sent is False


def test_native_publisher_cannot_use_ledger_other_than_externally_approved_policy(native_open):
    x = native_open
    a = x.a
    other = x.n.home / "other-policy-ledger"
    other.mkdir(mode=0o700)
    database = other / "ledger.sqlite3"
    database.write_bytes(a.f.database.read_bytes())
    database.chmod(0o600)
    store = op.durable.PrivateStore(str(other), h.identity(other))
    x.n.stores[str(other)] = store
    publisher = op.NativeOpenPublisher(
        ledger_store=store,
        ledger_name="ledger.sqlite3",
        ledger_identity=h.identity(database),
        candidate_store=a.stores["candidate"],
        floor=a.b3_floor,
        transport=a.b3_transport,
        signer_pin=a.f.pin,
        clock=a.now,
        clock_health=lambda: True,
    )
    q = x.make_qualifier(x.attempt)
    authenticated = a.journal.authenticate(x.commit, "commit-open")
    with pytest.raises(op.OperationalError, match="HOST_POLICY_INVALID"):
        publisher.prepare(qualifier=q, opening=x.attempt, authenticated_request=authenticated)
    assert list(Path(q.store.root).iterdir()) == []
    assert a.b3_transport.calls.count("B3_CAS") == 0


@pytest.mark.parametrize("provider", ["approval_verifier", "envelope_verifier"])
def test_native_policy_provider_cannot_change_compose_after_native_session_qualification(
    native_open, provider
):
    x = native_open
    q = x.make_qualifier(x.attempt)
    policy = q.policy
    original = policy.approve if provider == "approval_verifier" else policy.envelope

    def changes(**kw):
        original(**kw)
        Path(x.n.value["compose_files"][0]["path"]).write_bytes(b"changed by policy provider\n")

    if provider == "approval_verifier":
        policy.approve = changes
    else:
        policy.envelope = changes
    with pytest.raises(op.OperationalError):
        x.publisher.prepare(
            qualifier=q,
            opening=x.attempt,
            authenticated_request=x.a.journal.authenticate(x.commit, "commit-open"),
        )
    assert list(Path(q.store.root).iterdir()) == []
    assert x.a.b3_transport.calls.count("B3_CAS") == 0


@pytest.fixture
def native_sources(native_start_rig, pair):
    import types

    rig = native_start_rig
    a = rig.a
    n = rig.n
    for name in ("candidate", "b3-floor", "native-artifacts", "native-certificates"):
        path = a.root / name
        path.mkdir(mode=0o700)
        a.stores[name] = op.durable.PrivateStore(str(path), h.identity(path))
    floor_store = a.stores["b3-floor"]
    floor_store.create_exact(
        floor_store.root + "/floor-00000000000000000000.json", op.sql.canonical_bytes(a.f.receipt)
    )
    floor = op.durable.DurableFloor(
        floor_store, [a.f.pin], op.sql.digest(a.f.receipt), a.f.receipt["genesis"]
    )
    publisher = op.NativeOpenPublisher(
        ledger_store=rig.host_policy.ledger,
        ledger_name="ledger.sqlite3",
        ledger_identity=h.identity(a.f.database),
        candidate_store=a.stores["candidate"],
        floor=floor,
        transport=h.Transport(a.remote, a.auth.credential),
        signer_pin=a.f.pin,
        clock=a.now,
        clock_health=lambda: True,
    )
    names = [rig.base_receipt, rig.policy, rig.envelope_binding]
    pf_ref = wire.parse_request_bytes(rig.raw).to_dict()["payload"]["preflight_reference"]
    names.append(
        op.HostArtifactBinding("preflight", op.sql.canonical_bytes(pf_ref), n.stores[str(n.home)])
    )
    original_intent_reader = a.f.trust()["intent_payload_reader"]

    def read_candidate(path):
        if path.startswith(a.stores["candidate"].root + "/"):
            return a.stores["candidate"].read(path)
        return original_intent_reader(path)

    a.sources.intent_reader = read_candidate
    reader = a.sources.read_authority
    source = op.NativeHostSources(
        session_qualifier=n.qualifier,
        registry=op.HostArtifactRegistry(tuple(names)),
        policy=rig.host_policy,
        authority_reader=reader,
        historical_contexts=lambda **kw: op.NativeGovernanceHistory(()),
        artifact_store=a.stores["native-artifacts"],
        certificate_store=a.stores["native-certificates"],
        publisher=publisher,
        executor_fingerprint=rig.sample["context"].executor_fingerprint,
        signer=a.bridge,
        bridge_pin=a.bridge_pin,
    )
    source.verify_registry(canonical_binding=a.journal.binding, journal=a.journal)
    a.journal.sources = source
    host, executor = pair(budget=30)
    request = wire.parse_request_bytes(rig.raw).to_dict()
    host.exchange_id = executor.exchange_id = request["exchange_id"]
    source.begin_exchange(channel=host)
    body = {
        k: v
        for k, v in request.items()
        if k not in ("capability", "sender_signature", "request_digest")
    }
    return types.SimpleNamespace(rig=rig, source=source, body=body, host=host, executor=executor)


def test_native_sources_approve_exact_refs_and_global_state_before_capability(native_sources):
    x = native_sources
    a = x.rig.a
    before = a.journal.read()
    snapshot = x.source.read_authority()
    approved = x.source.approve_reserve(
        body=op.sql.canonical_bytes(x.body),
        native=x.rig.n.qualifier.qualify(),
        holder=a.holder.public_key(),
        challenge=None,
    )
    record = wire.qualify_reservation(approved.reservation_record).to_dict()
    assert record["request_digest"] == op.hashlib.sha256(approved.canonical_body).hexdigest()
    assert record["coordination_policy_reference"] == x.rig.host_policy.reference
    assert record["host_root_binding_sha256"] == x.rig.host_policy.root_binding
    assert record["fencing_token"] == 1 and approved.context.authority_revision == 0
    assert op.history.current_revision(snapshot)["revision"] == 0
    assert a.journal.read() == before and a.transport.calls.count("RESERVE") == 0


@pytest.mark.parametrize(
    "field",
    [
        "deployment_verification_reference",
        "descriptor_reference",
        "operational_envelope_reference",
        "preflight_reference",
        "profile_reference",
        "session_reference",
    ],
)
def test_native_sources_never_mint_for_substituted_reserve_reference(native_sources, field):
    x = native_sources
    a = x.rig.a
    body = copy.deepcopy(x.body)
    body["payload"][field]["sha256"] = "f" * 64
    with pytest.raises(op.OperationalError, match="REQUEST_CONTEXT_INVALID"):
        x.source.approve_reserve(
            body=op.sql.canonical_bytes(body),
            native=x.rig.n.qualifier.qualify(),
            holder=a.holder.public_key(),
            challenge=None,
        )
    assert (
        x.source.approvals == {}
        and a.journal.read()["claims"] == {}
        and a.transport.calls.count("RESERVE") == 0
    )


@pytest.mark.parametrize(
    "field",
    [
        "namespace",
        "authority_id",
        "authority_epoch",
        "descriptor_reference",
        "approved_host_identity_reference",
        "coordinator_registry_reference",
        "lock_file_identity",
        "sidecar_identity",
        "ledger_identity",
        "bridge_key_public_approval_reference",
        "independent_witness_transport_reference",
        "writer_closure_reference",
        "approved_executor_entrypoint_identity",
        "normal_operation_allowlist",
        "recovery_operator_approval_policy",
    ],
)
def test_native_policy_all15_fields_compare_to_fixed_actual_host_resources(native_start_rig, field):
    x = native_start_rig
    policy = x.host_policy
    value = op.sql.parse_canonical(policy.registry.read("policy"))
    if type(value[field]) is dict:
        key = next(iter(value[field]))
        old = value[field][key]
        value[field][key] = old + 1 if type(old) is int else "substituted"
    elif type(value[field]) is list:
        value[field] = value[field][1:]
    elif type(value[field]) is int:
        value[field] += 1
    else:
        value[field] = "substituted"
    binding = x.store_file(
        "policy", op.sql.canonical_bytes(value), policy.reference["schema_version"]
    )
    bindings = tuple(binding if k == "policy" else v for k, v in policy.registry.bindings.items())
    options = dict(x.policy_options, registry=op.HostArtifactRegistry(bindings))
    with pytest.raises(op.OperationalError, match="HOST_POLICY_INVALID"):
        op.NativeHostPolicy(**options).qualify(native=x.n.qualifier.qualify(), journal=x.a.journal)


@pytest.mark.parametrize("failure", ["reject", "exception", "same-exception"])
def test_native_policy_external_signed_approval_missing_or_failed_is_redacted(
    native_start_rig, failure
):
    x = native_start_rig

    def provider(**kw):
        if failure == "reject":
            return False
        if failure == "same-exception":
            raise op.OperationalError("private host signature token/path")
        raise RuntimeError("private host signature token/path")

    policy = op.NativeHostPolicy(**dict(x.policy_options, approval_verifier=provider))
    with pytest.raises(op.OperationalError, match="^HOST_APPROVAL_REQUIRED$"):
        policy.qualify(native=x.n.qualifier.qualify(), journal=x.a.journal)


@pytest.mark.parametrize(
    "change",
    ["source", "protocol", "methodology", "side-effect", "stale", "future", "checks", "duplicate"],
)
def test_native_sources_preflight_actual_content_and_freshness_before_grant(native_sources, change):
    x = native_sources
    s = x.source
    value = op.sql.parse_canonical(s.registry.read("preflight"))
    if change == "source":
        value["git_sha"] = "f" * 40
    elif change == "protocol":
        value["protocol_version"] = "substituted"
    elif change == "methodology":
        value["methodology_version"] = "substituted"
    elif change == "side-effect":
        value["report_posts"] = 1
    elif change in ("stale", "future"):
        from datetime import timedelta

        value["checked_at"] = cf.coord._utc(
            x.rig.a.now() + timedelta(seconds=-901 if change == "stale" else 1)
        )
    elif change == "checks":
        value["checks"] = [value["checks"][0]]
    else:
        value["checks"].append(copy.deepcopy(value["checks"][0]))
    binding = x.rig.store_file(
        "preflight",
        op.sql.canonical_bytes(value),
        s.registry.reference("preflight")["schema_version"],
    )
    s.registry = op.HostArtifactRegistry(
        tuple(binding if k == "preflight" else v for k, v in s.registry.bindings.items())
    )
    body = copy.deepcopy(x.body)
    body["payload"]["preflight_reference"] = op.sql.parse_canonical(binding.reference)
    with pytest.raises(op.OperationalError, match="PREFLIGHT_INVALID"):
        s.approve_reserve(
            body=op.sql.canonical_bytes(body),
            native=x.rig.n.qualifier.qualify(),
            holder=x.rig.a.holder.public_key(),
            challenge=None,
        )
    assert s.approvals == {} and x.rig.a.transport.calls.count("RESERVE") == 0


def test_native_sources_b5_qualifier_is_fixed_and_cannot_return_unqualified_true(native_sources):
    x = native_sources
    x.rig.host_policy.envelope = lambda **kw: True
    with pytest.raises(op.OperationalError, match="ENVELOPE_INVALID"):
        x.source.approve_reserve(
            body=op.sql.canonical_bytes(x.body),
            native=x.rig.n.qualifier.qualify(),
            holder=x.rig.a.holder.public_key(),
            challenge=None,
        )
    assert x.source.approvals == {} and x.rig.a.transport.calls.count("RESERVE") == 0


def native_executor_artifacts(rig, *, response):
    """Test-only existing four artifacts produced by the private executor peer."""
    record = response["result"]["reservation"]
    permit = response["result"]["open_permit"]
    identity = record["run_identity"]
    payloads = copy.deepcopy(rig.sample["payloads"])
    manifest, start, pf = (payloads[k] for k in op.evidence.REFERENCE_FIELDS[:3])
    manifest.update(
        run_id=identity["run_id"], git_sha=identity["git_sha"], started_at=identity["started_at"]
    )
    manifest["configuration"]["git_sha"] = identity["git_sha"]
    pf.update(
        git_sha=identity["git_sha"],
        expected_git_sha=identity["git_sha"],
        checked_at=identity["started_at"],
    )
    start.update(
        run_id=identity["run_id"], git_sha=identity["git_sha"], recorded_at=identity["started_at"]
    )
    start["preflight"].update(sha256=op.sql.digest(pf), checked_at=identity["started_at"])
    root = Path(rig.n.value["host_roots"]["evidence"]["path"])
    directory = root / "runs" / identity["run_id"] / "metadata" / "EX-10"
    directory.mkdir(mode=0o700, parents=True)
    for path in (root / "runs", root / "runs" / identity["run_id"], directory.parent, directory):
        path.chmod(0o700)
    refs = {}
    for field, filename in zip(
        op.evidence.REFERENCE_FIELDS,
        ("run-manifest.initial.json", "ex10-start.json", "ex10-preflight.json", "ex10-plan.json"),
        strict=True,
    ):
        raw = op.sql.canonical_bytes(payloads[field])
        path = directory / filename
        path.write_bytes(raw)
        path.chmod(0o600)
        refs[field] = op._artifact_reference(str(path), raw, op.evidence.ARTIFACT_SCHEMAS[field])
    value = {
        **rig.sample["receipt"],
        **refs,
        "reserve_request_id": record["request_id"],
        "reservation_id": record["reservation_id"],
        "fencing_token": record["fencing_token"],
        "open_permit_digest": permit["permit_digest"],
        "run_id": identity["run_id"],
        "identity_digest": identity["identity_digest"],
        "runtime_profile_sha256": record["runtime_profile_sha256"],
        "session_header_sha256": record["session_header_sha256"],
        "opened_at": cf.coord._utc(rig.a.now()),
    }
    return op.evidence.parse_open_evidence_bytes(cf.ebuilder.sign(value, rig.a.holder)).to_dict()


def test_native_sources_full_signed_pipeline_no_partial_host_qualifiers(native_sources):
    import traceback
    import uuid

    x = native_sources
    a = x.rig.a
    s = x.source
    host, executor = x.host, x.executor
    captured = {}
    peer_errors = []
    host_error = None
    broker = op.NativeBridgeBroker(
        journal=a.journal,
        session_qualifier=x.rig.n.qualifier,
        signer=a.bridge,
        host_sources=s,
        holder_admitter=lambda **kw: a.holder.public_key(),
    )

    def send(kind, value):
        executor.send(
            op.sql.canonical_bytes(
                {
                    "schema_version": "s11-ex10-b4-frame-v1",
                    "exchange_id": executor.exchange_id,
                    "sequence": executor.position + 1,
                    "kind": kind,
                    "direction": "EXECUTOR_TO_HOST",
                    "body": value,
                }
            )
        )

    def peer():
        try:
            challenge = wire.parse_host_challenge_bytes(
                op.sql.canonical_bytes(executor.receive().to_dict()["body"])
            )
            proposal = builder.fill(wire.schema_definitions()["open_proposal"])
            proposal.update(
                exchange_id=executor.exchange_id,
                host_challenge_digest=challenge.sha256,
                holder_public_key_hex=a.holder.public_key().public_bytes_raw().hex(),
                reserve_request_body=x.body,
            )
            send(
                "PROPOSE_OPEN",
                op.sql.parse_canonical(
                    builder.signed(proposal, a.holder, wire.DOMAINS["open_proposal"])
                ),
            )
            cap = executor.receive().to_dict()["body"]
            send("RPC_REQUEST", holder_request(x.body, cap, a.holder).to_dict())
            response = executor.receive().to_dict()["body"]
            captured["reserve"] = response
            evd = native_executor_artifacts(x.rig, response=response)
            record = response["result"]["reservation"]
            request = builder.fill(
                next(
                    v
                    for v in wire.request_schemas()
                    if v["properties"]["operation"]["const"] == "commit-open"
                )
            )
            for field in (
                "exchange_id",
                "session_id",
                "session_header_sha256",
                "runtime_profile_sha256",
                "authority_id",
                "authority_epoch",
                "authority_revision",
                "source_revision",
                "project",
                "authorization_reference",
            ):
                request[field] = copy.deepcopy(x.body[field])
            request.update(
                request_id=str(uuid.uuid4()),
                nonce="e" * 64,
                issued_at=cf.coord._utc(a.now()),
                expires_at=record["freshness_deadline"],
                payload={
                    **{
                        k: record[k]
                        for k in (
                            "reservation_id",
                            "reservation_digest",
                            "fencing_token",
                            "run_identity",
                            "reservation_receipt_reference",
                        )
                    },
                    "reserve_request_id": record["request_id"],
                    "open_permit_digest": response["result"]["open_permit"]["permit_digest"],
                    "expected_head": x.body["payload"]["expected_head"],
                    "open_evidence": evd,
                },
            )
            body = {
                k: v
                for k, v in request.items()
                if k not in ("capability", "sender_signature", "request_digest")
            }
            frame = builder.fill(wire.schema_definitions()["open_evidence_frame"])
            frame.update(
                exchange_id=executor.exchange_id,
                open_permit_digest=response["result"]["open_permit"]["permit_digest"],
                commit_request_body=body,
            )
            send(
                "OPEN_EVIDENCE",
                op.sql.parse_canonical(
                    builder.signed(frame, a.holder, wire.DOMAINS["open_evidence_frame"])
                ),
            )
            second = executor.receive().to_dict()["body"]
            assert second["capability_id"] != cap["capability_id"]
            signed = holder_request(body, second, a.holder)
            captured["commit"] = signed
            send("RPC_REQUEST", signed.to_dict())
            final = executor.receive().to_dict()["body"]
            captured["final"] = final
            assert (
                final["status"] == "OK"
                and s.grants[record["reservation_id"]]["opening"].writer.fd is None
            )
            executor.finish()
        except Exception as exc:
            peer_errors.append(
                (
                    type(exc).__name__,
                    [(f.name, f.lineno) for f in traceback.extract_tb(exc.__traceback__)],
                )
            )
            executor.close()

    thread = threading.Thread(target=peer)
    thread.start()
    try:
        captured["response"] = broker.serve_start(host)
    except Exception as exc:
        host_error = (
            type(exc).__name__,
            [(f.name, f.lineno) for f in traceback.extract_tb(exc.__traceback__)],
        )
        host.close()
    finally:
        thread.join(5)
    assert not thread.is_alive() and host_error is None, (host_error, peer_errors)
    assert peer_errors == [] and host.closed and executor.closed
    assert a.transport.calls.count("RESERVE") == a.transport.calls.count("COMMIT_OPEN") == 1
    assert s.publisher.transport.calls.count("B3_CAS") == 1
    state = a.journal.read()
    assert state["sequence"] == 2 and len(state["claims"]) == 2
    assert state["responses"][captured["commit"].to_dict()["request_id"]] == captured["response"]
    assert len(s.catalog) == 1 and captured["final"]["result"]["opened"] is True
    snapshot = s.read_authority()
    assert op.history.current_revision(snapshot)["revision"] == 1
    assert (
        len(
            op.NativeGovernanceHistory(tuple(s.catalog.items())).qualify(
                snapshot=snapshot, journal=a.journal
            )
        )
        == 1
    )


def native_single_body(x, operation):
    request = builder.fill(
        next(
            v for v in wire.request_schemas() if v["properties"]["operation"]["const"] == operation
        )
    )
    for field in (
        "exchange_id",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "authorization_reference",
        "issued_at",
        "expires_at",
    ):
        request[field] = copy.deepcopy(x.body[field])
    from datetime import timedelta

    now = x.rig.a.journal._now()
    request.update(
        issued_at=cf.coord._utc(now), expires_at=cf.coord._utc(now + timedelta(seconds=120))
    )
    payload = request["payload"]
    if operation == "inspect-history":
        payload.update(
            expected_head=x.body["payload"]["expected_head"], limit=1000, cursor_after_run_id=None
        )
    elif operation == "verify-history-authority":
        payload.update(
            **{
                k: x.body["payload"][k]
                for k in (
                    "expected_head",
                    "deployment_verification_reference",
                    "descriptor_reference",
                )
            }
        )
    elif operation == "inspect-coordination":
        payload.update(
            expected_authority_id=request["authority_id"],
            expected_authority_epoch=request["authority_epoch"],
        )
    elif operation == "inspect-reservation":
        payload.update(original_request_id=request["request_id"], reservation_id=builder.UUID)
    return {
        k: v
        for k, v in request.items()
        if k not in ("capability", "sender_signature", "request_digest")
    }


@pytest.mark.parametrize(
    "operation",
    ["inspect-history", "verify-history-authority", "inspect-coordination", "inspect-reservation"],
)
def test_native_single_five_signed_frames_all_read_operations_leave_durable_state_unchanged(
    native_sources, pair, operation
):
    import traceback

    x = native_sources
    a = x.rig.a
    s = x.source
    known = None
    if operation == "inspect-reservation":
        previous, approved, reserve = native_grant_request(x)
        with a.journal.opening(
            raw_request=reserve.canonical, raw_record=approved.reservation_record
        ) as opening:
            previous._reserve_response(approved, opening, a.holder.public_key())
            known = copy.deepcopy(opening.record)
    x.host.close()
    x.executor.close()
    s.channel = None
    host, executor = pair("SINGLE_RPC", budget=30)
    host.exchange_id = executor.exchange_id = x.body["exchange_id"]
    body = native_single_body(x, operation)
    if known is not None:
        body["payload"].update(
            original_request_id=known["request_id"], reservation_id=known["reservation_id"]
        )
    before = a.journal.read()
    db_before = a.f.database.read_bytes()
    reserve_count = a.transport.calls.count("RESERVE")
    artifact_before = list(Path(s.artifacts.root).iterdir())
    gov_before = list(Path(s.certificates.root).iterdir())
    captured = {}
    errors = []
    host_error = None
    broker = op.NativeBridgeBroker(
        journal=a.journal,
        session_qualifier=x.rig.n.qualifier,
        signer=a.bridge,
        host_sources=s,
        holder_admitter=lambda **kw: a.holder.public_key(),
    )
    source_errors = []
    for name in ("approve_single", "register_request", "dispatch_single"):
        original = getattr(s, name)

        def diagnostic(_call=original, **kw):
            try:
                return _call(**kw)
            except Exception as exc:
                source_errors.append(
                    (
                        type(exc).__name__,
                        [(f.name, f.lineno) for f in traceback.extract_tb(exc.__traceback__)],
                    )
                )
                raise

        setattr(s, name, diagnostic)

    def send(kind, value):
        executor.send(
            op.sql.canonical_bytes(
                {
                    "schema_version": "s11-ex10-b4-frame-v1",
                    "exchange_id": executor.exchange_id,
                    "sequence": executor.position + 1,
                    "kind": kind,
                    "direction": "EXECUTOR_TO_HOST",
                    "body": value,
                }
            )
        )

    def peer():
        try:
            challenge = wire.parse_host_challenge_bytes(
                op.sql.canonical_bytes(executor.receive().to_dict()["body"])
            )
            proposal = builder.fill(wire.schema_definitions()["single_rpc_proposal"])
            proposal.update(
                exchange_id=executor.exchange_id,
                host_challenge_digest=challenge.sha256,
                holder_public_key_hex=a.holder.public_key().public_bytes_raw().hex(),
                request_body=body,
            )
            send(
                "RPC_PROPOSAL",
                op.sql.parse_canonical(
                    builder.signed(proposal, a.holder, wire.DOMAINS["single_rpc_proposal"])
                ),
            )
            cap = executor.receive().to_dict()["body"]
            request = holder_request(body, cap, a.holder)
            captured["request"] = request
            send("RPC_REQUEST", request.to_dict())
            response = executor.receive().to_dict()["body"]
            captured["response"] = response
            assert response["status"] == "OK" and response["operation"] == operation
            executor.finish()
        except Exception as exc:
            errors.append(
                (
                    type(exc).__name__,
                    [(f.name, f.lineno) for f in traceback.extract_tb(exc.__traceback__)],
                )
            )
            executor.close()

    thread = threading.Thread(target=peer)
    thread.start()
    try:
        broker.serve_single(host)
    except Exception as exc:
        host_error = (
            type(exc).__name__,
            [(f.name, f.lineno) for f in traceback.extract_tb(exc.__traceback__)],
        )
        host.close()
    finally:
        thread.join(5)
    assert not thread.is_alive() and host_error is None and errors == [], (
        host_error,
        errors,
        source_errors,
    )
    assert host.closed and executor.closed and not host.dispatched
    assert a.journal.read() == before and a.f.database.read_bytes() == db_before
    assert (
        list(Path(s.artifacts.root).iterdir()) == artifact_before
        and list(Path(s.certificates.root).iterdir()) == gov_before
    )
    assert (
        a.transport.calls.count("RESERVE") == reserve_count
        and a.transport.calls.count("COMMIT_OPEN") == 0
    )
    assert s.publisher.transport.calls.count("B3_CAS") == 0
    if operation == "inspect-history":
        assert (
            captured["response"]["result"]["complete"]
            and captured["response"]["result"]["first_valid_status"] == "NONE"
        )
        assert len(captured["response"]["result"]["nd_ack_targets"]) == 1
    if operation == "inspect-coordination":
        assert captured["response"]["result"]["lock_state"] == "NOT_PROBED"
    if operation == "inspect-reservation":
        reference = captured["response"]["result"]["original_response_reference"]
        assert reference is not None
        saved = before["responses"][known["request_id"]]
        assert reference["sha256"] == op.hashlib.sha256(saved).hexdigest()
        assert s.artifacts.read(reference["path"]) == saved


def test_native_normal_uncertainty_without_live_owned_writer_is_denied_no_auto_owner_loss(
    native_sources,
):
    x = native_sources
    body = native_single_body(x, "record-uncertain")
    with pytest.raises(op.OperationalError, match="OWNER_LEASE_REQUIRED"):
        x.source.approve_single(
            body=op.sql.canonical_bytes(body),
            native=x.rig.n.qualifier.qualify(),
            holder=x.rig.a.holder.public_key(),
            role="SESSION_EXECUTOR",
        )
    assert x.rig.a.journal.read()["claims"] == {} and x.source.approvals == {}


@pytest.mark.parametrize(
    "operation",
    [
        "resolve-uncertain-as-committed",
        "resolve-uncertain-as-not-committed",
        "cancel-reservation-manual",
    ],
)
def test_native_admin_three_never_authorized_with_normal_role(native_sources, operation):
    x = native_sources
    body = native_single_body(x, operation)
    with pytest.raises(op.OperationalError, match="ROLE_OPERATION_DENIED"):
        x.source.approve_single(
            body=op.sql.canonical_bytes(body),
            native=x.rig.n.qualifier.qualify(),
            holder=x.rig.a.holder.public_key(),
            role="SESSION_EXECUTOR",
        )
    assert x.source.approvals == {} and x.rig.a.journal.read()["claims"] == {}


@pytest.mark.parametrize(
    "operation",
    [
        "resolve-uncertain-as-committed",
        "resolve-uncertain-as-not-committed",
        "cancel-reservation-manual",
    ],
)
def test_native_admin_three_require_separate_operator_approval_provider(native_sources, operation):
    x = native_sources
    body = native_single_body(x, operation)
    with pytest.raises(op.OperationalError, match="RECOVERY_APPROVAL_REQUIRED"):
        x.source.approve_single(
            body=op.sql.canonical_bytes(body),
            native=x.rig.n.qualifier.qualify(),
            holder=x.rig.a.holder.public_key(),
            role="RECOVERY_OPERATOR",
        )
    assert x.source.approvals == {} and x.rig.a.journal.read()["claims"] == {}


def native_grant_request(x):
    a = x.rig.a
    s = x.source
    native = x.rig.n.qualifier.qualify()
    broker = op.NativeBridgeBroker(
        journal=a.journal,
        session_qualifier=x.rig.n.qualifier,
        signer=a.bridge,
        host_sources=s,
        holder_admitter=lambda **kw: a.holder.public_key(),
    )
    approval = s.approve_reserve(
        body=op.sql.canonical_bytes(x.body),
        native=native,
        holder=a.holder.public_key(),
        challenge=None,
    )
    cap = broker._capability(approval, native=native, holder=a.holder.public_key())
    request = holder_request(x.body, cap.to_dict(), a.holder)
    s.register_request(raw=request.canonical, approval=approval)
    return broker, approval, request


def native_owner_uncertainty(x, broker, scope):
    opening = scope.opening
    a = x.rig.a
    s = x.source
    s.owner = lambda: scope
    s.uncertainty = lambda **kw: None  # Existing externally approved synthetic observation.
    body = native_single_body(x, "record-uncertain")
    body["payload"].update(
        reservation_id=opening.record["reservation_id"],
        fencing_token=opening.record["fencing_token"],
        reason_code="EVIDENCE_RESULT_UNKNOWN",
    )
    if opening.record["open_witness_reference"] is not None:
        body["authority_revision"] = opening.record["open_witness_reference"]["revision"]
    native = x.rig.n.qualifier.qualify()
    approval = s.approve_single(
        body=op.sql.canonical_bytes(body),
        native=native,
        holder=a.holder.public_key(),
        role="SESSION_EXECUTOR",
    )
    cap = broker._capability(approval, native=native, holder=a.holder.public_key())
    request = holder_request(body, cap.to_dict(), a.holder)
    s.register_request(raw=request.canonical, approval=approval)
    authenticated = a.journal.authenticate(request.canonical, "record-uncertain")
    result = s.dispatch_single(authenticated_request=authenticated, role="SESSION_EXECUTOR")
    return result


def test_native_normal_uncertainty_uses_actual_owned_live_lease_distinct_once_transition(
    native_sources,
):
    x = native_sources
    a = x.rig.a
    s = x.source
    broker, approval, request = native_grant_request(x)
    with op.NativeOpeningScope(
        journal=a.journal, raw_request=request.canonical, raw_record=approval.reservation_record
    ) as scope:
        opening = scope.opening
        broker._reserve_response(approval, opening, a.holder.public_key())
        result = native_owner_uncertainty(x, broker, scope)
        record = op.sql.parse_canonical(result.result)["reservation"]
        assert record["state"] == "UNCERTAIN" and opening.writer.fd is None and scope.closed
        assert (
            a.floor.current()["sequence"] == 2
            and a.journal.read()["active"] == record["reservation_id"]
        )
    assert opening.writer.fd is None
    assert a.transport.calls.count("RESERVE") == a.transport.calls.count("RECORD_UNCERTAIN") == 1
    assert (
        a.transport.calls.count("COMMIT_OPEN") == s.publisher.transport.calls.count("B3_CAS") == 0
    )
    assert len(a.journal.read()["claims"]) == 2 and list(Path(s.certificates.root).iterdir()) == []


def test_native_normal_uncertainty_cannot_reuse_released_lease_or_certify_dead_owner(
    native_sources,
):
    x = native_sources
    a = x.rig.a
    s = x.source
    broker, approval, request = native_grant_request(x)
    with a.journal.opening(
        raw_request=request.canonical, raw_record=approval.reservation_record
    ) as opening:
        pass
    s.owner = lambda: opening
    s.uncertainty = lambda **kw: None
    body = native_single_body(x, "record-uncertain")
    body["payload"].update(
        reservation_id=opening.record["reservation_id"],
        fencing_token=opening.record["fencing_token"],
    )
    with pytest.raises((op.OperationalError, op.sql.AuthorityError)):
        s.approve_single(
            body=op.sql.canonical_bytes(body),
            native=x.rig.n.qualifier.qualify(),
            holder=a.holder.public_key(),
            role="SESSION_EXECUTOR",
        )
    assert (
        a.transport.calls.count("RECORD_UNCERTAIN") == 0
        and a.journal.read()["active"] == opening.record["reservation_id"]
    )


def test_native_inspect_reservation_unknown_pair_never_returns_fake_ok(native_sources):
    x = native_sources
    body = native_single_body(x, "inspect-reservation")
    with pytest.raises(op.OperationalError, match="RESERVATION_CONTEXT_INVALID"):
        x.source.approve_single(
            body=op.sql.canonical_bytes(body),
            native=x.rig.n.qualifier.qualify(),
            holder=x.rig.a.holder.public_key(),
            role="SESSION_EXECUTOR",
        )
    assert x.source.approvals == {} and x.rig.a.journal.read()["claims"] == {}


def test_native_preflight_freshness_uses_clock_after_envelope_provider(native_sources):
    from datetime import timedelta

    x = native_sources
    s = x.source
    a = x.rig.a
    initial = a.now()
    current = [initial]
    a.journal.clock = lambda: current[0]
    value = op.sql.parse_canonical(s.registry.read("preflight"))
    value["checked_at"] = cf.coord._utc(initial - timedelta(seconds=899))
    binding = x.rig.store_file(
        "preflight",
        op.sql.canonical_bytes(value),
        s.registry.reference("preflight")["schema_version"],
    )
    s.registry = op.HostArtifactRegistry(
        tuple(binding if k == "preflight" else v for k, v in s.registry.bindings.items())
    )
    body = copy.deepcopy(x.body)
    body["payload"]["preflight_reference"] = op.sql.parse_canonical(binding.reference)
    original = s.policy.envelope

    def advance(**kw):
        result = original(**kw)
        current[0] = initial + timedelta(seconds=2)
        return result

    s.policy.envelope = advance
    with pytest.raises(op.OperationalError, match="PREFLIGHT_INVALID"):
        s.approve_reserve(
            body=op.sql.canonical_bytes(body),
            native=x.rig.n.qualifier.qualify(),
            holder=a.holder.public_key(),
            challenge=None,
        )
    assert (
        s.approvals == {}
        and a.transport.calls.count("RESERVE") == 0
        and s.publisher.transport.calls.count("B3_CAS") == 0
    )


@pytest.mark.parametrize("change", ["policy", "pf-age"])
def test_native_open_one_rechecks_policy_and_pf_after_actual_reserve(native_sources, change):
    from datetime import timedelta

    x = native_sources
    s = x.source
    a = x.rig.a
    initial = a.now()
    if change == "pf-age":
        value = op.sql.parse_canonical(s.registry.read("preflight"))
        value["checked_at"] = cf.coord._utc(initial - timedelta(seconds=899))
        binding = x.rig.store_file(
            "preflight",
            op.sql.canonical_bytes(value),
            s.registry.reference("preflight")["schema_version"],
        )
        s.registry = op.HostArtifactRegistry(
            tuple(binding if k == "preflight" else v for k, v in s.registry.bindings.items())
        )
        x.body["payload"]["preflight_reference"] = op.sql.parse_canonical(binding.reference)
    broker, approval, request = native_grant_request(x)
    original = a.transport.reserve

    def changed_after_reserve(payload, **kw):
        result = original(payload, **kw)
        if change == "policy":
            Path(s.policy.reference["path"]).write_bytes(b"{}\n")
        else:
            a.journal.clock = lambda: initial + timedelta(seconds=2)
        return result

    a.transport.reserve = changed_after_reserve
    if change == "policy":
        raw_policy = Path(s.policy.reference["path"]).read_bytes()
        with pytest.raises(op.sql.AuthorityError, match="TRUST_UNAPPROVED"):
            with a.journal.opening(
                raw_request=request.canonical, raw_record=approval.reservation_record
            ):
                pytest.fail("Changed policy must be denied before OPEN_ONE")
        Path(s.policy.reference["path"]).write_bytes(raw_policy)
        state = a.journal._local_state()
        assert (
            state["active"] == op.sql.parse_canonical(approval.reservation_record)["reservation_id"]
        )
        assert (
            a.floor.current()["sequence"] == 0
            and state["responses"] == {}
            and len(state["claims"]) == 1
        )
    else:
        with a.journal.opening(
            raw_request=request.canonical, raw_record=approval.reservation_record
        ) as opening:
            with pytest.raises(op.OperationalError):
                broker._reserve_response(approval, opening, a.holder.public_key())
        state = a.journal.read()
        assert opening.writer.fd is None and state["active"] == opening.record["reservation_id"]
        assert state["latest"][opening.record["reservation_id"]]["state"] == "PENDING"
        assert state["responses"] == {} and len(state["claims"]) == 1
    assert (
        a.transport.calls.count("RESERVE") == 1 and s.publisher.transport.calls.count("B3_CAS") == 0
    )


def native_five_frame_peer(
    x, host, executor, body, broker, key, *, on_response=lambda response: None, expect_failure=False
):
    import traceback

    captured = {}
    errors = []
    host_error = None
    source_errors = []
    for name in ("approve_single", "register_request", "dispatch_single"):
        original = getattr(x.source, name)

        def diagnostic(_call=original, **kw):
            try:
                return _call(**kw)
            except Exception as exc:
                source_errors.append(
                    (
                        type(exc).__name__,
                        [(f.name, f.lineno) for f in traceback.extract_tb(exc.__traceback__)],
                    )
                )
                raise

        setattr(x.source, name, diagnostic)
    original_request = broker._request

    def request_diagnostic(*args, **kw):
        try:
            return original_request(*args, **kw)
        except Exception as exc:
            source_errors.append(
                (
                    type(exc).__name__,
                    [(f.name, f.lineno) for f in traceback.extract_tb(exc.__traceback__)],
                )
            )
            raise

    broker._request = request_diagnostic

    def send(kind, value):
        executor.send(
            op.sql.canonical_bytes(
                {
                    "schema_version": "s11-ex10-b4-frame-v1",
                    "exchange_id": executor.exchange_id,
                    "sequence": executor.position + 1,
                    "kind": kind,
                    "direction": "EXECUTOR_TO_HOST",
                    "body": value,
                }
            )
        )

    def peer():
        try:
            challenge = wire.parse_host_challenge_bytes(
                op.sql.canonical_bytes(executor.receive().to_dict()["body"])
            )
            proposal = builder.fill(wire.schema_definitions()["single_rpc_proposal"])
            proposal.update(
                exchange_id=executor.exchange_id,
                host_challenge_digest=challenge.sha256,
                holder_public_key_hex=key.public_key().public_bytes_raw().hex(),
                request_body=body,
            )
            send(
                "RPC_PROPOSAL",
                op.sql.parse_canonical(
                    builder.signed(proposal, key, wire.DOMAINS["single_rpc_proposal"])
                ),
            )
            cap = executor.receive().to_dict()["body"]
            request = holder_request(body, cap, key)
            captured.update(cap=cap, request=request)
            send("RPC_REQUEST", request.to_dict())
            response = executor.receive().to_dict()["body"]
            captured["response"] = response
            on_response(response)
            executor.finish()
        except Exception as exc:
            errors.append(
                (
                    type(exc).__name__,
                    [(f.name, f.lineno) for f in traceback.extract_tb(exc.__traceback__)],
                )
            )
            executor.close()

    thread = threading.Thread(target=peer)
    thread.start()
    try:
        captured["raw_response"] = broker.serve_single(host)
    except Exception as exc:
        host_error = (
            type(exc).__name__,
            [(f.name, f.lineno) for f in traceback.extract_tb(exc.__traceback__)],
        )
        host.close()
    finally:
        thread.join(5)
    if expect_failure:
        assert (
            not thread.is_alive() and host_error is not None and host_error[0] == "OperationalError"
        ), (host_error, errors, source_errors)
        assert all(error[0] == "OperationalError" for error in errors), (errors, source_errors)
    else:
        assert not thread.is_alive() and host_error is None and errors == [], (
            host_error,
            errors,
            source_errors,
        )
    assert host.closed and executor.closed
    return captured


def test_native_single_uncertainty_releases_actual_scope_before_fifth_frame(native_sources, pair):
    x = native_sources
    a = x.rig.a
    s = x.source
    previous, approval, request = native_grant_request(x)
    with op.NativeOpeningScope(
        journal=a.journal, raw_request=request.canonical, raw_record=approval.reservation_record
    ) as scope:
        opening = scope.opening
        previous._reserve_response(approval, opening, a.holder.public_key())
        s.owner = lambda: scope
        s.uncertainty = lambda **kw: None
        x.host.close()
        x.executor.close()
        s.channel = None
        host, executor = pair("SINGLE_RPC", budget=30)
        host.exchange_id = executor.exchange_id = x.body["exchange_id"]
        body = native_single_body(x, "record-uncertain")
        body["payload"].update(
            reservation_id=opening.record["reservation_id"],
            fencing_token=opening.record["fencing_token"],
            reason_code="EVIDENCE_RESULT_UNKNOWN",
        )
        broker = op.NativeBridgeBroker(
            journal=a.journal,
            session_qualifier=x.rig.n.qualifier,
            signer=a.bridge,
            host_sources=s,
            holder_admitter=lambda **kw: a.holder.public_key(),
        )

        def delivered(response):
            assert opening.writer.fd is None
            assert (
                response["result"]["global_start_blocked"]
                and response["result"]["reservation"]["state"] == "UNCERTAIN"
            )

        native_five_frame_peer(x, host, executor, body, broker, a.holder, on_response=delivered)
    assert (
        opening.writer.fd is None
        and len(a.journal.read()["claims"]) == 2
        and a.floor.current()["sequence"] == 2
    )
    assert a.transport.calls.count("RESERVE") == a.transport.calls.count("RECORD_UNCERTAIN") == 1
    assert (
        a.transport.calls.count("COMMIT_OPEN") == s.publisher.transport.calls.count("B3_CAS") == 0
    )


def test_native_owner_scope_enter_failure_releases_real_context_and_keeps_blocker(
    native_sources, monkeypatch
):
    x = native_sources
    a = x.rig.a
    _, approval, request = native_grant_request(x)
    scope = op.NativeOpeningScope(
        journal=a.journal, raw_request=request.canonical, raw_record=approval.reservation_record
    )

    def fail_after_enter(self):
        raise op.OperationalError("HOST_CONTEXT_INVALID")

    monkeypatch.setattr(op.NativeOpeningScope, "check", fail_after_enter)
    with pytest.raises(op.OperationalError, match="HOST_CONTEXT_INVALID"):
        scope.__enter__()
    assert scope.opening.writer.fd is None and scope.closed
    with op.durable.HeldWriter(
        a.journal.lock_store, a.journal.lock_name, a.journal.lock_identity
    ) as writer:
        writer.check()
    state = a.journal.read()
    assert len(state["claims"]) == 1 and state["active"] == scope.opening.record["reservation_id"]
    assert (
        a.transport.calls.count("RESERVE") == 1 and a.transport.calls.count("RECORD_UNCERTAIN") == 0
    )


def native_operator_context(x, body, holder, record):
    from dataclasses import fields

    native = x.rig.n.qualifier.qualify()
    common = x.source._context(
        body,
        native=native,
        holder=holder,
        revision=body["authority_revision"],
        run_id=record["run_id"],
        record=record,
    )
    return wire.RecoveryRequestContext(
        **{f.name: getattr(common, f.name) for f in fields(common)},
        operator_authorization_reference=op.sql.canonical_bytes(
            body["payload"]["operator_authorization_reference"]
        ),
    )


def native_admin_provider(
    x, body, operator, record, *, candidate=None, completion=None, prepared=None
):
    """Fixed externally approved disposable operator proofs; no real key/policy."""
    import types

    s = x.source
    a = x.rig.a
    raw = op.sql.canonical_bytes(body)
    payload = body["payload"]
    approved_operator = operator.public_key().public_bytes_raw()
    public_refs = [payload["operator_authorization_reference"], *payload["evidence_references"]]
    if "writer_closure_reference" in payload:
        public_refs.append(payload["writer_closure_reference"])
    if "no_open_certificate_reference" in payload:
        public_refs.append(payload["no_open_certificate_reference"])

    def check_proofs():
        for ref in public_refs:
            store = next(
                store
                for store in (x.rig.n.stores[str(x.rig.n.home)], s.artifacts)
                if str(Path(ref["path"]).parent) == store.root
            )
            assert op.hashlib.sha256(store.read(ref["path"])).hexdigest() == ref["sha256"]

    def approve_body(**kw):
        assert (
            kw["body"] == raw
            and kw["holder"].public_bytes_raw() == approved_operator
            and kw["journal"] is a.journal
        )
        check_proofs()
        assert kw["native"].profile.to_dict() == x.rig.n.value
        if candidate is not None:
            current = op.history.current_revision(candidate)
            assert payload["fresh_authority_head"]["witness_digest"] == current["witness_digest"]
            assert current["revision"] == record["expected_next_revision"]
            op.NativeGovernanceHistory(tuple(s.catalog.items())).qualify(
                snapshot=candidate, journal=a.journal
            )
        return op.BridgeBodyApproval(raw, native_operator_context(x, body, kw["holder"], record))

    def qualify_recovery(**kw):
        kw["writer"].check()
        actual = kw["authenticated_request"].request.to_dict()
        assert (
            op.sql.canonical_bytes(
                {
                    k: v
                    for k, v in actual.items()
                    if k not in ("capability", "sender_signature", "request_digest")
                }
            )
            == raw
        )
        check_proofs()
        snapshot = s.read_authority() if candidate is None else candidate
        if candidate is None:
            assert op.history.find_run(snapshot, record["run_id"]) is None
        approval = cf.service.RecoveryApproval(
            actual["request_id"],
            record["reservation_id"],
            kw["coordination_head"]["receipt_digest"],
            op.sql.canonical_bytes(payload["operator_authorization_reference"]),
            op.sql.canonical_bytes(
                payload.get("writer_closure_reference", payload["evidence_references"][-1])
            ),
            None
            if "no_open_certificate_reference" not in payload
            else op.sql.canonical_bytes(payload["no_open_certificate_reference"]),
        )
        return op.coordination.RecoveryQualification(
            approval,
            snapshot,
            authority_completion=completion,
            open_witness_reference=None
            if candidate is None
            else op.sql.canonical_bytes(payload["existing_open_witness_reference"]),
            evidence_reference=None if prepared is None else prepared.evidence_reference,
            open_evidence=None if prepared is None else prepared.open_evidence,
            authority_floor=None if candidate is None else s.publisher.floor,
        )

    def verify_manual_event(**kw):
        assert (
            kw["event"]["manual_resolution_reference"]
            == payload["operator_authorization_reference"]
        )
        assert (
            kw["event"]["reservation_digest"]
            == kw["original_record"]["reservation_digest"]
            == record["reservation_digest"]
        )

    def observe_authority(**kw):
        assert kw["canonical_body"] == raw and kw["journal"] is a.journal
        check_proofs()
        return op.NativeRecoveryAuthority(
            s.read_authority() if candidate is None else candidate, completion
        )

    return types.SimpleNamespace(
        approve_body=approve_body,
        qualify_recovery=qualify_recovery,
        verify_manual_event=verify_manual_event,
        observe_authority=observe_authority,
    )


def native_admin_body(x, operation, record):
    import uuid

    body = native_single_body(x, operation)
    payload = body["payload"]
    body["request_id"] = str(
        uuid.uuid5(uuid.NAMESPACE_URL, "native-admin-" + operation + record["reservation_id"])
    )
    body["nonce"] = op.sql.digest(
        {"admin-operation": operation, "reservation": record["reservation_id"]}
    )
    approval = x.rig.store_file("operator-proof", b"{}\n", "s11-ex10-synthetic-operator-proof-v1")
    closure = x.rig.store_file("operator-closure", b"{}\n", "s11-ex10-synthetic-writer-closure-v1")
    no_open = x.rig.store_file("operator-no-open", b"{}\n", "s11-ex10-synthetic-no-open-v1")
    payload.update(
        reservation_id=record["reservation_id"],
        expected_state="UNCERTAIN",
        expected_record_digest=record["record_digest"],
        expected_record_sequence=record["record_sequence"],
        fresh_coordination_head=x.rig.a.journal.fresh_head(x.rig.a.journal.read()),
        fresh_authority_head=x.body["payload"]["expected_head"],
        operator_authorization_reference=op.sql.parse_canonical(approval.reference),
        evidence_references=[op.sql.parse_canonical(closure.reference)],
        idempotency_token=op.sql.digest(
            {"operation": operation, "record": record["record_digest"]}
        ),
    )
    if operation != "resolve-uncertain-as-committed":
        payload.update(
            writer_closure_reference=op.sql.parse_canonical(closure.reference),
            no_open_certificate_reference=op.sql.parse_canonical(no_open.reference),
        )
    return body


@pytest.mark.parametrize(
    "operation", ["resolve-uncertain-as-not-committed", "cancel-reservation-manual"]
)
def test_native_admin_no_open_five_frames_actual_single_transition_no_new_start(
    native_sources, pair, operation
):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    x = native_sources
    a = x.rig.a
    s = x.source
    previous, approval, request = native_grant_request(x)
    with op.NativeOpeningScope(
        journal=a.journal, raw_request=request.canonical, raw_record=approval.reservation_record
    ) as scope:
        previous._reserve_response(approval, scope.opening, a.holder.public_key())
        native_owner_uncertainty(x, previous, scope)
        record = copy.deepcopy(scope.opening.record)
    x.host.close()
    x.executor.close()
    s.channel = None
    host, executor = pair("SINGLE_RPC", budget=30)
    host.exchange_id = executor.exchange_id = x.body["exchange_id"]
    s.begin_exchange(channel=host)
    body = native_admin_body(x, operation, record)
    operator = Ed25519PrivateKey.generate()
    s.recovery = native_admin_provider(x, body, operator, record)
    broker = op.NativeBridgeBroker(
        journal=a.journal,
        session_qualifier=x.rig.n.qualifier,
        signer=a.bridge,
        host_sources=s,
        holder_admitter=lambda **kw: operator.public_key(),
        role="RECOVERY_OPERATOR",
    )
    before = a.f.database.read_bytes()

    def delivered(response):
        assert response["result"]["reservation"]["state"] == "CANCELLED_MANUALLY"
        with op.durable.HeldWriter(
            a.journal.lock_store, a.journal.lock_name, a.journal.lock_identity
        ) as writer:
            writer.check()

    captured = native_five_frame_peer(
        x, host, executor, body, broker, operator, on_response=delivered
    )
    state = a.journal.read()
    assert (
        state["active"] is None and len(state["claims"]) == 3 and a.floor.current()["sequence"] == 3
    )
    assert (
        a.transport.calls.count("CANCEL_MANUAL") == 1
        and a.transport.calls.count("RECORD_UNCERTAIN") == 1
    )
    assert a.f.database.read_bytes() == before and s.publisher.transport.calls.count("B3_CAS") == 0
    assert (
        captured["cap"]["role"] == "RECOVERY_OPERATOR"
        and captured["response"]["authority_revision_before"]
        == captured["response"]["authority_revision_after"]
        == 0
    )
    if operation == "cancel-reservation-manual":
        assert captured["response"]["result"]["new_start_permitted_by_this_response"] is False
    else:
        assert captured["response"]["result"]["no_open_proven"] is True


def native_commit_request(x, broker, opening):
    import uuid

    s = x.source
    a = x.rig.a
    permit, raw = broker._reserve_response(
        s.requests[opening.request.request.sha256], opening, a.holder.public_key()
    )
    response = op.sql.parse_canonical(raw)
    evd = native_executor_artifacts(x.rig, response=response)
    record = opening.record
    sample = builder.fill(
        next(
            v
            for v in wire.request_schemas()
            if v["properties"]["operation"]["const"] == "commit-open"
        )
    )
    for field in (
        "exchange_id",
        "session_id",
        "session_header_sha256",
        "runtime_profile_sha256",
        "authority_id",
        "authority_epoch",
        "authority_revision",
        "source_revision",
        "project",
        "authorization_reference",
    ):
        sample[field] = copy.deepcopy(x.body[field])
    sample.update(
        request_id=str(uuid.uuid4()),
        nonce="e" * 64,
        issued_at=cf.coord._utc(a.now()),
        expires_at=record["freshness_deadline"],
        payload={
            **{
                k: record[k]
                for k in (
                    "reservation_id",
                    "reservation_digest",
                    "fencing_token",
                    "run_identity",
                    "reservation_receipt_reference",
                )
            },
            "reserve_request_id": record["request_id"],
            "open_permit_digest": permit.to_dict()["permit_digest"],
            "expected_head": x.body["payload"]["expected_head"],
            "open_evidence": evd,
        },
    )
    body = {
        k: v
        for k, v in sample.items()
        if k not in ("capability", "sender_signature", "request_digest")
    }
    native = x.rig.n.qualifier.qualify()
    approval = s.approve_commit(
        body=op.sql.canonical_bytes(body),
        native=native,
        holder=a.holder.public_key(),
        opening=opening,
        permit=permit,
    )
    cap = broker._capability(approval, native=native, holder=a.holder.public_key())
    request = holder_request(body, cap.to_dict(), a.holder)
    s.register_request(raw=request.canonical, approval=approval)
    return request


def native_existing_open_candidate(x):
    a = x.rig.a
    s = x.source
    previous, approval, request = native_grant_request(x)
    original = s.qualify_open
    prepared = []

    def keep_actual(**kw):
        result = original(**kw)
        prepared.append(result)
        return result

    s.qualify_open = keep_actual
    with op.NativeOpeningScope(
        journal=a.journal, raw_request=request.canonical, raw_record=approval.reservation_record
    ) as scope:
        commit = native_commit_request(x, previous, scope.opening)
        s.publisher.transport.fail = "response"
        with pytest.raises(op.sql.AuthorityError, match="UNCERTAIN_PUBLICATION"):
            scope.opening.complete_open(raw_request=commit.canonical)
        assert s.publisher.transport.calls.count("B3_CAS") == 1
        native_owner_uncertainty(x, previous, scope)
        record = copy.deepcopy(scope.opening.record)
    s.publisher.transport.fail = None
    a.b3_floor = s.publisher.floor
    a.prepared_open = prepared[0]
    a.stores["ledger"] = s.publisher.ledger
    verified, head, completion = cf.full_existing_candidate(a, commit.canonical)
    assert op.history.current_revision(verified)["revision"] == 1
    return record, verified, head, completion, prepared[0]


def test_native_admin_adopts_remote_ahead_candidate_five_frames_zero_new_open(native_sources, pair):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    x = native_sources
    a = x.rig.a
    s = x.source
    record, candidate, head, completion, prepared = native_existing_open_candidate(x)
    assert (
        len(op.sql._load_database(a.f.database)["history_revisions"]) == 1
        and s.publisher.floor.current().revision == 0
    )
    retained = Path(completion.candidate_path).read_bytes()
    x.host.close()
    x.executor.close()
    s.channel = None
    host, executor = pair("SINGLE_RPC", budget=30)
    host.exchange_id = executor.exchange_id = x.body["exchange_id"]
    s.begin_exchange(channel=host)
    body = native_admin_body(x, "resolve-uncertain-as-committed", record)
    body["payload"].update(
        fresh_authority_head=head,
        existing_open_witness_reference=head,
        run_identity=record["run_identity"],
        open_evidence=prepared.open_evidence.receipt.to_dict(),
    )
    body["payload"]["evidence_references"].insert(
        0, op.sql.parse_canonical(prepared.evidence_reference)
    )
    operator = Ed25519PrivateKey.generate()
    s.recovery = native_admin_provider(
        x, body, operator, record, candidate=candidate, completion=completion, prepared=prepared
    )
    broker = op.NativeBridgeBroker(
        journal=a.journal,
        session_qualifier=x.rig.n.qualifier,
        signer=a.bridge,
        host_sources=s,
        holder_admitter=lambda **kw: operator.public_key(),
        role="RECOVERY_OPERATOR",
    )

    def delivered(response):
        assert response["result"]["committed"] and response["result"]["new_open_executed"] is False
        assert (
            response["authority_revision_before"] == 0 and response["authority_revision_after"] == 1
        )
        with op.durable.HeldWriter(
            a.journal.lock_store, a.journal.lock_name, a.journal.lock_identity
        ) as writer:
            writer.check()

    captured = native_five_frame_peer(
        x, host, executor, body, broker, operator, on_response=delivered
    )
    state = a.journal.read()
    assert (
        state["latest"][record["reservation_id"]]["state"] == "COMMITTED"
        and state["active"] == record["reservation_id"]
    )
    assert (
        len(state["claims"]) == 4
        and s.publisher.floor.current().revision == 1
        and a.floor.current()["sequence"] == 3
    )
    assert (
        a.transport.calls.count("ADOPT_OPEN_MANUAL") == 1
        and a.transport.calls.count("COMMIT_OPEN") == 0
    )
    assert (
        s.publisher.transport.calls.count("B3_CAS") == 1
        and Path(completion.candidate_path).read_bytes() == retained
    )
    assert captured["response"]["witness_head"] == head


@pytest.mark.parametrize("known_open", [False, True])
def test_native_owner_uncertainty_after_snapshot_expiry_preserves_known_revision(
    native_sources, monkeypatch, known_open
):
    from datetime import timedelta

    x = native_sources
    a = x.rig.a
    s = x.source
    previous, approval, request = native_grant_request(x)
    with op.NativeOpeningScope(
        journal=a.journal, raw_request=request.canonical, raw_record=approval.reservation_record
    ) as scope:
        if known_open:
            commit = native_commit_request(x, previous, scope.opening)
            scope.opening.complete_open(raw_request=commit.canonical)
        else:
            previous._reserve_response(approval, scope.opening, a.holder.public_key())
        snapshot = scope.opening.open_result if known_open else scope.opening.snapshot
        expired = wire._time(snapshot.expires_at) + timedelta(seconds=1)
        a.journal.clock = lambda: expired
        a.remote.clock = lambda: expired
        monkeypatch.setattr(op.history, "_now", lambda: cf.coord._utc(expired))
        with pytest.raises(op.sql.AuthorityError, match="EXPIRED_OBSERVATION"):
            op.history.current_revision(snapshot)

        def unavailable():
            raise AssertionError("UNCERTAIN must not require a latest B3 snapshot")

        s.reader = unavailable
        result = native_owner_uncertainty(x, previous, scope)
        assert result.before == result.after == (1 if known_open else 0)
        assert scope.closed and scope.opening.writer.fd is None
        assert op.sql.parse_canonical(result.result)["reservation"]["state"] == "UNCERTAIN"
    assert a.journal.read()["active"] == scope.opening.record["reservation_id"]
    assert s.publisher.transport.calls.count("B3_CAS") == int(known_open)
    assert a.transport.calls.count("RECORD_UNCERTAIN") == 1 and a.transport.calls.count(
        "COMMIT_OPEN"
    ) == int(known_open)


@pytest.mark.parametrize("change", ["run-identity", "evidence"])
def test_native_recovery_candidate_target_mismatch_denied_before_capability(
    native_sources, pair, change
):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    x = native_sources
    a = x.rig.a
    s = x.source
    record, candidate, head, completion, prepared = native_existing_open_candidate(x)
    x.host.close()
    x.executor.close()
    s.channel = None
    host, executor = pair("SINGLE_RPC", budget=30)
    host.exchange_id = executor.exchange_id = x.body["exchange_id"]
    s.begin_exchange(channel=host)
    body = native_admin_body(x, "resolve-uncertain-as-committed", record)
    body["payload"].update(
        fresh_authority_head=head,
        existing_open_witness_reference=head,
        run_identity=copy.deepcopy(record["run_identity"]),
        open_evidence=copy.deepcopy(prepared.open_evidence.receipt.to_dict()),
    )
    body["payload"]["evidence_references"].insert(
        0, op.sql.parse_canonical(prepared.evidence_reference)
    )
    if change == "run-identity":
        identity = body["payload"]["run_identity"]
        identity["execution_nonce"] = "d" * 64
        identity["run_id"] = wire.make_run_id(
            started_at=wire._time(identity["started_at"]),
            git_sha=identity["git_sha"],
            dataset_id=identity["dataset_id"],
            scenario_id=identity["scenario_id"],
            seed=identity["seed"],
            execution_nonce=identity["execution_nonce"],
        )
        identity["identity_digest"] = op.sql.digest(
            {k: v for k, v in identity.items() if k != "identity_digest"}
        )
    else:
        body["payload"]["open_evidence"]["reservation_id"] = builder.UUID
    operator = Ed25519PrivateKey.generate()
    s.recovery = native_admin_provider(
        x, body, operator, record, candidate=candidate, completion=completion, prepared=prepared
    )
    broker = op.NativeBridgeBroker(
        journal=a.journal,
        session_qualifier=x.rig.n.qualifier,
        signer=a.bridge,
        host_sources=s,
        holder_admitter=lambda **kw: operator.public_key(),
        role="RECOVERY_OPERATOR",
    )
    before = a.journal.read()
    count = len(s.approvals)
    captured = native_five_frame_peer(
        x, host, executor, body, broker, operator, expect_failure=True
    )
    assert "cap" not in captured
    assert a.journal.read() == before and len(s.approvals) == count
    assert (
        s.publisher.transport.calls.count("B3_CAS") == 1
        and a.transport.calls.count("ADOPT_OPEN_MANUAL") == 0
    )


def test_native_publication_leg_never_retries_even_when_transport_outcome_unknown(native_open):
    import types

    x = native_open
    a = x.a
    authenticated = a.journal.authenticate(x.commit, "commit-open")
    prepared = a.sources.qualify_open(authenticated_request=authenticated, opening=x.attempt)
    leg = prepared.publication["adapter"].transport
    calls = []

    def unknown(payload, previous):
        calls.append((payload, previous))
        raise OSError("synthetic remote response lost")

    leg.transport = types.SimpleNamespace(publish_b3=unknown)
    payload = {"operation_id": x.cert.value["commit_request_id"]}
    previous = x.attempt.record["previous_witness_digest"]
    with pytest.raises(OSError):
        leg.publish_b3(payload, previous)
    with pytest.raises(op.OperationalError, match="UNCERTAIN_PUBLICATION"):
        leg.publish_b3(payload, previous)
    assert leg.sent and len(calls) == 1


@pytest.mark.parametrize("expired", [False, True])
def test_native_second_start_denied_before_capability_even_after_owner_exit_and_deadline(
    native_sources, expired
):
    import uuid
    from datetime import timedelta

    x = native_sources
    a = x.rig.a
    s = x.source
    _, approval, request = native_grant_request(x)
    with a.journal.opening(
        raw_request=request.canonical, raw_record=approval.reservation_record
    ) as opening:
        pass
    assert opening.writer.fd is None
    now = (
        wire._time(opening.record["freshness_deadline"]) + timedelta(seconds=1)
        if expired
        else a.journal._now()
    )
    a.journal.clock = lambda: now
    a.remote.clock = lambda: now
    body = copy.deepcopy(x.body)
    identity = body["payload"]["run_identity"]
    body.update(
        request_id=str(uuid.uuid4()),
        nonce="d" * 64,
        issued_at=cf.coord._utc(now),
        expires_at=cf.coord._utc(now + timedelta(seconds=120)),
    )
    identity.update(execution_nonce=body["nonce"], started_at=cf.coord._utc(now))
    identity["run_id"] = wire.make_run_id(
        started_at=now,
        git_sha=identity["git_sha"],
        dataset_id=identity["dataset_id"],
        scenario_id=identity["scenario_id"],
        seed=identity["seed"],
        execution_nonce=identity["execution_nonce"],
    )
    identity["identity_digest"] = op.sql.digest(
        {k: v for k, v in identity.items() if k != "identity_digest"}
    )
    before = a.journal.read()
    count = len(s.approvals)
    with pytest.raises(op.OperationalError, match="GLOBAL_START_BLOCKED"):
        s.approve_reserve(
            body=op.sql.canonical_bytes(body),
            native=x.rig.n.qualifier.qualify(),
            holder=a.holder.public_key(),
            challenge=None,
        )
    assert (
        a.journal.read() == before
        and len(s.approvals) == count
        and a.transport.calls.count("RESERVE") == 1
    )
    assert s.publisher.transport.calls.count("B3_CAS") == 0


@pytest.mark.parametrize("field", ["session_id", "session_header_sha256", "runtime_profile_sha256"])
def test_native_signed_capability_claims_bind_exact_current_native_context(native_sources, field):
    x = native_sources
    s = x.source
    a = x.rig.a
    native = x.rig.n.qualifier.qualify()
    approved = s.approve_reserve(
        body=op.sql.canonical_bytes(x.body),
        native=native,
        holder=a.holder.public_key(),
        challenge=None,
    )
    broker = op.NativeBridgeBroker(
        journal=a.journal,
        session_qualifier=x.rig.n.qualifier,
        signer=a.bridge,
        host_sources=s,
        holder_admitter=lambda **kw: a.holder.public_key(),
    )
    cap = broker._capability(approved, native=native, holder=a.holder.public_key())
    assert cap.to_dict()[field] == x.body[field]
    assert (
        cap.to_dict()["request_nonce"] == x.body["nonce"]
        and cap.to_dict()["request_body_digest"]
        == op.hashlib.sha256(approved.canonical_body).hexdigest()
    )


def test_native_docker_adapter_never_carries_recovery_operator_or_launches_process(native_sources):
    x = native_sources
    a = x.rig.a
    calls = []
    identity = x.rig.n.options["process"]
    adapter = op.FixedDockerAdapter(
        identity=identity,
        observer=lambda: identity,
        process_factory=lambda *args, **kw: calls.append(args),
    )
    broker = op.NativeBridgeBroker(
        journal=a.journal,
        session_qualifier=x.rig.n.qualifier,
        signer=a.bridge,
        host_sources=x.source,
        holder_admitter=lambda **kw: a.holder.public_key(),
        role="RECOVERY_OPERATOR",
    )
    with pytest.raises(op.OperationalError, match="ROLE_OPERATION_DENIED"):
        broker.open_docker_single(adapter, exchange_id=x.body["exchange_id"])
    assert calls == [] and not adapter.used and a.journal.read()["claims"] == {}


@pytest.mark.parametrize("admitted", ["missing", "different"])
def test_native_holder_never_trusts_input_key_without_independent_process_admission(
    native_sources, admitted
):
    import types

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    x = native_sources
    a = x.rig.a
    proposal = types.SimpleNamespace(
        to_dict=lambda: {"holder_public_key_hex": a.holder.public_key().public_bytes_raw().hex()}
    )
    independent = None if admitted == "missing" else Ed25519PrivateKey.generate().public_key()
    broker = op.NativeBridgeBroker(
        journal=a.journal,
        session_qualifier=x.rig.n.qualifier,
        signer=a.bridge,
        host_sources=x.source,
        holder_admitter=lambda **kw: independent,
    )
    with pytest.raises(op.OperationalError, match="PROCESS_ADMISSION_REQUIRED"):
        broker._holder(x.host, x.rig.n.qualifier.qualify(), proposal)
    assert a.journal.read()["claims"] == {} and x.source.approvals == {}


@pytest.mark.parametrize("failure", ["before", "response"])
def test_native_unknown_actual_witness_grant_never_emits_open_one_or_publishes_open(
    native_sources, failure
):
    x = native_sources
    a = x.rig.a
    s = x.source
    broker, approval, request = native_grant_request(x)
    a.transport.fail = failure
    emitted = []
    with pytest.raises(op.sql.AuthorityError, match="UNCERTAIN_PUBLICATION"):
        with a.journal.opening(
            raw_request=request.canonical, raw_record=approval.reservation_record
        ) as opening:
            emitted.append(broker._reserve_response(approval, opening, a.holder.public_key()))
    state = a.journal.read()
    assert emitted == [] and s.grants == {} and state["responses"] == {}
    assert state["active"] is not None and state["sequence"] == 0 and len(state["claims"]) == 1
    assert a.transport.calls.count("RESERVE") == 1 and a.transport.calls.count("COMMIT_OPEN") == 0
    assert s.publisher.transport.calls.count("B3_CAS") == 0
    a.transport.fail = None
    with a.journal.opening(
        raw_request=request.canonical, raw_record=approval.reservation_record
    ) as duplicate:
        assert type(duplicate) is cf.coord.DuplicateRecognition
    assert a.transport.calls.count("RESERVE") == 1 and state == a.journal.read()


def test_native_policy_preparatory_plan_exact_existing_facts_without_policy_grant(native_start_rig):
    x = native_start_rig
    native = x.n.qualifier.qualify()
    plan = op.NativePolicyResourcePlan.from_journal(policy=x.host_policy, journal=x.a.journal)
    bindings = tuple(v for k, v in x.host_policy.registry.bindings.items() if k != "policy")
    preparatory = op.NativeHostPolicy(
        **dict(x.policy_options, registry=op.HostArtifactRegistry(bindings), resource_plan=plan)
    )
    facts = preparatory.facts_from_resources(native=native, resources=plan)
    assert len(facts) == 15
    assert op.sql.canonical_bytes(facts) == x.host_policy.registry.read("policy")
    assert facts == x.host_policy.facts(native=native, journal=x.a.journal)
    assert facts["approved_executor_entrypoint_identity"] == {
        "container_id": native.process.container_id,
        "identity_digest": native.process.identity_digest,
        "argv": ["/usr/local/bin/python", "-B", "/app/manage.py", "s11_ex10", "bridge"],
    }
    # Preparing facts never bypasses the original real policy/approval admission.
    with pytest.raises(op.OperationalError):
        preparatory.qualify(native=native, journal=x.a.journal)
    qualified = op.NativeHostPolicy(**dict(x.policy_options, resource_plan=plan))
    assert qualified.qualify(native=native, journal=x.a.journal) is None


@pytest.mark.parametrize(
    "change",
    ["authority", "epoch", "binding", "file-id", "alias", "floor", "pin", "clock", "transport"],
)
def test_native_policy_plan_invalid_actual_resource_preparation_is_bounded(
    native_start_rig, change
):
    from dataclasses import replace

    x = native_start_rig
    plan = op.NativePolicyResourcePlan.from_journal(policy=x.host_policy, journal=x.a.journal)
    changes = {
        "authority": {"authority_id": "invalid"},
        "epoch": {"authority_epoch": plan.authority_epoch + 1},
        "binding": {"registry_binding": b"noncanonical"},
        "file-id": {"sidecar_identity": plan.lock_identity},
        "alias": {"receipt_store": plan.floor.store},
        "floor": {"floor": object()},
        "pin": {"bridge_pin": None},
        "clock": {"clock": None},
        "transport": {"transport": None},
    }
    with pytest.raises(op.OperationalError, match="^HOST_POLICY_INVALID$"):
        replace(plan, **changes[change])


@pytest.mark.parametrize("field", ["lock_store", "store", "receipt_store"])
def test_native_policy_plan_requires_same_journal_handles_not_only_same_facts(
    native_start_rig, field
):
    x = native_start_rig
    journal = x.a.journal
    plan = op.NativePolicyResourcePlan.from_journal(policy=x.host_policy, journal=journal)
    policy = op.NativeHostPolicy(**dict(x.policy_options, resource_plan=plan))
    original = getattr(journal, field)
    duplicate = op.durable.PrivateStore(original.root, original.identity)
    try:
        duplicate.check()
        assert duplicate.identity == original.identity and duplicate.fd != original.fd
        setattr(journal, field, duplicate)
        with pytest.raises(op.OperationalError, match="^HOST_POLICY_INVALID$"):
            policy.qualify(native=x.n.qualifier.qualify(), journal=journal)
    finally:
        setattr(journal, field, original)
        duplicate.close()


def test_native_policy_plan_rejects_authentic_floor_store_swap_after_approval(
    native_start_rig, tmp_path
):
    x = native_start_rig
    journal = x.a.journal
    original = journal.floor.store
    root = tmp_path / "authentic-floor-copy"
    root.mkdir(mode=0o700)
    for src in Path(original.root).glob("head-*.json"):
        dst = root / src.name
        dst.write_bytes(src.read_bytes())
        dst.chmod(0o600)
    replacement = op.durable.PrivateStore(str(root), h.identity(root))
    try:
        copied_floor = op.coordination.CoordinationFloor(
            replacement,
            authority_id=journal.authority_id,
            authority_epoch=journal.authority_epoch,
            witness_pins=journal.floor.pins,
        )
        assert copied_floor.current() == journal.floor.current()
        plan = op.NativePolicyResourcePlan.from_journal(policy=x.host_policy, journal=journal)
        approved = x.policy_options["approval_verifier"]
        calls = []

        def swap(**kw):
            approved(**kw)
            journal.floor.store = replacement
            calls.append("approved-original-then-swapped")

        policy = op.NativeHostPolicy(
            **dict(x.policy_options, resource_plan=plan, approval_verifier=swap)
        )
        with pytest.raises(op.OperationalError, match="^HOST_POLICY_INVALID$"):
            policy.qualify(native=x.n.qualifier.qualify(), journal=journal)
        assert calls == ["approved-original-then-swapped"]
    finally:
        journal.floor.store = original
        replacement.close()


@pytest.mark.parametrize(
    "store_field", ["lock_store", "sidecar_store", "ledger_store", "receipt_store"]
)
def test_native_policy_plan_rejects_same_inode_new_fd_after_approval(native_start_rig, store_field):
    x = native_start_rig
    plan = op.NativePolicyResourcePlan.from_journal(policy=x.host_policy, journal=x.a.journal)
    store = getattr(plan, store_field)
    original = store.fd
    duplicate = os.dup(original)
    approved = x.policy_options["approval_verifier"]
    calls = []

    def swap(**kw):
        approved(**kw)
        store.fd = duplicate
        store.check()
        calls.append("same-valid-inode-new-fd")

    policy = op.NativeHostPolicy(
        **dict(x.policy_options, resource_plan=plan, approval_verifier=swap)
    )
    try:
        with pytest.raises(op.OperationalError, match="^HOST_POLICY_INVALID$"):
            policy.qualify(native=x.n.qualifier.qualify(), journal=x.a.journal)
        assert calls == ["same-valid-inode-new-fd"]
    finally:
        store.fd = original
        os.close(duplicate)


@pytest.mark.parametrize("change", ["clear", "new-equivalent-plan"])
def test_native_policy_plan_mode_is_retained_after_approval(native_start_rig, change):
    x = native_start_rig
    plan = op.NativePolicyResourcePlan.from_journal(policy=x.host_policy, journal=x.a.journal)
    approved = x.policy_options["approval_verifier"]
    calls = []

    def swap(**kw):
        approved(**kw)
        policy._resource_plan = (
            None
            if change == "clear"
            else op.NativePolicyResourcePlan.from_journal(policy=x.host_policy, journal=x.a.journal)
        )
        calls.append("changed-mode")

    policy = op.NativeHostPolicy(
        **dict(x.policy_options, resource_plan=plan, approval_verifier=swap)
    )
    with pytest.raises(op.OperationalError, match="^HOST_POLICY_INVALID$"):
        policy.qualify(native=x.n.qualifier.qualify(), journal=x.a.journal)
    assert calls == ["changed-mode"]


def test_native_policy_preparatory_facts_recheck_resources_after_reference_io(native_start_rig):
    x = native_start_rig
    plan = op.NativePolicyResourcePlan.from_journal(policy=x.host_policy, journal=x.a.journal)
    policy = op.NativeHostPolicy(**dict(x.policy_options, resource_plan=plan))
    store = plan.receipt_store
    original_fd = store.fd
    duplicate = os.dup(original_fd)
    original_reference = policy.registry.reference
    calls = []

    def reference(name):
        result = original_reference(name)
        if name == "recovery_policy":
            store.fd = duplicate
            store.check()
            calls.append("last-reference-rebound-fd")
        return result

    policy.registry.reference = reference
    try:
        with pytest.raises(op.OperationalError, match="^HOST_POLICY_INVALID$"):
            policy.facts_from_resources(native=x.n.qualifier.qualify(), resources=plan)
        assert calls == ["last-reference-rebound-fd"]
    finally:
        store.fd = original_fd
        os.close(duplicate)
        policy.registry.reference = original_reference


@pytest.mark.parametrize("change", ["new-fd", "journal-handle"])
def test_native_policy_plan_last_approval_read_rechecks_resources_and_journal(
    native_start_rig, change
):
    x = native_start_rig
    journal = x.a.journal
    plan = op.NativePolicyResourcePlan.from_journal(policy=x.host_policy, journal=journal)
    registry = op.HostArtifactRegistry(tuple(x.host_policy.registry.bindings.values()))
    policy = op.NativeHostPolicy(**dict(x.policy_options, resource_plan=plan, registry=registry))
    store = plan.receipt_store
    original_fd = store.fd
    duplicate_fd = os.dup(original_fd)
    duplicate_store = op.durable.PrivateStore(store.root, store.identity)
    original_reference = registry.reference
    calls = []

    def reference(name):
        result = original_reference(name)
        if name == "recovery_policy":
            calls.append(name)
            # Original facts, approval tuple, final facts, final approval reread.
            if len(calls) == 4:
                if change == "new-fd":
                    store.fd = duplicate_fd
                    store.check()
                else:
                    journal.receipt_store = duplicate_store
                    journal.receipt_store.check()
        return result

    registry.reference = reference
    try:
        with pytest.raises(op.OperationalError, match="^HOST_POLICY_INVALID$"):
            policy.qualify(native=x.n.qualifier.qualify(), journal=journal)
        assert len(calls) == 4, "Denied before intended final actual approval read"
    finally:
        store.fd = original_fd
        journal.receipt_store = store
        os.close(duplicate_fd)
        duplicate_store.close()
        registry.reference = original_reference


@pytest.mark.parametrize("stage", ["approval", "last-reference"])
def test_native_policy_plan_retains_original_floor_witness_pins(native_start_rig, stage):
    x = native_start_rig
    journal = x.a.journal
    original = journal.floor.pins
    plan = op.NativePolicyResourcePlan.from_journal(policy=x.host_policy, journal=journal)
    registry = op.HostArtifactRegistry(tuple(x.host_policy.registry.bindings.values()))
    approved = x.policy_options["approval_verifier"]
    calls = []
    references = []

    def rebind():
        before = journal.floor.current()
        journal.floor.pins = (*original, original[0])
        assert journal.floor.current() == before
        calls.append("original-pin-selection-changed")

    def approval(**kw):
        approved(**kw)
        if stage == "approval":
            rebind()

    original_reference = registry.reference

    def reference(name):
        result = original_reference(name)
        if name == "recovery_policy":
            references.append(name)
            if stage == "last-reference" and len(references) == 4:
                rebind()
        return result

    policy = op.NativeHostPolicy(
        **dict(x.policy_options, resource_plan=plan, registry=registry, approval_verifier=approval)
    )
    registry.reference = reference
    try:
        with pytest.raises(op.OperationalError, match="^HOST_POLICY_INVALID$"):
            policy.qualify(native=x.n.qualifier.qualify(), journal=journal)
        assert calls == ["original-pin-selection-changed"]
        if stage == "last-reference":
            assert len(references) == 4
    finally:
        journal.floor.pins = original
        registry.reference = original_reference


def test_native_open_uses_publisher_clock_for_authenticated_b3_commit(native_open, monkeypatch):
    from datetime import timedelta

    x = native_open
    fallback_calls = []

    def unrelated_backend_default():
        fallback_calls.append(True)
        return op.coordination._utc(x.a.now() + timedelta(minutes=10))

    # Fault-inject only the unrelated constructor default. Signed heads, the
    # mandatory publisher clock, permits and journal windows retain their values.
    monkeypatch.setitem(
        op.durable.AuthorityBackend.__init__.__kwdefaults__,
        "clock",
        unrelated_backend_default,
    )
    try:
        response = x.attempt.complete_open(raw_request=x.commit)
    except Exception as error:
        chain = []
        seen = set()
        while error is not None and id(error) not in seen:
            seen.add(id(error))
            chain.append(type(error).__name__ + ":" + str(error))
            error = error.__cause__ or error.__context__
        pytest.fail("Actual B3/B4 publication failed: " + " <- ".join(chain))
    assert wire.parse_response_bytes(response).to_dict()["status"] == "OK"
    assert fallback_calls == []
    assert x.a.b3_floor.current().revision == 1
    assert x.a.floor.current()["sequence"] == 2
    assert x.a.b3_transport.calls.count("B3_CAS") == 1
    assert x.a.transport.calls.count("RESERVE") == 1
    assert x.a.transport.calls.count("COMMIT_OPEN") == 1
    assert len(x.cert.value) == 31


@pytest.mark.parametrize("stage", ["before-seal", "after-qualification"])
@pytest.mark.parametrize("age_microseconds", [0, 6_000_000, 6_000_001, -1])
def test_native_governance_six_second_seal_boundary(
    native_open, monkeypatch, stage, age_microseconds
):
    from datetime import timedelta

    a = native_open
    q = a.make_qualifier(a.attempt)
    authenticated = a.a.journal.authenticate(a.commit, "commit-open")
    issued_time = a.a.now()
    issued = issued_time.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    expected = q._expected
    qualification_calls = []

    def advance():
        a.a.journal.clock = lambda: issued_time + timedelta(microseconds=age_microseconds)

    def qualified(**kwargs):
        result = expected(**kwargs)
        qualification_calls.append(True)
        if stage == "after-qualification":
            advance()
        return result

    monkeypatch.setattr(q, "_expected", qualified)
    if stage == "before-seal":
        advance()
    before_b3 = a.a.b3_transport.calls.count("B3_CAS")
    if age_microseconds in (0, 6_000_000):
        cert, row, _ = q.seal(
            opening=a.attempt, authenticated_request=authenticated, issued_at=issued
        )
        assert qualification_calls == [True]
        assert cert.value["issued_at"] == issued
        assert Path(row["path"]).read_bytes() == cert.canonical_bytes
        assert op.governance.parse_governance_certificate_bytes(cert.canonical_bytes)
    else:
        with pytest.raises(op.OperationalError, match="^HOST_CONTEXT_INVALID$"):
            q.seal(opening=a.attempt, authenticated_request=authenticated, issued_at=issued)
        assert qualification_calls == ([True] if stage == "after-qualification" else [])
        assert not list(Path(q.store.root).iterdir())
    assert a.a.b3_transport.calls.count("B3_CAS") == before_b3


@pytest.mark.parametrize("age_microseconds", [0, 6_000_000, 6_000_001, -1])
def test_native_governance_six_second_live_window_boundary(native_open, age_microseconds):
    from datetime import timedelta

    a = native_open
    q = a.make_qualifier(a.attempt)
    issued_time = a.a.now()
    issued = issued_time.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    a.a.journal.clock = lambda: issued_time + timedelta(microseconds=age_microseconds)
    before_b3 = a.a.b3_transport.calls.count("B3_CAS")
    if age_microseconds in (0, 6_000_000):
        assert q.live_window(opening=a.attempt, issued_at=issued) is None
    else:
        with pytest.raises(op.OperationalError, match="^HOST_CONTEXT_INVALID$"):
            q.live_window(opening=a.attempt, issued_at=issued)
    assert a.a.b3_transport.calls.count("B3_CAS") == before_b3
    assert not list(Path(q.store.root).iterdir())
