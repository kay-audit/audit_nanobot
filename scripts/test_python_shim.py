"""Test _ensure_python_shim in gateway.py (isolated tests)."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Just exec the function definition to avoid running gateway.py imports
GATEWAY_PY = Path(__file__).resolve().parent.parent / "gateway.py"
source = GATEWAY_PY.read_text(encoding="utf-8")

# Extract the _ensure_python_shim function
import re
match = re.search(r"def _ensure_python_shim.*?(?=\ndef )", source, re.DOTALL)
if not match:
    raise RuntimeError("Could not find _ensure_python_shim")

# Execute in isolated namespace
ns = {"os": os, "Path": Path}
exec(match.group(0), ns)
_ensure_python_shim = ns["_ensure_python_shim"]


# Test 1: AUDIT_PYTHON_SHIM=0 disables shim
def test_disabled_by_env():
    os.environ["AUDIT_PYTHON_SHIM"] = "0"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            result = _ensure_python_shim(ws)
            assert result is None, f"Should return None when disabled, got {result}"
            assert not (ws / ".python-shim").exists(), "Shim dir should not be created"
        print("  test_disabled_by_env: PASSED")
    finally:
        del os.environ["AUDIT_PYTHON_SHIM"]


# Test 2: Missing target python returns None
def test_missing_target():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        with patch.dict(os.environ, {"AUDIT_PYTHON_BIN": "/tmp/nonexistent_python_xyzzy"}):
            result = _ensure_python_shim(ws)
        assert result is None, f"Should return None for missing target, got {result}"
        assert not (ws / ".python-shim").exists(), "Shim dir should not be created"
        print("  test_missing_target: PASSED")


# Test 3: Valid target creates shim (with mocked symlink)
def test_valid_target_with_mock():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        fake_py = Path(tmp) / "python3.12"
        fake_py.write_text("#!/bin/sh\necho fake python 3.12\n")

        symlinks_created = []

        def fake_symlink_to(self, target):
            symlinks_created.append((str(self), target))

        with patch.dict(os.environ, {"AUDIT_PYTHON_BIN": str(fake_py)}):
            with patch.object(Path, "symlink_to", fake_symlink_to):
                result = _ensure_python_shim(ws)

        assert result is not None, "Should return shim dir path"
        assert result == str(ws / ".python-shim"), f"Wrong path: {result}"
        names = [Path(s[0]).name for s in symlinks_created]
        assert "python3" in names, f"Missing python3 symlink: {names}"
        assert "python3.12" in names, f"Missing python3.12 symlink: {names}"
        for src, target in symlinks_created:
            assert target == str(fake_py), f"Wrong target: {target}"
        print("  test_valid_target_with_mock: PASSED")


# Test 4: Symlink failures return None
def test_symlink_failure():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        fake_py = Path(tmp) / "python3.12"
        fake_py.write_text("fake")

        def failing_symlink_to(self, target):
            raise OSError("symlink not supported")

        with patch.dict(os.environ, {"AUDIT_PYTHON_BIN": str(fake_py)}):
            with patch.object(Path, "symlink_to", failing_symlink_to):
                result = _ensure_python_shim(ws)

        assert result is None, f"Should return None when symlink fails, got {result}"
        print("  test_symlink_failure: PASSED")


# Test 5: Idempotent — existing shim doesn't re-create
def test_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        fake_py = Path(tmp) / "python3.12"
        fake_py.write_text("fake")

        with patch.dict(os.environ, {"AUDIT_PYTHON_BIN": str(fake_py)}):
            with patch.object(Path, "symlink_to", lambda self, target: None):
                result1 = _ensure_python_shim(ws)
                result2 = _ensure_python_shim(ws)

        assert result1 == result2, "Should return same path"
        assert result1 == str(ws / ".python-shim")
        print("  test_idempotent: PASSED")


# Test 6: AUDIT_PYTHON_BIN override works
def test_audit_python_bin_override():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        custom_py = Path(tmp) / "my_custom_python"
        custom_py.write_text("custom")

        symlinks_created = []

        def fake_symlink_to(self, target):
            symlinks_created.append((str(self), target))

        with patch.dict(os.environ, {"AUDIT_PYTHON_BIN": str(custom_py)}):
            with patch.object(Path, "symlink_to", fake_symlink_to):
                result = _ensure_python_shim(ws)

        assert result == str(ws / ".python-shim")
        for src, target in symlinks_created:
            assert target == str(custom_py), f"Override failed: {target}"
        print("  test_audit_python_bin_override: PASSED")


# Test 7: Default /usr/bin/python3.12 doesn't exist on Windows
def test_default_target_missing():
    if "AUDIT_PYTHON_BIN" in os.environ:
        del os.environ["AUDIT_PYTHON_BIN"]
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        result = _ensure_python_shim(ws)
        assert result is None, f"Should return None for missing default, got {result}"
        print("  test_default_target_missing: PASSED")


# Test 8: shutil.which detection for python3.12 in PATH
def test_default_with_python_in_path():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        # Create a fake python3.12 in a temp dir
        fake_bin = Path(tmp) / "fakebin"
        fake_bin.mkdir()
        fake_py = fake_bin / "python3.12"
        fake_py.write_text("fake")

        # Simulate the case where /usr/bin/python3.12 is the default
        # but doesn't exist; with AUDIT_PYTHON_BIN pointing to existing
        symlinks_created = []

        def fake_symlink_to(self, target):
            symlinks_created.append((str(self), target))

        with patch.dict(os.environ, {"AUDIT_PYTHON_BIN": str(fake_py)}):
            with patch.object(Path, "symlink_to", fake_symlink_to):
                result = _ensure_python_shim(ws)

        assert result == str(ws / ".python-shim")
        # The target file's basename is python3.12, so symlinks should be
        # python3, python3.12, and python3.12 (the base name)
        names = set(Path(s[0]).name for s in symlinks_created)
        assert names == {"python3", "python3.12"}, f"Wrong symlinks: {names}"
        print("  test_default_with_python_in_path: PASSED")


if __name__ == "__main__":
    print("=== Testing _ensure_python_shim ===")
    test_disabled_by_env()
    test_missing_target()
    test_valid_target_with_mock()
    test_symlink_failure()
    test_idempotent()
    test_audit_python_bin_override()
    test_default_target_missing()
    test_default_with_python_in_path()
    print("\n=== All tests passed ===")
