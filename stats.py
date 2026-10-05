"""Bootstrap CIs + paired significance for every eval run. No LLM calls.   python stats.py"""
import random
from math import comb
from rca_shared import dir_hit, load_jsonl

RUNS = {"baseline (no RAG)": ("baseline_k0", "k0"), "baseline (RAG k=5)": ("baseline_k5", "k5"),
        "baseline (RAG k=5, 24h comments only)": ("baseline_k5_24h", "baseline_k5_24h"),
        "graph v1": ("graph_v1", "graph_v1"), "graph v2": ("graph_v2", "graph_v2"), "graph v3": ("graph", "graph")}
B, rng = 10_000, random.Random(0)

truth = {g["ticket_id"]: g for g in load_jsonl("data/clean/ground_truth.jsonl")}
hard = {r["ticket_id"] for r in load_jsonl("data/clean/gold_reviews.jsonl")
        if r["verdict"] == "keep" and not r.get("input_states_cause")}

def per_ticket(pred_file, judge_name):
    judge = {v["ticket_id"]: v["score"] for v in load_jsonl(f"results/judge_v2_{judge_name}.jsonl")}
    return {p["ticket_id"]: {"exact": judge[p["ticket_id"]] == 2,
                             "dir": dir_hit(p["suspected_files"], truth[p["ticket_id"]]["fix_files"])}
            for p in load_jsonl(f"results/{pred_file}.jsonl")}

def ci(xs):
    n = len(xs)
    means = sorted(sum(rng.choices(xs, k=n)) / n for _ in range(B))
    return sum(xs) / n, means[int(.025 * B)], means[int(.975 * B)]

def paired(a, b, ids):
    """a - b on the same tickets: bootstrap CI of the difference + exact McNemar p-value."""
    d = [int(a[t]) - int(b[t]) for t in ids]
    mean, lo, hi = ci(d)
    wins, losses = d.count(1), d.count(-1)
    k, n = min(wins, losses), wins + losses
    p = min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0
    return mean, lo, hi, wins, losses, p

data = {name: per_ticket(*files) for name, files in RUNS.items()}
lines = ["## Results with 95% bootstrap confidence intervals", "",
         "| run | RCA exact (all, n=162) | RCA exact (hard, n=68) | dir hit (all) | dir hit (hard) |", "|---|---|---|---|---|"]
for name, r in data.items():
    cell = lambda metric, ids: "{:.0%} [{:.0%}, {:.0%}]".format(*ci([r[t][metric] for t in ids]))
    lines.append(f"| {name} | {cell('exact', r)} | {cell('exact', hard)} | {cell('dir', r)} | {cell('dir', hard)} |")

lines += ["", "## Paired comparisons (same tickets): difference [95% CI], wins/losses, McNemar p", "",
          "| comparison | metric | slice | diff | wins / losses | p |", "|---|---|---|---|---|---|"]
for a, b in [("graph v3", "baseline (RAG k=5)"), ("baseline (RAG k=5)", "baseline (no RAG)"), ("graph v3", "graph v2"),
             ("baseline (RAG k=5, 24h comments only)", "baseline (RAG k=5)")]:
    for metric in ("exact", "dir"):
        for slice_name, ids in (("all", list(data[a])), ("hard", sorted(hard))):
            m, lo, hi, w, l, p = paired({t: data[a][t][metric] for t in ids}, {t: data[b][t][metric] for t in ids}, ids)
            lines.append(f"| {a} vs {b} | {'RCA exact' if metric == 'exact' else 'dir hit'} | {slice_name} | "
                         f"{m:+.1%} [{lo:+.1%}, {hi:+.1%}] | {w} / {l} | {p:.3f}{' *' if p < .05 else ''} |")
lines += ["", "\\* p < 0.05"]

open("results/RESULTS.md", "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("\n".join(lines))
