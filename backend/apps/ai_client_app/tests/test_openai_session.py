"""Protocolo `s11-ex09-openai-v1` y su sesión. LOCAL TEST / NON-EXPERIMENTAL.

Ninguna prueba abre un socket: el SDK real de OpenAI se ejercita contra un
transporte `httpx2` en memoria, de modo que la petición, el bucle de reintentos y
los errores son los de la versión congelada sin que nada salga de la máquina. Las
claves tienen forma de clave pero son inventadas.
"""

import json
import socket
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx2
import pytest

from apps.ai_client_app.adapters.openai import (
    OpenAiClassificationAdapter,
    ProviderCall,
    build_classification_schema,
    build_client,
)
from apps.ai_client_app.evaluation import openai_session
from apps.ai_client_app.evaluation.dataset import load_dataset
from apps.ai_client_app.evaluation.openai_session import (
    INVALID,
    S11_EX09_OPENAI_V1,
    VALID,
    OpenAiProtocolError,
    canonical_sha256,
    classify_error,
    run_openai_session,
    verify_protocol_inputs,
)
from apps.ai_client_app.parser import ClassificationParseError
from apps.experiments_app.canonical import canonical_json_bytes, sha256_bytes

FAKE_KEY = "sk-proj-LOCALtestNOTaREALkeyEX09xxxxxxxxxxxx"
PROTOCOL = S11_EX09_OPENAI_V1


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("LOCAL TEST / NON-EXPERIMENTAL: la red está prohibida.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture(scope="module")
def dataset():
    return load_dataset()


def _gold(case) -> str:
    return json.dumps(
        {
            "category_slug": case.expected.category_slug,
            "institution_slug": case.expected.institution_slug,
            "priority": case.expected.priority,
            "confidence": 0.9,
            "inconsistency": case.expected.inconsistency,
            "justification": "LOCAL TEST / NON-EXPERIMENTAL.",
        }
    )


def _response_body(
    text: str, *, response_id="resp_local_1", model="gpt-6-luna", status="completed"
):
    return {
        "id": response_id,
        "object": "response",
        "created_at": 1790000000,
        "status": status,
        "model": model,
        "output": [
            {
                "type": "message",
                "id": "msg_local",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
    }


class Transport:
    """Transporte en memoria. Registra cada petición HTTP que el SDK intenta enviar."""

    def __init__(self, respond):
        self.requests = []
        self.respond = respond

    def __call__(self, request):
        self.requests.append(request)
        return self.respond(request)


def _sdk_adapter(transport):
    client = build_client(
        api_key=FAKE_KEY,
        timeout_seconds=PROTOCOL.request_timeout_seconds,
        http_client=httpx2.Client(transport=httpx2.MockTransport(transport)),
    )
    return OpenAiClassificationAdapter(
        client=client, model=PROTOCOL.model_name, reasoning_effort=PROTOCOL.reasoning_effort
    )


def _first_request(dataset):
    from apps.ai_client_app.evaluation.runner import build_request

    return build_request(dataset.cases[0], dataset)


# --- Protocolo congelado ---------------------------------------------------------------


def test_protocol_holds_the_values_frozen_in_p6():
    assert PROTOCOL.fields() == {
        "version": "s11-ex09-openai-v1",
        "provider": "openai",
        "api": "responses",
        "endpoint": "POST /v1/responses",
        "model_name": "gpt-6-luna",
        "reasoning_effort": "low",
        "image_detail": "auto",
        "store": False,
        "request_timeout_seconds": 60,
        "sdk_package": "openai",
        "sdk_version": "3.19.2",
        "sdk_max_retries": 0,
        "script_retries": 0,
        "celery_retries": 0,
        "dataset_version": "s8-evaluation-v1",
        "dataset_sha256": "3bc54d591dc104aeabeecb2f5cc7c7a38e70ba042e7f045321ab33ae9b5efad4",
        "total_cases": 28,
        "prompt_version": "s8-classification-v1",
        "prompt_sha256": "f3c803984806a3b934db0417f6cc3ce59a49cdde1e30b4c86b862c2e11c665ca",
        "schema_name": "classification_v1",
        "schema_sha256": "ef2a7d354dc1f4526e6776c22e2356afa9881370618de75343aef339c5425f23",
        "sessions": 1,
        "concurrency": 1,
        "inter_request_delay_seconds": 0,
    }


def test_schema_hash_uses_the_s11_canonical_encoding():
    schema = build_classification_schema()
    assert canonical_sha256(schema) == sha256_bytes(canonical_json_bytes(schema))
    assert openai_session.schema_sha256() == PROTOCOL.schema_sha256


def test_real_inputs_match_the_protocol(dataset):
    verified, measured = verify_protocol_inputs(protocol=PROTOCOL)
    assert [case.id for case in verified.cases] == [case.id for case in dataset.cases]
    assert measured == {
        "dataset_sha256": PROTOCOL.dataset_sha256,
        "prompt_sha256": PROTOCOL.prompt_sha256,
        "schema_sha256": PROTOCOL.schema_sha256,
        "sdk_version": "3.19.2",
    }


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"dataset_sha256": "0" * 64}, "dataset"),
        ({"prompt_sha256": "0" * 64}, "prompt"),
        ({"schema_sha256": "0" * 64}, "schema-sha256"),
        ({"sdk_version": "3.19.1"}, "sdk-version"),
        ({"sdk_max_retries": 2}, "adapter-sdk_max_retries"),
        ({"image_detail": "high"}, "adapter-image_detail"),
        ({"prompt_version": "otro"}, "prompt-version"),
    ],
)
def test_divergent_inputs_are_refused_before_any_client(change, error):
    with pytest.raises(OpenAiProtocolError, match=error):
        verify_protocol_inputs(protocol=replace(PROTOCOL, **change))


