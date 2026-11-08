# WP-07 Scan Report: Secondary UI Panels, Avatars/Voice UI, WebEngine HUDs & Visual Assets (UI Part B)

## Metadata
- **Work Package:** WP-07 (Secondary UI Panels, Avatars/Voice UI, WebEngine HUDs & Visual Assets — UI Part B)
- **Baseline Commit:** `6439b2efde3c36b1d34b21e4cc5e93baff31b2b5` (`6439b2e`)
- **Scan Date:** 2026-11-07
- **Auditor:** Static Systems & Security Audit Agent (WP-07 Lead)
- **Subsystem Health:** **AMBER / WARNING** (Multiple thread safety violations, module-level Chromium engine imports violating Invariant `I-25`, unescaped string interpolations in `runJavaScript`, a zombie 2.5s polling loop in hidden legacy smart-home UI, blocking camera/MediaPipe inference on the Qt main thread, and PyInstaller frozen path breakages).

---

## 1. Subsystem Architecture & Data-Flow Summary

WP-07 encompasses the secondary visual layer, dynamic holographic HUDs, multimodal voice and avatar libraries, companion IoT/smart-home pages, floating transient overlays, and WebEngine-based visual assets across Brahma AI Evo.

### 1.1 Architectural Composition

```
+--------------------------------------------------------------------------------------------------+
|                                    Brahma AI UI Architecture                                      |
+--------------------------------------------------------------------------------------------------+
                                                 |
             +-----------------------------------+-----------------------------------+
             |                                                                       |
             v                                                                       v
+--------------------------+                                           +---------------------------+
| Secondary Panels & Pages |                                           | WebEngine Holographic HUDs|
+--------------------------+                                           +---------------------------+
| - ui_agent_panel.py      |                                           | - ui_bg.py (Reactor 3D)   |
| - ui_security_panel.py   |                                           | - globe_window.py (3D/2D) |
| - ui_skill_review.py     |                                           | - circuit_hud.py (Schem)  |
| - ui_avatar_library.py   |                                           | - radar_hud.py (LAN PPI)  |
| - ui_voice_library.py    |                                           | - space_cleaner_hud.py    |
| - smart_home_page_new.py |                                           +---------------------------+
| - ui_devices.py (Gateway)|                                                         |
| - ui_setup.py / flow     |                                                         v
| - ui_telemetry.py        |                                           +---------------------------+
| - ui_sysmetrics.py       |                                           | Embedded WebGL/HTML Scenes|
| - ui_transients.py       |                                           +---------------------------+
| - ui_gesture.py          |                                           | - assets/web_background/  |
+--------------------------+                                           | - assets/avatar_vrm/      |
             |                                                         | - assets/avatar_live2d/   |
             v                                                         | - assets/backgrounds/     |
+--------------------------+                                           | - assets/globe/           |
|  PyQt6 Native Controls   |                                           | - assets/radar/           |
| (QDialog, QFrame, QTimer)|                                           | - assets/space_cleaner/   |
+--------------------------+                                           +---------------------------+
```

### 1.2 Data Flow & Thread Boundaries

1. **Avatar Emotion & Audio Pipeline:**
   Emotion tags parsed by `BrahmaLive._split_emotion_tag` are routed into `core/avatar_runtime.py`. The active visual host (`core/ui_bg.py` via `BackgroundController`) marshals state, audio levels, and normalized cursor coordinates through `QWebEngineView.page().runJavaScript(...)` to drive the active asset scene (`assets/web_background/index.html`, `assets/avatar_vrm/index.html`, or `assets/avatar_live2d/index.html`). All scenes adhere to a canonical 7-hook contract (`setBrahmaState`, `setAudioLevel`, `setPointerNorm`, `setAvatarEmotion`, `__pauseAnimation`, `__resumeAnimation`, `__getAvatarState`).

2. **WebChannel Dual-Bridge HUDs:**
   Interactive holographic overlays (`radar_hud.py`, `globe_window.py`, `space_cleaner_hud.py`) bind a native `QObject` bridge (`RadarBridge`, `GlobeBridge`, `SpaceCleanerBridge`) to the Chromium DOM via `QWebChannel`. Background operations (subnet sweeps, ping tests, drive purges, WMI storage scans) execute in daemon background threads, which emit PyQt signals connected via Qt `QueuedConnection` back to the main GUI thread to invoke `runJavaScript(...)`.

3. **Smart Home Split:**
   The Brahma interface hosts two distinct device surfaces:
   - `smart_home_page_new.py` (`BrahmaHomePage`): Active primary page (page index 1) managing local smart plugs, lights, thermostats, and scenes.
   - `core/ui_devices.py`: Houses `BrahmaConnectDevicesPage` (page index 2, companion phone gateway and push alerts), but also retains 512 lines of legacy `SmartDevicesSection` UI code hidden inside `ui_panels.py` container.

---

## 2. File-by-File Inventory & Health Matrix

