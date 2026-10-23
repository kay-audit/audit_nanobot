from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["enabled", "existing_answer", "no_root", "no_flag", "foreign_chat", "not_command", "missing", "cancelled", "completed"])
async def test_demo_priority_writeback_respects_db_source_and_opt_in(monkeypatch, case):
    from lib.channels import postgres_channel as module
    from nanobot.bus.events import OutboundMessage

    monkeypatch.setenv("LOCAL_STAND_ROOT","synthetic-fixture")
    monkeypatch.setenv("LOCAL_STAND_PRIORITY_WRITEBACK","1")
    if case == "no_root":monkeypatch.delenv("LOCAL_STAND_ROOT")
    if case == "no_flag":monkeypatch.delenv("LOCAL_STAND_PRIORITY_WRITEBACK")
    source={"content":"/status","chat_id":"chat","status":"processing"}
    if case=="foreign_chat":source["chat_id"]="other"
    if case=="not_command":source["content"]="hello"
    if case in {"cancelled","completed"}:source["status"]=case
    if case=="missing":source=None
    conn=SimpleNamespace(fetchrow=AsyncMock(return_value=source),
        fetchval=AsyncMock(return_value="existing" if case=="existing_answer" else None), execute=AsyncMock())
    entered=Mock()
    @asynccontextmanager
    async def transaction():
        entered()
        yield conn
    monkeypatch.setattr(module,"transaction",transaction)
    channel=object.__new__(module.PostgresChannel)
    channel._fq_table='synthetic.messages'
    channel._delete_claim=AsyncMock()
    channel._merge_tool_delivery=AsyncMock()
    text="Полный ответ "*2000
    message=OutboundMessage(channel="postgres",chat_id="chat",content=text,
        metadata={"priority":True,"origin_message_id":"user-1"})
    await channel.send(message)
    if case in {"enabled","existing_answer"}:
        assert conn.execute.await_count==2
        assert conn.execute.await_args_list[0].args[2 if case=="enabled" else 1]==text
        channel._delete_claim.assert_awaited_once_with(conn,"user-1")
        channel._merge_tool_delivery.assert_not_awaited()
    else:
        conn.execute.assert_not_awaited()
        channel._delete_claim.assert_not_awaited()
        if case in {"cancelled","completed"}:channel._merge_tool_delivery.assert_not_awaited()
        else:channel._merge_tool_delivery.assert_awaited_once()
    if case in {"no_root","no_flag"}:entered.assert_not_called()
