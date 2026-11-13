# Local LLM setup on the HP ProBook 460 G11

How the local Ollama stack is tuned for agentic work (OpenCode, Brahma, OpenBot)
on a 16 GB CPU-only laptop, and when to use which model.

## Hardware

| Item | Value |
| --- | --- |
| CPU | Intel Core Ultra 5 125U, 2P + 8E + 2LPE, 14 threads |
| RAM | 16 GB DDR5-5600 (2 x 8 GB, dual channel) |
| GPU | Intel integrated only. Ollama has a Vulkan backend but drops the iGPU unless `OLLAMA_IGPU_ENABLE=1`. It is intentionally left off: the iGPU speeds prefill but makes decode slower, and decode is what agent turns wait on. |

Everything runs on CPU. Decode speed is bounded by memory bandwidth
(~20 tok/s for a 1B-active MoE, ~17-20 tok/s for a dense 1.7B). Long-context
work is bounded by KV-cache size, which is why the settings below matter.

## What was wrong before

1. The Ollama desktop app stored `context_length = 262144` in
   `%LOCALAPPDATA%\Ollama\db.sqlite` (table `settings`) and injected it as
   `OLLAMA_CONTEXT_LENGTH` when it spawned the server. Every model therefore
   loaded with a 262K KV cache: 2.5 GB of resident memory for a 1.1 GB model,
   and much slower long-context decode.
