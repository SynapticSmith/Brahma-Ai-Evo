"""Startup/default-settings helpers, moved verbatim out of ui.py (P4-19).
stdlib imports are direct; BASE_DIR and _OS stay in ui.py and are injected
via bind() (used only at runtime)."""
from __future__ import annotations

import platform
import psutil
import subprocess
import sys
import time

from pathlib import (Path)


def bind(*, BASE_DIR, _OS):
    globals().update(BASE_DIR=BASE_DIR, _OS=_OS)

def _quiet_run(*args, **kwargs):
    if _OS == "Windows":
        kwargs.setdefault("creationflags", getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return subprocess.run(*args, **kwargs)


def _quote_cmd_arg(path: str) -> str:
    return f'"{path}"'


def _hidden_launch_args(*extra_args: str) -> list[str]:
    main_py = BASE_DIR / "main.py"
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        return [str(exe), *extra_args]
    venv_pythonw = BASE_DIR / ".venv" / "Scripts" / "pythonw.exe"
    if venv_pythonw.exists():
        return [str(venv_pythonw), str(main_py), *extra_args]
    return [str(sys.executable), str(main_py), *extra_args]

def _startup_run_value() -> str:
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        return f'{_quote_cmd_arg(str(exe))} --startup'
    main_py = BASE_DIR / "main.py"
    venv_pythonw = BASE_DIR / ".venv" / "Scripts" / "pythonw.exe"
    if venv_pythonw.exists():
        return f'{_quote_cmd_arg(str(venv_pythonw))} {_quote_cmd_arg(str(main_py))} --startup'
    return f'{_quote_cmd_arg(sys.executable)} {_quote_cmd_arg(str(main_py))} --startup'


def _startup_registry_key():
    if platform.system() != "Windows":
        return None
    return r"Software\Microsoft\Windows\CurrentVersion\Run"


def _current_boot_stamp() -> int:
    try:
        return int(psutil.boot_time())
    except Exception:
        return int(time.time())


def _launched_from_windows_startup() -> bool:
    return any(str(arg).strip().lower() == "--startup" for arg in sys.argv[1:])


def _default_app_settings() -> dict:
    return {
        "startup_animation_enabled": True,
        "last_boot_stamp": 0,
        "boot_sequence_played": True,
        "show_workspace_on_startup": False,
        "launcher_pos": None,
        "launch_minimized": False,
        "check_updates_on_startup": True,
        "default_ai_provider": "Gemini",
        "auto_provider_switch": True,
        "local_ai_url": "http://localhost:11434/v1",
        "local_ai_model": "llama3.2",
        "local_api_key": "",
        "custom_ai_url": "",
        "custom_ai_model": "",
        "custom_api_key": "",
        "custom_mcp_servers": [],
        # De-facto standard MCP config shape, read as a fallback by
        # core.mcp_manager when custom_mcp_servers is empty, so a config from
        # another MCP client works unchanged: {"name": {command,args,env}} for
        # stdio or {"name": {url,headers}} for a remote server.
        "mcpServers": {},
        # Tool & Skill Index. Budget is the number of function declarations sent
        # on the Gemini Live setup frame; the provider hard-caps this at 128, so
        # the default stays far below. Everything else is reachable on demand
        # through the search_tools / call_tool meta-tools.
        "tool_index_enabled": True,
        "tool_index_budget": 24,
        # Per-provider kill switches for the failover chain (core/provider_router.py).
        # All default True so behaviour is unchanged until someone toggles one.
        # Switch OpenRouter off to guarantee zero cloud egress from background work.
        "provider_enabled_gemini": True,
        "provider_enabled_openrouter": True,
        "provider_enabled_local": True,
        "provider_enabled_custom": True,
        # Background memory extraction. Deliberately independent of the selected
        # provider: running locally is when you still want the memory graph built.
        "memory_extraction_enabled": True,
        # 3D background animation; costs GPU/CPU every frame, so it is switchable.
        "enable_3d_background": True,
        # Speech engines for the modular (non-Gemini) speech pipeline. Both
        # default to the previous cloud behaviour so nothing changes until the
        # user picks a local engine; see core/speech_stt.py and core/speech_tts.py.
        "speech_stt_engine": "google",
        "speech_whisper_model": "base.en",
        "speech_tts_engine": "edge",
        "speech_tts_voice": "",
        "attention_message_prompts": True,
        "attention_call_prompts": True,
        "developer_mode_enabled": False,
        "developer_mode_workspace": "",
    }


def _default_discord_settings() -> dict:
    return {
        "bot_token": "",
        "enabled": False,
        "channel_id": "",
    }
