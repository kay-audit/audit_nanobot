"""Independent, bounded semantic requests using the shared LLM infrastructure."""
from __future__ import annotations

import json
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from llm.single_flight import guarded_chat
from llm.tokens import TokenEstimator, TokenEstimatorConfig


@dataclass(frozen=True)
class HistoryPolicy:
    batch_input_ratio: float = 0.45
    max_output_tokens: int = 4096
    max_retries: int = 2
    chars_per_token: float = 3.5
    safety_margin_ratio: float = 0.15
    event_max_chars: int = 12000
    timeout_sec: int = 120
    temperature: float = 0.1

    def __post_init__(self):
        if not (0 < self.batch_input_ratio <= .55 and 512 <= self.max_output_tokens <= 4096
                and 0 <= self.max_retries <= 3 and 0 < self.chars_per_token <= 4
                and .1 <= self.safety_margin_ratio < .5 and self.event_max_chars >= 512
                and 1 <= self.timeout_sec <= 600 and 0 <= self.temperature <= 1):
            raise ValueError("Некорректные параметры skills.legal_summarizer.history")


def runtime_settings():
    from config import SETTINGS
    from lib.core.project_settings import SkillHistorySettings
    from llm.config import get_llm_config
    cfg = get_llm_config()
    context = int(SETTINGS.get("agents", {}).get("defaults", {}).get("contextWindowTokens") or 0)
    if context < 2048:
        raise ValueError("Задайте реальный agents.defaults.contextWindowTokens (>=2048)")
    section = SETTINGS.get("skills", {}).get("legal_summarizer", {}).get("history", {})
    return cfg, context, HistoryPolicy(**SkillHistorySettings.model_validate(dict(section)).model_dump())


