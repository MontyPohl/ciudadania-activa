"""EX-10 fase 2B-1: fixture de 10 ciudadanos y tokens efímeros. LOCAL TEST / NON-EXPERIMENTAL.

Sólo base de tests: sin stack S11, sin requests a `/api/v1/reports/`, sin `var/s11-runs` y
sin run oficial. `s11-load-v1` sirve únicamente para derivar y verificar los actores.
"""

import copy
import io
import json
import pickle
import threading
import traceback
from datetime import timedelta

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, transaction
from django.utils import timezone
from rest_framework_simplejwt import state as jwt_state
from rest_framework_simplejwt.backends import TokenBackend
from rest_framework_simplejwt.exceptions import TokenBackendError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import AccessToken

from apps.auth_app.enums import UserRole, VerificationStatus
from apps.auth_app.models import CitizenProfile
from apps.experiments_app import ex10_fixture as fx
from apps.experiments_app import ex10_load

User = get_user_model()
Result = fx.ActorResult
#: Clave de firma local de la rotación simulada: fija, ficticia y de más de 32 bytes.
ROTATED_SIGNING_KEY = "rotated-local-test-signing-key-not-a-secret"  # noqa: S105


@pytest.fixture(scope="module")
def dataset():
    return ex10_load.load_dataset()


@pytest.fixture(scope="module")
def specs(dataset):
    return fx.actor_specs(dataset)


def with_payload(dataset, mutate):
    payload = copy.deepcopy(dict(dataset.payload))
    mutate(payload)
    return ex10_load.LoadDataset(dataset.sha256, payload)


# --- Dataset e identidad ---------------------------------------------------------------------


def test_actor_specs_are_the_ten_frozen_dataset_users(dataset, specs):
    users = dataset.payload["users"]
    assert dataset.sha256 == ex10_load.DATASET_SHA256
    assert len(specs) == 10
    assert [s.alias for s in specs] == [u["alias"] for u in users]
    assert [str(s.user_id) for s in specs] == [u["id"] for u in users]
    assert len({s.alias for s in specs}) == len({s.user_id for s in specs}) == 10
    assert all(len(s.request_keys) == 5 for s in specs)
    assert sorted(k for s in specs for k in s.request_keys) == [
        f"load-request-{n:02d}" for n in range(1, 51)
    ]


def test_identity_is_deterministic_and_synthetic(dataset, specs):
    again = fx.actor_specs(ex10_load.load_dataset())
    assert again == specs
    first = specs[0]
    assert first.email == "s11-ex10-load-citizen-01@example.invalid"
    assert first.full_name == "SYNTHETIC load-citizen-01"
    assert len(first.document_hmac) == 64 and first.document_hmac == again[0].document_hmac
    assert all(s.email.endswith("@example.invalid") for s in specs)
    assert len({s.document_hmac for s in specs}) == 10


def test_altered_dataset_bytes_are_rejected_before_any_write():
    raw = ex10_load.DATASET_PATH.read_bytes()
    with pytest.raises(ex10_load.Ex10DatasetError):
        ex10_load.parse_dataset(raw.replace(b"load-citizen-01", b"load-citizen-99", 1))


@pytest.mark.parametrize(
    ("mutate", "issue"),
    [
        (
            lambda p: p["users"][0].update(email="real.person@example.com"),
            fx.DatasetIssue("fields", "exact-fields-required", 1, "missing:none,extra:1"),
        ),
        (
            lambda p: p["users"][1].update(role="operator"),
            fx.DatasetIssue("role", "citizen-required", 2),
        ),
        (
            lambda p: p["users"][2].update(verified=False),
            fx.DatasetIssue("verified", "true-required", 3),
        ),
        (
            lambda p: p["users"][3].update(alias="real-name"),
            fx.DatasetIssue("alias", "canonical-alias-required", 4),
        ),
        (
            lambda p: p["users"][4].update(id="not-a-uuid"),
            fx.DatasetIssue("id", "canonical-uuid-required", 5),
        ),
        (
            lambda p: p["users"][5].pop("verified"),
            fx.DatasetIssue("fields", "exact-fields-required", 6, "missing:verified,extra:0"),
        ),
    ],
)
def test_dataset_users_are_validated_beyond_the_hash(dataset, mutate, issue):
    with pytest.raises(fx.Ex10FixtureError) as rejected:
        fx.actor_specs(with_payload(dataset, mutate))
    assert (rejected.value.code, rejected.value.issues) == ("DATASET_INVALID", (issue,))
    assert str(rejected.value) == f"DATASET_INVALID: {issue}"
    assert "real.person" not in str(rejected.value)


