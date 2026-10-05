import json, random
from collections import defaultdict

random.seed(42)
N = 220   # ~10% get dropped in review -> ~200 gold
tickets = {t["ticket_id"]: t for t in map(json.loads, open("data/clean/tickets.jsonl", encoding="utf-8"))}
truth = [json.loads(l) for l in open("data/clean/ground_truth.jsonl", encoding="utf-8")]

pool = [g for g in truth if tickets[g["ticket_id"]]["split"] == "test" and g.get("fix_scope") == "product"]
by_cat = defaultdict(list)
for g in pool:
    by_cat[g["root_cause_category"]].append(g["ticket_id"])

# note: round-robin across (noisy) LLM categories so rare ones show up; review fixes the labels
picked = []
while len(picked) < N and any(by_cat.values()):
    for ids in by_cat.values():
        if ids and len(picked) < N:
            picked.append(ids.pop(random.randrange(len(ids))))

open("data/clean/gold_candidates.txt", "w").write("\n".join(picked))
print(len(pool), "eligible test tickets ->", len(picked), "candidates")