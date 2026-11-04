"""Scores all results in <BENCH_OUT>/res_*.json and prints a comparison table (+ failure details with --detail)."""
import glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repository root
OUT = os.environ.get("BENCH_OUT", "bench-results")  # frozen prompts and per-model results
os.makedirs(OUT, exist_ok=True)
from eval_cases import CASES, flags, score
detail = "--detail" in sys.argv
rows = []
for f in sorted(glob.glob(os.path.join(OUT, "res_*.json"))):
    name = os.path.basename(f)[4:-5]; res = json.load(open(f))
    ok = n = 0; fl = {}; tps = []; first = []; total = []; fails = []
    for r in res:
        if r.get("refusal"): continue
        n += 1
        good, why = score(r["id"], r["heard"], r["ctx"])
        ok += good
        if not good: fails.append((r["id"], why, r["heard"][:150].replace("\n", " ")))
        for x in flags(r["heard"], r["has_src"]): fl[x] = fl.get(x, 0) + 1
        if r["gen_tps"]: tps.append(r["gen_tps"])
        first.append(r["first_s"]); total.append(r["total_s"])
    avg = lambda l: sum(l) / len(l) if l else 0
    rows.append((ok / n if n else 0, name, ok, n, fl, avg(tps), avg(first), avg(total), fails))
rows.sort(reverse=True)
print(f"{'model':20s} {'correct':>8s} {'tok/s':>6s} {'1st sentence':>10s} {'total':>6s}  flags")
for sc, name, ok, n, fl, tps, first, total, fails in rows:
    print(f"{name:20s} {ok:>3d}/{n:<4d} {tps:6.1f} {first:9.1f}s {total:5.1f}s  {' '.join(f'{k}×{v}' for k, v in sorted(fl.items())) or '-'}")
if detail:
    for sc, name, ok, n, fl, tps, first, total, fails in rows:
        print(f"\n== {name}")
        for cid, why, ans in fails: print(f"   ✗ {cid:13s} ({why}) → {ans}")
