"""Binding del timeout efectivo en la ejecución canónica de recuperación.

Ninguna prueba construye un cliente real ni llama al proveedor: las factorías del
SDK entran por parámetro y se sustituyen por espías. Lo que se comprueba es que el
número que gobierna la petición y el que queda archivado en la evidencia salgan
de la misma instancia de `RecoveryProtocol`.
"""

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from apps.ai_client_app.evaluation import execution as execution_module
from apps.ai_client_app.evaluation.dataset import DATASET_PATH, load_dataset
from apps.ai_client_app.evaluation.execution import (
    build_provider_adapter,
    load_verified_dataset,
    run_recovery_batch,
    verify_prompt,
)
from apps.ai_client_app.evaluation.recovery import (
    BATCH_SIZE,
    R1,
    R2,
    RecoveryProtocolError,
    batch_to_json,
    select_batch,
)
from apps.ai_client_app.evaluation.scoring import CA4_MIN_CONCORDANCE_PCT

from ._ast_checks import code_identifiers

# Con la forma de una clave de Google pero inventada: la suite nunca ve una real.
CLAVE_FALSA = "AIzaSyD9fakeKEYfakeKEYfakeKEY0123456789x"


@pytest.fixture(scope="module")
def dataset():
    return load_dataset()


class ClienteEspia:
    """Doble del cliente del SDK. Registra con qué se construyó y nunca sale a red."""

    def __init__(self, *, api_key, timeout_seconds):
        self.api_key_recibida = api_key
        self.timeout_seconds = timeout_seconds


class AdapterEspia:
    """Doble del adaptador real: responde la etiqueta de oro del caso que le toca."""

    def __init__(self, *, client, model, casos=()):
        self.client = client
        self.model = model
        self._casos = list(casos)
        self.llamadas = 0

    def classify(self, request) -> str:
        case = self._casos[self.llamadas]
        self.llamadas += 1
        return json.dumps(
            {
                "category_slug": case.expected.category_slug,
                "institution_slug": case.expected.institution_slug,
                "priority": case.expected.priority,
                "confidence": 0.9,
                "inconsistency": case.expected.inconsistency,
                "justification": "Justificación sintética del benchmark.",
            }
        )


class RelojFalso:
    def __init__(self, paso_ns=5_000_000):
        self._ahora = 0
        self._paso = paso_ns

    def __call__(self) -> int:
        valor = self._ahora
        self._ahora += self._paso
        return valor


def _factorias(dataset, batch_number):
    """Devuelve las factorías espía y el registro de lo que se les pasó."""
    registro = {}

    def client_factory(**kwargs):
        registro["client"] = kwargs
        return ClienteEspia(**kwargs)

    def adapter_factory(**kwargs):
        registro["adapter"] = kwargs
        return AdapterEspia(casos=select_batch(dataset, batch_number), **kwargs)

    return client_factory, adapter_factory, registro


def _ejecutar(dataset, protocol, batch_number=1, **extra):
    client_factory, adapter_factory, registro = _factorias(dataset, batch_number)
    lote = run_recovery_batch(
        protocol=protocol,
        batch_number=batch_number,
        api_key=CLAVE_FALSA,
        client_factory=client_factory,
        adapter_factory=adapter_factory,
        sleep=lambda segundos: None,
        monotonic_ns=RelojFalso(),
        **extra,
    )
    return lote, registro


# --------------------------------------------------------------------------- #
# El timeout de la petición y el de la evidencia son el mismo número
# --------------------------------------------------------------------------- #


def test_r2_construye_el_cliente_con_60_segundos(dataset):
    lote, registro = _ejecutar(dataset, R2)

    assert registro["client"]["timeout_seconds"] == 60
    assert lote.request_timeout_seconds == 60
    # La igualdad es el punto: no hay dos fuentes que puedan discrepar.
    assert registro["client"]["timeout_seconds"] == lote.request_timeout_seconds


