"""MainWindow's _RuntimeMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations

import psutil
import time

from PyQt6.QtWidgets import (QFileDialog)
from pathlib import (Path)


def bind(*, _active_net_label, _camera_available, _metrics):
    globals().update(_active_net_label=_active_net_label, _camera_available=_camera_available, _metrics=_metrics)


class _RuntimeMixin:
    def _tick_clock(self):
        if hasattr(self, "_clock_lbl") and self._clock_lbl is not None:
            try:
                self._clock_lbl.setText(time.strftime("%H:%M"))
            except Exception:
                pass
        if hasattr(self, "_date_lbl") and self._date_lbl is not None:
            try:
                self._date_lbl.setText(time.strftime("%A • %d %b"))
            except Exception:
                pass

    def _update_metrics(self):
        if not hasattr(self, "_bar_cpu"):
            return
        snap = _metrics.snapshot()

        # CPU
        cpu = snap["cpu"]
        if hasattr(self, "_bar_cpu"):
            self._bar_cpu.set_value(cpu, f"{cpu:.0f}%")
        if hasattr(self, "_stat_cpu"):
            cpu_cores = psutil.cpu_count(logical=False) or psutil.cpu_count(logical=True) or 0
            cpu_threads = psutil.cpu_count(logical=True) or cpu_cores
            self._stat_cpu.set_value(
                f"{cpu:.0f}%",
                int(cpu),
                f"{cpu_threads} threads / {cpu_cores or cpu_threads} cores",
            )

        # MEM
        mem = snap["mem"]
        if hasattr(self, "_bar_mem"):
            self._bar_mem.set_value(mem, f"{mem:.0f}%")
        
        if hasattr(self, "_uptime_lbl"):
            try:
                boot = psutil.boot_time()
                uptime_min = int((time.time() - boot) / 60)
                self._uptime_lbl.setText(f"Up: {uptime_min // 60}h {uptime_min % 60}m")
            except: pass

        if hasattr(self, "_stat_mem"):
            vm = psutil.virtual_memory()
            used_gb = vm.used / (1024**3)
            total_gb = vm.total / (1024**3)
            self._stat_mem.set_value(
                f"{mem:.0f}%",
                int(mem),
                f"{used_gb:.1f} GB / {total_gb:.1f} GB used",
            )

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)  # 10 MB/s = %100
        if hasattr(self, "_bar_net"):
            self._bar_net.set_value(net_pct, net_str)
        if hasattr(self, "_stat_net"):
            self._stat_net.set_value(
                net_str if net >= 1 else "ONLINE",
                int(net_pct),
                _active_net_label(),
            )

        # GPU
        gpu = snap["gpu"]
        if hasattr(self, "_bar_gpu"):
            if gpu >= 0:
                self._bar_gpu.set_value(gpu, f"{gpu:.0f}%")
            else:
                self._bar_gpu.set_value(0, "N/A")

        # TMP
        tmp = snap["tmp"]
        if hasattr(self, "_bar_tmp"):
            if tmp >= 0:
                tmp_pct = min(100, (tmp / 100) * 100)
                self._bar_tmp.set_value(tmp_pct, f"{tmp:.0f}°C")
            else:
                self._bar_tmp.set_value(0, "N/A")
        if hasattr(self, "_stat_cam"):
            cam_on = _camera_available()
            self._stat_cam.set_value(
                "ON" if cam_on else "OFF",
                100 if cam_on else 0,
                "Webcam detected" if cam_on else "No camera found",
            )

        if hasattr(self, "_uptime_lbl"):
            try:
                boot_t  = psutil.boot_time()
                elapsed = time.time() - boot_t
                h = int(elapsed // 3600)
                m = int((elapsed % 3600) // 60)
                self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
            except Exception:
                self._uptime_lbl.setText("UP  --:--")

        if hasattr(self, "_proc_lbl"):
            try:
                proc_count = len(psutil.pids())
                self._proc_lbl.setText(f"PROC  {proc_count}")
            except Exception:
                self._proc_lbl.setText("PROC  --")


    def _browse_attachment(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Attach a file to Brahma Evo", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._on_file_selected(path)
