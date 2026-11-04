
from core.user_paths import get_user_data_dir
from core.secret_store import load_api_keys
import os

from core.qt_bootstrap import bootstrap

# Qt/QtWebEngine runtime settings (Chromium flags + shared GL contexts) must be
# in place before any module pulls in QtWebEngineWidgets.
bootstrap()

from core import audio_devices
from core.echo import EchoGuard
from core.provider_failover import ProviderFailover
from memory import config_manager

from core import boot_sentry
boot_sentry.run()
from core.sensorium import sensorium
from core import desktop_tools
import asyncio
import threading
import re
import shutil
import socket
import subprocess
import sys
import time
import traceback
import pyperclip
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from google.genai import types
from ui import BrahmaUI
from memory.memory_manager import (
    update_memory, should_extract_memory, extract_memory, auto_learn_interaction
)

from actions.screen_processor  import screen_process  # noqa: F401
from actions.meeting_assistant import MeetingAssistant
from actions.attention_monitor import AttentionMonitor, speak_native, stop_native_speech, set_speech_sink  # noqa: F401
# from actions.daily_briefing import compile_daily_briefing
from workspace_store import store as workspace_store
from smart_home.service import SmartHomeService
from plugin_manager import PluginManager

try:
    from dashboard.server import DashboardServer
except Exception:
    DashboardServer = None

try:
    from actions.instagram_mcp import start_daemon as start_ig_daemon
except ImportError:
    try:
        from actions.instagram_chat import start_daemon as start_ig_daemon
    except ImportError:
        start_ig_daemon = None

try:
    from brahma_connect.service import get_service as get_brahma_connect_service
except Exception:
    get_brahma_connect_service = None


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = get_user_data_dir() / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
STARTUP_LOG     = Path(os.environ.get("LOCALAPPDATA", str(BASE_DIR))) / "Brahma Evo" / "startup.log"
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024
LIVE_CONNECT_TIMEOUT = 12