def test_r2_construye_el_adapter_con_el_modelo_del_protocolo(dataset):
    lote, registro = _ejecutar(dataset, R2)

    assert registro["adapter"]["model"] == "gemini-3.6-flash"
    assert lote.model_name == registro["adapter"]["model"]


def test_r1_construye_el_cliente_con_20_segundos(dataset):
    """R1 no se reinterpreta: su timeout histórico sigue gobernando su ejecución."""
    lote, registro = _ejecutar(dataset, R1)

    assert registro["client"]["timeout_seconds"] == 20
    assert lote.request_timeout_seconds == 20


def test_el_lote_queda_marcado_con_el_protocolo_ejecutado(dataset):
    lote_r2, _ = _ejecutar(dataset, R2)
    lote_r1, _ = _ejecutar(dataset, R1)

    assert lote_r2.protocol_version == "s8-e2-r2"
    assert lote_r2.status == "R2_IN_PROGRESS"
    assert lote_r1.protocol_version == "s8-e2-r1"
    assert lote_r1.status == "R1_IN_PROGRESS"


def test_el_protocolo_es_obligatorio_y_no_tiene_default(dataset):
    """Olvidar `protocol=R2` no puede degradar silenciosamente a R1."""
    with pytest.raises(TypeError):
        run_recovery_batch(batch_number=1, api_key=CLAVE_FALSA)


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("model_name", "gemini-2.5-flash"),
        ("request_timeout_seconds", 20),
        ("inter_request_delay_seconds", 0),
        ("dataset_sha256", "0" * 64),
        ("protocol", R1),
    ],
)
def test_no_hay_ruta_para_pasar_a_mano_lo_que_deriva_del_protocolo(dataset, campo, valor):
    """Cerrar la segunda fuente: si no se puede pasar, no puede discrepar."""
    client_factory, adapter_factory, _ = _factorias(dataset, 1)

    with pytest.raises(TypeError):
        run_recovery_batch(
            protocol=R2,
            batch_number=1,
            api_key=CLAVE_FALSA,
            client_factory=client_factory,
            adapter_factory=adapter_factory,
            **{campo: valor},
        )


def test_no_existe_ruta_r2_que_evidencie_60_habiendo_pedido_con_20(dataset):
    """El invariante central, comprobado sobre todos los protocolos conocidos."""
    for protocolo in (R1, R2):
        lote, registro = _ejecutar(dataset, protocolo)

        assert registro["client"]["timeout_seconds"] == protocolo.request_timeout_seconds
        assert lote.request_timeout_seconds == protocolo.request_timeout_seconds
        assert registro["adapter"]["model"] == protocolo.model_name


# --------------------------------------------------------------------------- #
# Seguridad y fronteras
# --------------------------------------------------------------------------- #


def test_la_clave_no_aparece_en_la_evidencia(dataset):
    lote, _ = _ejecutar(dataset, R2)
    serializado = batch_to_json(lote)

    assert CLAVE_FALSA not in serializado
    assert "api_key" not in serializado


def test_la_clave_no_queda_en_el_adaptador_ni_en_su_repr(dataset):
    _, registro = _ejecutar(dataset, R2)
    cliente = registro["adapter"]["client"]

    # El doble sí la guarda, para poder afirmar que llegó; lo que importa es que el
    # adaptador no recibe la clave, solo el cliente ya construido.
    assert cliente.api_key_recibida == CLAVE_FALSA
    assert "api_key" not in registro["adapter"]
    assert CLAVE_FALSA not in repr(registro["adapter"]["model"])


def test_una_clave_vacia_se_rechaza_antes_de_construir_nada(dataset):
    client_factory, adapter_factory, registro = _factorias(dataset, 1)

    with pytest.raises(ValueError):
        build_provider_adapter(
            protocol=R2,
            api_key="",
            client_factory=client_factory,
            adapter_factory=adapter_factory,
        )

    assert registro == {}