| # | File Path | SLOC | Primary Symbol(s) | Health Status | Primary Architectural & Security Concerns |
|---|---|---|---|---|---|
| 1 | `core/ui_agent_panel.py` | 304 | `AgentBridgeCard`, `_AsyncBridgeWorker` | AMBER | `_AsyncBridgeWorker` (`QThread`) lacks cancellation/join on widget deletion; risk of `RuntimeError: wrapped C/C++ object has been deleted`. |
| 2 | `core/ui_openai_port.py` | 328 | `OpenAIPortDialog`, `_ProbeWorker` | GREEN | Clean token input; checks port availability, uses `QThread` safely with `finished.connect`. |
| 3 | `core/ui_security_panel.py` | 315 | `SecurityPanel`, `_AuditLogModel` | GREEN | Clean read of audit logs and sandbox policy toggles; proper signal mapping. |
| 4 | `core/ui_skill_review.py` | 628 | `SkillReviewDialog`, `DiffViewer` | GREEN | Robust AST diff viewer and code review sandbox; proper syntax highlighting. |
| 5 | `core/ui_avatar_library.py` | 955 | `AvatarLibraryDialog`, `_UiBridge` | AMBER | Model download threads run in background without cooperative cancellation on dialog close; potential crash if widget is deleted. |
| 6 | `core/ui_avatar_audio.py` | 64 | `AvatarAudioVisualizer` | GREEN | Lightweight QPainter waveform widget; zero thread leakage. |
| 7 | `core/ui_voice_library.py` | 849 | `VoiceLibraryDialog`, `_Downloader` | AMBER | Pip install and voice model download threads lack graceful teardown if dialog is dismissed prematurely. |
| 8 | `core/ui_devices.py` | 1312 | `SmartDevicesSection`, `BrahmaConnectDevicesPage` | RED | **Zombie polling loop & plaintext PIN storage**: `SmartDevicesSection` is hidden in `ui_panels.py`, yet its internal `QTimer(2500)` polls `smart_home_service.list_devices()` indefinitely; `BrahmaConnectDevicesPage._set_device_pin` (`lines 1276-1292`) writes the user's physical Android phone lockscreen PIN (`device_pins[device_id]`) in plaintext into `app_settings.json` via non-atomic `open(..., "w")`. |
| 9 | `smart_home_page_new.py` | 1396 | `BrahmaHomePage`, `_DeviceTile`, `AddDeviceDialog` | GREEN | Well-structured active smart home page; smooth sliders, Tuya/Kasa integrations, debounced controls. |
| 10 | `core/ui_setup.py` | 1068 | `SetupWizardDialog`, `_PageReactor` | GREEN | Interactive setup wizard with 3D reactor canvas orchestration; cleanly bound. |
| 11 | `core/ui_setup_flow.py` | 68 | `run_setup_flow` | AMBER | Hardcodes `open('setup_crash.log', 'w')` in the working directory instead of `%LOCALAPPDATA%\BrahmaAI\logs`. |
| 12 | `core/ui_telemetry.py` | 1104 | `TelemetryDashboard`, `LatencyChart` | GREEN | Rich metrics visualization; proper Qt chart styling and throttled refresh. |
| 13 | `core/ui_sysmetrics.py` | 180 | `SysMetricsMonitor` | RED | **Subprocess fork bomb**: Spawns `powershell.exe` every 1,500ms via `subprocess.check_output` to query ACPI thermal zones, causing 30%+ CPU thrashing. |
| 14 | `core/ui_transients.py` | 1065 | `ScanningOverlay`, `ToastOverlay` | RED | **Missing imports crash**: `ScanningOverlay._generate_layout` calls `np.array(...)` and `cv2.cvtColor(...)` without importing `numpy` or `cv2`, throwing `NameError: name 'np' is not defined` every frame. |
| 15 | `core/ui_floating.py` | 504 | `FloatingOverlayManager`, `FloatingCard` | GREEN | Clean desktop HUD docking; proper coordinate clamping and geometry persistence. |
| 16 | `core/ui_gesture.py` | 682 | `GestureController`, `GestureOverlay` | AMBER | **Main-thread stall**: Camera frame capture (`VideoCapture.read()`) and MediaPipe inference run directly on the Qt main GUI thread every 30ms. |
| 17 | `gesture_utils.py` | 181 | `HandDetector`, `GestureClassifier` | GREEN | Pure MediaPipe calculation helpers; cleanly structured. |
| 18 | `core/ui_bg.py` | 402 | `BackgroundController`, `WebEngineBackground` | GREEN | Invariant `I-25` compliant: lazily probes `find_spec("PyQt6.QtWebEngineWidgets")`; marshals thread calls via signals. |
| 19 | `core/ui_panels.py` | 310 | `AppPanelStack`, `PanelRegistry` | AMBER | Retains hidden legacy `_smart_devices_section` in `_hidden_legacy_container`, enabling the zombie polling loop in `ui_devices.py`. |
| 20 | `core/ui_nav.py` | 91 | `NavigationBar` | GREEN | Clean side navigation with active tab indicators. |
| 21 | `core/ui_wiring.py` | 64 | `wire_ui_signals` | GREEN | Clean signal-to-slot decoupling. |
| 22 | `core/ui_branding.py` | 84 | `BrandingHeader` | GREEN | Top banner rendering; clean vector logos. |
| 23 | `core/ui_filedrop.py` | 209 | `FileDropOverlay` | GREEN | Clean drag-and-drop file ingestion handling. |
| 24 | `core/ui_devmode.py` | 98 | `DevModeOverlay` | GREEN | Toggles debug inspect tools and inspector consoles. |
| 25 | `core/ui_discord.py` | 130 | `DiscordConfigDialog` | RED | **Insecure secret storage**: Writes plaintext Discord `bot_token` directly to JSON file without DPAPI encryption or `atomic_write_json`. |
| 26 | `core/ui_meeting.py` | 89 | `MeetingHUD` | GREEN | Frameless meeting overlay; clean layout. |
| 27 | `core/ui_remote.py` | 63 | `RemoteControlDialog` | GREEN | Compact pairing modal. |
| 28 | `core/ui_startup.py` | 155 | `StartupSplash` | GREEN | Animated splash window; clean frame timer. |
| 29 | `core/ui_startup_os.py` | 99 | `WindowsStartupManager` | GREEN | Windows Run key registry configuration. |
| 30 | `core/globe_window.py` | 759 | `GlobeWindow`, `GlobeBridge` | AMBER | Lazy import used, but `inspect_current_view` invokes `QTimer.singleShot(0)` across threads without affinity; unescaped JS interpolation in `switchMode`. |
| 31 | `core/circuit_hud.py` | 559 | `CircuitHUDWindow` | GREEN | Lazy `QtWebEngineWidgets` import compliant; properly sanitizes circuit JSON with HTML escaping. |
| 32 | `core/radar_hud.py` | 333 | `RadarPopupOverlay`, `RadarBridge` | RED | **Invariant I-25 violation**: Imports `QtWebEngineWidgets` at top-level module scope; unescaped string interpolation in `set_latency_result` and `set_device_notified`. |
| 33 | `core/space_cleaner_hud.py` | 329 | `SpaceCleanerPopupOverlay`, `SpaceCleanerBridge` | RED | **Invariant I-25 violation**: Imports `QtWebEngineWidgets` at module scope; sets global `QTWEBENGINE_CHROMIUM_FLAGS` at module level; unescaped string in `set_scan_status`. |
| 34 | `assets/avatar_vrm/index.html` | 491 | VRM WebGL Scene | GREEN | Self-contained, vendored runtime (`./vendor/...`); no CDN dependencies; implements canonical 7 avatar hooks. |
| 35 | `assets/avatar_live2d/index.html` | 502 | Live2D WebGL Scene | GREEN | Uses vendored Cubism runtime; polyfills `fetch` for `file://` XMLHttpRequest bridge; implements canonical 7 avatar hooks. |
| 36 | `assets/backgrounds/index.html` | 461 | Equirectangular / GLTF Panorama | GREEN | Self-contained Three.js + HDRLoader; implements canonical 7 avatar hooks. |
| 37 | `assets/web_background/index.html` | 1060 | Quantum Core Resonator (Three.js) | GREEN | Primary 3D background; implements canonical 7 avatar hooks; pause/resume loop handling. |
| 38 | `assets/globe/index.html` | 2202 | Dual 3D Planetary / Leaflet Engine | AMBER | External CartoDB tile CDN dependency (`https://{s}.basemaps.cartocdn.com/...`) requiring `LocalContentCanAccessRemoteUrls`. |
| 39 | `assets/radar/index.html` | 1272 | PPI Tactical Radar Canvas | AMBER | External dependency on `https://api.qrserver.com/` for QR code generation; leaks local IP to 3rd party. |
| 40 | `assets/space_cleaner/index.html` | 1199 | Sunburst Disk Map Canvas | GREEN | Pure HTML5/Canvas sunburst visualizer; zero external CDN calls. |

