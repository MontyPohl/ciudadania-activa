"""Runner de evaluación y cálculo de métricas (S8-E1).

El comportamiento del adaptador falso no es el objeto de estudio: para probar la
aritmética se usa un adaptador de guion que devuelve exactamente lo que cada
prueba necesita. Así el evaluador queda validado con independencia de lo bien o
mal que puntúe cualquier proveedor.
"""

import inspect
import json
from decimal import Decimal

import pytest

from apps.ai_client_app.adapters import FakeAiClassificationAdapter
from apps.ai_client_app.evaluation import runner as runner_module
from apps.ai_client_app.evaluation.dataset import load_dataset
from apps.ai_client_app.evaluation.reporting import report_to_dict, report_to_json
from apps.ai_client_app.evaluation.runner import build_request, evaluate_dataset
from apps.ai_client_app.evaluation.scoring import CA4_MIN_CONCORDANCE_PCT, percentage

from ._ast_checks import code_identifiers, imported_names


@pytest.fixture(scope="module")
def dataset():
    return load_dataset()


class AdapterDeGuion:
    """Devuelve la respuesta que la prueba dicte para cada caso, por id."""

    def __init__(self, dataset, *, mutar=None, raw_por_defecto=None, error=None):
        self._dataset = dataset
        self._mutar = mutar or (lambda case, salida: salida)
        self._raw = raw_por_defecto
        self._error = error
        self.requests = []

    def classify(self, request) -> str:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        if self._raw is not None:
            return self._raw

        case = self._dataset.cases[len(self.requests) - 1]
        salida = {
            "category_slug": case.expected.category_slug,
            "institution_slug": case.expected.institution_slug,
            "priority": case.expected.priority,
            "confidence": 0.9,
            "inconsistency": case.expected.inconsistency,
            "justification": "Justificación sintética del benchmark.",
        }
        return json.dumps(self._mutar(case, salida))


def evaluar(dataset, adapter, nombre="scripted"):
    return evaluate_dataset(dataset, adapter, adapter_name=nombre, model_name="modelo-de-prueba")


# --------------------------------------------------------------------------- #
# Aislamiento del runner
# --------------------------------------------------------------------------- #


def test_el_runner_no_conoce_al_proveedor_ni_al_runtime():
    importados = imported_names(runner_module)
    identificadores = code_identifiers(runner_module)

    assert "google" not in importados
    assert "genai" not in importados
    assert "get_runtime_adapter" not in identificadores
    assert "GeminiAiClassificationAdapter" not in identificadores
    assert "settings" not in identificadores


def test_el_runner_no_toca_la_base_de_datos_ni_celery():
    importados = imported_names(runner_module)

    assert not any("models" in nombre for nombre in importados)
    assert "celery" not in importados
    assert "django.db" not in importados


def test_el_runner_usa_el_parser_real():
    assert "parse_classification_response" in imported_names(runner_module)


def test_el_runner_no_introduce_datos_variables():
    """Sin relojes ni azar: dos mediciones deben ser comparables."""
    importados = imported_names(runner_module)
    identificadores = code_identifiers(runner_module)

    assert importados.isdisjoint({"datetime", "time", "random", "uuid", "os"})
    assert identificadores.isdisjoint({"now", "utcnow", "uuid4", "getenv"})


# --------------------------------------------------------------------------- #
# La petición no filtra la respuesta
# --------------------------------------------------------------------------- #


def test_la_peticion_no_lleva_las_etiquetas_de_oro(dataset):
    """Preguntar con la respuesta delante no mediría nada."""
    adapter = AdapterDeGuion(dataset)

    evaluar(dataset, adapter)

    for request, case in zip(adapter.requests, dataset.cases, strict=True):
        serializado = json.dumps(
            {
                "description": request.description,
                "current_category_slug": request.current_category_slug,
                "current_institution_slug": request.current_institution_slug,
                "allowed_category_slugs": list(request.allowed_category_slugs),
                "allowed_institution_slugs": list(request.allowed_institution_slugs),
                "allowed_priorities": list(request.allowed_priorities),
            }
        )
        assert case.id not in serializado
        assert case.notes not in serializado or not case.notes
        campos = set(type(request).__dataclass_fields__)
        assert campos.isdisjoint({"expected", "ground_truth", "case_id", "notes", "score"})


def test_la_peticion_usa_la_taxonomia_congelada(dataset):
    request = build_request(dataset.cases[0], dataset)

    assert request.allowed_category_slugs == dataset.catalog.category_slugs
    assert request.allowed_institution_slugs == dataset.catalog.institution_slugs
    assert request.allowed_priorities == dataset.catalog.priorities
    assert request.image_bytes == b""
    assert request.image_mime_type is None


# --------------------------------------------------------------------------- #
# Aritmética de la concordancia
# --------------------------------------------------------------------------- #