def _get_api_key() -> str:
    """Return the Gemini API key, or raise with an actionable message.
    Callers can catch RuntimeError if the config file or key is missing.
    """
    try:
        data = load_api_keys(API_CONFIG_PATH, must_exist=True)
    except FileNotFoundError:
        raise RuntimeError(
            f"Missing {API_CONFIG_PATH}. Add {{\"gemini_api_key\": \"...\"}} or switch "
            "the AI provider to Local/offline."
        ) from None
    except Exception as exc:
        raise RuntimeError(f"Could not read {API_CONFIG_PATH}: {exc}") from exc

    key = str((data or {}).get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError(
            f'No "gemini_api_key" in {API_CONFIG_PATH}. Set it, or switch the AI '
            "provider to Local/offline to run without Gemini."
        )
    return key


def _is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((host, port)) == 0


def _startup_log(message: str) -> None:
    try:
        STARTUP_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(STARTUP_LOG, "a", encoding="utf-8") as f:
            f.write(message + "\n")
    except Exception:
        pass


def _ensure_desktop_shortcut() -> None:
    if os.name != "nt":
        return

    marker_path = get_user_data_dir() / "config" / ".desktop_shortcut_created"
    if marker_path.exists():
        return

    try:
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
            desktop_raw, _ = winreg.QueryValueEx(key, "Desktop")
            winreg.CloseKey(key)
            desktop_dir = Path(os.path.expandvars(desktop_raw))
        except Exception:
            desktop_dir = Path(os.path.expanduser("~")) / "Desktop"
            
        desktop_dir.mkdir(parents=True, exist_ok=True)
        shortcut_path = desktop_dir / "Brahma Evo.lnk"
        script_path = BASE_DIR / "main.py"
        icon_path = BASE_DIR / "assets" / "Brahma_Lite_Logo.ico"

        if not icon_path.exists():
            icon_path = None

        python_exe = sys.executable
        if not python_exe:
            python_exe = shutil.which("python") or shutil.which("py") or "python"

        shortcut_target = python_exe
        shortcut_args = f'"{script_path}"'
        if getattr(sys, "frozen", False):
            shortcut_target = python_exe
            shortcut_args = ""

        powershell_exe = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell_exe is None:
            raise RuntimeError("PowerShell is not available")

        def _ps_escape(value: str) -> str:
            return value.replace("'", "''")

        icon_value = str(icon_path) if icon_path and icon_path.exists() else ""
        ps1_path = get_user_data_dir() / "config" / "create_desktop_shortcut.ps1"
        ps1_script = "\n".join([
            "$WshShell = New-Object -ComObject WScript.Shell",
            f"$Shortcut = $WshShell.CreateShortcut('{_ps_escape(str(shortcut_path))}')",
            f"$Shortcut.TargetPath = '{_ps_escape(shortcut_target)}'",
            f"$Shortcut.Arguments = '{_ps_escape(shortcut_args)}'",
            f"$Shortcut.WorkingDirectory = '{_ps_escape(str(BASE_DIR))}'",
            "$Shortcut.WindowStyle = 1",
            "$Shortcut.Description = 'Launch Brahma Evo'",
            f"if ('{_ps_escape(icon_value)}') {{ $Shortcut.IconLocation = '{_ps_escape(icon_value)},0' }}",
            "$Shortcut.Save()",
        ])
        ps1_path.write_text(ps1_script, encoding="utf-8")

        subprocess.run(
            [powershell_exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        marker_path.write_text("created", encoding="utf-8")
        _startup_log(f"desktop shortcut created at {shortcut_path}")
    except Exception as exc:
        _startup_log(f"desktop shortcut creation skipped: {exc}")


def _prompt_core_and_manual() -> tuple:
    """``(core, manual)`` for the two-tier prompt, with the legacy fallback.

    ``core/prompt.txt`` holds both halves separated by ``--- MANUAL ---``. A
    file predating the split (no marker) yields ``(whole_text, "")`` so an
    older prompt still boots; the resident core then keeps the old behaviour
    rather than losing the only copy of its instructions.
    """
    try:
        from core import prompt_parts

        core_prompt, manual = prompt_parts.load_prompt_tiers(PROMPT_PATH)
        if core_prompt:
            return core_prompt, manual
    except Exception:
        pass
    try:
        legacy = PROMPT_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        legacy = ""
    return legacy, ""


def _prompt_tier() -> str:
    """Resolve the prompt tier for this process from Settings.

    ``prompt_tier`` (``lite`` | ``full`` | ``auto``) decides whether the model
    gets just the resident core or the combined prompt; see
    :mod:`core.prompt_parts`. Failure to read settings must never break the
    prompt, so the lean tier is the fallback.
    """
    try:
        from core import config_manager
        from core import prompt_parts

        settings = config_manager.load_settings() or {}
        return prompt_parts.tier_for(settings, settings.get("local_ai_url", ""))
    except Exception:
        return "core"


def _load_system_prompt() -> str:
    try:
        core_prompt, manual = _prompt_core_and_manual()
        if _prompt_tier() == "full":
            base_prompt = f"{core_prompt}\n\n{manual}" if manual else core_prompt
        else:
            base_prompt = core_prompt
        if not base_prompt:
            raise ValueError("empty prompt file")
    except Exception:
        base_prompt = (
            "You are Brahma Evo, a calm, direct, and professional AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool. "
            "If the user asks to create, build, launch, or open a website, always use the selected workspace folder."
        )
        
    try:
        from core.identity import identity
        ast_name = identity.get_assistant_name() or "Brahma Evo"
        own_name = identity.get_owner_name() or "the user"
        role = identity.get_owner_role()
        mode = identity.get_behavior_mode()
        
        identity_str = f"You are {ast_name}. You are assisting {own_name}"
        if role:
            identity_str += f" (Role: {role}).\n"
        else:
            identity_str += ".\n"
            
        directive = identity.get_behavior_directive(mode)
        identity_str += f"Your current behavior mode is: {mode}. Directive: {directive}\n"
        
        custom = identity.get_custom_instructions()
        if custom:
            identity_str += f"Custom Instructions: {custom}\n\n"

        # Inject continuously learned rules and behavioral directives
        try:
            from core.learned_rules import LearnedRulesEngine
            learned_directives = LearnedRulesEngine.get_prompt_injections()
            if learned_directives:
                identity_str += f"{learned_directives}\n\n"
        except Exception as e_rules:
            print(f"[LearnedRules] Error injecting rules into prompt: {e_rules}")

        return identity_str + base_prompt
    except Exception as e:
        print(f"Error injecting identity: {e}")
        try:
            from core.learned_rules import LearnedRulesEngine
            learned_directives = LearnedRulesEngine.get_prompt_injections()
            if learned_directives:
                return f"{learned_directives}\n\n" + base_prompt
        except Exception:
            pass
        return base_prompt


def _speak_daily_briefing(ui=None, speak=None) -> None:
    try:
        from actions.daily_briefing import compile_unified_briefing
        data, narrative = compile_unified_briefing()
        if ui:
            ui.show_daily_briefing(data)
            ui.write_log(f"Brahma Evo: {narrative}")
        (speak or speak_native)(narrative)
    except Exception as e:
        print(f"[DailyBriefing] Error: {e}")
    
def _extract_gemini_text(response) -> str:
    text_parts: list[str] = []
    try:
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", []) or []:
                part_text = getattr(part, "text", None)
                if part_text:
                    text_parts.append(part_text)
    except Exception:
        pass

    text = "".join(text_parts).strip()
    if text:
        return text

    try:
        return (getattr(response, "text", "") or "").strip()
    except Exception:
        return ""


def _injected_instructions(layer: str) -> str:
    """Instruction text for a prompt layer, or "" when nothing applies.

    Wrapped so every caller stays one line and no prompt path can be broken by
    the injector. The `live` and `fallback` layers were declared but were never
    composed, which is why Gemini Live reported receiving no avatar
    instructions at all.
    """
    try:
        from core import instruction_injector

        return instruction_injector.compose(layer) or ""
    except Exception:
        return ""


def _gemini_text_reply(prompt: str, layer: str = "fallback") -> str:
    from core import llm_gateway

    system_prompt = (
        "You are Brahma Evo, a concise, helpful desktop assistant. "
        "Reply naturally and briefly. Do not mention internal implementation details."
    )
    extra = _injected_instructions(layer)
    if extra:
        system_prompt = f"{system_prompt}{extra}"
    # Grounded to the Gemini link only: the reply ladder owns failover and
    # must see a real failure (not another provider's voice) when Gemini is
    # disabled or throttled. GatewayError carries the SDK's reason verbatim,
    # so the ladder's rate-limit detection keeps working; call sites outside
    # the ladder (ReplyFlow) already catch and fall back themselves.
    response = llm_gateway.generate_content(
        f"{system_prompt}\n\nUser: {prompt}",
        model="gemini-2.5-flash",
        config={"temperature": 0.6},
        links=("gemini",),
        purpose="text reply",
    )
    return _extract_gemini_text(response)


from core.reply_flow import ig_gemini_reply as _ig_gemini_reply


def _clipboard_gemini_reply(text: str) -> str:
    system_prompt = (
        "You are Brahma Evo, a witty and helpful AI assistant. "
        "The user just copied the following text to their clipboard. "
        "Make a very short, interesting, or helpful 1-sentence comment or question about it. "
        "Do not offer to 'help' or ask 'how can I help'. Just make a standalone witty observation or summary."
    )
    prompt = text
    try:
        from core import llm_gateway

        response = llm_gateway.generate_content(
            f"{system_prompt}\n\nClipboard Text: {prompt}",
            model="gemini-2.5-flash",
            config={"temperature": 0.8},
            purpose="clipboard comment",
        )
        return _extract_gemini_text(response)
    except Exception as e:
        print(f"[Clipboard] Reply generation failed: {e}")
        return "Interesting stuff you copied there!"


# Provider id -> the name shown in Settings / the UI. Every failure line the
# reply ladder logs uses these so log text matches what the user configured.
PROVIDER_LABELS = {
    "gemini": "Google Gemini",
    "openrouter": "OpenRouter",
    "local": "Local model",
    "custom": "Custom endpoint",
}


def _provider_id_for_name(name: str) -> str | None:
    """Map the Settings value of ``default_ai_provider`` to a provider_router id.

    Settings stores display names ("Gemini", "OpenRouter", "Local", "Custom
    (OpenAI-compatible)") while the router keys on ids; the reply ladder needs
    the id to place the selected provider first in its attempt order. Returns
    None for an unrecognised value so callers fall back to plain router order.
    """
    low = (name or "").strip().lower()
    if low in ("gemini", "google gemini"):
        return "gemini"
    if low == "openrouter":
        return "openrouter"
    if low == "local":
        return "local"
    if low.startswith("custom"):
        return "custom"
    return None


def _looks_like_code_request(text: str) -> bool:
    low = (text or "").lower()
    code_words = (
        "build", "create", "write", "implement", "code", "python", "app",
        "module", "function", "class", "project", "script", "api",
        "ui", "webpage", "bot", "server", "service"
    )
    return any(word in low for word in code_words)


def _looks_like_website_request(text: str) -> bool:
    low = (text or "").lower()
    website_words = (
        "website",
        "web site",
        "webpage",
        "web page",
        "landing page",
        "homepage",
        "home page",
        "portfolio",
        "product site",
        "business site",
        "marketing site",
        "web app",
        "frontend",
        "site",
        "html",
        "react",
        "web",
    )
    action_words = ("make", "create", "build", "design", "develop", "generate", "code", "edit", "update", "fix")
    has_web = any(re.search(rf"\b{re.escape(w)}\b", low) for w in website_words)
    has_action = any(re.search(rf"\b{re.escape(a)}\b", low) for a in action_words)
    return has_web and (has_action or any(w in low for w in ("landing page", "homepage", "portfolio", "website", "web app", "web page")))


def _looks_like_presentation_request(text: str) -> bool:
    low = (text or "").lower()
    ppt_keywords = (
        "presentation", "powerpoint", "slideshow", "slides", "slide deck",
        "pitch deck", "deck", "ppt", "pptx"
    )
    action_words = ("make", "create", "build", "design", "develop", "generate", "draft", "prepare")
    has_keyword = any(re.search(rf"\b{re.escape(k)}\b", low) for k in ppt_keywords)
    has_action = any(re.search(rf"\b{re.escape(a)}\b", low) for a in action_words)
    return has_keyword and (has_action or any(k in low for k in ("slide deck", "pitch deck", "powerpoint", "pptx", "ppt")))


def _looks_like_spreadsheet_request(text: str) -> bool:
    low = (text or "").lower()
    sheet_keywords = (
        "spreadsheet", "excel", "sheet", "sheets", "workbook", "xlsx",
        "tracker", "expense tracker", "budget sheet"
    )
    action_words = ("make", "create", "build", "design", "develop", "generate", "draft", "prepare")
    has_keyword = any(re.search(rf"\b{re.escape(k)}\b", low) for k in sheet_keywords)
    has_action = any(re.search(rf"\b{re.escape(a)}\b", low) for a in action_words)
    return has_keyword and (has_action or any(k in low for k in ("spreadsheet", "excel sheet", "expense tracker", "budget sheet", "xlsx")))


def _extract_skill_creation_goal(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    match = re.match(
        r"^(?:please\s+)?(?:(?:i\s+(?:want|need|would like)\s+you\s+to|(?:can|could)\s+you)\s+)?"
        r"(?:make|create|build|develop|generate|forge|write|implement|add|design)\s+"
        r"(?:me\s+)?(?:your\s+)?(?:(?:a|an|new|custom|own)\s+)*(?:skill|feature)\b(.*)$",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    goal = match.group(1).strip(" \t:,-")
    goal = re.sub(r"^(?:that|which|to)\s+", "", goal, flags=re.IGNORECASE)
    goal = re.sub(
        r"^(?:(?:will|can|could|should)\s+)?(?:allow|let|enable|allows|lets|enables)\s+(?:you\s+)?(?:to\s+)?",
        "",
        goal,
        flags=re.IGNORECASE,
    )
    return goal.strip()


def _is_gemini_limit_error(exc: Exception) -> bool:
    """True only for genuine rate-limit / quota exhaustion.

    Deliberately narrow: the caller uses this to fail over to OpenRouter, so an
    auth error ("permission denied", "access denied") or an unrelated numeric
    code ("1008" appears in timestamps and request ids) must NOT be mistaken for
    a throttle, or a bad key silently disables Gemini.
    """
    msg = str(exc).lower()
    return any(token in msg for token in (
        "429",
        "resource_exhausted",
        "resource has been exhausted",
        "quota",
        "rate limit",
        "too many requests",
        "exceeded",
    ))


class _LiveReconnect(Exception):
    """Raised to ask run() to rebuild the live session (e.g. after a GoAway)."""


def _is_transient_live_error(exc: BaseException) -> bool:
    """True for a dropped live link that should be retried quietly.

    A Gemini Live socket ends with a bare ``1006`` whenever the network path
    hiccups: a Proactor recv raising ``WinError 121`` (semaphore timeout) over a
    VPN, a WebSocket cut without a close frame, or a DNS blip during the opening
    handshake. None of those mean the app is broken, so they must not dump a
    full ExceptionGroup traceback and must not be treated as a hard failure.
    """
    seen = set()
    stack = [exc]
    while stack:
        cur = stack.pop()
        if cur is None or id(cur) in seen:
            continue
        seen.add(id(cur))
        if isinstance(cur, _LiveReconnect):
            return True
        text = f"{type(cur).__name__} {cur}".lower()
        if any(token in text for token in (
            "1006",
            "abnormal closure",
            "connectionclosed",
            "no close frame",
            "winerror 121",
            "semaphore timeout",
            "timed out during opening handshake",
            "getaddrinfo",
            "connection reset",
            "connection aborted",
            "server disconnected",
        )):
            return True
        nested = getattr(cur, "exceptions", None)
        if isinstance(nested, (list, tuple)):
            stack.extend(nested)
        cause = getattr(exc, "__cause__", None) or getattr(cur, "__context__", None)
        if cause is not None:
            stack.append(cause)
    return False


def _live_error_reason(exc: Exception) -> str:
    """A one-line, log-friendly reason for a dropped live link."""
    cur = exc.exceptions[0] if getattr(exc, "exceptions", None) else exc
    if isinstance(cur, _LiveReconnect):
        return str(cur) or "server requested reconnect"
    return f"{type(cur).__name__}: {cur}".strip()[:160] or type(cur).__name__


def _looks_like_screen_request(text: str) -> bool:
    t = (text or "").lower()
    if not t:
        return False
    direct_phrases = (
        "what's on my screen",
        "whats on my screen",
        "what is on my screen",
        "what's on screen",
        "check my screen",
        "look at my screen",
        "analyze my screen",
        "analyse my screen",
        "tell me what's on my screen",
        "tell me what is on my screen",
        "read my screen",
        "what does my screen say",
        "explain this error",
        "what's this error",
        "what is this error",
        "explain the error",
        "look at this error",
        "explain what's on my screen",
        "inspect my screen",
        "what am i looking at",
    )
    if any(p in t for p in direct_phrases):
        return True
    screen_words = ("screen", "display", "monitor", "window")
    request_words = ("what", "check", "look", "analy", "analyse", "analyze", "read", "tell", "answer", "see", "explain")
    has_screen_target = any(re.search(rf"\b{re.escape(word)}\b", t) for word in screen_words)
    has_screen_request = any(re.search(rf"\b{re.escape(word)}\b", t) for word in request_words)
    return has_screen_target and has_screen_request


def _looks_like_daily_briefing_request(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").lower()).strip()
    phrases = (
        "daily briefing", "morning briefing", "morning update", "daily update",
        "brief me", "give me a briefing", "briefing for today", "what's happening today",
        "whats happening today", "what is happening today",
    )
    return any(phrase in normalized for phrase in phrases)


def _wakeword_detected(text: str) -> bool:
    t = re.sub(r"[^a-z0-9\s]+", " ", (text or "").lower())
    words = [w for w in t.split() if w]
    if not words:
        return False
    phrases = (
        "brahma evo",
        "hey brahma evo",
        "hi brahma evo",
        "hello brahma evo",
        "hey",
        "hi",
        "hello",
    )
    compact = " ".join(words)
    if compact in phrases or any(p in compact for p in phrases):
        return True
    return any(word in {"brahma evo", "hey", "hi", "hello"} for word in words)


def _standby_wakeword_detected(text: str) -> bool:
    """Strict wake match for On Call standby.

    _wakeword_detected() also fires on a bare "hey"/"hi"/"hello". That is
    harmless in-session, where the socket is already up and the wake word only
    serves to unmute. As a standby auto-connect trigger it is a false-positive
    machine: the microphone is listening to an idle room, so any passing
    "hi" would open a Gemini session and burn quota. Standby therefore
    requires the product name.
    """
    t = re.sub(r"[^a-z0-9\s]+", " ", (text or "").lower())
    words = [w for w in t.split() if w]
    if not words:
        return False
    return "brahma" in words


def _build_task_plan(text: str) -> list[str]:
    t = (text or "").lower()
    if any(word in t for word in ("presentation", "ppt", "slides", "deck")):
        return [
            "Understand the topic and goal",
            "Build a slide structure",
            "Generate and format the deck",
            "Open the finished presentation",
        ]
    if any(word in t for word in ("spreadsheet", "excel", "sheet", "table", "tracker", "budget")):
        return [
            "Read the data request",
            "Lay out sheets and columns",
            "Apply formulas and formatting",
            "Open the workbook",
        ]
    if any(word in t for word in ("word", "docx", "document", "report", "letter")):
        return [
            "Understand the document type",
            "Draft the structure and content",
            "Preserve formatting and polish",
            "Save the editable file",
        ]
    if any(word in t for word in ("website", "web site", "landing page", "saaS", "saas", "dashboard", "app")):
        return [
            "Interpret the brief",
            "Generate frontend and backend files",
            "Launch the local preview",
            "Debug and fix launch issues if needed",
        ]
    if any(word in t for word in ("browser", "website", "google", "search", "open url", "navigate")):
        return [
            "Open the browser",
            "Navigate to the target page",
            "Collect the needed information",
            "Return the result",
        ]
    if any(word in t for word in ("screen", "camera", "meeting", "call", "analyze", "analyse", "analyze")):
        return [
            "Capture the live screen or camera",
            "Inspect what is visible",
            "Answer with the important details",
            "Keep listening for follow-up commands",
        ]
    if any(word in t for word in ("fan", "light", "plug", "kasa", "atomberg", "smart home", "home device", "room", "bedroom", "living room", "kitchen", "office", "bathroom", "balcony")):
        return [
            "Identify the smart-home device or room",
            "Choose the correct action",
            "Send the command to the connected provider",
            "Confirm the result back to the user",
        ]
    return [
        "Understand the command",
        "Choose the right tool",
        "Execute the task",
        "Return the result",
    ]


_last_memory_input = ""

def _update_memory_async(user_text: str, brahma_text: str) -> None:
    global _last_memory_input

    user_text   = (user_text   or "").strip()
    brahma_text = (brahma_text or "").strip()

    if len(user_text) < 4 or user_text == _last_memory_input:
        return
    _last_memory_input = user_text

    # Fast deterministic heuristic extraction (Pillar 5 - Living Knowledge Graph)
    try:
        learned = auto_learn_interaction(user_text, brahma_text)
        if learned:
            print(f"[Memory] 🧠 Auto-learned: {list(learned.keys())}")
    except Exception as exc:
        print(f"[Memory] ⚠️ Auto-learn error: {exc}")

    try:
        # Background memory extraction toggle. This is the ONLY thing that gates
        # extraction: it is deliberately NOT keyed on offline mode or the selected
        # provider, because running locally is exactly when you still want the
        # memory graph built. Extraction stays connected; the user decides whether
        # to spend the CPU on it.
        if not config_manager.load_settings().get("memory_extraction_enabled", True):
            return
    except Exception:
        pass

    try:
        api_key = _get_api_key()
        if not should_extract_memory(user_text, brahma_text, api_key):
            return
        data = extract_memory(user_text, brahma_text, api_key)
        if data:
            update_memory(data)
            print(f"[Memory] ✅ {list(data.keys())}")
    except Exception as e:
        if "429" not in str(e):
            print(f"[Memory] ⚠️ {e}")

def _memory_context_for_request(text: str) -> str:
    try:
        return workspace_store().memory_context(text, limit=5)
    except Exception:
        return ""


TOOL_DECLARATIONS = [
    {
        "name": "undo",
        "description": (
            "Roll back the last change made to the computer — reversing a file move, "
            "copy, create, or edit, a desktop organization, a volume or brightness change, "
            "or a Wi-Fi toggle. Call this whenever the user says undo, revert, take it back, "
            "put it back, restore, or says they made a mistake. "
            "Use action='list' when they ask what can be undone."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "undo (default) — reverse the last change | list — show what can be undone",
                },
            },
        },
    },
    {
        "name": "recall_memory",
        "description": (
            "Search long-term memory for stored facts, preferences, user info, projects, or history. "
            "Use this when the user asks 'do you remember', 'what is my...', or when context about "
            "past instructions or preferences is needed."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "Keyword or topic to search for in memory (e.g. 'project', 'coffee', 'birthday', 'preference')",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "execute_protocol",
        "description": (
            "Activates or switches high-level macro system directives (protocols) across the PC. "
            "Call this whenever the user asks to activate, engage, start, run, or switch to a protocol: "
            "'deep_work' (deep focus mode), 'redline' (gaming / max power performance), "
            "'lockdown' (instant lock and security mute), or 'nightfall' (evening standby wrap-up)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "protocol": {
                    "type": "STRING",
                    "description": "Name of the protocol: 'deep_work', 'redline', 'lockdown', 'nightfall'",
                },
            },
            "required": ["protocol"],
        },
    },
    {
        "name": "get_sensorium_telemetry",
        "description": (
            "Read real-time situational awareness telemetry: current active application, "
            "window title, task dwell time in seconds, user idle time, CPU/RAM load, and battery health."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        },
    },
    {
        "name": "manage_local_model",
        "description": (
            "Inspect, download, or switch offline local LLM models on the machine. "
            "action='status' returns available models and server health. "
            "action='pull' downloads a new model (e.g., 'qwen2.5:3b', 'llama3.2:3b'). "
            "action='toggle_offline' switches between local offline inference and cloud."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "One of: 'status', 'pull', 'toggle_offline', 'set_active'",
                },
                "model_name": {
                    "type": "STRING",
                    "description": "Model name when pulling or setting active (e.g. 'qwen2.5:3b', 'llama3.2:3b')",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "dev_agent",
        "description": (
            "An autonomous coding agent that builds full projects, writes code, installs dependencies, "
            "runs the project, and automatically fixes errors. Use this when the user asks you to 'write a script', "
            "'build an app', 'code a program', or 'run a project'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description": {
                    "type": "STRING",
                    "description": "A very detailed description of what the project should do."
                },
                "language": {
                    "type": "STRING",
                    "description": "The programming language to use (e.g., 'python', 'javascript')"
                },
                "project_name": {
                    "type": "STRING",
                    "description": "A short, snake_case name for the project folder."
                }
            },
            "required": ["description"]
        }
    },
    {
        "name": "background_monitor",
        "description": (
            "Sets up a background monitor to check crypto prices, system RAM/CPU, or website uptime. "
            "Use this when the user asks to be alerted when a condition is met (e.g., 'tell me if RAM goes over 90%' or 'alert me if bitcoin drops below 50000')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "'add' to create a monitor (default), 'list' to see active monitors."
                },
                "type": {
                    "type": "STRING",
                    "description": "One of: 'system', 'crypto', 'website'"
                },
                "target": {
                    "type": "STRING",
                    "description": "What to monitor (e.g. 'ram', 'cpu', 'bitcoin', 'https://example.com')"
                },
                "threshold": {
                    "type": "NUMBER",
                    "description": "The threshold value (e.g. 90 for 90%, 50000 for $50k)"
                },
                "condition": {
                    "type": "STRING",
                    "description": "'above' or 'below'"
                },
                "interval": {
                    "type": "INTEGER",
                    "description": "How often to check in seconds (default 60)"
                }
            },
            "required": []
        }
    },
    {
        "name": "system_manager",
        "description": (
            "Checks the system health (CPU, RAM, disk, battery) and lists top resource-hogging apps. "
            "Can also be used to forcefully close or kill frozen or heavy applications."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "What to do: 'status' to check system health (default), or 'kill' to close an app."
                },
                "process_name": {
                    "type": "STRING",
                    "description": "The exact name of the process to kill (if action is 'kill'), e.g. 'chrome.exe' or 'Spotify'"
                },
                "pid": {
                    "type": "INTEGER",
                    "description": "The PID of the process to kill (if action is 'kill')"
                }
            },
            "required": []
        }
    },
    {
        "name": "check_instagram_messages",
        "description": (
            "Checks your Instagram inbox for any recent unread or direct messages. "
            "Use this when the user asks 'do I have any messages', 'check my instagram', or similar."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "clipboard_processor",
        "description": (
            "Instantly reads the current text copied to the user's Windows clipboard. "
            "Use this whenever the user asks you to read, analyze, or fix what they just copied to their clipboard."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
       "name": "instagram_reply",
       "description": "Replies to a pending Instagram message or takes over the Instagram chat in auto-mode.",
       "parameters": {
           "type": "OBJECT",
           "properties": {
               "action": {
                   "type": "STRING",
                   "description": "Must be 'take_over' to handle it automatically, or 'manual_reply' to send a specific text message."
               },
               "reply_text": {
                   "type": "STRING",
                   "description": "The exact message to send to the user if action is 'manual_reply'."
               }
           },
           "required": ["action"]
       }
    },
    {
       "name": "instagram_send_dm",
       "description": "Sends an Instagram direct message to a username or conversation thread, and automatically opens the chat in your browser.",
       "parameters": {
           "type": "OBJECT",
           "properties": {
               "recipient": {
                   "type": "STRING",
                   "description": "The Instagram username (e.g. 'john_doe') or numeric thread ID."
               },
               "message": {
                   "type": "STRING",
                   "description": "The message text to send."
               },
               "open_in_browser": {
                   "type": "BOOLEAN",
                   "description": "Whether to auto-open the chat thread in your browser (default: true)."
               }
           },
           "required": ["recipient", "message"]
       }
    },
    {
       "name": "instagram_post_photo",
       "description": "Publishes a photo directly to your Instagram feed and automatically opens the live post in your browser.",
       "parameters": {
           "type": "OBJECT",
           "properties": {
               "image_path": {
                   "type": "STRING",
                   "description": "Absolute path to the JPG or PNG image file to publish."
               },
               "caption": {
                   "type": "STRING",
                   "description": "The caption text with optional hashtags for the post."
               },
               "open_in_browser": {
                   "type": "BOOLEAN",
                   "description": "Whether to auto-open the published post in your browser (default: true)."
               }
           },
           "required": ["image_path"]
       }
    },
    {
       "name": "instagram_post_reel",
       "description": "Publishes a video or Reel directly to your Instagram account and automatically opens the live Reel in your browser.",
       "parameters": {
           "type": "OBJECT",
           "properties": {
               "video_path": {
                   "type": "STRING",
                   "description": "Absolute path to the MP4 video file to publish."
               },
               "caption": {
                   "type": "STRING",
                   "description": "The caption text with optional hashtags."
               },
               "thumbnail_path": {
                   "type": "STRING",
                   "description": "Optional path to cover image."
               },
               "open_in_browser": {
                   "type": "BOOLEAN",
                   "description": "Whether to auto-open the published Reel in your browser (default: true)."
               }
           },
           "required": ["video_path"]
       }
    },
    {
       "name": "instagram_get_user_info",
       "description": "Looks up profile details, bio, follower count, following count, and verified badge for any Instagram handle.",
       "parameters": {
           "type": "OBJECT",
           "properties": {
               "username": {
                   "type": "STRING",
                   "description": "The Instagram username to look up (e.g. 'natgeo' or 'openai')."
               }
           },
           "required": ["username"]
       }
    },
    {
        "name": "open_app",
        "description": (
            "Opens any application on the Windows computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": "Searches the web for any information.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "weather_report",
        "description": (
            "Fetches live real-time weather, forecast, and temperature for any city or location. "
            "Call this whenever the user asks about the weather, temperature, rain, or forecast for any city or location."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, Instagram DMs, or other messaging platform. Can also upload media to Instagram when mode=upload and media_path is supplied.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name for DMs"},
                "message_text": {"type": "STRING", "description": "The message to send or Instagram caption"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, Instagram, etc."},
                "mode":         {"type": "STRING", "description": "dm | upload (Instagram only; default: dm)"},
                "media_path":   {"type": "STRING", "description": "Optional image/video path for Instagram uploads"}
            },
            "required": ["platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Windows Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool. "
            "After calling this tool, stay SILENT — the vision module speaks directly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."}
            },
            "required": []
        }
    },
    {
        "name": "smart_home_control",
        "description": (
            "Controls connected smart-home devices such as Atomberg fans and TP-Link Kasa lights/plugs. "
            "Use when the user asks to turn devices on or off, set fan speed, change brightness, or control a room."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "command": {"type": "STRING", "description": "Natural language smart-home command"}
            },
            "required": ["command"]
        }
    },
    {
        "name": "connect_list_devices",
        "description": (
            "Lists devices connected to Brahma Connect. Use when the user asks what devices are connected, "
            "what is online, or wants a simple inventory of paired devices."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "connect_get_device",
        "description": (
            "Gets the details for one connected device by name, id, or natural reference such as my phone, "
            "my laptop, my PC, or my tablet."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "device": {"type": "STRING", "description": "Device name, id, or natural reference"},
                "target": {"type": "STRING", "description": "Alias for device"},
                "device_id": {"type": "STRING", "description": "Exact device id"},
                "name": {"type": "STRING", "description": "Exact device name"},
                "query": {"type": "STRING", "description": "Search query"},
            },
            "required": ["device"]
        }
    },
    {
        "name": "connect_get_capabilities",
        "description": (
            "Returns the capabilities and permissions reported by a connected device. "
            "Use before trying any device command."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "device": {"type": "STRING", "description": "Device name, id, or natural reference"},
                "target": {"type": "STRING", "description": "Alias for device"},
                "device_id": {"type": "STRING", "description": "Exact device id"},
                "name": {"type": "STRING", "description": "Exact device name"},
                "query": {"type": "STRING", "description": "Search query"},
            },
            "required": ["device"]
        }
    },
    {
        "name": "connect_execute",
        "description": (
            "Routes a Brahma Connect command to a paired device through the gateway. "
            "Use for actions such as launch_app, open_url, get_battery, capture_screen, take_photo, "
            "clipboard_get, clipboard_set, send_file, receive_file, media_play, media_pause, volume_set, "
            "notification_list, get_device_info, close_app, mouse_move, keyboard_type, unlock_phone, file_list, file_read, file_write, file_delete."
            "Do not execute device operations directly anywhere else."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "device": {"type": "STRING", "description": "Target device name, id, or natural reference"},
                "target": {"type": "STRING", "description": "Alias for device"},
                "device_id": {"type": "STRING", "description": "Exact device id"},
                "name": {"type": "STRING", "description": "Exact device name"},
                "query": {"type": "STRING", "description": "Search query"},
                "action": {"type": "STRING", "description": "Command to execute on the device"},
                "parameters": {"type": "OBJECT", "description": "Action parameters"},
            },
            "required": ["device", "action"]
        }
    },
    {
        "name": "unlock_device",
        "description": "Unlocks a paired Android device using its saved PIN.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "target": {"type": "STRING", "description": "Device name or ID to unlock"}
            },
            "required": ["target"]
        }
    },
    {
        "name": "connect_pair_device",
        "description": (
            "Creates or approves Brahma Connect pairing. Use to generate a QR code / pairing code for a new device, "
            "or to approve a pending pairing request."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "device_name": {"type": "STRING", "description": "Optional device name shown in the pairing flow"},
                "platform": {"type": "STRING", "description": "android | windows | ios | tablet | other"},
                "pending_id": {"type": "STRING", "description": "Pending request id to approve"},
            },
            "required": []
        }
    },
    {
        "name": "connect_disconnect_device",
        "description": (
            "Disconnects a device from Brahma Connect and marks it offline. "
            "Use when the user asks to disconnect, log out, or stop a paired device."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "device": {"type": "STRING", "description": "Target device name, id, or natural reference"},
                "target": {"type": "STRING", "description": "Alias for device"},
                "device_id": {"type": "STRING", "description": "Exact device id"},
                "name": {"type": "STRING", "description": "Exact device name"},
                "query": {"type": "STRING", "description": "Search query"},
                "reason": {"type": "STRING", "description": "Optional reason for disconnect"},
            },
            "required": ["device"]
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Complete browser automation powered by Microsoft Playwright MCP. "
            "Use for: opening websites, web searching, navigating, semantic accessibility snapshots, "
            "clicking elements or snapshot references, typing, form filling, hovering, executing JavaScript, "
            "taking screenshots, managing tabs, and any web-based task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | navigate | search | click | hover | type | scroll | fill_form | snapshot | find | evaluate | screenshot | press | back | forward | refresh | open_tab | new_tab | switch_tab | list_tabs | wait_for | select_option | upload | console | network | close"},
                "url":         {"type": "STRING", "description": "URL for go_to/navigate"},
                "query":       {"type": "STRING", "description": "Search query or search term for find"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type/hover"},
                "element":     {"type": "STRING", "description": "Snapshot element reference (e.g. e2)"},
                "text":        {"type": "STRING", "description": "Text to click, type, or wait for"},
                "description": {"type": "STRING", "description": "Element description for smart targeting"},
                "direction":   {"type": "STRING", "description": "up or down for scroll"},
                "key":         {"type": "STRING", "description": "Key name for press action (Enter, Tab, Escape, etc.)"},
                "expression":  {"type": "STRING", "description": "JavaScript expression to evaluate in page"},
                "path":        {"type": "STRING", "description": "File path for screenshot or upload"},
                "tab":         {"type": "INTEGER", "description": "1-based tab index for switch_tab"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": (
            "Manages files and folders: open, close, list, create, delete, move, copy, rename, read, write, find, disk usage, "
            "and organizing a desktop or any folder into subfolders by type/date. Can also be used to explore and manage files on a connected Android phone."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "open | close | list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | organize_folder | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home. For android, use paths like downloads, documents, photos, movies, root."},
                "target":      {"type": "STRING", "description": "If operating on an Android device, provide the device name or ID. Leave empty for PC local files."},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
                "mode":        {"type": "STRING", "description": "by_type or by_date for organize actions"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "smart_organizer",
        "description": (
            "Smart Desktop & Downloads Organizer MCP with safe transaction rollback (undo), "
            "dry-run previews, duplicate file detection, empty folder cleanup, and archiving. "
            "Use when the user asks to organize, preview, declutter, clean, or find duplicate files on the desktop, downloads, or any folder."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "preview | organize | undo | find_duplicates | clean_empty_folders | archive_old"},
                "target": {"type": "STRING", "description": "Target folder: 'desktop' | 'downloads' | 'documents' | 'pictures' or a custom directory path"},
                "mode":   {"type": "STRING", "description": "by_type (categorizes into Documents, Images/Screenshots, Installers, Code, Archives, etc.) or by_date (YYYY-MM)"},
                "days":   {"type": "INTEGER", "description": "Number of days for archive_old (default: 30)"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, preview, clean, undo, find_duplicates, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | preview | clean | undo | find_duplicates | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use browser_control or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "file_processor",
        "description": (
            "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
            "text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
            "ALWAYS call this tool when a non-Word file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx via word_document; txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
        }
    },
    {
        "name": "presentation_builder",
        "description": (
            "Creates editable PowerPoint presentations (.pptx) from a structured slide outline. "
            "Brahma Evo automatically infers the best visual style from the topic, searches for a matching online template when available, "
            "reuses cached templates, and falls back to the built-in designer if no suitable template is found. "
            "Use when the user asks for a deck, slideshow, presentation, pitch deck, or report slides."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING", "description": "Presentation title"},
                "subtitle": {"type": "STRING", "description": "Optional subtitle or audience line"},
                "theme": {
                    "type": "STRING",
                    "description": "Optional presentation theme or visual direction such as neon, corporate, luxury, academic, sunset, or creative. If omitted, Brahma Evo infers the best style automatically."
                },
                "outline": {
                    "type": "STRING",
                    "description": "Slide-by-slide outline. Use blank lines to separate slides if slides array is omitted."
                },
                "slides": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "title": {"type": "STRING", "description": "Slide title"},
                            "kicker": {"type": "STRING", "description": "Short all-caps kicker"},
                            "bullets": {
                                "type": "ARRAY",
                                "items": {"type": "STRING"},
                                "description": "Bullet points for the slide"
                            },
                            "notes": {"type": "STRING", "description": "Optional speaker note or footnote"}
                        },
                        "required": ["title"]
                    },
                    "description": "Structured slides. Preferred when the model can format the deck directly."
                },
                "output_path": {"type": "STRING", "description": "Optional output path for the .pptx"},
                "auto_open": {"type": "BOOLEAN", "description": "Open the file after creating it (default: true)"},
            },
            "required": ["title"]
        }
    },
    {
        "name": "spreadsheet_builder",
        "description": (
            "Creates editable Excel workbooks (.xlsx) from structured sheet data. "
            "Use for trackers, tables, analysis workbooks, budgets, planners, and other spreadsheet requests."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING", "description": "Workbook title"},
                "worksheets": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "name": {"type": "STRING", "description": "Worksheet name"},
                            "title": {"type": "STRING", "description": "Optional sheet title row"},
                            "headers": {
                                "type": "ARRAY",
                                "items": {"type": "STRING"},
                                "description": "Column headers"
                            },
                            "rows": {
                                "type": "ARRAY",
                                "items": {
                                    "type": "ARRAY",
                                    "items": {"type": "STRING"},
                                },
                                "description": "Data rows"
                            },
                            "chart": {
                                "type": "OBJECT",
                                "properties": {
                                    "type": {"type": "STRING", "description": "bar | line | pie"},
                                    "title": {"type": "STRING", "description": "Chart title"},
                                    "anchor": {"type": "STRING", "description": "Cell anchor such as E2"},
                                    "x_axis": {"type": "STRING", "description": "Optional x-axis title"},
                                    "y_axis": {"type": "STRING", "description": "Optional y-axis title"},
                                }
                            }
                        },
                        "required": ["name"]
                    },
                    "description": "One or more worksheets to create."
                },
                "output_path": {"type": "STRING", "description": "Optional output path for the .xlsx"},
                "auto_open": {"type": "BOOLEAN", "description": "Open the file after creating it (default: true)"},
            },
            "required": ["title"]
        }
    },
    {
        "name": "word_document",
        "description": (
            "Creates, edits, reads, summarizes, extracts text from, and opens editable Word documents (.docx). "
            "Use for Word document requests, letters, reports, headings, bullets, formatting edits, and preserving existing formatting."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "create | create_letter | create_report | read | summarize | extract_text | append | replace_text | add_heading | add_bullets | reformat | open"
                },
                "file_path": {"type": "STRING", "description": "Existing .docx file path for read/edit/open actions"},
                "output_path": {"type": "STRING", "description": "Optional output path for the saved .docx"},
                "title": {"type": "STRING", "description": "Document title"},
                "doc_type": {"type": "STRING", "description": "letter | report | generic"},
                "content": {"type": "STRING", "description": "Main body content or text to append"},
                "body": {"type": "STRING", "description": "Body text for letter/report creation"},
                "paragraphs": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Paragraphs to add"},
                "bullets": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Bullet items to add"},
                "numbered": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Numbered items to add"},
                "sections": {"type": "ARRAY", "items": {"type": "OBJECT"}, "description": "Structured sections with heading/body/bullets"},
                "replacements": {"type": "OBJECT", "description": "Find/replace mapping for formatting-preserving edits"},
                "find": {"type": "STRING", "description": "Text to find for simple replace_text edits"},
                "replace": {"type": "STRING", "description": "Replacement text for simple replace_text edits"},
                "heading": {"type": "STRING", "description": "Heading text to append"},
                "level": {"type": "INTEGER", "description": "Heading level 1-3"},
                "recipient": {"type": "STRING", "description": "Letter recipient"},
                "salutation": {"type": "STRING", "description": "Custom letter salutation"},
                "closing": {"type": "STRING", "description": "Custom letter closing"},
                "date": {"type": "STRING", "description": "Letter date"},
                "author": {"type": "STRING", "description": "Document author"},
                "subject": {"type": "STRING", "description": "Document subject"},
                "open_after": {"type": "BOOLEAN", "description": "Open the saved document after writing (default: true)"},
                "save": {"type": "BOOLEAN", "description": "Save large generated summaries to a text file"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "pdf_document",
        "description": (
            "Creates editable-style PDF documents (.pdf) from structured content or converts DOCX / text files into PDFs. "
            "Use for PDF creation, PDF exports, and PDF generation requests that need a direct file output."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "create | create_report | create_letter | convert"
                },
                "file_path": {"type": "STRING", "description": "Existing file to convert, typically .docx or .txt"},
                "output_path": {"type": "STRING", "description": "Optional output path for the saved .pdf"},
                "title": {"type": "STRING", "description": "PDF title"},
                "subtitle": {"type": "STRING", "description": "Optional subtitle"},
                "content": {"type": "STRING", "description": "Main body content"},
                "body": {"type": "STRING", "description": "Main body content"},
                "paragraphs": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Paragraphs to add"},
                "bullets": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Bullet items to add"},
                "numbered": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Numbered items to add"},
                "sections": {"type": "ARRAY", "items": {"type": "OBJECT"}, "description": "Structured sections with heading/body/bullets"},
                "recipient": {"type": "STRING", "description": "Letter recipient"},
                "salutation": {"type": "STRING", "description": "Custom letter salutation"},
                "closing": {"type": "STRING", "description": "Custom letter closing"},
                "date": {"type": "STRING", "description": "Letter date"},
                "author": {"type": "STRING", "description": "Document author"},
                "subject": {"type": "STRING", "description": "Document subject"},
                "auto_open": {"type": "BOOLEAN", "description": "Open the file after creating it (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "shutdown_brahma",
        "description": (
            "Shuts down the assistant completely. "
        "Call this when the user expresses intent to end the conversation, "
        "close the assistant, say goodbye, or stop Brahma Evo. "
        "The user can say this in ANY language."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {},
    }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. User, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "spotify_controller",
        "description": (
            "Plays and controls music via Spotify, Spotify MCP, and Google Chrome. "
            "ALWAYS use this tool whenever the user asks to play any song, music, track, or artist "
            "(e.g. 'play Starboy', 'play music on Spotify'), or control playback "
            "('pause the music', 'resume', 'skip song', 'next track', 'volume up', 'volume down', 'mute'). "
            "Do NOT use open_app for playing songs."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "search_play | play | pause | toggle | next | previous | volume_up | volume_down | set_volume | get_now_playing | get_playlists | get_queue | get_devices | auth | mute | open_spotify (default: search_play)"
                },
                "query": {
                    "type": "STRING",
                    "description": "Song title, artist name, album, or playlist to search and play"
                },
                "volume": {
                    "type": "NUMBER",
                    "description": "Volume level (optional)"
                },
                "device_id": {
                    "type": "STRING",
                    "description": "Optional Spotify Connect playback device ID"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "calendar_scheduler",
        "description": (
            "Manages calendar events, appointments, and schedules. "
            "Use whenever the user asks to schedule a meeting, check upcoming events, "
            "view schedule for today/tomorrow, or remove an event."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "add_event | list_events | check_day | delete_event | get_upcoming | export_ics"
                },
                "title": {
                    "type": "STRING",
                    "description": "Title or summary of the meeting/event"
                },
                "date": {
                    "type": "STRING",
                    "description": "Date (YYYY-MM-DD or 'today', 'tomorrow')"
                },
                "time": {
                    "type": "STRING",
                    "description": "Time (HH:MM in 24h format, e.g. '14:30')"
                },
                "duration_minutes": {
                    "type": "NUMBER",
                    "description": "Duration in minutes (default: 30)"
                },
                "location": {
                    "type": "STRING",
                    "description": "Location or meeting link (optional)"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "daily_briefing",
        "description": (
            "Delivers a complete daily briefing including time, date, today's schedule/calendar events, "
            "and top world & tech headlines. Use whenever the user asks for their daily briefing, morning update, "
            "or what's happening today."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": "Optional news category: all (default), tech, world"
                }
            },
            "required": []
        }
    },
    {
        "name": "google_workspace",
        "description": (
            "Full Google Workspace MCP for Gmail, Calendar, and Google Drive automation. "
            "Supports: reading unread emails, listing inbox, searching emails, sending emails; "
            "listing calendar events, adding calendar events; "
            "searching Drive files, reading Drive files, and uploading files to Google Drive."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "service": {
                    "type": "STRING",
                    "description": "gmail | calendar | drive"
                },
                "action": {
                    "type": "STRING",
                    "description": "gmail actions: 'list', 'unread', 'read', 'search', 'send' | calendar actions: 'list', 'add' | drive actions: 'list', 'search', 'read', 'upload'"
                },
                "to": {
                    "type": "STRING",
                    "description": "Recipient email address for sending emails"
                },
                "subject": {
                    "type": "STRING",
                    "description": "Subject for sending or searching emails"
                },
                "body": {
                    "type": "STRING",
                    "description": "Body message for email"
                },
                "query": {
                    "type": "STRING",
                    "description": "Search query for Gmail or Drive"
                },
                "email_id": {
                    "type": "STRING",
                    "description": "ID of email to read"
                },
                "title": {
                    "type": "STRING",
                    "description": "Title/summary for calendar event"
                },
                "date": {
                    "type": "STRING",
                    "description": "Date for calendar event (YYYY-MM-DD or today/tomorrow)"
                },
                "time": {
                    "type": "STRING",
                    "description": "Time for calendar event (HH:MM)"
                },
                "duration_minutes": {
                    "type": "NUMBER",
                    "description": "Duration in minutes"
                },
                "file_path": {
                    "type": "STRING",
                    "description": "Local file path or name for Drive upload/read"
                }
            },
            "required": ["service", "action"]
        }
    },
    {
        "name": "system_diagnostics",
        "description": (
            "Local OS Hardware and System Diagnostics MCP. "
            "Checks battery health, power status, CPU usage and thermals, RAM utilization and top memory/CPU hogs, "
            "storage drive space, and controls multi-monitor display brightness. Can safely terminate frozen apps."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "status | ram_hogs | cpu_hogs | kill | brightness | battery | disk"
                },
                "target": {
                    "type": "STRING",
                    "description": "Application name (e.g. 'chrome', 'notepad') or numerical PID to terminate"
                },
                "level": {
                    "type": "NUMBER",
                    "description": "Display brightness level from 0 to 100"
                },
                "monitor": {
                    "type": "STRING",
                    "description": "Optional target monitor name or index"
                },
                "relative": {
                    "type": "BOOLEAN",
                    "description": "Whether brightness level is relative (+10, -10)"
                },
                "limit": {
                    "type": "NUMBER",
                    "description": "Maximum number of processes to return (default: 5)"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "auto_heal",
        "description": (
            "Autonomous Self-Healing and Continuous Self-Improvement System. "
            "Analyzes tracebacks/exceptions in first-party codebase, synthesizes surgical code hotfixes, "
            "validates AST syntax in safety sandbox, applies hotfixes with atomic backup, or rolls back changes. "
            "Also manages learned behavioral rules and persistent user directives."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "status | heal | history | rollback | learn_rule | list_rules"
                },
                "error_traceback": {
                    "type": "STRING",
                    "description": "The exact exception traceback or error message to heal"
                },
                "rule_text": {
                    "type": "STRING",
                    "description": "The user preference, habit, or behavioral directive to remember permanently"
                },
                "category": {
                    "type": "STRING",
                    "description": "Category for learned rule (general, formatting, workflow, habit)"
                },
                "patch_id": {
                    "type": "STRING",
                    "description": "Specific patch ID to rollback (default: latest)"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "circuit_assembler",
        "description": "Designs a safe electronic wiring schematic from component names or a screen image, then shows the interactive circuit HUD.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "assemble_components | analyze_screen | show_schematic"},
                "components": {"type": "STRING", "description": "Electronic components, such as Arduino Uno, ESP32, DHT11, or HC-SR04."},
                "query": {"type": "STRING", "description": "Circuit goal or wiring question."}
            },
            "required": ["action"]
        }
    },
    {
        "name": "geospatial_globe",
        "description": "Opens Brahma's interactive 3D map for routes, live aircraft, weather, earthquakes, nearby places, and ISS tracking.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "open | route | drive | location | fly_to | weather | flights | iss | earthquakes | nearby | radar | inspect"},
                "origin": {"type": "STRING", "description": "Origin city for a route."},
                "destination": {"type": "STRING", "description": "Destination city for a route."},
                "location": {"type": "STRING", "description": "City or area to focus on."},
                "query": {"type": "STRING", "description": "Nearby place category, such as hospitals, fuel, or restaurants."},
                "min_magnitude": {"type": "NUMBER", "description": "Minimum earthquake magnitude to display."},
                "enable": {"type": "BOOLEAN", "description": "Enable or disable the weather radar overlay."}
            },
            "required": ["action"]
        }
    },
    {
        "name": "call_screening",
        "description": "Screens an incoming call with Brahma as an AI attendant. Answering always waits for the user to confirm on the Echo HUD.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "start | take_over | hang_up"},
                "caller": {"type": "STRING", "description": "Caller name or notification title."},
                "app": {"type": "STRING", "description": "Calling application, such as Phone Link, WhatsApp, or Teams."}
            },
            "required": ["action"]
        }
    },
    {
        "name": "skill_forge",
        "description": "Use whenever the user explicitly asks to make, create, build, or add a new skill or feature. Starts creation immediately, validates the generated Python feature, and registers it for use. Do not use screen analysis for feature-creation requests.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "forge | list"},
                "goal": {"type": "STRING", "description": "What new capability the skill should provide."},
                "skill_name": {"type": "STRING", "description": "Optional short name for the skill."}
            },
            "required": ["action"]
        }
    },
    {
        "name": "dynamic_skill",
        "description": "Lists and runs installed Brahma skills, including stock, crypto, cricket, speed-test, and ISS examples. Running a skill requires user confirmation.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "list | run"},
                "skill_name": {"type": "STRING", "description": "Skill name to run."},
                "arguments": {"type": "OBJECT", "description": "Arguments for the selected skill."}
            },
            "required": ["action"]
        }
    },
    {
        "name": "calorie_counter",
        "description": (
            "Analyzes food and meals to report calories, macronutrients (protein, carbs, fat, fiber), "
            "and dietary advice. Can capture a live snapshot via webcam, inspect an image file, "
            "or analyze a spoken/typed meal description. "
            "Use whenever the user asks about the calories or nutritional value of food."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "Food or meal description (e.g. 'How many calories in this plate?', 'I had 2 eggs and toast')."
                },
                "image_path": {
                    "type": "STRING",
                    "description": "Optional local file path to a food photo."
                },
                "use_camera": {
                    "type": "BOOLEAN",
                    "description": "True to capture a live photo from the webcam."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "pushup_counter",
        "description": (
            "Live AI workout and repetition counter. Tracks reps, tempo, and calories burned "
            "for pushups, squats, and bodyweight exercises through the webcam with live HUD overlay. "
            "Use whenever the user asks to count pushups, track squats, or start a workout."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "User's request (e.g. 'count my pushups', 'track 20 squats')."
                },
                "exercise": {
                    "type": "STRING",
                    "description": "pushups | squats | general"
                },
                "target": {
                    "type": "INTEGER",
                    "description": "Target rep goal (e.g. 20)."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "upload_video",
        "description": (
            "Automates video publishing to TikTok, YouTube Shorts, or Instagram. "
            "Auto-locates recent videos on Desktop/Downloads, generates viral SEO captions and tags, "
            "copies metadata to clipboard, reveals the file in Explorer, and launches the studio uploader."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description": {
                    "type": "STRING",
                    "description": "Video topic, caption brief, or context (in user's language)."
                },
                "platform": {
                    "type": "STRING",
                    "description": "tiktok | youtube | instagram (default: tiktok)"
                },
                "video_path": {
                    "type": "STRING",
                    "description": "Optional path to the video file to publish."
                }
            },
            "required": ["description"]
        }
    },
]

