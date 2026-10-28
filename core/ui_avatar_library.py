"""
core/ui_avatar_library.py
The "Avatar library" dialog: browse and install Live2D models, VRM avatars,
and 3D backgrounds from within Settings, plus first-class import.

Three tabs
----------
Live2D    Cubism sample models (licence-gated, Free Material License). Every
          file is sha256-verified against the CDN's tree, and the model is
          only usable if the Cubism runtime is present in assets/avatar_live2d/vendor/.
VRM       A small list of licence-verified models (pinned digests, verified
          from the file's own declaration). Everything else goes through
          "Import .vrm" (and its verdict is shown plainly).
Backgrounds
          Installed scenes (.glb/.gltf) and panoramas (.hdr/.jpg/.png), plus a
          Poly Haven CC0 HDRI catalogue (md5+size verified at download time),
          with "Import file/folder". Selecting one switches to the custom
          background backend.

This mirrors `ui_voice_library.py` in its threading/bridge model: no blocking
work on the GUI thread, every download/import is a background thread, and all
widget updates hop through `_UiBridge` on the queued connection.
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
    QFileDialog,
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
    """Marshals worker-thread callbacks onto the GUI thread."""

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
        if n <= 0:
            return "?"
        mb = float(n) / (1024 * 1024)
        if mb < 0.1:
            return f"{float(n) / 1024:.0f} KB"
        return f"{mb:.1f} MB"
    except Exception:
        return "?"


class AvatarLibraryDialog(QDialog):
    """Browse, install, import and select avatars/backgrounds."""

    def __init__(self, parent=None, on_changed: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self.setWindowTitle("Avatar Library")
        self.setMinimumSize(920, 680)
        self._bridge = _UiBridge(self)
        self._run_on_ui = self._bridge.post
        self._on_changed = on_changed

        self._loading = False
        self._busy = False
        self._cancel = threading.Event()

        self._live2d_entries: List[Dict[str, Any]] = []
        self._live2d_installed: List[Dict[str, Any]] = []
        self._vrm_entries: List[Dict[str, Any]] = []
        self._vrm_installed: List[Dict[str, Any]] = []
        self._bg_installed: List[Dict[str, Any]] = []
        self._bg_poly: List[Dict[str, Any]] = []

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

        title = QLabel("Avatar Library")
        title.setFont(QFont("Segoe UI", 20, QFont.Weight.Black))
        title.setStyleSheet(f"color: {C.WHITE};")
        root.addWidget(title)

        sub = QLabel(
            "Install or import Live2D models, VRM avatars and 3D backgrounds. "
            "Catalogue downloads are licence-gated; imported files are recorded "
            "with their declared permissions."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        root.addWidget(sub)

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
        self._tabs.addTab(self._build_live2d_tab(), "Live2D")
        self._tabs.addTab(self._build_vrm_tab(), "VRM")
        self._tabs.addTab(self._build_bg_tab(), "Backgrounds")
        root.addWidget(self._tabs, 1)

        root.addWidget(self._op_label)
        bar_row = QHBoxLayout()
        bar_row.addWidget(self._bar, 1)
        bar_row.addWidget(self._cancel_btn)
        root.addLayout(bar_row)

        footer = QHBoxLayout()
        self._folder_btn = QPushButton("Open avatar folder")
        self._folder_btn.clicked.connect(self._open_avatar_folder)
        footer.addWidget(self._folder_btn)
        footer.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(110)
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        root.addLayout(footer)

        self._refresh_all_async()
        self.resize(self.minimumSize())
        self.setModal(True)

    def _build_live2d_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(10, 12, 10, 10)
        lay.setSpacing(8)

        row = QHBoxLayout()
        self._l2d_status = QLabel("")
        self._l2d_status.setWordWrap(True)
        self._l2d_status.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        row.addWidget(self._l2d_status, 1)
        self._l2d_refresh = QPushButton("Refresh")
        self._l2d_refresh.clicked.connect(self._refresh_live2d_async)
        row.addWidget(self._l2d_refresh)
        lay.addLayout(row)

        label = QLabel("Catalogue")
        label.setStyleSheet(f"color: {C.TEXT}; font-size: 12px; font-weight: 600;")
        lay.addWidget(label)

        self._l2d_tree = QTreeWidget()
        self._l2d_tree.setColumnCount(4)
        self._l2d_tree.setHeaderLabels(["Model", "Files", "Size", "Status"])
        self._l2d_tree.setAlternatingRowColors(True)
        self._l2d_tree.setRootIsDecorated(False)
        self._l2d_tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._l2d_tree.setColumnWidth(0, 240)
        self._l2d_tree.setColumnWidth(1, 70)
        self._l2d_tree.setColumnWidth(2, 90)
        self._l2d_tree.setColumnWidth(3, 120)
        lay.addWidget(self._l2d_tree, 1)

        label2 = QLabel("Installed")
        label2.setStyleSheet(f"color: {C.TEXT}; font-size: 12px; font-weight: 600;")
        lay.addWidget(label2)

        self._l2d_inst = QTreeWidget()
        self._l2d_inst.setColumnCount(4)
        self._l2d_inst.setHeaderLabels(["Model", "Folder", "Size", "Status"])
        self._l2d_inst.setAlternatingRowColors(True)
        self._l2d_inst.setRootIsDecorated(False)
        self._l2d_inst.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._l2d_inst.setColumnWidth(0, 240)
        self._l2d_inst.setColumnWidth(1, 330)
        self._l2d_inst.setColumnWidth(2, 90)
        self._l2d_inst.setColumnWidth(3, 80)
        lay.addWidget(self._l2d_inst, 1)

        acts = QHBoxLayout()
        self._l2d_download = QPushButton("Download selected")
        self._l2d_download.clicked.connect(self._on_l2d_download)
        acts.addWidget(self._l2d_download)
        self._l2d_use = QPushButton("Use selected")
        self._l2d_use.clicked.connect(self._on_l2d_use)
        acts.addWidget(self._l2d_use)
        self._l2d_remove = QPushButton("Remove selected")
        self._l2d_remove.clicked.connect(self._on_l2d_remove)
        acts.addWidget(self._l2d_remove)
        acts.addStretch(1)
        acts.addWidget(QLabel("Live2D Cubism runtime:"))
        self._l2d_runtime = QLabel("-")
        self._l2d_runtime.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        acts.addWidget(self._l2d_runtime)
        acts.addWidget(QLabel("|"))
        self._l2d_import = QPushButton("Import folder…")
        self._l2d_import.clicked.connect(self._on_l2d_import)
        acts.addWidget(self._l2d_import)
        self._l2d_open = QPushButton("Open folder")
        self._l2d_open.clicked.connect(self._open_live2d_folder)
        acts.addWidget(self._l2d_open)
        lay.addLayout(acts)
        return tab

    def _build_vrm_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(10, 12, 10, 10)
        lay.setSpacing(8)

        row = QHBoxLayout()
        self._vrm_status = QLabel("")
        self._vrm_status.setWordWrap(True)
        self._vrm_status.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        row.addWidget(self._vrm_status, 1)
        self._vrm_refresh = QPushButton("Refresh")
        self._vrm_refresh.clicked.connect(self._refresh_vrm_async)
        row.addWidget(self._vrm_refresh)
        lay.addLayout(row)

        label = QLabel("Catalogue (licence-verified)")
        label.setStyleSheet(f"color: {C.TEXT}; font-size: 12px; font-weight: 600;")
        lay.addWidget(label)
        self._vrm_tree = QTreeWidget()
        self._vrm_tree.setColumnCount(4)
        self._vrm_tree.setHeaderLabels(["Model", "Spec", "Size", "Licence"])
        self._vrm_tree.setAlternatingRowColors(True)
        self._vrm_tree.setRootIsDecorated(False)
        self._vrm_tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._vrm_tree.setColumnWidth(0, 240)
        self._vrm_tree.setColumnWidth(1, 70)
        self._vrm_tree.setColumnWidth(2, 90)
        self._vrm_tree.setColumnWidth(3, 420)
        lay.addWidget(self._vrm_tree, 1)

        label2 = QLabel("Installed")
        label2.setStyleSheet(f"color: {C.TEXT}; font-size: 12px; font-weight: 600;")
        lay.addWidget(label2)
        self._vrm_inst = QTreeWidget()
        self._vrm_inst.setColumnCount(4)
        self._vrm_inst.setHeaderLabels(["Model", "Spec", "Size", "Verdict"])
        self._vrm_inst.setAlternatingRowColors(True)
        self._vrm_inst.setRootIsDecorated(False)
        self._vrm_inst.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._vrm_inst.setColumnWidth(0, 240)
        self._vrm_inst.setColumnWidth(1, 70)
        self._vrm_inst.setColumnWidth(2, 90)
        self._vrm_inst.setColumnWidth(3, 420)
        lay.addWidget(self._vrm_inst, 1)

        acts = QHBoxLayout()
        self._vrm_download = QPushButton("Download selected")
        self._vrm_download.clicked.connect(self._on_vrm_download)
        acts.addWidget(self._vrm_download)
        self._vrm_use = QPushButton("Use selected")
        self._vrm_use.clicked.connect(self._on_vrm_use)
        acts.addWidget(self._vrm_use)
        self._vrm_remove = QPushButton("Remove selected")
        self._vrm_remove.clicked.connect(self._on_vrm_remove)
        acts.addWidget(self._vrm_remove)
        acts.addStretch(1)
        self._vrm_import = QPushButton("Import .vrm…")
        self._vrm_import.clicked.connect(self._on_vrm_import)
        acts.addWidget(self._vrm_import)
        self._vrm_open = QPushButton("Open folder")
        self._vrm_open.clicked.connect(self._open_vrm_folder)
        acts.addWidget(self._vrm_open)
        lay.addLayout(acts)
        return tab

    def _build_bg_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(10, 12, 10, 10)
        lay.setSpacing(8)

        row = QHBoxLayout()
        self._bg_status = QLabel("")
        self._bg_status.setWordWrap(True)
        self._bg_status.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        row.addWidget(self._bg_status, 1)
        self._bg_refresh = QPushButton("Refresh")
        self._bg_refresh.clicked.connect(self._refresh_bg_async)
        row.addWidget(self._bg_refresh)
        lay.addLayout(row)

        label1 = QLabel("Installed backgrounds")
        label1.setStyleSheet(f"color: {C.TEXT}; font-size: 12px; font-weight: 600;")
        lay.addWidget(label1)
        self._bg_inst_tree = QTreeWidget()
        self._bg_inst_tree.setColumnCount(4)
        self._bg_inst_tree.setHeaderLabels(["Background", "Kind", "Size", "Source"])
        self._bg_inst_tree.setAlternatingRowColors(True)
        self._bg_inst_tree.setRootIsDecorated(False)
        self._bg_inst_tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._bg_inst_tree.setColumnWidth(0, 240)
        self._bg_inst_tree.setColumnWidth(1, 100)
        self._bg_inst_tree.setColumnWidth(2, 90)
        self._bg_inst_tree.setColumnWidth(3, 420)
        lay.addWidget(self._bg_inst_tree, 1)

        label2 = QLabel("Poly Haven CC0 HDRIs")
        label2.setStyleSheet(f"color: {C.TEXT}; font-size: 12px; font-weight: 600;")
        lay.addWidget(label2)
        srow = QHBoxLayout()
        self._bg_search = QLineEdit()
        self._bg_search.setPlaceholderText("Search HDRIs by name or tag…")
        self._bg_search.textChanged.connect(lambda _: self._render_bg_poly())
        srow.addWidget(self._bg_search, 1)
        self._bg_res = QComboBox()
        self._bg_res.addItem("1K", "1k")
        self._bg_res.addItem("2K (recommended)", "2k")
        self._bg_res.addItem("4K", "4k")
        self._bg_res.setCurrentIndex(1)
        srow.addWidget(self._bg_res)
        lay.addLayout(srow)
        self._bg_poly_tree = QTreeWidget()
        self._bg_poly_tree.setColumnCount(3)
        self._bg_poly_tree.setHeaderLabels(["HDRI", "Categories", "Tags"])
        self._bg_poly_tree.setAlternatingRowColors(True)
        self._bg_poly_tree.setRootIsDecorated(False)
        self._bg_poly_tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._bg_poly_tree.setColumnWidth(0, 240)
        self._bg_poly_tree.setColumnWidth(1, 240)
        self._bg_poly_tree.setColumnWidth(2, 360)
        lay.addWidget(self._bg_poly_tree, 1)

        acts = QHBoxLayout()
        self._bg_use = QPushButton("Use selected")
        self._bg_use.clicked.connect(self._on_bg_use)
        acts.addWidget(self._bg_use)
        self._bg_remove = QPushButton("Remove selected")
        self._bg_remove.clicked.connect(self._on_bg_remove)
        acts.addWidget(self._bg_remove)
        acts.addWidget(QLabel("|"))
        self._bg_import_file = QPushButton("Import file…")
        self._bg_import_file.clicked.connect(self._on_bg_import_file)
        acts.addWidget(self._bg_import_file)
        self._bg_import_dir = QPushButton("Import folder…")
        self._bg_import_dir.clicked.connect(self._on_bg_import_dir)
        acts.addWidget(self._bg_import_dir)
        acts.addStretch(1)
        self._bg_dl = QPushButton("Download selected (Poly Haven)")
        self._bg_dl.clicked.connect(self._on_bg_download_poly)
        acts.addWidget(self._bg_dl)
        self._bg_open = QPushButton("Open folder")
        self._bg_open.clicked.connect(self._open_bg_folder)
        acts.addWidget(self._bg_open)
        lay.addLayout(acts)
        return tab

    # helpers + rendering follow; truncated for brevity in this write - will complete
    def _fire_changed(self):
        if self._on_changed:
            try:
                self._on_changed()
            except Exception:
                pass

    def _on_cancel(self):
        if self._busy:
            self._cancel.set()
            self._cancel_btn.setEnabled(False)
            self._op_label.setText("Cancelling…")

    def _open_avatar_folder(self):
        try:
            from core import avatar_catalog
            path = avatar_catalog.avatar_root()
            os.startfile(path) if hasattr(os, "startfile") else os.system(f'explorer "{path}"')
        except Exception as exc:
            self._op_label.setText(f"Could not open folder: {exc}")


    def _open_live2d_folder(self):
        try:
            from core import avatar_catalog
            path = avatar_catalog.live2d_dir()
            if hasattr(os, "startfile"):
                os.startfile(path)
            else:
                os.system(f'explorer "{path}"')
        except Exception as exc:
            self._op_label.setText(f"Could not open folder: {exc}")

    def _open_vrm_folder(self):
        try:
            from core import avatar_catalog
            path = avatar_catalog.vrm_dir()
            if hasattr(os, "startfile"):
                os.startfile(path)
            else:
                os.system(f'explorer "{path}"')
        except Exception as exc:
            self._op_label.setText(f"Could not open folder: {exc}")

    def _open_bg_folder(self):
        try:
            from core import avatar_catalog
            path = avatar_catalog.backgrounds_dir()
            if hasattr(os, "startfile"):
                os.startfile(path)
            else:
                os.system(f'explorer "{path}"')
        except Exception as exc:
            self._op_label.setText(f"Could not open folder: {exc}")

    def _set_busy(self, busy: bool, label: str = ""):
        self._busy = busy
        if busy:
            self._cancel.clear()
        else:
            self._cancel.set()
        self._cancel_btn.setEnabled(busy)
        if label:
            self._op_label.setText(label)

    def _progress(self, done, total, label):
        if not total:
            return
        pct = int(min(100, (done / total) * 100))
        def _apply():
            self._bar.setValue(pct)
            if label:
                self._op_label.setText(f"{label} - {_size_label(done)} / {_size_label(total)}")
        self._run_on_ui(_apply)

    # Live2D
    def _refresh_all_async(self):
        self._refresh_live2d_async()
        self._refresh_vrm_async()
        self._refresh_bg_async()

    def _refresh_live2d_async(self):
        if self._loading:
            return
        self._loading = True
        self._l2d_refresh.setEnabled(False)
        self._op_label.setText("Loading Live2D catalogue...")
        def _work():
            try:
                from core import avatar_catalog, avatar_runtime
                entries = avatar_catalog.live2d_catalog(force=True)
                installed = avatar_catalog.installed_live2d()
                runtime = avatar_runtime.runtime_present()
            except Exception as exc:
                entries, installed, runtime = [], [], []
                error = f"Live2D catalogue error: {exc}"
                def _e():
                    self._op_label.setText(error)
                self._run_on_ui(_e)
                def _done():
                    self._loading = False
                    self._l2d_refresh.setEnabled(True)
                self._run_on_ui(_done)
                return
            def _apply():
                self._loading = False
                self._l2d_refresh.setEnabled(True)
                self._live2d_entries = entries
                self._live2d_installed = installed
                self._render_l2d(entries, installed, runtime)
            self._run_on_ui(_apply)
        threading.Thread(target=_work, daemon=True, name="l2d-cat").start()

    def _render_l2d(self, entries, installed, runtime):
        inst_ids = {os.path.basename(i["folder"]) for i in installed}
        self._l2d_tree.clear()
        for e in entries:
            status = "Installed" if e["id"] in inst_ids else ""
            item = QTreeWidgetItem([e["name"], str(e["count"]), _size_label(e["bytes"]), status])
            item.setData(0, Qt.ItemDataRole.UserRole, ("catalog", e["id"]))
            if status:
                item.setForeground(3, QColor(C.GREEN))
            item.setToolTip(0, e.get("note", ""))
            self._l2d_tree.addTopLevelItem(item)
        self._l2d_inst.clear()
        for i in installed:
            item = QTreeWidgetItem([i["name"], i["folder"], _size_label(i["bytes"]), ""])
            item.setData(0, Qt.ItemDataRole.UserRole, ("installed", i["id"]))
            self._l2d_inst.addTopLevelItem(item)
        if runtime:
            self._l2d_runtime.setText("present")
            self._l2d_runtime.setStyleSheet(f"color: {C.GREEN}; font-size: 11px;")
        else:
            self._l2d_runtime.setText("not installed")
            self._l2d_runtime.setStyleSheet("color: #ff9f43; font-size: 11px;")
        self._op_label.setText(f"{len(entries)} in catalogue - {len(installed)} installed")

    def _sel_l2d_cat(self):
        item = self._l2d_tree.currentItem()
        if not item:
            return None
        d = item.data(0, Qt.ItemDataRole.UserRole)
        if not d or d[0] != "catalog":
            return None
        return next((e for e in self._live2d_entries if e["id"] == d[1]), None)

    def _sel_l2d_inst(self):
        item = self._l2d_inst.currentItem()
        if not item:
            return None
        d = item.data(0, Qt.ItemDataRole.UserRole)
        if not d or d[0] != "installed":
            return None
        return next((i for i in self._live2d_installed if i["id"] == d[1]), None)

    def _on_l2d_download(self):
        entry = self._sel_l2d_cat()
        if entry is None:
            self._op_label.setText("Select a catalogue model.")
            return
        if self._busy:
            return
        self._set_busy(True, f"Downloading {entry['name']}...")
        self._bar.setValue(0)
        cancel = self._cancel
        def _work():
            from core import avatar_catalog
            def _p(done, total, label):
                self._progress(done, total, label)
            ok, err = avatar_catalog.install_live2d(entry, on_progress=_p, cancel=lambda: cancel.is_set())
            def _done():
                self._set_busy(False)
                self._bar.setValue(100 if ok else 0)
                self._op_label.setText("Installed." if ok else err)
                if ok:
                    self._fire_changed()
                    self._refresh_live2d_async()
            self._run_on_ui(_done)
        threading.Thread(target=_work, daemon=True, name="l2d-dl").start()

    def _on_l2d_import(self):
        path = QFileDialog.getExistingDirectory(self, "Select Live2D model folder")
        if not path:
            return
        self._set_busy(True, "Importing Live2D folder...")
        def _work():
            from core import avatar_catalog
            ok, err = avatar_catalog.import_live2d_folder(path)
            def _done():
                self._set_busy(False)
                self._op_label.setText("Imported." if ok else err)
                if ok:
                    self._fire_changed()
                    self._refresh_live2d_async()
            self._run_on_ui(_done)
        threading.Thread(target=_work, daemon=True, name="l2d-import").start()

    def _on_l2d_use(self):
        inst = self._sel_l2d_inst()
        if inst is None:
            self._op_label.setText("Select an installed Live2D model.")
            return
        try:
            from core import avatar_runtime as ar
            ar.set_asset_path("live2d", inst["folder"])
            ar.set_active("live2d")
            ar.set_enabled(True)
            self._op_label.setText(f"Using Live2D model {inst['name']}")
            self._fire_changed()
        except Exception as exc:
            self._op_label.setText(f"Could not select: {exc}")

    def _on_l2d_remove(self):
        inst = self._sel_l2d_inst()
        if inst is None:
            return
        if QMessageBox.question(self, "Remove Live2D model", f"Delete {inst['name']}?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            from core import avatar_catalog
            ok, err = avatar_catalog.delete_live2d(inst["id"])
            self._op_label.setText("Removed." if ok else err)
            if ok:
                self._fire_changed()
                self._refresh_live2d_async()
        except Exception as exc:
            self._op_label.setText(f"Could not remove: {exc}")

    # VRM
    def _refresh_vrm_async(self):
        if self._loading:
            return
        self._loading = True
        self._vrm_refresh.setEnabled(False)
        def _work():
            try:
                from core import avatar_catalog
                self._vrm_entries = avatar_catalog.vrm_catalog()
                self._vrm_installed = avatar_catalog.installed_vrm()
            except Exception as exc:
                self._vrm_entries, self._vrm_installed = [], []
                error = f"VRM error: {exc}"
                def _e():
                    self._op_label.setText(error)
                self._run_on_ui(_e)
            def _apply():
                self._loading = False
                self._vrm_refresh.setEnabled(True)
                self._render_vrm()
            self._run_on_ui(_apply)
        threading.Thread(target=_work, daemon=True, name="vrm-cat").start()

    def _render_vrm(self):
        self._vrm_tree.clear()
        for e in self._vrm_entries:
            item = QTreeWidgetItem([e["name"], e.get("spec", ""), _size_label(e["bytes"]), e.get("licence", "")])
            item.setData(0, Qt.ItemDataRole.UserRole, ("catalog", e["id"]))
            item.setToolTip(0, e.get("note", ""))
            self._vrm_tree.addTopLevelItem(item)
        self._vrm_inst.clear()
        for i in self._vrm_installed:
            ver = i.get("verdict", "")
            item = QTreeWidgetItem([i["name"], i.get("spec", ""), _size_label(i["bytes"]), ver])
            item.setData(0, Qt.ItemDataRole.UserRole, ("installed", i["id"]))
            if ver and ver.lower().startswith(("permissive", "cc0")):
                item.setForeground(3, QColor(C.GREEN))
            else:
                item.setForeground(3, QColor("#ff9f43"))
            self._vrm_inst.addTopLevelItem(item)
        self._vrm_status.setText(f"{len(self._vrm_entries)} verified in catalogue - {len(self._vrm_installed)} installed")

    def _sel_vrm_cat(self):
        item = self._vrm_tree.currentItem()
        if not item:
            return None
        d = item.data(0, Qt.ItemDataRole.UserRole)
        if not d or d[0] != "catalog":
            return None
        return next((e for e in self._vrm_entries if e["id"] == d[1]), None)

    def _sel_vrm_inst(self):
        item = self._vrm_inst.currentItem()
        if not item:
            return None
        d = item.data(0, Qt.ItemDataRole.UserRole)
        if not d or d[0] != "installed":
            return None
        return next((i for i in self._vrm_installed if i["id"] == d[1]), None)

    def _on_vrm_download(self):
        entry = self._sel_vrm_cat()
        if entry is None:
            self._op_label.setText("Select a catalogue VRM.")
            return
        if self._busy:
            return
        self._set_busy(True, f"Downloading {entry['name']}...")
        self._bar.setValue(0)
        cancel = self._cancel
        def _work():
            from core import avatar_catalog
            def _p(d, t, l):
                self._progress(d, t, l)
            ok, err = avatar_catalog.install_vrm(entry, on_progress=_p, cancel=lambda: cancel.is_set())
            def _done():
                self._set_busy(False)
                self._bar.setValue(100 if ok else 0)
                self._op_label.setText("Installed." if ok else err)
                if ok:
                    self._fire_changed()
                    self._refresh_vrm_async()
            self._run_on_ui(_done)
        threading.Thread(target=_work, daemon=True, name="vrm-dl").start()

    def _on_vrm_import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import VRM file", "", "VRM (*.vrm);;All (*.*)")
        if not path:
            return
        self._set_busy(True, "Importing VRM...")
        def _work():
            from core import avatar_catalog
            ok, err = avatar_catalog.import_vrm(path)
            def _done():
                self._set_busy(False)
                self._op_label.setText("Imported." if ok else err)
                if ok:
                    self._fire_changed()
                    self._refresh_vrm_async()
            self._run_on_ui(_done)
        threading.Thread(target=_work, daemon=True, name="vrm-import").start()

    def _on_vrm_use(self):
        inst = self._sel_vrm_inst()
        if inst is None:
            self._op_label.setText("Select an installed VRM.")
            return
        try:
            from core import avatar_runtime as ar
            ar.set_asset_path("vrm", inst["path"])
            ar.set_active("vrm")
            ar.set_enabled(True)
            self._op_label.setText(f"Using VRM {inst['name']} ({inst['verdict']})")
            self._fire_changed()
        except Exception as exc:
            self._op_label.setText(f"Could not select: {exc}")

    def _on_vrm_remove(self):
        inst = self._sel_vrm_inst()
        if inst is None:
            return
        if QMessageBox.question(self, "Remove VRM", f"Delete {inst['name']}?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            from core import avatar_catalog
            ok, err = avatar_catalog.delete_vrm(inst["id"])
            self._op_label.setText("Removed." if ok else err)
            if ok:
                self._fire_changed()
                self._refresh_vrm_async()
        except Exception as exc:
            self._op_label.setText(f"Could not remove: {exc}")

    # Backgrounds
    def _refresh_bg_async(self):
        if self._loading:
            return
        self._loading = True
        self._bg_refresh.setEnabled(False)
        def _work():
            try:
                from core import avatar_catalog
                inst = avatar_catalog.list_backgrounds()
                poly = avatar_catalog.polyhaven_catalog(force=False)
            except Exception as exc:
                inst, poly = [], []
                error = f"Background error: {exc}"
                def _e():
                    self._op_label.setText(error)
                self._run_on_ui(_e)
            def _apply():
                self._loading = False
                self._bg_refresh.setEnabled(True)
                self._bg_installed = inst
                self._bg_poly = poly
                self._render_bg_inst()
                self._render_bg_poly()
            self._run_on_ui(_apply)
        threading.Thread(target=_work, daemon=True, name="bg-cat").start()

    def _render_bg_inst(self):
        self._bg_inst_tree.clear()
        for b in self._bg_installed:
            prov = b.get("provenance") or {}
            src = prov.get("licence") or prov.get("source") or ""
            item = QTreeWidgetItem([b["name"], b.get("kind", ""), _size_label(b.get("bytes") or b.get("total_bytes")), str(src)[:60]])
            item.setData(0, Qt.ItemDataRole.UserRole, ("inst", b["name"]))
            if b.get("warnings"):
                item.setToolTip(0, "; ".join(b["warnings"]))
            self._bg_inst_tree.addTopLevelItem(item)
        self._bg_status.setText(f"{len(self._bg_installed)} background(s) installed")

    def _render_bg_poly(self):
        needle = self._bg_search.text().strip().lower()
        self._bg_poly_tree.clear()
        for e in self._bg_poly:
            cats = " ".join(e.get("categories") or [])
            tags = " ".join(e.get("tags") or [])
            if needle and needle not in (e["name"].lower() + " " + cats + " " + tags):
                continue
            item = QTreeWidgetItem([e["name"], ", ".join((e.get("categories") or [])[:3]), ", ".join((e.get("tags") or [])[:5])])
            item.setData(0, Qt.ItemDataRole.UserRole, ("poly", e["id"]))
            self._bg_poly_tree.addTopLevelItem(item)

    def _sel_bg_inst(self):
        item = self._bg_inst_tree.currentItem()
        if not item:
            return None
        d = item.data(0, Qt.ItemDataRole.UserRole)
        if not d or d[0] != "inst":
            return None
        return next((b for b in self._bg_installed if b["name"] == d[1]), None)

    def _sel_bg_poly(self):
        item = self._bg_poly_tree.currentItem()
        if not item:
            return None
        d = item.data(0, Qt.ItemDataRole.UserRole)
        if not d or d[0] != "poly":
            return None
        return next((e for e in self._bg_poly if e["id"] == d[1]), None)

    def _on_bg_use(self):
        b = self._sel_bg_inst()
        if b is None:
            self._op_label.setText("Select an installed background.")
            return
        try:
            from core import avatar_runtime as ar
            ar.set_asset_path("scene_background", b["folder"])
            ar.set_active("scene_background")
            ar.set_enabled(True)
            self._op_label.setText(f"Using custom background {b['name']} ({b['kind']})")
            self._fire_changed()
        except Exception as exc:
            self._op_label.setText(f"Could not select: {exc}")

    def _on_bg_remove(self):
        b = self._sel_bg_inst()
        if b is None:
            return
        if QMessageBox.question(self, "Remove background", f"Delete {b['name']}?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            from core import avatar_catalog
            ok, err = avatar_catalog.delete_background(b["name"])
            self._op_label.setText("Removed." if ok else err)
            if ok:
                self._fire_changed()
                self._refresh_bg_async()
        except Exception as exc:
            self._op_label.setText(f"Could not remove: {exc}")

    def _on_bg_import_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import background file", "", "3D/Panorama (*.glb *.gltf *.hdr *.jpg *.jpeg *.png);;All (*.*)")
        if not path:
            return
        self._set_busy(True, "Importing background...")
        cancel = self._cancel
        def _work():
            from core import avatar_catalog
            def _p(d, t, l):
                self._progress(d, t, l)
            ok, err = avatar_catalog.import_background(path, on_progress=_p, cancel=lambda: cancel.is_set())
            def _done():
                self._set_busy(False)
                self._bar.setValue(100 if ok else 0)
                self._op_label.setText("Imported." if ok else err)
                if ok:
                    self._fire_changed()
                    self._refresh_bg_async()
            self._run_on_ui(_done)
        threading.Thread(target=_work, daemon=True, name="bg-import").start()

    def _on_bg_import_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select background folder")
        if not path:
            return
        self._set_busy(True, "Importing background folder...")
        cancel = self._cancel
        def _work():
            from core import avatar_catalog
            def _p(d, t, l):
                self._progress(d, t, l)
            ok, err = avatar_catalog.import_background(path, on_progress=_p, cancel=lambda: cancel.is_set())
            def _done():
                self._set_busy(False)
                self._bar.setValue(100 if ok else 0)
                self._op_label.setText("Imported." if ok else err)
                if ok:
                    self._fire_changed()
                    self._refresh_bg_async()
            self._run_on_ui(_done)
        threading.Thread(target=_work, daemon=True, name="bg-import-dir").start()

    def _on_bg_download_poly(self):
        e = self._sel_bg_poly()
        if e is None:
            self._op_label.setText("Select a Poly Haven HDRI.")
            return
        if self._busy:
            return
        res = self._bg_res.currentData() or "2k"
        self._set_busy(True, f"Downloading {e['name']} ({res})...")
        self._bar.setValue(0)
        cancel = self._cancel
        def _work():
            from core import avatar_catalog
            def _p(d, t, l):
                self._progress(d, t, l)
            ok, err = avatar_catalog.download_polyhaven(e["id"], resolution=res, on_progress=_p, cancel=lambda: cancel.is_set())
            def _done():
                self._set_busy(False)
                self._bar.setValue(100 if ok else 0)
                self._op_label.setText("Downloaded." if ok else err)
                if ok:
                    self._fire_changed()
                    self._refresh_bg_async()
            self._run_on_ui(_done)
        threading.Thread(target=_work, daemon=True, name="poly-dl").start()
