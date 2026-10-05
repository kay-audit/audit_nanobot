"""Тесты подавленного финала (``nanobot_bugfix_stutter``).

Корень проблемы: ``MessageTool`` помечает ``ctx.suppress_response``, и
``AgentLoop._prepare_outbound`` (nanobot/agent/loop.py:2068) выходит с
``ctx.outbound = None``. ``_assemble_outbound`` не вызывается, синтетический
``_final_turn`` недостижим, канал не закрывает слот — входящая строка висит
в ``processing`` до ``processing_timeout`` и повторяется.

Патч ``patch_prepare_outbound_suppressed`` снимает подавление для обычных
оборотов, собирая финальный outbound с ``_final_turn``.
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@dataclass
class _FakeDelivery:
    chat_id: str = "chat-1"
    channel: str = "postgres"
    media: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    stop_reason: str | None = None
    latency_ms: int | None = None
    message: Any = None

    def __post_init__(self) -> None:
        self.message = _FakeMsg(self)

    @property
    def delivery_message(self) -> Any:
        return self.message

    def record_stop_reason(self, stop_reason, failure_error_kind=None) -> None:
        self.stop_reason = stop_reason

    def record_latency(self, latency_ms) -> None:
        self.latency_ms = latency_ms


@dataclass
class _FakeMsg:
    delivery: _FakeDelivery

    @property
    def channel(self) -> str:
        return self.delivery.channel

    @property
    def chat_id(self) -> str:
        return self.delivery.chat_id

    @property
    def media(self) -> list:
        return self.delivery.media

    @property
    def metadata(self) -> dict:
        return self.delivery.metadata


@dataclass
class _FakeCtx:
    suppress_response: bool = False
    ephemeral: bool = False
    kind: Any = "USER"
    final_content: str = ""
    stop_reason: str | None = "stop"
    failure_error_kind: str | None = None
    turn_latency_ms: int | None = 1200
    delivery: _FakeDelivery = field(default_factory=_FakeDelivery)
    outbound: Any = None


class _FakeAgent:
    """Мини-агент: считает вызовы, повторяет upstream-поведение."""

    def __init__(self) -> None:
        self.prepare_calls = 0

    async def _prepare_outbound(self, ctx: Any) -> None:
        self.prepare_calls += 1
        if ctx.suppress_response:
            ctx.outbound = None
            return
        ctx.outbound = "final-outbound"


def _patched_agent() -> _FakeAgent:
    from lib.services.runtime_patcher import RuntimePatcher

    agent = _FakeAgent()
    ok, detail = RuntimePatcher().patch_prepare_outbound_suppressed(agent)
    assert ok, detail
    return agent


def test_not_suppressed_passes_through():
    """Без подавления поведение не меняется."""
    agent = _patched_agent()
    ctx = _FakeCtx(suppress_response=False, final_content="привет")
    asyncio.run(agent._prepare_outbound(ctx))
    assert ctx.outbound == "final-outbound"
    assert agent.prepare_calls == 1


def test_suppressed_emits_final_turn():
    """Подавленный оборот всё равно получает финал с ``_final_turn``."""
    from lib.utils.outbound_meta import FINAL_TURN_KEY

    agent = _patched_agent()
    ctx = _FakeCtx(suppress_response=True, final_content="")
    asyncio.run(agent._prepare_outbound(ctx))

    assert ctx.outbound is not None, "финальный outbound не построен"
    assert FINAL_TURN_KEY in ctx.outbound.metadata, "нет маркера _final_turn"
    assert ctx.delivery.stop_reason == "stop"
    assert ctx.delivery.latency_ms == 1200


def test_suppressed_keeps_final_content():
    """Непустой финальный контент не теряется."""
    from lib.utils.outbound_meta import FINAL_TURN_KEY

    agent = _patched_agent()
    ctx = _FakeCtx(suppress_response=True, final_content="Отчёт готов")
    asyncio.run(agent._prepare_outbound(ctx))

    assert ctx.outbound is not None
    assert ctx.outbound.content == "Отчёт готов"
    assert FINAL_TURN_KEY in ctx.outbound.metadata


def test_system_turn_stays_suppressed():
    """Служебные обороты не должны доставлять финал пользователю."""
    agent = _patched_agent()
    ctx = _FakeCtx(suppress_response=True, kind="SYSTEM", final_content="пинг")
    asyncio.run(agent._prepare_outbound(ctx))
    assert ctx.outbound is None, "SYSTEM-оборот не должен получать финал"


def test_ephemeral_stays_suppressed():
    """Ephemeral-обороты не финализируются."""
    agent = _patched_agent()
    ctx = _FakeCtx(suppress_response=True, ephemeral=True, final_content="x")
    asyncio.run(agent._prepare_outbound(ctx))
    assert ctx.outbound is None


def test_patch_is_idempotent():
    """Второй вызов патча не переподписывает метод."""
    from lib.services.runtime_patcher import RuntimePatcher

    agent = _FakeAgent()
    ok1, _ = RuntimePatcher().patch_prepare_outbound_suppressed(agent)
    ok2, detail2 = RuntimePatcher().patch_prepare_outbound_suppressed(agent)
    assert ok1 is True
    assert ok2 is False
    assert "already patched" in detail2


def test_missing_target_is_reported():
    """Отсутствующий метод — понятный skip, а не исключение."""
    from lib.services.runtime_patcher import RuntimePatcher

    ok, detail = RuntimePatcher().patch_prepare_outbound_suppressed(object())
    assert ok is False
    assert "_prepare_outbound" in detail