---

## 3. Invariant & Specific Audit Question Verdicts

### Invariant I-25 & I-26 Verdicts (`docs/ARCHITECTURE-SPEC.md` §4.1 & Decision D46)

- **Invariant I-25 (One Marshaller Per Boundary — Qt-Thread-Only Calls Emitted Through Queued Signals):** **FAIL / VIOLATED**
  - **Verdict:**
    1. `core/globe_window.py:756` calls `QTimer.singleShot(0, _do_query)` from whatever thread called `inspect_current_view`, breaking timer thread affinity if called from an agent background worker.
    2. `core/ui_gesture.py:165-210` executes blocking camera frame acquisition (`cv2.VideoCapture.read()`) and CPU-heavy MediaPipe machine learning inference directly inside the main Qt UI thread on a 30ms timer.
    3. `core/ui_sysmetrics.py:160` executes synchronous PowerShell subprocess calls on a 1.5s timer from the UI monitoring path.
- **Invariant I-26 (No Import-Time Side Effects) & Decision D46 (Lazy `QtWebEngineWidgets` Import):** **FAIL / VIOLATED**
  - **Verdict:** `core/ui_bg.py`, `core/globe_window.py`, and `core/circuit_hud.py` correctly defer the import of `QtWebEngineWidgets` until instantiation or helper call. However, `core/radar_hud.py:31` and `core/space_cleaner_hud.py:47` import `from PyQt6.QtWebEngineWidgets import QWebEngineView` directly at module scope (and `space_cleaner_hud.py:21` mutates `os.environ["QTWEBENGINE_CHROMIUM_FLAGS"]` at module scope). This forces Chromium multi-process initialization during module import, increasing resident memory by ~150 MB and risking crashes if imported prior to `QApplication` initialization.

