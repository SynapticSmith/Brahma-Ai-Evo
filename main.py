
from core.user_paths import get_user_data_dir
import os

from core.qt_bootstrap import bootstrap

# Qt/QtWebEngine runtime settings (Chromium flags + shared GL contexts) must be
# in place before any module pulls in QtWebEngineWidgets.
bootstrap()

from core import undo as undo_stack
from core import audio_devices
from core.echo import EchoGuard
from core.hotkey import PushToTalk
from core.provider_failover import ProviderFailover
from memory import config_manager
from memory.memory_manager import search_memory

from core import boot_sentry
boot_sentry.run()
from core.sensorium import sensorium
from core.protocols import protocols
from core.local_brain import local_brain
import asyncio
import threading
import json
import re
import shutil
import socket
import subprocess
import sys
import time
import traceback
import os
import pyperclip
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import sounddevice as sd
from google import genai
from google.genai import types
from ui import BrahmaUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    should_extract_memory, extract_memory, auto_learn_interaction
)

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import screen_process
from actions.meeting_assistant import MeetingAssistant
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.office_builder     import create_presentation, create_spreadsheet
from actions.docx_tools        import word_document
from actions.pdf_tools         import create_pdf
from actions.brahma_connect    import (
    connect_list_devices,
    connect_get_device,
    connect_get_capabilities,
    connect_execute,
    connect_pair_device,
    connect_disconnect_device,
)
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater
from actions.attention_monitor import AttentionMonitor, speak_native, stop_native_speech, handle_call_action, read_event_preview, set_speech_sink
# from actions.daily_briefing import compile_daily_briefing
from llm_client import client as openrouter_client
from workspace_store import store as workspace_store
from smart_home.service import SmartHomeService
from plugin_manager import PluginManager
from updater import restart_application, update_from_github

try:
    from dashboard.server import DashboardServer
except Exception:
    DashboardServer = None

try:
    from actions.instagram_mcp import start_daemon as start_ig_daemon, set_ig_prompt_callback
