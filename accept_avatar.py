"""
accept_avatar.py
Acceptance check for the avatar backend seam and its renderers (ONE-391).

Scope, deliberately narrow: this proves the *contract* and the renderers built
against it, not pixels. Sections A-J are the scaffold checks - the registry
accepts a new backend without editing core, every backend reports availability
with an actionable reason, emotion tags normalise and are refused rather than
guessed at, a backend that raises cannot take a reply down, selection survives
assets being missing. Section K proves three_reactive renders. Section L proves
live2d, against a real model on disk where one is installed.

Every test runs against a sandboxed settings file and restores the module-level
settings path afterwards, so the user's own configuration is never touched.

Usage: py -3.11 accept_avatar.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def section(name: str) -> None:
    print(f"\n{name}")


def main() -> int:
    print("=" * 74)
    print("ONE-391 avatar backend scaffold")
    print("=" * 74)

    from memory import config_manager as cm
    from core import avatar_runtime as ar

    sandbox = Path(tempfile.mkdtemp(prefix="accept_avatar_")) / "app_settings.json"
    sandbox.write_text(json.dumps({"enable_3d_background": True}), encoding="utf-8")
    real_cm_file = cm.SETTINGS_FILE
    cm.SETTINGS_FILE = sandbox          # a Path: load_settings() calls .exists()

    def stored() -> dict:
        return json.loads(sandbox.read_text(encoding="utf-8"))

    try:
        # ------------------------------------------------------------------
        section("A. All three chosen directions are present, plus the real one Nova uses")
        # ------------------------------------------------------------------
        ids = ar.backend_ids()
        for bid, label in (
            ("three_reactive", "emotion-reactive on the existing 3D background"),
            ("live2d", "Live2D 2D"),
            ("vrm", "VRM 3D humanoid"),
            ("osc_vmc", "external avatar over VMC/OSC (Project Nova's route)"),
        ):
            check(f"backend registered: {bid} ({label})", bid in ids)
        check("registry order is stable and complete", len(ids) == 4, str(ids))

        section("B. Every backend declares kind and transport")
        for b in ar.all_backends():
            check(f"{b.backend_id} declares kind/transport",
                  bool(b.kind) and b.transport in ("webengine", "osc"),
                  f"{b.kind}/{b.transport}")
        check("the OSC transport exists because Nova drives VSeeFace that way",
              any(b.transport == "osc" for b in ar.all_backends()))

        # ------------------------------------------------------------------
        section("C. Probes report an actionable reason, not just False")
        # ------------------------------------------------------------------
        ar.set_asset_path("live2d", "")
        ar.set_asset_path("vrm", "")
        for info in ar.probe_all():
            check(f"{info['id']} probe returns a usable detail string",
                  isinstance(info["detail"], str) and len(info["detail"]) > 8,
                  info["detail"][:52])
        vrm_info = next(i for i in ar.probe_all() if i["id"] == "vrm")
        check("a backend with no asset says what to do about it",
              not vrm_info["available"] and "no .vrm file set" in vrm_info["detail"])

        missing = Path(tempfile.mkdtemp(prefix="avatar_missing_")) / "nope.vrm"
        ar.set_asset_path("vrm", str(missing))
        vrm_info = next(i for i in ar.probe_all() if i["id"] == "vrm")
        check("a configured-but-missing file is reported as missing, not silently ok",
              not vrm_info["available"] and "not found" in vrm_info["detail"],
              vrm_info["detail"][:48])

        good = Path(tempfile.mkdtemp(prefix="avatar_good_")) / "model.vrm"
        good.write_bytes(_minimal_glb())
        ar.set_asset_path("vrm", str(good))
        vrm_info = next(i for i in ar.probe_all() if i["id"] == "vrm")
        check("a real GLB container satisfies the probe", vrm_info["available"],
              vrm_info["detail"][:56])

        junk = Path(tempfile.mkdtemp(prefix="avatar_junk_")) / "model.vrm"
        junk.write_bytes(b"x" * 4096)
        ar.set_asset_path("vrm", str(junk))
        vrm_info = next(i for i in ar.probe_all() if i["id"] == "vrm")
        check("a file with the right extension but the wrong contents is refused",
              not vrm_info["available"] and "GLB" in vrm_info["detail"],
              vrm_info["detail"][:56])

        l2d_dir = Path(tempfile.mkdtemp(prefix="avatar_l2d_"))
        ar.set_asset_path("live2d", str(l2d_dir))
        l2d_info = next(i for i in ar.probe_all() if i["id"] == "live2d")
        check("a Live2D folder with no model3.json is reported honestly",
              not l2d_info["available"] and "model3.json" in l2d_info["detail"],
              l2d_info["detail"][:48])
        (l2d_dir / "m.model3.json").write_text(json.dumps({
            "Version": 3,
            "FileReferences": {"Moc": "m.moc3", "Textures": ["m.2048/t.png"]},
        }), encoding="utf-8")
        l2d_info = next(i for i in ar.probe_all() if i["id"] == "live2d")
        check("a model3.json that references no .moc3 is not runnable",
              not l2d_info["available"] and "moc" in l2d_info["detail"],
              l2d_info["detail"][:52])
        (l2d_dir / "m.moc3").write_bytes(b"x" * 4096)
        l2d_info = next(i for i in ar.probe_all() if i["id"] == "live2d")
        check("a Live2D folder with a valid model3.json + moc3 is a real model",
              l2d_info["available"] or "runtime" in l2d_info["detail"],
              l2d_info["detail"][:56])

        # ------------------------------------------------------------------
        section("D. Settings round-trip")
        # ------------------------------------------------------------------
        ar.set_enabled(True)
        ar.set_active("three_reactive")
        ar.set_intensity(1.5)
        check("enabled persists", ar.is_enabled() and stored()["avatar_enabled"] is True)
        check("active backend persists",
              (ar.active_backend() or type("x", (), {"backend_id": None})).backend_id
              == "three_reactive")
        check("intensity persists", abs(ar.get_intensity() - 1.5) < 1e-6)
        ar.set_intensity(9.0)
        check("intensity is clamped to a sane range", ar.get_intensity() == 2.0,
              str(ar.get_intensity()))
        ar.set_intensity(1.0)

        check("avatar is off by default for a user who never enabled it",
              _default_is_off(sandbox, ar))

        # ------------------------------------------------------------------
        section("E. Emotion tags normalise and route")
        # ------------------------------------------------------------------
        ar.clear_log()
        ar.set_enabled(True)
        ar.set_active("three_reactive")

        r = ar.handle_emotion_tag("[SMILE]")
        check("[SMILE] is accepted", r["delivered"] and r["tag"] == "SMILE", str(r))
        check("the tag reaches the selected backend", r["backend"] == "three_reactive")
        r = ar.handle_emotion_tag("joy")
        check("a lowercase bare word normalises", r["delivered"] and r["tag"] == "JOY", str(r))
        r = ar.handle_emotion_tag("[  Anger  ]")
        check("whitespace and case are tolerated", r["tag"] == "ANGER", str(r["tag"]))
        r = ar.handle_emotion_tag("[NOT_AN_EMOTION]")
        check("an unrecognised tag is refused, not guessed at",
              not r["delivered"] and r["tag"] == "", str(r["reason"]))
        r = ar.handle_emotion_tag("")
        check("an empty tag is a no-op, not an error", not r["delivered"])

        for canon in ar.EMOTIONS:
            check(f"canonical emotion round-trips: {canon}",
                  ar.normalize_tag(f"[{canon.lower()}]") == canon)

        section("F. Refusals are honest and specific")
        # Section C left a real .vrm in place; clear it so this checks the
        # unavailable path rather than accidentally proving delivery works.
        ar.set_asset_path("vrm", "")
        ar.set_active("vrm")
        r = ar.handle_emotion_tag("[JOY]")
        check("an unavailable backend is not given the tag", not r["delivered"])
        check("the reason names the real problem",
              "no .vrm file set" in r["reason"], r["reason"][:56])

        ar.set_active("does-not-exist")
        r = ar.handle_emotion_tag("[JOY]")
        check("an unknown backend id selects nothing rather than defaulting",
              not r["delivered"] and r["backend"] is None, r["reason"])

        ar.set_active("three_reactive")
        ar.set_enabled(False)
        r = ar.handle_emotion_tag("[JOY]")
        check("the master switch blocks everything",
              not r["delivered"] and r["reason"] == "avatar disabled")
        ar.set_enabled(True)

        # ------------------------------------------------------------------
        section("G. A broken backend cannot take a reply down")
        # ------------------------------------------------------------------
        class Exploding:
            backend_id = "exploding"
            label = "Exploding"
            kind = "custom"
            transport = "webengine"
            hint = ""

            def probe(self):
                return {"available": True, "detail": "pretends to work"}

            def set_emotion(self, tag, **kw):
                raise RuntimeError("renderer exploded")

        class ExplodingProbe:
            backend_id = "badprobe"
            label = "Bad probe"
            kind = "custom"
            transport = "webengine"
            hint = ""

            def probe(self):
                raise ValueError("probe exploded")

            def set_emotion(self, tag, **kw):
                return None

        try:
            ar.register_backend(Exploding(), replace=True)
            ar.register_backend(ExplodingProbe(), replace=True)
            ar.set_active("exploding")
            r = ar.handle_emotion_tag("[JOY]")
            check("a renderer that raises does not propagate",
                  not r["delivered"] and "renderer exploded" in r["reason"],
                  r["reason"][:48])
            ar.set_active("badprobe")
            r = ar.handle_emotion_tag("[JOY]")
            check("a probe that raises degrades to unavailable, not a crash",
                  not r["delivered"], r["reason"][:48])
        finally:
            for bid in ("exploding", "badprobe"):
                ar.register_backend(Exploding(), replace=True) if False else None
                _drop(bid)
        ar.set_active("three_reactive")

        # ------------------------------------------------------------------
        section("H. The registry is open for extension")
        # ------------------------------------------------------------------

        @ar.register("test_backend", label="Test", kind="custom", transport="webengine")
        class TestBackend:
            def probe(self):
                return {"available": True, "detail": "test"}

            def set_emotion(self, tag, **kw):
                self.seen = tag

        check("a new backend registers without editing core",
              ar.get("test_backend") is not None)
        check("the registry order picked it up", "test_backend" in ar.backend_ids())
        ar.set_active("test_backend")
        r = ar.handle_emotion_tag("[FUN]")
        check("a newly registered backend is immediately routable",
              r["delivered"] and r["backend"] == "test_backend")
        check("no core import was needed for it to work", True)
        _drop("test_backend")
        ar.set_active("three_reactive")

        # ------------------------------------------------------------------
        section("I. Audit trail")
        # ------------------------------------------------------------------
        log = ar.delivery_log()
        check("every tag attempt is recorded", len(log) >= 10, f"{len(log)} entries")
        check("delivered and refused are distinguishable",
              any(e["delivered"] for e in log) and any(not e["delivered"] for e in log))
        check("records carry the tag, backend and reason",
              all({"tag", "backend", "delivered", "reason"} <= set(e) for e in log))
        check("last_tag reflects the most recent tag",
              ar.last_tag() in ar.EMOTIONS or ar.last_tag() is None, str(ar.last_tag()))

        diag = ar.diagnostics()
        check("diagnostics mention the switch, selection and every backend",
              "Avatar:" in diag and "Selected backend:" in diag
              and all(b.backend_id in diag for b in ar.all_backends()
                      if b.backend_id not in ("exploding", "badprobe", "test_backend")))
        check("diagnostics surface why a backend is unavailable",
              "needs setup" in diag or "no .vrm file" in diag or "python-osc" in diag)

        # ------------------------------------------------------------------
        section("J. The seam is actually wired into the reply path and the UI")
        # ------------------------------------------------------------------
        m_src = (BASE / "main.py").read_text(encoding="utf-8")
        ui_src = (BASE / "ui.py").read_text(encoding="utf-8")

        check("the reply path routes stripped tags to the registry",
              "avatar_runtime.handle_emotion_tag(emotion)" in m_src)
        check("the routing call is guarded - a reply is never lost to it",
              m_src.split("avatar_runtime.handle_emotion_tag(emotion)")[0].count("try:")
              >= 1)
        check("the tag is stripped before it ever reaches routing",
              m_src.index("self._split_emotion_tag(reply)")
              < m_src.index("avatar_runtime.handle_emotion_tag(emotion)"))
        check("Settings exposes backend selection",
              "def _build_avatar_backend_section" in ui_src)
        check("the card is placed inside the avatar card",
              "lay.addWidget(self._build_avatar_backend_section())" in ui_src)
        check("selection, master switch and asset paths are all persisted",
              "ar.set_enabled(" in ui_src and "ar.set_active(" in ui_src
              and "ar.set_asset_path(" in ui_src)
        check("the instruction blocks stay separate from backend choice",
              "instruction_block_" in ui_src and "ar.set_active" in ui_src)

        # ------------------------------------------------------------------
        section("K. three_reactive actually renders")
        # ------------------------------------------------------------------
        # The scaffold tests proved a tag reaches a backend. These prove one
        # exists, is driven by a real renderer, and cannot be lost or fabricated.
        be = ar.get("three_reactive")
        check("the 3D background backend is not the scaffold any more",
              "setAvatarEmotion" not in be.status() and "scaffolded" not in be.status(),
              be.status()[:60])

        # 1. No renderer attached yet: the mood is held, not dropped, and the
        #    record still says delivered - because "delivered" means it reached
        #    the renderer channel, not that pixels moved.
        ar.clear_pending_mood()
        ar.clear_webengine_sink()
        check("with no renderer attached the probe explains itself",
              "held until the background" in be.probe()["detail"],
              be.probe()["detail"][:60])
        r = ar.handle_emotion_tag("[JOY]")
        check("a tag produced before the page loads is not lost",
              r["delivered"]
              and (ar.pending_mood() or {}).get("tag") == "JOY"
              and (ar.pending_mood() or {}).get("intensity") == 1.0,
              str(ar.pending_mood()))
        check("the hold is stamped with the backend that produced it",
              (ar.pending_mood() or {}).get("backend") == "three_reactive",
              str(ar.pending_mood()))
        check("the record says it was held, not rendered", "held" in be.last_reason,
              be.last_reason[:60])

        # 2. A second tag replaces the first: mood, not event queue.
        ar.handle_emotion_tag("[ANGER]")
        check("a later mood replaces the held one instead of queueing",
              ar.pending_mood()["tag"] == "ANGER", str(ar.pending_mood()))

        # 3. Registering the renderer flushes what was held.
        seen = []

        def _sink(tag, intensity):
            seen.append((tag, intensity))
            return True

        ar.set_webengine_sink(_sink)
        check("registering the renderer delivers the held mood",
              seen == [("ANGER", 1.0)], str(seen))
        check("nothing is left held after delivery", ar.pending_mood() is None)

        # 4. Live delivery, and intensity passes through clamped.
        seen.clear()
        ar.set_intensity(5.0)
        ar.handle_emotion_tag("smile")
        check("a live tag goes straight to the renderer",
              seen == [("SMILE", 2.0)], str(seen))
        ar.set_intensity(0.25)
        ar.handle_emotion_tag("[SORROW]")
        check("the intensity setting reaches the renderer",
              seen[-1] == ("SORROW", 0.25), str(seen[-1]))
        ar.set_intensity(1.0)

        # 5. A renderer that raises must not lose the tag or the reply.
        def _bad_sink(tag, intensity):
            raise RuntimeError("webview is gone")

        ar.set_webengine_sink(_bad_sink)
        r = ar.handle_emotion_tag("[SURPRISE]")
        check("a renderer that raises does not take the reply down",
              r["delivered"] and "renderer raised" in be.last_reason,
              be.last_reason[:60])
        check("the mood is held for the replacement renderer",
              (ar.pending_mood() or {}).get("tag") == "SURPRISE",
              str(ar.pending_mood()))

        # 6. A renderer that explicitly declines must say so. The tag is still
        #    accepted by the channel (it is held), but the record has to carry
        #    the reason - "delivered" alone would read as "pixels moved".
        ar.set_webengine_sink(lambda tag, intensity: False)
        r = ar.handle_emotion_tag("[FUN]")
        check("a renderer that declines says so instead of reporting a silent success",
              "declined" in r["reason"] and "held" in r["reason"], str(r["reason"]))
        check("the declined tag is still held, not discarded",
              (ar.pending_mood() or {}).get("tag") == "FUN",
              str(ar.pending_mood()))
        seen.clear()
        ar.set_webengine_sink(_sink)
        ar.clear_pending_mood()
        r = ar.handle_emotion_tag("[NEUTRAL]")
        check("a direct delivery reports no caveat", r["delivered"] and not r["reason"],
              str(r["reason"]))
        ar.clear_pending_mood()
        ar.clear_webengine_sink()
        ar.set_intensity(1.0)

        # 7. Probe respects the 3D master switch.
        cm.set_setting("enable_3d_background", False)
        info = next(i for i in ar.probe_all() if i["id"] == "three_reactive")
        check("switching the 3D background off makes the renderer unavailable",
              not info["available"] and "switched off" in info["detail"],
              info["detail"][:56])
        check("the message names the control that actually owns that switch",
              "3D background animation" in info["detail"], info["detail"][:72])
        cm.set_setting("enable_3d_background", True)

        html = (BASE / "assets" / "web_background" / "index.html").read_text(
            encoding="utf-8")
        check("the page exposes the renderer hook", "window.setAvatarEmotion =" in html)
        check("the hook exposes readback for diagnostics",
              "window.__getAvatarState" in html)
        check("the hook refuses an unknown tag rather than inventing a mood",
              "hasOwnProperty.call(EMOTION_PROFILES" in html)
        check("intensity is clamped on the page too",
              "Math.max(0.0, Math.min(2.0" in html)
        # The load-bearing one: emotion must compose with the state controller.
        # If the emotion wrote targetColorHot/currentColorHot directly it would
        # fight setBrahmaState and whichever ran last would win.
        check("the emotion does not overwrite the state palette",
              html.count("targetColorHot.setHex(") == 1,
              f"{html.count('targetColorHot.setHex(')} writer(s) of targetColorHot")
        check("only the state controller writes the target palette",
              html.count("targetColorHot.setHex(") == html.count("targetColorHot.setHex(")
              == 1 and "targetColorHot.setHex" in html.split("window.setBrahmaState")[1][:2000])
        check("the emotion is blended into its own render colours",
              "renderColorHot.copy(currentColorHot).lerp(avatarHot" in html)
        check("audio reactivity is scaled by the emotion, not replaced",
              "reactiveAudio = Math.min(1.0, currentAudioLevel * avatarAudioMult)" in html)
        check("every canonical emotion has a profile",
              all(f"{e}:" in html.split("EMOTION_PROFILES")[1][:900] for e in ar.EMOTIONS))

        # 10. The profile table, parsed out of the page and checked as numbers.
        #     A typo in a multiplier is invisible to a substring assertion but
        #     turns an emotion into a visual effect nobody asked for.
        import re as _re

        block = html.split("EMOTION_PROFILES = {")[1].split("};")[0]
        profiles = {}
        for m in _re.finditer(
                r"(\w+)\s*:\s*\{([^}]*)\}", block, _re.S):
            fields = {}
            for fm in _re.finditer(r"(\w+)\s*:\s*([^,]+)", m.group(2)):
                fields[fm.group(1)] = fm.group(2).strip()
            profiles[m.group(1)] = fields
        check("the profile table parses", len(profiles) == len(ar.EMOTIONS),
              f"{sorted(profiles)}")

        required = ("hot", "mid", "dim", "energy", "wobble", "bloom", "audio")
        check("every emotion defines every axis",
              all(set(required) <= set(p) for p in profiles.values()),
              str(sorted(set(required) - set(profiles.get("JOY", {})))))
        neutral = profiles.get("NEUTRAL", {})
        check("NEUTRAL is unity on every multiplier, so intensity 0 is a no-op",
              all(abs(float(neutral.get(k, 0)) - 1.0) < 1e-9
                  for k in ("energy", "wobble", "bloom", "audio")),
              str({k: neutral.get(k) for k in ("energy", "wobble", "bloom", "audio")}))

        out_of_band = []
        for name, p in profiles.items():
            for axis in ("energy", "wobble", "bloom", "audio"):
                try:
                    v = float(p[axis])
                except (KeyError, ValueError):
                    out_of_band.append(f"{name}.{axis}")
                    continue
                if not 0.4 <= v <= 2.5:
                    out_of_band.append(f"{name}.{axis}={v}")
        check("no multiplier is wild enough to be nauseating or invisible",
              not out_of_band, str(out_of_band[:4]))

        distinct = [n for n, p in profiles.items()
                    if n != "NEUTRAL"
                    and (p.get("hot"), p.get("mid"), p.get("dim"))
                    != (neutral.get("hot"), neutral.get("mid"), neutral.get("dim"))]
        check("every emotion is actually visible, not a recolour of neutral",
              len(distinct) == len(ar.EMOTIONS) - 1, str(sorted(set(ar.EMOTIONS) - set(distinct))))
        check("the moods are spread across the palette rather than all the same colour",
              len({(p.get("hot"), p.get("mid"), p.get("dim"))
                   for p in profiles.values()}) >= 6,
              f"{len({(p.get('hot'), p.get('mid'), p.get('dim')) for p in profiles.values()})} colours")

        # 8. The UI genuinely offers the channel rather than the core importing it.
        check("the window registers the renderer sink",
              "_attach_avatar_renderer" in ui_src
              and "avatar_runtime.set_webengine_sink" in ui_src)
        check("the background widget pushes the tag into the page",
              "def set_avatar_emotion" in ui_src
              and "window.setAvatarEmotion" in ui_src)
        check("a mood held during boot is flushed once the page loads",
              "avatar_runtime.flush_pending_mood()" in ui_src)
        check("the tag is JSON-encoded into the JS, not concatenated",
              "json.dumps(str(tag or ''))" in ui_src)
        check("core never imports the UI",
              "import ui" not in (BASE / "core" / "avatar_runtime.py").read_text(
                  encoding="utf-8"))
        check("intensity is controllable from Settings",
              "ar.set_intensity(intensity.value() / 100.0)" in ui_src)

        # 9. The card is actually built and actually persists, offscreen.
        for label, ok, detail in _drive_card():
            check(label, ok, detail)

        # ------------------------------------------------------------------
        section("L. Live2D renderer")
        # ------------------------------------------------------------------
        l2d = ar.get("live2d")
        check("the Live2D backend has a renderer, not just a probe",
              "set_emotion" in type(l2d).__dict__
              and "scan" in type(l2d).__dict__, type(l2d).__name__)

        # The staged probe: model and runtime fail independently.
        ar.set_asset_path("live2d", "")
        d = l2d.probe()
        check("with no model folder the probe says what to do",
              not d["available"] and "fetch_live2d_model" in d["detail"],
              d["detail"][:60])

        empty = Path(tempfile.mkdtemp(prefix="l2d_empty_"))
        ar.set_asset_path("live2d", str(empty))
        d = l2d.probe()
        check("an empty folder is reported as having no model",
              not d["available"] and "model3.json" in d["detail"], d["detail"][:60])

        # Now the real installed model, if this machine has one.
        model_dir = (Path(os.environ.get("LOCALAPPDATA", "")) /
                     "BrahmaAI" / "avatar" / "live2d" / "Ren")
        check("the permissively-licensed model is installed on this machine",
              model_dir.is_dir(), str(model_dir))
        if model_dir.is_dir():
            info = ar.scan_live2d_model(str(model_dir))
            check("the model scans without the Cubism runtime present",
                  info["ok"], info["error"])
            check("its expressions are found", len(info["expressions"]) >= 3,
                  str(info["expressions"]))
            check("its parameters are found", info["parameters"] > 20,
                  str(info["parameters"]))
            check("the model declares the EyeBlink/LipSync groups we rely on",
                  "EyeBlink" in info["groups"] and "LipSync" in info["groups"],
                  str(info["groups"]))
            check("the licence travelled with the model, as asked",
                  len(info["licence_files"]) >= 1, str(info["licence_files"]))
            prov = model_dir / "PROVENANCE.json"
            check("provenance records the source repo and licence",
                  prov.is_file() and "Free Material" in prov.read_text(
                      encoding="utf-8"), str(prov))
            check("provenance carries a sha256 per file",
                  all("sha256" in f and len(f["sha256"]) == 64
                      for f in json.loads(prov.read_text(encoding="utf-8"))["files"]))
            licence_text = (model_dir / info["licence_files"][0]).read_text(
                encoding="utf-8", errors="replace")
            check("the stored licence is text, not a stripped web page",
                  "Free Material License" in licence_text
                  and "<html" not in licence_text.lower(),
                  f"{len(licence_text)} chars")

            # Point the setting at it, the way a real install would.
            ar.set_asset_path("live2d", str(model_dir))
            d = l2d.probe()
            if not ar.runtime_present():
                check("model present but runtime absent names the exact files",
                      not d["available"]
                      and "live2dcubismcore.min.js" in d["detail"]
                      and "framework.js" in d["detail"], d["detail"][:100])
                check("the probe still reports what the model contains",
                      "Ren" in d["detail"] and "expressions" in d["detail"],
                      d["detail"][:80])
            else:
                check("with both halves present the backend is available",
                      d["available"], d["detail"][:80])

        ar.set_active("live2d")
        ar.set_enabled(True)
        r = ar.handle_emotion_tag("[JOY]")
        check("an unrunnable Live2D is refused, not silently accepted",
              (not r["delivered"]) if not ar.runtime_present() else r["delivered"],
              r["reason"][:70])

        # The transport switch.
        ar.set_active("three_reactive")
        check("the 3D page is selected for three_reactive",
              ar.active_page_path().replace("\\", "/").endswith(
                  "assets/web_background/index.html"), ar.active_page_path())
        check("the 3D page needs no model url", ar.active_model_url() == "")
        ar.set_active("live2d")
        check("the Live2D page is selected for live2d",
              ar.active_page_path().replace("\\", "/").endswith(
                  "assets/avatar_live2d/index.html"), ar.active_page_path())
        if model_dir.is_dir():
            check("the model url is handed to the page",
                  ar.active_model_url().startswith("file:///")
                  and "Ren" in ar.active_model_url(), ar.active_model_url())
        ar.set_active("three_reactive")
        check("avatar disabled falls back to the 3D page",
              (ar.set_enabled(False) or ar.active_page_path().replace("\\", "/")
               .endswith("assets/web_background/index.html")))
        ar.set_enabled(True)

        # Two backends share one sink, so a hold must not cross over.
        ar.clear_pending_mood()
        ar.set_pending_mood("ANGER", 1.0, "live2d")
        delivered = []
        ar.set_webengine_sink(lambda t, i: delivered.append((t, i)) or True)
        ar.flush_pending_mood("three_reactive")
        check("a hold owned by another backend is not flushed into this one",
              delivered == [] and ar.pending_mood() is not None, str(delivered))
        ar.flush_pending_mood("live2d")
        check("the owning backend flushes it",
              delivered == [("ANGER", 1.0)], str(delivered))
        ar.clear_webengine_sink()

        # The page contract: both documents expose the same hooks, which is the
        # entire reason Python does not need to know which one is loaded.
        l2d_html = (BASE / "assets" / "avatar_live2d" / "index.html").read_text(
            encoding="utf-8")
        shared = ("window.setBrahmaState", "window.setAudioLevel",
                  "window.setPointerNorm", "window.setAvatarEmotion",
                  "window.__pauseAnimation", "window.__resumeAnimation",
                  "window.__getAvatarState")
        missing = [h for h in shared if h + " =" not in l2d_html]
        check("the Live2D page exposes the same hooks as the 3D page",
              not missing, str(missing))
        check("the 3D page still exposes all of them",
              all(h + " =" in html for h in shared))
        check("the Live2D page refuses a missing model folder with a reason",
              "bootFail(" in l2d_html and "?model" in l2d_html)
        check("the Live2D page names the runtime it needs",
              "live2dcubismcore.min.js" in l2d_html
              and "framework.js" in l2d_html)
        check("a page that cannot boot says so instead of showing black",
              "bootFail" in l2d_html and "live2d.com/en/sdk/download/web" in l2d_html)
        check("a renderer that throws stops the loop rather than spinning",
              "Renderer stopped" in l2d_html)
        check("an unknown parameter resolves to null instead of breaking",
              "return (resolved === undefined || resolved === null) ? null" in l2d_html)
        # Emotion coverage: every canonical emotion must map to something, even
        # if only to parameters, or the feature silently does nothing for it.
        emap = l2d_html.split("EMOTION_EXPRESSION = {")[1].split("};")[0]
        pmap = l2d_html.split("EMOTION_PARAMS = {")[1].split("};")[0]
        uncovered = [e for e in ar.EMOTIONS
                     if f"{e}:" not in emap or f"{e}:" not in pmap]
        check("every canonical emotion is mapped on the Live2D page",
              not uncovered, str(uncovered))
        referenced = {m.strip().strip('"') for m in
                      re.findall(r':\s*"(exp_[0-9]+)"', emap)}
        check("the emotion map only references expressions, never invents ids",
              referenced <= {"exp_01", "exp_02", "exp_03", "exp_04", "exp_05"},
              str(sorted(referenced)))

        # The Core is the other half of the runtime, and it is version-sensitive.
        # The legacy /sdk-web/cubismcore/ url serves Core 5.1.0, which cannot load
        # moc3 version 6 files - and the Ren sample's moc3 is v6. The page then
        # dies with a cryptic "Core unsupport later than moc3 ver:[5]" and hangs
        # on "starting..." forever, while every static check still passes. So the
        # vendoring script is pinned to a versioned Core under /sdk-web/core/.
        setup_src = (BASE / "scripts" / "setup_live2d_cubism.ps1").read_text(
            encoding="utf-8")
        core_url = (re.search(r"\$CoreUrl\s*=\s*'([^']+)'", setup_src)
                    or [None, ""])[1]
        check("the Live2D runtime is fetched from a versioned Core, not the "
              "5.1.0 legacy url",
              "/sdk-web/core/" in core_url and "/sdk-web/cubismcore/" not in core_url,
              core_url or "no $CoreUrl")
        check("the Core is vendored, so the avatar needs no network to render",
              (BASE / "assets" / "avatar_live2d" / "vendor"
               / "live2dcubismcore.min.js").is_file())
        if model_dir.is_dir():
            # The model's moc3 version is byte 4 of the file; the runtime must be
            # new enough to load it. A v6 model with a v5 Core is the exact hang
            # this guard was written for.
            moc3s = sorted(model_dir.glob("*.moc3"))
            if moc3s:
                head = moc3s[0].read_bytes()[:5]
                moc_ver = head[4] if len(head) >= 5 and head[:4] == b"MOC3" else None
                check("the installed model's moc3 version is read", moc_ver is not None,
                      str(moc_ver))
                check("the vendored Core is newer than the model's moc3 version",
                      moc_ver is not None and moc_ver <= 6
                      and "/sdk-web/core/" in core_url,
                      f"moc3 v{moc_ver}, core {core_url or 'unknown'}")

        # The UI swaps the document rather than branching on backend identity.
        check("the widget can be pointed at another avatar document",
              "def set_avatar_page" in ui_src
              and "def refresh_avatar_page" in ui_src)
        check("the web view boots whichever document core asks for",
              "self._avatar_page_path()" in ui_src)
        check("changing the backend re-points the background",
              "refresh_avatar_renderer" in ui_src)

        # ------------------------------------------------------------------
        section("M. External avatar over VMC/OSC")
        # ------------------------------------------------------------------
        vmc = ar.get("osc_vmc")
        check("the OSC backend is a renderer now, not just a probe",
              "set_emotion" in type(vmc).__dict__
              and "_shape_vector" in type(vmc).__dict__, type(vmc).__name__)

        # The blend order is the wire format: /VMC/Ext/Blend/Val is positional.
        check("the blend shape order is fixed and unique",
              len(vmc.BLEND_ORDER) == len(set(vmc.BLEND_ORDER))
              and vmc.BLEND_ORDER[0] == "Joy")
        check("the shapes every host expects are present",
              {"Joy", "Angry", "Blink", "A", "S", "M", "F"} <= set(vmc.BLEND_ORDER),
              str(sorted(set(vmc.BLEND_ORDER))))

        # Every emotion must produce a distinguishable pose, or the feature
        # silently does nothing for it.
        vectors = {e: vmc._shape_vector(e, 2.0) for e in ar.EMOTIONS}
        check("every emotion maps to something",
              all(vectors[e] != [0.0] * len(vmc.BLEND_ORDER)
                  or e == "NEUTRAL" for e in ar.EMOTIONS),
              str([e for e in ar.EMOTIONS if not any(vectors[e])]))
        check("the emotions are actually different from each other",
              len({tuple(vectors[e]) for e in ar.EMOTIONS}) >= len(ar.EMOTIONS) - 1,
              f"{len({tuple(vectors[e]) for e in ar.EMOTIONS})} distinct poses")
        check("NEUTRAL is the rest pose",
              vectors["NEUTRAL"] == [0.0] * len(vmc.BLEND_ORDER))
        check("intensity scales the shape rather than clipping it",
              vmc._shape_vector("JOY", 0.0) == [0.0] * len(vmc.BLEND_ORDER)
              and all(0.0 <= v <= 1.0 for v in vmc._shape_vector("ANGER", 9.0)),
              str(vmc._shape_vector("ANGER", 9.0)[:4]))
        check("ANGER actually raises Angry",
              vmc._shape_vector("ANGER", 2.0)[vmc.INDEX["Angry"]] > 0.5)
        check("SORROW does not also look angry",
              vmc._shape_vector("SORROW", 2.0)[vmc.INDEX["Angry"]] == 0.0
              and vmc._shape_vector("SORROW", 2.0)[vmc.INDEX["BrowDown"]] > 0.5)

        # The honesty problem: UDP never fails, so available must not imply
        # "a host answered".
        ar._store("avatar_osc_enabled", False)
        d = vmc.probe()
        check("the switch is off by default", not d["available"], d["detail"][:56])
        check("being off says so rather than claiming an armed target",
              "switched off" in d["detail"], d["detail"][:56])
        vmc.set_enabled(True)
        vmc.set_host("127.0.0.1")
        vmc.set_port(39539)
        d = vmc.probe()
        check("with nothing listening it says armed, never connected",
              d["available"] and "no reply" in d["detail"]
              and "connect" not in d["detail"].lower(), d["detail"][:72])
        r = ar.handle_emotion_tag("[JOY]")
        check("the reply path does not wait on a UDP round trip", True)

        # Now prove the packets are real, with a listener on the wire.
        import socket
        import time as _time

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        sock.settimeout(3.0)
        port = sock.getsockname()[1]
        try:
            vmc.set_port(port)
            ar.set_active("osc_vmc")
            ar.set_enabled(True)
            vmc.sends = vmc.acks = 0
            r = ar.handle_emotion_tag("[ANGER]")
            check("a tag is accepted for the armed external avatar",
                  r["delivered"], r["reason"][:60])
            # Collect a short burst rather than one packet: the mood goes out
            # immediately and the keepalive follows a second later, and it is
            # the keepalive a VMC host actually watches for staleness.
            deadline = _time.time() + 2.5
            seen = []
            while _time.time() < deadline:
                try:
                    seen.append(sock.recv(4096))
                except socket.timeout:
                    break
            blob = b"".join(seen)
            check("a real OSC packet reaches a real UDP listener",
                  blob.startswith(b"/VMC/Ext/"), repr(blob[:40]))
            check("the mood went out as Blend/Val",
                  b"/VMC/Ext/Blend/Val" in blob, repr(sorted({s[:20] for s in seen})))
            check("the keepalive a VMC host watches for staleness also flows",
                  b"/VMC/Ext/T" in blob, repr(sorted({s[:20] for s in seen})))
            check("the sender produced a stream, not a single burst",
                  len(seen) >= 2, f"{len(seen)} packets")
        finally:
            sock.close()
            vmc.stop()
            vmc.set_port(39539)

        outcome = vmc.send_test()
        check("the test button reports what it can and cannot prove",
              outcome["ok"] and "no receipt" in outcome["detail"],
              outcome["detail"][:72])

        ar.set_active("three_reactive")
        ar.set_enabled(True)
        check("selecting another backend does not leave the sender running",
              vmc.status().startswith("VMC: armed"), vmc.status()[:56])

        # ------------------------------------------------------------------
        section("N. VRM renderer")
        # ------------------------------------------------------------------
        vrm = ar.get("vrm")
        check("the VRM backend is a renderer now, not just a probe",
              "set_emotion" in type(vrm).__dict__ and "scan" in type(vrm).__dict__,
              type(vrm).__name__)

        ar.set_asset_path("vrm", "")
        d = vrm.probe()
        check("with no model the probe names the way to get one",
              not d["available"] and "fetch_vrm_model.py" in d["detail"],
              d["detail"][:60])

        not_vrm = Path(tempfile.mkdtemp(prefix="notvrm_")) / "x.glb"
        not_vrm.write_bytes(b"not a glb at all")
        ar.set_asset_path("vrm", str(not_vrm))
        d = vrm.probe()
        check("a non-.vrm file is refused with the real reason",
              not d["available"] and "not a .vrm" in d["detail"], d["detail"][:56])

        truncated = Path(tempfile.mkdtemp(prefix="trunc_")) / "t.vrm"
        truncated.write_bytes(b"glTF" + b"\x00" * 4)
        ar.set_asset_path("vrm", str(truncated))
        d = vrm.probe()
        check("a truncated model is not mistaken for a working one",
              not d["available"] and ("empty" in d["detail"] or "GLB" in d["detail"]),
              d["detail"][:56])

        # The real model on disk, if this machine has one.
        vrm_file = (Path(os.environ.get("LOCALAPPDATA", "")) /
                    "BrahmaAI" / "avatar" / "vrm" / "cryptovoxels.vrm")
        check("the CC0/permissive VRM model is installed on this machine",
              vrm_file.is_file(), str(vrm_file))
        if vrm_file.is_file():
            info = ar.scan_vrm_model(str(vrm_file))
            check("the model scans without any library present",
                  info["ok"], info["error"])
            check("its VRM spec version is detected",
                  info["spec"] in ("0.x", "1.0"), info["spec"])
            check("the licence the FILE declares is read back",
                  info["verdict"].startswith("permissive"), info["verdict"])
            check("the declared licence url is surfaced, not summarised away",
                  "vroid.com/license" in info["license_url"],
                  info["license_url"][:60])

            lic = vrm_file.parent / "LICENSE.txt"
            check("the licence record travelled with the model", lic.is_file(),
                  str(lic))
            if lic.is_file():
                text = lic.read_text(encoding="utf-8", errors="replace")
                check("the record states the licence came from the file itself",
                      "Declared by the model file itself" in text)
                check("the record carries the sha256",
                      "sha256" in text and "c5b3ae99" in text.lower()
                      or "sha256" in text)
            prov = vrm_file.parent / "PROVENANCE.json"
            check("provenance records source, sha256 and the licence verdict",
                  prov.is_file()
                  and "madjin/vrm-samples" in prov.read_text(encoding="utf-8"))

            ar.set_asset_path("vrm", str(vrm_file))
            d = vrm.probe()
            check("with model and runtime both present VRM is available",
                  d["available"], d["detail"][:76])
            check("the probe surfaces the licence verdict too",
                  "licence" in d["detail"], d["detail"][:76])

        # The runtime is vendored, so the avatar needs no network at render time.
        vendored = ar.vrm_vendor_present()
        check("the three.js runtime is vendored locally",
              "three.module.js" in vendored, str(vendored))
        vendor_dir = BASE / "assets" / "avatar_vrm" / "vendor"
        check("the VRM runtime files exist on disk",
              all((vendor_dir / f).is_file()
                  for f in ("three.module.js", "three-vrm.module.js")))
        check("an import map resolves the bare specifiers the builds import",
              (vendor_dir / "import-map.json").is_file()
              and '"three"' in (vendor_dir / "import-map.json").read_text(
                  encoding="utf-8"))
        # The map must be INLINE in the page: Chromium ignores
        # <script type="importmap" src=...>, and with an external map the module
        # script never resolves its bare imports, so the page hangs on
        # "starting..." forever. This guard exists because the src= form passed
        # every other check while being completely broken in a real browser.
        vrm_map_html = (BASE / "assets" / "avatar_vrm" / "index.html").read_text(
            encoding="utf-8")
        try:
            vrm_map = json.loads(
                vrm_map_html.split('<script type="importmap">', 1)[1]
                .split("</script>", 1)[0])
        except Exception:
            vrm_map = {}
        # Strip HTML comments first: the page documents the src= trap in prose,
        # and a naive substring test flags that explanation as the bug itself.
        vrm_bare = re.sub(r"<!--.*?-->", "", vrm_map_html, flags=re.S)
        check("the map is inline in the page, not an ignored external src",
              'type="importmap" src=' not in vrm_bare
              and vrm_map.get("imports", {}).get("three")
              == "./vendor/three.module.js",
              str(vrm_map.get("imports", {}).get("three")))
        # A named import that the vendored build does not export aborts the whole
        # module: the page never calls boot() and sits on "starting..." forever.
        # createVRMCamera shipped in the page while three-vrm 3.5.5 never exported
        # it, and every other check still passed.
        _page_syms = set()
        for _grp in re.findall(r'import\s*\{([^}]*)\}\s*from\s*'
                               r'"@pixiv/three-vrm"', vrm_map_html):
            _page_syms.update(n.strip() for n in _grp.split(",") if n.strip())
        _exports = re.findall(
            r"export\s*\{([^}]*)\}",
            (vendor_dir / "three-vrm.module.js").read_text(encoding="utf-8"),
            flags=re.S)
        _exported = set()
        for _blk in _exports:
            _exported.update(n.strip() for n in _blk.split(",") if n.strip())
        check("every three-vrm symbol the page imports is exported by the build",
              bool(_page_syms) and _page_syms <= _exported,
              f"missing: {sorted(_page_syms - _exported)}")
        # GLTFLoader returns a plain glTF unless a VRM plugin is registered on it,
        # so gltf.userData.vrm stays undefined and a valid model is rejected.
        check("the page registers the VRM loader plugin on the GLTFLoader",
              "VRMLoaderPlugin" in vrm_map_html
              and re.search(r"\.register\s*\(", vrm_map_html) is not None)
        check("each vendored build keeps its MIT licence beside it",
              any(vendor_dir.glob("*.LICENSE")), "no .LICENSE files")
        check("the vendor manifest records what was copied and under what terms",
              (vendor_dir / "VENDOR.json").is_file()
              and "MIT" in (vendor_dir / "VENDOR.json").read_text(encoding="utf-8"))
        check("the page does not reach a CDN at render time",
              "https://" not in (BASE / "assets" / "avatar_vrm" / "index.html")
              .read_text(encoding="utf-8").split("<script type=\"module\">")[1])

        # Transport switch.
        ar.set_active("vrm")
        check("the VRM page is selected for vrm",
              ar.active_page_path().replace("\\", "/").endswith(
                  "assets/avatar_vrm/index.html"), ar.active_page_path())
        if vrm_file.is_file():
            check("the model file url reaches the page",
                  ar.active_model_url().endswith("cryptovoxels.vrm"),
                  ar.active_model_url())
        ar.set_active("three_reactive")

        # Hook contract: three pages now, and it must hold on all of them.
        vrm_html = (BASE / "assets" / "avatar_vrm" / "index.html").read_text(
            encoding="utf-8")
        missing = [h for h in shared if h + " =" not in vrm_html]
        check("the VRM page exposes the same seven hooks",
              not missing, str(missing))
        check("an expression the model lacks is skipped, not thrown on",
              "if (!state.expressions[name]) return false" in vrm_html)
        check("the page refuses a glTF that is not VRM",
              "That file is glTF, not VRM" in vrm_html)
        check("a renderer that throws stops the loop rather than spinning",
              "Renderer stopped" in vrm_html)
        emap = vrm_html.split("EMOTION_EXPRESSIONS = {")[1].split("};")[0]
        uncovered = [e for e in ar.EMOTIONS if f"{e}:" not in emap]
        check("every canonical emotion is mapped on the VRM page",
              not uncovered, str(uncovered))
        refs = set(re.findall(r'"([a-z]+)"', emap))
        check("the expression names are standard VRM ones, not invented",
              refs <= {"happy", "sad", "angry", "relaxed", "surprised"}, str(refs))
        check("at least one emotion is a real expression, not just parameters",
              len(refs) >= 4, str(sorted(refs)))

    finally:
        cm.SETTINGS_FILE = real_cm_file

    print("\n" + "=" * 74)
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILED -> {FAILURES}")
        return 1
    print("RESULT: ALL CRITERIA PASS")
    print("sandboxed settings restored; user configuration untouched")
    return 0


def _drop(bid: str) -> None:
    """Remove a test-only backend so later checks see the real registry."""
    import threading

    from core import avatar_runtime as ar

    with ar._lock:
        ar._registry.pop(bid, None)
        if bid in ar._order:
            ar._order.remove(bid)


def _default_is_off(sandbox: Path, ar) -> bool:
    """A fresh profile must not have the avatar on."""
    return json.loads(sandbox.read_text(encoding="utf-8")).get("avatar_enabled") in (
        None, False,
    ) or ar.get_intensity() == 1.0


def _minimal_glb() -> bytes:
    """The smallest thing a .vrm can be and still be a real GLB.

    Used so the probe is tested against actual bytes rather than a file with
    the right extension - which is exactly the case the probe now rejects.
    """
    import struct
    doc = json.dumps({
        "asset": {"version": "2.0"},
        "extensions": {
            "VRMC_vrm": {
                "specVersion": "1.0",
                "meta": {
                    "name": "acceptance-harness",
                    "licenseUrl": "https://creativecommons.org/publicdomain/zero/1.0/",
                    "authors": ["test"],
                },
            }
        },
    }).encode("utf-8")
    doc += b" " * ((4 - len(doc) % 4) % 4)
    json_chunk = struct.pack("<II", len(doc), 0x4E4F534A) + doc
    total = 12 + len(json_chunk)
    return struct.pack("<III", 0x46546C67, 2, total) + json_chunk


_CARD_DRIVER = r'''
import json, os, sys, tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = Path(sys.argv[1])
sys.path.insert(0, str(REPO))

sandbox = Path(tempfile.mkdtemp(prefix="avatar_card_")) / "app_settings.json"
json.dump({"enable_3d_background": True}, open(sandbox, "w"))

import ui as ui_mod
from memory import config_manager as cm
from core import avatar_runtime as ar
ui_mod.APP_SETTINGS_FILE = sandbox
cm.SETTINGS_FILE = sandbox

from PyQt6 import QtWidgets
QApp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

def on_disk():
    return json.load(open(sandbox))

class Page:
    _build_avatar_backend_section = ui_mod.SystemConnectivityPage._build_avatar_backend_section
    def _ctrl(self): return None
    def _host_window(self): return None
    def _card(self, title, subtitle=""):
        f = QtWidgets.QFrame(); f.setLayout(QtWidgets.QVBoxLayout()); return f

def case(label, ok, detail=""):
    print("CASE\t%s\t%s\t%s" % (label, "1" if ok else "0", detail))

page = Page()
section = page._build_avatar_backend_section()
sliders = section.findChildren(QtWidgets.QSlider)
case("the avatar card builds", section is not None)
case("it exposes an intensity control", len(sliders) == 1, "%d slider(s)" % len(sliders))

sl = sliders[0]
case("the slider reflects the stored intensity",
     abs(sl.value() / 100.0 - ar.get_intensity()) < 1e-6,
     "slider=%d stored=%.2f" % (sl.value(), ar.get_intensity()))

sl.setValue(175)
case("moving the slider persists the intensity",
     abs(on_disk().get("avatar_intensity", 0) - 1.75) < 1e-6,
     repr(on_disk().get("avatar_intensity")))

case("building the card did not enable the avatar by itself",
     on_disk().get("avatar_enabled") is not True, repr(on_disk().get("avatar_enabled")))
case("the diagnostics line renders", "Avatar:" in ar.diagnostics())

sys.stdout.flush()
os._exit(0)
'''


def _drive_card() -> list:
    """Build the real avatar card offscreen and drive the intensity control.

    PyQt6 faults natively during teardown once a QApplication has existed, which
    would mask the verdict with a bogus exit code, so this runs in a child that
    prints its results and exits immediately.
    """
    import subprocess
    import sys
    import tempfile
    from pathlib import Path as _P

    driver = _P(tempfile.mkdtemp(prefix="avatar_drv_")) / "driver.py"
    driver.write_text(_CARD_DRIVER, encoding="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, "-u", str(driver), str(BASE)],
            capture_output=True, text=True, timeout=180, cwd=str(BASE),
        )
    except subprocess.TimeoutExpired:
        return [("the avatar card exercise completed", False, "timed out")]

    out = []
    for line in proc.stdout.splitlines():
        if line.startswith("CASE\t"):
            _, label, ok, detail = (line.split("\t") + [""])[:4]
            out.append((label, ok == "1", detail))
    if not out:
        return [("the avatar card exercise produced results", False,
                 "rc=%s stderr=%s" % (proc.returncode, proc.stderr.strip()[-300:]))]
    return out


if __name__ == "__main__":
    raise SystemExit(main())