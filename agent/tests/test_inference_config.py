"""Startup configuration tests; never start Gradio or load model weights."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which("bash"), "requires bash")
class InferenceConfigTest(unittest.TestCase):
    def test_path_argument_is_exported_and_overrides_environment(self):
        script = Path(__file__).resolve().parents[1] / "inference.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "model cache with spaces"
            cache.mkdir()
            executable = root / "python-probe"
            executable.write_text('#!/bin/sh\nprintf "%s\\n" "$MINICHECK_CACHE_DIR" "$MEDRAX_AGENT_MODE"\n')
            executable.chmod(0o700)
            env = {**os.environ, "MEDRAX_PYTHON": str(executable), "MINICHECK_CACHE_DIR": "/not-used", "ANSWER_AUDIT_MODE": "strict"}
            result = subprocess.run(["bash", str(script), str(cache)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines()[-2:], [str(cache), "medical_evidence"])
            result = subprocess.run(["bash", str(script), str(cache), "http://127.0.0.1:18080"], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("HIS endpoint: http://127.0.0.1:18080", result.stdout)
            env["MINICHECK_CACHE_DIR"] = str(cache)
            result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(cache), result.stdout)
            result = subprocess.run(["bash", str(script), str(root / "missing")], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("does not exist", result.stderr)
            env["ANSWER_AUDIT_MODE"] = "observe"
            result = subprocess.run(["bash", str(script), str(root / "missing")], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Answer audit mode: observe", result.stdout)
            env["ANSWER_AUDIT_MODE"] = "invalid"
            result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