def test_a_different_installed_sdk_is_refused(monkeypatch):
    monkeypatch.setattr(openai_session, "installed_sdk_version", lambda package="openai": "3.20.0")
    with pytest.raises(OpenAiProtocolError, match="sdk-version:3.20.0"):
        verify_protocol_inputs(protocol=PROTOCOL)


# --- La petición real, sin red ---------------------------------------------------------


def test_real_sdk_sends_the_frozen_request_once(dataset):
    transport = Transport(
        lambda request: httpx2.Response(
            200, json=_response_body(_gold(dataset.cases[0])), headers={"x-request-id": "req_1"}
        )
    )
    call = _sdk_adapter(transport).classify_with_metadata(_first_request(dataset))

    assert call == ProviderCall(
        text=_gold(dataset.cases[0]),
        response_id="resp_local_1",
        request_id="req_1",
        model="gpt-6-luna",
    )
    [request] = transport.requests
    assert (request.method, str(request.url)) == ("POST", "https://api.openai.com/v1/responses")
    body = json.loads(request.content)
    assert sorted(body) == ["input", "instructions", "model", "reasoning", "store", "text"]
    assert body["model"] == "gpt-6-luna"
    assert body["reasoning"] == {"effort": "low"}
    assert body["store"] is False
    fmt = body["text"]["format"]
    assert (fmt["type"], fmt["name"], fmt["strict"]) == ("json_schema", "classification_v1", True)
    assert canonical_sha256(fmt["schema"]) == PROTOCOL.schema_sha256
    blocks = [block for turn in body["input"] for block in turn["content"]]
    assert [block["type"] for block in blocks] == ["input_text"]
    assert "image: no disponible" in blocks[0]["text"]
    assert request.headers["x-stainless-retry-count"] == "0"


@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_retryable_statuses_are_attempted_exactly_once(dataset, status):
    transport = Transport(lambda request: httpx2.Response(status, json={"error": {}}))
    with pytest.raises(Exception) as caught:
        _sdk_adapter(transport).classify_with_metadata(_first_request(dataset))
    assert len(transport.requests) == 1
    assert classify_error(caught.value) in {"provider_4xx", "rate_limit_429", "provider_5xx"}


