"""Startup schema accepts the existing manual snapshot resource contract."""
from pathlib import Path

import pytest
from pydantic import ValidationError

from config import load_config_json
from lib.core.project_settings import ProjectSettings, TableEntry


def test_integrated_project_validates_without_losing_sync_modes():
    settings = ProjectSettings.model_validate(load_config_json(Path(__file__).resolve().parents[1] / "project.json"))
    resources = settings.skills.model_extra["sql_assistant"]["tables"]
    manual = [r for r in resources if r.get("sync_mode") == "manual_snapshot"]
    assert len(manual) == 2
    assert all(r["label"] and not r.get("tracking_column") for r in manual)


@pytest.mark.parametrize("extra", [{"sync_mode":"typo"}, {"sync_mode":"manual_snapshot"}, {"sync_mode":"manual_snapshot","label":"private","tracking_column":"id"}, {"sync_mode":"manual_snapshot","label":"private","type":"vector"}])
def test_invalid_sync_mode_fails_at_startup(extra):
    with pytest.raises(ValidationError):
        TableEntry.model_validate({"name":"db.table",**extra})
