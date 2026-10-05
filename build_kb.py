import json, os
from pathlib import Path
import numpy as np
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(max_retries=5)
EMBED = os.environ.get("EMBED_MODEL", "text-embedding-3-small")
OUT = Path("data/kb"); OUT.mkdir(parents=True, exist_ok=True)

tickets = {t["ticket_id"]: t for t in map(json.loads, open("data/clean/tickets.jsonl", encoding="utf-8"))}
truth = [json.loads(l) for l in open("data/clean/ground_truth.jsonl", encoding="utf-8")]

docs = []
for g in truth:
    t = tickets[g["ticket_id"]]
    if t["split"] != "train" or g.get("fix_scope") != "product" or g.get("is_real_bug") is False or not g.get("root_cause_text"):
        continue
    docs.append({
        "chunk_id": "kb-" + t["ticket_id"].rsplit("#", 1)[1],
        "ticket_id": t["ticket_id"], "url": t["url"], "resolved_at": t["resolved_at"],
        "embed_text": f"{t['title']}\n{t['description']}"[:6000],   # symptom side: matches new tickets
        "text": (f"TITLE: {t['title']}\nSYMPTOM: {t['description'][:1200]}\n"
                 f"ROOT CAUSE: {g['root_cause_text']}\nFIX: {g['fix_summary']}\n"
                 f"FILES: {', '.join(g['fix_files'][:10])}"),
    })

vecs = []
for i in range(0, len(docs), 100):
    r = client.embeddings.create(model=EMBED, input=[d["embed_text"] for d in docs[i:i + 100]])
    vecs += [e.embedding for e in r.data]
E = np.array(vecs, dtype=np.float32)
E /= np.linalg.norm(E, axis=1, keepdims=True)

np.save(OUT / "kb_emb.npy", E)
(OUT / "kb.jsonl").write_text("\n".join(json.dumps(d) for d in docs), encoding="utf-8")
print(len(docs), "KB docs embedded")
