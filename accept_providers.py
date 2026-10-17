"""
accept_providers.py
Acceptance check for the provider failover chain and the launcher toggles (ONE-391).

The user's complaint was concrete: with Local selected, background work still
tried OpenRouter and stalled for minutes. These assertions prove that switching
a provider off now stops it *before* any model-pool walk, and that the toggles
on the launcher control panel persist and apply.

No network calls are made: the OpenRouter client is exercised with its pool
intact and asserted to bail before touching it.
Usage: py -3.11 accept_providers.py
"""
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from accept_util import check, section, report


def _ui_source(marker: str) -> str:
    """The source of the ui.py class/method starting at `marker`.

    The class may live in ui.py or in an extracted panel module
    (core/ui_connectivity.py, core/ui_telemetry.py); read all so the
    move is transparent.
    """
    text = (BASE / "ui.py").read_text(encoding="utf-8") + "\n" + (
        BASE / "core" / "ui_connectivity.py").read_text(encoding="utf-8") + "\n" + (
        BASE / "core" / "ui_telemetry.py").read_text(encoding="utf-8") + "\n" + (
        BASE / "core" / "ui_startup.py").read_text(encoding="utf-8") + "\n" + (
        BASE / "core" / "ui_bg.py").read_text(encoding="utf-8")
    start = text.index(marker)
    nxt = text.find("\nclass ", start + 1)
    return text[start:] if nxt == -1 else text[start:nxt]


def _exercise_providers_card(g):
    """Drive the real Providers & Resources card and report what it did.

    Runs in a child process: building Qt widgets inside this harness makes
    PyQt6 fault natively during teardown (0xC0000005), which would mask the
    verdict with a bogus non-zero exit. The child prints one RESULT line per
    assertion and exits immediately afterwards.
    """
    import subprocess
    import tempfile
    from pathlib import Path as _P

    driver = _P(tempfile.mkdtemp(prefix="brahma_drv_")) / "driver.py"
    driver.write_text(_CARD_DRIVER, encoding="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, "-u", str(driver), str(BASE)],
            capture_output=True, text=True, timeout=180, cwd=str(BASE),
        )
    except subprocess.TimeoutExpired:
        return [("the settings page exercise completed", False, "timed out")]

    out = []
    for line in proc.stdout.splitlines():
        if line.startswith("CASE\t"):
            _, label, ok, detail = (line.split("\t") + [""])[:4]
            out.append((label, ok == "1", detail))
    if not out:
        return [(
            "the settings page exercise produced results",
            False,
            "rc=%s stderr=%s" % (proc.returncode, proc.stderr.strip()[-300:]),
        )]
    return out


