"""
core/ui_voice_library.py
The "Voice library" dialog: browse and download Piper voices and Kokoro models
from within Settings, instead of editing files by hand or running a script.

Two tabs, one per engine:

Piper    the full published catalogue (every voice in the rhasspy/piper-voices
         manifest: ~100 voices, ~40 languages), searchable and filterable by
         language, with size and quality shown before committing to a
         download. Each voice is a .onnx + .onnx.json pair, md5-verified.
Kokoro   every model build of the pinned kokoro-onnx release (English v1.0 and
         Chinese+English v1.1-zh, each in fp32/fp16/int8) plus the matching
         voice packs, with the required pack auto-downloaded alongside a model
         and a picker for the ~100 voice names inside the pack.

Rules this dialog obeys:
- No blocking network or disk work on the GUI thread. Catalog fetches and
  downloads run on worker threads; only queued signal emissions touch widgets.
- Every failure is reported in the dialog (installing pip packages fails for
  very ordinary reasons, and silence there reads as "the app is broken").
- After any change, `on_changed` fires so the Speech Engines card re-reads the
  engine availability without the user reopening Settings.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.ui_theme import C


class _UiBridge(QObject):
    """Marshals worker-thread callbacks onto the GUI thread.

    A plain `QTimer.singleShot` from a worker thread has no event loop to fire
    on, so a signal with a queued connection is the only reliable hop here.
    """

    call = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.call.connect(self._invoke, Qt.ConnectionType.QueuedConnection)

    def _invoke(self, fn):
        try:
            fn()
        except Exception:
            pass

    def post(self, fn) -> None:
        self.call.emit(fn)


def _size_label(n: int) -> str:
    try:
        return f"{float(n) / (1024 * 1024):.1f} MB"
    except Exception:
        return "?"


class VoiceLibraryDialog(QDialog):
    """Browse/download local TTS voices. `on_changed` fires after any change."""

    def __init__(self, parent=None, on_changed: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self.setWindowTitle("Voice Library")
        self.setMinimumSize(880, 620)
        self._bridge = _UiBridge(self)
        self._run_on_ui = self._bridge.post
        self._on_changed = on_changed

        # Catalog state
        self._piper_entries: List[Dict[str, Any]] = []
        self._kokoro_entries: List[Dict[str, Any]] = []
        self._loading = False
        self._busy = False
        self._cancel = threading.Event()

        self.setStyleSheet(f"""
            QDialog {{ background: rgba(8,10,14,240); color: {C.WHITE}; border: 1px solid {C.BORDER_B}; }}
            QLabel {{ color: {C.TEXT}; background: transparent; }}
            QLineEdit, QComboBox {{
                background: rgba(16,16,16,240); color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 8px; padding: 6px 9px;
            }}
            QLineEdit:focus, QComboBox:focus {{ border: 1px solid {C.ACC}; }}
            QComboBox QAbstractItemView {{
                background: rgba(12,12,14,250); color: {C.WHITE};
                selection-background-color: rgba(0,229,255,0.25);
            }}
            QTreeWidget {{
                background: rgba(10,11,15,230); color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 8px;
                alternate-background-color: rgba(255,255,255,0.03);
            }}
            QTreeWidget::item {{ padding: 3px 2px; }}
            QTreeWidget::item:selected {{ background: rgba(0,229,255,0.22); color: {C.WHITE}; }}
            QHeaderView::section {{
                background: rgba(255,255,255,0.05); color: {C.TEXT_DIM};
                border: none; padding: 5px 6px; font-weight: 600;
            }}
            QPushButton {{
                background: rgba(255,255,255,0.06); color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 8px; padding: 6px 14px;
            }}
            QPushButton:hover {{ border: 1px solid {C.ACC}; }}
            QPushButton:disabled {{ color: {C.TEXT_DIM}; border: 1px solid rgba(255,255,255,0.08); }}
            QProgressBar {{
                background: rgba(255,255,255,0.06); border: none; border-radius: 5px; height: 10px; text-align: center;
            }}
            QProgressBar::chunk {{ background: {C.ACC}; border-radius: 5px; }}
            QTabWidget::pane {{ border: 1px solid {C.BORDER}; border-radius: 8px; top: -1px; }}
            QTabBar::tab {{
                background: rgba(255,255,255,0.04); color: {C.TEXT_DIM};
                border: 1px solid transparent; border-radius: 8px 8px 0 0; padding: 8px 22px;
            }}
            QTabBar::tab:selected {{ color: {C.WHITE}; border: 1px solid {C.BORDER}; background: rgba(0,229,255,0.08); }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 16)
        root.setSpacing(12)

        title = QLabel("Voice Library")
        title.setFont(QFont("Segoe UI", 20, QFont.Weight.Black))
        title.setStyleSheet(f"color: {C.WHITE};")
        root.addWidget(title)

        sub = QLabel(
            "Download local voices for the Piper and Kokoro engines. They land in "
            "%LOCALAPPDATA%\\BrahmaAI\\voices and are picked up automatically."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        root.addWidget(sub)

        # Built before the tabs: the tabs' background workers report through
        # these, and a worker can finish while __init__ is still running.
        self._op_label = QLabel("")
        self._op_label.setWordWrap(True)
        self._op_label.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setFixedHeight(10)
        self._bar.setTextVisible(False)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setFixedWidth(90)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._cancel_btn.setEnabled(False)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_piper_tab(), "Piper voices")
        self._tabs.addTab(self._build_kokoro_tab(), "Kokoro models")
        root.addWidget(self._tabs, 1)

        # --- shared progress + status -------------------------------------- #
        root.addWidget(self._op_label)

        bar_row = QHBoxLayout()
        bar_row.addWidget(self._bar, 1)
        bar_row.addWidget(self._cancel_btn)
        root.addLayout(bar_row)

        footer = QHBoxLayout()
        self._folder_btn = QPushButton("Open voices folder")
        self._folder_btn.clicked.connect(self._open_folder)
        footer.addWidget(self._folder_btn)
        footer.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(110)
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        root.addLayout(footer)

    # ------------------------------------------------------------------ #
    # Piper tab
    # ------------------------------------------------------------------ #
    def _build_piper_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(10, 12, 10, 10)
        lay.setSpacing(8)

        engine_row = QHBoxLayout()
        self._piper_status = QLabel("")
        self._piper_status.setWordWrap(True)
        self._piper_status.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        engine_row.addWidget(self._piper_status, 1)
        self._piper_install = QPushButton("Install Piper runtime (pip)")
        self._piper_install.clicked.connect(lambda: self._install_runtime("piper"))
        engine_row.addWidget(self._piper_install)
        self._piper_refresh = QPushButton("Refresh list")
        self._piper_refresh.clicked.connect(self._load_piper_catalog)
        engine_row.addWidget(self._piper_refresh)
        lay.addLayout(engine_row)

        filter_row = QHBoxLayout()
        self._piper_search = QLineEdit()
        self._piper_search.setPlaceholderText("Search voices (name, key, language)…")
        self._piper_search.textChanged.connect(lambda _t: self._render_piper())
        filter_row.addWidget(self._piper_search, 1)
        self._piper_lang = QComboBox()
        self._piper_lang.setMinimumWidth(220)
        self._piper_lang.currentIndexChanged.connect(lambda _i: self._render_piper())
        filter_row.addWidget(self._piper_lang)
        lay.addLayout(filter_row)

        self._piper_tree = QTreeWidget()
        self._piper_tree.setColumnCount(5)
        self._piper_tree.setHeaderLabels(["Voice", "Language", "Quality", "Size", "Status"])
        self._piper_tree.setAlternatingRowColors(True)
        self._piper_tree.setRootIsDecorated(False)
        self._piper_tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        for idx, width in enumerate((240, 180, 90, 90, 110)):
            self._piper_tree.setColumnWidth(idx, width)
        lay.addWidget(self._piper_tree, 1)

        actions = QHBoxLayout()
        self._piper_download = QPushButton("Download selected")
        self._piper_download.clicked.connect(self._on_piper_download)
        actions.addWidget(self._piper_download)
        self._piper_use = QPushButton("Use selected voice")
        self._piper_use.clicked.connect(self._on_piper_use)
        actions.addWidget(self._piper_use)
        self._piper_remove = QPushButton("Remove selected")
        self._piper_remove.clicked.connect(self._on_piper_remove)
        actions.addWidget(self._piper_remove)
        actions.addStretch(1)
        lay.addLayout(actions)

        self._update_piper_engine_status()
        # The catalog fetch is a network call; kick it off here and let the
        # dialog render instantly with an empty list.
        self._load_piper_catalog()
        return tab

    def _update_piper_engine_status(self) -> None:
        try:
            from core import voice_catalog

            installed = voice_catalog.installed_piper_voices()
            importable = voice_catalog.engine_importable("piper")
            have = f"{len(installed)} voice(s) installed"
            runtime = "runtime installed" if importable else "runtime NOT installed"
            self._piper_status.setText(f"{have} · {runtime}.")
            self._piper_install.setEnabled(not importable)
        except Exception as exc:
            self._piper_status.setText(f"Status unavailable: {exc}")

    def _load_piper_catalog(self) -> None:
        if self._loading or self._busy:
            return
        self._loading = True
        self._piper_refresh.setEnabled(False)
        self._piper_status.setText("Loading the Piper voice list…")

        def _work():
            from core import voice_catalog

            entries, err = voice_catalog.piper_catalog(force_refresh=True)

            def _apply():
                self._loading = False
                self._piper_refresh.setEnabled(True)
                self._piper_entries = entries
                self._populate_piper_languages()
                self._render_piper()
                self._update_piper_engine_status()
                if err:
                    self._op_label.setText(err)
                elif not entries:
                    self._piper_status.setText(
                        "No voices available. Check the internet connection and press Refresh list."
                    )

            self._run_on_ui(_apply)

        threading.Thread(target=_work, daemon=True, name="piper-catalog").start()

    def _populate_piper_languages(self) -> None:
        counts: Dict[str, int] = {}
        for entry in self._piper_entries:
            code = str(entry.get("language_code") or "?")
            counts[code] = counts.get(code, 0) + 1
        current = self._piper_lang.currentData() if self._piper_lang.count() else ""
        self._piper_lang.blockSignals(True)
        self._piper_lang.clear()
        self._piper_lang.addItem(f"All languages ({len(self._piper_entries)})", "")
        for code in sorted(counts):
            self._piper_lang.addItem(f"{code} ({counts[code]})", code)
        idx = self._piper_lang.findData(current or "")
        self._piper_lang.setCurrentIndex(idx if idx >= 0 else 0)
        self._piper_lang.blockSignals(False)

    def _render_piper(self) -> None:
        needle = self._piper_search.text().strip().lower()
        lang = self._piper_lang.currentData() if self._piper_lang.count() else ""
        selected = self._selected_piper_id()
        self._piper_tree.clear()
        shown = 0
        for entry in self._piper_entries:
            if lang and str(entry.get("language_code")) != lang:
                continue
            haystack = " ".join(
                str(entry.get(k) or "") for k in ("id", "name", "language", "language_code")
            ).lower()
            if needle and needle not in haystack:
                continue
            item = QTreeWidgetItem([
                str(entry.get("id") or ""),
                str(entry.get("language") or ""),
                str(entry.get("quality") or ""),
                str(entry.get("size_label") or ""),
                "Installed" if entry.get("installed") else "",
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, str(entry.get("id") or ""))
            if entry.get("installed"):
                item.setForeground(4, QColor(C.GREEN))  # type: ignore[arg-type]
            if entry.get("warning"):
                item.setToolTip(0, str(entry["warning"]))
            self._piper_tree.addTopLevelItem(item)
            shown += 1
            if selected and str(entry.get("id")) == selected:
                self._piper_tree.setCurrentItem(item)
        self._op_label.setText(
            f"{shown} of {len(self._piper_entries)} Piper voices shown."
        )
        selected_entry = next(
            (e for e in self._piper_entries if str(e.get("id")) == selected), None
        )
        self._piper_use.setEnabled(bool(selected_entry and selected_entry.get("installed")))

    def _selected_piper_id(self) -> str:
        item = self._piper_tree.currentItem() if hasattr(self, "_piper_tree") else None
        if item is None:
            return ""
        return str(item.data(0, Qt.ItemDataRole.UserRole) or item.text(0))

    def _on_piper_download(self) -> None:
        voice_id = self._selected_piper_id()
        entry = next((e for e in self._piper_entries if e.get("id") == voice_id), None)
        if entry is None:
            self._op_label.setText("Select a voice first.")
            return
        if entry.get("installed"):
            self._op_label.setText(f"{voice_id} is already installed.")
            return
        self._start_download(entry)

    def _on_piper_use(self) -> None:
        voice_id = self._selected_piper_id()
        if not voice_id:
            return
        entry = next((e for e in self._piper_entries if e.get("id") == voice_id), None)
        if entry is not None and not entry.get("installed"):
            self._op_label.setText(f"Download {voice_id} before using it.")
            return
        try:
            from core import speech_tts

            speech_tts.set_piper_voice(voice_id)
            # The legacy "Voice file" field is a file path override that would
            # silently win over the picker; clear it so this click is what the
            # engine actually uses.
            speech_tts.set_voice("")
            self._op_label.setText(
                f"Using {voice_id} for Piper."
                + ("" if speech_tts.ENGINES["piper"]["probe"]()
                   else " Install the Piper runtime for it to become live.")
            )
            self._fire_changed()
        except Exception as exc:
            self._op_label.setText(f"Could not select the voice: {exc}")

    def _on_piper_remove(self) -> None:
        voice_id = self._selected_piper_id()
        entry = next((e for e in self._piper_entries if e.get("id") == voice_id), None)
        if entry is None or not entry.get("installed"):
            self._op_label.setText("Select an installed voice first.")
            return
        if QMessageBox.question(
            self, "Remove voice", f"Delete {voice_id} from disk?", 
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            from core import voice_catalog

            ok, err = voice_catalog.delete_entry(entry)
            self._op_label.setText("Removed." if ok else err)
            self._update_piper_engine_status()
            self._render_piper()
            self._fire_changed()
        except Exception as exc:
            self._op_label.setText(f"Could not remove: {exc}")

    # ------------------------------------------------------------------ #
    # Kokoro tab
    # ------------------------------------------------------------------ #
    def _build_kokoro_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(10, 12, 10, 10)
        lay.setSpacing(8)

        engine_row = QHBoxLayout()
        self._kokoro_status = QLabel("")
        self._kokoro_status.setWordWrap(True)
        self._kokoro_status.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        engine_row.addWidget(self._kokoro_status, 1)
        self._kokoro_install = QPushButton("Install Kokoro runtime (pip)")
        self._kokoro_install.clicked.connect(lambda: self._install_runtime("kokoro"))
        engine_row.addWidget(self._kokoro_install)
        lay.addLayout(engine_row)

        models_label = QLabel("Models")
        models_label.setStyleSheet(f"color: {C.TEXT}; font-size: 12px; font-weight: 600;")
        lay.addWidget(models_label)

        self._kokoro_models = QTreeWidget()
        self._kokoro_models.setColumnCount(4)
        self._kokoro_models.setHeaderLabels(["Model", "Precision", "Size", "Status"])
        self._kokoro_models.setAlternatingRowColors(True)
        self._kokoro_models.setRootIsDecorated(False)
        self._kokoro_models.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._kokoro_models.itemSelectionChanged.connect(self._on_kokoro_model_selected)
        for idx, width in enumerate((330, 90, 100, 130)):
            self._kokoro_models.setColumnWidth(idx, width)
        lay.addWidget(self._kokoro_models, 2)

        pack_label = QLabel("Voice packs")
        pack_label.setStyleSheet(f"color: {C.TEXT}; font-size: 12px; font-weight: 600;")
        lay.addWidget(pack_label)

        self._kokoro_bins = QTreeWidget()
        self._kokoro_bins.setColumnCount(3)
        self._kokoro_bins.setHeaderLabels(["Voice pack", "Size", "Status"])
        self._kokoro_bins.setAlternatingRowColors(True)
        self._kokoro_bins.setRootIsDecorated(False)
        self._kokoro_bins.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._kokoro_bins.itemSelectionChanged.connect(self._on_kokoro_bin_selected)
        for idx, width in enumerate((330, 100, 130)):
            self._kokoro_bins.setColumnWidth(idx, width)
        lay.addWidget(self._kokoro_bins, 1)

        voice_row = QHBoxLayout()
        voice_row.addWidget(QLabel("Voice"))
        self._kokoro_voice = QComboBox()
        self._kokoro_voice.setMinimumWidth(220)
        voice_row.addWidget(self._kokoro_voice, 1)
        lay.addLayout(voice_row)

        actions = QHBoxLayout()
        self._kokoro_download = QPushButton("Download selected")
        self._kokoro_download.clicked.connect(self._on_kokoro_download)
        actions.addWidget(self._kokoro_download)
        self._kokoro_use = QPushButton("Use selected model + voice")
        self._kokoro_use.clicked.connect(self._on_kokoro_use)
        actions.addWidget(self._kokoro_use)
        self._kokoro_remove = QPushButton("Remove selected")
        self._kokoro_remove.clicked.connect(self._on_kokoro_remove)
        actions.addWidget(self._kokoro_remove)
        actions.addStretch(1)
        lay.addLayout(actions)

        self._load_kokoro_catalog()
        return tab

    def _load_kokoro_catalog(self) -> None:
        try:
            from core import voice_catalog

            self._kokoro_entries, _err = voice_catalog.kokoro_catalog()
        except Exception as exc:
            self._kokoro_entries = []
            self._op_label.setText(f"Kokoro catalogue unavailable: {exc}")
        self._render_kokoro()
        self._update_kokoro_engine_status()

    def _update_kokoro_engine_status(self) -> None:
        try:
            from core import voice_catalog

            models = voice_catalog.installed_kokoro_models()
            bins = voice_catalog.installed_kokoro_bins()
            importable = voice_catalog.engine_importable("kokoro")
            self._kokoro_status.setText(
                f"{len(models)} model(s) and {len(bins)} voice pack(s) installed · "
                + ("runtime installed" if importable else "runtime NOT installed")
                + ". Each model needs its matching voice pack; the Download button fetches both."
            )
            self._kokoro_install.setEnabled(not importable)
        except Exception as exc:
            self._kokoro_status.setText(f"Status unavailable: {exc}")

    def _render_kokoro(self) -> None:
        selected_model = self._selected_kokoro("model")
        selected_bin = self._selected_kokoro("bin")
        self._kokoro_models.clear()
        self._kokoro_bins.clear()
        for entry in self._kokoro_entries:
            kind = str(entry.get("kind") or "")
            status = "Installed" if entry.get("installed") else ""
            if kind == "model":
                item = QTreeWidgetItem([
                    str(entry.get("label") or entry.get("filename") or ""),
                    str(entry.get("precision") or ""),
                    str(entry.get("size_label") or ""),
                    status,
                ])
                item.setData(0, Qt.ItemDataRole.UserRole, str(entry.get("filename") or ""))
                note = " ".join(x for x in (str(entry.get("detail") or ""),
                                            str(entry.get("warning") or "")) if x)
                if note:
                    item.setToolTip(0, note)
                if entry.get("installed"):
                    item.setForeground(3, QColor(C.GREEN))  # type: ignore[arg-type]
                self._kokoro_models.addTopLevelItem(item)
                if selected_model and str(entry.get("filename")) == selected_model:
                    self._kokoro_models.setCurrentItem(item)
            else:
                item = QTreeWidgetItem([
                    str(entry.get("label") or entry.get("filename") or ""),
                    str(entry.get("size_label") or ""),
                    status,
                ])
                item.setData(0, Qt.ItemDataRole.UserRole, str(entry.get("filename") or ""))
                if entry.get("detail"):
                    item.setToolTip(0, str(entry["detail"]))
                if entry.get("installed"):
                    item.setForeground(2, QColor(C.GREEN))  # type: ignore[arg-type]
                self._kokoro_bins.addTopLevelItem(item)
                if selected_bin and str(entry.get("filename")) == selected_bin:
                    self._kokoro_bins.setCurrentItem(item)
        self._refresh_kokoro_voices()

    def _selected_kokoro(self, which: str) -> str:
        tree = self._kokoro_models if which == "model" else self._kokoro_bins
        item = tree.currentItem()
        if item is None:
            return ""
        return str(item.data(0, Qt.ItemDataRole.UserRole) or item.text(0))

    def _on_kokoro_model_selected(self) -> None:
        model = self._selected_kokoro("model")
        entry = next((e for e in self._kokoro_entries if e.get("filename") == model), None)
        if entry is None:
            return
        required = str(entry.get("requires") or "")
        if required and not entry.get("installed"):
            self._op_label.setText(
                f"{entry['label']} — needs the {required} voice pack "
                "(downloading the model will fetch it too)."
            )
        self._refresh_kokoro_voices()

    def _on_kokoro_bin_selected(self) -> None:
        self._refresh_kokoro_voices()

    def _refresh_kokoro_voices(self) -> None:
        """Fill the voice-name combo from the pack that will actually be used."""
        try:
            from core import speech_tts, voice_catalog

            pack = self._selected_kokoro("bin") or speech_tts._kokoro_voices_path()
            if pack and not os.path.isabs(pack):
                pack = os.path.join(voice_catalog._engine_dir("kokoro"), pack)
            names = voice_catalog.kokoro_voice_names(pack)
            current = self._kokoro_voice.currentText()
            self._kokoro_voice.blockSignals(True)
            self._kokoro_voice.clear()
            self._kokoro_voice.addItems(names)
            idx = self._kokoro_voice.findText(current)
            self._kokoro_voice.setCurrentIndex(idx if idx >= 0 else 0)
            self._kokoro_voice.blockSignals(False)
        except Exception:
            pass
        installed_model = self._selected_kokoro("model")
        self._kokoro_use.setEnabled(bool(installed_model))

    def _on_kokoro_download(self) -> None:
        model = self._selected_kokoro("model")
        bin_name = self._selected_kokoro("bin")
        filename = model or bin_name
        entry = next((e for e in self._kokoro_entries if e.get("filename") == filename), None)
        if entry is None:
            self._op_label.setText("Select a model or a voice pack first.")
            return
        self._start_download(entry)

    def _on_kokoro_use(self) -> None:
        model = self._selected_kokoro("model")
        if not model:
            return
        try:
            from core import speech_tts, voice_catalog

            entry = next((e for e in self._kokoro_entries if e.get("filename") == model), None)
            required = str((entry or {}).get("requires") or "")
            speech_tts.set_kokoro_model(model)
            speech_tts.set_voice("")  # clear the legacy path override, see Piper
            if required:
                speech_tts.set_kokoro_voices(required)
            voice = self._kokoro_voice.currentText().strip()
            if voice:
                speech_tts.set_kokoro_voice(voice)
            # No voice chosen means the engine picks the pack's own default; a
            # zh pack has no af_heart, so do not name one here.
            spoken = f"voice {voice}" if voice else "the pack's default voice"
            if not voice_catalog.engine_importable("kokoro"):
                self._op_label.setText(
                    f"Using {model} with {spoken}. "
                    "Install the Kokoro runtime for it to become live."
                )
            else:
                self._op_label.setText(f"Using {model} with {spoken}.")
            self._fire_changed()
        except Exception as exc:
            self._op_label.setText(f"Could not select the model: {exc}")

    def _on_kokoro_remove(self) -> None:
        model = self._selected_kokoro("model")
        bin_name = self._selected_kokoro("bin")
        filename = model or bin_name
        entry = next((e for e in self._kokoro_entries if e.get("filename") == filename), None)
        if entry is None or not entry.get("installed"):
            self._op_label.setText("Select an installed model or pack first.")
            return
        if QMessageBox.question(
            self, "Remove", f"Delete {filename} from disk?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            from core import voice_catalog

            ok, err = voice_catalog.delete_entry(entry)
            self._op_label.setText("Removed." if ok else err)
            self._load_kokoro_catalog()
            self._fire_changed()
        except Exception as exc:
            self._op_label.setText(f"Could not remove: {exc}")

    # ------------------------------------------------------------------ #
    # Shared operations
    # ------------------------------------------------------------------ #
    def _start_download(self, entry: Dict[str, Any]) -> None:
        if self._busy:
            self._op_label.setText("Another download is still running.")
            return
        self._busy = True
        self._cancel.clear()
        self._set_busy(True)
        self._bar.setValue(0)
        label = str(entry.get("label") or entry.get("filename") or "voice")

        # A Kokoro model without its matching pack is unusable, so fetch the
        # pack first (it is the smaller of the two: fail cheap).
        queue: List[Dict[str, Any]] = [entry]
        if entry.get("engine") == "kokoro" and entry.get("kind") == "model":
            required = str(entry.get("requires") or "")
            pack = next(
                (e for e in self._kokoro_entries
                 if e.get("filename") == required and not e.get("installed")),
                None,
            )
            if pack is not None:
                queue.insert(0, pack)

        def _work():
            from core import voice_catalog

            outcome = ("", "")
            for item in queue:
                name = str(item.get("label") or item.get("filename") or "")

                def _progress(done: int, total: int, filename: str, name: str = name) -> None:
                    def _apply():
                        if total:
                            self._bar.setMaximum(100)
                            self._bar.setValue(int(done * 100 / total))
                        self._op_label.setText(
                            f"Downloading {name} — {voice_catalog.size_label(done)}"
                            f" of {voice_catalog.size_label(total)}"
                        )
                    self._run_on_ui(_apply)

                ok, err = voice_catalog.download_entry(
                    item, on_progress=_progress, cancel=self._cancel.is_set
                )
                if not ok:
                    outcome = (name, err)
                    break
                outcome = (name, "")
                self._run_on_ui(lambda n=name: self._op_label.setText(f"Downloaded {n}."))

            done_label, err = outcome
            cancelled = self._cancel.is_set()

            def _finish():
                self._busy = False
                self._set_busy(False)
                self._bar.setValue(100 if not (err or cancelled) else 0)
                if cancelled:
                    self._op_label.setText("Download cancelled.")
                elif err:
                    self._op_label.setText(f"{done_label}: {err}")
                else:
                    self._op_label.setText(
                        f"Installed {len(queue)} file(s). Restart is not needed — "
                        "the engine picks new voices up immediately."
                    )
                self._refresh_after_change(entry.get("engine"))
                self._fire_changed()

            self._run_on_ui(_finish)

        threading.Thread(target=_work, daemon=True, name="voice-download").start()

    def _refresh_after_change(self, engine: Any) -> None:
        """Re-mark installed flags without refetching the catalogue."""
        try:
            from core import voice_catalog

            if engine == "piper":
                self._piper_entries = voice_catalog._mark_installed(
                    "piper", self._piper_entries
                )
                self._render_piper()
                self._update_piper_engine_status()
            elif engine == "kokoro":
                self._kokoro_entries = voice_catalog._mark_installed(
                    "kokoro", self._kokoro_entries
                )
                self._render_kokoro()
                self._update_kokoro_engine_status()
        except Exception:
            pass

    def _install_runtime(self, engine: str) -> None:
        if self._busy:
            self._op_label.setText("Another operation is still running.")
            return
        self._busy = True
        self._cancel.clear()
        self._set_busy(True)
        self._bar.setValue(0)
        self._bar.setRange(0, 0)  # indeterminate: pip reports no percentage
        self._op_label.setText(f"Installing the {engine} runtime with pip…")

        def _work():
            from core import voice_catalog

            last: List[str] = []

            def _line(text: str) -> None:
                last.append(text)

            ok, tail = voice_catalog.install_engine(engine, on_line=_line)
            snippet = tail or (last[-1] if last else "")

            def _finish():
                self._busy = False
                self._set_busy(False)
                self._bar.setRange(0, 100)
                self._bar.setValue(100 if ok else 0)
                if ok:
                    self._op_label.setText(
                        f"{engine} runtime installed. The engine becomes available "
                        "in the Speech card without a restart."
                    )
                else:
                    self._op_label.setText(f"Install failed: {snippet}")
                self._update_piper_engine_status()
                self._update_kokoro_engine_status()
                self._fire_changed()

            self._run_on_ui(_finish)

        threading.Thread(target=_work, daemon=True, name="voice-pip").start()

    def _on_cancel(self) -> None:
        self._cancel.set()
        self._op_label.setText("Cancelling…")

    def _set_busy(self, busy: bool) -> None:
        self._cancel_btn.setEnabled(busy)
        for widget in (
            self._piper_download, self._piper_use, self._piper_remove,
            self._piper_refresh, self._piper_install,
            self._kokoro_download, self._kokoro_use, self._kokoro_remove,
            self._kokoro_install,
        ):
            try:
                widget.setEnabled(not busy)
            except Exception:
                pass

    def _open_folder(self) -> None:
        try:
            from core import voice_catalog

            path = voice_catalog._voices_dir()
            os.makedirs(path, exist_ok=True)
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
        except Exception as exc:
            self._op_label.setText(f"Could not open the folder: {exc}")

    def _fire_changed(self) -> None:
        if self._on_changed is None:
            return
        try:
            self._on_changed()
        except Exception:
            pass

    def showEvent(self, event) -> None:
        try:
            super().showEvent(event)
        except Exception:
            pass
        # The engine state can change between visits (files deleted by hand,
        # runtime installed elsewhere), so re-read it every time it opens.
        self._update_piper_engine_status()
        self._update_kokoro_engine_status()