def system_prompt() -> str:
    return (Path(__file__).resolve().parents[2] / "prompts/history_system.md").read_text(encoding="utf-8")


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def compact_pair(before: str, after: str, max_chars: int) -> tuple[str, str, bool]:
    """Keep changed word ranges with nearby context; never modify stored facts."""
    if len(before) + len(after) <= max_chars:
        return before, after, False
    old, new = before.split(), after.split()
    spans = [[], []]
    for tag, a, b, c, d in SequenceMatcher(None, old, new, autojunk=True).get_opcodes():
        if tag == "equal":
            continue
        for side, start, end, words in ((0, a, b, old), (1, c, d, new)):
            start, end = max(0, start - 15), min(len(words), end + 15)
            if spans[side] and start <= spans[side][-1][1]:
                spans[side][-1][1] = max(end, spans[side][-1][1])
            else:
                spans[side].append([start, end])
    texts = [" … ".join(" ".join(words[a:b]) for a, b in ranges)
             for words, ranges in ((old, spans[0]), (new, spans[1]))]
    # When changed material itself cannot fit, retain both edges and flag review.
    cap = max_chars // 2
    for i, text in enumerate(texts):
        if not text:
            text = (before, after)[i]
        if len(text) > cap:
            text = text[:cap // 2 - 3] + " … " + text[-cap // 2 + 3:]
        texts[i] = text
    return texts[0], texts[1], True


class HistoryRequests:
    def __init__(self, context: int, policy: HistoryPolicy, prompt: str | None = None):
        self.policy = policy
        self.context = context
        self.prompt = system_prompt() if prompt is None else prompt
        self.estimator = TokenEstimator(TokenEstimatorConfig(policy.chars_per_token, policy.safety_margin_ratio))
        self.output_cap = min(policy.max_output_tokens, context // 4)
        self.input_budget = min(int(context * policy.batch_input_ratio), self.estimator.available(
            context, self.estimator.estimate(self.prompt) + 128, self.output_cap))
        if self.input_budget < 256 or self.output_cap < 512:
            raise ValueError("Контекст слишком мал для history prompt и резервов")

    def view(self, event: dict) -> dict:
        view = {k: event.get(k) for k in ("id", "revision_date", "effective_date", "provision", "missing")}
        # Bound metadata too: unusual headings must not defeat the token budget.
        view["provision"] = str(view["provision"] or "")[:1000]
        view["missing"] = [str(s)[:200] for s in (view["missing"] or [])[:8]]
        cap = min(self.policy.event_max_chars, int(self.input_budget * self.policy.chars_per_token) - 2200)
        if cap < 256:
            raise ValueError("Недостаточно места для события")
        before, after, shortened = compact_pair(event.get("before") or "", event.get("after") or "", cap)
        view.update(before=before or None, after=after or None, llm_view_compacted=shortened)
        # Diff is redundant for full pairs. Include it only when small enough.
        changes = event.get("text_changes", [])
        if len(dumps(changes)) < 1000:
            view["text_changes"] = changes
        return view

    def output_tokens(self, count: int) -> int:
        return min(self.output_cap, max(512, 128 + 192 * count))

    def fits(self, views: list[dict]) -> bool:
        return (self.estimator.estimate(dumps({"events": views})) <= self.input_budget
                and 128 + 192 * len(views) <= self.output_cap)

    def batches(self, events: list[dict]):
        batch = []
        for event in events:
            view = self.view(event)
            if not self.fits([view]):
                raise ValueError(f"Событие {event['id']} не помещается в бюджет")
            if batch and not self.fits(batch + [view]):
                yield batch
                batch = []
            batch.append(view)
        if batch:
            yield batch

    def messages(self, views):
        if not self.fits(views):
            raise ValueError("History batch exceeds budget")
        return [{"role": "system", "content": self.prompt},
                {"role": "user", "content": dumps({"events": views})}]


def request_meanings(messages, *, cfg, max_tokens, policy):
    from lib.services.llm_client import call_llm
    # Retry belongs to the semantic layer, not a nested HTTP retry loop.
    return guarded_chat(call_llm, messages, cfg=cfg, context=None, max_tokens=max_tokens,
                        temperature=policy.temperature, max_retries=0, timeout=policy.timeout_sec)


def validate_response(text: str, expected: set[str]) -> dict:
    text = text.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    result = json.loads(text)
    if not isinstance(result, dict) or not isinstance(result.get("results"), list):
        raise ValueError("Expected results array")
    accepted, seen, duplicate = {}, set(), set()
    for row in result["results"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] not in expected:
            raise ValueError("Unknown event id")
        key = row["id"]
        if key in seen:
            duplicate.add(key)
        seen.add(key)
        meaning = row.get("meaning")
        if not isinstance(meaning, str) or not meaning.strip() or len(meaning) > 2000:
            continue
        if not isinstance(row.get("needs_review", False), bool):
            continue
        accepted[key] = {"meaning": meaning.strip(), "needs_review": row.get("needs_review", False),
                         "reason": str(row.get("reason", ""))[:500], "explanation_status": "completed"}
    for key in duplicate:
        accepted.pop(key, None)
    return accepted


def explain_batch(views, requests, cfg, checkpoint, *, call=request_meanings, attempt=0):
    """Keep valid IDs; split/retry only failures. Each request has two messages."""
    accepted = {}
    try:
        text = call(requests.messages(views), cfg=cfg, max_tokens=requests.output_tokens(len(views)), policy=requests.policy)
        accepted = validate_response(text, {v["id"] for v in views})
    except Exception as exc:
        # No raw server errors/prompts/credentials in public results or logs.
        error = type(exc).__name__
    else:
        error = "MissingOrInvalidIds"
    for view in views:
        row = accepted.get(view["id"])
        if row is not None:
            row["llm_view_compacted"] = view["llm_view_compacted"]
            if view["llm_view_compacted"] or view["before"] is None or view["after"] is None:
                row["needs_review"] = True
                row["reason"] = "Неполные исходные данные или сокращённое представление для LLM"
    if accepted:
        checkpoint(accepted)
    pending = [v for v in views if v["id"] not in accepted]
    if not pending:
        return accepted
    if attempt < requests.policy.max_retries:
        middle = max(1, len(pending) // 2)
        for part in (pending[:middle], pending[middle:]):
            if part:
                accepted.update(explain_batch(part, requests, cfg, checkpoint, call=call, attempt=attempt + 1))
    else:
        failed = {v["id"]: {"meaning": "Автоматическое пояснение не получено; исходные факты сохранены.",
                            "needs_review": True, "reason": error, "explanation_status": "failed",
                            "llm_view_compacted": v["llm_view_compacted"]} for v in pending}
        checkpoint(failed)
        accepted.update(failed)
    return accepted
