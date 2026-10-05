import json
from collections import Counter

reviews = {r["ticket_id"]: r for r in map(json.loads, open("data/clean/gold_reviews.jsonl", encoding="utf-8"))}
truth = [json.loads(l) for l in open("data/clean/ground_truth.jsonl", encoding="utf-8")]

for g in truth:
    r = reviews.get(g["ticket_id"])
    g["gold"] = bool(r) and r["verdict"] == "keep"
    if r:
        g["review_verdict"] = r["verdict"]
    if g["gold"]:
        g.update(root_cause_text=r["root_cause_text"], root_cause_category=r["root_cause_category"],
                 label_source="llm_reviewed" if r.get("reviewer") == "llm" else "human_verified")

open("data/clean/ground_truth.jsonl", "w", encoding="utf-8").write("\n".join(json.dumps(g) for g in truth))

kept = [r for r in reviews.values() if r["verdict"] == "keep"]
print("verdicts:", Counter(r["verdict"] for r in reviews.values()))
print("gold:", len(kept))
print(f"LLM text accepted as-is: {1 - sum(r['edited_text'] for r in kept) / len(kept):.0%}")
print(f"LLM category correct:    {1 - sum(r['edited_category'] for r in kept) / len(kept):.0%}")
print("gold categories:", Counter(r["root_cause_category"] for r in kept).most_common())