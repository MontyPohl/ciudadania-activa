"""Pruebas puras con SQL ejecutado sobre una base efímera, sin Django ni red."""

import importlib.util
import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "s15_reconcile", Path(__file__).with_name("reconcile_creation.py")
)
reconcile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reconcile)
DATABASE = {"name": "ciudadania_s15_load", "host": "ca-s15-db"}


class SqlCursor:
    """Solo adapta los placeholders; las consultas se ejecutan realmente."""

    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, query, parameters):
        self.cursor.execute(query.replace("%s", "?"), parameters)

    def fetchall(self):
        return self.cursor.fetchall()


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        actors = [
            {
                "id": f"actor_{n}",
                "userId": str(uuid.UUID(int=n + 1)),
                "role": "citizen",
                "verified": True,
            }
            for n in range(10)
        ]
        manifest = {
            "schemaVersion": 1,
            "runId": "run-local-1",
            "datasetSha256": "a" * 64,
            "actorsFile": "/host/path/actors.json",
            "create": {
                "payloads": [
                    {
                        "caseId": f"create_{n}",
                        "actorId": actors[n // 5]["id"],
                        "description": f"Caso sintético {n}",
                        "photo": {"sha256": f"{n + 1:064x}"},
                    }
                    for n in range(50)
                ]
            },
        }
        self.manifest_bytes = json.dumps(manifest).encode()
        self.actor_bytes = json.dumps({"schemaVersion": 1, "actors": actors}).encode()
        self.inputs = reconcile.parse_inputs(self.manifest_bytes, self.actor_bytes)
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("""
            CREATE TABLE users(id TEXT,role TEXT,verification_status TEXT,is_active BOOLEAN);
            CREATE TABLE reports(id TEXT,citizen_id TEXT,source TEXT,description TEXT);
            CREATE TABLE report_media(id TEXT,report_id TEXT,file_hash TEXT,
                                      media_type TEXT,uploaded_by_id TEXT);
            CREATE TABLE report_status_history(report_id TEXT,previous_status TEXT,
                new_status TEXT,changed_by_id TEXT,resolution_cycle INTEGER);
            CREATE TABLE reward_transactions(report_id TEXT,event_type TEXT,user_id TEXT,
                status TEXT,impact_delta INTEGER,credit_delta INTEGER,institution_id TEXT,
                credited_at TEXT,voided_at TEXT,reversed_at TEXT,resolution_cycle INTEGER,
                confirmation_id TEXT,redemption_id TEXT,related_transaction_id TEXT);
        """)
        self.db.executemany(
            "INSERT INTO users VALUES(?,?,?,?)",
            [(a["userId"], "citizen", "APROBADO", True) for a in actors],
        )
        self.cursor = SqlCursor(self.db.cursor())

    def tearDown(self):
        self.db.close()

    def persist(self, n):
        case = self.inputs["cases"][n]
        report_id, media_id = str(uuid.UUID(int=1000 + n)), str(uuid.UUID(int=2000 + n))
        self.db.execute(
            "INSERT INTO reports VALUES(?,?,?,?)",
            (report_id, case["userId"], "platform", case["description"]),
        )
        self.db.execute(
            "INSERT INTO report_media VALUES(?,?,?,?,?)",
            (media_id, report_id, case["photoSha256"], "FOTO", case["userId"]),
        )
        self.db.execute(
            "INSERT INTO report_status_history VALUES(?,'','CREADO',?,0)",
            (report_id, case["userId"]),
        )
        self.db.execute(
            "INSERT INTO reward_transactions VALUES(?,'REPORT_CREATED',?,'PENDIENTE',"
            "20,20,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL)",
            (report_id, case["userId"]),
        )
        return report_id

    def test_before_after_exports_only_real_rows_including_unobserved_http(self):
        baseline = reconcile.collect_before(self.cursor, self.inputs, DATABASE)
        first, last = self.persist(0), self.persist(49)
        result = reconcile.collect_after(self.cursor, self.inputs, baseline, DATABASE)
        self.assertEqual(result["method"], "postgresql-before-after")
        self.assertEqual(result["persistedCases"], 2)
        self.assertEqual(len(result["missingCases"]), 48)
        self.assertEqual({r["reportId"] for r in result["rows"]}, {first, last})
        self.assertEqual(
            result["rows"][0],
            {
                "caseId": "create_0",
                "actorId": "actor_0",
                "reportId": first,
                "photoSha256": f"{1:064x}",
                "reportRows": 1,
                "mediaRows": 1,
                "initialHistoryRows": 1,
                "pendingCreditRows": 1,
                "impactDelta": 20,
                "creditDelta": 20,
            },
        )

    def test_before_rejects_preexisting_hash_or_case(self):
        self.persist(0)
        with self.assertRaisesRegex(reconcile.ReconcileError, "preexistente"):
            reconcile.collect_before(self.cursor, self.inputs, DATABASE)
        self.db.execute("DELETE FROM report_media")
        with self.assertRaisesRegex(reconcile.ReconcileError, "preexistente"):
            reconcile.collect_before(self.cursor, self.inputs, DATABASE)

    def test_after_rejects_tampered_baseline_and_recomputed_nonempty_baseline(self):
        baseline = reconcile.collect_before(self.cursor, self.inputs, DATABASE)
        baseline["rows"][0]["caseRows"] = 1
        with self.assertRaisesRegex(reconcile.ReconcileError, "Hash"):
            reconcile.collect_after(self.cursor, self.inputs, baseline, DATABASE)
        document = {k: v for k, v in baseline.items() if k != "baselineSha256"}
        baseline["baselineSha256"] = reconcile._sha(reconcile._canonical(document))
        with self.assertRaisesRegex(reconcile.ReconcileError, "ausencia"):
            reconcile.collect_after(self.cursor, self.inputs, baseline, DATABASE)

    def test_after_binds_exact_manifest_bytes_and_actor_uuids(self):
        baseline = reconcile.collect_before(self.cursor, self.inputs, DATABASE)
        changed = reconcile.parse_inputs(self.manifest_bytes + b"\n", self.actor_bytes)
        with self.assertRaisesRegex(reconcile.ReconcileError, "manifiesto"):
            reconcile.collect_after(self.cursor, changed, baseline, DATABASE)
        actors = json.loads(self.actor_bytes)
        actors["actors"][0]["userId"] = str(uuid.UUID(int=9999))
        changed = reconcile.parse_inputs(self.manifest_bytes, json.dumps(actors).encode())
        with self.assertRaisesRegex(reconcile.ReconcileError, "manifiesto"):
            reconcile.collect_after(self.cursor, changed, baseline, DATABASE)

    def test_after_rejects_duplicate_initial_history_and_credit_delta(self):
        baseline = reconcile.collect_before(self.cursor, self.inputs, DATABASE)
        report_id = self.persist(0)
        self.db.execute("INSERT INTO report_status_history SELECT * FROM report_status_history")
        with self.assertRaisesRegex(reconcile.ReconcileError, "historial"):
            reconcile.collect_after(self.cursor, self.inputs, baseline, DATABASE)
        self.db.execute(
            "DELETE FROM report_status_history "
            "WHERE rowid=(SELECT MAX(rowid) FROM report_status_history)"
        )
        self.db.execute(
            "UPDATE reward_transactions SET credit_delta=21 WHERE report_id=?", (report_id,)
        )
        with self.assertRaisesRegex(reconcile.ReconcileError, "ledger"):
            reconcile.collect_after(self.cursor, self.inputs, baseline, DATABASE)

    def test_after_rejects_duplicate_hash_and_wrong_author(self):
        baseline = reconcile.collect_before(self.cursor, self.inputs, DATABASE)
        report_id = self.persist(0)
        self.db.execute("INSERT INTO report_media SELECT * FROM report_media")
        with self.assertRaisesRegex(reconcile.ReconcileError, "duplicado"):
            reconcile.collect_after(self.cursor, self.inputs, baseline, DATABASE)
        self.db.execute(
            "DELETE FROM report_media WHERE rowid=(SELECT MAX(rowid) FROM report_media)"
        )
        self.db.execute(
            "UPDATE reports SET citizen_id=? WHERE id=?", (str(uuid.UUID(int=9999)), report_id)
        )
        with self.assertRaises(reconcile.ReconcileError):
            reconcile.collect_after(self.cursor, self.inputs, baseline, DATABASE)

    def test_private_file_permissions_symlinks_and_exclusive_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(reconcile, "ALLOWED_ROOT", root):
                target = root / "baseline.json"
                reconcile.write_private(target, {"sample": True})
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads(reconcile.read_private(target)), {"sample": True})
                with self.assertRaisesRegex(reconcile.ReconcileError, "sobrescribe"):
                    reconcile.write_private(target, {})
                linked = root / "link.json"
                linked.symlink_to(target)
                with self.assertRaisesRegex(reconcile.ReconcileError, "simbólicos"):
                    reconcile.read_private(linked)
                target.chmod(0o644)
                with self.assertRaisesRegex(reconcile.ReconcileError, "privado"):
                    reconcile.read_private(target)

    def test_database_guard_rejects_production_shared_host_and_debug_false(self):
        settings = SimpleNamespace(
            DEBUG=True,
            DATABASES={
                "default": {
                    "NAME": "ciudadania_s15_load",
                    "HOST": "ca-s15-db",
                    "ENGINE": "django.contrib.gis.db.backends.postgis",
                }
            },
        )
        connection = SimpleNamespace(vendor="postgresql")
        self.assertEqual(reconcile.guard_database(settings, connection), DATABASE)
        settings.DEBUG = False
        with self.assertRaises(reconcile.ReconcileError):
            reconcile.guard_database(settings, connection)
        settings.DEBUG = True
        settings.DATABASES["default"]["NAME"] = "production"
        with self.assertRaises(reconcile.ReconcileError):
            reconcile.guard_database(settings, connection)
        settings.DATABASES["default"]["NAME"] = "ciudadania_s15"
        settings.DATABASES["default"]["HOST"] = "ca-s12-db"
        with self.assertRaises(reconcile.ReconcileError):
            reconcile.guard_database(settings, connection)


if __name__ == "__main__":
    unittest.main()
