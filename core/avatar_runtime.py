"""
core/avatar_runtime.py
Avatar backend registry: the seam where emotion tags become visuals.

Why this exists
---------------
Three avatar directions were chosen at once - emotion/audio-reactive driving the
existing WebEngine background, a Live2D model, and a VRM humanoid - and none of
them is implemented yet. Without a seam, the first one to be built would hardcode
its assumptions into the reply path, and the next two would each need another
branch there. The reply path already has a place where an emotion tag is peeled
off a reply (`BrahmaLive._split_emotion_tag`); this module is the single
destination for that tag.

The contract is deliberately small, and deliberately *narrower* than it will
eventually need to be:

    probe()               -> is this backend usable right now, and if not, why
    set_emotion(tag, ...) -> best-effort, must never raise
    status()              -> one line for diagnostics

That is enough to select a backend, gate it on assets that exist, and route
a tag. `three_reactive` is implemented against it, as the proof that the seam
is real; the asset-backed ones ship probe-and-report only until their renderers
are written. Because the contract is so small, the whole pipeline can be
exercised end to end before a single renderer exists, and a renderer can then
be added without touching the reply path.

Transports
----------
`transport="webengine"` renders in-process inside the existing QWebEngine
background. `transport="osc"` drives an external avatar over UDP OSC using the
VMC protocol (what Project Nova does with VSeeFace). Both are first-class
because Project Nova shows the external-avatar route is a real option, not a
hypothetical, and forcing it through the WebEngine transport would have made the
wrong assumption for the third backend.

Writing a backend
-----------------
Subclass nothing, register a duck type:

    @register("my_avatar", label="My avatar", kind="3d_reactive",
              transport="webengine")
    class MyBackend:
        def probe(self):  return {"available": True}
        def set_emotion(self, tag, **kw):  ...

or call `register_backend(AvatarBackend(...))` with any object exposing the same
three methods. The registry never imports a backend by name, so adding one does
not require editing this module.
"""
from __future__ import annotations

import json
import os
import struct
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from memory import config_manager

_lock = threading.RLock()

SETTING_ACTIVE = "avatar_backend"
SETTING_ENABLED = "avatar_enabled"
SETTING_INTENSITY = "avatar_intensity"


# Canonical emotion vocabulary. Backends map from these; they never invent their
# own names, because the tag is model-produced text and must stay stable.
EMOTIONS = (
    "NEUTRAL", "JOY", "SORROW", "ANGER", "FUN",
    "SURPRISE", "THINKING", "SMILE",
)


class AvatarBackend:
    """The contract. A backend is any object with these three methods."""

    backend_id: str = ""
    label: str = ""
    kind: str = "unknown"        # 3d_reactive | live2d | vrm | osc_vmc | custom
    transport: str = "webengine"  # webengine | osc
    hint: str = ""

    def probe(self) -> Dict[str, Any]:
        """Report whether this backend can run now.

        Must not raise. Returning a reason matters more than a bare False: the
        Settings card shows `detail`, and "you have not chosen a model file" is
        actionable where "unavailable" is not.
        """
        return {"available": False, "detail": "no probe implemented"}

    def set_emotion(self, tag: str, **kwargs: Any) -> None:
        """Route one emotion tag to the renderer. Must not raise."""
        return None

    def status(self) -> str:
        return f"{self.label}: not implemented yet"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
_registry: Dict[str, AvatarBackend] = {}
_order: List[str] = []


def register_backend(backend: AvatarBackend, *, replace: bool = False) -> AvatarBackend:
    bid = getattr(backend, "backend_id", "") or ""
    if not bid:
        raise ValueError("avatar backend needs a backend_id")
    with _lock:
        if bid in _registry and not replace:
            return _registry[bid]
        if bid not in _registry:
            _order.append(bid)
        _registry[bid] = backend
    return backend


def register(bid: str, *, label: str, kind: str, transport: str = "webengine",
             hint: str = "") -> Callable[[type], type]:
    """Class decorator: registers a backend under `bid`."""

    def _wrap(cls: type) -> type:
        cls.backend_id = bid
        cls.label = label or bid
        cls.kind = kind
        cls.transport = transport
        cls.hint = hint
        register_backend(cls(), replace=True)
        return cls

    return _wrap


def get(bid: str) -> Optional[AvatarBackend]:
    with _lock:
        return _registry.get(bid)


def all_backends() -> List[AvatarBackend]:
    with _lock:
        return [_registry[b] for b in _order if b in _registry]


def backend_ids() -> List[str]:
    with _lock:
        return list(_order)


def describe() -> str:
    """One-line summary for diagnostics, same shape as provider_router."""
    return " > ".join(
        f"{b.backend_id}{'' if is_available(b) else ' (unavailable)'}"
        for b in all_backends()
    )


# ---------------------------------------------------------------------------
# Settings (one writer: config_manager, like provider_router)
# ---------------------------------------------------------------------------
def _setting(key: str, default: Any = None) -> Any:
    try:
        return config_manager.get_setting(key, default)
    except Exception:
        return default


def _store(key: str, value: Any) -> None:
    try:
        config_manager.set_setting(key, value)
    except Exception:
        pass


