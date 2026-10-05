import json, sys, time
from pathlib import Path
from pydantic import BaseModel, Field
from rca_shared import CATEGORY_GUIDE, MODEL, Category, client, cost, load_jsonl, retrieve, ticket_text

class Diagnosis(BaseModel):
    root_cause: str = Field(description="1-3 sentences: WHY the bug happens (the defect), not the symptom")
    root_cause_category: Category
    suspected_files: list[str] = Field(description="Repo file paths or directories most likely containing the defect")
    citations: list[str] = Field(description="chunk_ids of past incidents you actually relied on; empty if none helped")

SYSTEM = """You are an SRE diagnosing the root cause of a Kubernetes bug report at triage time.
Explain the underlying defect, not the symptom. Be specific about the component and mechanism.
You may get similar past incidents. Use them only if they are genuinely relevant, cite them by chunk_id,
and never cite a chunk_id that was not given to you.

""" + CATEGORY_GUIDE

def quick_diagnosis(t, k=5):
    """Single-agent RAG answer: one retrieval + one LLM call. Returns (Diagnosis, hits, cost_usd)."""
    hits = retrieve(t, k)
    user = ticket_text(t)
    if hits:
        user += "\n\nSIMILAR PAST INCIDENTS:\n" + "\n\n".join(f"[{h['chunk_id']}]\n{h['text']}" for h in hits)
    resp = client.chat.completions.parse(model=MODEL, response_format=Diagnosis,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}])
    return resp.choices[0].message.parsed, hits, cost(resp.usage)

if __name__ == "__main__":
    K = int(sys.argv[1]) if len(sys.argv) > 1 else 5          # python baseline.py 5      (k=0 -> no RAG)
    LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else None   # python baseline.py 5 10   (pilot)
    OUT = Path(f"results/baseline_k{K}.jsonl"); OUT.parent.mkdir(exist_ok=True)
    tickets = {t["ticket_id"]: t for t in load_jsonl("data/clean/tickets.jsonl")}
    gold = [g for g in load_jsonl("data/clean/ground_truth.jsonl") if g.get("gold")]
    done = {r["ticket_id"] for r in load_jsonl(OUT)} if OUT.exists() else set()
    todo = [g for g in gold if g["ticket_id"] not in done][:LIMIT]

    with open(OUT, "a", encoding="utf-8") as out:
        for i, g in enumerate(todo):
            start = time.time()
            d, hits, c = quick_diagnosis(tickets[g["ticket_id"]], K)
            out.write(json.dumps({"ticket_id": g["ticket_id"], **d.model_dump(),
                                  "retrieved": [h["chunk_id"] for h in hits],
                                  "latency_s": round(time.time() - start, 2), "cost_usd": c}) + "\n")
            out.flush()
            print(f"{i+1}/{len(todo)} {g['ticket_id']} -> {d.root_cause_category}")