2. Flash attention was off and the KV cache was f16.
3. The server used 7 threads (Ollama's default), not the 10-12 this CPU can feed.
4. Brahma hardcoded `LOCAL_NUM_CTX = 8192`, so Brahma and OpenBot (which routes
   through Brahma) were capped at 8K regardless of the model.

## Current configuration

### Ollama app database

`%LOCALAPPDATA%\Ollama\db.sqlite` -> `settings.context_length = 32768`

This is the same value the Ollama desktop app shows as **Context length**. If
you change it in the app UI, change it to 32768. To edit it safely while the
app is closed, use `scripts/local-llm/set_ollama_ctx.py`
(`TARGET_CTX` env var overrides the target).

### Persistent user environment variables

| Variable | Value | Why |
| --- | --- | --- |
| `OLLAMA_CONTEXT_LENGTH` | `32768` | Matches the app DB. Big enough for the Brahma/OpenCode system prompt, a multi-turn agent exchange and a pasted document. |
| `OLLAMA_FLASH_ATTENTION` | `1` | Required for a quantized KV cache and faster attention. |
| `OLLAMA_KV_CACHE_TYPE` | `q8_0` | Halves KV memory and gives +72% decode at ~25K context. |
| `OLLAMA_KEEP_ALIVE` | `30m` | Avoids a 5-8 s reload between agent turns. |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | 16 GB cannot hold two models plus their KV caches. |

The desktop app does **not** reliably inherit variables that were set after it
started. After changing them, log out/in (or start `ollama app.exe` from a shell
that has them) and verify with the `server config` line in
`%LOCALAPPDATA%\Ollama\server.log`:

```text
OLLAMA_CONTEXT_LENGTH:32768 OLLAMA_FLASH_ATTENTION:true OLLAMA_KV_CACHE_TYPE:q8_0
OLLAMA_KEEP_ALIVE:30m0s OLLAMA_MAX_LOADED_MODELS:1
```

### Ollama model profiles

Created with `ollama create` from the Modelfiles in `scripts/local-llm/`:

```powershell
cd scripts\local-llm
ollama create lfm25-8b -f Modelfile.lfm25-8b
ollama create lfm3-32k -f Modelfile.lfm3-32k
ollama create lfm3-doc -f Modelfile.lfm3-doc
```

| Profile | Base | Context | Threads | Use |
| --- | --- | --- | --- | --- |
| `lfm25-8b` | `lfm2.5:8b-a1b-q4_K_M` | 32768 | 12 | Best quality. 8.5B total / 1B active MoE, native tool calling. |
| `lfm3-32k` | `lfm3:1.7b` | 32768 | 12 | Fastest prefill. Sliding-window attention keeps long-context cost down. |
| `lfm3-doc` | `lfm3:1.7b` | 65536 | 12 | One-shot document ingestion / long-file review. |

`num_thread 12` is passed through as `-t 12`; Ollama's default is 7, which leaves this CPU underused.

Verify what actually loaded:

```powershell
ollama ps          # shows CONTEXT and SIZE
Select-String -Path "$env:LOCALAPPDATA\Ollama\server.log" -Pattern 'starting llama-server' |
  Select-Object -Last 1
```

The command line should contain `-c 32768 --cache-type-k q8_0 --cache-type-v q8_0
--flash-attn on -t 12`.

## Benchmarks

All numbers are CPU-only with a 32768 context allocation and 64 generated
tokens. The Threads column also tells you which KV configuration was active:
the 7-thread `lfm3:1.7b` rows are the old f16 KV / no-flash-attention baseline;
every other row is `q8_0` KV with flash attention on. "Prefill" is prompt
processing; "decode" is generation speed.

| Model | Threads | Prompt | Prefill tok/s | Decode tok/s |
| --- | --- | --- | --- | --- |
| `lfm3:1.7b` | 7 | 7.1K | 123.5 | 17.1 |
| `lfm3:1.7b` | 7 | 28.1K | 101.2 | 6.4 |
| `lfm3:1.7b` + q8_0 KV | 7 | 7.1K | 82.7* | 19.6 |
| `lfm3:1.7b` + q8_0 KV | 7 | 24.6K | 42.4* | 11.0 |
| `lfm2.5:8b-a1b` | 7 | 7.1K | 65.8 | 19.4 |
| `lfm2.5:8b-a1b` | 7 | 24.6K | 20.9 | 7.9 |
| `lfm25-8b` (profile) | 10 | 6.2K | 80.0 | 20.6 |
| `lfm25-8b` (profile) | 10 | 21.1K | 37.7 | 10.2 |
| `lfm3-32k` (profile) | 10 | 6.2K | 101.5 | 20.2 |
| `lfm3-32k` (profile) | 10 | 24.6K | 46.2 | 13.4 |
| `lfm3-t8` (sweep) | 8 | 5.3K | 103.8 | 22.7 |
| `lfm3-t12` (sweep) | 12 | 5.3K | 119.3 | 22.3 |
| `lfm3-t12` (sweep) | 12 | 21.1K | 55.1 | 14.2 |
| `lfm3-t14` (sweep) | 14 | 5.3K | 118.8 | 20.2 |
| `lfm25-t12` (sweep) | 12 | 5.3K | 87.5 | 20.1 |
| `lfm3-doc` (profile) | 10 | 64K | not run (estimate 25-35 min) | — |

The `(profile)` rows were measured at 10 threads; the shipped profiles now use
12 threads (see the sweep rows, which are the same models and settings at other
thread counts).

\* measured while a 5.2 GB model download was running; prefill is understated.

Reproduce a row with:

```powershell
python scripts\local-llm\bench_ctx.py lfm3-32k 7000,28000 32768
```

Arguments are `<model> <prompt-tokens,csv> <num_ctx>`. Set `OLLAMA_BENCH_URL` to
benchmark a different server. Each row is one request; the second request reuses
part of the first prompt from Ollama's prompt cache, exactly like a second agent
turn.

Key takeaways:

- **Quantized KV cache is the long-context win.** At ~25K context, decode goes
  from 6.4 to 11.0 tok/s (+72%) because decode is memory-bandwidth bound.
  The price is prefill: `q8_0` costs roughly 20-35% prompt-processing throughput
  versus f16 (101 vs 123 tok/s on LFM3 at 6K). That trade is worth it for
  agent sessions, where most prompt tokens are cached and the answer decode is
  the long pole. For a short, one-shot prompt with a long answer, f16 KV would
  be faster; that is not the common case here.
- **Decode is similar for LFM3 1.7B and LFM2.5 8B-A1B** (~20 tok/s at short
  context). The 8B MoE only activates ~1B parameters per token.
- **Prefill is the LFM2.5 weak spot.** LFM2.5 has no sliding-window attention
  (`n_swa = 0`), so prompt processing collapses with context. LFM3 has
  `n_swa = 256` and holds up far better. For a new OpenCode session (large tool
  schema + system prompt), LFM3 reaches the first token roughly twice as fast.
- **Thread count matters more than expected.** Ollama's default is 7 threads.
  Going to 12 threads on this 2P + 8E + 2LPE chip raised prefill from 101 to
  119 tok/s and long-context decode from 11.0 to 14.2 tok/s at ~21K. Decode is
  latency-bound on a few P-cores, so the extra threads keep the memory pipeline
  fed. 14 threads (every logical CPU) was no better and lost decode speed
  (118.8 / 20.2), so 12 is the ceiling.
- Ollama creates context checkpoints and keeps a prompt cache (8 GiB limit), so
  subsequent turns reuse the unchanged prefix and only prefill the new tokens.
  This is why keeping one model resident (`OLLAMA_MAX_LOADED_MODELS=1`,
  `OLLAMA_KEEP_ALIVE=30m`) matters.

## Which model to use

| Situation | Model |
| --- | --- |
| Quality matters, prompt under ~16K (Brahma, OpenBot, chat, code edits) | `lfm25-8b` |
| Large tool schema / many turns / fastest first token (OpenCode agent) | `lfm3-32k` |
| One large document, 32K-64K | `lfm3-doc` |
| Embeddings | `nomic-embed-text` |

Only one model fits in RAM at a time. Pick one default per workflow; switching
models costs a 5-8 s reload plus a cold prefill.

### Thinking models (`lfm25-8b`)

`lfm25-8b` is a thinking model: it writes a chain of thought into `content`
between the think markers. Its GGUF carries no chat template, so Ollama renders
it with the built-in ChatML template and **ignores** `reasoning_effort: "none"`
and `think: false` - the block is always emitted, inline, in the message
content. A one-line question therefore costs ~60-200 extra decode tokens
(~3-10 s on this CPU). Suppressing it server-side would mean replacing the
template, which risks the tool-call formatting, so it is handled in the client.

Brahma scrubs a *leading* block in `core/local_brain.py` (`_strip_think` and
`_ThinkStreamFilter`) on both the non-streaming and streaming paths, so neither
the UI nor the replayed history sees the raw tag. A tag quoted mid-answer is
left alone. OpenCode and raw API calls still show the block; use `lfm3-32k`
there when clean, fast output matters more than answer quality.

## Application wiring

### Brahma (`C:\Users\Admin\Brahma-AI-Evo`)

- `core/local_brain.py`: `LOCAL_NUM_CTX = 32768` (was 8192). Sent as
  `options.num_ctx` on both local paths.
- `local_ai_url` / `local_ai_model` select the endpoint and model, and are
  re-read on every call, so a Settings change takes effect without a restart.
- **Where the live settings file is.** The repo copy at
  `config/app_settings.json` is only the default template. At runtime Brahma
  reads `%LOCALAPPDATA%\BrahmaAI\config\app_settings.json`. Brahma currently
  runs under the Microsoft Store build of Python 3.12, which redirects
  `%LOCALAPPDATA%`, so the real file is:
  `%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\LocalCache\Local\BrahmaAI\config\app_settings.json`.
  The simplest way to change the model is Brahma's own Settings UI; editing the
  repo copy has no effect on the running app.
- Current value: `"local_ai_model": "lfm25-8b:latest"` (the app stores the
  `:latest` suffix). That is the quality default. Switch it to
  `lfm3-32k:latest` when first-token latency matters more than answer quality.
- Brahma's `brahma-evo:passthrough` API lane goes through the same LocalBrain, so
  the model and 32K context apply there too.

### OpenBot (`%APPDATA%\OpenBot`)

- Provider `brahma` -> `http://127.0.0.1:8010/v1`, models
  `brahma-evo:passthrough`, `brahma-evo`, `brahma-evo:read`, `brahma-evo:plan`.
- OpenBot's preferred provider is `opencode` with
  `brahma/brahma-evo:passthrough`, so the request chain is
  OpenBot -> OpenCode -> Brahma API -> LocalBrain -> Ollama.
- No OpenBot change is needed: it inherits Brahma's `local_ai_model` and 32K
  context.

### OpenCode (`C:\Users\Admin\.config\opencode\opencode.json`)

The native `ollama` provider discovers `http://127.0.0.1:11434` automatically.
The config pins context limits so OpenCode does not assume a 200K window:

```jsonc
"providers": {
  "ollama": {
    "settings": { "baseURL": "http://127.0.0.1:11434/v1" },
    "models": {
      "lfm25-8b": { "limit": { "context": 32768, "output": 4096 }, "capabilities": { "tools": true } },
      "lfm3-32k": { "limit": { "context": 32768, "output": 4096 }, "capabilities": { "tools": true } },
      "lfm3-doc": { "limit": { "context": 65536, "output": 4096 }, "capabilities": { "tools": true } }
    }
  }
}
```

The `:latest` duplicates created by discovery are disabled. Select a model with
`/models` inside OpenCode, or for one run:

```powershell
opencode run --model ollama/lfm3-32k "summarize this repo"
```

The OpenCode default model is unchanged; local models are opt-in.

Verified end to end:

```powershell
opencode run --model ollama/lfm3-32k "Reply with exactly: OK"
# > build · lfm3-32k
# OK            (and a follow-up tool call also returned OK)
```

## Maintenance and troubleshooting

- **After an Ollama update:** re-check `db.sqlite` `context_length` and the
  `server config` line. Updates can reset the app database or the env vars.
- **Model reloads every turn:** check `ollama ps`; a blank list means the model
  expired (keep-alive) or another model evicted it (`OLLAMA_MAX_LOADED_MODELS=1`).
- **Decode collapses at long context:** confirm `K (q8_0)` and `V (q8_0)` in
  `server.log`. With f16 KV, decode roughly halves at 25K.
- **First token takes minutes:** that is prefill. LFM3 is about twice as fast as
  LFM2.5 at this. Prefer smaller prompts, or use `lfm3-32k` for the session.
- **Replies start with raw reasoning text:** `lfm25-8b` is a thinking model and
  Ollama will not suppress it (see "Thinking models" above). Brahma scrubs the
  leading block; if the tag shows up again, the scrub in
  `core/local_brain.py` regressed. Other clients show it by design.
- **Brahma ignores a settings edit:** the repo copy was edited, not the live one.
  The running app reads the Store-Python-redirected file under
  `%LOCALAPPDATA%\Packages\...\LocalCache\Local\BrahmaAI\config\app_settings.json`
  (see the Brahma section above). Change it in Brahma's Settings UI instead.
- **`python` in a shell is not the app's interpreter:** `python` resolves to the
  Microsoft Store 3.12 (same as the app), but its `%LOCALAPPDATA%` is redirected,
  so scripts see different files than Explorer or PowerShell.
- **Never** allocate 262K context on this machine: the KV cache alone exceeds
  the RAM budget and every request gets slower.
