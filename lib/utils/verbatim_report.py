"""Request-bound provenance for native report publications (never trust inbound JSON)."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
import hashlib
import hmac
import json
import secrets

VERBATIM_REPORT_TOOLS = frozenset({'ior_analyzer', 'appeals_analyzer'})
SOURCE = '_verbatim_report_source'
SIGNATURE = '_verbatim_report_signature'
RECEIPT = '_verbatim_report_receipt'
SUCCESS_REASONS = frozenset({'', 'completed', 'empty_final_response', 'stop'})
_KEY = secrets.token_bytes(32)


def digest(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def _signature(source, content, channel, chat_id, metadata):
    payload = [source, digest(content), str(channel), str(chat_id)]
    payload.extend(str(metadata.get(k) or '') for k in ('origin_message_id', 'message_id', 'answer_id'))
    return hmac.new(_KEY, json.dumps(payload, ensure_ascii=False).encode('utf-8'), hashlib.sha256).hexdigest()


@contextmanager
def verbatim_report_context(ctx, source, content):
    """Only the native tool's individual MessageTool call receives this capability.

    MessageBus and PostgresChannel live in the same process. The ephemeral HMAC
    is stripped before DB persistence. A queued send is not a database receipt.
    """
    from nanobot.agent.tools.context import request_context
    from nanobot.utils.helpers import strip_think

    if source not in VERBATIM_REPORT_TOOLS:
        raise ValueError('Unknown verbatim report source')
    metadata = dict(ctx.metadata)
    metadata[SOURCE] = source
    metadata['_record_channel_delivery'] = True
    if ctx.message_id:
        metadata['message_id'] = ctx.message_id
    metadata[SIGNATURE] = _signature(source, strip_think(content), ctx.channel, ctx.chat_id, metadata)
    with request_context(replace(ctx, metadata=metadata)):
        yield


def verified_source(msg, metadata):
    source = metadata.get(SOURCE)
    proof = metadata.get(SIGNATURE)
    if not isinstance(source, str) or source not in VERBATIM_REPORT_TOOLS or not isinstance(proof, str) or not (msg.content or '').strip():
        return None
    expected = _signature(source, msg.content, msg.channel, msg.chat_id, metadata)
    return source if hmac.compare_digest(proof, expected) else None


def delivery_metadata(metadata):
    """Neither inherited metadata nor final LLM metadata may assert provenance."""
    return {k: v for k, v in metadata.items() if k not in {SOURCE, SIGNATURE, RECEIPT}}


@dataclass(frozen=True)
class ReportReceipt:
    assistant_id: str
    reports: tuple[tuple[str, str], ...]
    receipt_id: str = field(default_factory=lambda: secrets.token_hex(16))

    def matches(self, assistant_id, metadata):
        return self.assistant_id == assistant_id and metadata.get(RECEIPT) == self.receipt_id

    @property
    def content(self):
        return '\n\n'.join(text for _, text in self.reports)


def receipt_for(ctx, assistant_id, metadata):
    receipt = ctx.get(RECEIPT)
    return receipt if isinstance(receipt, ReportReceipt) and receipt.matches(assistant_id, metadata) else None


def next_receipt(previous, assistant_id, source, content):
    reports = list(previous.reports) if previous else []
    if source and (source, content) not in reports:
        reports.append((source, content))
    return ReportReceipt(assistant_id, tuple(reports)) if reports else None
