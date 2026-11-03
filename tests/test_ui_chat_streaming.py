"""Unit tests for chat streaming, markdown rendering, and typing animations (User Note 6)."""

from __future__ import annotations

import os

# Ensure Qt offscreen
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

# Ensure QApplication exists for Qt widget tests
app = QApplication.instance() or QApplication([])

# Import ui to bind dependencies into core.ui_chat
import ui  # noqa: F401
from core.ui_chat import _markdown_to_html, ChatBubble, ConversationFeed


def test_markdown_to_html_no_double_escaping():
    raw = 'Here is code:\n```python\nif x < 10 and y > 2:\n    print("<hello>")\n```\nDone.'
    html = _markdown_to_html(raw, role="assistant")

    # It should escape < into &lt;, NOT &amp;lt;
    assert "&lt;hello&gt;" in html
    assert "&amp;lt;" not in html
    assert "<pre style=" in html
    assert "<code>if x &lt; 10 and y &gt; 2:" in html


def test_markdown_to_html_streaming_unclosed_code():
    partial = "Streaming code:\n```python\ndef calculate(a, b):\n    return a + b"
    html = _markdown_to_html(partial, role="assistant", streaming=True)

    # Should safely close code block and add cursor
    assert "<pre style=" in html
    assert "<code>def calculate(a, b):" in html
    assert "▋" in html


def test_markdown_to_html_formatting():
    text = "# Title\n## Subtitle\n### Heading\n**Bold Text** and `inline_code`\n*Italic*"
    html = _markdown_to_html(text, role="assistant")

    assert "<h1 " in html
    assert "<h2 " in html
    assert "<h3 " in html
    assert "<b>Bold Text</b>" in html
    assert "<i>Italic</i>" in html
    assert "<code " in html
    assert "inline_code" in html


def test_chat_bubble_instant_finish():
    bubble = ChatBubble("assistant", "Brahma Evo", "Hello world this is a test.", "12:00", animate=True)
    assert bubble._typing_timer is not None
    assert bubble._typing_timer.isActive()

    # Finish typing immediately (simulate mouse click or skip)
    bubble._finish_typing()
    assert bubble._typing_timer is None
    assert bubble._typing_index == len("Hello world this is a test.")


def test_conversation_feed_finalizes_previous_bubble():
    feed = ConversationFeed()
    feed.add_message("assistant", "Brahma Evo", "First long message", "12:00", animate=True)

    # Find first bubble
    first_bubble = None
    for i in range(feed._layout.count()):
        w = feed._layout.itemAt(i).widget()
        if isinstance(w, ChatBubble):
            first_bubble = w
            break

    assert first_bubble is not None
    assert first_bubble._typing_timer is not None

    # Adding a second message should finalize first bubble
    feed.add_message("user", "You", "Second message", "12:01", animate=False)
    assert first_bubble._typing_timer is None
    assert first_bubble._typing_index == len("First long message")
