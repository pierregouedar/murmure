"""Queries the local llama.cpp server (127.0.0.1:8080) with the frozen prompts; writes <BENCH_OUT>/res_<name>.json."""
import json, os, re, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repository root
OUT = os.environ.get("BENCH_OUT", "bench-results")  # frozen prompts and per-model results
os.makedirs(OUT, exist_ok=True)
from murmure import rules as R
name = sys.argv[1]
extra = json.loads(os.environ.get("BENCH_EXTRA", "{}"))
res = []
fails = 0
HEADERS = {"Content-Type": "application/json"}
if os.environ.get("BENCH_KEY_FILE"): HEADERS["Authorization"] = "Bearer " + open(os.environ["BENCH_KEY_FILE"]).read().strip()
for p in json.load(open(os.environ.get("BENCH_PROMPTS", os.path.join(OUT, "prompts.json")))):
    if p["refusal"]:
        res.append({"id": p["id"], "refusal": True}); continue
    msgs = p["messages"]
    if os.environ.get("BENCH_SYSTEM_FROM_RULES"):  # same frozen contexts, current system prompt
        msgs = [R.system_message()] + msgs[1:]
    if os.environ.get("BENCH_SYSTEM_FILE"):  # same frozen contexts, system prompt read from a file (candidate prompt)
        msgs = [{"role": "system", "content": open(os.environ["BENCH_SYSTEM_FILE"], encoding="utf-8").read().strip() + f" Today is {R.today()}."}] + msgs[1:]
    if os.environ.get("BENCH_MODEL"): extra = {**extra, "model": os.environ["BENCH_MODEL"]}
    body = {**extra, "messages": msgs, "max_tokens": 200, "temperature": 0.3, "stream": True, "stream_options": {"include_usage": True}}
    for attempt in range(2):  # free models: frequent 429s, wait then retry
        t0 = time.time(); first = None; raw = ""; tm = {}; err = ""
        try:
            req = urllib.request.Request(os.environ.get("BENCH_URL", "http://127.0.0.1:8080") + "/v1/chat/completions", json.dumps(body).encode(), HEADERS)
            with urllib.request.urlopen(req, timeout=200) as r:
                for line in r:
                    line = line.strip()
                    if not line.startswith(b"data: ") or line == b"data: [DONE]": continue
                    d = json.loads(line[6:])
                    if d.get("timings"): tm = d["timings"]
                    if not d.get("choices"): continue
                    c = d["choices"][0].get("delta", {}).get("content")
                    if c:
                        raw += c
                        if first is None and len(raw) > 15 and re.search(r"[.!?…](\s|$)", raw): first = time.time() - t0
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        if raw or not os.environ.get("BENCH_RETRY"): break
        time.sleep(8 * (attempt + 1))
    time.sleep(float(os.environ.get("BENCH_SLEEP", "0")))
    fails = 0 if raw else fails + 1
    if fails >= 2 and os.environ.get("BENCH_RETRY"):
        print(f"  [{name}] GIVING UP: 2 failures in a row ({err})", flush=True); break
    has_src = p["n_sources"] > 0
    spoken = []
    for s in R.sentences([raw]):  # what Murmure would speak aloud
        if not has_src: s = R.drop_fake_source(s)
        if s: spoken.append(s)
    res.append({"id": p["id"], "refusal": False, "raw": raw.strip(), "heard": " ".join(spoken), "ctx": p["user"], "has_src": has_src,
                "first_s": round(first or time.time() - t0, 1), "total_s": round(time.time() - t0, 1),
                "gen_tps": round(tm.get("predicted_per_second", 0), 1), "prompt_tps": round(tm.get("prompt_per_second", 0), 1), "err": err})
    print(f"  [{name}] {p['id']:14s} 1st sentence {res[-1]['first_s']:5.1f}s total {res[-1]['total_s']:5.1f}s gen {res[-1]['gen_tps']:5.1f} tok/s {err}", flush=True)
json.dump(res, open(os.path.join(OUT, f"res_{name}.json"), "w"), ensure_ascii=False, indent=1)
