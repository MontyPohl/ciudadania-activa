"""El diagnóstico remoto no arranca fuera del contexto explícitamente autorizado."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

from tools.s15.remote_db_probe import percentile


class RemoteProbeTests(unittest.TestCase):
    def test_guard_rejects_local_invocation_before_django_or_network(self):
        script = Path(__file__).with_name("remote_db_probe.py")
        for arguments in ([], ["--confirm-existing-deployment-readonly"], ["--samples", "100"]):
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [sys.executable, str(script), *arguments],
                    env={"PATH": os.environ.get("PATH", "")},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("Traceback", result.stderr)

    def test_percentile_is_interpolated_and_keeps_extremes(self):
        self.assertEqual(percentile([10, 0, 5], 0.5), 5)
        self.assertAlmostEqual(percentile([10, 0, 5], 0.99), 9.9)


if __name__ == "__main__":
    unittest.main()