---

### Specific Audit Questions

#### 1. Lazy `QtWebEngineWidgets` Import & `runJavaScript` Safety (`I-25`, `I-26`)
- **Module Import Behavior:**
  - `core/ui_bg.py`: Lazily imports using `importlib.util.find_spec("PyQt6.QtWebEngineWidgets")` inside `_new_web_engine_view`. **Compliant.**
  - `core/circuit_hud.py`: Lazily imports inside `_new_webengine_view`. **Compliant.**
  - `core/globe_window.py`: Lazily imports inside `_setup_ui`. **Compliant.**
  - `core/radar_hud.py`: Module-level import at line 31: `from PyQt6.QtWebEngineWidgets import QWebEngineView`. **Non-Compliant.**
  - `core/space_cleaner_hud.py`: Module-level import at line 47: `from PyQt6.QtWebEngineWidgets import QWebEngineView`. **Non-Compliant.**
- **`runJavaScript` Safety & Injection Vectors:**
  - **Unescaped String Interpolation:**
    - `core/radar_hud.py:279-281`: `f"if (selectedDevice && selectedDevice.ip === '{ip}') ..."` interpolates `ip` directly into JavaScript with single quotes. A crafted IP or device hostname containing single quotes breaks JS syntax or executes arbitrary JS.
    - `core/radar_hud.py:295`: `f"if (window.onDeviceNotified) onDeviceNotified('{ip}', {clean_json});"` interpolates `ip` unescaped.
    - `core/space_cleaner_hud.py:228-229`: `safe_msg = msg.replace("'", "\\'")` fails if `msg` contains newlines (`\n`) or trailing backslashes (`\`), producing `SyntaxError: Unterminated string constant` in Chromium.
    - `core/globe_window.py:560`: `f"BrahmaGlobe.switchMode('{mode}')"` interpolates `mode` without JSON encoding.
  - **Cross-Thread Marshalling:**
    - `core/ui_bg.py` safely routes all cross-thread calls via Qt signals (`_state_sig`, `_audio_sig`, `_emotion_sig`).
    - `core/globe_window.py` routes through `_cmd_sig`, except for `inspect_current_view` (line 756), which incorrectly invokes `QTimer.singleShot(0, ...)` directly on the calling thread.

#### 2. Duplicate Smart Home UI (`core/ui_devices.py` vs `smart_home_page_new.py`)
- **Relationship Analysis:**
  - `smart_home_page_new.py` contains the modern, active smart home UI (`BrahmaHomePage`, `_DeviceTile`, `AddDeviceDialog`). In `core/ui_panels.py:64`, it is mounted as tab index 1 ("home").
  - `core/ui_devices.py` contains two classes:
    1. `SmartDevicesSection` (lines 20–532): Legacy smart home control page. In `core/ui_panels.py:54-55`, it is placed into `self._hidden_legacy_container` and hidden (`_smart_devices_section.hide()`). **This is completely dead/dormant UI.**
    2. `BrahmaConnectDevicesPage` (lines 679–1312): Active companion phone gateway (Brahma Connect / Push Notifications). In `core/ui_panels.py:65`, it is mounted as tab index 2 ("devices").
- **Critical Zombie Polling Finding:**
  - In `core/ui_devices.py:53`, `SmartDevicesSection.__init__` starts an active `QTimer`:
    ```python
    self._poll_tmr = QTimer(self)
    self._poll_tmr.setInterval(2500)
    self._poll_tmr.timeout.connect(self._refresh_from_service)
    self._poll_tmr.start()
    ```
  - Because `_smart_devices_section` is instantiated in `ui_panels.py` (even though hidden in `_hidden_legacy_container`), this timer **runs forever in the background**, querying `smart_home_service.list_devices()` every 2,500ms, consuming background CPU cycles and polling hardware backends for a widget that is never displayed to the user!
- **Control Logic Duplication:**
  - Device card rendering, brightness/speed sliders, color pickers, and device renaming logic are duplicated across `SmartDevicesSection` and `BrahmaHomePage`.

#### 3. Background `QThread` / Worker Lifecycle
- **`_AsyncBridgeWorker` (`core/ui_agent_panel.py`):**
  - Worker instances are appended to `self._active_workers`, but `AgentBridgeCard` lacks a `closeEvent` or widget deletion handler. If the user navigates away or the parent panel is destroyed while a worker is probing an agent bridge, the finished worker signal invokes slots on deleted C++ Qt objects, raising:
    `RuntimeError: wrapped C/C++ object of type AgentBridgeCard has been deleted`.
- **Download Workers (`core/ui_avatar_library.py` & `core/ui_voice_library.py`):**
  - Both dialogs spawn download workers via `threading.Thread(daemon=True)`. While `threading.Event()` cancel flags exist (`self._cancel`), closing the dialog does not set the cancel event. Download chunks or `pip` subprocess runs continue in the background, emitting events to `_UiBridge` that attempt to update dead UI widgets.
- **Camera Threads (`core/ui_gesture.py`):**
  - `GestureController` **does not use a background worker thread at all**. It uses a `QTimer(30)` connected to `_tick()`, running `cv2.VideoCapture.read()` and MediaPipe `Hands.process()` directly on the GUI main thread, blocking event processing for 30–75ms per frame.
  - When embedded in `FloatingGestureCard` (`core/ui_floating.py`), closing the floating card does not guarantee `closeEvent` propagation, leaving the webcam hardware handle locked by OpenCV until app termination.

#### 4. Frozen PyInstaller Asset Paths (`assets/...` Resolution)
- **Status in Secondary Modules:** **FAIL / DEFECTIVE**
  - While `main.py:75` and `core/skills_library.py:86` correctly resolve paths via:
    `Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))`
  - The following modules hardcode resolution relative to `__file__`:
    - `core/radar_hud.py:40`: `RADAR_HTML_FILE = Path(__file__).resolve().parent.parent / "assets" / "radar" / "index.html"`
    - `core/space_cleaner_hud.py:76`: `HUD_HTML_FILE = Path(__file__).resolve().parent.parent / "assets" / "space_cleaner" / "index.html"`
    - `core/globe_window.py:50`: `GLOBE_HTML = Path(__file__).resolve().parent.parent / "assets" / "globe" / "index.html"`
    - `core/ui_bg.py:27`: `_BG_HTML = Path(__file__).resolve().parent.parent / "assets" / "web_background" / "index.html"`
    - `core/avatar_runtime.py:486, 904`: `_repo_root()` returns `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`
  - In a PyInstaller frozen distribution (e.g., one-dir layout `dist\BrahmaEvo\_internal\core\...`), `__file__` is located inside `_internal\core\`. Calling `.parent.parent` resolves to `_internal\`, failing to find `assets/` if assets are bundled at top-level next to the executable, and ignoring `sys._MEIPASS` in one-file builds.

---

## 4. Detailed Findings

### FINDING-WP07-01 (CRITICAL): Missing Imports Cause Immediate Exception in `ScanningOverlay`
- **Category:** Crash / Missing Dependencies
- **Severity:** Critical
- **Location:** `core/ui_transients.py:124-126` (`ScanningOverlay._generate_layout`)
- **Evidence & Mechanism:**
  ```python
  # core/ui_transients.py
  # Lines 124-126:
  img_np = np.array(screen)
  img_rgb = cv2.cvtColor(img_np, cv2.COLOR_RGBA2RGB)
  gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)
  ```
  Neither `numpy as np` nor `cv2` is imported anywhere in `core/ui_transients.py`. Furthermore, `pyproject.toml:25-38` suppresses Ruff `F821` (`undefined-name`) across `core/ui_transients.py` and 15 other `core/` modules (due to `globals().update(deps)` mixin injection), which masked this `NameError` from CI lint gates!
- **Impact:**
  Every time `ScanningOverlay` activates, it immediately raises `NameError: name 'np' is not defined`. Caught by `except Exception as e:` on line 140 which logs `[ScanningOverlay] CV2 Error: name 'np' is not defined` on every frame, forcing fallback to random synthetic boxes.
- **Recommended Fix:**
  Add guarded lazy imports at the top of `_generate_layout`:
  ```python
  try:
      import numpy as np
      import cv2
  except ImportError:
      np = None
      cv2 = None
  if np is None or cv2 is None:
      self._generate_random_boxes()
      return
  ```

---

### FINDING-WP07-02 (CRITICAL): Subprocess Thrashing Polling WMI via PowerShell Every 1.5s
- **Category:** Performance / Subprocess Safety / Invariant `I-26`
- **Severity:** Critical
- **Location:** `core/ui_sysmetrics.py:160-165` (`SysMetricsMonitor._poll_windows_temperature`)
- **Evidence & Mechanism:**
  ```python
  # core/ui_sysmetrics.py
  # Timer configured at 1500ms on line 42:
  cmd = [
      "powershell",
      "-NoProfile",
      "-NonInteractive",
      "-Command",
      "(Get-WmiObject -Namespace root/wmi -Class MSAcpi_ThermalZoneTemperature).CurrentTemperature",
  ]
  raw = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=1.0).decode().strip()
  ```
- **Impact:**
  Spawning a full `powershell.exe` process every 1.5 seconds consumes 25%–45% sustained CPU load, incurs disk/process creation overhead, and causes severe battery drain and frame drops.
- **Recommended Fix:**
  Replace PowerShell CLI spawning with native Python WMI via `win32com` or `comtypes` if available, or fall back to LibreHardwareMonitor / OpenHardwareMonitor shared memory:
  ```python
  # Use pythoncom/win32com once per session rather than spawning powershell.exe
  import win32com.client
  wmi = win32com.client.GetObject("winmgmts:root\\wmi")
  zones = wmi.ExecQuery("SELECT CurrentTemperature FROM MSAcpi_ThermalZoneTemperature")
  ```

---

### FINDING-WP07-03 (HIGH): Hidden Legacy Smart Home Widget Runs Indefinite Background Polling Loop
- **Category:** Resource Thrashing / Architecture
- **Severity:** High
- **Location:** `core/ui_devices.py:53` & `core/ui_panels.py:54-55`
- **Evidence & Mechanism:**
  In `core/ui_panels.py:54-55`:
  ```python
  self._hidden_legacy_container = QWidget()
  self._smart_devices_section = SmartDevicesSection()
  self._hidden_legacy_container.layout().addWidget(self._smart_devices_section)
  self._smart_devices_section.hide()
  ```
  In `core/ui_devices.py:53`:
  ```python
  self._poll_tmr = QTimer(self)
  self._poll_tmr.setInterval(2500)
  self._poll_tmr.timeout.connect(self._refresh_from_service)
  self._poll_tmr.start()
  ```
- **Impact:**
  Even though `_smart_devices_section` is permanently hidden, `_poll_tmr` runs continuously every 2.5 seconds, invoking `self._service.list_devices()`, making network calls, and rebuilding device cards in memory for an invisible widget.
- **Recommended Fix:**
  Stop the timer if the widget is hidden, or deprecate and remove `SmartDevicesSection` entirely from `core/ui_panels.py` since `smart_home_page_new.py` is the active smart home controller.

---

### FINDING-WP07-04 (HIGH): Plaintext Storage of Discord Bot Token & Physical Android Device Lockscreen PIN Violating Secret Storage & Atomic Write Invariants
- **Category:** Security / Credential Storage & Invariant `I-17`
- **Severity:** High
- **Location:** `core/ui_discord.py:32-35` (`DiscordConfigDialog.save_settings`), `core/ui_devices.py:1276-1292` (`BrahmaConnectDevicesPage._set_device_pin`), and `actions/unlock_device.py:30-45`
- **Evidence & Mechanism:**
  ```python
  # core/ui_discord.py:32
  with open(DISCORD_SETTINGS_FILE, "w", encoding="utf-8") as f:
      json.dump({"bot_token": token, "guild_id": guild, "channel_id": channel}, f)

  # core/ui_devices.py:1276-1292 (_set_device_pin)
  pins = data.get("device_pins", {})
  pins[device_id] = pin.strip()
  data["device_pins"] = pins
  with open(APP_SETTINGS_FILE, "w", encoding="utf-8") as f:
      json.dump(data, f, indent=2)
  ```
- **Impact:**
  1. Stores sensitive Discord bot tokens and **the user's physical Android phone lockscreen PIN (`device_pins[device_id]`)** in unencrypted plaintext inside `app_settings.json` on disk.
  2. Non-atomic `open(..., "w")` file writes violate Invariant `I-17` and risk truncating `app_settings.json` on power loss or crash.
- **Recommended Fix:**
  Store both the Discord bot token and `device_pins` inside the DPAPI-encrypted secret store (`core/secret_store.py`) and use `core/user_paths.py:atomic_write_json` for all non-secret settings writes.

---

### FINDING-WP07-05 (HIGH): Module-Level `QtWebEngineWidgets` Imports Violating Invariant `I-25`
- **Category:** Architecture / Invariant `I-25`
- **Severity:** High
- **Location:** `core/radar_hud.py:31` and `core/space_cleaner_hud.py:47`
- **Evidence & Mechanism:**
  ```python
  # core/radar_hud.py:31
  try:
      from PyQt6.QtWebChannel import QWebChannel
      from PyQt6.QtWebEngineWidgets import QWebEngineView
      _WEBENGINE_OK = True
  except Exception:
      _WEBENGINE_OK = False
  ```
- **Impact:**
  Forces Chromium runtime initialization when the Python module is imported, bypassing lazy-loading safeguards.
- **Recommended Fix:**
  Remove top-level import. Import inside `__init__` or `_setup_web_view` method after `find_spec` verification, matching the pattern in `core/ui_bg.py`.

---

### FINDING-WP07-06 (HIGH): Unescaped Dynamic Interpolation into Chromium `runJavaScript`
- **Category:** Security / Script Injection / Syntax Fragility
- **Severity:** High
- **Locations:**
  - `core/radar_hud.py:279-281`: `f"if (selectedDevice && selectedDevice.ip === '{ip}') ..."`
  - `core/radar_hud.py:295`: `f"if (window.onDeviceNotified) onDeviceNotified('{ip}', {clean_json});"`
  - `core/space_cleaner_hud.py:228-230`: `safe_msg = msg.replace("'", "\\'"); f"window.setStatus('{safe_msg}', true);"`
  - `core/globe_window.py:560`: `f"BrahmaGlobe.switchMode('{mode}')"`
- **Evidence & Mechanism:**
  String formatting with single quotes allows quotes, newlines, or backslashes to break JS parsing or inject malicious code into the local DOM.
- **Impact:**
  DOM syntax errors causing HUD features to freeze; potential execution of arbitrary JavaScript in the context of `file:///` URLs.
