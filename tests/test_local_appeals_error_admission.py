import importlib.util
from pathlib import Path


def test_admission_requires_evidence_not_just_correction():
    source = Path(__file__).resolve().parents[1] / "workspace/skills/appeals-analyzer/testing/runner.py"
    spec = importlib.util.spec_from_file_location("appeals_admission_runner", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._has_error_admission("operator: Ошибка признана, исправим расчёт")
    assert module._has_error_admission("Оператор признал ошибку и назначил исправление")
    assert not module._has_error_admission("Расхождение устранено, повторная операция разрешена")
    assert not module._has_error_admission("Оператор не признал ошибку")
    assert not module._has_error_admission("Ошибка не признана, нужны документы")