def test_a_transport_timeout_is_one_attempt_and_classified_as_timeout(dataset):
    def respond(request):
        raise httpx2.ReadTimeout("simulated", request=request)

    transport = Transport(respond)
    with pytest.raises(Exception) as caught:
        _sdk_adapter(transport).classify_with_metadata(_first_request(dataset))
    assert len(transport.requests) == 1
    assert classify_error(caught.value) == "timeout"


def test_a_connection_failure_is_classified_as_network_error(dataset):
    def respond(request):
        raise httpx2.ConnectError("simulated", request=request)

    transport = Transport(respond)
    with pytest.raises(Exception) as caught:
        _sdk_adapter(transport).classify_with_metadata(_first_request(dataset))
    assert len(transport.requests) == 1
    assert classify_error(caught.value) == "network_error"


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (429, "rate_limit_exceeded", "rate_limit_429"),
        (429, "insufficient_quota", "insufficient_quota"),
        (401, "invalid_api_key", "authentication"),
        (403, "unsupported_country_region_territory", "authorization"),
        (400, "invalid_request_error", "provider_4xx"),
        (404, "model_not_found", "provider_4xx"),
        (500, "server_error", "provider_5xx"),
        (503, "overloaded", "provider_5xx"),
    ],
)
def test_provider_errors_are_classified_by_status_and_code(dataset, status, code, expected):
    transport = Transport(
        lambda request: httpx2.Response(status, json={"error": {"code": code, "message": "x"}})
    )
    with pytest.raises(Exception) as caught:
        _sdk_adapter(transport).classify_with_metadata(_first_request(dataset))
    assert classify_error(caught.value) == expected


def test_an_incomplete_response_is_classified_and_never_parsed(dataset):
    transport = Transport(
        lambda request: httpx2.Response(200, json=_response_body("{}", status="incomplete"))
    )
    with pytest.raises(Exception) as caught:
        _sdk_adapter(transport).classify_with_metadata(_first_request(dataset))
    assert classify_error(caught.value) == "provider_incomplete"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("no es json", "malformed_response"),
        ("[1, 2]", "malformed_response"),
        ('{"category_slug": null}', "schema_validation"),
    ],
)
def test_parser_rejections_split_malformed_from_schema(raw, expected):
    from apps.ai_client_app.parser import parse_classification_response

    with pytest.raises(ClassificationParseError) as caught:
        parse_classification_response(raw)
    assert classify_error(caught.value) == expected


def test_an_error_outside_the_sdk_is_unexpected():
    assert classify_error(RuntimeError("boom")) == "unexpected_provider_error"


# --- La sesión -------------------------------------------------------------------------


class FakeClient:
    def __init__(self, *, api_key, timeout_seconds):
        assert api_key == FAKE_KEY
        assert timeout_seconds == PROTOCOL.request_timeout_seconds


class FakeAdapter:
    """Responde el oro, salvo los casos marcados como erróneos, caídos o malformados."""

    def __init__(self, dataset, *, wrong=(), errors=None, malformed=()):
        self.by_description = {case.description: case for case in dataset.cases}
        self.priorities = dataset.catalog.priorities
        self.wrong = set(wrong)
        self.errors = dict(errors or {})
        self.malformed = set(malformed)
        self.calls = []

    def __call__(self, *, client, model, reasoning_effort):
        assert (model, reasoning_effort) == ("gpt-6-luna", "low")
        return self

    def classify_with_metadata(self, request):
        assert request.image_bytes == b""
        case = self.by_description[request.description]
        self.calls.append(case.id)
        if case.id in self.errors:
            raise self.errors[case.id]
        text = _gold(case)
        if case.id in self.wrong:
            data = json.loads(text)
            data["priority"] = next(p for p in self.priorities if p != data["priority"])
            text = json.dumps(data)
        if case.id in self.malformed:
            text = "no es json"
        return ProviderCall(
            text=text,
            response_id=f"resp_{case.id}",
            request_id=f"req_{case.id}",
            model="gpt-6-luna",
        )


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)

    def __call__(self):
        self.now += timedelta(seconds=1)
        return self.now


