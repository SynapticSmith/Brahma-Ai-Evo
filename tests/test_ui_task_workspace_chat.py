"""The task-workspace card must not replay a result as a chat message.

User note 3: after a turn the assistant answered twice.  The second copy was
not a model response at all -- ``finish_task_workspace`` mirrored the task
card's result into the feed as a *system* message, while the same text had
already arrived as the assistant message (``write_log("Brahma Evo: ...")``).
The card is presentation; the transcript is owned by ``record_chat_event``
callers that carry a real role.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from core.ui_chat import InlineChatWorkspace, WorkspaceSidebar  # noqa: E402


class _Card:
    """Duck-typed TaskCard: only the calls ``finish`` makes."""

    def show(self):
        pass

    def start_workspace(self, command, plan, source):
        self.started = (command, plan, source)

    def update_workspace(self, **kwargs):
        self.updated = dict(kwargs)

    def finish_workspace(self, result, status, percent):
        self.finished = (result, status, percent)

    def clear_workspace(self):
        pass


@pytest.mark.parametrize("widget_cls", [WorkspaceSidebar, InlineChatWorkspace])
def test_finish_does_not_append_a_chat_message(widget_cls):
    widget = widget_cls.__new__(widget_cls)
    widget._task_card = _Card()
    events = []
    widget.record_chat_event = events.append

    widget_cls.apply_task_workspace(
        widget,
        {
            "action": "finish",
            "result": "Here is the answer.",
            "status": "Task completed.",
            "percent": 100,
            "source": "local",
        },
    )

    assert widget._task_card.finished[0] == "Here is the answer."
    assert events == []


@pytest.mark.parametrize("widget_cls", [WorkspaceSidebar, InlineChatWorkspace])
def test_update_does_not_append_a_chat_message(widget_cls):
    widget = widget_cls.__new__(widget_cls)
    widget._task_card = _Card()
    events = []
    widget.record_chat_event = events.append

    widget_cls.apply_task_workspace(
        widget,
        {"action": "update", "output": "Searching the web...", "percent": 40},
    )

    assert events == []