def test_dataset_with_other_sha_or_size_is_rejected(dataset):
    with pytest.raises(fx.Ex10FixtureError) as rejected:
        fx.actor_specs(ex10_load.LoadDataset("0" * 64, dataset.payload))
    assert rejected.value.issues == (fx.DatasetIssue("sha256", "frozen-sha256-required"),)
    with pytest.raises(fx.Ex10FixtureError, match="DATASET_USERS"):
        fx.actor_specs(with_payload(dataset, lambda p: p["users"].pop()))


#: Valores que un dataset alterado podría traer: ninguno puede volver en un error.
HOSTILE = [
    "load-citizen-01\nload-citizen-02",
    "../../var/s11-runs/secret",
    "real.person@example.com",
    "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiaG9zdGlsZSJ9.aG9zdGlsZS1zaWduYXR1cmU",
    "Authorization: Bearer hostile-token-0123456789abcdef",
    "sk-proj-HOSTILEHOSTILEHOSTILEHOSTILE0123456789",
    "load-citizen-01\x1b[31m\x00\x07",
    "load-citizen-" + "9" * 10_000,
]


def assert_no_dataset_value(error, value):
    """Ni el valor ni un fragmento reconocible aparecen en el error ni en su traceback."""
    texts = [str(error), repr(error), *map(str, error.args), *map(str, error.issues)]
    assert all(text.isprintable() and len(text) < 2_000 for text in texts)
    # La excepción original (p. ej. un KeyError con el alias) no se encadena ni se muestra.
    assert error.__cause__ is None
    assert error.__context__ is None or error.__suppress_context__
    texts.append("".join(traceback.format_exception(error)))
    fragments = {value, value[:16], value[-16:]}
    assert not [f for f in fragments for text in texts if f in text]


@pytest.mark.parametrize("value", HOSTILE, ids=range(len(HOSTILE)))
@pytest.mark.parametrize(
    "companion",
    [
        {},
        {"email": "real.person@example.com"},  # la ruta de campos extra citaba el alias
        {"role": "operator"},  # la ruta de rol citaba el alias
    ],
    ids=["alias-only", "with-extra-field", "with-wrong-role"],
)
def test_hostile_aliases_never_reach_the_error(dataset, value, companion):
    def mutate(payload):
        payload["users"][3].update(alias=value, **companion)

    with pytest.raises(fx.Ex10FixtureError) as rejected:
        fx.actor_specs(with_payload(dataset, mutate))
    error = rejected.value
    assert error.code == "DATASET_INVALID"
    assert {(i.actor_index, i.field) for i in error.issues} <= {
        (4, "alias"),
        (4, "role"),
        (4, "fields"),
    }
    assert_no_dataset_value(error, value)


@pytest.mark.parametrize("value", HOSTILE[1:6], ids=range(5))
def test_hostile_values_in_other_fields_and_keys_never_reach_the_error(dataset, value):
    def mutate(payload):
        users = payload["users"]
        users[0].update(id=value)
        users[1].update(role=value)
        users[2].update(verified=value)
        users[3][value] = value  # clave extra hostil

    with pytest.raises(fx.Ex10FixtureError) as rejected:
        fx.actor_specs(with_payload(dataset, mutate))
    assert [(i.actor_index, i.field) for i in rejected.value.issues] == [
        (1, "id"),
        (2, "role"),
        (3, "verified"),
        (4, "fields"),
    ]
    assert_no_dataset_value(rejected.value, value)


@pytest.mark.parametrize("value", HOSTILE, ids=range(len(HOSTILE)))
def test_hostile_request_rows_never_reach_the_plan_error(dataset, value):
    def unknown_user(payload):
        payload["requests"][0]["user_alias"] = value  # antes: KeyError con el alias crudo

    def rejected_plan(payload):
        payload["requests"][0].update(request_key=value)  # Ex10PlanError citaba la clave
        payload["requests"][0]["image"]["sha256"] = "not-a-sha"

    for mutate, detail in ((unknown_user, "KeyError"), (rejected_plan, "plan-image-sha256")):
        with pytest.raises(fx.Ex10FixtureError) as rejected:
            fx.actor_specs(with_payload(dataset, mutate))
        assert rejected.value.code == "DATASET_PLAN"
        assert detail in {issue.detail for issue in rejected.value.issues}
        assert_no_dataset_value(rejected.value, value)