- **Recommended Fix:**
  Always encode string arguments using `json.dumps()`:
  ```python
  # Safe pattern:
  ip_json = json.dumps(ip)
  msg_json = json.dumps(msg)
  self._run_js(f"if (window.setStatus) window.setStatus({msg_json}, true);")
  ```

---

### FINDING-WP07-07 (HIGH): PyInstaller Frozen Asset Path Resolution Failure
- **Category:** Packaging / Portability
- **Severity:** High
- **Locations:**
  - `core/radar_hud.py:40`
  - `core/space_cleaner_hud.py:76`
  - `core/globe_window.py:50`
  - `core/ui_bg.py:27`
  - `core/avatar_runtime.py:486, 904`
- **Evidence & Mechanism:**
  Modules resolve assets using `Path(__file__).resolve().parent.parent / "assets" / ...`. Under PyInstaller `_internal` directory layout, `parent.parent` points to `_internal` rather than the app bundle root or `sys._MEIPASS`.
- **Impact:**
  HUD overlays and WebGL backgrounds fail to load in frozen standalone distributions, rendering blank windows.
- **Recommended Fix:**
  Create a shared asset resolver in `core/user_paths.py`:
  ```python
  def get_asset_path(*parts: str) -> Path:
      base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent))
      return base.joinpath("assets", *parts)
  ```

