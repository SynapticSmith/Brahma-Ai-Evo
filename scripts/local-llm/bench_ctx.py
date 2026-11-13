import json, os, sys, time, urllib.request

BASE = os.environ.get("OLLAMA_BENCH_URL", "http://127.0.0.1:11434")

FILLER = (
    "The deployment pipeline validates configuration files before applying them. "
    "Each module exposes a small interface, and the scheduler retries failed tasks "
    "with exponential backoff. Logs are written to disk and shipped to the collector. "
    "class ConfigLoader:\n    def __init__(self, path):\n        self.path = path\n\n"
    "def parse_options(argv):\n    opts = {}\n    for arg in argv:\n        key, _, value = arg.partition('=')\n        opts[key] = value\n    return opts\n"
)


def gen(model, target_tokens, num_predict=64, num_ctx=None, extra=None):
    # ~4 chars per token for English/code
    text = FILLER * max(1, int(target_tokens * 4 / len(FILLER)) + 1)
    prompt = text + "\n\nSummarize the above in one sentence."
    opts = {"num_predict": num_predict, "temperature": 0}
    if num_ctx:
        opts["num_ctx"] = num_ctx
    if extra:
        opts.update(extra)
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "options": opts}).encode()
    req = urllib.request.Request(BASE + "/api/generate", body, {"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        data = json.loads(r.read())
    wall = time.time() - t0
    pe = data.get("prompt_eval_count", 0)
    ped = data.get("prompt_eval_duration", 0) / 1e9
    ec = data.get("eval_count", 0)
    ed = data.get("eval_duration", 0) / 1e9
    return {
        "model": model,
        "prompt_tokens": pe,
        "prefill_s": round(ped, 2),
        "prefill_tok_s": round(pe / ped, 1) if ped else None,
        "gen_tokens": ec,
        "decode_s": round(ed, 2),
        "decode_tok_s": round(ec / ed, 1) if ed else None,
        "load_s": round(data.get("load_duration", 0) / 1e9, 1),
        "wall_s": round(wall, 1),
    }


if __name__ == "__main__":
    model = sys.argv[1]
    targets = [int(x) for x in sys.argv[2].split(",")]
    ctx = int(sys.argv[3]) if len(sys.argv) > 3 else None
    for t in targets:
        try:
            r = gen(model, t, num_ctx=ctx)
            print(json.dumps(r), flush=True)
        except Exception as e:
            print(json.dumps({"model": model, "target": t, "error": str(e)}), flush=True)
