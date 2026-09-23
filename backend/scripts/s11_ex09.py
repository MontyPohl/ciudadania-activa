#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from apps.experiments_app.ex09_launcher import ACTIONS, LauncherError, launch  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lanza un paso de EX-09 (S11-C3) en el contenedor efímero.",
        epilog="Los argumentos tras la acción se pasan a `manage.py s11_ex09 <acción>`.",
    )
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument("--expected-git-sha", required=True)
    args, rest = parser.parse_known_args()
    try:
        return launch(
            args.action,
            rest,
            repo_root=REPO_ROOT,
            expected_git_sha=args.expected_git_sha,
            environ=os.environ,
        )
    except LauncherError as exc:
        parser.exit(2, f"s11_ex09: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
