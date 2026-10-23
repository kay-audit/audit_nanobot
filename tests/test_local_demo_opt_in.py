from __future__ import annotations

import importlib
import types
import sys
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("local",[False,True])
def test_minimax_effort_requires_demo_opt_in(monkeypatch,local):
    from lib.services import llm_reasoning
    monkeypatch.setenv("LOCAL_STAND_MINIMAX_REASONING_EFFORT","low")
    if local:monkeypatch.setenv("LOCAL_STAND_ROOT","synthetic-fixture")
    else:monkeypatch.delenv("LOCAL_STAND_ROOT",raising=False)
    value=llm_reasoning.extra_body_for("minimax")
    assert (value.get("reasoning_effort")=="low") is local


def test_local_catalog_requires_both_explicit_settings(monkeypatch,tmp_path):
    path=Path(__file__).resolve().parents[1]/"workspace/skills/ior-analyzer/utils/resolve/semantic_grounding.py"
    package=types.ModuleType("demo_resolve")
    package.__path__=[str(path.parent)]
    monkeypatch.setitem(sys.modules,"demo_resolve",package)
    module=importlib.import_module("demo_resolve.semantic_grounding")
    catalog=tmp_path/"catalog.json"
    catalog.write_text(json.dumps({"columns":{"d6_base_of_knowledge_ior.org_struct_lvl_3_name":{"values":["DEMO_ONLY_VALUE"]}}}),encoding="utf-8")
    monkeypatch.setenv("LOCAL_STAND_IOR_CATALOG",str(catalog))
    monkeypatch.delenv("LOCAL_STAND_ROOT",raising=False)
    assert "DEMO_ONLY_VALUE" not in module.load_catalog().get("org_struct_lvl_3_name",[])
    monkeypatch.setenv("LOCAL_STAND_ROOT",str(tmp_path))
    assert "DEMO_ONLY_VALUE" in module.load_catalog()["org_struct_lvl_3_name"]
