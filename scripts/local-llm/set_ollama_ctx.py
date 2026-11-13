import os, shutil, sqlite3, tempfile

DB = os.path.expandvars(r"%LOCALAPPDATA%\Ollama\db.sqlite")
TARGET = int(os.environ.get("TARGET_CTX", "32768"))

d = tempfile.mkdtemp()
q = os.path.join(d, "db.sqlite")
for s in ("", "-wal", "-shm"):
    if os.path.exists(DB + s):
        shutil.copy(DB + s, q + s)

c = sqlite3.connect(q)
before = c.execute("select context_length from settings where id=1").fetchone()
c.execute("update settings set context_length=? where id=1", (TARGET,))
c.commit()
after = c.execute("select context_length from settings where id=1").fetchone()
c.close()

# Replace the live DB only after a successful edit.
for s in ("", "-wal", "-shm"):
    src, dst = q + s, DB + s
    if os.path.exists(src):
        shutil.copy(src, dst)
    elif os.path.exists(dst):
        os.remove(dst)

print(f"context_length: {before[0]} -> {after[0]}")
