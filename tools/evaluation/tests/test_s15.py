from __future__ import annotations

import copy
import importlib.util
import io
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.evaluation import s15_common as common
from tools.evaluation import s15_run as runner
from tools.evaluation import s15_score as scorer

HAS_DJANGO = importlib.util.find_spec("django") is not None


class EvaluationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.exclusions = {
            "schema_version": "s15-exclusions-v1",
            "scene_ids": [],
            "origin_ids": [],
            "original_sha256": [],
            "normalized_sha256": [],
        }
        self.gold = {
            "schema_version": "s15-gold-v1",
            "dataset_id": "synthetic-transport",
            "review": {
                "reviewer_ids": ["synthetic-a", "synthetic-b"],
                "rubric": "classification-v2",
                "adjudicated_before_prediction": True,
            },
            "labels": [],
        }
        mapping = common.prompt_catalog()
        self.dataset = {
            "schema_version": common.VERSION,
            "dataset_id": "synthetic-transport",
            "state": "frozen",
            "purpose": "offline-test",
            "frozen_n": 3,
            "protocol": {
                "protocol_id": "s15-test",
                "frozen_at": "2026-01-01T00:00:00Z",
                "error_policy": "invalidate",
                "attempt_limit": 1,
                "primary_metric": "exact-category-institution-priority-before-policy",
                "secondary_metric": "exact-category-priority-before-policy",
                "threshold_percent": 85,
                "sdk_max_retries": 0,
                "script_max_retries": 0,
                "celery_used": False,
            },
            "provider": {
                "name": "openai",
                "model": "gpt-test-offline",
                "prompt_version": "v2",
                "reasoning_effort": "",
                "timeout_seconds": 30,
            },
            "implementation_sha256": common.implementation_hashes(),
            "catalog": {
                "captured_at": "2026-01-01T00:00:00Z",
                "environment": "synthetic-test",
                "source": "active-orm+GET /api/v1/public/categories/",
                "categories": [
                    {"slug": slug, "institution_slug": institution}
                    for slug, institution in sorted(mapping.items())
                ],
                "institutions": sorted(set(mapping.values())),
                "public_category_slugs": sorted(mapping),
            },
            "gold_sha256": None,
            "exclusions_sha256": common.object_hash(self.exclusions),
            "cases": [],
        }
        from PIL import Image

        for index in range(3):
            image = Image.new("RGB", (16 + index, 16), (index * 50, 50, 30))
            photo_path = self.directory / f"transport-{index}.png"
            image.save(photo_path, format="PNG")
            image.close()
            original, normalized, mime = common.normalize_photo(photo_path, "image/png")
            case_id = f"test-{index}"
            self.dataset["cases"].append(
                {
                    "case_id": case_id,
                    "scene_id": f"scene-{index}",
                    "origin_id": f"origin-{index}",
                    "request": {
                        "description": "Transporte sintético; no es evaluación visual.",
                        "current_category_slug": "baches",
                        "current_institution_slug": "municipalidad-de-asuncion",
                    },
                    "photo": {
                        "original_path": photo_path.name,
                        "original_sha256": common.digest(original),
                        "normalized_sha256": common.digest(normalized),
                        "normalized_bytes": len(normalized),
                        "mime_type": mime,
                    },
                    "review": {
                        "rights_confirmed": True,
                        "pii_reviewed": True,
                        "scene_reserved": True,
                        "not_used_for_prompt_tuning": True,
                    },
                }
            )
            self.gold["labels"].append(
                {
                    "case_id": case_id,
                    "category_slug": "baches",
                    "institution_slug": "municipalidad-de-asuncion",
                    "priority": "MEDIA",
                }
            )
        self.write_inputs()

    def write_inputs(self):
        self.dataset["gold_sha256"] = common.object_hash(self.gold)
        self.dataset["exclusions_sha256"] = common.object_hash(self.exclusions)
        for filename, obj in (
            ("dataset.json", self.dataset),
            ("gold.json", self.gold),
            ("exclusions.json", self.exclusions),
        ):
            (self.directory / filename).write_bytes(common.canonical(obj))

    def validate(self):
        return common.validate_dataset(
            *(self.directory / name for name in ("dataset.json", "gold.json", "exclusions.json"))
        )

    def record(self, index=0, **prediction):
        record = runner.blank_record(self.dataset["cases"][index], self.dataset)
        record.update(
            status="parsed",
            error_code=None,
            response_sha256="a" * 64,
            prediction={
                "category_slug": "baches",
                "institution_slug": "municipalidad-de-asuncion",
                "priority": "MEDIA",
                "confidence": "0.99",
                "inconsistency": False,
                **prediction,
            },
        )
        return record

    def all_records(self):
        return {f"test-{index}": self.record(index) for index in range(3)}

    def test_full_offline_validation_uses_real_photo_normalizer(self):
        dataset, gold, prepared = self.validate()
        self.assertEqual(len(prepared), 3)
        self.assertEqual(dataset, self.dataset)
        self.assertEqual(gold, self.gold)

    def test_pending_template_cannot_be_mistaken_for_frozen_dataset(self):
        self.dataset["state"] = "preparation"
        self.write_inputs()
        with self.assertRaisesRegex(common.ValidationError, "pendiente"):
            self.validate()

    def test_no_omitted_or_extra_case_or_gold(self):
        for target in ("cases", "labels"):
            with self.subTest(target=target):
                original = copy.deepcopy(self.dataset if target == "cases" else self.gold)
                (self.dataset if target == "cases" else self.gold)[target].pop()
                self.write_inputs()
                with self.assertRaises(common.ValidationError):
                    self.validate()
                if target == "cases":
                    self.dataset = original
                else:
                    self.gold = original

    def test_original_and_normalized_hash_and_bytes_mime_checked(self):
        for field, value in (
            ("original_sha256", "0" * 64),
            ("normalized_sha256", "0" * 64),
            ("normalized_bytes", 1),
            ("mime_type", "image/jpeg"),
        ):
            with self.subTest(field=field):
                old = self.dataset["cases"][0]["photo"][field]
                self.dataset["cases"][0]["photo"][field] = value
                self.write_inputs()
                with self.assertRaises(common.ValidationError):
                    self.validate()
                self.dataset["cases"][0]["photo"][field] = old

    def test_real_size_limit_not_declared_size(self):
        path = self.directory / self.dataset["cases"][0]["photo"]["original_path"]
        path.write_bytes(b"X" * (common.MAX_PHOTO_BYTES + 1))
        with self.assertRaises(common.ValidationError):
            self.validate()

    def test_scene_duplicate_and_prior_origin_rejected(self):
        self.dataset["cases"][1]["scene_id"] = self.dataset["cases"][0]["scene_id"]
        self.write_inputs()
        with self.assertRaises(common.ValidationError):
            self.validate()
        self.dataset["cases"][1]["scene_id"] = "scene-1"
        self.exclusions["origin_ids"] = ["origin-0"]
        self.write_inputs()
        with self.assertRaises(common.ValidationError):
            self.validate()

    def test_gold_and_private_fields_cannot_enter_request(self):
        self.dataset["cases"][0]["request"]["gold"] = "MEDIA"
        self.write_inputs()
        with self.assertRaises(common.ValidationError):
            self.validate()

    def test_prompt_and_catalog_drift_rejected(self):
        self.dataset["catalog"]["categories"][0]["institution_slug"] = "ande"
        self.write_inputs()
        with self.assertRaises(common.ValidationError):
            self.validate()
        self.dataset["catalog"]["categories"][0]["institution_slug"] = common.prompt_catalog()[
            self.dataset["catalog"]["categories"][0]["slug"]
        ]
        self.dataset["implementation_sha256"]["parser"] = "f" * 64
        self.write_inputs()
        with self.assertRaises(common.ValidationError):
            self.validate()

    def test_actual_api_contract_has_no_institution_relation(self):
        api = [
            {"id": "synthetic", "name": "Synthetic", "slug": slug}
            for slug in self.dataset["catalog"]["public_category_slugs"]
        ]
        path = self.directory / "api.json"
        path.write_text(json.dumps(api))
        common.check_public_api(self.dataset["catalog"], path)
        api[0]["institution_slug"] = "ande"
        path.write_text(json.dumps(api))
        with self.assertRaises(common.ValidationError):
            common.check_public_api(self.dataset["catalog"], path)

    def test_primary_triple_and_secondary_pair_are_different(self):
        records = self.all_records()
        records["test-0"] = self.record(institution_slug="ande")
        score = scorer.score_records(self.dataset, self.gold, records)
        self.assertEqual(score["primary"]["correct"], 2)
        self.assertEqual(score["secondary"]["correct"], 3)
        self.assertEqual(score["verdict"], "REPLAY_ONLY")

    def test_unknown_never_matches_null(self):
        self.gold["labels"][0].update(category_slug=None, institution_slug=None)
        records = self.all_records()
        records["test-0"] = self.record(
            category_slug=common.UNKNOWN_SLUG, institution_slug=common.UNKNOWN_SLUG
        )
        score = scorer.score_records(self.dataset, self.gold, records)
        self.assertEqual(score["primary"]["correct"], 2)
        records["test-0"] = self.record(category_slug=None, institution_slug=None)
        self.assertEqual(
            scorer.score_records(self.dataset, self.gold, records)["primary"]["correct"], 3
        )

    def test_policy_does_not_affect_primary_confidence_and_inconsistency(self):
        records = self.all_records()
        records["test-0"] = self.record(confidence="0.1", inconsistency=True)
        self.assertEqual(
            scorer.score_records(self.dataset, self.gold, records)["primary"]["correct"], 3
        )

    def test_missing_case_never_changes_denominator_and_policy_is_explicit(self):
        records = self.all_records()
        del records["test-0"]
        score = scorer.score_records(self.dataset, self.gold, records)
        self.assertEqual(score["denominator"], 3)
        self.assertFalse(score["run_valid"])
        self.assertIsNone(score["primary"]["threshold_met"])
        self.dataset["protocol"]["error_policy"] = "count_as_miss"
        score = scorer.score_records(self.dataset, self.gold, records)
        self.assertTrue(score["run_valid"])
        self.assertEqual(score["primary"]["correct"], 2)
        self.assertEqual(score["errors"], {"missing": 1})

    def test_exact_85_percent_integer_boundary_no_rounding(self):
        self.dataset["frozen_n"] = 101
        self.dataset["cases"] = [
            dict(self.dataset["cases"][i % 3], case_id=f"test-{i}") for i in range(101)
        ]
        self.gold["labels"] = [
            dict(self.gold["labels"][i % 3], case_id=f"test-{i}") for i in range(101)
        ]
        records = {}
        for index in range(101):
            case = self.dataset["cases"][index]
            record = self.record(0, priority="MEDIA" if index < 85 else "ALTA")
            record.update(
                case_id=case["case_id"],
                request_sha256=common.request_hash(case, self.dataset["catalog"]),
                image_original_sha256=case["photo"]["original_sha256"],
                image_normalized_sha256=case["photo"]["normalized_sha256"],
            )
            records[case["case_id"]] = record
        score = scorer.score_records(self.dataset, self.gold, records, execution_mode="live")
        self.assertEqual(score["required_correct"], 86)
        self.assertEqual(score["verdict"], "FAIL")
        records["test-85"]["prediction"]["priority"] = "MEDIA"
        self.assertEqual(
            scorer.score_records(self.dataset, self.gold, records, execution_mode="live")[
                "verdict"
            ],
            "PASS",
        )

    def test_unknown_tokens_are_not_zero(self):
        score = scorer.score_records(self.dataset, self.gold, self.all_records())
        self.assertIsNone(score["usage"]["total_tokens"]["observed_sum"])
        self.assertEqual(score["usage"]["total_tokens"]["unknown_attempts"], 3)
        records = self.all_records()
        records["test-0"]["usage"]["total_tokens"] = 0
        score = scorer.score_records(self.dataset, self.gold, records)
        self.assertEqual(score["usage"]["total_tokens"]["observed_sum"], 0)
        self.assertEqual(score["usage"]["total_tokens"]["unknown_attempts"], 2)

    def test_extra_and_duplicate_replay_cases_rejected(self):
        item = {
            "case_id": "test-0",
            "attempt": 1,
            "status": "missing",
            "raw_response": None,
            "returned_model": None,
            "usage": dict(runner.UNKNOWN_USAGE),
            "elapsed_ms": None,
        }
        payload = {
            "schema_version": common.REPLAY_VERSION,
            "dataset_sha256": common.object_hash(self.dataset),
            "source": "offline-fixture",
            "responses": [item, item],
        }
        path = self.directory / "responses.json"
        path.write_bytes(common.canonical(payload))
        with self.assertRaises(common.ValidationError):
            runner.load_replay(path, self.dataset)
        payload["responses"] = [dict(item, case_id="unknown")]
        path.write_bytes(common.canonical(payload))
        with self.assertRaises(common.ValidationError):
            runner.load_replay(path, self.dataset)

    def test_output_must_be_external_and_durable_even_through_symlink(self):
        for path in (common.ROOT / "var/evidence", self.directory, Path("/var/tmp/s15-test")):
            with self.subTest(path=path):
                with self.assertRaises(common.ValidationError):
                    common.safe_output_directory(path)
        link = self.directory / "link"
        link.symlink_to(common.ROOT, target_is_directory=True)
        with self.assertRaises(common.ValidationError):
            common.safe_output_directory(link / "var/evidence")

    def test_atomic_evidence_permissions_and_no_partial_file(self):
        path = self.directory / "cases" / "test-0" / "attempt-001.json"
        common.atomic_json(path, {"status": "started"})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(path.parent.parent.stat().st_mode), 0o700)
        common.atomic_json(path, {"status": "parsed"})
        self.assertEqual(common.read_json(path)["status"], "parsed")
        self.assertEqual([entry.name for entry in path.parent.iterdir()], ["attempt-001.json"])

    def test_request_is_production_allowlist_without_gold_or_paths(self):
        request = common.build_request(
            self.dataset["cases"][0], b"NORMALIZED", self.dataset["catalog"]
        )
        from dataclasses import asdict

        self.assertEqual(
            set(asdict(request)),
            {
                "description",
                "current_category_slug",
                "current_institution_slug",
                "allowed_category_slugs",
                "allowed_institution_slugs",
                "allowed_priorities",
                "image_bytes",
                "image_mime_type",
            },
        )
        self.assertEqual(request.image_bytes, b"NORMALIZED")

    def test_parser_errors_do_not_echo_private_arguments(self):
        output = io.StringIO()
        with patch("sys.stderr", output), self.assertRaises(SystemExit):
            runner.main(["--private-unknown", "sk-SECRET-CANARY-/home/private-person"])
        self.assertNotIn("SECRET-CANARY", output.getvalue())
        self.assertNotIn("private-person", output.getvalue())

    def test_no_live_mode_without_opt_in_and_no_calls_in_default_validate(self):
        args = [
            "--dataset",
            str(self.directory / "dataset.json"),
            "--gold",
            str(self.directory / "gold.json"),
            "--exclusions",
            str(self.directory / "exclusions.json"),
        ]
        with (
            patch.object(
                runner, "build_live_adapter", side_effect=AssertionError("must never call")
            ),
            patch("builtins.print"),
        ):
            self.assertEqual(runner.main(args), 0)
            self.assertEqual(runner.main(args + ["--mode", "live"]), 2)
        self.assertFalse((self.directory / "run.json").exists())

    def test_interruption_has_durable_started_marker_and_resume_never_repeats_call(self):
        self.dataset["protocol"]["error_policy"] = "count_as_miss"
        prepared = [(case, b"unused") for case in self.dataset["cases"]]
        output = self.directory / "interrupted-output"
        output.mkdir()

        class InterruptedAdapter:
            def classify_with_metadata(self, _request):
                raise KeyboardInterrupt("private interruption: /home/person sk-secret")

        with patch.object(runner, "safe_output_directory", return_value=output):
            with self.assertRaises(KeyboardInterrupt):
                runner.execute(
                    self.dataset,
                    self.gold,
                    prepared,
                    output,
                    mode="live",
                    adapter=InterruptedAdapter(),
                )
            started = common.read_json(output / "cases/test-0/attempt-001.json")
            self.assertEqual(started["status"], "started")
            self.assertEqual(common.read_json(output / "run.json")["run_state"], "running")
            self.assertNotIn("sk-secret", json.dumps(started))
            from apps.ai_client_app.exceptions import TransientAiProviderError

            class UnavailableAdapter:
                def classify_with_metadata(self, _request):
                    raise TransientAiProviderError("sensitive provider failure")

            score = runner.execute(
                self.dataset,
                self.gold,
                prepared,
                output,
                mode="live",
                adapter=UnavailableAdapter(),
                resume=True,
            )
            self.assertEqual(score["errors"], {"interrupted": 1, "provider_error": 2})
            self.assertEqual(score["denominator"], 3)
            self.assertEqual(score["primary"]["correct"], 0)
            self.assertIsNone(score["usage"]["total_tokens"]["observed_sum"])

    def test_internal_or_configuration_failure_is_terminal_even_with_resume_count_as_miss(self):
        from apps.ai_client_app.exceptions import PermanentAiProviderError

        class HttpFailure(Exception):
            status_code = 401

        class InvalidConfigurationAdapter:
            def classify_with_metadata(self, _request):
                raise PermanentAiProviderError(
                    "private configuration /home/person sk-secret"
                ) from HttpFailure()

        class BrokenAdapter:
            def classify_with_metadata(self, _request):
                raise RuntimeError("private internal error /home/person sk-secret")

        self.dataset["protocol"]["error_policy"] = "count_as_miss"
        prepared = [(case, b"unused") for case in self.dataset["cases"]]
        for label, adapter, error in (
            ("configuration", InvalidConfigurationAdapter(), common.ValidationError),
            ("internal", BrokenAdapter(), RuntimeError),
        ):
            with self.subTest(failure=label):
                output = self.directory / label
                output.mkdir()
                with (
                    patch.object(runner, "safe_output_directory", return_value=output),
                    patch.object(scorer, "safe_output_directory", return_value=output),
                ):
                    with self.assertRaises(error):
                        runner.execute(
                            self.dataset, self.gold, prepared, output, mode="live", adapter=adapter
                        )
                    meta = common.read_json(output / "run.json")
                    self.assertEqual(meta["run_state"], "invalidated")
                    record = common.read_json(output / "cases/test-0/attempt-001.json")
                    self.assertEqual(record["status"], "instrument_error")
                    score = common.read_json(output / "score.json")
                    self.assertFalse(score["run_valid"])
                    self.assertEqual(score["verdict"], "INVALID_RUN")
                    self.assertNotIn("sk-secret", json.dumps([meta, record, score]))
                    with patch.object(
                        runner, "live_case", side_effect=AssertionError("must not call")
                    ):
                        with self.assertRaisesRegex(common.ValidationError, "invalidada"):
                            runner.execute(
                                self.dataset,
                                self.gold,
                                prepared,
                                output,
                                mode="live",
                                adapter=adapter,
                                resume=True,
                            )
                        with self.assertRaisesRegex(common.ValidationError, "invalidada"):
                            scorer.rescore_run(self.dataset, self.gold, output)
                    # El CLI de resume también rechaza, con cliente sustituido y cero red.
                    args = [
                        "--dataset",
                        "unused",
                        "--gold",
                        "unused",
                        "--exclusions",
                        "unused",
                        "--mode",
                        "live",
                        "--allow-live-provider",
                        "--public-api-capture",
                        "unused",
                        "--output",
                        str(output),
                        "--resume",
                    ]
                    with (
                        patch.object(
                            runner,
                            "validate_dataset",
                            return_value=(self.dataset, self.gold, prepared),
                        ),
                        patch.object(runner, "build_live_adapter", return_value=adapter),
                        patch("builtins.print"),
                    ):
                        self.assertEqual(runner.main(args), 2)
                    self.assertEqual(
                        common.read_json(output / "run.json")["run_state"], "invalidated"
                    )

    def test_integrity_failure_in_existing_record_invalidates_run_permanently(self):
        self.dataset["protocol"]["error_policy"] = "count_as_miss"
        output = self.directory / "integrity"
        output.mkdir()
        prepared = [(case, b"unused") for case in self.dataset["cases"]]
        with patch.object(runner, "safe_output_directory", return_value=output):
            runner.execute(self.dataset, self.gold, prepared, output, mode="replay", replay={})
            record_path = output / "cases/test-0/attempt-001.json"
            record = common.read_json(record_path)
            record["request_sha256"] = "0" * 64
            common.atomic_json(record_path, record)
            with self.assertRaises(common.ValidationError):
                runner.execute(
                    self.dataset, self.gold, prepared, output, mode="replay", replay={}, resume=True
                )
            self.assertEqual(common.read_json(output / "run.json")["run_state"], "invalidated")
            self.assertEqual(common.read_json(output / "score.json")["verdict"], "INVALID_RUN")
            with self.assertRaisesRegex(common.ValidationError, "invalidada"):
                runner.execute(
                    self.dataset, self.gold, prepared, output, mode="replay", replay={}, resume=True
                )

    def test_rescore_complete_requires_every_attempt_and_rejects_started(self):
        self.dataset["protocol"]["error_policy"] = "count_as_miss"
        self.dataset["frozen_n"] = 10
        self.dataset["cases"] = [
            dict(self.dataset["cases"][i % 3], case_id=f"test-{i}") for i in range(10)
        ]
        self.gold["labels"] = [
            dict(self.gold["labels"][i % 3], case_id=f"test-{i}") for i in range(10)
        ]
        self.dataset["gold_sha256"] = common.object_hash(self.gold)
        prepared = [(case, b"unused") for case in self.dataset["cases"]]

        def synthetic_correct(case, _image, dataset, _adapter):
            record = runner.blank_record(case, dataset)
            record.update(
                status="parsed",
                error_code=None,
                response_sha256="a" * 64,
                prediction=dict(self.record()["prediction"]),
            )
            return record

        for kind in ("absent-file", "started", "explicit-missing"):
            with self.subTest(record_state=kind):
                output = self.directory / kind
                output.mkdir()
                with (
                    patch.object(runner, "safe_output_directory", return_value=output),
                    patch.object(scorer, "safe_output_directory", return_value=output),
                    patch.object(runner, "live_case", side_effect=synthetic_correct),
                ):
                    initial = runner.execute(
                        self.dataset, self.gold, prepared, output, mode="live", adapter=object()
                    )
                    self.assertEqual(initial["verdict"], "PASS")
                    self.assertEqual(initial["primary"]["correct"], 10)
                    path = output / "cases/test-0/attempt-001.json"
                    if kind == "absent-file":
                        path.unlink()
                    else:
                        status = "started" if kind == "started" else "missing"
                        common.atomic_json(
                            path,
                            runner.blank_record(self.dataset["cases"][0], self.dataset, status),
                        )
                    if kind == "explicit-missing":
                        score = scorer.rescore_run(self.dataset, self.gold, output)
                        self.assertTrue(score["run_valid"])
                        self.assertEqual(score["verdict"], "PASS")
                        self.assertEqual(score["primary"]["correct"], 9)
                        self.assertEqual(score["denominator"], 10)
                        self.assertEqual(score["errors"], {"missing": 1})
                        self.assertEqual(
                            common.read_json(output / "run.json")["run_state"], "complete"
                        )
                    else:
                        with self.assertRaises(common.ValidationError):
                            scorer.rescore_run(self.dataset, self.gold, output)
                        self.assertEqual(
                            common.read_json(output / "run.json")["run_state"], "invalidated"
                        )
                        stale_score = common.read_json(output / "score.json")
                        self.assertEqual(stale_score["verdict"], "INVALID_RUN")
                        self.assertFalse(stale_score["run_valid"])
                        with self.assertRaisesRegex(common.ValidationError, "invalidada"):
                            scorer.rescore_run(self.dataset, self.gold, output)

    def test_instrument_error_never_passes_even_with_95_percent_correct(self):
        self.dataset["protocol"]["error_policy"] = "count_as_miss"
        self.dataset["frozen_n"] = 20
        self.dataset["cases"] = [
            dict(self.dataset["cases"][i % 3], case_id=f"test-{i}") for i in range(20)
        ]
        self.gold["labels"] = [
            dict(self.gold["labels"][i % 3], case_id=f"test-{i}") for i in range(20)
        ]
        records = {}
        for case in self.dataset["cases"]:
            record = self.record(0)
            record.update(
                case_id=case["case_id"],
                request_sha256=common.request_hash(case, self.dataset["catalog"]),
                image_original_sha256=case["photo"]["original_sha256"],
                image_normalized_sha256=case["photo"]["normalized_sha256"],
            )
            records[case["case_id"]] = record
        records["test-0"] = runner.blank_record(
            self.dataset["cases"][0], self.dataset, "instrument_error"
        )
        score = scorer.score_records(self.dataset, self.gold, records, execution_mode="live")
        self.assertEqual(score["primary"]["correct"], 19)
        self.assertFalse(score["run_valid"])
        self.assertIsNone(score["primary"]["threshold_met"])
        self.assertEqual(score["verdict"], "INVALID_RUN")

    @unittest.skipUnless(
        HAS_DJANGO, "El parser vigente requiere Django (ejecutar también en backend Docker)."
    )
    def test_replay_uses_production_parser_redacts_response_and_unknown_slugs(self):
        raw = json.dumps(
            {
                "category_slug": "sk-sensitive-key",
                "institution_slug": "ande",
                "priority": "MEDIA",
                "confidence": 0.8,
                "inconsistency": False,
                "justification": "PII /home/private-person token sk-secret",
            }
        )
        record = runner.minimize_response(
            runner.blank_record(self.dataset["cases"][0], self.dataset), raw, self.dataset
        )
        self.assertEqual(record["status"], "parsed")
        self.assertEqual(record["prediction"]["category_slug"], common.UNKNOWN_SLUG)
        self.assertNotIn("private-person", json.dumps(record))
        self.assertNotIn("sk-sensitive", json.dumps(record))
        self.assertEqual(record["response_sha256"], common.digest(raw.encode()))
        self.assertIsNone(runner.safe_model("sk-sensitive-key"))

    @unittest.skipUnless(
        HAS_DJANGO, "El parser vigente requiere Django (ejecutar también en backend Docker)."
    )
    def test_replay_valid_invalid_timeout_missing_and_incremental_resume(self):
        self.dataset["protocol"]["error_policy"] = "count_as_miss"
        self.dataset["frozen_n"] = 4
        extra = copy.deepcopy(self.dataset["cases"][2])
        extra["case_id"] = "test-3"
        self.dataset["cases"].append(extra)
        self.gold["labels"].append(dict(self.gold["labels"][2], case_id="test-3"))
        raw = json.dumps(
            {
                "category_slug": "baches",
                "institution_slug": "municipalidad-de-asuncion",
                "priority": "MEDIA",
                "confidence": 0.99,
                "inconsistency": False,
                "justification": "Transporte sintético.",
            }
        )
        base = {
            "attempt": 1,
            "returned_model": "gpt-test-offline",
            "usage": dict(runner.UNKNOWN_USAGE),
            "elapsed_ms": 4,
        }
        payload = {
            "schema_version": common.REPLAY_VERSION,
            "dataset_sha256": common.object_hash(self.dataset),
            "source": "offline-fixture",
            "responses": [
                {**base, "case_id": "test-0", "status": "response", "raw_response": raw},
                {
                    **base,
                    "case_id": "test-1",
                    "status": "response",
                    "raw_response": "not-json PII secret",
                },
                {**base, "case_id": "test-2", "status": "timeout", "raw_response": None},
            ],
        }
        prepared = [(case, b"unused-replay") for case in self.dataset["cases"]]
        with (
            patch.object(runner, "safe_output_directory", return_value=self.directory),
            patch.object(scorer, "safe_output_directory", return_value=self.directory),
        ):
            # El temporal ya contiene entradas; usar un subdirectorio vacío.
            output = self.directory / "output"
            output.mkdir()
            with (
                patch.object(runner, "safe_output_directory", return_value=output),
                patch.object(scorer, "safe_output_directory", return_value=output),
            ):
                score = runner.execute(
                    self.dataset,
                    self.gold,
                    prepared,
                    output,
                    mode="replay",
                    replay_payload=payload,
                    replay={item["case_id"]: item for item in payload["responses"]},
                )
                self.assertEqual(score["primary"]["correct"], 1)
                self.assertEqual(score["denominator"], 4)
                self.assertEqual(score["errors"], {"parse_error": 1, "timeout": 1, "missing": 1})
                self.assertEqual(score["verdict"], "REPLAY_ONLY")
                self.assertEqual(scorer.rescore_run(self.dataset, self.gold, output), score)
                with patch.object(
                    runner, "replay_case", side_effect=AssertionError("must not repeat")
                ):
                    resumed = runner.execute(
                        self.dataset,
                        self.gold,
                        prepared,
                        output,
                        mode="replay",
                        replay_payload=payload,
                        replay={item["case_id"]: item for item in payload["responses"]},
                        resume=True,
                    )
                self.assertEqual(resumed, score)
                self.assertNotIn("secret", (output / "cases/test-1/attempt-001.json").read_text())
                self.assertEqual(stat.S_IMODE((output / "run.json").stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
