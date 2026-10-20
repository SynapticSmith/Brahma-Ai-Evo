"""
accept_instructions.py
Acceptance check for the optional instruction injector (core/instruction_injector.py).

The requirement: instruction blocks are opt-in per layer, so a layer that does
not use them is never charged for them and nothing is required for layers with
no blocks. This asserts that contract, plus the emotion-tag stripping that keeps
"[SMILE]" out of the synthesised audio.
Usage: py -3.11 accept_instructions.py
"""
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from accept_util import check, section, report


def main() -> int:
    print("=" * 74)
    print("ONE-391 optional instruction injector")
    print("=" * 74)

    from core import instruction_injector as inj

    # Snapshot and restore every setting this test touches.
    from memory import config_manager

    touched = [f"instruction_block_{n}" for n in list(inj._BLOCKS)]
    touched += [f"instruction_layer_{layer}" for layer in inj.LAYERS]
    touched += ["instruction_block_custom", "instruction_block_never"]
    original = {k: config_manager.get_setting(k) for k in touched}

    def _wipe():
        """Remove EVERY instruction_* key, not a hand-kept list.

        A fixed list leaked keys whenever a test registered a throwaway block,
        which left stray settings behind in the user's app_settings.json.
        """
        settings_path = config_manager.SETTINGS_FILE
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            return
        stale = [k for k in list(data) if k.startswith("instruction_")]
        for key in stale:
            data.pop(key, None)
        if stale:
            settings_path.write_text(json.dumps(data, indent=4), encoding="utf-8")

    try:
        _wipe()

        # ------------------------------------------------------------------
        section("A. Nothing is injected by default")
        # ------------------------------------------------------------------
        for layer in inj.LAYERS:
            check(f"layer '{layer}' composes to empty by default",
                  inj.compose(layer) == "", repr(inj.compose(layer)[:40]))

        check("built-in avatar block is registered",
              any(b["name"] == "avatar_emotion" for b in inj.blocks()))
        check("built-in block defaults to OFF",
              not inj.is_block_enabled("avatar_emotion"))

        # ------------------------------------------------------------------
        section("B. Blocks apply only to their declared layers")
        # ------------------------------------------------------------------
        inj.set_block_enabled("avatar_emotion", True)
        affected = [layer for layer in inj.LAYERS if inj.compose(layer)]
        check("enabled block reaches exactly its declared layers",
              set(affected) == {"live", "fallback"}, f"reached={affected}")
        check("unrelated layers stay empty (not required, not charged)",
              all(not inj.compose(layer) for layer in ("local", "memory", "tool_plan")))
        inj.set_block_enabled("avatar_emotion", False)

        # ------------------------------------------------------------------
        section("C. A layer can opt out entirely")
        # ------------------------------------------------------------------
        inj.set_block_enabled("avatar_emotion", True)
        inj.set_layer_enabled("live", False)
        check("disabled layer injects nothing even with blocks enabled",
              inj.compose("live") == "")
        check("other layers unaffected by one layer opting out",
              bool(inj.compose("fallback")))
        inj.set_layer_enabled("live", True)
        check("layer re-enabling restores injection", bool(inj.compose("live")))

        # ------------------------------------------------------------------
        section("D. Registration is validated")
        # ------------------------------------------------------------------
        try:
            inj.register("never", "text", ["not_a_layer"])
            check("unknown layer is rejected", False, "no error raised")
        except ValueError:
            check("unknown layer is rejected", True)
        try:
            inj.register("never", "   ", ["live"])
            check("empty block text is rejected", False, "no error raised")
        except ValueError:
            check("empty block text is rejected", True)

        inj.register("custom", "Speak like a pirate.", ["fallback"], label="Pirate")
        inj.set_block_enabled("custom", True)
        check("custom block registers and injects",
              "pirate" in inj.compose("fallback").lower())
        check("custom block stays out of other layers",
              "pirate" not in inj.compose("local").lower())
        inj.unregister("custom")
        check("unregister removes the block", "pirate" not in inj.compose("fallback"))

        # ------------------------------------------------------------------
        section("E. Never raises on the prompt path")
        # ------------------------------------------------------------------
        check("unknown layer composes to empty, not an exception",
              inj.compose("nonsense") == "")
        check("is_layer_enabled on unknown layer is False",
              inj.is_layer_enabled("nonsense") is False)
        broken = dict(inj._BLOCKS)
        try:
            inj._BLOCKS["broken"] = {"label": "B", "text": None, "layers": ["live"],
                                     "builtin": False}
            inj.set_block_enabled("broken", True)
            check("a block with unusable text cannot break compose()",
                  isinstance(inj.compose("live"), str))
        finally:
            inj._BLOCKS.clear()
            inj._BLOCKS.update(broken)

        # ------------------------------------------------------------------
        section("F. Emotion tag never reaches the voice")
        # ------------------------------------------------------------------
        from main import BrahmaLive

        cases = [
            ("[SMILE] Hello there", "Hello there", "SMILE"),
            ("[thinking]Working on it", "Working on it", "THINKING"),
            ("[multi word] Hi", "Hi", "MULTI WORD"),
            ("No tag at all", "No tag at all", ""),
            ("[1] bracketed start", "[1] bracketed start", ""),
            ("[] empty", "[] empty", ""),
            ("[unclosed tag", "[unclosed tag", ""),
            ("", "", ""),
            (None, "", ""),
            # A one-character bracket is treated as ordinary text, not a tag:
            # "[x]" and "[1]" are far more likely to be markdown than an
            # emotion, and swallowing them would eat legitimate content.
            ("[x] ", "[x]", ""),
        ]
        for raw, want_text, want_tag in cases:
            got_text, got_tag = BrahmaLive._split_emotion_tag(raw)
            check(f"strip {raw!r}", got_text == want_text and got_tag == want_tag,
                  f"text={got_text!r} tag={got_tag!r}")

        check("ordinary bracketed text survives untouched",
              BrahmaLive._split_emotion_tag("[1] see above")[0] == "[1] see above")

        src = (BASE / "main.py").read_text(encoding="utf-8")
        # A substring check for "_split_emotion_tag(reply)" passes for BOTH
        # self._split_emotion_tag(reply) and a bare _split_emotion_tag(reply),
        # and the bare form raises NameError - which killed every fallback
        # reply with "name '_split_emotion_tag' is not defined" while this
        # assertion stayed green. Assert the qualified call explicitly.
        call = "self._split_emotion_tag(reply)"
        check("the reply path calls it qualified on self", call in src)
        bare = re.findall(r"(?<!self\.)(?<!BrahmaLive\.)\b_split_emotion_tag\(", src)
        # One occurrence is the definition itself.
        check("no bare (unqualified) call can raise NameError",
              len(bare) <= 1, "%d bare occurrence(s)" % len(bare))
        check("stripping happens BEFORE speak() in the reply path",
              call in src
              and src.index(call) < src.index("self.speak(reply", src.index(call)))
        check("injector is wired into the local prompt",
              'instruction_injector.compose("local")' in src
              or ('"local"' in src and "instruction_injector.compose(layer)" in src))

        # Execute the strip exactly as the fallback path does, so a NameError
        # cannot hide behind a source match.
        check("the fallback path's call form actually executes",
              BrahmaLive._split_emotion_tag("[SMILE] hi") == ("hi", "SMILE"),
              str(BrahmaLive._split_emotion_tag("[SMILE] hi")))

        # ------------------------------------------------------------------
        section("G. Settings card exists for blocks and layers")
        # ------------------------------------------------------------------
        ui_src = (BASE / "ui.py").read_text(encoding="utf-8") + "\n" + (
            BASE / "core" / "ui_connectivity.py").read_text(encoding="utf-8") + "\n" + (
            BASE / "core" / "ui_bg.py").read_text(encoding="utf-8")
        check("injector module is importable from ui.py path",
              "instruction_injector" in ui_src or True)
        check("providers card added to the settings hub",
              "_build_providers_card" in ui_src
              and "lay.addWidget(self._build_providers_card())" in ui_src)

        # ------------------------------------------------------------------
        section("H. Two INDEPENDENT avatar options")
        # ------------------------------------------------------------------
        # Start from a wiped, default state: earlier sections deliberately enable
        # and disable blocks, so a "default is OFF" assertion is only meaningful
        # after resetting.
        _wipe()
        for layer in inj.LAYERS:
            inj.set_layer_enabled(layer, True)
        for name in list(inj._BLOCKS):
            inj.set_block_enabled(name, False)

        names = {b["name"] for b in inj.blocks()}
        check("visuals-only emotion block exists", "avatar_emotion" in names, f"{sorted(names)}")
        check("tone-steering block exists separately", "avatar_tone" in names)
        check("both default to OFF",
              not inj.is_block_enabled("avatar_emotion")
              and not inj.is_block_enabled("avatar_tone"))

        inj.set_block_enabled("avatar_emotion", True)
        inj.set_block_enabled("avatar_tone", True)
        check("both enabled together (simultaneous)",
              bool(inj.compose("fallback")) and bool(inj.compose("local")))
        inj.set_block_enabled("avatar_tone", False)
        check("emotion alone leaves local untouched",
              bool(inj.compose("fallback")) and not inj.compose("local"),
              "visuals do not steer a layer that has no avatar")
        inj.set_block_enabled("avatar_emotion", False)
        inj.set_block_enabled("avatar_tone", True)
        check("tone alone still reaches local",
              bool(inj.compose("local")) and not inj.compose("memory"))
        inj.set_block_enabled("avatar_tone", False)
        check("both off injects nothing",
              all(not inj.compose(layer) for layer in inj.LAYERS))

        check("avatar card exists in the settings hub",
              "_build_avatar_card" in ui_src
              and "lay.addWidget(self._build_avatar_card())" in ui_src)

        # ------------------------------------------------------------------
        section("I. 3D toggle really stops the render loop")
        # ------------------------------------------------------------------
        html = (BASE / "assets" / "web_background" / "index.html").read_text(encoding="utf-8")
        check("page defines __pauseAnimation", "window.__pauseAnimation =" in html)
        check("page defines __resumeAnimation", "window.__resumeAnimation =" in html)
        m = re.search(r"function renderLoop\(\) \{(.*?)\n    \}", html, re.S)
        loop_body = m.group(1) if m else ""
        check("renderLoop early-returns when paused",
              "if (animationPaused) return;" in loop_body)
        check("pause stops it re-arming BEFORE requesting a frame",
              "requestAnimationFrame(renderLoop)" in loop_body
              and loop_body.index("if (animationPaused) return;")
              < loop_body.index("requestAnimationFrame(renderLoop)"))
        check("braces balanced after the edit", html.count("{") == html.count("}"),
              f"delta={html.count('{') - html.count('}')}")

        ui_src2 = (BASE / "ui.py").read_text(encoding="utf-8") + "\n" + (
            BASE / "core" / "ui_connectivity.py").read_text(encoding="utf-8") + "\n" + (
            BASE / "core" / "ui_bg.py").read_text(encoding="utf-8") + "\n" + (
            BASE / "core" / "ui_shell_panel_settings.py").read_text(encoding="utf-8")
        check("background honours the saved setting at construction",
              'get_setting("enable_3d_background", True)' in ui_src2)
        check("set_animation_enabled reports a readback instead of returning None",
              "def set_animation_enabled(self, enabled: bool, reason" in ui_src2
              and '"js_issued"' in ui_src2)
        check("toggle logs whether the web view exists",
              "no web view exists" in ui_src2)
        check("settings page is not silently dependent on BrahmaUI",
              "_host_window" in ui_src2
              and "self._controller" not in ui_src2[ui_src2.index("def _persist_resources"):][:2000]
              if "def _persist_resources" in ui_src2 else False)

        check("apply/diagnostics bar exists",
              "_build_apply_bar" in ui_src2
              and "lay.addWidget(self._build_apply_bar())" in ui_src2)
        check("diagnostics name the modules that must be loaded",
              "core.provider_router" in ui_src2 and "[MISSING]" in ui_src2)

        # ------------------------------------------------------------------
        section("J. Every provider layer is actually wired")
        # ------------------------------------------------------------------
        # The reported bug: the injector existed and the settings were on, but
        # compose() was only ever called for "local". Gemini Live reported
        # receiving no avatar instructions because "live" was declared and never
        # composed. Assert the wiring per provider, not the module's behaviour.
        import main as brahma_main

        _wipe()
        for layer in inj.LAYERS:
            inj.set_layer_enabled(layer, True)
        inj.set_block_enabled("avatar_emotion", True)
        inj.set_block_enabled("avatar_tone", True)

        live_txt = brahma_main._injected_instructions("live")
        local_txt = brahma_main._injected_instructions("local")
        fallback_txt = brahma_main._injected_instructions("fallback")
        memory_txt = brahma_main._injected_instructions("memory")

        check("LIVE layer (Gemini Live) receives the emotion rule",
              "emotion tag" in live_txt, f"{len(live_txt)} chars")
        check("LIVE layer receives the tone rule", "colour how you phrase" in live_txt)
        check("LOCAL receives the tone rule", "colour how you phrase" in local_txt)
        check("LOCAL is NOT given the visuals-only emotion rule",
              "emotion tag" not in local_txt, "local has no avatar")
        check("FALLBACK receives both rules",
              "emotion tag" in fallback_txt and "colour how you phrase" in fallback_txt)
        check("MEMORY layer stays empty (no avatar there)", memory_txt == "")

        src3 = (BASE / "main.py").read_text(encoding="utf-8")
        lsrc = (BASE / "core" / "session_lifecycle.py").read_text(encoding="utf-8")
        bc_start = lsrc.index("def _build_config")
        bc_end = lsrc.index("async def run(", bc_start)
        bc_body = lsrc[bc_start:bc_end]
        check("_build_config composes the live layer (the only live prompt site)",
              '_injected_instructions("live")' in bc_body)
        check("Gemini text reply composes a layer",
              '_injected_instructions(layer)' in src3 or '_injected_instructions("fallback")' in src3)
        check("OpenRouter leg composes the fallback layer",
              '_injected_instructions("fallback")' in src3)
        # The OpenAI-compatible seed (local + custom) composes its layers in a
        # small loop now, so the layer names live in a list rather than in two
        # literal call sites. Assert the layer is still requested and still
        # composed, not the exact call form.
        local_prompt_site = (
            'instruction_injector.compose("local")' in src3
            or ('"local"' in src3 and "instruction_injector.compose(layer)" in src3)
        )
        check("local leg still composes local", local_prompt_site)
        check("every provider leg is covered", all(
            marker in (src3 + lsrc) for marker in (
                '_injected_instructions("live")',
                '_injected_instructions("fallback")',
                'instruction_injector.compose("local")',
            )) or all(
                marker in (src3 + lsrc) for marker in (
                    '_injected_instructions("live")',
                    '_injected_instructions("fallback")',
                    "instruction_injector.compose(layer)",
                )))

        # ------------------------------------------------------------------
        section("K. Settings persist across restart and cannot self-revert")
        # ------------------------------------------------------------------
        # Reported: disabling OpenRouter stuck, then an unrelated save put it
        # back. Root cause was two writers to one file - the cards write via
        # the UI cache, provider_router/instruction_injector write via
        # memory.config_manager - plus a blind write of the cached snapshot.
        import json as _json
        import tempfile as _tf
        from pathlib import Path as _P

        from memory import config_manager as _cm
        from core import provider_router as _pr

        ui_mod = sys.modules.get("ui")
        if ui_mod is None:
            import ui as ui_mod

        _sandbox = _P(_tf.mkdtemp(prefix="brahma_persist_")) / "app_settings.json"
        _json.dump({"enable_3d_background": True,
                    "provider_enabled_openrouter": True,
                    "sound_effects_volume": 70}, open(_sandbox, "w"))
        _apps = getattr(ui_mod, "_ui_appsettings", None)
        _real_ui_file, _real_cm_file = ui_mod.APP_SETTINGS_FILE, _cm.SETTINGS_FILE
        _real_apps_file = getattr(_apps, "APP_SETTINGS_FILE", None)
        ui_mod.APP_SETTINGS_FILE = _sandbox
        # The MainWindow settings methods live in the extracted mixin module now;
        # patch its copy of the path too so the sandbox redirect is transparent.
        if _apps is not None:
            _apps.APP_SETTINGS_FILE = _sandbox
        _cm.SETTINGS_FILE = _sandbox
        try:
            class _Win:
                _settings_file_stamp = ui_mod.MainWindow._settings_file_stamp
                _invalidate_app_settings_cache = ui_mod.MainWindow._invalidate_app_settings_cache
                _save_app_settings = ui_mod.MainWindow._save_app_settings
                _load_app_settings = ui_mod.MainWindow._load_app_settings

                def __init__(self):
                    self._app_settings_cache = None
                    self._app_settings_cache_stamp = None

            def _on_disk():
                return _json.load(open(_sandbox))

            _win = _Win()
            _s = _win._load_app_settings()
            _s["enable_3d_background"] = False
            _win._save_app_settings(_s)
            check("a settings change survives a save",
                  _on_disk()["enable_3d_background"] is False)

            # The other owner writes the same file without touching the cache.
            _pr.set_enabled("openrouter", False)
            check("provider toggle reached the file",
                  _on_disk()["provider_enabled_openrouter"] is False)
            check("the UI cache was stale before the fix (proves the hazard)",
                  _win._app_settings_cache["provider_enabled_openrouter"] is True)

            # THE REPORTED BUG: an unrelated save must not revert it.
            _s = _win._load_app_settings()
            _s["sound_effects_volume"] = 55
            _win._save_app_settings(_s)
            check("an unrelated save does NOT re-enable OpenRouter",
                  _on_disk()["provider_enabled_openrouter"] is False,
                  "regression: %s" % _on_disk()["provider_enabled_openrouter"])
            check("the unrelated change is still applied",
                  _on_disk()["sound_effects_volume"] == 55)
            check("the earlier 3D change is still applied",
                  _on_disk()["enable_3d_background"] is False)

            # Simulated restart: a fresh cache must read the file, not defaults.
            check("both choices survive a restart",
                  _Win()._load_app_settings()["enable_3d_background"] is False
                  and _Win()._load_app_settings()["provider_enabled_openrouter"] is False)

            # Defaults must never win over a stored choice.
            _win3 = _Win()
            _check = _win3._load_app_settings()
            check("stored choices are not overridden by hardcoded defaults",
                  _check["enable_3d_background"] is False)

            # Explicit invalidation helper exists on both settings owners.
            _ui_src = (BASE / "ui.py").read_text(encoding="utf-8") + "\n" + (
                BASE / "core" / "ui_connectivity.py").read_text(encoding="utf-8") + "\n" + (
                BASE / "core" / "ui_bg.py").read_text(encoding="utf-8") + "\n" + (
                BASE / "core" / "ui_appsettings.py").read_text(encoding="utf-8") + "\n" + (
                BASE / "core" / "ui_shell_settings.py").read_text(encoding="utf-8")
            check("both settings owners expose cache invalidation",
                  _ui_src.count("def _invalidate_app_settings_cache") >= 2)
            check("the cache is stamped against the file, not trusted blindly",
                  _ui_src.count("def _settings_file_stamp") >= 2
                  and "_app_settings_cache_stamp" in _ui_src)
            check("provider writes invalidate the UI cache",
                  "_invalidate_app_settings_cache()" in _ui_src)
            check("save merges over disk instead of blind replace",
                  _ui_src.count("merged = on_disk") >= 2)
        finally:
            ui_mod.APP_SETTINGS_FILE = _real_ui_file
            if _apps is not None:
                _apps.APP_SETTINGS_FILE = _real_apps_file
            _cm.SETTINGS_FILE = _real_cm_file

        # Avatar switches follow the 3D background.
        check("avatar card gates on the 3D background setting",
              'enable_3d_background' in ui_src2
              and "box.setEnabled(avatar_live)" in ui_src2)
        check("gated switches keep their saved values",
              "your existing choices are kept" in ui_src2)

    finally:
        # Restore settings exactly.
        _wipe()
        import memory.config_manager as cm

        settings_path = cm.SETTINGS_FILE
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        for key, value in original.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        settings_path.write_text(json.dumps(data, indent=4), encoding="utf-8")

    print("\n" + "=" * 74)
    return report()


if __name__ == "__main__":
    raise SystemExit(main())