#: Hard ceiling on one agentic run on the text paths. The live voice path is
#: bounded by the provider's own session; this is the belt-and-braces stop for
#: a local model that will not stop calling tools.
AGENT_MAX_TURNS = 24

#: Lazily created GUI-side MCP manager. It lives here (not on ``BrahmaLive``)
#: because the app's teardown path is in ``main()`` while the instance is built
#: inside the nested runner.
_HARNESS_MCP = None


def _harness_mcp_manager():
    """The process-wide GUI MCP manager, created on first use."""
    global _HARNESS_MCP
    if _HARNESS_MCP is None:
        from agent.mcp import WorkspaceMCP

        _HARNESS_MCP = WorkspaceMCP()
    return _HARNESS_MCP


# The harness describes its tools once in ``agent/spec.py``; append their
# Gemini declarations so the live function-calling surface advertises them
# too. Importing ``agent.code_tools`` / ``agent.subagent`` is what performs
# registration. The voice socket has no text completer for a child loop, so
# `run_subagent` is deliberately left off the live surface; the text and CLI
# loops advertise it.
try:
    from agent import code_tools as _agent_code_tools  # noqa: F401
    from agent import subagent as _agent_subagent  # noqa: F401
    from agent.spec import all_specs as _agent_specs
    from agent.spec import gemini_declarations as _agent_gemini_declarations

    TOOL_DECLARATIONS.extend(
        _agent_gemini_declarations(
            [
                spec.name
                for spec in _agent_specs()
                if (
                    desktop_tools.DESKTOP_TAG not in spec.tags
                    and spec.name not in _agent_subagent.DELEGATION_TOOLS
                )
            ]
        )
    )