def test_la_ejecucion_pide_cada_caso_una_sola_vez(dataset):
    lote, _ = _ejecutar(dataset, R2)

    assert len(lote.results) == BATCH_SIZE
    assert lote.batch_case_ids == tuple(c.id for c in select_batch(dataset, 1))


def test_el_modulo_de_ejecucion_no_importa_el_sdk_al_cargarse():
    """El SDK de Google se importa dentro de la función, no al importar el módulo.

    Así una recuperación no ejecutada no puede hacer fallar el arranque, y las
    pruebas no necesitan el SDK presente para cubrir esta capa.
    """
    identificadores = code_identifiers(execution_module)

    assert "genai" not in identificadores
    assert "interactions" not in identificadores
    assert "create" not in identificadores


def test_el_modulo_de_ejecucion_no_lee_la_clave_del_entorno():
    identificadores = code_identifiers(execution_module)

    assert "getenv" not in identificadores
    assert "environ" not in identificadores
    assert "settings" not in identificadores


def test_la_ejecucion_no_toca_celery():
    identificadores = code_identifiers(execution_module)

    assert "delay" not in identificadores
    assert "apply_async" not in identificadores
    assert "shared_task" not in identificadores


def test_un_dataset_en_otra_ruta_con_los_mismos_bytes_es_valido(dataset, tmp_path):
    """Lo que se exige son los bytes, no la ubicación del archivo."""
    copia = tmp_path / "copia_identica.json"
    copia.write_bytes(DATASET_PATH.read_bytes())

    lote, _ = _ejecutar(dataset, R2, dataset_path=copia)

    assert lote.dataset_sha256 == R2.dataset_sha256


# --------------------------------------------------------------------------- #
# Provenance: lo declarado en la evidencia es lo realmente usado
# --------------------------------------------------------------------------- #

DATASET_SHA_CONGELADO = "3bc54d591dc104aeabeecb2f5cc7c7a38e70ba042e7f045321ab33ae9b5efad4"
PROMPT_SHA_CONGELADO = "f3c803984806a3b934db0417f6cc3ce59a49cdde1e30b4c86b862c2e11c665ca"


def _dataset_adulterado(tmp_path, mutar):
    """Copia del dataset con una modificación, conservando `dataset_version`."""
    crudo = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    mutar(crudo)
    destino = tmp_path / "adulterado.json"
    destino.write_text(json.dumps(crudo, ensure_ascii=False), encoding="utf-8")
    return destino


def test_el_dataset_canonico_pasa_el_preflight():
    dataset, digest = load_verified_dataset(protocol=R2)

    assert digest == DATASET_SHA_CONGELADO == R2.dataset_sha256
    assert dataset.dataset_version == "s8-evaluation-v1"
    assert len(dataset.cases) == 28


def test_un_solo_byte_distinto_en_el_dataset_se_rechaza(tmp_path):
    destino = tmp_path / "un_byte.json"
    crudo = bytearray(DATASET_PATH.read_bytes())
    # Un espacio añadido: JSON equivalente, bytes distintos. El hash es sobre bytes.
    crudo.extend(b" ")
    destino.write_bytes(bytes(crudo))

    with pytest.raises(RecoveryProtocolError, match="no es el prerregistrado"):
        load_verified_dataset(protocol=R2, dataset_path=destino)


def test_mismo_dataset_version_con_contenido_distinto_se_rechaza(tmp_path):
    """El agujero que este endurecimiento cierra, convertido en regresión.

    Antes era posible ejecutar con este archivo y archivar el hash prerregistrado:
    el input real no era el prerregistrado y la evidencia no lo delataba.
    """
    destino = _dataset_adulterado(
        tmp_path,
        lambda d: d["cases"][0].__setitem__("description", "DESCRIPCION ADULTERADA"),
    )
    crudo = json.loads(destino.read_text(encoding="utf-8"))

    assert crudo["dataset_version"] == "s8-evaluation-v1"
    with pytest.raises(RecoveryProtocolError):
        load_verified_dataset(protocol=R2, dataset_path=destino)


