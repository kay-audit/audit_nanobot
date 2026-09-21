"""Isolation and error-surface contracts for the native appeals tool."""
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _drop_appeals_runtime_modules() -> None:
    for name in list(sys.modules):
        if name == "appeals_analyzer_runtime" or name.startswith("appeals_analyzer_runtime."):
            sys.modules.pop(name, None)


def test_native_loader_rejects_foreign_utils_instead_of_using_wrong_db(monkeypatch):
    from workspace.tools.appeals_analyzer import AppealsAnalyzerTool

    _drop_appeals_runtime_modules()
    foreign_utils = ModuleType("utils")
    foreign_utils.__path__ = ["foreign-utils-path"]
    foreign_bge = ModuleType("utils.bge_search_engine")
    foreign_bge.FOREIGN = True
    monkeypatch.setitem(sys.modules, "utils", foreign_utils)
    monkeypatch.setitem(sys.modules, "utils.bge_search_engine", foreign_bge)
    with pytest.raises(RuntimeError, match="gateway shared workspace/utils"):
        AppealsAnalyzerTool._load_runner()


def test_native_loader_uses_canonical_shared_db():
    from workspace.tools.appeals_analyzer import AppealsAnalyzerTool

    _drop_appeals_runtime_modules()
    shared_db = AppealsAnalyzerTool._require_shared_db(ROOT / "workspace")
    runner = AppealsAnalyzerTool._load_runner()
    facade = sys.modules["appeals_analyzer_runtime.utils.db"]

    assert runner.__module__ == "appeals_analyzer_runtime.scripts.appeals_reports"
    assert Path(shared_db.__file__).resolve() == (ROOT / "workspace/utils/db.py").resolve()
    assert facade.run is shared_db.run


def test_skill_directory_resolution_is_deterministic(tmp_path):
    from workspace.tools.appeals_analyzer import AppealsAnalyzerTool

    skills = tmp_path / "skills"
    hyphen = skills / "appeals-analyzer"
    hyphen.mkdir(parents=True)
    assert AppealsAnalyzerTool._resolve_skill_dir(tmp_path) == hyphen
    underscore = skills / "appeals_analyzer"
    underscore.mkdir()
    with pytest.raises(RuntimeError, match="Ambiguous"):
        AppealsAnalyzerTool._resolve_skill_dir(tmp_path)


@pytest.mark.asyncio
async def test_native_error_does_not_expose_traceback(monkeypatch):
    from workspace.tools.appeals_analyzer import AppealsAnalyzerTool, AppealsAnalyzerToolConfig

    def fail():
        raise RuntimeError("SELECT secret FROM internal_table at C:/private/runtime.py")

    monkeypatch.setattr(AppealsAnalyzerTool, "_load_runner", staticmethod(fail))
    result = await AppealsAnalyzerTool(config=AppealsAnalyzerToolConfig()).execute(prompt="x")
    rendered = str(result)
    assert "Traceback" not in rendered
    assert "internal_table" not in rendered
    assert "private/runtime.py" not in rendered
    assert "RuntimeError" in rendered