except Exception as _agent_tools_exc:  # pragma: no cover - import-time guard
    print(f"[BRAHMA EVO] ⚠️ Agent harness tools unavailable: {_agent_tools_exc}")


def _pcm_block(indata):
    """Convert one sounddevice block to (int16 PCM bytes, mean level).

    Defensive on purpose. With sd.InputStream + dtype="int16" `indata` is a
    numpy int16 array, but sounddevice promises nothing about that holding:
    with RawInputStream it is a raw C buffer, and `.tobytes()` on it raises
    AttributeError inside a cffi callback, where the exception is swallowed
    ("Exception ignored from cffi callback") and the mic then goes permanently
    silent with no visible cause. Every shape is handled here so one buffer
    kind cannot silently break the voice path again. Module-level so it can be
    tested directly rather than buried in a callback needing a live stream.
    """
    import numpy as np

    if isinstance(indata, np.ndarray):
        if indata.dtype == np.float32:
            samples = (indata.reshape(-1) * 32767.0).astype(np.int16)
        else:
            samples = indata.astype(np.int16).reshape(-1)
    else:
        raw = indata if isinstance(indata, bytes) else bytes(indata)
        samples = np.frombuffer(raw, dtype=np.int16)

    level = float(np.abs(samples.astype(np.float32)).mean())
    return samples.tobytes(), level