# --- Fixture -------------------------------------------------------------------------------


@pytest.mark.django_db
def test_first_run_creates_exactly_the_ten_dataset_actors(specs):
    result = fx.prepare_fixture()
    assert result.outcome is fx.FixtureOutcome.PREPARED
    assert [a.result for a in result.actors] == [Result.CREATED] * 10
    assert User.objects.count() == 10 == CitizenProfile.objects.count()
    assert set(User.objects.values_list("id", flat=True)) == {s.user_id for s in specs}
    for spec in specs:
        user = User.objects.get(pk=spec.user_id)
        assert (user.email, user.full_name) == (spec.email, spec.full_name)
        assert (user.role, user.verification_status) == (
            UserRole.CITIZEN,
            VerificationStatus.APROBADO,
        )
        assert user.is_active and not user.is_staff and not user.is_superuser
        assert not user.has_usable_password()
        profile = user.citizen_profile
        assert profile.document_hmac == spec.document_hmac
        assert profile.verified_at is not None and profile.verification_selfie_url == ""


@pytest.mark.django_db
def test_second_run_matches_without_duplicating():
    first = fx.prepare_fixture()
    second = fx.prepare_fixture()
    assert [a.result for a in second.actors] == [Result.ALREADY_MATCHED] * 10
    assert [a.profile_id for a in second.actors] == [a.profile_id for a in first.actors]
    assert User.objects.count() == 10 == CitizenProfile.objects.count()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("role", UserRole.OPERATOR),
        ("verification_status", VerificationStatus.PENDIENTE),
        ("is_active", False),
        ("full_name", "Otra persona"),
    ],
)
def test_drift_is_reported_and_never_corrected(specs, field, value):
    fx.prepare_fixture()
    User.objects.filter(pk=specs[6].user_id).update(**{field: value})
    result = fx.prepare_fixture()
    assert result.outcome is fx.FixtureOutcome.DRIFT_DETECTED and result.prepared_at is None
    drifted = [a for a in result.actors if a.result is Result.DRIFT_DETECTED]
    assert [(a.alias, a.drift) for a in drifted] == [(specs[6].alias, (field,))]
    assert getattr(User.objects.get(pk=specs[6].user_id), field) == value  # sin corregir
    assert User.objects.count() == 10


@pytest.mark.django_db
def test_missing_profile_and_usable_password_are_drift(specs):
    fx.prepare_fixture()
    CitizenProfile.objects.filter(user_id=specs[0].user_id).delete()
    user = User.objects.get(pk=specs[1].user_id)
    user.set_password("local-test-password")
    user.save()
    drift = {a.alias: a.drift for a in fx.prepare_fixture().actors if a.drift}
    assert drift == {
        specs[0].alias: ("citizen-profile:missing",),
        specs[1].alias: ("password-usable",),
    }


@pytest.mark.django_db
def test_a_foreign_account_is_never_reused(specs):
    foreign = User.objects.create_user(email=specs[3].email, password=None, full_name="Ajena")
    result = fx.prepare_fixture()
    assert result.outcome is fx.FixtureOutcome.DRIFT_DETECTED
    by_alias = {a.alias: a for a in result.actors}
    assert by_alias[specs[3].alias].drift == ("foreign-account:email",)
    assert {a.result for a in result.actors if a.alias != specs[3].alias} == {Result.MISSING}
    assert list(User.objects.values_list("id", flat=True)) == [foreign.id]  # nada creado


@pytest.mark.django_db
def test_a_failure_halfway_rolls_back_every_actor(monkeypatch):
    original = fx._create_actor
    calls = []

    def failing(spec, **kwargs):
        calls.append(spec.alias)
        if len(calls) == 7:
            raise RuntimeError("fallo simulado en el actor 7")
        return original(spec, **kwargs)

    monkeypatch.setattr(fx, "_create_actor", failing)
    with pytest.raises(RuntimeError, match="actor 7"):
        fx.prepare_fixture()
    assert len(calls) == 7
    assert User.objects.count() == 0 == CitizenProfile.objects.count()