_CARD_DRIVER = r'''
import json, os, sys, tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = Path(sys.argv[1])
sys.path.insert(0, str(REPO))

sandbox = Path(tempfile.mkdtemp(prefix="brahma_card_")) / "app_settings.json"
stored = {"enable_3d_background": False, "provider_enabled_gemini": True,
          "provider_enabled_openrouter": True, "provider_enabled_local": True,
          "provider_enabled_custom": True, "memory_extraction_enabled": True}
json.dump(stored, open(sandbox, "w"))

import ui as ui_mod
from memory import config_manager as cm
from core import provider_router as pr
ui_mod.APP_SETTINGS_FILE = sandbox
# The settings page lives in the extracted panel module now; patch its copy
# of the settings path too so the sandbox redirect is transparent.
_uc = getattr(ui_mod, "_ui_connectivity", None)
if _uc is not None:
    _uc.APP_SETTINGS_FILE = sandbox
cm.SETTINGS_FILE = sandbox

from PyQt6 import QtWidgets
QApp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

def on_disk():
    return json.load(open(sandbox))

class Page:
    _build_providers_card = ui_mod.SystemConnectivityPage._build_providers_card
    _load_app_settings = ui_mod.SystemConnectivityPage._load_app_settings
    _replace_app_settings = ui_mod.SystemConnectivityPage._replace_app_settings
    _refresh_diagnostics = ui_mod.SystemConnectivityPage._refresh_diagnostics
    _invalidate_app_settings_cache = (
        ui_mod.SystemConnectivityPage._invalidate_app_settings_cache)
    _settings_file_stamp = ui_mod.SystemConnectivityPage._settings_file_stamp
    _on_save_all_clicked = ui_mod.SystemConnectivityPage._on_save_all_clicked
    _announce_save = ui_mod.SystemConnectivityPage._announce_save
    _log = ui_mod.SystemConnectivityPage._log
    _owner_window = ui_mod.SystemConnectivityPage._owner_window
    _host_window = ui_mod.SystemConnectivityPage._host_window

    def _ctrl(self):
        return None

    def _host_window(self):
        return None

    def _card(self, title, subtitle=""):
        f = QtWidgets.QFrame()
        f.setLayout(QtWidgets.QVBoxLayout())
        return f

    def write_log(self, msg):
        pass

def case(label, ok, detail=""):
    print("CASE\t%s\t%s\t%s" % (label, "1" if ok else "0", detail))

read = Page()._load_app_settings()
case("page with no controller reads the stored 3D choice",
     read.get("enable_3d_background") is False,
     "read %r" % read.get("enable_3d_background"))

before = on_disk()
card = Page()._build_providers_card()
case("building the card does not rewrite stored settings", before == on_disk())
case("the 3D off choice survives building the card",
     on_disk()["enable_3d_background"] is False)

boxes = [b for b in card.findChildren(QtWidgets.QCheckBox) if "OpenRouter" in b.text()]
raised = ""
try:
    boxes[0].setChecked(False)
except Exception as exc:
    raised = repr(exc)
case("toggling a provider does not abort", not raised, raised)
case("the toggled provider was written",
     on_disk()["provider_enabled_openrouter"] is False)
others = ("provider_enabled_gemini", "provider_enabled_local",
          "provider_enabled_custom")
case("no other provider was rewritten by that one toggle",
     all(on_disk()[k] is stored[k] for k in others),
     str({k: on_disk()[k] for k in others}))
case("the 3D off choice survives the toggle",
     on_disk()["enable_3d_background"] is False)

Page()._refresh_diagnostics()
Page()._build_providers_card()
Page()._refresh_diagnostics()
case("repeated refresh + rebuild never re-enables the provider",
     on_disk()["provider_enabled_openrouter"] is False)
case("repeated refresh + rebuild never re-ticks the 3D background",
     on_disk()["enable_3d_background"] is False)

# Save All with no window attached - the state that produced
# "'SystemConnectivityPage' object has no attribute '_win'".
page = Page()
page._save_status = QtWidgets.QLabel("")
saw_fail = []
_orig = page._announce_save
def _spy(ok, detail):
    if not ok:
        saw_fail.append(detail)
    _orig(ok, detail)
page._announce_save = _spy
case("the settings page exposes no _win of its own",
     not hasattr(page, "_win"))
case("resolving the owner window with none attached is not an error",
     page._owner_window() is None)
try:
    ok = page._on_save_all_clicked()
except Exception as exc:
    ok = False
    saw_fail.append(repr(exc))
case("Save All succeeds with no window attached", bool(ok), "; ".join(saw_fail))
case("Save All does not report a failure", not saw_fail, "; ".join(saw_fail))
case("Save All reports success on the card",
     "Saved" in page._save_status.text(), page._save_status.text())
case("Save All preserved the 3D off choice",
     on_disk()["enable_3d_background"] is False)
case("Save All preserved the disabled provider",
     on_disk()["provider_enabled_openrouter"] is False)
case("logging a message with no window attached is safe",
     page._log("SYS: probe") is None)

sys.stdout.flush()
os._exit(0)
'''


def _undefined_names(rel_paths):
    import re as _re
    import subprocess
    import sys as _sys

    try:
        import pyflakes  # noqa: F401
    except Exception:
        return None
    try:
        proc = subprocess.run(
            [_sys.executable, "-m", "pyflakes", *rel_paths],
            capture_output=True, text=True, cwd=str(BASE), timeout=300,
        )
    except Exception:
        return None
    out = []
    for line in proc.stdout.splitlines():
        m = _re.match(r"^(.+?):(\d+):\d+: undefined name '([^']+)'", line)
        if m:
            out.append((m.group(1), int(m.group(2)), m.group(3)))
    return out