class BrahmaLive:

    def __init__(self, ui: BrahmaUI, dashboard=None, dashboard_started: bool = False, enable_dashboard: bool = True):
        self.ui             = ui
        self._smart_home    = SmartHomeService()
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._startup_briefing_started = False
        self._loop          = None
        self._is_speaking   = False
        self._speaking_lock = threading.Lock()
        # Gemini->OpenRouter failover timing (see core.provider_failover). The
        # registry of enabled providers lives in core.provider_router.
        self._provider_failover = ProviderFailover()
        self._pending_attention: dict | None = None
        self._pending_reply_event: dict | None = None
        self._reply_mode = False
        self._attention_lock = threading.Lock()
        self._last_user_text = ""
        # Durable transcript for the OpenAI-compatible text paths. Lazy: the
        # first agentic turn creates the row, /new drops the binding, and
        # /resume binds an existing one. See agent/session_bridge.py.
        self._agent_session = None
        # --- Always On / On Call session control ---------------------------------
        # _live_mode is the user's preference; _manual_call_active is the
        # momentary "a live socket is wanted right now" gate. Both are written
        # by Qt slots on the GUI thread and read by the async session loop, so
        # every read/write pair goes through _control_lock.
        self._live_mode = "always_on"       # "always_on" | "on_call"
        self._manual_call_active = False
        # An explicit Disconnect, honoured in BOTH modes until Connect is pressed
        # again. Without this the Connect/Disconnect button was only meaningful in
        # On Call: in Always On the session loop ignored the flag and reconnected
        # five seconds later, so Disconnect looked broken. Mode and connection are
        # separate axes; this flag is the connection axis.
        self._live_suspended = False
        self._disconnect_requested = False
        self._control_lock = threading.Lock()
        # Every control change (mode, Connect/Disconnect) bumps _control_seq. A
        # reconnect pause that starts *after* a change would otherwise clear the
        # wake event and sleep the full backoff, silently ignoring it -- so the
        # pause compares the sequence it has already consumed.
        self._control_seq = 0
        self._pause_seen = 0
        self._ui_loop = None                 # loop running run(); set once by it
        self._connect_event = None           # asyncio.Event, built on that loop
        self._control_wake = None            # asyncio.Event; aborts a reconnect pause
        self._session_task = None            # the live Gemini session task
        self._reconnect_delay = 0.0          # exponential backoff, reset on connect
        self._wake_stop = None               # threading.Event that stops standby mic
        self._wake_thread = None
        # Strong refs to fire-and-forget loop tasks (dashboard, remote commands,
        # phone-audio relay). Without this the task can be garbage-collected
        # mid-flight and its exception is silently lost.
        self._background_tasks = set()
        # Wire the tool bridge's call_tool escape hatch back into this app's own
        # dispatcher, so a tool the model discovered mid-session runs through
        # exactly the same path as an advertised one.
        try:
            from core import tool_bridge

            tool_bridge.configure(executor=self._execute_tool_via_bridge)
        except Exception:
            pass
        self._attention_monitor = AttentionMonitor(on_event=self._on_external_notification)
        try:
            set_speech_sink(self.speak)
        except Exception:
            pass
            
        try:
            from actions.background_monitor import set_monitor_speech_sink
            set_monitor_speech_sink(self.speak)
        except Exception as e:
            print(f"[Main] Failed to init background monitor: {e}")
        self._meeting_lock = threading.Lock()
        self._meeting_active = False
        self._meeting_event: dict | None = None
        self._meeting_assistant = MeetingAssistant(
            on_update=self._on_meeting_update,
            on_state=self._on_meeting_state,
        )
        self._phone_active = False
        self._dashboard = dashboard if dashboard is not None else (DashboardServer() if (enable_dashboard and DashboardServer is not None) else None)
        self._dashboard_started = bool(dashboard_started and self._dashboard is not None)
        self.ui.on_text_command = self._on_text_command
        self.ui.on_attention_action = self._on_attention_action
        self.ui.on_remote_clicked = self._make_remote_key
        self._echo = EchoGuard()
        self._resume_handle = None
        self._ptt = None
        self._ptt_held = False
        try:
            self._ptt_enabled = config_manager.get_push_to_talk_enabled()
            if self._ptt_enabled:
                self.set_push_to_talk(True)
        except Exception:
            self._ptt_enabled = False

        try:
            audio_devices.configure(SEND_SAMPLE_RATE, RECEIVE_SAMPLE_RATE)
            audio_devices.prefetch()
        except Exception:
            pass
        self._last_activity = time.monotonic()
        self._idle_prompts = [
            "Hey, you there?",
            "Yo, get alive.",
            "How may I help, bro?",
            "Need anything?",
            "I'm here if you want me.",
        ]
        self._idle_speech_thread = threading.Thread(target=self._idle_speech_loop, daemon=True)
        self._idle_speech_thread.start()

    # ------------------------------------------------------------------
    # Always On / On Call session control.
    #
    # These are invoked from Qt slots on the GUI thread while the Gemini
    # session lives on the asyncio loop inside run(). An asyncio.Event and a
    # live Task are both loop-affine, so every handoff goes through
    # call_soon_threadsafe -- touching them directly from the GUI thread
    # loses wakeups with no error.
    # ------------------------------------------------------------------

    def set_live_call(self, active: bool):
        return self._session_lifecycle.set_live_call(active)

    def _provider_failover_or_default(self) -> ProviderFailover:
        failover = getattr(self, "_provider_failover", None)
        if failover is None:
            failover = ProviderFailover()
            self._provider_failover = failover
        return failover

    def _prefer_openrouter(self) -> bool:
        return self._provider_failover_or_default().prefer_openrouter()

    def _throttled_to_openrouter(self, seconds: float = 300.0) -> None:
        self._provider_failover_or_default().throttle_to_openrouter(seconds)

    def _should_standby(self) -> bool:
        return self._session_lifecycle.should_standby()

    def _request_connect(self):
        return self._session_lifecycle.request_connect()

    def _disconnect_live(self):
        return self._session_lifecycle.disconnect_live()

    def _clear_disconnect_request(self):
        with self._control_lock:
            self._disconnect_requested = False

    def _take_disconnect_request(self) -> bool:
        with self._control_lock:
            requested, self._disconnect_requested = self._disconnect_requested, False
            return requested

    def _wake_control(self):
        return self._session_lifecycle.wake_control()

    def _next_reconnect_delay(self) -> float:
        return self._session_lifecycle.next_reconnect_delay()

    def _report_voice_status(self, live: bool, detail: str = ""):
        return self._session_lifecycle.report_voice_status(live, detail)

    def _session_task_guard(self, coro):
        return self._session_lifecycle.session_task_guard(coro)

    def _voice_backend(self) -> str:
        return self._session_lifecycle.voice_backend()

    def _local_voice_turn(self, pcm_bytes: bytes, speaking: dict):
        return self._session_lifecycle.local_voice_turn(pcm_bytes, speaking)

    async def _wait_for_connect_request(self):
        return await self._session_lifecycle.wait_for_connect_request()

    def _start_wakeword_listener(self):
        return self._session_lifecycle.start_wakeword_listener()

    def _stop_wakeword_listener(self):
        return self._session_lifecycle.stop_wakeword_listener()

    @staticmethod
    def _transcribe_wakeword(recognizer, audio) -> str:
        try:
            from core import speech_stt

            return speech_stt.transcribe_pcm(
                audio.get_raw_data(), audio.sample_rate or 16000
            )
        except Exception:
            try:
                return recognizer.recognize_google(audio) or ""
            except Exception:
                return ""

    def set_push_to_talk(self, enabled: bool) -> str:
        return self._session_lifecycle.set_push_to_talk(enabled)

    def _on_ptt(self, held: bool) -> None:
        self._ptt_held = held

    def _reset_idle_activity(self):
        self._last_activity = time.monotonic()

    def _should_announce_idle(self) -> bool:
        if self.ui.muted or self._is_speaking or self._meeting_active or self._pending_attention:
            return False
        return time.monotonic() - self._last_activity >= 240

    def _make_remote_key(self):
        if self._dashboard is None:
            self.ui.write_log("ERR: Mobile Connect unavailable. Install fastapi, uvicorn, cryptography, and qrcode[pil].")
            return None
        key = self._dashboard.new_key()
        url = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    def _on_phone_connected(self):
        try:
            self.ui.notify_phone_connected()
        except Exception:
            pass

    def _handle_agent_session_command(self, text: str) -> bool:
        from core.text_turn_controller import TextTurnController

        return TextTurnController(self).handle_agent_session_command(text)

    def _on_text_command(self, text: str, source: str = "local"):
        from core.text_turn_controller import TextTurnController

        return TextTurnController(self).on_text_command(text, source)

    @property
    def _device_flow(self):
        """Lazily build the device-routing flows (P4-19 seam; see core.device_flow)."""
        df = getattr(self, "_device_flow_obj", None)
        if df is None:
            from core.device_flow import DeviceFlow

            df = DeviceFlow(self)
            self._device_flow_obj = df
        return df

    def _handle_smart_home_command(self, text: str, source: str = "local") -> bool:
        return self._device_flow.handle_smart_home_command(text, source)

    def _handle_brahma_connect_command(self, text: str, source: str = "local") -> bool:
        return self._device_flow.handle_brahma_connect_command(text, source)

    @property
    def _attention_flow(self):
        """Lazily build the attention/meeting flows (P4-19 seam; see core.attention_flow)."""
        af = getattr(self, "_attention_flow_obj", None)
        if af is None:
            from core.attention_flow import AttentionFlow

            af = AttentionFlow(self)
            self._attention_flow_obj = af
        return af

    def _on_external_notification(self, event: dict):
        return self._attention_flow.on_external_notification(event)

    def _stop_meeting_mode(self, reason: str = "Meeting mode stopped."):
        return self._attention_flow.stop_meeting_mode(reason)

    def _on_meeting_update(self, payload: dict):
        return self._attention_flow.on_meeting_update(payload)

    def _on_meeting_state(self, state: str):
        return self._attention_flow.on_meeting_state(state)

    def _handle_attention_response(self, text: str) -> bool:
        return self._attention_flow.handle_attention_response(text)

    def _on_attention_action(self, event: dict, decision: str):
        return self._attention_flow.on_attention_action(event, decision)

    @property
    def _session_lifecycle(self):
        """Lazily build the session state machine (P4-19 seam; see core.session_lifecycle)."""
        sl = getattr(self, "_session_lifecycle_obj", None)
        if sl is None:
            from core.session_lifecycle import SessionLifecycle

            sl = SessionLifecycle(
                self,
                deps={
                    "SEND_SAMPLE_RATE": SEND_SAMPLE_RATE,
                    "LIVE_MODEL": LIVE_MODEL,
                    "LIVE_CONNECT_TIMEOUT": LIVE_CONNECT_TIMEOUT,
                    "TOOL_DECLARATIONS": TOOL_DECLARATIONS,
                    "_load_system_prompt": _load_system_prompt,
                    "_injected_instructions": _injected_instructions,
                    "_standby_wakeword_detected": _standby_wakeword_detected,
                    "_speak_daily_briefing": _speak_daily_briefing,
                    "_pcm_block": _pcm_block,
                    "_get_api_key": _get_api_key,
                    "_is_gemini_limit_error": _is_gemini_limit_error,
                    "_is_transient_live_error": _is_transient_live_error,
                    "_live_error_reason": _live_error_reason,
                    "_startup_log": _startup_log,
                },
            )
            self._session_lifecycle_obj = sl
        return sl

    async def run(self):
        await self._session_lifecycle.run()

    async def _serve_live_session(self, session):
        await self._session_lifecycle._serve_live_session(session)

    def _wakeword_loop(self):
        self._session_lifecycle._wakeword_loop()

    def _idle_speech_loop(self):
        self._session_lifecycle._idle_speech_loop()

    def set_live_mode(self, mode: str):
        self._session_lifecycle.set_live_mode(mode)

    def _tool_executor(self):
        """Lazily build the tool-call executor (P4-19 seam; see core.tool_exec)."""
        ex = getattr(self, "_tool_exec", None)
        if ex is None:
            from core.tool_exec import ToolExecutor

            ex = ToolExecutor(
                self,
                ig_gemini_reply=_ig_gemini_reply,
                tool_declarations=TOOL_DECLARATIONS,
            )
            self._tool_exec = ex
        return ex

    @property
    def _reply_flow(self):
        """Lazily build the reply/IG/email flows (P4-19 seam; see core.reply_flow)."""
        rf = getattr(self, "_reply_flow_obj", None)
        if rf is None:
            from core.reply_flow import ReplyFlow

            rf = ReplyFlow(
                self,
                gemini_text_reply=_gemini_text_reply,
                get_api_key=_get_api_key,
                ig_gemini_reply=_ig_gemini_reply,
            )
            self._reply_flow_obj = rf
        return rf

    def _prompt_message_reply(self, event: dict) -> bool:
        return self._reply_flow.prompt_message_reply(event)

    def _handle_pending_reply(self, text: str) -> bool:
        return self._reply_flow.handle_pending_reply(text)

    def _handle_ig_reply_flow(self, text: str) -> bool:
        return self._reply_flow.handle_ig_reply_flow(text)

    def _handle_email_flow(self, text: str) -> bool:
        return self._reply_flow.handle_email_flow(text)

    @property
    def _audio_io(self):
        aio = getattr(self, "_audio_io_obj", None)
        if aio is None:
            from core.audio_io import AudioIO
            aio = AudioIO(
                self,
                channels=CHANNELS,
                send_sample_rate=SEND_SAMPLE_RATE,
                recv_sample_rate=RECEIVE_SAMPLE_RATE,
                chunk_size=CHUNK_SIZE,
                LiveReconnect=_LiveReconnect,
                wakeword_detected=_wakeword_detected,
                update_memory_async=_update_memory_async,
            )
            self._audio_io_obj = aio
        return aio

    async def _listen_audio(self):
        return await self._audio_io._listen_audio()

    async def _receive_audio(self):
        return await self._audio_io._receive_audio()

    async def _play_audio(self):
        return await self._audio_io._play_audio()

    def speak(self, text: str, proactive: bool = False):
        return self._audio_io.speak(text, proactive=proactive)

    def speak_error(self, tool_name: str, error: str):
        return self._audio_io.speak_error(tool_name, error)

    def _execute_tool_via_bridge(self, name: str, args: dict) -> str:
        """Runs a tool on behalf of the tool bridge's `call_tool` meta-tool."""
        return self._tool_executor().execute_via_bridge(name, args)

    def _execute_tool_sync(self, name: str, args: dict, call_id: str = "call_local") -> str:
        return self._tool_executor().execute_sync(name, args, call_id)

    @staticmethod
    def _split_emotion_tag(text: str) -> tuple:
        """Peel a leading [EMOTION] tag off a reply.

        Returns (clean_text, emotion_or_empty). Tolerant by design: anything that
        does not look like a single leading tag is returned untouched, because the
        user may legitimately start a reply with brackets.
        """
        raw = (text or "").strip()
        if not raw.startswith("["):
            return raw, ""
        end = raw.find("]")
        if end < 0 or end > 40:
            return raw, ""
        tag = raw[1:end].strip()
        if not tag or not re.fullmatch(r"[A-Za-z0-9 _\-]{2,32}", tag):
            return raw, ""
        return raw[end + 1:].strip(), tag.upper()

    def _deliver_avatar_emotion(self, emotion: str) -> None:
        from core.text_turn_controller import TextTurnController

        return TextTurnController(self).deliver_avatar_emotion(emotion)

    def _ensure_agent_session(self, request_text: str, model: str = ""):
        from core.text_turn_controller import TextTurnController

        return TextTurnController(self).ensure_agent_session(request_text, model)

    def _record_voice_turn(self, user_text: str, assistant_text: str) -> None:
        from core.text_turn_controller import TextTurnController

        return TextTurnController(self).record_voice_turn(user_text, assistant_text)

    def _with_agent_mcp(self, tools: list) -> list:
        from core.text_turn_controller import TextTurnController

        return TextTurnController(self).with_agent_mcp(tools)

    def _local_brain_reply(self, request_text: str, local_model_target: str) -> str:
        from core.text_turn_controller import TextTurnController

        return TextTurnController(self).local_brain_reply(
            request_text, local_model_target
        )

    def _agentic_seed_messages(
        self, request_text: str, *, advertise_discovery: bool = True
    ) -> list:
        from core.text_turn_controller import TextTurnController

        return TextTurnController(self).agentic_seed_messages(
            request_text, advertise_discovery=advertise_discovery
        )

    def _finish_tool_turn(
        self,
        res,
        messages: list,
        *,
        source: str,
        model_label: str,
        tools: list | None = None,
    ) -> str:
        from core.text_turn_controller import TextTurnController

        return TextTurnController(self).finish_tool_turn(
            res, messages, source=source, model_label=model_label, tools=tools
        )

    def _agent_event(self, event: dict) -> None:
        from core.text_turn_controller import TextTurnController

        return TextTurnController(self).agent_event(event)

    def _custom_brain_turn(
        self, request_text: str, label: str = "Custom provider"
    ) -> str:
        from core.text_turn_controller import TextTurnController

        return TextTurnController(self).custom_brain_turn(request_text, label)

    @property
    def _custom_tools_broken(self) -> bool:
        return bool(getattr(self, "_custom_tools_broken_", False))

    @_custom_tools_broken.setter
    def _custom_tools_broken(self, value: bool) -> None:
        self._custom_tools_broken_ = bool(value)

    def _fallback_reply(self, text: str, memory_ctx: str = ""):
        from core.text_turn_controller import TextTurnController

        return TextTurnController(self).fallback_reply(text, memory_ctx)

    def set_speaking(self, value: bool):
        return self._session_lifecycle.set_speaking(value)

    def trigger_barge_in(self):
        return self._session_lifecycle.trigger_barge_in()
    def _forge_skill(self, goal: str, skill_name: str | None = None) -> str:
        from core.skill_forge import SkillForge

        try:
            result = SkillForge.forge_skill(goal=goal, skill_name=skill_name)
            message = str(result.get("message") or result.get("error") or "Skill creation failed.")
        except Exception as exc:
            result = {"success": False}
            message = f"Skill creation failed: {exc}"
        if result.get("success"):
            feature_name = str(result.get("name") or skill_name or "new feature")
            description = str(result.get("description") or "")
            staged_path = str(result.get("staged_path") or result.get("skill_path") or "")
            announcement = (
                f"I drafted the feature '{feature_name}' and verified it in the "
                "sandbox, but it is not active yet."
            )
            if description:
                announcement += f" {description}"
            announcement += f" I drafted it for this goal: {goal}."
            if staged_path:
                announcement += f" It is staged at {staged_path} for review."

            try:
                self.ui.show_hud_deliverable(
                    title=f"Staged feature: {feature_name}",
                    summary=(
                        f"{announcement}\n\n"
                        "Nothing is imported or registered until you review and promote the staged copy."
                    ),
                    kind="result",
                )
            except Exception:
                pass
            try:
                self.ui.write_log(f"Brahma Evo: {announcement}")
            except Exception:
                pass
            self.speak(announcement)
        else:
            try:
                self.ui.write_log(f"ERR: {message}")
            except Exception:
                pass
            self.speak(message)
        return message

    def _run_confirmed_skill(self, skill_name: str, arguments: dict) -> str:
        from core.dynamic_registry import DynamicToolRegistry

        result = DynamicToolRegistry.execute_sync(skill_name, arguments or {})
        message = str(result)
        try:
            self.ui.show_content(f"Skill: {skill_name}", message)
            self.ui.write_log(f"SYS: Skill '{skill_name}' completed.")
        except Exception:
            pass
        return message

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        return await self._tool_executor().execute(fc)

    @property
    def _realtime_flow(self):
        """Lazily build the realtime/dashboard flows (P4-19 seam; see core.realtime_flow)."""
        rf = getattr(self, "_realtime_flow_obj", None)
        if rf is None:
            from core.realtime_flow import RealtimeFlow

            rf = RealtimeFlow(self)
            self._realtime_flow_obj = rf
        return rf

    def _spawn_background(self, coro, name: str):
        return self._realtime_flow.spawn_background(coro, name)

    async def _serve_dashboard(self):
        return await self._realtime_flow.serve_dashboard()

    async def _consume_remote_commands(self):
        return await self._realtime_flow.consume_remote_commands()

    async def _relay_phone_audio(self):
        return await self._realtime_flow.relay_phone_audio()

    async def _send_realtime(self):
        return await self._realtime_flow.send_realtime()