def is_enabled() -> bool:
    """Master switch. Off by default: the whole feature is opt-in."""
    return bool(_setting(SETTING_ENABLED, False))


def set_enabled(on: bool) -> None:
    _store(SETTING_ENABLED, bool(on))


def get_intensity() -> float:
    try:
        return max(0.0, min(2.0, float(_setting(SETTING_INTENSITY, 1.0))))
    except Exception:
        return 1.0


def set_intensity(value: float) -> None:
    _store(SETTING_INTENSITY, max(0.0, min(2.0, float(value))))


def active_backend() -> Optional[AvatarBackend]:
    """The selected backend, or None when none is selected/available.

    Selection is stored separately from availability on purpose: a chosen
    backend whose asset is missing must stay *chosen* so the UI can say what is
    missing, rather than silently falling back to something else.
    """
    bid = str(_setting(SETTING_ACTIVE, "") or "")
    if not bid:
        return None
    backend = get(bid)
    if backend is None:
        return None
    if not is_enabled():
        return None
    return backend


def set_active(bid: str) -> None:
    _store(SETTING_ACTIVE, str(bid or ""))


def asset_path(bid: str) -> str:
    return str(_setting(f"avatar_asset_{bid}", "") or "")


def set_asset_path(bid: str, path: str) -> None:
    _store(f"avatar_asset_{bid}", str(path or ""))


def is_available(backend: Any) -> bool:
    if backend is None:
        return False
    probe = getattr(backend, "probe", None)
    if not callable(probe):
        return False
    try:
        return bool((probe() or {}).get("available"))
    except Exception:
        return False


def probe_all() -> List[Dict[str, Any]]:
    """Availability report for every registered backend. Never raises."""
    out = []
    for b in all_backends():
        try:
            info = dict(b.probe() or {})
        except Exception as exc:
            info = {"available": False, "detail": f"probe raised {exc}"}
        info.setdefault("detail", "")
        out.append({
            "id": b.backend_id,
            "label": b.label,
            "kind": b.kind,
            "transport": b.transport,
            "hint": b.hint,
            "available": bool(info.get("available")),
            "detail": str(info.get("detail") or ""),
            "asset": asset_path(b.backend_id),
        })
    return out


# ---------------------------------------------------------------------------
# Emotion intake - the single entry point for the reply path
# ---------------------------------------------------------------------------
_last_tag: Optional[str] = None
_delivery_log: List[Dict[str, Any]] = []
MAX_LOG = 50


def last_tag() -> Optional[str]:
    with _lock:
        return _last_tag


def delivery_log() -> List[Dict[str, Any]]:
    with _lock:
        return list(_delivery_log)


def clear_log() -> None:
    with _lock:
        _delivery_log.clear()


def normalize_tag(tag: str) -> str:
    t = str(tag or "").strip().strip("[]").strip().upper()
    return t if t in EMOTIONS else ""


def _probe_detail(backend: Any) -> str:
    """probe()'s detail, or a reason. Never raises.

    The whole point of handle_emotion_tag is that a broken avatar cannot take a
    reply down, and this is the call site that broke that promise: asking for the
    detail unguarded let a raising probe escape into the reply path.
    """
    probe = getattr(backend, "probe", None)
    if not callable(probe):
        return "backend has no probe()"
    try:
        return str((probe() or {}).get("detail", "") or "")
    except Exception as exc:
        return f"probe failed: {exc}"


def handle_emotion_tag(tag: str) -> Dict[str, Any]:
    """Route one emotion tag to the active backend.

    This is the seam. The reply path calls it after peeling a tag off a reply;
    it never needs to know which backend is selected, and a backend never needs
    to know how the tag was produced.

    Never raises: a broken avatar must never take down a reply. Always returns
    a record of what happened, so the UI and the tests can see it.
    """
    record: Dict[str, Any] = {
        "tag": "", "backend": None, "delivered": False,
        "reason": "", "intensity": get_intensity(),
    }
    with _lock:
        global _last_tag
        normalized = normalize_tag(tag)
        _last_tag = normalized or None
    if not normalized:
        record["reason"] = "no tag / unrecognised"
        _log(record)
        return record
    record["tag"] = normalized

    if not is_enabled():
        record["reason"] = "avatar disabled"
        _log(record)
        return record
    backend = active_backend()
    if backend is None:
        record["reason"] = "no backend selected"
        _log(record)
        return record
    record["backend"] = backend.backend_id
    if not is_available(backend):
        record["reason"] = "selected backend unavailable: " + _probe_detail(backend)
        _log(record)
        return record

    try:
        outcome = backend.set_emotion(normalized, intensity=get_intensity(),
                                      normalized=normalized)
        # A backend may decline (explicit False) - e.g. the WebEngine renderer is
        # not attached and refuses to buffer. That is not delivered, and the
        # backend's own last_reason is more useful than a generic message.
        # Anything else (None, as the no-op backends return) counts as delivered,
        # which is the pre-existing contract.
        if outcome is False:
            record["delivered"] = False
            record["reason"] = str(getattr(backend, "last_reason", "")
                                   or "backend declined the tag")
        else:
            record["delivered"] = True
            # A call can succeed and still be partial: the renderer may not be
            # attached yet, so the mood is being held rather than drawn. Carry the
            # backend's own note so the audit trail never implies pixels moved
            # when they did not.
            note = str(getattr(backend, "last_reason", "") or "")
            if note:
                record["reason"] = note
    except Exception as exc:
        # A renderer that throws is a rendering problem, not a reply problem.
        record["reason"] = f"backend raised: {exc}"
    _log(record)
    return record


