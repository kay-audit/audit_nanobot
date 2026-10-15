"""Централизация обращения к модели в навыке ``follow_up`` (D5).

Навык не заводит свою модель: адрес, ключ и имя он берёт у агента
(``agents.defaults`` + ``providers.<имя>``) через
``core/nanobot_config.adopt_agent_llm``. Закреплено здесь:

* **нейтральные дефолты** — пустые ``llm_base_url``/``llm_api_key``, а не
  адрес Fireworks: забытый дефолт уводил навык на чужой сервис и давал 401
  при полностью правильной настройке агента;
* **громкий отказ** — без адреса и без модели навык не идёт в сеть, а
  объясняет, что чинить, и MCP-сервер отказывается подниматься;
* **имя провайдера доезжает до навыка** — по нему выбирается форма управления
  рассуждениями (``minimax`` → ``reasoning_split``,
  ``vllm`` → ``chat_template_kwargs.enable_thinking``), и переключение
  ``agents.defaults.provider`` переключает её без правок кода.

Сеть не используется: модель замокана, проверяется форма запроса.
"""
from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import REPO_ROOT

# follow_up/backend/* живёт в skills/follow_up/backend — добавляем в sys.path
SKILL_ROOT = REPO_ROOT / "workspace" / "skills" / "follow_up"
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