def test_el_rechazo_del_dataset_ocurre_antes_de_construir_el_cliente(dataset, tmp_path):
    """Un input equivocado cuesta cero llamadas y cero cuota."""
    destino = _dataset_adulterado(
        tmp_path,
        lambda d: d["cases"][0].__setitem__("description", "OTRA DESCRIPCION"),
    )
    client_factory, adapter_factory, registro = _factorias(dataset, 1)

    with pytest.raises(RecoveryProtocolError):
        run_recovery_batch(
            protocol=R2,
            batch_number=1,
            api_key=CLAVE_FALSA,
            dataset_path=destino,
            client_factory=client_factory,
            adapter_factory=adapter_factory,
        )

    assert registro == {}


def test_la_evidencia_archiva_el_digest_realmente_verificado(dataset):
    lote, _ = _ejecutar(dataset, R2)
    _, digest = load_verified_dataset(protocol=R2)

    assert lote.dataset_sha256 == digest
    assert lote.dataset_sha256 == DATASET_SHA_CONGELADO


def test_el_prompt_real_reproduce_el_hash_congelado():
    assert verify_prompt(protocol=R2) == PROMPT_SHA_CONGELADO
    assert R2.prompt_sha256 == PROMPT_SHA_CONGELADO
    assert R1.prompt_sha256 == PROMPT_SHA_CONGELADO


def test_un_prompt_alterado_se_rechaza():
    """Se altera el protocolo, no el archivo: `classification_v1.txt` es intocable."""
    protocolo_torcido = replace(R2, prompt_sha256="0" * 64)

    with pytest.raises(RecoveryProtocolError, match="El prompt no es el prerregistrado"):
        verify_prompt(protocol=protocolo_torcido)


def test_el_rechazo_del_prompt_ocurre_antes_de_construir_el_cliente(dataset):
    protocolo_torcido = replace(R2, prompt_sha256="0" * 64)
    client_factory, adapter_factory, registro = _factorias(dataset, 1)

    with pytest.raises(RecoveryProtocolError):
        run_recovery_batch(
            protocol=protocolo_torcido,
            batch_number=1,
            api_key=CLAVE_FALSA,
            client_factory=client_factory,
            adapter_factory=adapter_factory,
        )

    assert registro == {}


def test_la_evidencia_registra_el_hash_del_prompt(dataset):
    lote, _ = _ejecutar(dataset, R2)
    serializado = json.loads(batch_to_json(lote))

    assert serializado["prompt_sha256"] == PROMPT_SHA_CONGELADO
    assert serializado["prompt_version"] == "s8-classification-v1"


def test_el_dataset_se_lee_una_sola_vez(dataset, monkeypatch):
    """Sin TOCTOU: hashear y volver a abrir dejaría una ventana entre lecturas."""
    lecturas = []
    original = Path.read_bytes

    def espia(self, *args, **kwargs):
        lecturas.append(str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", espia)
    load_verified_dataset(protocol=R2)

    assert sum(1 for r in lecturas if r.endswith("dataset_s8_v1.json")) == 1


def test_los_invariantes_sustantivos_siguen_intactos(dataset):
    lote, registro = _ejecutar(dataset, R2)

    assert R2.model_name == "gemini-3.6-flash"
    assert R2.prompt_version == "s8-classification-v1"
    assert R2.dataset_version == "s8-evaluation-v1"
    assert R2.request_timeout_seconds == 60
    assert R1.request_timeout_seconds == 20
    assert R2.inter_request_delay_seconds == 20
    assert CA4_MIN_CONCORDANCE_PCT == Decimal("85.00")
    assert lote.batch_case_ids == tuple(c.id for c in select_batch(dataset, 1))
    assert registro["adapter"]["model"] == "gemini-3.6-flash"
