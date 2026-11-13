"""End-to-end check of Brahma's LocalBrain path against the tuned Ollama.

Builds a ~5-6K token system prompt (the size a real Brahma turn sends) and asks
for a two-token answer. Prints the configured model, context budget, wall time
and token usage so the prefill/decode behaviour can be verified outside the UI.
"""

import sys
import time

sys.path.insert(0, r"C:\Users\Admin\Brahma-AI-Evo")

from core import local_brain  # noqa: E402

line = "The quick brown fox jumps over the lazy dog. " * 40 + "\n"
system = "You are Brahma's local assistant.\n" + line * 12
messages = [
    {"role": "system", "content": system},
    {"role": "user", "content": "Reply with exactly: OK"},
]

brain = local_brain.LocalBrain()
print("model    :", brain.default_model)
print("endpoint :", brain.endpoint)
print("num_ctx  :", local_brain.LOCAL_NUM_CTX)
print("prompt_chars:", len(system))

started = time.monotonic()
reply = brain.chat_complete(messages)
elapsed = time.monotonic() - started

print("elapsed_s:", round(elapsed, 1))
print("usage    :", reply.get("usage"))
choices = reply.get("choices") or [{}]
print("text     :", (choices[0].get("message") or {}).get("content", "")[:120])
