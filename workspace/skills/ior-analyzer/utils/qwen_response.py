"""Pure helpers for distinguishing a Qwen final answer from model reasoning."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class QwenResponse:
    final_text: str = ""
    content: str = ""
    reasoning_content: str = ""
    selected_final_field: str = "none"
    http_ok: bool = False


def strip_think_blocks(text: str) -> str:
    value = text or ""
    while "<think>" in value and "</think>" in value:
        start = value.find("<think>")
        end = value.find("</think>", start)
        if end < 0:
            break
        value = value[:start] + value[end + len("</think>"):]
    return value.strip()


def extract_openai_response(payload: Mapping[str, Any]) -> QwenResponse:
    """Extract only an explicit final field; never promote reasoning to final."""
    choices = payload.get("choices", []) if isinstance(payload, Mapping) else []
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        return QwenResponse(http_ok=True)
    choice = choices[0]
    if "message" in choice and isinstance(choice["message"], Mapping):
        message = choice["message"]
        content = strip_think_blocks(str(message.get("content") or ""))
        reasoning = str(message.get("reasoning_content") or "").strip()
        return QwenResponse(
            final_text=content,
            content=content,
            reasoning_content=reasoning,
            selected_final_field="choices[0].message.content" if content else "none",
            http_ok=True,
        )
    text = strip_think_blocks(str(choice.get("text") or ""))
    return QwenResponse(
        final_text=text,
        content=text,
        selected_final_field="choices[0].text" if text else "none",
        http_ok=True,
    )


def response_from_generate(text: str) -> QwenResponse:
    final = strip_think_blocks(text)
    return QwenResponse(
        final_text=final,
        content=final,
        selected_final_field="result" if final else "none",
        http_ok=bool(final),
    )
