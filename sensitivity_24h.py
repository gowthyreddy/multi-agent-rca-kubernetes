"""How much do pre-fix discussion comments inflate scores? Rerun baseline k=5 with ONLY the first 24h of comments.
Tickets with no late comments get identical inputs, so their flips measure run-to-run noise.   python sensitivity_24h.py"""
import json, subprocess, sys, time
from datetime import datetime, timedelta
from pathlib import Path
from baseline import quick_diagnosis
from rca_shared import load_jsonl

OUT = Path("results/baseline_k5_24h.jsonl")
ts = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))

def triage_window(t):
    cutoff = ts(t["created_at"]) + timedelta(hours=24)
    return {**t, "comments": [c for c in t["comments"] if ts(c["created_at"]) < cutoff]}

tickets = {t["ticket_id"]: t for t in load_jsonl("data/clean/tickets.jsonl")}
gold = [g["ticket_id"] for g in load_jsonl("data/clean/ground_truth.jsonl") if g.get("gold")]
affected = {i for i in gold if len(triage_window(tickets[i])["comments"]) < len(tickets[i]["comments"])}

done = {r["ticket_id"] for r in load_jsonl(OUT)} if OUT.exists() else set()
with open(OUT, "a", encoding="utf-8") as out:
    for tid in [i for i in gold if i not in done]:
        start = time.time()
        d, hits, c = quick_diagnosis(triage_window(tickets[tid]))
        out.write(json.dumps({"ticket_id": tid, **d.model_dump(), "retrieved": [h["chunk_id"] for h in hits],
                              "latency_s": round(time.time() - start, 2), "cost_usd": c}) + "\n")
        out.flush()

subprocess.run([sys.executable, "eval_baseline.py", "baseline_k5_24h"], check=True)   # same strict judge, cached

hard = {r["ticket_id"] for r in load_jsonl("data/clean/gold_reviews.jsonl")
        if r["verdict"] == "keep" and not r.get("input_states_cause")}
full = {v["ticket_id"]: v["score"] == 2 for v in load_jsonl("results/judge_v2_k5.jsonl")}
h24 = {v["ticket_id"]: v["score"] == 2 for v in load_jsonl("results/judge_v2_baseline_k5_24h.jsonl")}

print("\nRCA exact: full pre-fix discussion -> first 24h only")
for name, ids in [("all", gold), ("hard", [i for i in gold if i in hard]),
                  ("affected (had late comments)", [i for i in gold if i in affected]),
                  ("unaffected = noise floor", [i for i in gold if i not in affected])]:
    a, b = sum(full[i] for i in ids) / len(ids), sum(h24[i] for i in ids) / len(ids)
    lost, gained = sum(full[i] and not h24[i] for i in ids), sum(h24[i] and not full[i] for i in ids)
    print(f"  {name:30} n={len(ids):3}  {a:.0%} -> {b:.0%}  ({b - a:+.0%})   flips: -{lost} / +{gained}")
