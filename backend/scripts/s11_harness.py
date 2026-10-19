#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from apps.experiments_app.harness import FullStackHarness, build_plan  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ejecuta posteriormente EX-02…EX-08 sobre el stack aislado S11."
    )
    parser.add_argument("--scenario", required=True, choices=[f"EX-{n:02d}" for n in range(2, 9)])
    parser.add_argument(
        "--nonce", required=True, help="Identificador controlado y único del intento"
    )
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument(
        "--custody-approved",
        action="store_true",
        help="Confirma que las gates de custodia S11-A P02/P03 permiten la ejecución",
    )
    args = parser.parse_args()
    if not args.custody_approved:
        parser.error("--custody-approved es obligatorio; no se asume autorización de custodia")
    hmac_secret = os.environ.get("S11_DOCUMENT_HMAC_SECRET", "")
    if not hmac_secret.strip():
        parser.error("S11_DOCUMENT_HMAC_SECRET debe contener un secreto efímero no versionado")

    plan = build_plan(
        scenario_id=args.scenario,
        execution_nonce=args.nonce,
        expected_git_sha=args.expected_git_sha,
    )
    result = FullStackHarness(REPO_ROOT).execute(
        plan,
        environment={
            "S11_EXPERIMENTS_ENABLED": "true",
            "S11_ENVIRONMENT": "isolated-experiment",
            "S11_CUSTODY_APPROVED": "true",
            "S11_DOCUMENT_HMAC_SECRET": hmac_secret,
        },
    )
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