def in_threads(*targets):
    """Corre cada `target` en su hilo, con su propia conexión, y exige que todos terminen.

    Cada hilo cierra su conexión de Django al salir; ninguna conexión ni transacción se
    comparte con el hilo del test. Devuelve los resultados en el orden de `targets`.
    """
    outcomes = [None] * len(targets)
    closed = [False] * len(targets)

    def run(index, target):
        try:
            outcomes[index] = ("ok", target())
        except BaseException as exc:
            outcomes[index] = ("error", exc)
        finally:
            connection.close()
            closed[index] = connection.connection is None

    threads = [
        threading.Thread(target=run, args=(index, target), daemon=True)
        for index, target in enumerate(targets)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert not [thread for thread in threads if thread.is_alive()], "un hilo no terminó"
    assert all(closed), "un hilo no cerró su conexión"
    assert [value for kind, value in outcomes if kind == "error"] == []
    return [value for _, value in outcomes]


def advisory_locks():
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'")
        return cursor.fetchone()[0]


def lock_is_free_elsewhere():
    """Intenta el lock del fixture desde otra conexión, sin esperar a obtenerlo."""

    def probe():
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [fx._LOCK_KEY])
            return cursor.fetchone()[0]

    return in_threads(probe)[0]


def test_the_lock_key_is_stable_across_processes():
    # Derivada con SHA-256, nunca con `hash()`: no depende de PYTHONHASHSEED.
    assert fx._LOCK_KEY == -8116833747247149353


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_concurrent_fixtures_serialize_instead_of_duplicating():
    barrier = threading.Barrier(2, timeout=10)

    def prepare():
        barrier.wait()
        return fx.prepare_fixture()

    results = in_threads(prepare, prepare)
    per_run = sorted(tuple({a.result for a in result.actors}) for result in results)
    assert per_run == [(Result.ALREADY_MATCHED,), (Result.CREATED,)]
    assert User.objects.count() == 10 == CitizenProfile.objects.count()
    assert advisory_locks() == 0


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_advisory_lock_lives_only_inside_the_fixture_transaction(monkeypatch):
    original = fx._create_actor
    held = []

    def failing(spec, **kwargs):
        held.append(not lock_is_free_elsewhere())
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(fx, "_create_actor", failing)
    with pytest.raises(RuntimeError, match="fallo simulado"):
        fx.prepare_fixture()
    assert lock_is_free_elsewhere()  # el rollback lo libera
    assert User.objects.count() == 0

    def probing(spec, **kwargs):
        if len(held) == 1:
            held.append(not lock_is_free_elsewhere())
        return original(spec, **kwargs)

    monkeypatch.setattr(fx, "_create_actor", probing)
    assert fx.prepare_fixture().outcome is fx.FixtureOutcome.PREPARED
    assert held == [True, True]  # retenido mientras dura la transacción
    assert lock_is_free_elsewhere()  # y liberado al confirmar
    assert advisory_locks() == 0


# --- Payload seguro ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_safe_payload_holds_identifiers_only(specs):
    result = fx.prepare_fixture()
    payload = result.to_json()
    assert payload["dataset_sha256"] == ex10_load.DATASET_SHA256
    assert (payload["count"], payload["outcome"]) == (10, "PREPARED")
    assert set(payload["actors"][0]) == {
        "alias",
        "user_id",
        "profile_id",
        "role",
        "verification_status",
        "result",
        "drift",
    }
    text = json.dumps(payload)
    assert "@example.invalid" not in text
    assert not any(s.document_hmac in text for s in specs)
    assert not any(u.password in text for u in User.objects.all())


# --- Tokens ---------------------------------------------------------------------------------


@pytest.fixture
def prepared(db):
    return fx.prepare_fixture()


def test_ten_tokens_are_issued_for_the_right_actors(prepared):
    tokens = fx.issue_tokens(prepared)
    assert len(tokens) == 10
    assert tokens.aliases() == tuple(sorted(a.alias for a in prepared.actors))
    for actor in prepared.actors:
        decoded = AccessToken(tokens.mapping()[actor.alias])  # nunca el JWT en la aserción
        assert decoded["user_id"] == actor.user_id
    checks = fx.verify_tokens(tokens, prepared)
    assert all(check.ok for check in checks) and len(checks) == 10


