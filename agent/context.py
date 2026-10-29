"""Conversation state that survives a long task.

The pre-existing text path rebuilt ``[system, user]`` on every turn, so it had
nothing to compact. Now that the loop accumulates tool calls and results, the
window fills up and the naive fix — drop the oldest messages — corrupts the
OpenAI tool protocol: an ``assistant`` message carrying ``tool_calls`` must be
followed by one ``tool`` message per call. Compaction therefore cuts at a safe
boundary and folds everything before it into a single summary message.

Summarisation is pluggable. Pass a model-backed callable for quality; the
deterministic fallback is always available, never raises, and keeps the facts
that matter for resuming work (what was asked, which tools ran, which files
were touched).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

CHARS_PER_TOKEN = 4
SUMMARY_MARKER = "[Earlier context — compacted]"


def estimate_tokens(text: str) -> int:
    """Rough token count. Deliberately conservative (4 chars/token)."""
    return max(1, len(text or "") // CHARS_PER_TOKEN)


def message_chars(message: Dict[str, Any]) -> int:
    total = len(str(message.get("content") or ""))
    calls = message.get("tool_calls") or []
    if calls:
        try:
            total += len(json.dumps(calls, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            total += 200 * len(calls)
    return total


def total_chars(messages: Iterable[Dict[str, Any]]) -> int:
    return sum(message_chars(m) for m in messages)


def truncate_tool_output(text: str, limit: int = 12_000) -> str:
    """Keep the head and tail of a long tool result.

    The tail matters: test runners and compilers put the verdict there. A pure
    head-truncation hides the one line the model needs.
    """
    text = text if isinstance(text, str) else str(text)
    if len(text) <= limit:
        return text
    tail = max(400, limit // 5)
    head = limit - tail - 80
    return (
        text[:head]
        + f"\n... [{len(text) - limit + 80} chars truncated] ...\n"
        + text[-tail:]
    )


@dataclass
class ContextBudget:
    """Character budgets rather than token counts.

    Character counting is model-agnostic and cannot be wrong by much; a token
    counter that needs the provider's tokenizer would make the harness depend
    on whichever endpoint is configured.
    """

    max_chars: int = 120_000
    keep_recent_messages: int = 10
    max_tool_output_chars: int = 12_000
    summary_render_chars: int = 24_000
    compaction_target_chars: int = 30_000

    @property
    def max_tokens(self) -> int:
        return self.max_chars // CHARS_PER_TOKEN


@dataclass
class ConversationContext:
    """The message list the loop sends, plus its budget accounting."""

    system: Optional[str] = None
    messages: List[Dict[str, Any]] = field(default_factory=list)
    budget: ContextBudget = field(default_factory=ContextBudget)
    compactions: int = 0
    summary: str = ""

    # -- assembly ----------------------------------------------------------

    def as_payload(self) -> List[Dict[str, Any]]:
        """System message first, exactly once."""
        payload: List[Dict[str, Any]] = []
        if self.system:
            payload.append({"role": "system", "content": self.system})
        payload.extend(self.messages)
        return payload

    def append(self, message: Dict[str, Any]) -> Dict[str, Any]:
        self.messages.append(message)
        return message

    def append_user(self, text: str) -> Dict[str, Any]:
        return self.append({"role": "user", "content": text})

    # -- accounting --------------------------------------------------------

    @property
    def chars(self) -> int:
        return total_chars(self.as_payload())

    @property
    def estimated_tokens(self) -> int:
        return max(1, self.chars // CHARS_PER_TOKEN)

    def needs_compaction(self) -> bool:
        return self.chars > self.budget.max_chars

    # -- compaction --------------------------------------------------------

    @staticmethod
    def _safe_boundary(messages: List[Dict[str, Any]], start: int) -> int:
        """Move ``start`` forward past messages that would be orphaned.

        A ``tool`` message is only valid immediately after the ``assistant``
        message that requested it. If the cut lands inside a tool block, drop
        the whole block rather than send an orphan.
        """
        while start < len(messages) and messages[start].get("role") == "tool":
            start += 1
        return start

    def render_for_summary(self, messages: List[Dict[str, Any]]) -> str:
        lines: List[str] = []
        used = 0
        for message in messages:
            role = message.get("role")
            content = str(message.get("content") or "").strip()
            if role == "assistant":
                entry = f"ASSISTANT: {content[:600]}"
                calls = message.get("tool_calls") or []
                if calls:
                    names = []
                    for call in calls:
                        fn = call.get("function") or call
                        names.append(str(fn.get("name") or "?"))
                    entry += f" [tool calls: {', '.join(names)}]"
            elif role == "tool":
                entry = f"TOOL({message.get('name') or '?'}): {content[:400]}"
            elif role == "user":
                entry = f"USER: {content[:800]}"
            else:
                continue
            used += len(entry) + 1
            if used > self.budget.summary_render_chars:
                lines.append("... [earlier messages elided] ...")
                break
            lines.append(entry)
        return "\n".join(lines)

    @staticmethod
    def _deterministic_summary(messages: List[Dict[str, Any]]) -> str:
        requests: List[str] = []
        tools: Dict[str, int] = {}
        files: set[str] = set()
        for message in messages:
            if message.get("role") == "user":
                text = str(message.get("content") or "").strip()
                if text and not text.startswith(SUMMARY_MARKER):
                    requests.append(text[:300])
            for call in message.get("tool_calls") or []:
                fn = call.get("function") or call
                name = str(fn.get("name") or "?")
                tools[name] = tools.get(name, 0) + 1
                raw_args = fn.get("arguments")
                if isinstance(raw_args, str):
                    try:
                        raw_args = json.loads(raw_args)
                    except ValueError:
                        raw_args = {}
                if isinstance(raw_args, dict):
                    for key in ("path", "file", "filename", "file_path", "cwd"):
                        value = raw_args.get(key)
                        if isinstance(value, str) and value.strip():
                            files.add(value)
        parts = []
        if requests:
            parts.append("Requests so far: " + " | ".join(requests[-4:]))
        if tools:
            parts.append("Tools used: " + ", ".join(f"{k}×{v}" for k, v in sorted(tools.items())))
        if files:
            parts.append("Files touched: " + ", ".join(sorted(files)[:20]))
        return "\n".join(parts) or "(no recoverable detail)"

    def compact(
        self,
        summarizer: Optional[Callable[[str], str]] = None,
        *,
        force: bool = False,
    ) -> Optional[str]:
        """Fold the oldest messages into one summary. Returns it, or ``None``.

        ``summarizer`` receives the rendered span and must return a summary
        string. If it raises, the deterministic fallback runs — compaction is
        a safety mechanism and must not be able to fail a turn.
        """
        if not force and not self.needs_compaction():
            return None
        keep = max(2, self.budget.keep_recent_messages)
        start = max(0, len(self.messages) - keep)
        start = self._safe_boundary(self.messages, start)
        if start <= 0:
            return None

        dropped = self.messages[:start]
        kept = self.messages[start:]
        rendered = self.render_for_summary(dropped)

        summary = ""
        if summarizer is not None:
            try:
                summary = str(summarizer(rendered) or "").strip()
            except Exception:
                summary = ""
        if not summary:
            summary = self._deterministic_summary(dropped)

        summary = truncate_tool_output(summary, self.budget.compaction_target_chars)
        self.messages = [
            {"role": "user", "content": f"{SUMMARY_MARKER}\n{summary}"}
        ] + kept
        self.compactions += 1
        self.summary = summary
        return summary
