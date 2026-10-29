import subprocess
import sys

# The pinned runtime stack (numpy 2.4, PyQt6 6.11, opencv 5.0) publishes wheels
# for 3.11+ only. Fail here, with a sentence, instead of letting pip resolve
# into a wall of "no matching distribution" errors.
if sys.version_info < (3, 11):
    sys.exit(
        "Brahma AI Evo needs Python 3.11 or newer; this interpreter is "
        f"{sys.version.split()[0]}.\n"
        "Install 3.11+ and re-run: py -3.11 setup.py"
    )

print("Installing requirements...")
subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], check=True)

print("Installing Playwright browsers...")
subprocess.run([sys.executable, "-m", "playwright", "install"], check=True)

print("\n✅ Setup complete! Run 'python main.py' or start_brahma.bat to start Brahma Evo.")