def test_un_adaptador_perfecto_alcanza_el_cien_por_cien(dataset):
    informe = evaluar(dataset, AdapterDeGuion(dataset))

    assert informe.concordant_cases == informe.total_cases
    assert informe.concordance_pct == Decimal("100.00")
    assert informe.thesis_concordance_pct == Decimal("100.00")
    assert informe.errors_count == 0


def test_un_adaptador_siempre_equivocado_obtiene_cero(dataset):
    def arruinar(case, salida):
        otra = next(s for s in dataset.catalog.category_slugs if s != case.expected.category_slug)
        return {**salida, "category_slug": otra}

    informe = evaluar(dataset, AdapterDeGuion(dataset, mutar=arruinar))

    assert informe.concordant_cases == 0
    assert informe.concordance_pct == Decimal("0.00")
    assert informe.category_accuracy_pct == Decimal("0.00")


def test_el_umbral_de_ochenta_y_cinco_se_calcula_exactamente():
    """Frontera de CA-4 sin afirmar que ningún modelo la haya alcanzado."""
    assert percentage(17, 20) == Decimal("85.00")
    assert percentage(17, 20) == CA4_MIN_CONCORDANCE_PCT
    assert percentage(16, 20) == Decimal("80.00")
    assert percentage(69, 80) == Decimal("86.25")


def test_el_porcentaje_se_redondea_a_dos_decimales():
    assert percentage(1, 3) == Decimal("33.33")
    assert percentage(2, 3) == Decimal("66.67")
    assert percentage(0, 28) == Decimal("0.00")
    assert percentage(5, 0) == Decimal("0.00")


def test_una_fraccion_exacta_del_dataset_produce_el_porcentaje_esperado(dataset):
    """Falla en 7 de 28 casos: exactamente el 75 %."""
    fallidos = {c.id for c in dataset.cases[:7]}

    def arruinar(case, salida):
        if case.id not in fallidos:
            return salida
        otra = next(p for p in dataset.catalog.priorities if p != case.expected.priority)
        return {**salida, "priority": otra}

    informe = evaluar(dataset, AdapterDeGuion(dataset, mutar=arruinar))

    assert informe.concordant_cases == 21
    assert informe.concordance_pct == Decimal("75.00")


# --------------------------------------------------------------------------- #
# Métricas por campo
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("campo", "metrica"),
    [
        ("category_slug", "category_accuracy_pct"),
        ("institution_slug", "institution_accuracy_pct"),
        ("priority", "priority_accuracy_pct"),
        ("inconsistency", "inconsistency_accuracy_pct"),
    ],
)
def test_un_error_en_un_campo_solo_baja_su_metrica(dataset, campo, metrica):
    alternativas = {
        "category_slug": lambda c: next(
            s for s in dataset.catalog.category_slugs if s != c.expected.category_slug
        ),
        "institution_slug": lambda c: next(
            s for s in dataset.catalog.institution_slugs if s != c.expected.institution_slug
        ),
        "priority": lambda c: next(
            p for p in dataset.catalog.priorities if p != c.expected.priority
        ),
        "inconsistency": lambda c: not c.expected.inconsistency,
    }

    def arruinar(case, salida):
        return {**salida, campo: alternativas[campo](case)}

    informe = evaluar(dataset, AdapterDeGuion(dataset, mutar=arruinar))

    assert informe.concordant_cases == 0, "el caso debe dejar de concordar"
    assert getattr(informe, metrica) == Decimal("0.00")

    otras = {
        "category_accuracy_pct",
        "institution_accuracy_pct",
        "priority_accuracy_pct",
        "inconsistency_accuracy_pct",
    } - {metrica}
    for nombre in otras:
        assert getattr(informe, nombre) == Decimal("100.00"), f"{nombre} no debía verse afectada"


def test_la_confianza_no_participa_en_la_concordancia(dataset):
    """Es una estimación del modelo, no una etiqueta humana."""
    informe_alto = evaluar(dataset, AdapterDeGuion(dataset))
    informe_bajo = evaluar(
        dataset, AdapterDeGuion(dataset, mutar=lambda c, s: {**s, "confidence": 0.01})
    )

    assert informe_alto.concordance_pct == informe_bajo.concordance_pct == Decimal("100.00")


def test_la_justificacion_no_se_puntua_semanticamente(dataset):
    """Dos redacciones distintas pueden ser igual de correctas."""
    informe = evaluar(
        dataset,
        AdapterDeGuion(dataset, mutar=lambda c, s: {**s, "justification": "Otro texto distinto."}),
    )

    assert informe.concordance_pct == Decimal("100.00")
    assert all(r.justification_length == len("Otro texto distinto.") for r in informe.cases)


# --------------------------------------------------------------------------- #
# Fallos: cuentan como no concordantes y no detienen la corrida
# --------------------------------------------------------------------------- #