except ImportError:
    try:
        from actions.instagram_chat import start_daemon as start_ig_daemon, set_ig_prompt_callback
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

    Previously this raised a bare ``KeyError`` from ``json.load(...)["gemini_api_key"]``
    when the file or key was missing. That escaped ``run()`` and killed the whole
    voice loop with no visible reason. Callers can now catch one clear RuntimeError.
    """
    try:
        data = json.loads(API_CONFIG_PATH.read_text(encoding="utf-8"))
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


def _load_system_prompt() -> str:
    try:
        base_prompt = PROMPT_PATH.read_text(encoding="utf-8")
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
            
        identity_str += f"Your current behavior mode is: {mode}.\n"
        
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
    client = genai.Client(
        api_key=_get_api_key(),
        http_options={"api_version": "v1beta"},
    )
    system_prompt = (
        "You are Brahma Evo, a concise, helpful desktop assistant. "
        "Reply naturally and briefly. Do not mention internal implementation details."
    )
    extra = _injected_instructions(layer)
    if extra:
        system_prompt = f"{system_prompt}{extra}"
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=f"{system_prompt}\n\nUser: {prompt}",
        config={"temperature": 0.6},
    )
    return _extract_gemini_text(response)


def _ig_gemini_reply(username: str, text: str) -> str:
    system_prompt = (
        "You are Brahma Evo, an AI personal assistant acting on behalf of your user. "
        "You have taken over their Instagram chat with the user's permission. "
        "Reply naturally, briefly, and conversationally to the incoming message. "
        "Do not sound like a bot. Keep your replies under 2 sentences."
    )
    prompt = f"Instagram DM from {username}: {text}"
    
    try:
        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"},
        )
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"{system_prompt}\n\nUser: {prompt}",
            config={"temperature": 0.6},
        )
        return _extract_gemini_text(response)
    except Exception as e:
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e) or _is_gemini_limit_error(e):
            print("[InstagramChat] Gemini Rate Limit hit, falling back to OpenRouter...")
            try:
                from llm_client import client as openrouter_client
                return openrouter_client.chat(prompt, system=system_prompt)
            except Exception as or_e:
                print(f"[InstagramChat] OpenRouter fallback failed: {or_e}")
                return "Hey, I'm currently busy. I will get back to you later!"
        print(f"[InstagramChat] Gemini Reply Error: {e}")
        return "Hey, I'm currently busy. I will get back to you later!"


def _clipboard_gemini_reply(text: str) -> str:
    system_prompt = (
        "You are Brahma Evo, a witty and helpful AI assistant. "
        "The user just copied the following text to their clipboard. "
        "Make a very short, interesting, or helpful 1-sentence comment or question about it. "
        "Do not offer to 'help' or ask 'how can I help'. Just make a standalone witty observation or summary."
    )
    prompt = text
    try:
        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"},
        )
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"{system_prompt}\n\nClipboard Text: {prompt}",
            config={"temperature": 0.8},
        )
        return _extract_gemini_text(response)
    except Exception as e:
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e) or _is_gemini_limit_error(e):
            try:
                from llm_client import client as openrouter_client
                return openrouter_client.chat(prompt, system=system_prompt)
            except Exception:
                pass
        return "Interesting stuff you copied there!"


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
    if isinstance(exc, _LiveReconnect):
        return str(exc) or "server requested reconnect"
    text = f"{type(exc).__name__}: {exc}".strip()
    return text[:160] if text else type(exc).__name__


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
            "Use for ANY single computer control command. NEVER route to agent_task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."},
                "confirmed":   {"type": "STRING", "description": "Pass 'yes' if the user explicitly confirmed a dangerous action like 'shutdown' or 'restart'."}
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
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks requiring multiple different tools. "
            "Examples: 'research X and save to file', 'find and organize files'. "
            "DO NOT use for single commands. NEVER use for Steam/Epic — use game_updater."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}
            },
            "required": ["goal"]
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
            "NEVER use agent_task, browser_control, or web_search for Steam/Epic."
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
        """Connect / Disconnect. Authoritative in BOTH modes."""
        active = bool(active)
        with self._control_lock:
            self._manual_call_active = active
            # Pressing Connect clears an earlier explicit Disconnect; pressing
            # Disconnect latches it until Connect is pressed again.
            self._live_suspended = not active
        if active:
            self._stop_wakeword_listener()
            self._request_connect()
        else:
            self.ui.set_state("STANDBY")
            self._disconnect_live()
        # Abort any in-flight reconnect backoff so Connect/Disconnect is instant.
        self._wake_control()

    def _provider_failover_or_default(self) -> ProviderFailover:
        """Return the failover tracker, creating it if ``__init__`` was bypassed.

        Test harnesses build instances by setting attributes rather than calling
        ``__init__``, so the tracker may not exist yet.
        """
        failover = getattr(self, "_provider_failover", None)
        if failover is None:
            failover = ProviderFailover()
            self._provider_failover = failover
        return failover

    def _prefer_openrouter(self) -> bool:
        """True while a recent Gemini throttle should keep OpenRouter first."""
        return self._provider_failover_or_default().prefer_openrouter()

    def _throttled_to_openrouter(self, seconds: float = 300.0) -> None:
        """Record a Gemini throttle and route to OpenRouter for a cooldown."""
        self._provider_failover_or_default().throttle_to_openrouter(seconds)

    def _should_standby(self) -> bool:
        """True when the session loop must idle instead of holding a session.

        Three independent inputs, deliberately separate:
          _live_suspended   - an explicit Disconnect, honoured in any mode
          _live_mode        - the Always On / On Call preference
          _manual_call_active - a call placed through the Connect button
        """
        with self._control_lock:
            if self._live_suspended:
                return True
            return self._live_mode == "on_call" and not self._manual_call_active

    def _request_connect(self):
        """Wake the standby wait. Safe to call before run() has started."""
        loop, event = self._ui_loop, self._connect_event
        if loop is None or event is None:
            # run() has not reached the standby wait yet; it re-reads the
            # flags on its next pass, so nothing is lost.
            return
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            pass  # loop already closed; nothing left to wake

    def _disconnect_live(self):
        """Drop the live session. No-op when nothing is connected."""
        loop, task = self._ui_loop, self._session_task
        with self._control_lock:
            self._disconnect_requested = True
        if loop is None or task is None or task.done():
            return
        try:
            loop.call_soon_threadsafe(task.cancel)
        except RuntimeError:
            pass

    def _clear_disconnect_request(self):
        with self._control_lock:
            self._disconnect_requested = False

    def _take_disconnect_request(self) -> bool:
        with self._control_lock:
            requested, self._disconnect_requested = self._disconnect_requested, False
            return requested

    def _wake_control(self):
        """Abort an in-progress reconnect pause so a mode change is immediate.

        The session loop waits between reconnect attempts. That wait used to be a
        bare ``asyncio.sleep``, so switching to On Call (or pressing Disconnect)
        did nothing until the sleep expired -- up to the full backoff. Waking the
        pause lets the loop re-read the control flags right away and park in
        standby instead of retrying a socket the user no longer wants.
        """
        loop = getattr(self, "_ui_loop", None)
        event = getattr(self, "_control_wake", None)
        with self._control_lock:
            self._control_seq = getattr(self, "_control_seq", 0) + 1
        if loop is None or event is None:
            # run() has not built the pause event yet; the sequence bump makes
            # the next pause return immediately, so nothing is lost.
            return
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            pass  # loop already closed; nothing left to wake

    def _next_reconnect_delay(self) -> float:
        """Exponential backoff for the reconnect pause: 2, 4, 8 ... capped at 30.

        ``_reconnect_delay`` is reset to 0 on every good connect, so the first
        retry after a healthy session is always the fast one.
        """
        self._reconnect_delay = 2.0 if self._reconnect_delay <= 0.0 \
            else min(self._reconnect_delay * 2.0, 30.0)
        return self._reconnect_delay

    def _report_voice_status(self, live: bool, detail: str = ""):
        """Tell the UI which voice backend is driving the session and its state.

        The chat indicator previously only reflected the microphone, so a local
        voice session, a Gemini socket and a custom endpoint were
        indistinguishable. This is the single place that reports it, so both
        backends cannot disagree.
        """
        label = "Local voice" if self._voice_backend() == "local_loop" else "Gemini Live"
        try:
            self.ui.set_voice_status(label, bool(live), detail)
        except Exception:
            pass

    def _session_task_guard(self, coro):
        """Register the running local session so _disconnect_live() can cancel it.

        The Gemini path does this with asyncio.create_task for exactly this
        reason: awaiting inline leaves no handle, and "Disconnect" would
        silently do nothing. The local loop needs the same handle.
        """
        async def _runner():
            try:
                await coro
            finally:
                with self._control_lock:
                    self._session_task = None

        self._session_task = asyncio.ensure_future(_runner())
        return self._session_task

    def _voice_backend(self) -> str:
        """Which engine drives the voice session: "gemini_live" or "local_loop".

        This used to be decided by skipping the whole loop when Local was
        selected, so with a local model the socket never opened and the mic
        handler that pipes whisper STT into the local model never ran. The
        controls (Always On / On Call / Connect / Disconnect) are therefore
        inert for local models. Backend choice is now an axis of the session,
        not a way of bypassing it.
        """
        try:
            cfg = config_manager.load_settings()
        except Exception:
            cfg = {}
        if cfg.get("offline_mode_enabled", False):
            return "local_loop"
        if str(cfg.get("default_ai_provider", "") or "").strip().lower() == "local":
            return "local_loop"
        return "gemini_live"

    def _local_voice_turn(self, pcm_bytes: bytes, speaking: dict):
        """One local voice turn: transcribe, answer, speak. Runs off-loop."""
        try:
            from core import speech_stt

            text = speech_stt.transcribe_pcm(pcm_bytes, SEND_SAMPLE_RATE)
            if not text or len(text.strip()) <= 1:
                return
            print(f"[BRAHMA EVO] 🎙️ Heard: {text}")
            if self.ui.muted and _wakeword_detected(text):
                try:
                    self.ui.set_muted_state(False, wakeword=True)
                except Exception:
                    pass
            speaking["on"] = True
            try:
                self._on_text_command(text, source="mic")
            finally:
                speaking["on"] = False
        except Exception as exc:
            print(f"[BRAHMA EVO] ⚠️ Local voice turn failed: {exc}")

    async def _wait_for_connect_request(self):
        """Idle in On Call standby until the button or the wake word asks.

        The Event is created here, on the loop that awaits it, so it is always
        correctly bound. A request arriving between the caller reading the flag
        and this Event existing would be lost, so the flags are re-checked right
        after creation.
        """
        self._connect_event = asyncio.Event()
        self.set_speaking(False)
        self.ui.set_state("STANDBY")
        self._start_wakeword_listener()
        try:
            if not self._should_standby():
                return
            self.ui.write_log("SYS: On Call standby - say 'Brahma Evo' or press Connect.")
            await self._connect_event.wait()
        finally:
            self._connect_event = None
            self._stop_wakeword_listener()

    def _start_wakeword_listener(self):
        if self._wake_thread is not None and self._wake_thread.is_alive():
            return
        self._wake_stop = threading.Event()
        self._wake_thread = threading.Thread(
            target=self._wakeword_loop, daemon=True, name="oncall-wakeword"
        )
        self._wake_thread.start()

    def _stop_wakeword_listener(self):
        # Keep the thread reference until the worker actually exits. Nulling it
        # here let a still-blocked listener (up to ~3s inside recognizer.listen)
        # overlap with the next one, and the two then fought over the microphone.
        # _start refuses to arm while the previous thread is still alive, and the
        # worker clears its own slot in a finally once it is truly done.
        stop = self._wake_stop
        if stop is not None:
            stop.set()

    @staticmethod
    def _transcribe_wakeword(recognizer, audio) -> str:
        # Capture still comes from SpeechRecognition (it owns the mic and the
        # silence trimming); only the transcription is pluggable, so an offline
        # machine can use faster-whisper for the wake word too.
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
        self._ptt_enabled = bool(enabled)
        self._ptt_held = False
        if not enabled:
            if self._ptt is not None:
                self._ptt.stop()
                self._ptt = None
            return "off"
        if self._ptt is None:
            self._ptt = PushToTalk(self._on_ptt)
        scope = self._ptt.start()
        try:
            self.ui.write_log(
                f"SYS: Push-to-talk on — hold {self._ptt.label}"
                + ("." if scope == "global" else " (works while window focused).")
            )
        except Exception:
            pass
        return scope

    def _on_ptt(self, held: bool) -> None:
        self._ptt_held = held

    def _reset_idle_activity(self):
        self._last_activity = time.monotonic()

    def _should_announce_idle(self) -> bool:
        if self.ui.muted:
            return False
        if self._is_speaking:
            return False
        if self._meeting_active:
            return False
        if self._pending_attention:
            return False
        if time.monotonic() - self._last_activity < 240:
            return False
        return True

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

    def _on_text_command(self, text: str, source: str = "local"):
        self._reset_idle_activity()
        text = (text or "").strip()
        if not text:
            return
        if len(text) > 4:
            threading.Thread(
                target=_update_memory_async,
                args=(text, ""),
                daemon=True
            ).start()
        self.ui.set_state("THINKING")
        try:
            stop_native_speech()
        except Exception:
            pass
        # allow plugins to handle the incoming text command first
        try:
            pm = getattr(self, "plugin_manager", None)
            if pm is not None:
                handled = pm.dispatch("on_text_command", text, source)
                if handled:
                    return
        except Exception:
            pass
        if self._reply_mode:
            if self._handle_pending_reply(text):
                return
            # Still in reply mode but no pending reply event means reset and continue
            self._reply_mode = False

        if getattr(self, "_ig_reply_mode", False):
            if self._handle_ig_reply_flow(text):
                return

        if getattr(self, "_email_mode", False):
            if self._handle_email_flow(text):
                return

        # Direct verbal toggle for Air-Gapped Offline Mode
        lower = text.lower().strip()
        if any(p in lower for p in ("switch to offline mode", "go offline", "turn on offline mode", "enable offline mode", "air gap mode")):
            config_manager.set_setting("offline_mode_enabled", True)
            config_manager.set_setting("default_ai_provider", "Local")
            self.ui.write_log("🔒 SYSTEM: Air-Gapped Offline Mode ENGAGED. All operations running 100% locally.")
            self.speak("Offline mode engaged, sir. Running 100 percent locally on your machine.", proactive=True)
            return

        if any(p in lower for p in ("switch to online mode", "go online", "turn off offline mode", "disable offline mode")):
            config_manager.set_setting("offline_mode_enabled", False)
            config_manager.set_setting("default_ai_provider", "Google Gemini")
            self.ui.write_log("🌐 SYSTEM: Offline Mode DISENGAGED. Cloud connectivity restored.")
            self.speak("Online mode restored, sir. Cloud connectivity is active.", proactive=True)
            return

        # Check for email command initiation
        lower = text.lower()
        skill_goal = _extract_skill_creation_goal(text)
        if skill_goal is not None:
            if not skill_goal:
                prompt = "What should the new skill or feature do?"
                self.ui.write_log(f"Brahma Evo: {prompt}")
                self.speak(prompt)
                return

            try:
                self.ui.begin_task_workspace(
                    text,
                    ["Generate the requested feature", "Validate it in the skill sandbox", "Register it for future use"],
                    source=source or "local",
                )
            except Exception:
                pass

            self.speak("Starting feature creation now.")
            threading.Thread(
                target=self._forge_skill,
                args=(skill_goal,),
                daemon=True,
                name="skill-forge",
            ).start()
            return

        # Check for dynamic/evolved skill execution (e.g. "run the skill", "system status", "show cpu and ram")
        try:
            from core.dynamic_registry import DynamicToolRegistry
            matched = DynamicToolRegistry.find_matching_skill(text)
            if matched:
                skill_name, skill_args = matched
                self.speak(f"Running {skill_name.replace('_', ' ')}.")
                def _run_dynamic_feature():
                    try:
                        res = DynamicToolRegistry.execute_sync(skill_name, skill_args or {})
                        out_text = ""
                        if isinstance(res, dict):
                            out_text = str(res.get("summary") or res.get("output") or res.get("text") or res).strip()
                        else:
                            out_text = str(res).strip()
                        self.ui.write_log(f"Brahma Evo [{skill_name}]:\n{out_text}")
                        if hasattr(self.ui, "finish_task_workspace"):
                            self.ui.finish_task_workspace(out_text, f"{skill_name} completed.", 100)
                        if hasattr(self.ui, "show_hud_deliverable"):
                            self.ui.show_hud_deliverable(
                                title=f"{skill_name.upper()} DELIVERABLE",
                                summary=out_text,
                                kind=skill_name
                            )
                        # Speak first line as voice response
                        first_line = out_text.strip().splitlines()[0] if out_text.strip() else "Task completed."
                        if len(first_line) > 120:
                            first_line = first_line[:115] + "..."
                        self.speak(first_line)
                    except Exception as err:
                        self.ui.write_log(f"ERR: {skill_name} failed: {err}")
                        self.speak(f"Error running {skill_name}: {err}")
                threading.Thread(target=_run_dynamic_feature, daemon=True, name=f"skill-{skill_name}").start()
                return
        except Exception as e_dyn:
            print(f"[Registry] Match error: {e_dyn}")

        if lower.startswith("email ") or lower.startswith("mail ") or "send email" in lower or "write email" in lower or "compose email" in lower:
            # Extract recipient
            rem = text
            for prefix in ("send email", "send an email", "write email", "write an email", "compose email", "compose an email", "email", "mail"):
                if rem.lower().strip().startswith(prefix):
                    rem = rem.strip()[len(prefix):].strip()
                    break
            
            # Remove leading "to " if present
            if rem.lower().startswith("to "):
                rem = rem[3:].strip()
                
            recipient = rem.strip()
            self._email_mode = True
            self._email_recipient = recipient
            
            try:
                self.ui.begin_task_workspace(
                    text,
                    [
                        "Identify the recipient",
                        "Select email application",
                        "Collect message content",
                        "Open application & compose",
                    ],
                    source=source or "local",
                )
            except Exception:
                pass
                
            if not recipient:
                self._email_step = 0
                prompt = "Who would you like to send the email to?"
                self.ui.write_log(f"Brahma Evo: {prompt}")
                self.speak(prompt)
                try:
                    self.ui.update_task_workspace(
                        status="Identifying recipient",
                        output="Asking for email recipient...",
                        percent=10,
                    )
                except Exception:
                    pass
            else:
                self._email_step = 1
                prompt = "Which email app would you like to use? (Gmail, default mail app, etc.)"
                self.ui.write_log(f"Brahma Evo: {prompt}")
                self.speak(prompt)
                try:
                    self.ui.update_task_workspace(
                        status="Selecting email app",
                        output=f"Recipient identified: {recipient}. Asking for email application...",
                        percent=25,
                    )
                except Exception:
                    pass
            return

        if _looks_like_daily_briefing_request(text):
            try:
                self.ui.begin_task_workspace(
                    text,
                    ["Collecting weather, schedule, inbox, and headlines", "Delivering your briefing"],
                    source=source or "local",
                )
            except Exception:
                pass
            self.speak("Preparing your daily briefing.")

            def _run_daily_briefing():
                try:
                    from actions.daily_briefing import daily_briefing
                    daily_briefing(parameters={"category": "all"}, player=self.ui, speak=self.speak)
                except Exception as exc:
                    message = f"Daily briefing failed: {exc}"
                    try:
                        self.ui.write_log(f"ERR: {message}")
                    except Exception:
                        pass
                    self.speak(message)

            threading.Thread(
                target=_run_daily_briefing,
                daemon=True,
                name="daily-briefing",
            ).start()
            return

        try:
            from smart_home.smart_device_manager import SmartDeviceManager
            sd_mgr = SmartDeviceManager()
            devices = self._smart_home.list_devices()
            routed_text_home = sd_mgr.route_command(text, devices)
            if routed_text_home != text:
                print(f"[BRAHMA EVO] Redirection: '{text}' -> '{routed_text_home}'")
                text = routed_text_home
        except Exception as e:
            print(f"[BRAHMA EVO] Redirection error: {e}")

        developer_settings = self.ui._load_app_settings() if hasattr(self.ui, "_load_app_settings") else {}
        developer_workspace = str(developer_settings.get("developer_mode_workspace", "")).strip()
        if not developer_workspace:
            developer_workspace = str(Path.home() / "Desktop" / "BrahmaProjects")
            Path(developer_workspace).mkdir(parents=True, exist_ok=True)

        presentation_request = _looks_like_presentation_request(text)
        spreadsheet_request = _looks_like_spreadsheet_request(text)

        if presentation_request:
            self.speak("Designing your presentation slides...")
            if hasattr(self.ui, "begin_task_workspace"):
                self.ui.begin_task_workspace(
                    text,
                    ["Researching topic", "Outlining slides", "Applying typography & theme", "Generating PowerPoint deck"],
                    source=source or "local"
                )

            def _run_presentation():
                try:
                    from actions.office_generator import generate_presentation_from_prompt
                    res = generate_presentation_from_prompt(text, player=self.ui, speak=self.speak)
                    self.ui.write_log(f"[BrahmaOffice] {res}")
                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Presentation Completed", output=res, percent=100)
                    self.speak("Your presentation has been created and saved to Desktop, sir.")
                except Exception as exc:
                    self.ui.write_log(f"ERR: Presentation generation failed: {exc}")
                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Generation Failed", output=str(exc), percent=0)
                    self.speak("There was an issue creating the presentation, sir. Please check the logs.")

            threading.Thread(target=_run_presentation, daemon=True).start()
            return

        if spreadsheet_request:
            self.speak("Building your spreadsheet workbook...")
            if hasattr(self.ui, "begin_task_workspace"):
                self.ui.begin_task_workspace(
                    text,
                    ["Analyzing data structure", "Defining headers & formulas", "Formatting workbook", "Saving Excel spreadsheet"],
                    source=source or "local"
                )

            def _run_spreadsheet():
                try:
                    from actions.office_generator import generate_spreadsheet_from_prompt
                    res = generate_spreadsheet_from_prompt(text, player=self.ui, speak=self.speak)
                    self.ui.write_log(f"[BrahmaOffice] {res}")
                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Spreadsheet Completed", output=res, percent=100)
                    self.speak("Your spreadsheet workbook has been created and saved to Desktop, sir.")
                except Exception as exc:
                    self.ui.write_log(f"ERR: Spreadsheet generation failed: {exc}")
                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Generation Failed", output=str(exc), percent=0)
                    self.speak("There was an issue creating the spreadsheet, sir. Please check the logs.")

            threading.Thread(target=_run_spreadsheet, daemon=True).start()
            return

        website_request = _looks_like_website_request(text)
        code_request = (not presentation_request and not spreadsheet_request) and _looks_like_code_request(text) and any(w in text.lower() for w in ("app", "website", "web", "program", "script", "project", "game", "calc", "html", "react"))

        if website_request or code_request:
            self.speak("Working on your project with Brahma Dev...")
            if hasattr(self.ui, "begin_task_workspace"):
                self.ui.begin_task_workspace(text, ["Analyzing specifications", "Scaffolding files", "Writing code", "Verifying build"], source=source or "local")

            def _run_brahma_dev():
                try:
                    import webbrowser
                    from actions.brahma_dev_agent import run_dev_agent
                    res = run_dev_agent({
                        "description": text,
                        "workspace_path": developer_workspace
                    }, speak=self.speak)
                    self.ui.write_log(f"[BrahmaDev] {res[:400]}")

                    if "encountered an error during inference" in res or "LLM error:" in res:
                        if hasattr(self.ui, "update_task_workspace"):
                            self.ui.update_task_workspace(status="Build Error", output=res, percent=0)
                        self.speak("There was an error building the project, sir. Please check the logs.")
                        return

                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Project Completed", output=res, percent=100)

                    folder_display = developer_workspace
                    try:
                        folder_display = Path(developer_workspace).name or developer_workspace
                    except Exception:
                        pass

                    self.speak(f"Your project has been completed and saved to {folder_display}, sir.")

                    # Auto-open index.html in browser if created
                    try:
                        idx_path = Path(developer_workspace) / "index.html"
                        if idx_path.exists():
                            webbrowser.open(str(idx_path.resolve()))
                    except Exception:
                        pass
                except Exception as exc:
                    self.ui.write_log(f"ERR: Brahma Dev failed: {exc}")
                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Build Failed", output=str(exc), percent=0)
                    self.speak("There was an issue building the project, sir. Please check the logs.")

            threading.Thread(target=_run_brahma_dev, daemon=True).start()
            return

        # Google Workspace Direct Command Handling (Gmail & Calendar checks)
        lower_cmd = text.lower().strip()
        is_email_check = any(phrase in lower_cmd for phrase in ("check email", "check my email", "unread email", "read email", "my inbox", "check inbox", "show emails"))
        is_calendar_check = any(phrase in lower_cmd for phrase in ("my schedule", "what's on my calendar", "check calendar", "my meetings", "schedule today", "events today"))

        if is_email_check:
            self.speak("Checking your Gmail inbox, sir...")
            if hasattr(self.ui, "begin_task_workspace"):
                self.ui.begin_task_workspace(
                    text,
                    ["Connecting to Gmail", "Fetching unread messages", "Parsing headers & body", "Summarizing inbox"],
                    source=source or "local"
                )
            def _run_gw_email():
                try:
                    from actions.google_workspace_mcp import google_workspace
                    res = google_workspace({"service": "gmail", "action": "unread"}, player=self.ui, speak=self.speak)
                    self.ui.write_log(f"[GoogleWorkspace] {res}")
                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Email Check Completed", output=res, percent=100)
                    self.speak("Here are your latest emails, sir.")
                except Exception as exc:
                    self.ui.write_log(f"ERR: Gmail check failed: {exc}")
                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Email Check Failed", output=str(exc), percent=0)
                    self.speak("Unable to check emails. Please ensure your credentials are set up in Settings.")
            threading.Thread(target=_run_gw_email, daemon=True).start()
            return

        if is_calendar_check:
            self.speak("Checking your calendar schedule...")
            if hasattr(self.ui, "begin_task_workspace"):
                self.ui.begin_task_workspace(
                    text,
                    ["Connecting to Calendar", "Retrieving scheduled events", "Formatting timeline"],
                    source=source or "local"
                )
            def _run_gw_calendar():
                try:
                    from actions.google_workspace_mcp import google_workspace
                    res = google_workspace({"service": "calendar", "action": "list"}, player=self.ui, speak=self.speak)
                    self.ui.write_log(f"[GoogleWorkspace] {res}")
                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Schedule Retrieved", output=res, percent=100)
                    self.speak("Here is your schedule, sir.")
                except Exception as exc:
                    self.ui.write_log(f"ERR: Calendar check failed: {exc}")
                    if hasattr(self.ui, "update_task_workspace"):
                        self.ui.update_task_workspace(status="Schedule Check Failed", output=str(exc), percent=0)
                    self.speak("There was an issue checking your schedule, sir.")
            threading.Thread(target=_run_gw_calendar, daemon=True).start()
            return

        # OS Hardware & Diagnostics Direct Command Handling (0 background API credits)
        is_ram_check = any(p in lower_cmd for p in ("eating my ram", "ram hogs", "memory hogs", "check ram", "ram usage", "memory usage", "who is using ram"))
        is_battery_check = any(p in lower_cmd for p in ("battery health", "check battery", "battery status", "battery percentage", "is laptop charging", "how much battery"))
        is_brightness_req = any(p in lower_cmd for p in ("dim screen", "dim monitor", "increase brightness", "lower brightness", "set brightness", "screen brightness"))
        is_kill_req = (lower_cmd.startswith("kill ") or lower_cmd.startswith("terminate ") or lower_cmd.startswith("force kill ")) and len(lower_cmd.split()) <= 4
        is_diag_req = any(p in lower_cmd for p in ("system status", "system diagnostics", "hardware status", "computer vitals", "pc diagnostics"))

        if is_ram_check:
            self.speak("Scanning memory consumers...")
            def _run_ram():
                from actions.system_diagnostics_mcp import system_diagnostics
                res = system_diagnostics({"action": "ram_hogs"}, player=self.ui, speak=self.speak)
                self.ui.write_log(f"[Diagnostics] {res}")
                if hasattr(self.ui, "finish_task_workspace"):
                    self.ui.finish_task_workspace(res, "Memory analysis complete.", 100)
            threading.Thread(target=_run_ram, daemon=True).start()
            return

        if is_battery_check:
            def _run_battery():
                from actions.system_diagnostics_mcp import system_diagnostics
                res = system_diagnostics({"action": "battery"}, player=self.ui, speak=self.speak)
                self.ui.write_log(f"[Diagnostics] {res}")
                if hasattr(self.ui, "finish_task_workspace"):
                    self.ui.finish_task_workspace(res, "Battery check complete.", 100)
            threading.Thread(target=_run_battery, daemon=True).start()
            return

        if is_brightness_req:
            import re
            m = re.search(r"(\d+)", lower_cmd)
            lvl = int(m.group(1)) if m else None
            rel = "dim" in lower_cmd or "lower" in lower_cmd or "increase" in lower_cmd or "boost" in lower_cmd
            if rel and lvl is None:
                lvl = -20 if ("dim" in lower_cmd or "lower" in lower_cmd) else 20
            def _run_brightness():
                from actions.system_diagnostics_mcp import system_diagnostics
                p = {"action": "brightness"}
                if lvl is not None:
                    p["level"] = lvl
                    p["relative"] = rel
                res = system_diagnostics(p, player=self.ui, speak=self.speak)
                self.ui.write_log(f"[Diagnostics] {res}")
                if hasattr(self.ui, "finish_task_workspace"):
                    self.ui.finish_task_workspace(res, "Brightness adjusted.", 100)
            threading.Thread(target=_run_brightness, daemon=True).start()
            return

        if is_kill_req:
            target_app = lower_cmd.split(None, 1)[1].strip()
            def _run_kill():
                from actions.system_diagnostics_mcp import system_diagnostics
                res = system_diagnostics({"action": "kill", "target": target_app, "force": "force" in lower_cmd}, player=self.ui, speak=self.speak)
                self.ui.write_log(f"[Diagnostics] {res}")
                if hasattr(self.ui, "finish_task_workspace"):
                    self.ui.finish_task_workspace(res, "Process management complete.", 100)
            threading.Thread(target=_run_kill, daemon=True).start()
            return

        if is_diag_req:
            self.speak("Retrieving system diagnostics report...")
            def _run_diag():
                from actions.system_diagnostics_mcp import system_diagnostics
                res = system_diagnostics({"action": "status"}, player=self.ui, speak=self.speak)
                self.ui.write_log(f"[Diagnostics] {res}")
                if hasattr(self.ui, "finish_task_workspace"):
                    self.ui.finish_task_workspace(res, "System report ready.", 100)
            threading.Thread(target=_run_diag, daemon=True).start()
            return

        # Autonomous Self-Healing & Continuous Learning Fast-Path
        is_trigger_bug = any(p in lower_cmd for p in ("trigger test bug", "simulate bug", "test bug", "create bug", "simulate error", "trigger error", "break test"))
        is_heal_cmd = any(p in lower_cmd for p in ("fix that bug", "fix the bug", "heal yourself", "auto heal", "patch yourself", "fix error", "fix this error"))
        is_rollback_cmd = any(p in lower_cmd for p in ("undo last patch", "rollback patch", "revert patch", "undo patch"))
        is_patch_history = any(p in lower_cmd for p in ("patch history", "patch log", "show patches", "auto heal status"))
        is_learn_rule = (
            lower_cmd.startswith("remember to ") or 
            lower_cmd.startswith("remember that ") or 
            (lower_cmd.startswith("always ") and len(lower_cmd.split()) > 2 and not lower_cmd.startswith("always open")) or
            (lower_cmd.startswith("never ") and len(lower_cmd.split()) > 2) or
            "new rule:" in lower_cmd
        )

        if is_trigger_bug:
            def _run_test_bug():
                try:
                    from actions.test_action import test_action
                    res = test_action({}, player=self.ui, speak=self.speak)
                    self.ui.write_log(f"[TestAction] {res}")
                    self.speak(f"Test action succeeded with no errors: {res}. The bug appears already healed!")
                except Exception as exc:
                    import traceback
                    from actions.auto_heal_engine import AutoHealEngine
                    tb_str = traceback.format_exc()
                    AutoHealEngine.record_last_error(tb_str)
                    err_msg = f"Simulated bug triggered in test_action.py: {type(exc).__name__}. Traceback captured! You can now say 'Brahma, fix that bug'."
                    self.speak(err_msg)
                    self.ui.write_log(f"[AutoHeal Test] {err_msg}")
                    if hasattr(self.ui, "finish_task_workspace"):
                        self.ui.finish_task_workspace(tb_str, "Simulated bug captured.", 100)
            threading.Thread(target=_run_test_bug, daemon=True).start()
            return

        if is_heal_cmd:
            self.speak("Analyzing last captured traceback and synthesizing hotfix...")
            def _run_heal():
                from actions.auto_heal_engine import auto_heal
                res = auto_heal({"action": "heal"}, player=self.ui, speak=self.speak)
                self.ui.write_log(f"[AutoHeal] {res}")
                if hasattr(self.ui, "finish_task_workspace"):
                    self.ui.finish_task_workspace(res, "Self-patching complete.", 100)
            threading.Thread(target=_run_heal, daemon=True).start()
            return

        if is_rollback_cmd:
            def _run_rollback():
                from actions.auto_heal_engine import auto_heal
                res = auto_heal({"action": "rollback"}, player=self.ui, speak=self.speak)
                self.ui.write_log(f"[AutoHeal] {res}")
                if hasattr(self.ui, "finish_task_workspace"):
                    self.ui.finish_task_workspace(res, "Rollback complete.", 100)
            threading.Thread(target=_run_rollback, daemon=True).start()
            return

        if is_patch_history:
            def _run_history():
                from actions.auto_heal_engine import auto_heal
                action_type = "status" if "status" in lower_cmd else "history"
                res = auto_heal({"action": action_type}, player=self.ui, speak=self.speak)
                self.ui.write_log(f"[AutoHeal] {res}")
                if hasattr(self.ui, "finish_task_workspace"):
                    self.ui.finish_task_workspace(res, "Auto-heal report ready.", 100)
            threading.Thread(target=_run_history, daemon=True).start()
            return

        if is_learn_rule:
            rule_raw = lower_cmd.replace("remember that ", "").replace("remember to ", "").replace("new rule: ", "").strip()
            def _run_learn():
                from core.learned_rules import LearnedRulesEngine
                res = LearnedRulesEngine.add_rule(rule_raw, origin="voice_directive")
                msg = res.get("message", f"Understood. I have committed '{rule_raw}' to my continuous memory.")
                self.speak(msg)
                self.ui.write_log(f"[LearnedRules] {msg}")
                if hasattr(self.ui, "finish_task_workspace"):
                    self.ui.finish_task_workspace(msg, "Rule learned.", 100)
            threading.Thread(target=_run_learn, daemon=True).start()
            return

        # Local PC Volume Control (Speakers)
        is_volume_cmd = any(p in lower_cmd for p in ("volume up", "volume down", "mute volume", "mute audio", "unmute", "set volume", "increase volume", "decrease volume", "lower volume")) or (lower_cmd in ("mute", "unmute", "volume max"))
        if is_volume_cmd and not any(m in lower_cmd for m in ("phone", "mobile", "android", "tablet")):
            def _run_volume():
                from actions.computer_settings import volume_up, volume_down, volume_mute, volume_set
                import re
                if any(w in lower_cmd for w in ("up", "increase", "higher", "raise")):
                    volume_up()
                    self.speak("Volume increased.")
                elif any(w in lower_cmd for w in ("down", "decrease", "lower")):
                    volume_down()
                    self.speak("Volume decreased.")
                elif "unmute" in lower_cmd or "mute" in lower_cmd:
                    volume_mute()
                    self.speak("Audio toggled.")
                else:
                    m = re.search(r"(\d+)", lower_cmd)
                    if m:
                        val = int(m.group(1))
                        volume_set(val)
                        self.speak(f"Volume set to {val} percent.")
                    else:
                        volume_up()
                        self.speak("Volume adjusted.")
            threading.Thread(target=_run_volume, daemon=True).start()
            return

        # Local PC App Launcher (runs on computer unless explicitly targeted to phone)
        has_mobile_target = any(m in lower_cmd for m in ("on phone", "on my phone", "on mobile", "on my mobile", "on android", "on tablet"))
        is_open_app_cmd = any(lower_cmd.startswith(prefix) for prefix in ("open app ", "open ", "launch app ", "launch ", "start app ", "start ")) and not has_mobile_target and not any(p in lower_cmd for p in ("website", "url", "http", "presentation", "sheet", "spreadsheet", "project", "code", "game"))
        if is_open_app_cmd:
            from actions.open_app import open_app, _APP_ALIASES
            clean_app_candidate = lower_cmd
            for prefix in ("open app ", "open ", "launch app ", "launch ", "start app ", "start "):
                if clean_app_candidate.startswith(prefix):
                    clean_app_candidate = clean_app_candidate[len(prefix):].strip()
                    break
            if clean_app_candidate in _APP_ALIASES or any(clean_app_candidate in k for k in _APP_ALIASES):
                def _run_open_local_app():
                    open_app({"app_name": clean_app_candidate}, player=self.ui)
                    self.speak(f"Opening {clean_app_candidate}, sir.")
                threading.Thread(target=_run_open_local_app, daemon=True).start()
                return

        memory_ctx = _memory_context_for_request(text)
        routed_text = f"{memory_ctx}\n\nCurrent User Request:\n{text}" if memory_ctx else text
        if source == "instagram":
            routed_text = f"Owner sent this via Instagram DM: {text}\n(SYSTEM: If this is an action like opening an app or running a command, you MUST execute it using your tools rather than just replying with text.)"
        if text.lower() in {"stop meeting mode", "end meeting mode", "close meeting mode"}:
            self._stop_meeting_mode("Meeting mode closed.")
            return
        if self._handle_attention_response(text):
            return
        try:
            self.ui.begin_task_workspace(text, _build_task_plan(text), source=source or "local")
        except Exception:
            pass
        if source != "instagram" and self._handle_brahma_connect_command(text, source=source or "local"):
            return
        if self._handle_smart_home_command(text, source=source or "local"):
            return
        if _looks_like_screen_request(text):
            try:
                self.ui.update_task_workspace(
                    status="Scanning screen",
                    output="Brahma Evo is inspecting the screen for what you asked about.",
                    percent=40,
                )
            except Exception:
                pass
            print("[Main] Screen analysis request received")
            img_bytes = None
            if hasattr(self.ui, "capture_screen_bytes"):
                print("[Main] Capturing screenshot from UI")
                img_bytes = self.ui.capture_screen_bytes()
                print(f"[Main] UI screenshot capture returned {len(img_bytes) if img_bytes is not None else 'None'} bytes")
            else:
                print("[Main] UI object has no capture_screen_bytes method")

            def _run_screen_process():
                print("[Main] Starting screen_process thread")
                success = screen_process(
                    parameters={"angle": "screen", "text": text},
                    response=None,
                    player=self.ui,
                    session_memory=None,
                    image_bytes=img_bytes,
                )
                print(f"[Main] screen_process finished: {success}")
                if not success:
                    try:
                        self.ui.update_task_workspace(
                            status="Screen analysis failed",
                            output=(
                                "Screen analysis could not complete. "
                                "Check your internet connection, API key, or screen capture permissions."
                            ),
                            percent=0,
                        )
                    except Exception:
                        pass

            threading.Thread(target=_run_screen_process, daemon=True).start()
            return
        # Route directly to Local Brain if preferred by user in settings or in air-gapped offline mode
        app_settings = config_manager.load_settings()
        configured_provider = app_settings.get("default_ai_provider", "Gemini")
        is_offline_mode = bool(app_settings.get("offline_mode_enabled", False))

        # If offline mode or Local provider selected, route directly to Local Brain
        if is_offline_mode or configured_provider == "Local":
            is_local_preferred = True
        else:
            is_local_preferred = False

        if is_local_preferred or self._prefer_openrouter() or not self._loop or not self.session:
            threading.Thread(target=self._fallback_reply, args=(text, memory_ctx), daemon=True).start()
            return
        self.ui.set_state("THINKING")
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": routed_text}]},
                turn_complete=True
            ),
            self._loop
        )


    def _handle_smart_home_command(self, text: str, source: str = "local") -> bool:
        normalized = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s%]", " ", text.lower())).strip()
        
        # Don't touch commands targeted at phone or mobile
        mobile_words = (
            "phone", "mobile", "android", "tablet", "brahma connect",
            "my phone", "my mobile", "my tablet", "my android", "flashlight", "torch"
        )
        if any(word in normalized for word in mobile_words):
            return False

        # Don't hijack PC screen/monitor brightness
        if any(w in normalized for w in ("screen", "monitor", "display", "laptop", "pc")) and "brightness" in normalized:
            return False

        smart_home_words = (
            "fan", "fans", "light", "lights", "lamp", "plug", "socket", "outlet", "bulb",
            "kasa", "atomberg", "bedroom", "living room", "kitchen", "office room",
            "balcony", "bathroom", "hall", "dining", "smart home", "smart-home",
            "ac", "air conditioner", "thermostat"
        )
        has_smart_word = any(word in normalized for word in smart_home_words)
        if not has_smart_word:
            try:
                for d in self._smart_home.list_devices():
                    d_name = str(d.get("name", "")).lower()
                    d_room = str(d.get("room", "")).lower()
                    if (d_name and d_name in normalized) or (d_room and d_room in normalized):
                        has_smart_word = True
                        break
            except Exception:
                pass

        if not has_smart_word:
            return False
        try:
            result = self._smart_home.execute_command(text)
            detail = str(result.get("detail") or "Smart-home command completed.")
            title = f"Smart Home: {result.get('action', 'control')}"
            plan = [
                "Identify the target device or room",
                "Send the command to the smart-home provider",
                "Verify the new state",
                "Report the result",
            ]
            self.ui.update_task_workspace(
                title=title,
                command=text,
                plan=plan,
                status="Executing smart-home command",
                output=detail,
                percent=100,
                source=source,
            )
            self.ui.write_log(f"Brahma Evo: {detail}")
            self.speak(detail)
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return True
        except Exception as exc:
            message = f"I couldn't control the smart home device: {exc}"
            self.ui.write_log(f"ERR: {message}")
            self.ui.update_task_workspace(
                title="Smart Home Control",
                command=text,
                plan=[
                    "Identify the target device or room",
                    "Send the command to the smart-home provider",
                    "Verify the new state",
                ],
                status="Smart-home command failed",
                output=message,
                percent=100,
                source=source,
            )
            self.speak(message)
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return True

    def _extract_launch_app_name(self, text: str) -> str:
        normalized = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s%]", " ", text.lower())).strip()
        if not normalized:
            return ""

        prefixes = (
            "launch app ",
            "open app ",
            "start app ",
            "open the app ",
            "launch the app ",
            "start the app ",
            "open ",
            "launch ",
            "start ",
            "run ",
            "bring up ",
        )

        candidate = normalized
        for prefix in prefixes:
            if candidate.startswith(prefix):
                candidate = candidate[len(prefix):]
                break

        candidate = re.sub(r"\s+(?:on|in|to)\s+(?:my\s+)?(?:phone|mobile|tablet|android|device)\b.*$", "", candidate).strip()
        candidate = re.sub(r"\b(?:app|application|please|the)\b", " ", candidate).strip()
        candidate = re.sub(r"\s+", " ", candidate)
        return candidate

    def _handle_brahma_connect_command(self, text: str, source: str = "local") -> bool:
        normalized = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s%]", " ", text.lower())).strip()
        
        # Explicit mobile indicators: only route to phone if user explicitly mentions phone/mobile
        # or asks for a phone-exclusive action (flashlight/torch, ring phone).
        is_flashlight = "flashlight" in normalized or "torch" in normalized
        is_ring_phone = any(p in normalized for p in ("ring phone", "ring my phone", "find my phone", "find phone", "locate phone"))
        has_phone_ref = any(token in normalized for token in (
            "phone", "mobile", "android", "tablet", "brahma connect",
            "my phone", "my mobile", "my tablet", "my android"
        ))

        # Never hijack local PC commands (e.g. open chrome, volume up, pc battery) unless user says 'on phone'
        if not (has_phone_ref or is_flashlight or is_ring_phone):
            return False

        try:
            devices_json = connect_list_devices(parameters={}, player=self.ui)
            devices = json.loads(devices_json).get("devices", [])
        except Exception:
            devices = []

        if not devices:
            return False

        try:
            target = ""
            for device in devices:
                name = str(device.get("name", "")).lower()
                device_id = str(device.get("device_id", "")).lower()
                platform = str(device.get("platform", "")).lower()
                if any(token in normalized for token in (name, device_id, platform, "phone", "mobile", "android", "tablet")):
                    target = str(device.get("device_id") or device.get("name") or "").strip()
                    break
            if not target and (has_phone_ref or is_flashlight or is_ring_phone) and len(devices) == 1:
                target = str(devices[0].get("device_id") or devices[0].get("name") or "").strip()
            if not target:
                return False

            action = None
            params: dict[str, object] = {}
            
            # Check for complex multi-step mobile workflow first
            # Examples: "open X and do Y", "search for Z on X", "message X on Y"
            if (" and " in normalized or " search " in normalized or " play " in normalized or " message " in normalized or " tell " in normalized) and ("open " in normalized or "launch " in normalized or "app" in normalized):
                action = "mobile_autopilot"
                params["instruction"] = text
            elif "flashlight" in normalized and ("turn on" in normalized or "switch on" in normalized or "power on" in normalized or "on" == normalized):
                action = "flashlight_on"
            elif "flashlight" in normalized and ("turn off" in normalized or "switch off" in normalized or "power off" in normalized or "off" == normalized):
                action = "flashlight_off"
            elif "battery" in normalized or "charge" in normalized:
                action = "get_battery"
            elif "open url" in normalized or "open website" in normalized or "go to" in normalized:
                action = "open_url"
            elif any(word in normalized for word in ("launch app", "open app", "start app", "open ", "launch ", "start ", "run ", "bring up ")):
                app_name = self._extract_launch_app_name(text)
                if app_name:
                    action = "launch_app"
                    params["app_name"] = app_name
            elif "volume" in normalized and ("set" in normalized or "change" in normalized or "to " in normalized):
                action = "volume_set"
                match = re.search(r"\b(\d{1,3})\b", normalized)
                if match:
                    params["value"] = int(match.group(1))
            elif "volume" in normalized:
                action = "volume_get"
            elif "info" in normalized or "status" in normalized:
                action = "get_device_info"
                
            if not action:
                return False

            if action == "mobile_autopilot":
                # Route complex commands to the autopilot loop
                def _run_autopilot():
                    try:
                        from actions.mobile_autopilot import mobile_autopilot
                        result_json = mobile_autopilot({"target": target, "instruction": params["instruction"]}, player=self.ui, speak=self.speak)
                        print(f"[Autopilot Result]: {result_json}")
                        try:
                            result_dict = json.loads(result_json)
                            if not result_dict.get("success"):
                                print(f"[Autopilot Error]: {result_dict.get('error') or result_dict}")
                                self.speak(f"Autopilot encountered an error: {result_dict.get('error', 'unknown error')}")
                        except Exception:
                            pass
                    except Exception as e:
                        print(f"Autopilot failed: {e}")
                    finally:
                        try:
                            self.ui.set_state("IDLE")
                        except Exception:
                            pass
                threading.Thread(target=_run_autopilot, daemon=True).start()
                return True

            if action == "launch_app":
                app_name = str(params.get("app_name") or "").strip()
                if not app_name:
                    return False
            if action == "open_url":
                m = re.search(r"(https?://\S+)", text, re.IGNORECASE)
                if m:
                    params["url"] = m.group(1)
                else:
                    return False

            payload = {
                "device": target,
                "action": action,
                "parameters": params,
            }
            result_json = connect_execute(parameters=payload, player=self.ui)
            result = json.loads(result_json)
            if result.get("success", False):
                detail = str(result.get("detail") or result.get("error") or "Device command completed.")
                title = f"Brahma Connect: {action}"
                self.ui.update_task_workspace(
                    title=title,
                    command=text,
                    plan=[
                        "Identify the paired phone or device",
                        "Route the command through Brahma Connect",
                        "Verify the device response",
                        "Report the result",
                    ],
                    status="Executing device command",
                    output=detail,
                    percent=100,
                    source=source,
                )
                self.ui.write_log(f"Brahma Evo: {detail}")
                self.speak(detail)
                if not self.ui.muted:
                    self.ui.set_state("LISTENING")
                return True

            self.ui.write_log(f"ERR: Brahma Connect command failed: {result.get('error') or 'Unknown error'}")
            return False
        except Exception:
            return False

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

    def _connect_tool_voice(self, name: str, raw_result: str) -> str | None:
        return self._session_lifecycle._connect_tool_voice(name, raw_result)

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
        """Hand one already-stripped emotion tag to the UI and the avatar seam.

        Shared by the fallback reply path and the live voice path. The live path
        previously never parsed tags at all, so in the default voice mode a tag
        the model emitted reached nothing: the reply path knows nothing about
        which backend is selected, core.avatar_runtime decides whether the tag
        reaches a renderer, and this never raises.
        """
        if not emotion:
            return
        print(f"[BRAHMA EVO] 🎭 Emotion tag: {emotion}")
        try:
            if hasattr(self.ui, "set_emotion_tag"):
                self.ui.set_emotion_tag(emotion)
        except Exception:
            pass
        try:
            from core import avatar_runtime

            record = avatar_runtime.handle_emotion_tag(emotion)
            if record.get("delivered"):
                print(f"[BRAHMA EVO] 🎭 Avatar: {record['tag']} -> "
                      f"{record['backend']}")
        except Exception:
            pass

    def _fallback_reply(self, text: str, memory_ctx: str = ""):
        try:
            self.ui.set_state("THINKING")
            try:
                self.ui.update_task_workspace(
                    status="Thinking",
                    output="Brahma Evo is drafting a direct reply.",
                    percent=35,
                )
            except Exception:
                pass
            reply = ""
            gemini_first = not self._prefer_openrouter()
            request_text = f"{memory_ctx}\n\nCurrent User Request:\n{text}" if memory_ctx else text

            app_settings = config_manager.load_settings()
            configured_provider = app_settings.get("default_ai_provider", "Gemini")
            local_model_target = app_settings.get("local_ai_model", "qwen2.5:3b")
            is_offline_mode = app_settings.get("offline_mode_enabled", False)

            is_cloud_gemini = configured_provider in ("Gemini", "Google Gemini")
            is_cloud_openrouter = configured_provider == "OpenRouter"

            # Vendor-agnostic failover order. Each link below is gated on being
            # enabled, so a provider switched off in Settings is never called --
            # that is what stops a background thread from silently walking the
            # OpenRouter free-model pool on a CPU already busy with a local
            # model. Logged so the active chain is visible when it misbehaves.
            active_chain = ["gemini", "openrouter", "local", "custom"]
            disabled = []
            try:
                from core import provider_router

                active_chain = provider_router.chain()
                disabled = [p["id"] for p in provider_router.all_providers() if not p["enabled"]]
                if disabled:
                    print(
                        f"[BRAHMA EVO] Provider chain: {provider_router.describe()} "
                        f"(disabled: {', '.join(disabled)})"
                    )
            except Exception:
                pass

            # 1. If user explicitly selected Google Gemini, run Gemini FIRST
            if is_cloud_gemini and not is_offline_mode and "gemini" in active_chain:
                try:
                    self.ui.update_task_workspace(
                        status="Thinking (Gemini)",
                        output="Processing on Google Gemini...",
                        percent=50,
                    )
                    reply = _gemini_text_reply(request_text)
                    print("[BRAHMA EVO] 🌐 Google Gemini answered successfully!")
                except Exception as e_gem:
                    print(f"[BRAHMA EVO] ⚠️ Gemini failed: {e_gem}")
                    if _is_gemini_limit_error(e_gem):
                        self._throttled_to_openrouter()

            # 2. If user explicitly selected OpenRouter, run OpenRouter FIRST
            elif is_cloud_openrouter and not is_offline_mode and "openrouter" in active_chain:
                try:
                    self.ui.update_task_workspace(
                        status="Thinking (OpenRouter)",
                        output="Processing on OpenRouter...",
                        percent=50,
                    )
                    reply = openrouter_client.chat(
                        request_text,
                        system=(
                            "You are Brahma Evo, a concise, helpful desktop assistant. "
                            "Reply naturally and briefly. Do not mention internal implementation details."
                            + _injected_instructions("fallback")
                        ),
                    )
                    print("[BRAHMA EVO] 🌐 OpenRouter answered successfully!")
                except Exception as e_or:
                    print(f"[BRAHMA EVO] ⚠️ OpenRouter failed: {e_or}")

            # 3. If user explicitly configured Local AI, is in Offline Mode, or cloud provider failed: run Local Brain
            if (not reply
                    and "local" in active_chain
                    and (configured_provider == "Local" or is_offline_mode or not (is_cloud_gemini or is_cloud_openrouter))
                    and local_brain.is_available()):
                try:
                    self.ui.update_task_workspace(
                        status="Thinking (Local AI)",
                        output=f"Processing on local model ({local_model_target})...",
                        percent=50,
                    )
                    prompt_txt = ""
                    try:
                        if PROMPT_PATH.exists():
                            prompt_txt = PROMPT_PATH.read_text(encoding="utf-8").strip()
                    except Exception:
                        pass
                    if not prompt_txt:
                        prompt_txt = "You are Brahma Evo, the autonomous desktop operating system."

                    system_prompt = (
                        f"{prompt_txt}\n\n"
                        "CRITICAL OPERATING SYSTEM DIRECTIVE:\n"
                        "- You have FULL DIRECT ACCESS and authority over this Windows PC via your tools.\n"
                        "- NEVER state that you are a text-based AI, that you cannot perform automations, or that you lack real-time access.\n"
                        "- Whenever the user requests an action (opening an app, changing volume, setting a reminder, running a protocol, organizing files, searching, etc.), ALWAYS call the corresponding tool.\n"
                        "- Keep responses concise, direct, and conversational."
                    )

                    # Local models get the same bounded, relevance-ranked tool
                    # surface Gemini Live gets. This is what makes MCP tools
                    # reachable here at all: the static TOOL_DECLARATIONS list
                    # contains only native tools, and its size swamps a 3B
                    # context window. Anything not advertised stays reachable
                    # through the search_tools / call_tool meta-tools, and any
                    # usage rules for the advertised groups arrive as JIT context.
                    local_tools, local_skill_ctx = local_brain.resolve_tools(
                        request_text, TOOL_DECLARATIONS
                    )
                    if local_skill_ctx:
                        system_prompt = f"{system_prompt}\n\n{local_skill_ctx}"

                    # Optional instruction blocks for this layer. Opt-in and
                    # per-layer: a layer with nothing registered contributes an
                    # empty string, so this is free when unused.
                    try:
                        from core import instruction_injector

                        injected = instruction_injector.compose("local")
                        if injected:
                            system_prompt = f"{system_prompt}{injected}"
                    except Exception:
                        pass
                    print(
                        f"[BRAHMA EVO] 🧰 Local turn advertising {len(local_tools)} tool(s) "
                        f"for {local_model_target}."
                    )

                    messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": request_text}
                    ]

                    res = local_brain.chat_complete(
                        messages,
                        model=local_model_target,
                        tools=local_tools,
                        focus_core=True
                    )
                    msg = res.get("choices", [{}])[0].get("message", {})
                    tool_calls = msg.get("tool_calls")
                    if tool_calls:
                        for tc in tool_calls:
                            call_id = tc.get("id", "call_local")
                            fn_info = tc.get("function", {})
                            fn_name = fn_info.get("name")
                            fn_args_raw = fn_info.get("arguments", {})
                            if isinstance(fn_args_raw, str):
                                try:
                                    fn_args = json.loads(fn_args_raw)
                                except Exception:
                                    fn_args = {}
                            else:
                                fn_args = fn_args_raw or {}

                            print(f"[BRAHMA EVO] 🔒 Local Brain executing tool: {fn_name}({fn_args})")
                            self.ui.update_task_workspace(
                                status=f"Executing {fn_name}",
                                output=f"Running action: {fn_name} on local machine...",
                                percent=75,
                            )
                            tool_result = self._execute_tool_sync(fn_name, fn_args, call_id)

                            messages.append(msg)
                            messages.append({
                                "role": "tool",
                                "tool_call_id": call_id,
                                "name": fn_name,
                                "content": str(tool_result)
                            })
                            try:
                                followup_res = local_brain.chat_complete(
                                    messages,
                                    model=local_model_target,
                                    temperature=0.3
                                )
                                followup_reply = followup_res.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                                if followup_reply:
                                    reply = followup_reply
                            except Exception as e_fu:
                                print(f"[BRAHMA EVO] ⚠️ Local Brain follow-up failed: {e_fu}")
                                reply = str(tool_result) if tool_result else f"{fn_name.replace('_', ' ').capitalize()} completed."
                    else:
                        reply = msg.get("content", "").strip()
                    print(f"[BRAHMA EVO] 🔒 Local Brain ({local_model_target}) answered successfully!")
                except Exception as e_loc:
                    print(f"[BRAHMA EVO] ⚠️ Local Brain failed: {e_loc}")

            # 4. Fallback cascading: if primary cloud choice failed, try secondary cloud choice
            if not reply and not is_offline_mode:
                if is_cloud_gemini and self._prefer_openrouter() and "openrouter" in active_chain:
                    try:
                        reply = openrouter_client.chat(request_text)
                    except Exception:
                        pass
                elif is_cloud_openrouter and "gemini" in active_chain:
                    try:
                        reply = _gemini_text_reply(request_text)
                    except Exception:
                        pass

            # 4b. Ultimate offline safety net: Local Brain fallback
            if not reply and "local" in active_chain and local_brain.is_available():
                try:
                    res = local_brain.chat_complete([
                        {"role": "system", "content": "You are Brahma Evo, the autonomous desktop operating system. You control this PC. Never claim you cannot do automations."},
                        {"role": "user", "content": request_text}
                    ], model=local_model_target, tools=None, focus_core=False)
                    msg_net = res.get("choices", [{}])[0].get("message", {})
                    tc_net = msg_net.get("tool_calls")
                    if tc_net:
                        call_id = tc_net[0].get("id", "call_local")
                        fn_name = tc_net[0].get("function", {}).get("name")
                        fn_args_raw = tc_net[0].get("function", {}).get("arguments", {})
                        if isinstance(fn_args_raw, str):
                            try:
                                fn_args = json.loads(fn_args_raw)
                            except Exception:
                                fn_args = {}
                        else:
                            fn_args = fn_args_raw or {}
                        reply = self._execute_tool_sync(fn_name, fn_args, call_id)
                    else:
                        reply = msg_net.get("content", "").strip()
                    print(f"[BRAHMA EVO] 🔒 Local Brain offline safety net answered ({local_model_target})!")
                except Exception as e_net:
                    print(f"[BRAHMA EVO] ⚠️ Offline Local Brain fallback failed: {e_net}")
            reply = (reply or "").strip()
            # An emotion tag is an instruction-layer artefact, not speech. Strip
            # it before logging and before TTS so "[SMILE]" is never vocalised,
            # then hand the tag to the shared delivery seam.
            reply, emotion = self._split_emotion_tag(reply)
            self._deliver_avatar_emotion(emotion)
            if not reply:
                reply = "I’m ready, sir."
            self.ui.write_log(f"Brahma Evo: {reply}")
            if not getattr(self.ui, "muted", False):
                self.speak(reply, proactive=True)
            try:
                self.ui.finish_task_workspace(reply, "Reply delivered.", 100)
            except Exception:
                pass
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
        except Exception as e:
            msg = f"Fallback reply failed: {e}"
            print(f"[BRAHMA EVO] ⚠️ {msg}")
            self.ui.write_log(f"ERR: {msg}")
            try:
                self.ui.finish_task_workspace(msg, "Reply failed.", 100)
            except Exception:
                pass
            if not self.ui.muted:
                self.ui.set_state("LISTENING")

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def trigger_barge_in(self):
        """Immediately interrupts AI speech playback and switches state to LISTENING."""
        with self._speaking_lock:
            if not self._is_speaking:
                return
            self._is_speaking = False

        try:
            from actions.attention_monitor import stop_native_speech
            stop_native_speech()
        except Exception:
            pass

        try:
            if self.audio_in_queue:
                while not self.audio_in_queue.empty():
                    try:
                        self.audio_in_queue.get_nowait()
                    except Exception:
                        break
        except Exception:
            pass

        try:
            from sound_manager import SoundManager
            SoundManager.instance().play_listening_start()
        except Exception:
            pass

        if hasattr(self, "ui") and self.ui and not self.ui.muted:
            self.ui.set_state("LISTENING")



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
            announcement = f"I created and activated the feature '{feature_name}'."
            if description:
                announcement += f" {description}"
            announcement += f" I created it for this goal: {goal}."

            # Immediately execute the newly forged skill to satisfy the user's goal
            execution_output = ""
            try:
                from core.dynamic_registry import DynamicToolRegistry
                if DynamicToolRegistry.has_tool(feature_name):
                    run_res = DynamicToolRegistry.execute_sync(feature_name, {"query": goal, "goal": goal, "input": goal})
                    if run_res:
                        if isinstance(run_res, dict):
                            execution_output = str(run_res.get("summary") or run_res.get("output") or run_res.get("text") or run_res).strip()
                        else:
                            execution_output = str(run_res).strip()
            except Exception as e_run:
                execution_output = f"(Execution note: {e_run})"

            try:
                self.ui.show_hud_deliverable(
                    title=f"Evolution: {feature_name}",
                    summary=execution_output or announcement,
                    kind="result",
                )
            except Exception:
                pass
            try:
                self.ui.write_log(f"Brahma Evo: {announcement}")
                if execution_output:
                    self.ui.write_log(f"Result:\n{execution_output}")
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

    def _spawn_background(self, coro, name: str):
        """Start a loop task that outlives its caller, with a strong ref.

        Fire-and-forget ``asyncio.create_task`` calls keep no reference, so the
        task can be garbage-collected before it finishes and its exception never
        surfaces. Holding it in ``_background_tasks`` until it completes fixes
        both, and the done-callback logs a real failure instead of dropping it.
        """
        task = asyncio.ensure_future(coro)
        self._background_tasks.add(task)

        def _done(t: "asyncio.Future") -> None:
            self._background_tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                try:
                    print(f"[BRAHMA EVO] background task '{name}' failed: {exc!r}")
                    traceback.print_exception(type(exc), exc, exc.__traceback__)
                except Exception:
                    pass

        task.add_done_callback(_done)
        return task

    async def _serve_dashboard(self):
        if self._dashboard is None:
            self.ui.write_log("ERR: Mobile Connect disabled because dashboard dependencies are missing.")
            return
        try:
            self._dashboard.set_connect_callback(self._on_phone_connected)
            self._dashboard.set_wake_callback(lambda: None)
            await self._dashboard.serve()
        except Exception as e:
            self.ui.write_log(f"ERR: Mobile Connect server failed: {e}")
            traceback.print_exc()

    async def _consume_remote_commands(self):
        if self._dashboard is None:
            return
        while True:
            text = await self._dashboard._command_queue.get()
            if text:
                try:
                    self.ui.submit_external_command(text, source="mobile")
                except Exception:
                    self._on_text_command(text, source="mobile")

    async def _relay_phone_audio(self):
        if self._dashboard is None:
            return
        while True:
            frame = await self._dashboard._phone_audio_queue.get()
            # Only relay while a live session can actually play it. Queuing into a
            # stale out_queue (no consumer) would just grow without bound while
            # disconnected.
            if self.session is None or not self.out_queue:
                continue
            self._phone_active = True
            try:
                await self.out_queue.put(frame)
            finally:
                await asyncio.sleep(0.08)
                if self._dashboard._phone_audio_queue.empty():
                    self._phone_active = False

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)




def main():
    _startup_log("main entered")
    try:
        if update_from_github(BASE_DIR):
            _startup_log("updated from GitHub; restarting")
            restart_application(BASE_DIR)
            return
    except Exception as exc:
        _startup_log(f"GitHub update skipped: {exc}")
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
        tool_bridge.prime(TOOL_DECLARATIONS, DynamicToolRegistry)
        _ts = tool_bridge.snapshot()
        _startup_log(
            f"tool index ready: {_ts['tools']} tools, advertising {_ts['advertised']}, "
            f"{_ts['deferred']} deferred, backends={','.join(_ts['backends']) or 'none'}"
        )
    except Exception as exc:
        _startup_log(f"tool index unavailable: {exc}")

    ui = BrahmaUI(str(BASE_DIR / "assets" / "Brahma_Lite_Logo.png"), show_immediately=True)
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
                    curr_clip = pyperclip.paste()
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


if __name__ == "__main__":
    import sys
    import os
    import traceback

    # Intercept subprocess calls when running as PyInstaller .exe
    if len(sys.argv) >= 2:
        if sys.argv[1].endswith(".py") and os.path.exists(sys.argv[1]):
            # AgentExecutor is trying to run a dynamic script
            try:
                with open(sys.argv[1], 'r', encoding='utf-8') as f:
                    code = f.read()
                namespace = {"__name__": "__main__", "__file__": sys.argv[1]}
                exec(code, namespace)
                sys.exit(0)
            except Exception as e:
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
    except Exception as e:
        with open("FATAL_CRASH.log", "w") as f:
            traceback.print_exc(file=f)
        raise