HOME_ENV = "NANOBOT_HOME"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Ни одной LLM-переменной из окружения.

    На машине администратора ``LLM_API_KEY`` задан всегда — это аварийный
    выход. Тест дефолтов обязан видеть состояние чистой машины, иначе он
    проверяет чужое окружение, а не код.
    """
    for name in list(os.environ):
        if name.startswith(("LLM_", "VLLM_", "FOLLOW_UP_LLM")) or \
                name in (HOME_ENV, "FU_SKIP_CORPUS_BOOT"):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings():
    """Фабрика настроек навыка с пустыми дефолтами, как их видит чистый запуск."""
    from backend.config import Settings

    def _make(**overrides):
        base = {"llm_base_url": "", "llm_api_key": "", "llm_model_name": None,
                "llm_mode": "api", "llm_provider": None}
        base.update(overrides)
        return Settings(**base)

    return _make


@pytest.fixture
def agent_home(tmp_path, monkeypatch):
    """Каталог нанобота с конфигом, который можно переключать в тесте."""
    root = tmp_path / "nanobot"
    root.mkdir()
    cfg = {
        "agents": {"defaults": {"provider": "minimax", "model": "MiniMax-M3"}},
        "providers": {"minimax": {"apiBase": "https://api.minimax.io/v1",
                                  "apiKey": "sk-cp-secret"}},
    }
    (root / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv(HOME_ENV, str(root))
    return root


# ── дефолты: никакого чужого сервиса в коде навыка ──────────────────

class TestNeutralDefaults:
    def test_no_vendor_endpoint_is_hardcoded(self):
        """Адрес Fireworks в коде навыка — это отказ, а не дефолт.

        Возвращает адрес, которого в репозитории нет, поэтому запрос уходит
        на 404 чужого сервиса, и диагностика уводит совсем не туда."""
        from backend.config import Settings

        assert Settings().llm_base_url == ""
        assert Settings().llm_api_key == ""

    def test_skill_source_mentions_no_foreign_endpoint(self):
        """Адрес чужого сервиса в коде навыка — тот же отказ, только оставленный
        константой.

        Ищутся присваивания, а не любые упоминания: объясняющий комментарий
        («бывший дефолт уводил на api.fireworks.ai») полезен и должен быть
        виден тому, кто чинит, — иначе тест гоняет правку полезного текста.
        """
        markers = ("api.fireworks.ai", "accounts/fireworks")
        offenders = []
        for path in (SKILL_ROOT / "backend").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                # Строковые литералы в присваивании и в аргументах вызовов —
                # всё, что может стать адресом или именем модели.
                if isinstance(node, ast.Assign):
                    values = [v for v in ast.walk(node.value)
                              if isinstance(v, ast.Constant) and isinstance(v.value, str)]
                elif isinstance(node, ast.Call):
                    values = [v for v in ast.walk(node) if isinstance(v, ast.Constant)
                              and isinstance(v.value, str)]
                else:
                    continue
                for value in values:
                    if any(m in value.value for m in markers):
                        rel = path.relative_to(REPO_ROOT)
                        offenders.append(f"{rel}:{getattr(node, 'lineno', '?')} "
                                         f"{value.value[:40]!r}")
        assert not offenders, offenders


# ── adoption: модель агента приезжает целиком ────────────────────────

class TestAdoptAgentLlm:
    def test_takes_model_address_key_and_provider(self, settings, agent_home):
        from backend.core import nanobot_config

        cfg = settings()
        applied = nanobot_config.adopt_agent_llm(cfg)

        assert applied["model"] == "MiniMax-M3"
        assert applied["provider"] == "minimax"
        assert cfg.llm_base_url == "https://api.minimax.io/v1"
        assert cfg.llm_model_name == "MiniMax-M3"
        assert cfg.llm_mode == "api"

    def test_provider_name_reaches_the_settings(self, settings, agent_home):
        """Без этого поля форма управления рассуждением не выводится из
        провайдера, и vLLM на ПРОМе получает поле не то, которое понимает."""
        from backend.core import nanobot_config

        cfg = settings()
        nanobot_config.adopt_agent_llm(cfg)

        assert cfg.llm_provider == "minimax"

    def test_provider_switch_changes_the_reasoning_field(self, settings, agent_home):
        """Главное обещание централизации: меняем только провайдера в
        config.json — меняется и форма управления рассуждением."""
        from backend.core import nanobot_config
        from backend.llm.client import _reasoning_extra_body

        cfg = settings()
        nanobot_config.adopt_agent_llm(cfg)
        assert _reasoning_extra_body(cfg) == {"reasoning_split": True}

        path = agent_home / "config.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["agents"]["defaults"]["provider"] = "vllm"
        data["providers"]["vllm"] = {"apiBase": "http://127.0.0.1:8000/v1",
                                    "apiKey": "EMPTY"}
        path.write_text(json.dumps(data), encoding="utf-8")

        cfg2 = settings()
        nanobot_config.adopt_agent_llm(cfg2)
        assert cfg2.llm_provider == "vllm"
        assert cfg2.llm_base_url == "http://127.0.0.1:8000/v1"
        assert _reasoning_extra_body(cfg2) == {
            "chat_template_kwargs": {"enable_thinking": True}}

    def test_unresolved_api_key_placeholder_is_not_taken(self, settings, agent_home):
        """Неразрешённый ``${ПЕРЕМЕННАЯ}`` — не ключ. Оставляем свой, но
        говорим вслух: иначе уход с агентским ключом молча ломает доступ."""
        from backend.core import nanobot_config

        path = agent_home / "config.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        # Подстановка берётся из окружения, а переменной с таким именем там
        # нет — это и есть «секрет не найден».
        data["providers"]["minimax"]["apiKey"] = "${FOLLOW_UP_КЛЮЧ_НЕ_ЗАДАН}"
        path.write_text(json.dumps(data), encoding="utf-8")

        cfg = settings(llm_api_key="свой-ключ")
        applied = nanobot_config.adopt_agent_llm(cfg)

        assert cfg.llm_api_key == "свой-ключ"
        assert "api_key" not in applied

    def test_ignoring_the_agent_is_an_explicit_tumbler(self, settings, agent_home):
        """Аварийный режим: свой адрес и LLM_IGNORE_NANOBOT=true."""
        from backend.core import nanobot_config

        cfg = settings(llm_base_url="http://localhost:8000/v1",
                       llm_ignore_nanobot=True)
        applied = nanobot_config.adopt_agent_llm(cfg)

        assert applied == {}
        assert cfg.llm_base_url == "http://localhost:8000/v1"

    def test_without_agent_home_nothing_is_taken(self, settings, monkeypatch):
        """Вне нанобота навык работает на своих настройках — и это не поломка."""
        from backend.core import nanobot_config

        monkeypatch.delenv(HOME_ENV, raising=False)
        cfg = settings(llm_base_url="http://localhost:8000/v1")

        assert nanobot_config.adopt_agent_llm(cfg) == {}
        assert cfg.llm_base_url == "http://localhost:8000/v1"


# ── громкий отказ вместо прогулки по неверному адресу ────────────────

class TestLoudRefusal:
    def test_empty_endpoint_is_refused(self, settings):
        from backend.llm import client as client_mod

        cfg = settings()
        with patch.object(client_mod, "get_settings", lambda: cfg):
            with pytest.raises(client_mod.LLMNotConfigured) as e:
                client_mod.require_configured_endpoint()
        assert "адрес пуст" in str(e.value)

    def test_refusal_names_where_to_configure_it(self, settings):
        """Отказ бесполезен, если не сказано, что именно чинить."""
        from backend.llm import client as client_mod

        cfg = settings()
        with patch.object(client_mod, "get_settings", lambda: cfg):
            with pytest.raises(client_mod.LLMNotConfigured) as e:
                client_mod.require_configured_endpoint()
        text = str(e.value)
        assert "config.json" in text
        assert "LLM_BASE_URL" in text

    def test_configured_endpoint_is_returned(self, settings):
        from backend.llm import client as client_mod

        cfg = settings(llm_base_url="https://api.minimax.io/v1")
        with patch.object(client_mod, "get_settings", lambda: cfg):
            assert client_mod.require_configured_endpoint() == \
                "https://api.minimax.io/v1"

    def test_unresolved_model_is_refused_not_guessed(self, settings):
        """Раньше здесь возвращалась константа accounts/fireworks/... —
        запрос уходил и падал на 404 чужого сервиса."""
        from backend.llm import client as client_mod

        cfg = settings(llm_base_url="https://api.minimax.io/v1")
        client = MagicMock()
        client.models.list.return_value = MagicMock(data=[])
        with patch.object(client_mod, "get_settings", lambda: cfg), \
             patch.object(client_mod, "get_sync_client", return_value=client), \
             patch.object(client_mod, "_resolved_model", None):
            with pytest.raises(client_mod.LLMNotConfigured):
                client_mod.resolve_model_sync()

    def test_server_refuses_to_start_without_a_model(self):
        """Сервер без адреса модели отвечает на каждый вопрос «недоступен»,
        и виновника ищут в базе вместо конфигурации.

        Проверяется сама функция отказа, а не весь ``_boot``: подъём корпуса
        занимает файл базы, и тест транспорта не должен из-за него падать.
        """
        import subprocess

        code = ("import sys; sys.path.insert(0, %r);"
                "from backend.skill import mcp_server as m;"
                "m._require_model_configured()" % str(SKILL_ROOT))
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, cwd=str(SKILL_ROOT))
        assert r.returncode == 2, r.stderr
        assert "Не стартую" in r.stderr

    def test_gigachat_mode_needs_no_agent_endpoint(self, settings):
        """У GigaChat свой адрес и свой режим; требовать агентский адрес
        здесь нельзя — навык в этом режиме работает исторически."""
        from backend.skill import mcp_server

        cfg = settings(llm_mode="gigachat")
        with patch("backend.config.get_settings", lambda: cfg):
            assert mcp_server._require_model_configured() is None


# ── рассуждение едет в тело запроса, а не в отдельное поле ───────────

class TestReasoningExtraBody:
    def _extra(self, cfg):
        from backend.llm import client as client_mod

        with patch.object(client_mod, "get_settings", lambda: cfg):
            return client_mod._reasoning_extra_body(cfg)

    def test_minimax_gets_reasoning_split(self, settings):
        assert self._extra(settings(llm_provider="minimax")) == \
            {"reasoning_split": True}

    def test_vllm_gets_chat_template_kwargs(self, settings):
        assert self._extra(settings(llm_provider="vllm")) == \
            {"chat_template_kwargs": {"enable_thinking": True}}

    def test_switch_off_is_visible_in_the_same_field(self, settings, monkeypatch):
        """Тумблер рассуждений общий у всех навыков и меняет значение в том
        же поле, а не исчезает: иначе сервер решает сам."""
        monkeypatch.setenv("VLLM_ENABLE_THINKING", "false")
        assert self._extra(settings(llm_provider="vllm")) == \
            {"chat_template_kwargs": {"enable_thinking": False}}

    def test_generation_sends_it_as_extra_body(self, settings):
        """SDK не пропускает неизвестные поля верхнего уровня, поэтому
        управляющее поле обязано ехать в extra_body."""
        from backend.llm import client as client_mod

        cfg = settings(llm_base_url="https://api.minimax.io/v1",
                       llm_model_name="MiniMax-M3", llm_provider="minimax")
        client = MagicMock()
        message = MagicMock(content="391", reasoning_content=None)
        client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=message)])

        with patch.object(client_mod, "get_settings", lambda: cfg), \
             patch.object(client_mod, "get_sync_client", return_value=client):
            text = client_mod.generate_sync(
                [{"role": "user", "content": "17*23?"}])

        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["extra_body"] == {"reasoning_split": True}
        assert text == "391"

    def test_draft_is_stripped_from_the_answer(self, settings):
        """Без reasoning_split minimax присылает черновик внутри content —
        пользователь видел бы размышления вместо ответа."""
        from backend.llm import client as client_mod

        cfg = settings(llm_base_url="https://api.minimax.io/v1",
                       llm_model_name="MiniMax-M3", llm_provider="minimax")
        client = MagicMock()
        message = MagicMock(content="<think>считаю…</think>Верный ответ: 391",
                            reasoning_content="считаю…")
        client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=message)])

        with patch.object(client_mod, "get_settings", lambda: cfg), \
             patch.object(client_mod, "get_sync_client", return_value=client):
            text = client_mod.generate_sync([{"role": "user", "content": "17*23?"}])

        assert text == "Верный ответ: 391"
        assert "<think>" not in text