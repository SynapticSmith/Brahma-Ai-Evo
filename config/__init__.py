from pathlib import Path
from core.secret_store import load_api_keys

_CONFIG_PATH = Path(__file__).parent / "api_keys.json"

def get_config() -> dict:
    if _CONFIG_PATH.exists():
        return load_api_keys(_CONFIG_PATH)
    return load_api_keys()

def get_os() -> str:
    """Returns: 'windows' | 'mac' | 'linux'"""
    return get_config().get("os_system", "windows").lower()

def is_windows() -> bool: return get_os() == "windows"
def is_mac()     -> bool: return get_os() == "mac"
def is_linux()   -> bool: return get_os() == "linux"
