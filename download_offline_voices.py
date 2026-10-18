import os
import urllib.request
import tempfile
from pathlib import Path

def get_voices_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "BrahmaAI" / "voices"

def download_file(url: str, dest_path: Path):
    print(f"Downloading: {dest_path.name}")
    print(f"From: {url}")
    try:
        # User-Agent header is sometimes required by GitHub/HuggingFace to prevent 403 Forbidden errors
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=60) as response:
            with open(dest_path, 'wb') as out_file:
                shutil.copyfileobj(response, out_file)
        print("OK\n")
    except Exception as e:
        print(f"FAILED: {e}\n")

if __name__ == "__main__":
    import shutil
    
    voices_dir = get_voices_dir()
    kokoro_dir = voices_dir / "kokoro"
    piper_dir = voices_dir / "piper"
    
    kokoro_dir.mkdir(parents=True, exist_ok=True)
    piper_dir.mkdir(parents=True, exist_ok=True)

    print("--- Downloading Kokoro v1.0 (ONNX) ---")
    # Using the official mirrored release files for kokoro-onnx
    download_file(
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.onnx",
        kokoro_dir / "kokoro-v1.0.onnx"
    )
    download_file(
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin",
        kokoro_dir / "voices-v1.0.bin"
    )

    print("--- Downloading Piper TTS (Lessac Medium) ---")
    download_file(
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx",
        piper_dir / "en_US-lessac-medium.onnx"
    )
    download_file(
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json",
        piper_dir / "en_US-lessac-medium.onnx.json"
    )
    
    print(f"All voices installed to: {voices_dir}")
    print("Restart Brahma Evo to activate local offline speech.")