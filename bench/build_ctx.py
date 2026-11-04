"""Freezes the bench contexts: runs each question through the real pipeline (decision, SearXNG search, filter, prompt)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repository root
OUT = os.environ.get("BENCH_OUT", "bench-results")  # frozen prompts and per-model results
os.makedirs(OUT, exist_ok=True)
from murmure import rules as R
from eval_cases import CASES
out = []
for cid, q, _ in CASES:
    R.last_query = ""
    text = R.normalize_question(q)
    query = R.decide(text)
    sources = R.web_search(query) if query else []
    refusal = bool(query) and not sources  # Murmure answers "I found nothing" without querying the model
    user = R.user_message(text, sources, query or "")
    out.append({"id": cid, "q": text, "query": query, "n_sources": len(sources), "refusal": refusal, "user": user,
                "messages": [R.system_message(), {"role": "user", "content": user}]})
json.dump(out, open(os.path.join(OUT, "prompts.json"), "w"), ensure_ascii=False, indent=1)
for o in out:
    print(f"{o['id']:14s} search={str(o['query'])[:48]!r:52s} sources={o['n_sources']} {'REFUS-PIPELINE' if o['refusal'] else ''}")