def main():
    _startup_log("main entered")
    # No startup self-update: the old path pulled from a hardcoded upstream and
    # restarted on any fast-forward. Signed releases are WS-6 work; until then
    # the app does not mutate its own install.
    _ensure_desktop_shortcut()

    # Boot user-configured custom MCP servers in the background. Never fatal: a
    # broken server must not stop Brahma from starting, so this is fully guarded
    # and runs after the update check so a restart-on-update exits first.
    def _start_custom_mcp_servers():
        try:
            from core.mcp_manager import MCPServerManager
            MCPServerManager.start_servers()
            _startup_log("custom MCP servers requested")
            # MCP servers register their tools asynchronously, so the index must
            # be refreshed once they land or those tools stay unreachable until
            # the next restart. Wait briefly, then re-sync.
            def _resync():
                import time as _t
                deadline = _t.time() + 30
                seen = set()
                while _t.time() < deadline:
                    _t.sleep(2.0)
                    try:
                        status = MCPServerManager.status()
                        names = {n for n, s in status.items() if s.get("tools")}
                        if names == seen:
                            continue
                        seen = names
                        from core import tool_bridge
                        from core.dynamic_registry import DynamicToolRegistry
                        tool_bridge.sync_all(TOOL_DECLARATIONS, DynamicToolRegistry)
                        total = sum(len(s.get("tools") or []) for s in status.values())
                        _startup_log(f"tool index refreshed after MCP connect: {total} MCP tool(s)")
                    except Exception as exc:
                        _startup_log(f"MCP index resync skipped: {exc}")

            threading.Thread(target=_resync, daemon=True, name="mcp-index-resync").start()
        except Exception as exc:
            _startup_log(f"custom MCP startup skipped: {exc}")

    threading.Thread(target=_start_custom_mcp_servers, daemon=True).start()

    # Build the tool index before the first Live handshake. This is what keeps
    # the advertised tool list inside Gemini's 128-declaration ceiling and keeps
    # every other tool reachable through search_tools / call_tool. Runs before
    # the UI so the settings panel can show real numbers at first paint.
    try:
        from core import tool_bridge
        from core.dynamic_registry import DynamicToolRegistry
        from memory import config_manager as _cfg

        DynamicToolRegistry.initialize()
        tool_bridge.configure(settings=_cfg.load_settings())
        # ADR-019 wave 1: the migrated native actions are ToolSpecs before the
        # first handshake, so both doors see them in the one registry.
        desktop_tools.register(TOOL_DECLARATIONS)
        tool_bridge.prime(TOOL_DECLARATIONS, DynamicToolRegistry)
        _ts = tool_bridge.snapshot()
        _startup_log(
            f"tool index ready: {_ts['tools']} tools, advertising {_ts['advertised']}, "
            f"{_ts['deferred']} deferred, backends={','.join(_ts['backends']) or 'none'}"
        )
    except Exception as exc:
        _startup_log(f"tool index unavailable: {exc}")

    ui = BrahmaUI(str(BASE_DIR / "assets" / "Brahma_Lite_Logo.png"), show_immediately=True)

    # Bind the agent harness to this process. Two things have to be set here or
    # the defaults are wrong for the GUI:
    #
    #   * workspace -- the folder the user pointed developer mode at, not the
    #     process cwd (a shortcut launch starts in the install directory, so a
    #     cwd default would silently let the agent write into the repo).
    #   * approver  -- the HUD confirmation banner. Left unset, the runtime
    #     computes headless=True and turns every "ask" into a deny, so the
    #     agent would be unable to run tests or touch a protected file even
    #     with the user sitting in front of it.
    try:
        from agent import runtime as _agent_runtime
        from memory import config_manager as _agent_cfg

        _agent_workspace = str(
            (_agent_cfg.load_settings() or {}).get("developer_mode_workspace") or ""
        ).strip()
        if _agent_workspace and Path(_agent_workspace).expanduser().is_dir():
            _agent_runtime.configure(
                workspace=_agent_workspace,
                approver=_agent_runtime.gui_approver,
                headless=False,
            )
            _startup_log(f"agent harness ready: workspace={_agent_workspace}")
        else:
            _agent_runtime.configure(
                approver=_agent_runtime.gui_approver,
                headless=False,
            )
            _startup_log(
                "agent harness ready: workspace="
                f"{_agent_runtime.current().workspace} (developer_mode_workspace unset)"
            )
    except Exception as exc:
        _startup_log(f"agent harness unavailable: {exc}")

    # Project skills are one store with two doors. The harness lists
    # `.brahma/skills` in the prompt and loads bodies with
    # `load_project_skill`; the Skills Library behind the voice/core
    # `search_skills`/`load_skill` surface catalogues the same directories.
    # Re-point it at the runtime workspace, which is fixed at startup.
    try:
        from agent.runtime import current as _current_agent_runtime
        from core import skills_library as _skills_library

        _skills_library.configure_library(
            project_workspace=str(_current_agent_runtime().workspace)
        )
        _roots = _skills_library.get_library().project_dirs()
        _startup_log(
            "skills library project roots: "
            + (", ".join(str(root) for root in _roots) or "none")
        )
    except Exception as exc:
        _startup_log(f"skills library project roots unavailable: {exc}")

    dashboard = None
    dashboard_enabled = DashboardServer is not None and not _is_port_in_use(8000)
    if DashboardServer is not None and not dashboard_enabled:
        _startup_log("dashboard disabled: port 8000 already in use")
        try:
            ui.write_log("SYS: Mobile Connect is already running in another Brahma Evo instance.")
        except Exception:
            pass
    if dashboard_enabled:
        dashboard = DashboardServer()

    if dashboard is not None:
        def _start_dashboard_server():
            try:
                _startup_log("dashboard thread started")
                asyncio.run(dashboard.serve())
            except Exception as exc:
                _startup_log(f"dashboard thread error: {exc}")
                try:
                    ui.write_log(f"ERR: Mobile Connect server failed: {exc}")
                except Exception:
                    pass

        threading.Thread(target=_start_dashboard_server, daemon=True).start()
        _startup_log("dashboard thread spawned")

    brahma_connect = None
    brahma_connect_enabled = False
    if get_brahma_connect_service is not None:
        try:
            brahma_connect = get_brahma_connect_service(BASE_DIR)
            brahma_connect_enabled = bool(brahma_connect.gateway.config.enabled)
        except Exception as exc:
            _startup_log(f"brahma connect init failed: {exc}")
            try:
                ui.write_log(f"ERR: Brahma Connect failed to initialize: {exc}")
            except Exception:
                pass
            brahma_connect = None
    try:
        if brahma_connect is not None and hasattr(ui, "set_brahma_connect_service"):
            ui.set_brahma_connect_service(brahma_connect)
    except Exception:
        pass
    if brahma_connect is not None and brahma_connect_enabled:
        connect_port = int(getattr(brahma_connect.gateway.config, "port", 8765))
        if _is_port_in_use(connect_port):
            _startup_log(f"brahma connect disabled: port {connect_port} already in use")
            try:
                ui.write_log(f"SYS: Brahma Connect is already running on port {connect_port}.")
            except Exception:
                pass
        else:
            def _start_brahma_connect_server():
                try:
                    _startup_log("brahma connect thread started")
                    brahma_connect.start_background()
                    _startup_log("brahma connect thread spawned")
                except Exception as exc:
                    _startup_log(f"brahma connect thread error: {exc}")
                    try:
                        ui.write_log(f"ERR: Brahma Connect server failed: {exc}")
                    except Exception:
                        pass

            threading.Thread(target=_start_brahma_connect_server, daemon=True).start()

    # Start OpenAI-compatible API port if enabled (Phase 1)
    brahma_api_server = None
    try:
        from brahma_api.config import BrahmaApiConfig
        from brahma_api.server import serve as serve_brahma_api

        api_config = BrahmaApiConfig.load(BASE_DIR)
        if api_config.enabled:
            api_port = api_config.port
            if _is_port_in_use(api_port):
                _startup_log(f"brahma_api disabled: port {api_port} already in use")
                try:
                    ui.write_log(f"SYS: Brahma API is already running on port {api_port}.")
                except Exception:
                    pass
            else:
                brahma_api_server = serve_brahma_api(api_config)

                def _start_api_server():
                    try:
                        _startup_log("brahma_api thread started")
                        brahma_api_server.run()
                    except Exception as exc:
                        _startup_log(f"brahma_api thread error: {exc}")

                threading.Thread(target=_start_api_server, daemon=True, name="brahma-api-server").start()
    except Exception as exc:
        _startup_log(f"brahma_api startup failed: {exc}")


    ui.show_main()
    _startup_log("ui shown")

    # Start Brahma Passive Sensorium Engine (v2)
    try:
        def _on_sensorium_alert(alert_type: str, meta: dict):
            msg = meta.get("message", "System state change detected.")
            try:
                ui.write_log(f"🧠 SENSORIUM: {msg}")
            except Exception:
                pass
        sensorium.register_interjection_handler(_on_sensorium_alert)
        sensorium.start()
        _startup_log("passive sensorium daemon started")
    except Exception as exc:
        _startup_log(f"sensorium start failed: {exc}")

    try:
        from core.globe_window import GlobeWindow
        GlobeWindow.get_instance(parent=None)
        _startup_log("globe window initialized")
    except Exception as exc:
        _startup_log(f"globe window initialization failed: {exc}")
        try:
            ui.write_log(f"ERR: Globe window initialization failed: {exc}")
        except Exception:
            pass

    # Initialize plugin manager and load any plugins from ./plugins
    try:
        plugin_manager = PluginManager(BASE_DIR)
        plugin_manager.load_plugins()
    except Exception:
        plugin_manager = None

    def runner():
        _startup_log("runner waiting api key")
        ui.wait_for_api_key()
        _startup_log("runner api key ready")
        brahma_evo = BrahmaLive(
            ui,
            dashboard=dashboard,
            dashboard_started=dashboard is not None,
            enable_dashboard=dashboard_enabled,
        )
        # These signals are declared on MainWindow and forwarded by BrahmaUI.
        # They used to be connected inside a bare `except Exception: pass`,
        # which hid an AttributeError and left the whole On Call feature
        # dead -- buttons repainted, the session never changed. Surface it.
        try:
            ui.live_mode_changed.connect(brahma_evo.set_live_mode)
            ui.live_call_toggled.connect(brahma_evo.set_live_call)
            print("[BRAHMA EVO] ✅ On Call control wired.")
        except Exception as exc:
            _startup_log(f"On Call control NOT wired: {exc}")

        # Wire proactive voice to Sensorium
        def _proactive_sensorium_voice(alert_type: str, meta: dict):
            spoken_text = meta.get("speech")
            if spoken_text and not getattr(ui, "muted", False):
                try:
                    brahma_evo.speak(spoken_text, proactive=True)
                except Exception as exc:
                    print(f"[Sensorium Speak Error]: {exc}")

        sensorium.register_interjection_handler(_proactive_sensorium_voice)
        try:
            if plugin_manager is not None:
                brahma_evo.plugin_manager = plugin_manager
                plugin_manager.register_brahma(brahma_evo)
                # allow plugins to run a startup hook
                try:
                    plugin_manager.dispatch("on_startup", brahma_evo)
                except Exception:
                    pass
        except Exception:
            pass

        print(f"DEBUG: start_ig_daemon is {start_ig_daemon}")
        
        if start_ig_daemon:
            try:
                from actions.instagram_mcp import set_ig_prompt_callback
            except ImportError:
                from actions.instagram_chat import set_ig_prompt_callback

            def _ig_handler(thread_id, username, text, is_auto):
                if is_auto:
                    return _ig_gemini_reply(username, text)
                else:
                    brahma_evo._ig_reply_mode = True
                    brahma_evo._ig_pending_thread = {
                        "thread_id": thread_id,
                        "username": username,
                        "message": text
                    }
                    clean_text = (text or "").strip()
                    snippet = f": '{clean_text[:75]}...'" if len(clean_text) > 75 else (f": '{clean_text}'" if clean_text else "")
                    msg = f"You received a new Instagram message from {username}{snippet}. What should I reply, or should I take over the chat?"
                    ui.write_log(f"📱 Insta (@{username}): {clean_text or '[Media/Attachment]'}")
                    ui.write_log(f"Brahma Evo: {msg}")
                    brahma_evo.speak(msg)
                    return None
                
            set_ig_prompt_callback(_ig_handler)
            start_ig_daemon()

        # Background Email Watcher
        try:
            from actions.google_workspace_mcp import (
                start_email_daemon,
                set_email_prompt_callback,
                get_stored_gmail_credentials,
            )
            gmail_addr, gmail_pw = get_stored_gmail_credentials()
            if gmail_addr and gmail_pw:
                def _email_handler(sender, subject, msg_id):
                    clean_subj = (subject or "No Subject").strip()
                    subj_preview = f"'{clean_subj[:70]}...'" if len(clean_subj) > 70 else f"'{clean_subj}'"
                    msg = f"You received a new email from {sender} with subject: {subj_preview}."
                    ui.write_log(f"📧 Email ({sender}): {clean_subj}")
                    ui.write_log(f"Brahma Evo: {msg}")
                    brahma_evo.speak(msg)

                set_email_prompt_callback(_email_handler)
                start_email_daemon(poll_interval=25)
                print("[Brahma Evo] Background email watcher started.")
        except Exception as e:
            print(f"[Brahma Evo] Email daemon initialization notice: {e}")

        def _clipboard_monitor():
            try:
                last_clip = pyperclip.paste()
            except Exception:
                last_clip = ""
            while True:
                time.sleep(1.0)
                try:
                    from core.tool_index import get_index
                    curr_clip = pyperclip.paste()
                    if get_index().is_disabled("clipboard_processor"):
                        last_clip = curr_clip
                        continue
                    if curr_clip != last_clip:
                        last_clip = curr_clip
                        text = (curr_clip or "").strip()
                        if text and len(text) > 3:
                            reply = _clipboard_gemini_reply(text[:1000])
                            ui.write_log(f"Brahma Evo (Clipboard): {reply}")
                            brahma_evo.speak(reply)
                except Exception:
                    pass

        threading.Thread(target=_clipboard_monitor, daemon=True, name="clipboard-monitor").start()

        try:
            asyncio.run(brahma_evo.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    def start_runner():
        threading.Thread(target=runner, daemon=True).start()

    start_runner()
    ui.show_main()
    try:
        ui.root.mainloop()
    finally:
        # Tear down MCP child processes so they cannot outlive the app.
        try:
            from core.mcp_manager import MCPServerManager
            MCPServerManager.stop_all()
        except Exception as exc:
            _startup_log(f"custom MCP shutdown skipped: {exc}")
        try:
            harness_mcp = globals().get("_HARNESS_MCP")
            if harness_mcp is not None:
                harness_mcp.close()
        except Exception as exc:
            _startup_log(f"harness MCP shutdown skipped: {exc}")
        try:
            if brahma_api_server is not None:
                brahma_api_server.should_exit = True
        except Exception as exc:
            _startup_log(f"brahma_api shutdown skipped: {exc}")


if __name__ == "__main__":
    import sys
    import os
    import traceback

    # Intercept subprocess calls when running as PyInstaller .exe
    if len(sys.argv) >= 2:
        if sys.argv[1].endswith(".py") and os.path.exists(sys.argv[1]):
            # A bundled helper may re-invoke this executable with a generated
            # .py script; running it here keeps those helpers working when the
            # app is frozen (sys.executable is the app, not a Python).
            try:
                with open(sys.argv[1], 'r', encoding='utf-8') as f:
                    code = f.read()
                namespace = {"__name__": "__main__", "__file__": sys.argv[1]}
                exec(code, namespace)
                sys.exit(0)
            except Exception:
                traceback.print_exc()
                sys.exit(1)
        elif sys.argv[1] == "-m" and len(sys.argv) >= 3 and sys.argv[2] == "pip":
            try:
                from pip._internal.cli.main import main as pip_main
                sys.exit(pip_main(sys.argv[3:]))
            except ImportError:
                print("pip is not available in the compiled executable.")
                sys.exit(1)

    try:
        main()
    except Exception:
        try:
            with open("FATAL_CRASH.log", "w") as f:
                traceback.print_exc(file=f)
        except Exception:
            pass
        raise