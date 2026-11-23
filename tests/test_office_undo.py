from pathlib import Path

import pytest

from actions import docx_tools, pdf_tools
from core import undo


@pytest.fixture(autouse=True)
def _clear_undo_stack():
    undo.clear()
    yield
    undo.clear()


class _FakeDocxDocument:
    def save(self, path):
        Path(path).write_bytes(b"new-docx")


class _FakeStyles(dict):
    def add(self, style):
        self[style.name] = style


class _FakeColors:
    @property
    def white(self):
        return "white"

    def HexColor(self, value):
        return value


class _FakePdfDoc:
    def __init__(self, path):
        self.path = Path(path)

    def build(self, story, canvasmaker=None):
        self.path.write_bytes(b"new-pdf")


def test_word_document_overwrite_is_undoable(tmp_path, monkeypatch):
    target = tmp_path / "edited.docx"
    target.write_bytes(b"old-docx")

    monkeypatch.setattr(
        docx_tools,
        "_import_docx",
        lambda: (lambda: _FakeDocxDocument(), None, None, None, None),
    )
    monkeypatch.setattr(docx_tools, "_docx_result_path", lambda *args: target)
    monkeypatch.setattr(docx_tools, "_set_document_defaults", lambda doc: None)
    monkeypatch.setattr(docx_tools, "_set_core_props", lambda *args, **kwargs: None)
    monkeypatch.setattr(docx_tools, "_create_generic", lambda doc, params: None)
    monkeypatch.setattr(docx_tools, "_open_file", lambda path: None)

    result = docx_tools.word_document({"action": "create", "title": "Test", "output_path": str(target)})

    assert "Word document created" in result
    assert target.read_bytes() == b"new-docx"
    assert undo.peek() == "edited edited.docx"

    undo_result = undo.undo_last()

    assert "Restored the previous contents" in undo_result
    assert target.read_bytes() == b"old-docx"


def test_pdf_creation_overwrite_is_undoable(tmp_path, monkeypatch):
    target = tmp_path / "created.pdf"
    target.write_bytes(b"old-pdf")

    styles = _FakeStyles(
        Normal=object(),
        Title=object(),
        Heading1=object(),
        Heading2=object(),
        Heading3=object(),
        BodyText=object(),
    )
    pdf = {
        "getSampleStyleSheet": lambda: styles,
        "ParagraphStyle": lambda **kwargs: type("Style", (), {"name": kwargs["name"]})(),
        "colors": _FakeColors(),
        "TA_CENTER": 1,
        "TA_JUSTIFY": 4,
        "SimpleDocTemplate": lambda path, **kwargs: _FakePdfDoc(path),
        "LETTER": "letter",
        "Paragraph": lambda *args: object(),
        "Spacer": lambda *args: object(),
        "inch": 1,
        "NumberedCanvas": object(),
    }

    monkeypatch.setattr(pdf_tools, "_import_pdf", lambda: pdf)
    monkeypatch.setattr(pdf_tools, "_resolve_output_path", lambda *args: target)
    monkeypatch.setattr(pdf_tools, "_structured_blocks", lambda params: [{"kind": "body", "text": "x"}])
    monkeypatch.setattr(pdf_tools, "_pdf_story_from_blocks", lambda *args: [])
    monkeypatch.setattr(pdf_tools, "_open_file", lambda path: None)

    result = pdf_tools.create_pdf({"action": "create", "title": "Test", "content": "x"})

    assert "PDF created" in result
    assert target.read_bytes() == b"new-pdf"
    assert undo.peek() == "edited created.pdf"

    undo_result = undo.undo_last()

    assert "Restored the previous contents" in undo_result
    assert target.read_bytes() == b"old-pdf"


def test_office_undo_removes_new_file(tmp_path):
    target = tmp_path / "new.docx"
    target.write_bytes(b"new")

    undo.push_undo_file(target, None, "created new.docx")

    undo.undo_last()

    assert not target.exists()