def test_token_checks_detect_short_life_and_foreign_tokens(prepared):
    tokens = fx.issue_tokens(prepared)
    # Relativo a la vida configurada, no a 15 min fijos: un minuto a cada lado del umbral G0.
    threshold = timezone.now() + api_settings.ACCESS_TOKEN_LIFETIME - fx.MIN_TOKEN_REMAINING
    soon = fx.verify_tokens(tokens, prepared, now=threshold + timedelta(minutes=1))
    assert not any(check.remaining_ok for check in soon)  # G0: al menos 10 min restantes
    early = fx.verify_tokens(tokens, prepared, now=threshold - timedelta(minutes=1))
    assert all(check.remaining_ok for check in early)
    swapped = dict(tokens.mapping())
    first, second = prepared.actors[0].alias, prepared.actors[1].alias
    swapped[first], swapped[second] = swapped[second], swapped[first]
    checks = {c.alias: c for c in fx.verify_tokens(fx.EphemeralTokens(swapped), prepared)}
    assert not checks[first].user_matches and not checks[second].user_matches
    garbled = dict(tokens.mapping(), **{first: "not-a-token"})
    assert not fx.verify_tokens(fx.EphemeralTokens(garbled), prepared)[0].signature_valid


def test_tokens_are_never_persisted(prepared):
    tokens = fx.issue_tokens(prepared)
    dump = io.StringIO()
    call_command("dumpdata", stdout=dump)
    assert not any(token in dump.getvalue() for token in tokens.mapping().values())
    assert "rest_framework_simplejwt.token_blacklist" not in settings.INSTALLED_APPS
    tables = connection.introspection.table_names()
    assert not [t for t in tables if "token_blacklist" in t or "outstanding" in t]


def test_tokens_never_appear_in_payloads_reprs_logs_or_pickles(prepared, caplog):
    with caplog.at_level("DEBUG"):
        tokens = fx.issue_tokens(prepared)
        checks = fx.verify_tokens(tokens, prepared)
    values = list(tokens.mapping().values())
    texts = [
        json.dumps(prepared.to_json()),
        json.dumps([c.to_json() for c in checks]),
        repr(tokens),
        str(tokens),
        repr(prepared),
        repr(checks),
        caplog.text,
    ]
    assert not any(value in text for value in values for text in texts)
    assert repr(tokens) == "EphemeralTokens(count=10, released=False)"
    with pytest.raises(TypeError):
        pickle.dumps(tokens)


def test_released_tokens_drop_their_references(prepared):
    tokens = fx.issue_tokens(prepared)
    tokens.release()
    assert (len(tokens), tokens.released, tokens.aliases()) == (0, True, ())
    with pytest.raises(fx.Ex10FixtureError, match="TOKENS_RELEASED"):
        tokens.mapping()
    tokens.release()  # idempotente


def test_tokens_require_a_prepared_fixture(prepared):
    drifted = fx.FixtureResult(fx.FixtureOutcome.DRIFT_DETECTED, prepared.actors, None)
    with pytest.raises(fx.Ex10FixtureError, match="FIXTURE_NOT_PREPARED"):
        fx.issue_tokens(drifted)


def signing_state():
    """Todo lo global que decide la firma: settings, api_settings y el backend de SimpleJWT."""
    return (
        settings.SECRET_KEY,
        api_settings.SIGNING_KEY,
        api_settings.VERIFYING_KEY,
        api_settings.ALGORITHM,
        jwt_state.token_backend.signing_key,
        jwt_state.token_backend.verifying_key,
    )


def test_signing_key_is_the_django_secret_so_rotation_invalidates(prepared):
    # Propiedad del firmante en settings.test, sin valores reales: rotar la clave de Django
    # cambia el firmante y los tokens anteriores dejan de validar. La rotación se simula con
    # un backend local; nada global cambia.
    before = signing_state()
    assert api_settings.SIGNING_KEY == settings.SECRET_KEY
    token = fx.issue_tokens(prepared).mapping()[prepared.actors[0].alias]
    current = TokenBackend(api_settings.ALGORITHM, signing_key=settings.SECRET_KEY)
    assert current.decode(token)["user_id"] == prepared.actors[0].user_id
    rotated = TokenBackend(api_settings.ALGORITHM, signing_key=ROTATED_SIGNING_KEY)
    with pytest.raises(TokenBackendError):
        rotated.decode(token)
    unchanged = signing_state() == before  # sin valores en el informe si fallara
    assert unchanged
    fresh = AccessToken.for_user(User.objects.get(pk=prepared.actors[1].user_id))
    decoded = AccessToken(str(fresh))
    assert decoded["user_id"] == prepared.actors[1].user_id
