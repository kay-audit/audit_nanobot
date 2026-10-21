"""Exercise the standalone shell entrypoint with real DOCX parsing, no gateway."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_doc_history import TARGET, REVIEW, docx, sample

RUNNER = Path(__file__).resolve().parents[1] / "test_history.sh"
BASH = os.environ.get("HISTORY_TEST_BASH") or ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" and Path("C:/Program Files/Git/bin/bash.exe").is_file() else shutil.which("bash"))


@unittest.skipUnless(BASH, "Requires bash")
class HistoryShellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="history shell ")
        self.root = Path(self.temp.name)
        self.input = self.root / "входящие документы"
        self.input.mkdir()
        (self.input / TARGET).write_bytes(docx([TARGET[:-5], "Статья 5. Реклама"]))
        (self.input / REVIEW).write_bytes(docx(sample()))
        self.env = dict(os.environ, PYTHON_BIN=Path(sys.executable).as_posix(), PYTHONUTF8="1")

    def tearDown(self):
        self.temp.cleanup()

    def run_shell(self, *args):
        return subprocess.run([BASH, RUNNER.as_posix(), *args], cwd=self.root,
                              env=self.env, capture_output=True, text=True, timeout=30)

    def test_help_and_default_folder_without_target(self):
        help_result = self.run_shell("--help")
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("tests/manual/input", help_result.stdout)
        result = self.run_shell()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "needs_input")

    def test_raw_from_other_cwd_with_spaces_and_unicode(self):
        result = self.run_shell("--raw", "--input", str(self.input), "--target", TARGET)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(len(payload["changes"]), 2)
        self.assertEqual(payload["changes"][1]["before"], "Срок 10 дней")
        self.assertEqual(payload["changes"][1]["after"], "Срок 20 дней")

    def test_separate_relative_files_and_equals_options(self):
        result = self.run_shell("--raw", "--input=" + str(Path(self.input.name) / TARGET),
                                "--input", str(Path(self.input.name) / REVIEW),
                                "--target=" + TARGET, "--session-key=another-test", "--limit", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)["changes"]), 1)

    def test_preserves_cli_failure_exit_code(self):
        result = self.run_shell("--unknown-option")
        self.assertEqual(result.returncode, 2)

    def test_explicit_python_with_spaces_and_missing_python(self):
        interpreter = self.root / "python with spaces"
        if os.name == "nt":
            interpreter.write_text('#!/usr/bin/env bash\nexec "' + Path(sys.executable).as_posix() + '" "$@"\n', encoding="utf-8")
        else:
            interpreter.symlink_to(sys.executable)
        self.env["PYTHON_BIN"] = interpreter.as_posix()
        result = self.run_shell("--raw", "--input", str(self.input))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "needs_input")
        self.env["PYTHON_BIN"] = str(self.root / "missing-python")
        result = self.run_shell()
        self.assertEqual(result.returncode, 127)
        self.assertIn("PYTHON_BIN", result.stderr)


if __name__ == "__main__":
    unittest.main()