---

### FINDING-WP07-08 (MEDIUM): Blocking Camera & MediaPipe Inference on Qt Main GUI Thread
- **Category:** Responsiveness / Invariant `I-26`
- **Severity:** Medium
- **Location:** `core/ui_gesture.py:165-210` (`GestureController._tick`)
- **Evidence & Mechanism:**
  `GestureController` runs a 30ms `QTimer` that calls `self._cap.read()` and `self._hands.process(...)` synchronously on the main thread.
- **Impact:**
  Frame drop spikes (30ms–75ms) freezing the GUI whenever the camera acquires a frame or detects hands.
- **Recommended Fix:**
  Move camera capture and MediaPipe processing into a dedicated `QThread` worker that emits a `frame_processed(QImage, list)` signal to the GUI thread.

---

### FINDING-WP07-09 (MEDIUM): Cross-Thread Timer Invocation in `GlobeWindow.inspect_current_view`
- **Category:** Thread Safety / Qt Thread Affinity
- **Severity:** Medium
- **Location:** `core/globe_window.py:756`
- **Evidence & Mechanism:**
  ```python
  # core/globe_window.py:756
  QTimer.singleShot(0, _do_query)
  ```
  `inspect_current_view` is called by background tools. Calling `QTimer.singleShot` from a worker thread without a running Qt event loop causes the timer to never trigger.