def test_una_respuesta_invalida_cuenta_como_fallo_y_no_aborta(dataset):
    informe = evaluar(dataset, AdapterDeGuion(dataset, raw_por_defecto="no soy json"))

    assert len(informe.cases) == informe.total_cases == len(dataset.cases)
    assert informe.concordant_cases == 0
    assert informe.errors_count == len(dataset.cases)
    assert all(r.error_type == "ClassificationParseError" for r in informe.cases)


def test_un_fallo_del_adaptador_cuenta_como_fallo_y_no_aborta(dataset):
    class ProveedorCaido(Exception):
        pass

    informe = evaluar(dataset, AdapterDeGuion(dataset, error=ProveedorCaido("caído")))

    assert len(informe.cases) == len(dataset.cases)
    assert informe.errors_count == len(dataset.cases)
    assert all(r.error_type == "ProveedorCaido" for r in informe.cases)


def test_la_corrida_continua_tras_un_caso_fallido(dataset):
    primero = dataset.cases[0].id

    def arruinar(case, salida):
        return {**salida, "campo_de_mas": 1} if case.id == primero else salida

    informe = evaluar(dataset, AdapterDeGuion(dataset, mutar=arruinar))

    assert informe.errors_count == 1
    assert informe.concordant_cases == len(dataset.cases) - 1
    assert informe.cases[0].error_type == "ClassificationParseError"


def test_el_error_no_incluye_traza_ni_contenido(dataset):
    informe = evaluar(dataset, AdapterDeGuion(dataset, raw_por_defecto='{"secreto": "dato"}'))

    for resultado in informe.cases:
        assert resultado.error_type == "ClassificationParseError"
        assert "secreto" not in json.dumps(report_to_dict(informe))


# --------------------------------------------------------------------------- #
# Determinismo de la serialización
# --------------------------------------------------------------------------- #


def test_dos_ejecuciones_producen_el_mismo_json(dataset):
    primera = report_to_json(evaluar(dataset, AdapterDeGuion(dataset)))
    segunda = report_to_json(evaluar(dataset, AdapterDeGuion(dataset)))

    assert primera == segunda


def test_el_json_no_contiene_marcas_de_tiempo_ni_rutas(dataset):
    serializado = report_to_json(evaluar(dataset, AdapterDeGuion(dataset)))

    for prohibido in ("timestamp", "generated_at", "duration", "/home/", "hostname", "elapsed"):
        assert prohibido not in serializado


def test_el_informe_no_declara_si_supera_el_umbral(dataset):
    """Con un doble, ese campo invitaría a leerlo como si dijera algo del modelo."""
    campos = set(type(evaluar(dataset, AdapterDeGuion(dataset))).__dataclass_fields__)

    assert "meets_threshold" not in campos
    assert "passed" not in campos


def test_los_porcentajes_se_serializan_como_cadena(dataset):
    datos = report_to_dict(evaluar(dataset, AdapterDeGuion(dataset)))

    assert datos["concordance_pct"] == "100.00"
    assert isinstance(datos["concordance_pct"], str)


# --------------------------------------------------------------------------- #
# El adaptador falso real del proyecto
# --------------------------------------------------------------------------- #


def test_el_adaptador_falso_recorre_el_dataset_completo(dataset):
    informe = evaluate_dataset(
        dataset,
        FakeAiClassificationAdapter(),
        adapter_name="fake",
        model_name="fake-deterministic",
    )

    assert informe.total_cases == len(dataset.cases)
    assert len(informe.cases) == len(dataset.cases)
    assert informe.errors_count == 0, "el fake debe producir siempre salida parseable"


def test_el_adaptador_falso_es_determinista_entre_corridas(dataset):
    def correr():
        return report_to_json(
            evaluate_dataset(
                dataset,
                FakeAiClassificationAdapter(),
                adapter_name="fake",
                model_name="fake-deterministic",
            )
        )

    assert correr() == correr()


def test_el_dataset_no_esta_disenado_para_favorecer_al_fake(dataset):
    """El fake repite la categoría del ciudadano y siempre propone MEDIA.

    Que puntúe bajo es la señal de que el benchmark exige corregir de verdad, no
    un defecto. Si algún día superase el umbral sin cambiar de comportamiento,
    habría que sospechar del dataset.
    """
    informe = evaluate_dataset(
        dataset,
        FakeAiClassificationAdapter(),
        adapter_name="fake",
        model_name="fake-deterministic",
    )

    assert informe.concordance_pct < CA4_MIN_CONCORDANCE_PCT


def test_el_informe_conserva_el_orden_del_dataset(dataset):
    informe = evaluar(dataset, AdapterDeGuion(dataset))

    assert [r.id for r in informe.cases] == [c.id for c in dataset.cases]


def test_la_firma_del_runner_exige_adaptador_explicito():
    firma = inspect.signature(evaluate_dataset)

    assert "adapter" in firma.parameters
    assert firma.parameters["adapter_name"].kind is inspect.Parameter.KEYWORD_ONLY
    assert firma.parameters["model_name"].kind is inspect.Parameter.KEYWORD_ONLY
