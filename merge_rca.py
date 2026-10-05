import json
from collections import Counter

rca = {r["ticket_id"]: r for r in map(json.loads, open("data/clean/rca_extracted.jsonl", encoding="utf-8"))}
truth = [json.loads(l) for l in open("data/clean/ground_truth.jsonl", encoding="utf-8")]

def fix_scope(files):
    if files and all(f.endswith(".md") for f in files):
        return "docs"
    if files and all(f.startswith(("hack/", "build/", "test/", ".github/")) or f.endswith("_test.go") for f in files):
        return "tooling_or_test"
    return "product"

for g in truth:
    r = rca.get(g["ticket_id"])
    if r:
            g.update(root_cause_text=r["root_cause_text"], root_cause_category=r["root_cause_category"],
                 cause_source=r["cause_source"], is_real_bug=r["is_real_bug"], evidence=r["evidence"],
                 fix_scope=fix_scope(g["fix_files"]), label_source="llm_extracted")

open("data/clean/ground_truth.jsonl", "w", encoding="utf-8").write("\n".join(json.dumps(g) for g in truth))

print("categories:", Counter(g.get("root_cause_category") for g in truth).most_common())
print("cause_source:", Counter(g.get("cause_source") for g in truth))
print("fix_scope:", Counter(fix_scope(g["fix_files"]) for g in truth))
print("gold-eligible (real bug, product, explicit):",
      sum(g.get("is_real_bug") and g.get("cause_source") == "explicit"
          and fix_scope(g["fix_files"]) == "product" for g in truth))