- **Impact:**
  Background queries for the globe's viewport state hang indefinitely.
- **Recommended Fix:**
  Use `QMetaObject.invokeMethod` with `Qt.ConnectionType.QueuedConnection` to ensure `_do_query` executes on the GUI thread.

---

### FINDING-WP07-10 (MEDIUM): Uncanceled Background Worker Threads on Dialog Destruction
- **Category:** Memory / Thread Safety / Lifecycle
- **Severity:** Medium
- **Locations:**
  - `core/ui_agent_panel.py:68` (`_AsyncBridgeWorker`)
  - `core/ui_avatar_library.py:695`
  - `core/ui_voice_library.py:521`
- **Evidence & Mechanism:**
  Workers do not get cancelled or joined in `closeEvent`. If the widget is deleted before the thread finishes, callbacks accessing widget attributes raise `RuntimeError: wrapped C/C++ object has been deleted`.
- **Impact:**
  Crash upon closing download or probe dialogs while operations are in progress.
- **Recommended Fix:**
  Implement `closeEvent` in dialog classes to set cancellation events and disconnect Qt signals before calling `super().closeEvent(event)`.

---

### FINDING-WP07-11 (LOW): External QR Code Generation Leaks Local IP to Third-Party API
- **Category:** Privacy / Offline Robustness
- **Severity:** Low
- **Location:** `assets/radar/index.html:1212`
- **Evidence & Mechanism:**
  ```javascript
  document.getElementById('mobileQrImg').src = `https://api.qrserver.com/v1/create-qr-code/?size=160x160&data=${encodeURIComponent(url)}`;
  ```
- **Impact:**
  Sends user's local network gateway IP address and port to `api.qrserver.com`; fails completely if the host has no internet access.
- **Recommended Fix:**
  Generate the QR code locally using Python's `qrcode` library and pass a base64 data URL to the web view.

---

### FINDING-WP07-12 (LOW): Hardcoded Log File in Working Directory
- **Category:** File Hygiene
- **Severity:** Low
- **Location:** `core/ui_setup_flow.py:38`
- **Evidence & Mechanism:**
  ```python
  with open('setup_crash.log', 'w') as f:
      f.write(traceback.format_exc())
  ```
- **Impact:**
  Pollutes the current working directory instead of writing to `%LOCALAPPDATA%\BrahmaAI\logs`.
- **Recommended Fix:**
  Direct the crash log to `get_user_data_dir() / "logs" / "setup_crash.log"`.

---

## 5. Cross-Package Seams & Handoff Notes for Orchestrator

1. **Seam with WP-06 (Primary UI Shell & Core Layout):**
   - `core/ui_panels.py` instantiates `SmartDevicesSection` inside `_hidden_legacy_container`. WP-06 should coordinate with WP-07 to remove `_smart_devices_section` from `ui_panels.py` to stop the 2.5s zombie polling loop completely.
2. **Seam with WP-04 (Smart Home & IoT Actions):**
   - `smart_home_page_new.py` is the canonical active smart home UI. All references to legacy `SmartDevicesSection` control methods in `core/ui_devices.py` should be formally deprecated.
3. **Seam with WP-01 / Core Infrastructure (Path Resolution):**
   - `get_asset_path()` must be added to `core/user_paths.py` and adopted across `core/ui_bg.py`, `core/globe_window.py`, `core/circuit_hud.py`, `core/radar_hud.py`, `core/space_cleaner_hud.py`, and `core/avatar_runtime.py` to guarantee PyInstaller frozen binary asset loading.
4. **Seam with WP-02 (Agent Runtime / Live Live2D & VRM Avatars):**
   - The 7-hook JS avatar contract (`setBrahmaState`, `setAudioLevel`, `setPointerNorm`, `setAvatarEmotion`, `__pauseAnimation`, `__resumeAnimation`, `__getAvatarState`) is uniform across all 3 visual scenes (`web_background`, `avatar_vrm`, `avatar_live2d`). Backends can be hot-swapped via `core/avatar_runtime.py` without touching the audio or emotion intake paths.

---

## 6. Prioritized Action Queue

| Priority | Finding ID | File & Line | Summary of Action Required |
|---|---|---|---|
| **P0** | FINDING-WP07-01 | `core/ui_transients.py:124` | Guard `np` and `cv2` imports in `ScanningOverlay._generate_layout` to eliminate runtime `NameError` crash. |
| **P0** | FINDING-WP07-02 | `core/ui_sysmetrics.py:160` | Eliminate 1.5s `powershell.exe` subprocess polling; replace with in-process COM/WMI query. |
| **P1** | FINDING-WP07-03 | `core/ui_devices.py:53`, `core/ui_panels.py:54` | Terminate `_poll_tmr` in `SmartDevicesSection` and remove dead hidden widget from `ui_panels.py`. |
| **P1** | FINDING-WP07-04 | `core/ui_discord.py:32`, `core/ui_devices.py:1276` | Protect Discord bot token and Android phone lockscreen PIN (`device_pins`) with DPAPI (`core/secret_store.py`) and use `atomic_write_json`. |
| **P1** | FINDING-WP07-05 | `core/radar_hud.py:31`, `core/space_cleaner_hud.py:47` | Enforce Invariant `I-25`: make `QtWebEngineWidgets` imports lazy. |
| **P1** | FINDING-WP07-06 | `core/radar_hud.py:279`, `core/space_cleaner_hud.py:228` | Wrap all dynamic string parameters in `json.dumps()` before passing to `runJavaScript`. |
| **P2** | FINDING-WP07-07 | `core/radar_hud.py:40`, `core/avatar_runtime.py:486` | Unify asset path resolution using `getattr(sys, "_MEIPASS", ...)` for PyInstaller compatibility. |
| **P2** | FINDING-WP07-08 | `core/ui_gesture.py:165` | Move camera capture and MediaPipe inference off the main thread into a worker `QThread`. |
| **P2** | FINDING-WP07-09 | `core/globe_window.py:756` | Fix thread affinity bug in `inspect_current_view` using `QMetaObject.invokeMethod`. |
| **P2** | FINDING-WP07-10 | `core/ui_agent_panel.py:68`, `core/ui_avatar_library.py:695` | Add `closeEvent` cleanup and thread cancellation to prevent deleted C++ object errors. |
| **P3** | FINDING-WP07-11 | `assets/radar/index.html:1212` | Replace external QR code API with local Python QR generation. |
| **P3** | FINDING-WP07-12 | `core/ui_setup_flow.py:38` | Route crash log to `%LOCALAPPDATA%\BrahmaAI\logs`. |
