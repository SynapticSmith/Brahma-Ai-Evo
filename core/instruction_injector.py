"""
core/instruction_injector.py
Optional instruction blocks, attached per layer.

Why
---
Some instructions only matter to certain consumers. An avatar/emotion directive
is meaningless to a text-only tool planner, and a tool-schema note is noise to a
speech renderer. Appending everything to every prompt trains the model to ignore
the whole block.

So instructions are registered as named blocks that each declare which LAYERS
they apply to. A layer that does not opt in never receives the block, and
nothing is required for a layer that has no blocks at all.

Layers
------
live        Gemini Live session (speech, bidirectional)
local       local model turns through LocalBrain
fallback    the text fallback ladder
memory      background memory extraction
tool_plan   tool-calling / schema shaping

Every block is OFF unless registered. Registering is explicit and opt-in, and
`compose()` never raises: a broken block must not be able to break a live
session's prompt.

Wiring
------
main.py builds prompts in several places. Those call sites pass the layer name;
this module decides what actually gets included. Adding a new consumer is one
argument, not a new prompt-building path.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List

_lock = threading.Lock()

LAYERS = ("live", "local", "fallback", "memory", "tool_plan")

# name -> {"text": str, "layers": [...], "label": str, "builtin": bool}
_BLOCKS: Dict[str, Dict[str, Any]] = {}

# Blocks that ship with the app. All inert until something opts in via
# `is_layer_enabled` / the settings key below.
BUILTIN_BLOCKS: Dict[str, Dict[str, Any]] = {
    # Two INDEPENDENT avatar blocks rather than one combined one, so the user can
    # drive the visuals without the model changing how it speaks, steer tone
    # without any avatar at all, or run both at once.
    "avatar_emotion": {
        "label": "Avatar emotion tags (visuals only)",
        "text": (
            "When an avatar is active, you may prefix a reply with a single "
            "emotion tag in square brackets, for example [SMILE] or [THINKING]. "
            "Use one of these tags and nothing else: NEUTRAL, JOY, SMILE, FUN, "
            "LOVE, EXCITED, SURPRISE, CALM, THINKING, CONFUSED, SORROW, TIRED, "
            "ANGER. "
            "Emit at most one tag, and only when the emotion genuinely differs "
            "from your neutral tone. Omit the tag entirely for ordinary replies. "
            "The tag drives the avatar only; it does not change your wording."
        ),
        "layers": ["live", "fallback"],
        "default_on": False,
    },
    "avatar_tone": {
        "label": "Avatar tone steering (affects wording)",
        "text": (
            "Let your emotional state colour how you phrase things: a warmer "
            "moment may be slightly more open, a tense one more direct and brief. "
            "Keep this subtle. Never mention the emotional state itself, never "
            "announce a mood, and never let it change factual content."
        ),
        "layers": ["live", "fallback", "local"],
        "default_on": False,
    },
}


def register(name: str, text: str, layers: List[str], label: str = "") -> None:
    """Define a block. Layers must be from LAYERS. Re-registering replaces."""
    unknown = [layer for layer in layers if layer not in LAYERS]
    if unknown:
        raise ValueError(f"unknown layer(s): {unknown}; expected from {LAYERS}")
    if not text.strip():
        raise ValueError("instruction block text must not be empty")
    with _lock:
        _BLOCKS[name] = {
            "label": label or name.replace("_", " ").title(),
            "text": text.strip(),
            "layers": list(layers),
            "builtin": False,
        }


def unregister(name: str) -> None:
    with _lock:
        _BLOCKS.pop(name, None)


def load_builtins() -> None:
    for name, spec in BUILTIN_BLOCKS.items():
        with _lock:
            _BLOCKS[name] = {
                "label": spec["label"],
                "text": spec["text"],
                "layers": list(spec["layers"]),
                "builtin": True,
                "default_on": spec.get("default_on", False),
            }


def _setting(key: str, default: Any = None) -> Any:
    try:
        from memory import config_manager

        return config_manager.get_setting(key, default)
    except Exception:
        return default


def _store(key: str, value: Any) -> None:
    try:
        from memory import config_manager

        config_manager.set_setting(key, value)
    except Exception:
        pass


def is_block_enabled(name: str) -> bool:
    """Enabled flags live in settings so the user can switch each block off."""
    return bool(_setting(f"instruction_block_{name}", False))


def set_block_enabled(name: str, enabled: bool) -> None:
    _store(f"instruction_block_{name}", bool(enabled))


def is_layer_enabled(layer: str) -> bool:
    """A layer can opt out of the mechanism entirely.

    This is the "not required for the layers that don't use it" switch: a layer
    that is disabled contributes nothing and costs a single dict lookup.
    """
    if layer not in LAYERS:
        return False
    return bool(_setting(f"instruction_layer_{layer}", True))


def set_layer_enabled(layer: str, enabled: bool) -> None:
    if layer not in LAYERS:
        raise ValueError(f"unknown layer: {layer}")
    _store(f"instruction_layer_{layer}", bool(enabled))


def blocks() -> List[Dict[str, Any]]:
    """Catalogue for a settings UI."""
    out = []
    for name, spec in sorted(_BLOCKS.items()):
        out.append({
            "name": name,
            "label": spec["label"],
            "layers": list(spec["layers"]),
            "enabled": is_block_enabled(name),
            "builtin": spec["builtin"],
            "chars": len(spec["text"]),
        })
    return out


def compose(layer: str) -> str:
    """Instruction text for one layer. Empty string when nothing applies.

    Never raises: this runs on the prompt path of a live voice session, where an
    exception would cost the user their conversation.
    """
    try:
        if not is_layer_enabled(layer):
            return ""
        chunks = []
        with _lock:
            candidates = [
                (name, spec) for name, spec in _BLOCKS.items()
                if layer in spec["layers"] and is_block_enabled(name)
            ]
        for name, spec in sorted(candidates):
            try:
                chunks.append(f"[{spec['label']}]\n{spec['text']}")
            except Exception:
                continue
        if not chunks:
            return ""
        return "\n\n" + ("\n\n".join(chunks)) + "\n"
    except Exception:
        return ""


def stats() -> Dict[str, Any]:
    """Compact readback for tests and the settings card."""
    return {
        "registered": sorted(_BLOCKS),
        "enabled": sorted(n for n in _BLOCKS if is_block_enabled(n)),
        "layers": {
            layer: {
                "layer_enabled": is_layer_enabled(layer),
                "injected_chars": len(compose(layer)),
            }
            for layer in LAYERS
        },
    }


load_builtins()