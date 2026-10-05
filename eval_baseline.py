import json, os, sys
from collections import defaultdict
from pathlib import Path
from typing import Literal
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field
from rca_shared import dir_hit

load_dotenv()
RUN = sys.argv[1] if len(sys.argv) > 1 else "5"   # "0"/"5" -> baseline_k{K}; "graph" -> results/graph.jsonl
NAME = f"k{RUN}" if RUN.isdigit() else RUN
PRED = f"results/baseline_k{RUN}.jsonl" if RUN.isdigit() else f"results/{RUN}.jsonl"
JUDGE = os.environ.get("JUDGE_MODEL", "gpt-4.1")
client = OpenAI(max_retries=5)
CACHE = Path(f"results/judge_v2_{NAME}.jsonl")

class Verdict(BaseModel):
    reason: str = Field(description="One sentence comparing the predicted cause to the reference cause")
    score: Literal[0, 1, 2] = Field(description="2 = same underlying defect; 1 = right component/mechanism "
                                                "but incomplete or partly wrong; 0 = different or wrong cause")

JUDGE_SYSTEM = """You grade root-cause diagnoses of software bugs against a reference written from the actual fix.
Ignore wording and length. Score strictly:
- 2: names the SAME component AND the SAME mechanism as the reference (the specific wrong condition,
  ordering, missing check, keying, locking, etc.).
- 1: right component, but the mechanism is vaguer, different, or incomplete.
- 0: restates the symptom, blames the wrong component, or blames an external factor (version skew,
  a library/gRPC bug, the environment) when the reference names a defect in this code.
When unsure between 2 and 1, choose 1."""

tickets = {t["ticket_id"]: t for t in map(json.loads, open("data/clean/tickets.jsonl", encoding="utf-8"))}
truth = {g["ticket_id"]: g for g in map(json.loads, open("data/clean/ground_truth.jsonl", encoding="utf-8"))}
reviews = {r["ticket_id"]: r for r in map(json.loads, open("data/clean/gold_reviews.jsonl", encoding="utf-8"))}
preds = [json.loads(l) for l in open(PRED, encoding="utf-8")]
cache = {v["ticket_id"]: v for v in map(json.loads, open(CACHE, encoding="utf-8"))} if CACHE.exists() else {}

with open(CACHE, "a", encoding="utf-8") as f:
    for p in preds:
        if p["ticket_id"] in cache:
            continue
        g, t = truth[p["ticket_id"]], tickets[p["ticket_id"]]
        user = (f"BUG: {t['title']}\n\nREFERENCE ROOT CAUSE:\n{g['root_cause_text']}\nFIX PR: {g['fix_pr_title']}\nFIX: {g['fix_summary']}\n\n"
                f"PREDICTED ROOT CAUSE:\n{p['root_cause']}")
        v = client.chat.completions.parse(model=JUDGE, response_format=Verdict,
            messages=[{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user}]).choices[0].message.parsed
        cache[p["ticket_id"]] = {"ticket_id": p["ticket_id"], **v.model_dump()}
        f.write(json.dumps(cache[p["ticket_id"]]) + "\n")

groups = defaultdict(list)
for p in preds:
    g = truth[p["ticket_id"]]
    row = {"cat": p["root_cause_category"] == g["root_cause_category"],
           "judge": cache[p["ticket_id"]]["score"],
           "dir": dir_hit(p["suspected_files"], g["fix_files"]),
           "cite_ok": set(p["citations"]) <= set(p["retrieved"]),
           "cost": p["cost_usd"], "lat": p["latency_s"], "grounded": p.get("grounded", True)}
    groups["all"].append(row)
    groups["easy (input states cause)" if reviews[p["ticket_id"]].get("input_states_cause") else "hard"].append(row)

print(f"\nRun: {NAME}")
print(f"{'slice':28}{'n':>5}{'RCA exact':>11}{'RCA mean':>10}{'category':>10}{'dir hit':>9}{'cite ok':>9}{'grounded':>10}{'$/tkt':>8}{'p50 s':>7}")
for name, rows in groups.items():
    n = len(rows)
    lat = sorted(r["lat"] for r in rows)[n // 2]
    print(f"{name:28}{n:>5}{sum(r['judge'] == 2 for r in rows)/n:>11.0%}{sum(r['judge'] for r in rows)/(2*n):>10.0%}"
          f"{sum(r['cat'] for r in rows)/n:>10.0%}{sum(r['dir'] for r in rows)/n:>9.0%}"
          f"{sum(r['cite_ok'] for r in rows)/n:>9.0%}{sum(r['grounded'] for r in rows)/n:>10.0%}{sum(r['cost'] for r in rows)/n:>8.4f}{lat:>7.1f}")