def _log(record: Dict[str, Any]) -> None:
    with _lock:
        _delivery_log.append(dict(record))
        if len(_delivery_log) > MAX_LOG:
            del _delivery_log[:-MAX_LOG]


# ---------------------------------------------------------------------------
# The 'webengine' transport
# ---------------------------------------------------------------------------
# A backend must never import the UI: core is the layer everything depends on,
# and importing a widget from it inverts the dependency for the sake of one
# renderer. So the WebEngine transport is a one-slot channel the UI fills in.
#
# The slot is *last mood wins*, not a queue. An emotion tag is a mood, not an
# event: if the renderer is still loading, holding the newest tag and dropping
# the older ones is the correct behaviour, and it also means a renderer that is
# never attached cannot grow memory without bound.
_webengine_sink: Optional[Callable[[str, float], bool]] = None
_pending_mood: Optional[Dict[str, Any]] = None


def set_webengine_sink(fn: Optional[Callable[[str, float], bool]]) -> None:
    """Register the function that pushes (tag, intensity) into the page.

    Replacing is allowed and last-wins: several widgets can host the same page,
    and only one of them is the visible background.
    """
    global _webengine_sink
    with _lock:
        _webengine_sink = fn
        held = _pending_mood
    if held:
        # Flush for the *selected* backend only. A hold belongs to the backend
        # that produced it, and the two webengine backends share one sink - so
        # an un-scoped flush here would push a live2d mood into the 3D page
        # after the user switched backends.
        flush_pending_mood(str(_setting(SETTING_ACTIVE, "") or ""))


def clear_webengine_sink(fn: Optional[Callable[[str, float], bool]] = None) -> None:
    """Detach the sink. If `fn` is given, only detach when it is the current one."""
    global _webengine_sink
    with _lock:
        if fn is not None and _webengine_sink is not fn:
            return
        _webengine_sink = None


# ---------------------------------------------------------------------------
# Model scanning
# ---------------------------------------------------------------------------
# A .model3.json is plain JSON, so the model can be inspected - textures,
# expressions, parameters, motion groups - without the Cubism runtime present.
# That matters: the runtime is proprietary and has to be fetched by hand, so a
# probe that could only answer "installed or not" would say nothing useful on a
# machine where the model is present and the runtime is not, which is exactly
# the state this is in.

LIVE2D_PAGE = "assets/avatar_live2d/index.html"
THREE_PAGE = "assets/web_background/index.html"
L2D_VENDOR = "assets/avatar_live2d/vendor"
L2D_RUNTIME_FILES = ("live2dcubismcore.min.js", "framework.js")


def find_model_file(folder: str) -> str:
    """The *.model3.json under `folder`, or ""."""
    if not folder or not os.path.isdir(folder):
        return ""
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if f.endswith(".model3.json"):
                return os.path.join(root, f)
    return ""


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def runtime_present() -> List[str]:
    """Which of the two Cubism runtime files are installed. Empty = none."""
    base = os.path.join(_repo_root(), L2D_VENDOR)
    out = []
    for name in L2D_RUNTIME_FILES:
        try:
            if os.path.isfile(os.path.join(base, name)) and \
                    os.path.getsize(os.path.join(base, name)) > 0:
                out.append(name)
        except Exception:
            pass
    return out


VRM_PAGE = "assets/avatar_vrm/index.html"
VRM_VENDOR = "assets/avatar_vrm/vendor"
VRM_RUNTIME_FILES = ("three.module.js", "three-vrm.module.js")


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))     # .../core
    return os.path.dirname(here)


def scan_live2d_model(folder: str) -> Dict[str, Any]:
    """Everything worth knowing about a model folder, without loading it."""
    out: Dict[str, Any] = {
        "folder": folder or "", "model_file": "", "name": "",
        "expressions": [], "parameters": 0, "groups": [],
        "textures": 0, "motions": 0, "moc_bytes": 0, "licence_files": [],
        "ok": False, "error": "",
    }
    if not folder:
        out["error"] = "no model folder set"
        return out
    if not os.path.isdir(folder):
        out["error"] = f"folder not found: {folder}"
        return out

    model_file = find_model_file(folder)
    if not model_file:
        out["error"] = "no *.model3.json found under the configured folder"
        return out
    out["model_file"] = model_file
    out["name"] = os.path.basename(model_file)[:-len(".model3.json")]

    data = _read_json(model_file)
    refs = data.get("FileReferences") or {}
    out["expressions"] = [e.get("Name", "") for e in (refs.get("Expressions") or [])
                          if isinstance(e, dict)]
    out["textures"] = len(refs.get("Textures") or [])
    out["motions"] = sum(len(v) for v in (refs.get("Motions") or {}).values()
                         if isinstance(v, list))
    for group in (data.get("Groups") or []):
        if isinstance(group, dict):
            out["groups"].append(str(group.get("Name", "")))

    moc = refs.get("Moc") or ""
    if moc:
        moc_path = os.path.join(os.path.dirname(model_file), moc)
        try:
            out["moc_bytes"] = os.path.getsize(moc_path)
        except Exception:
            out["error"] = f"moc not found: {moc}"

    cdi = refs.get("DisplayInfo") or ""
    if cdi:
        cdi_data = _read_json(os.path.join(os.path.dirname(model_file), cdi))
        out["parameters"] = len(cdi_data.get("Parameters") or [])

    for f in os.listdir(folder):
        if f.upper().startswith("LICENSE") or f.upper().startswith("COPYING"):
            out["licence_files"].append(f)

    out["ok"] = not out["error"] and out["moc_bytes"] > 0
    if not out["ok"] and not out["error"]:
        out["error"] = "model file present but the .moc3 is missing or empty"
    return out