def _monotonic(step_seconds: float):
    state = {"now": 0}

    def tick():
        state["now"] += int(step_seconds * 1_000_000_000)
        return state["now"]

    return tick


def _session(adapter, *, step=1.0):
    return run_openai_session(
        protocol=PROTOCOL,
        api_key=FAKE_KEY,
        client_factory=FakeClient,
        adapter_factory=adapter,
        clock=Clock(),
        monotonic_ns=_monotonic(step),
    )


def test_one_session_sends_the_28_cases_in_order_without_images(dataset):
    adapter = FakeAdapter(dataset)
    session = _session(adapter)

    assert adapter.calls == [case.id for case in dataset.cases]
    assert session.attempted_cases == 28
    assert session.images_sent == 0
    assert session.aborted_reason is None
    assert [record.sequence for record in session.cases] == list(range(1, 29))
    assert all(record.validity == VALID for record in session.cases)
    assert all(record.duration_ms == 1000 for record in session.cases)
    assert {record.model for record in session.cases} == {"gpt-6-luna"}
    assert all(result.ca4_concordant for result in session.results)
    assert (session.reasoning_effort, session.image_detail, session.store) == ("low", "auto", False)


def test_a_provider_error_is_recorded_never_retried_and_the_session_continues(dataset):
    first = dataset.cases[0].id
    adapter = FakeAdapter(dataset, errors={first: RuntimeError("boom")})
    session = _session(adapter)

    assert adapter.calls.count(first) == 1
    assert len(adapter.calls) == 28
    record = session.cases[0]
    assert (record.validity, record.error_class) == (INVALID, "unexpected_provider_error")
    assert session.results[0].error_type == "RuntimeError"
    assert session.results[0].ca4_concordant is False


def test_a_malformed_answer_is_invalid_not_a_disagreement(dataset):
    target = dataset.cases[3].id
    session = _session(FakeAdapter(dataset, malformed={target}))
    record = session.cases[3]
    assert (record.validity, record.error_class) == (INVALID, "malformed_response")
    assert session.results[3].error_type == "ClassificationParseError"


def test_a_case_longer_than_the_timeout_is_invalid(dataset):
    # El reloj monotónico avanza una vez entre el inicio y el fin de cada caso.
    session = _session(FakeAdapter(dataset), step=61.0)
    assert all(record.error_class == "duration_exceeded" for record in session.cases)
    assert all(record.duration_ms == 61000 for record in session.cases)
    assert all(record.validity == INVALID for record in session.cases)


def test_an_image_stops_the_session_before_sending_it(dataset, monkeypatch):
    from apps.ai_client_app.evaluation import runner

    real_build = runner.build_request
    target = dataset.cases[2].id

    def with_image(case, data):
        request = real_build(case, data)
        return replace(request, image_bytes=b"\x89PNG") if case.id == target else request

    monkeypatch.setattr(openai_session, "build_request", with_image)
    adapter = FakeAdapter(dataset)
    session = _session(adapter)

    assert target not in adapter.calls
    assert adapter.calls == [case.id for case in dataset.cases[:2]]
    assert session.aborted_reason == f"image-present:{target}"
    assert session.cases[-1].error_class == "image_present"
    assert session.images_sent == 0
    assert session.attempted_cases == 2


def test_the_session_never_holds_the_key(dataset):
    session = _session(FakeAdapter(dataset))
    from dataclasses import asdict

    assert FAKE_KEY not in json.dumps(asdict(session), default=str)


def test_an_empty_key_is_refused():
    with pytest.raises(ValueError):
        run_openai_session(protocol=PROTOCOL, api_key="")