def main() -> int:
    print("=" * 74)
    print("ONE-391 provider chain + launcher toggles")
    print("=" * 74)

    from core import provider_router as pr
    from memory import config_manager

    original = {}
    for p in pr.PROVIDERS:
        original[p["id"]] = config_manager.get_setting(f"provider_enabled_{p['id']}")

    try:
        # ------------------------------------------------------------------
        section("A. Registry")
        # ------------------------------------------------------------------
        ids = [p["id"] for p in pr.all_providers()]
        check("catalogue is vendor-agnostic, not just Gemini/OpenRouter",
              {"gemini", "openrouter", "local", "custom"} <= set(ids), f"{ids}")
        kinds = {p["id"]: p["kind"] for p in pr.PROVIDERS}
        check("only Gemini is bound to its own SDK",
              kinds["gemini"] == "gemini"
              and all(kinds[i] == "openai" for i in ("openrouter", "local", "custom")),
              f"{kinds}")
        check("every provider has a default enabled state",
              all("default" in p for p in pr.PROVIDERS))
        check("unknown provider is not enabled", not pr.is_enabled("nope"))

        # ------------------------------------------------------------------
        section("B. Kill switch")
        # ------------------------------------------------------------------
        pr.set_enabled("openrouter", False)
        check("openrouter reports disabled", pr.is_enabled("openrouter") is False)
        check("chain drops the disabled provider",
              "openrouter" not in pr.chain(), f"chain={pr.chain()}")
        check("describe() reads sensibly",
              "openrouter" not in pr.describe(), pr.describe())

        flipped = pr.toggle("openrouter")
        check("toggle() flips and returns the new state",
              flipped is True and pr.is_enabled("openrouter") is True)
        pr.set_enabled("openrouter", False)

        try:
            pr.guard("openrouter")
            check("guard() raises for a disabled provider", False, "did not raise")
        except pr.ProviderDisabled:
            check("guard() raises for a disabled provider", True)
        try:
            pr.guard("gemini")
            check("guard() passes for an enabled provider", True)
        except Exception as exc:
            check("guard() passes for an enabled provider", False, str(exc))

        # ------------------------------------------------------------------
        section("C. OpenRouter actually stops before the pool walk")
        # ------------------------------------------------------------------
        import or_client

        orc = or_client.client
        walked = []

        def _boom(*a, **k):
            walked.append(1)
            raise AssertionError("model pool was walked while disabled")

        original_call = orc._call
        orc._call = _boom
        try:
            t0 = time.perf_counter()
            try:
                orc._call_with_fallback(list(or_client.TEXT_MODELS), [{"role": "user", "content": "hi"}])
                check("disabled OpenRouter raises instead of answering", False, "returned a value")
            except pr.ProviderDisabled:
                elapsed = (time.perf_counter() - t0) * 1000
                check("disabled OpenRouter raises ProviderDisabled", True)
                check("it bails before walking the model pool", not walked,
                      f"{elapsed:.1f} ms, pool={len(or_client.TEXT_MODELS)} models")
                check("bail is immediate, not a 60s-per-model stall", elapsed < 100,
                      f"{elapsed:.1f} ms")
        finally:
            orc._call = original_call

        # With it enabled the client must still be allowed through.
        pr.set_enabled("openrouter", True)
        allowed = {"hit": False}

        def _sentinel(*a, **k):
            allowed["hit"] = True
            return "ok"

        orc._call = _sentinel
        try:
            out = orc._call_with_fallback(["m1"], [{"role": "user", "content": "hi"}])
            check("enabled OpenRouter still reaches the transport",
                  allowed["hit"] and out == "ok", f"{out!r}")
        finally:
            orc._call = original_call

        # ------------------------------------------------------------------
        section("D. llm_client honours the same switch")
        # ------------------------------------------------------------------
        import llm_client

        lc = llm_client.client
        # Pin the client's provider explicitly: which provider it currently
        # holds depends on whatever default_ai_provider says, and letting the
        # test inherit that is how it ended up exercising the custom branch.
        lc._provider = "Local"
        pr.set_enabled("local", False)
        try:
            lc._route_chat([{"role": "user", "content": "hi"}], 0.7, 64)
            check("disabled local provider is refused by llm_client", False, "no raise")
        except pr.ProviderDisabled:
            check("disabled local provider is refused by llm_client", True)
        except Exception as exc:
            check("disabled local provider is refused by llm_client", False,
                  f"{type(exc).__name__}: {exc}")
        pr.set_enabled("local", True)

        # ------------------------------------------------------------------
        section("E. Memory extraction toggle")
        # ------------------------------------------------------------------
        import re
        src = (BASE / "main.py").read_text(encoding="utf-8")
        fn_start = src.index("def _update_memory_async")
        fn_end = src.index("def _memory_context_for_request")
        fn_body = src[fn_start:fn_end]

        check("memory extraction reads its own setting",
              "memory_extraction_enabled" in fn_body)
        # The explicit user constraint: extraction must NOT be keyed on provider.
        check("extraction is NOT disabled just because Local is selected",
              'offline_mode_enabled' not in fn_body
              and 'default_ai_provider' not in fn_body,
              "no provider/offline gate in the extraction path")
        check("deterministic auto-learn still runs before the toggle",
              fn_body.index("auto_learn_interaction") < fn_body.index("memory_extraction_enabled"),
              "auto-learn is not gated")

        # ------------------------------------------------------------------
        section("F. Launcher control panel toggles")
        # ------------------------------------------------------------------
        # Panels may live in ui.py or an extracted module
        # (core/ui_connectivity.py, core/ui_telemetry.py); read all so the
        # move is transparent.
        panel_start = src and (
            (BASE / "ui.py").read_text(encoding="utf-8") + "\n" + (
                BASE / "core" / "ui_connectivity.py").read_text(encoding="utf-8") + "\n" + (
                BASE / "core" / "ui_telemetry.py").read_text(encoding="utf-8") + "\n" + (
                BASE / "core" / "ui_startup.py").read_text(encoding="utf-8") + "\n" + (
                BASE / "core" / "ui_bg.py").read_text(encoding="utf-8") + "\n" + (
                BASE / "core" / "ui_shell_launcher.py").read_text(encoding="utf-8"))
        pc_start = panel_start.index("class LauncherControlPanel")
        pc_end = panel_start.index("def _invoke(self, fn, *args)", pc_start)
        pc_body = panel_start[pc_start:pc_end]

        check("panel renders a toggle section", "_add_toggle_section" in pc_body)
        check("panel lists providers from the registry",
              "provider_router" in pc_body and "provider_enabled_" in pc_body)
        check("panel exposes the 3D background toggle", "enable_3d_background" in pc_body)
        check("panel exposes the memory extraction toggle",
              "memory_extraction_enabled" in pc_body)
        check("toggles do NOT close the panel",
              "_emit_setting" in pc_body
              and "self._invoke(self._on_setting_toggle" not in pc_body)

        check("BrahmaUI wires the toggle handler",
              "on_setting_toggle=self._on_panel_setting_toggle" in panel_start)
        check("3D toggle applies live to the background widget",
              "set_animation_enabled" in panel_start)
        check("3D toggle hides the web view, not just an overlay",
              "self._web_view.hide()" in panel_start)

        # ------------------------------------------------------------------
        section("G. Settings defaults + tool budget tiers")
        # ------------------------------------------------------------------
        for key in ("provider_enabled_gemini", "provider_enabled_openrouter",
                    "provider_enabled_local", "provider_enabled_custom",
                    "memory_extraction_enabled", "enable_3d_background"):
            check(f"default present: {key}", f'"{key}"' in panel_start)
        check("tool budget offers small tiers for tiny local models",
              "for value in (4, 8, 12," in panel_start)
        check("default budget index is derived, not a literal",
              "default_index = budget_box.findData(24)" in panel_start)

        # ------------------------------------------------------------------
        section("H. Settings hub has real controls, not just the launcher panel")
        # ------------------------------------------------------------------
        check("Providers & Resources card exists",
              "def _build_providers_card" in panel_start)
        check("card is added to the settings layout",
              "lay.addWidget(self._build_providers_card())" in panel_start)
        pc2 = panel_start.index("def _build_providers_card")
        pc2_end = panel_start.index("def _refresh_speech_status", pc2)
        pc2_body = panel_start[pc2:pc2_end]
        check("card reads and writes through the registry, not raw settings",
              "provider_router.is_enabled" in pc2_body
              and "provider_router.set_enabled" in pc2_body)
        check("card exposes all four providers",
              "provider_router.PROVIDERS" in pc2_body)
        check("card has a memory extraction checkbox",
              "memory_extraction_enabled" in pc2_body)
        check("card has a 3D background checkbox",
              "enable_3d_background" in pc2_body)
        check("card reports the live fallback order",
              "provider_router.describe()" in pc2_body)

        # ------------------------------------------------------------------
        section("I. Settings page cannot crash or self-revert")
        # ------------------------------------------------------------------
        # Regression: toggling a provider aborted the process, Save All raised
        # AttributeError('_invalidate_app_settings_cache'), and the diagnostics
        # box read "off" at launch then "on" after Refresh. All three were the
        # same page calling a method it did not define, and writing every
        # provider flag on any single toggle.
        page_src = _ui_source("class SystemConnectivityPage")
        _save_at = page_src.index("def _on_save_all_clicked")
        _save_end = page_src.index("    def ", _save_at + 10)
        _on_save_all_body = page_src[_save_at:_save_end]
        check("the settings page defines _invalidate_app_settings_cache",
              "def _invalidate_app_settings_cache" in page_src)
        check("the settings page defines _settings_file_stamp",
              "def _settings_file_stamp" in page_src)
        check("Save All no longer calls a method the page lacks",
              "self._save_app_settings(" not in _on_save_all_body)

        p_persist = pc2_body[pc2_body.index("def _persist("):pc2_body.index("# --- background work")]
        check("_persist writes only the provider that changed",
              "def _persist(pid)" in p_persist
              and "provider_router.set_enabled(pid," in p_persist)
        # The only per-provider loop left in _persist must be the signal wiring,
        # never a loop that writes every provider flag on one toggle.
        _persist_writes = [
            ln.strip() for ln in p_persist.splitlines()
            if "provider_router.set_enabled" in ln
        ]
        check("_persist writes at most one provider per toggle",
              len(_persist_writes) == 1
              and _persist_writes[0].startswith("provider_router.set_enabled(pid,"),
              str(_persist_writes))
        check("a failure in _persist is reported, not raised into the Qt loop",
              "except Exception" in p_persist)

        check("card construction cannot write to settings",
              pc2_body.count("blockSignals(True)") >= 3
              and pc2_body.count("blockSignals(False)") >= 3)
        check("resource toggles are also guarded against a raising persist",
              "except Exception" in pc2_body)

        # The page has no _win of its own; it reaches the window through
        # _owner_window()/__host_window(). Spelling the lookup out by hand is
        # what produced three consecutive AttributeErrors.
        _page_uses_win = re.findall(r"self\._win\b", page_src)
        check("the settings page never touches self._win",
              not _page_uses_win, "%d occurrence(s)" % len(_page_uses_win))
        check("the page has one accessor for the owning window",
              "def _owner_window" in page_src)
        check("settings methods route through that accessor",
              page_src.count("self._owner_window()") >= 4)
        check("the page has a logging helper that cannot raise",
              "def _log(self, message" in page_src)
        _save_body2 = page_src[page_src.index("def _on_save_all_clicked"):
                               page_src.index("    def _host_window")]
        check("Save All reports on the write, not on the logging",
              _save_body2.index("self._replace_app_settings(settings)")
              < _save_body2.index("self._announce_save(True, detail)"))

        # Behaviour, not just source: drive the real card with no controller.
        _live = _exercise_providers_card(globals())
        for label, ok, detail in _live:
            check(label, ok, detail)

        # ------------------------------------------------------------------
        section("J. No undefined names anywhere in the touched core")
        # ------------------------------------------------------------------
        # A bare `_split_emotion_tag(reply)` inside a method raised NameError
        # and killed every fallback reply, and `_add_custom_mcp_server()` did
        # the same for the Add MCP button. Both passed the targeted checks
        # because a substring match cannot tell a qualified call from a bare
        # one. A whole-file static pass catches the whole class at once.
        _und = _undefined_names(["main.py", "ui.py", "core/speech_tts.py",
                                 "core/instruction_injector.py",
                                 "core/provider_router.py"])
        if _und is None:
            print("  [SKIP] pyflakes is not installed - cannot check undefined names.")
            print("         pip install pyflakes")
        else:
            # numpy/opencv are optional dependencies used inside try/except in
            # the gesture-camera path; their absence is handled, so they are
            # expected to be "undefined" statically.
            allowed = {"np", "cv2"}
            real = [u for u in _und if u[2] not in allowed]
            check("no undefined names in the code paths this work touched",
                  not real,
                  "; ".join(f"{f}:{l} {n}" for f, l, n in real[:6]))



    finally:
        # Leave the user's provider settings exactly as we found them. A key that
        # was absent must go back to absent, not become an explicit null.
        import json
        settings_path = None
        try:
            from memory import config_manager

            settings_path = config_manager.SETTINGS_FILE
        except Exception:
            pass
        if settings_path:
            try:
                data = json.loads(settings_path.read_text(encoding="utf-8"))
            except Exception:
                data = None
            if isinstance(data, dict):
                changed = False
                for pid, value in original.items():
                    key = f"provider_enabled_{pid}"
                    if value is None:
                        if data.pop(key, None) is not None:
                            changed = True
                    elif data.get(key) != value:
                        data[key] = value
                        changed = True
                if changed:
                    settings_path.write_text(
                        json.dumps(data, indent=4), encoding="utf-8"
                    )

    print("\n" + "=" * 74)
    code = report()
    if code == 0:
        print("provider settings restored to their pre-test values")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