def webengine_sink_attached() -> bool:
    with _lock:
        return callable(_webengine_sink)


def pending_mood() -> Optional[Dict[str, Any]]:
    with _lock:
        return dict(_pending_mood) if _pending_mood else None


def set_pending_mood(tag: str, intensity: float, backend: str = "") -> None:
    """Hold a mood for a renderer that is not attached yet.

    Stamped with the backend that produced it: three_reactive and live2d share
    one sink and one page, so an un-stamped mood could be flushed into the wrong
    renderer when the user switches backends mid-hold.
    """
    global _pending_mood
    with _lock:
        _pending_mood = {"tag": str(tag), "intensity": clamp_intensity(intensity),
                         "backend": str(backend or "")}


def clear_pending_mood(backend: str = "") -> None:
    """Drop the held mood. With `backend` given, only if it owns the hold."""
    global _pending_mood
    with _lock:
        if backend and _pending_mood and _pending_mood.get("backend") not in ("", backend):
            return
        _pending_mood = None


def flush_pending_mood(backend: str = "") -> bool:
    """Push a held mood to the renderer. Returns True if it went out.

    Called by the UI when the background page finishes loading, which is the
    only moment a tag emitted during boot can be honoured instead of dropped.
    """
    with _lock:
        sink, held = _webengine_sink, _pending_mood
    if sink is None or not held:
        return False
    if backend and held.get("backend") not in ("", backend):
        return False
    try:
        ok = bool(sink(held["tag"], held["intensity"]))
    except Exception:
        return False
    if ok:
        clear_pending_mood()
    return ok


def clamp_intensity(value: Any) -> float:
    try:
        return max(0.0, min(2.0, float(value)))
    except (TypeError, ValueError):
        return 1.0


def _file_url(path: str) -> str:
    """Local path -> file:/// URL, forward slashes, no quoting games."""
    p = str(path or "").replace("\\", "/")
    if not p:
        return ""
    if not p.startswith("/"):
        p = "/" + p
    return "file://" + p


def active_page_path() -> str:
    """Local path of the document the WebEngine background should be showing.

    This is the transport switch, and it is a pure function of the selected
    backend so the UI never has to ask "which backend am I rendering" - it asks
    for a path. Both documents honour the same seven hooks, so nothing
    downstream changes when the page does.
    """
    bid = str(_setting(SETTING_ACTIVE, "") or "")
    root = _repo_root()
    if is_enabled():
        if bid == "live2d":
            return os.path.join(root, LIVE2D_PAGE)
        if bid == "vrm":
            return os.path.join(root, VRM_PAGE)
    return os.path.join(root, THREE_PAGE)


def active_model_url() -> str:
    """file:/// URL of the model for the active backend, or "".

    Passed to the page as ?model=... . Models live in the per-user data folder,
    outside the repository, so the page cannot guess the path.
    """
    bid = str(_setting(SETTING_ACTIVE, "") or "")
    if not is_enabled():
        return ""
    if bid == "live2d":
        info = scan_live2d_model(asset_path(bid))
        if info["model_file"]:
            return _file_url(os.path.dirname(info["model_file"]))
        return ""
    if bid == "vrm":
        info = scan_vrm_model(asset_path(bid))
        if info["ok"]:
            return _file_url(info["path"])
    return ""


def diagnostics() -> str:
    lines = [f"Avatar: {'enabled' if is_enabled() else 'disabled'} "
             f"(intensity {get_intensity():.2f})"]
    chosen = str(_setting(SETTING_ACTIVE, "") or "") or "(none)"
    lines.append(f"Selected backend: {chosen}")
    lines.append(f"WebEngine renderer: "
                 f"{'attached' if webengine_sink_attached() else 'not attached'}"
                 f"{'; holding ' + pending_mood()['tag'] if pending_mood() else ''}")
    lines.append(f"Backends: {describe()}")
    for info in probe_all():
        mark = "OK" if info["available"] else "--"
        detail = info["detail"] or info["hint"]
        lines.append(f"  [{mark}] {info['id']} ({info['kind']}/{info['transport']}) {detail}")
    lines.append(f"Last tag: {last_tag() or '(none)'}")
    lines.append(f"Tag attempts recorded: {len(delivery_log())}")
    return "<br>".join(lines)


# ---------------------------------------------------------------------------
# Built-in backends. All scaffolded: probes are real, renderers are not.
# ---------------------------------------------------------------------------
@register(
    "three_reactive",
    label="3D background (emotion-reactive)",
    kind="3d_reactive",
    transport="webengine",
    hint="Drives the existing WebEngine background. No extra asset needed.",
)
class ThreeReactiveBackend(AvatarBackend):
    """Emotion/audio-reactive intensity on the existing Three.js background.

    The cheapest of the four and the only one with no asset: the app already
    renders a reactive WebGL background and already ships an audio level into
    it, so an emotion is a mood applied on top of that look rather than a new
    renderer to maintain.

    Implementation is the WebEngine channel above plus three functions on the
    page (`window.setAvatarEmotion`, and the composition in
    `updateSimulation`). Nothing here knows how many rings or shaders exist.
    """

    def __init__(self) -> None:
        self.last_reason = ""
        self.last: Optional[tuple] = None

    def probe(self) -> Dict[str, Any]:
        if not _setting("enable_3d_background", True):
            return {"available": False,
                    "detail": "3D background is switched off - enable "
                              "'3D background animation' in Settings > "
                              "Providers & Resources"}
        if webengine_sink_attached():
            return {"available": True,
                    "detail": "renderer attached - emotion drives the existing "
                              "3D background"}
        return {"available": True,
                "detail": "ready - the mood is held until the background "
                          "finishes loading"}

    def set_emotion(self, tag: str, **kw: Any) -> bool:
        self.last_reason = ""
        intensity = clamp_intensity(kw.get("intensity", get_intensity()))
        self.last = (tag, intensity)

        with _lock:
            sink = _webengine_sink
        if sink is not None:
            try:
                if sink(tag, intensity):
                    clear_pending_mood()
                    return True
                self.last_reason = "renderer declined the tag"
            except Exception as exc:
                self.last_reason = f"renderer raised: {exc}"

        # No renderer yet, or it refused. Hold the mood rather than lose it: the
        # background page loads asynchronously, so a tag produced in the first
        # second of the session would otherwise always be dropped.
        set_pending_mood(tag, intensity, self.backend_id)
        if self.last_reason:
            self.last_reason += "; "
        self.last_reason += "held for the renderer"
        return True

    def status(self) -> str:
        held = pending_mood()
        if held and held.get("backend") in ("", self.backend_id):
            return f"3D background: renderer not attached, holding {held['tag']}"
        if webengine_sink_attached():
            last = self.last
            return (f"3D background: renderer attached"
                    + (f", last {last[0]} @ {last[1]:.2f}" if last else ""))
        return "3D background: ready, waiting for the page to load"


@register(
    "live2d",
    label="Live2D 2D model",
    kind="live2d",
    transport="webengine",
    hint="Needs a Cubism runtime folder containing a *.model3.json.",
)
class Live2DBackend(AvatarBackend):
    """Live2D Cubism, in its own page inside the same QWebEngineView.

    A separate document rather than a mode of the 3D background: the background
    already owns a GL context, a three.js scene and a render loop, and a Cubism
    model needs its own canvas, core and update cycle. Both documents expose the
    same seven hooks (`setBrahmaState`, `setAudioLevel`, `setPointerNorm`,
    `setAvatarEmotion`, `__pauseAnimation`, `__resumeAnimation`,
    `__getAvatarState`), so the reply path and the UI swap a URL and carry on
    calling the same methods.

    The probe is staged, because the two halves fail independently and for
    unrelated reasons: the model is ordinary JSON anyone can supply, while the
    Cubism runtime is proprietary and has to be fetched by hand.
    """

    def __init__(self) -> None:
        self.last_reason = ""
        self.last: Optional[tuple] = None

    def scan(self) -> Dict[str, Any]:
        return scan_live2d_model(asset_path(self.backend_id))

    def probe(self) -> Dict[str, Any]:
        info = self.scan()
        if not info["ok"]:
            if info["error"] == "no model folder set":
                return {"available": False,
                        "detail": "no model folder set - run fetch_live2d_model.py, "
                                  "or point it at a Cubism runtime folder"}
            return {"available": False, "detail": info["error"]}

        have = runtime_present()
        missing = [f for f in L2D_RUNTIME_FILES if f not in have]
        bits = (f"{info['name']}: {info['moc_bytes'] // 1024} KB moc, "
                f"{info['expressions'] and len(info['expressions']) or 0} expressions, "
                f"{info['parameters']} parameters")
        if missing:
            return {"available": False,
                    "detail": f"{bits} - but the Cubism runtime is missing "
                              f"({', '.join(missing)}); copy both files into "
                              f"{L2D_VENDOR}/ from live2d.com"}
        if not info["expressions"]:
            return {"available": True,
                    "detail": f"{bits} - no expressions, emotion drives parameters only"}
        return {"available": True, "detail": bits}

    def set_emotion(self, tag: str, **kw: Any) -> bool:
        self.last_reason = ""
        intensity = clamp_intensity(kw.get("intensity", get_intensity()))
        self.last = (tag, intensity)

        with _lock:
            sink = _webengine_sink
        if sink is not None:
            try:
                if sink(tag, intensity):
                    clear_pending_mood(self.backend_id)
                    return True
                self.last_reason = "renderer declined the tag"
            except Exception as exc:
                self.last_reason = f"renderer raised: {exc}"

        set_pending_mood(tag, intensity, self.backend_id)
        if self.last_reason:
            self.last_reason += "; "
        self.last_reason += "held for the renderer"
        return True

    def status(self) -> str:
        info = self.scan()
        if not info["ok"]:
            return f"Live2D: {info['error']}"
        if not runtime_present():
            return f"Live2D: model {info['name']} present, Cubism runtime not installed"
        held = pending_mood()
        if held and held.get("backend") in ("", self.backend_id):
            return f"Live2D: renderer attached, holding {held['tag']}"
        return f"Live2D: renderer attached ({info['name']})"


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))     # .../core
    return os.path.dirname(here)


def vrm_vendor_present() -> List[str]:
    """Which vendored VRM runtime modules are installed. Empty = none."""
    base = os.path.join(_repo_root(), VRM_VENDOR)
    out = []
    for name in VRM_RUNTIME_FILES:
        path = os.path.join(base, name)
        try:
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                out.append(name)
        except Exception:
            pass
    return out


def scan_vrm_model(path: str) -> Dict[str, Any]:
    """Read a .vrm's own declared identity and licence, with no library needed.

    A .vrm is a GLB: a 12-byte header then chunks. The first chunk is JSON,
    which carries the VRM metadata - including the licence the model declares
    about itself. That is the same evidence `fetch_vrm_model.py` gates on, so
    the probe can surface it here too.

    Implemented inline rather than importing the installer so that core stays
    dependency-free; the format is stable and eleven lines long.
    """
    out: Dict[str, Any] = {"path": path or "", "name": "", "spec": "",
                           "license_url": "", "verdict": "", "bytes": 0,
                           "ok": False, "error": ""}
    if not path:
        out["error"] = "no .vrm file set"
        return out
    if not os.path.isfile(path):
        out["error"] = f"file not found: {path}"
        return out
    if not path.lower().endswith(".vrm"):
        out["error"] = "not a .vrm file"
        return out
    try:
        size = os.path.getsize(path)
        out["bytes"] = size
        if size < 20:
            out["error"] = "file is empty or truncated"
            return out
        with open(path, "rb") as fh:
            head = fh.read(12)
            if len(head) < 12 or struct.unpack("<I", head[:4])[0] != 0x46546C67:
                out["error"] = "not a GLB container (VRM files are GLB)"
                return out
            chunk_len, chunk_type = struct.unpack("<II", fh.read(8))
            if chunk_type != 0x4E4F534A:
                out["error"] = "first GLB chunk is not JSON"
                return out
            data = json.loads(fh.read(chunk_len).decode("utf-8", errors="replace"))
    except Exception as exc:
        out["error"] = f"could not read the model: {exc}"
        return out

    exts = data.get("extensions") or {}
    vrm1 = exts.get("VRMC_vrm")
    if isinstance(vrm1, dict):
        out["spec"] = str(vrm1.get("specVersion", "1.0"))
        meta = vrm1.get("meta") or {}
    else:
        vrm0 = exts.get("VRM") or ((data.get("asset") or {}).get("extras", {})
                                   .get("VRM")) or {}
        meta = vrm0.get("meta") if isinstance(vrm0, dict) else {}
        meta = meta if isinstance(meta, dict) else {}
        out["spec"] = "0.x"
    out["name"] = str(meta.get("name") or "")
    out["license_url"] = str(meta.get("licenseUrl")
                             or meta.get("otherLicenseUrl") or "")
    low = out["license_url"].lower()
    if "creativecommons.org/publicdomain/zero" in low:
        out["verdict"] = "CC0"
    elif "creativecommons.org/licenses/by/" in low:
        out["verdict"] = "CC BY"
    elif "vroid.com/license" in low and "redistribution=allow" in low.replace(
            "%3d", "=").replace("&amp;", "&"):
        out["verdict"] = "permissive (VRoid Hub flags)"
    elif out["license_url"]:
        out["verdict"] = "declared, not obviously permissive"
    else:
        out["verdict"] = "undeclared"
    out["ok"] = True
    return out


@register(
    "vrm",
    label="VRM 3D humanoid",
    kind="vrm",
    transport="webengine",
    hint="Needs a .vrm file. Rendered with three.js in the existing WebEngine.",
)
class VRMBackend(AvatarBackend):
    """VRM humanoid via @pixiv/three-vrm, in its own page.

    The pleasant contrast with `live2d`: the entire runtime is MIT and
    mirrorable, so it is vendored into assets/avatar_vrm/vendor/ and the avatar
    never touches the network at render time. Nothing here needs a licence
    click, which is why this backend will actually reach a working state on a
    fresh machine where live2d will not.
    """

    def __init__(self) -> None:
        self.last_reason = ""
        self.last: Optional[tuple] = None

    def scan(self) -> Dict[str, Any]:
        return scan_vrm_model(asset_path(self.backend_id))

    def probe(self) -> Dict[str, Any]:
        info = self.scan()
        if not info["ok"]:
            if info["error"] == "no .vrm file set":
                return {"available": False,
                        "detail": "no .vrm file set - run fetch_vrm_model.py, "
                                  "or point it at one you own"}
            return {"available": False, "detail": info["error"]}

        have = vrm_vendor_present()
        missing = [f for f in VRM_RUNTIME_FILES if f not in have]
        bits = (f"{os.path.basename(info['path'])} "
                f"({info['bytes'] // 1024} KB, VRM {info['spec']}, "
                f"licence: {info['verdict']})")
        if missing:
            return {"available": False,
                    "detail": f"{bits} - but the three.js runtime is not "
                              f"vendored ({', '.join(missing)}); run "
                              f"vendor_vrm_runtime.py"}
        return {"available": True, "detail": bits}

    def set_emotion(self, tag: str, **kw: Any) -> bool:
        self.last_reason = ""
        intensity = clamp_intensity(kw.get("intensity", get_intensity()))
        self.last = (tag, intensity)

        with _lock:
            sink = _webengine_sink
        if sink is not None:
            try:
                if sink(tag, intensity):
                    clear_pending_mood(self.backend_id)
                    return True
                self.last_reason = "renderer declined the tag"
            except Exception as exc:
                self.last_reason = f"renderer raised: {exc}"

        set_pending_mood(tag, intensity, self.backend_id)
        if self.last_reason:
            self.last_reason += "; "
        self.last_reason += "held for the renderer"
        return True

    def status(self) -> str:
        info = self.scan()
        if not info["ok"]:
            return f"VRM: {info['error']}"
        if not vrm_vendor_present():
            return f"VRM: model present, three.js runtime not vendored"
        return f"VRM: renderer attached ({os.path.basename(info['path'])})"


@register(
    "osc_vmc",
    label="External avatar (VMC / OSC)",
    kind="osc_vmc",
    transport="osc",
    hint="Drives VSeeFace (or any VMC host) over UDP OSC. Project Nova uses this.",
)
class OscVmcBackend(AvatarBackend):
    """External avatar over the VMC protocol on UDP.

    Project Nova already proves this route: it sends /VMC/Ext/Bone/* and
    /VMC/Ext/Blend/* to VSeeFace on 127.0.0.1:39539 via python-osc. Keeping it
    as its own transport means the WebEngine backends and an external renderer
    are genuinely interchangeable from the reply path's point of view.

    Two things about this transport that the others do not have to think about:

    **UDP never fails.** Sending to 127.0.0.1:39539 with nothing listening
    succeeds silently, forever. So `available` here means "armed", never
    "confirmed", and the honest signal is `sends` vs `acks` below - a VMC host
    that is not running simply never answers.

    **It is not a page.** The other two renderers are documents inside the
    QWebEngineView. This one talks to another program, so it owns a sender
    thread and a keepalive instead of a URL.
    """

    # Blend shapes every VMC host derived from an MMD/VRoid model accepts, in
    # the positional order /VMC/Ext/Blend/Val expects. The protocol is
    # positional, so the order is part of the wire format, not a preference.
    BLEND_ORDER = ("Joy", "Angry", "Blink", "Blink_L", "Blink_R",
                   "BrowDown", "BrowUp", "A", "S", "M", "F")
    INDEX = {name: i for i, name in enumerate(BLEND_ORDER)}

    # Emotion -> blend shapes. Values are added on top of whatever the host's
    # idle animation already contributes, which is why they are partial: a
    # model with no "Blink_L" shape simply ignores that column.
    EMOTION_SHAPES = {
        "NEUTRAL":  {},
        "JOY":      {"Joy": 1.0, "M": -0.4},
        "SMILE":    {"Joy": 0.7, "S": 0.5, "M": -0.3},
        "SORROW":   {"BrowDown": 1.0, "S": -0.6, "M": 0.4},
        "ANGER":    {"Angry": 1.0, "BrowDown": 0.7, "M": 0.8},
        "FUN":      {"Joy": 0.6, "S": 0.4, "F": 0.6},
        "SURPRISE": {"A": 0.9, "BrowUp": 0.7, "Joy": 0.3},
        "THINKING": {"BrowUp": 0.4, "S": -0.2},
    }

    KEEPALIVE_SECONDS = 1.0
    APPLY = "/VMC/Ext/Blend/Apply"
    BLEND_VAL = "/VMC/Ext/Blend/Val"
    TIME_SYNC = "/VMC/Ext/T"

    def __init__(self) -> None:
        self.last_reason = ""
        self.last: Optional[tuple] = None
        self.sends = 0
        self.acks = 0
        self.last_error = ""
        self._client = None
        self._client_key: tuple = ()
        self._values = [0.0] * len(self.BLEND_ORDER)
        self._dirty = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- settings ---------------------------------------------------------
    def host(self) -> str:
        return str(_setting("avatar_osc_host", "127.0.0.1") or "127.0.0.1")

    def port(self) -> int:
        try:
            return int(_setting("avatar_osc_port", 39539) or 39539)
        except (TypeError, ValueError):
            return 39539

    def enabled(self) -> bool:
        return bool(_setting("avatar_osc_enabled", False))

    def set_enabled(self, on: bool) -> None:
        _store("avatar_osc_enabled", bool(on))

    def set_host(self, host: str) -> None:
        """Point at a different target. Drops the cached client so the next
        send actually goes to the new address rather than the old socket."""
        value = str(host or "127.0.0.1").strip() or "127.0.0.1"
        _store("avatar_osc_host", value)
        with _lock:
            self._client = None
            self._client_key = ()

    def set_port(self, port: Any) -> None:
        try:
            value = int(port)
        except (TypeError, ValueError):
            value = 39539
        _store("avatar_osc_port", value)
        with _lock:
            self._client = None
            self._client_key = ()

    # -- probe ------------------------------------------------------------
    def probe(self) -> Dict[str, Any]:
        try:
            import pythonosc  # noqa: F401
        except Exception:
            return {"available": False,
                    "detail": "python-osc not installed (pip install python-osc)"}
        target = f"{self.host()}:{self.port()}"
        if not self.enabled():
            return {"available": False,
                    "detail": f"armed at {target} but switched off "
                              "(Settings > Avatar > External avatar)"}
        # UDP cannot be probed without a handshake, so this says "armed", never
        # "connected" - see the class docstring.
        if self.acks:
            return {"available": True,
                    "detail": f"responding at {target} "
                              f"({self.sends} sent, {self.acks} acknowledged)"}
        return {"available": True,
                "detail": f"armed at {target} - {self.sends} sent, no reply yet "
                          f"(is VSeeFace running?)"}

    # -- sending ----------------------------------------------------------
    def _client_for(self):
        key = (self.host(), self.port())
        if self._client is not None and self._client_key == key:
            return self._client
        from pythonosc.udp_client import SimpleUDPClient

        self._client = SimpleUDPClient(key[0], key[1])
        self._client_key = key
        return self._client

    def _shape_vector(self, tag: str, intensity: float) -> List[float]:
        values = [0.0] * len(self.BLEND_ORDER)
        for name, amount in (self.EMOTION_SHAPES.get(tag) or {}).items():
            idx = self.INDEX.get(name)
            if idx is None:
                continue
            # intensity is 0..2; the shape range is 0..1, so a mood at full
            # intensity is a full shape and anything above 1 does not clip the
            # host into a broken pose.
            values[idx] = max(0.0, min(1.0, amount * (intensity / 2.0)))
        return values

    def _stamp(self) -> List[float]:
        """VMC time sync payload: [frame, seconds].

        Hosts use staleness to decide when to return the avatar to rest, so a
        constant zero would look like a stalled stream and get the mood dropped.
        Both fields are 32-bit floats on the wire, hence the wrap.
        """
        self._frame = (getattr(self, "_frame", 0) + 1) & 0xFFFFFFFF
        now = time.monotonic()
        start = getattr(self, "_t0", None)
        if start is None:
            self._t0 = now
        return [float(self._frame), float(now - self._t0)]

    def _flush(self) -> None:
        client = self._client_for()
        client.send_message(self.BLEND_VAL, list(self._values))
        client.send_message(self.APPLY, [True])
        client.send_message(self.TIME_SYNC, self._stamp())
        self.sends += 1
        self._dirty = False

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self._dirty:
                    self._flush()
                else:
                    # Keepalive. A VMC host that stops hearing /VMC/Ext/T
                    # returns the avatar to rest, so silence is not neutral.
                    self._client_for().send_message(self.TIME_SYNC, self._stamp())
            except Exception as exc:
                self.last_error = str(exc)
            self._stop.wait(self.KEEPALIVE_SECONDS)

    def _ensure_thread(self) -> None:
        with _lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="brahma-vmc", daemon=True)
            self._thread.start()

    def set_emotion(self, tag: str, **kw: Any) -> bool:
        self.last_reason = ""
        intensity = clamp_intensity(kw.get("intensity", get_intensity()))
        self.last = (tag, intensity)

        if not self.enabled():
            self.last_reason = "external avatar is switched off"
            return False
        try:
            self._values = self._shape_vector(tag, intensity)
            self._dirty = True
            self._ensure_thread()
            self.last_reason = ""
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self.last_reason = f"could not reach {self.host()}:{self.port()}: {exc}"
            return False

    def send_test(self) -> Dict[str, Any]:
        """Fire one packet now, synchronously, so a UI button can report truth.

        The background thread sends at most once a second; a user clicking
        "test" deserves an immediate answer rather than silence.
        """
        try:
            client = self._client_for()
            client.send_message(self.BLEND_VAL, list(self._values))
            client.send_message(self.APPLY, [True])
            client.send_message(self.TIME_SYNC, self._stamp())
            self.sends += 1
            self.last_error = ""
            return {"ok": True,
                    "detail": f"sent to {self.host()}:{self.port()} - "
                              f"UDP gives no receipt, so this proves it did not "
                              f"throw, not that VSeeFace heard it"}
        except Exception as exc:
            self.last_error = str(exc)
            return {"ok": False, "detail": str(exc)}

    def stop(self) -> None:
        """Stop the sender. Exists so tests and shutdown do not leak a thread."""
        self._stop.set()
        with _lock:
            self._thread = None

    def status(self) -> str:
        if not self.enabled():
            return "VMC: switched off"
        return (f"VMC: armed at {self.host()}:{self.port()} "
                f"({self.sends} sent, {self.acks} acknowledged)")