import json, glob, re
from collections import Counter
from pathlib import Path

REPO = "kubernetes/kubernetes"
SPLIT_DATE = "2025-01-01"   # train = closed before, test = closed after
BOTS = {"k8s-ci-robot", "k8s-triage-robot"}
OUT = Path("data/clean"); OUT.mkdir(parents=True, exist_ok=True)

def clean(text):
    text = re.sub(r"<!--.*?-->", "", text or "", flags=re.S)   # issue-template comments
    text = re.sub(r"(?m)^/\S.*$", "", text)                     # prow commands: /kind bug, /assign
    return re.sub(r"\n{3,}", "\n\n", text).strip()

def redact(text, issue_number, pr_numbers):
    def sub(m):
        nums = [int(n) for n in re.findall(r"(?:pull/|#)(\d+)", m.group(0))]
        return "[REDACTED]" if any(n > issue_number or n in pr_numbers for n in nums) else m.group(0)
    return re.sub(r"\S*(?:pull/|#)\d+\S*", sub, text)

def is_bot(c):
    a = c["author"]
    return a is None or a["login"] in BOTS or a["login"].endswith("[bot]")

def label(labels, prefix):
    return next((l[len(prefix):] for l in labels if l.startswith(prefix)), None)

def release_note(body):
    m = re.search(r"```release-note\s*(.*?)```", body or "", re.S)
    note = m.group(1).strip() if m else ""
    return "" if note.upper() == "NONE" else note

raw = {}
for f in sorted(glob.glob("data/raw/*.jsonl")):
    for line in open(f, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            raw[r["number"]] = r   # dedup: month windows overlap by a day

tickets, truth = [], []
for r in raw.values():
    prs = [p for p in r["closedByPullRequestsReferences"]["nodes"] if p["merged"]]
    body = clean(r["body"])
    if not prs or len(body) < 50:
        continue
    pr = prs[0]
    cutoff = pr.get("createdAt") or pr["mergedAt"]   # ISO strings compare correctly
    labels = [l["name"] for l in r["labels"]["nodes"]]
    pr = prs[0]
    pr_numbers = [p["number"] for p in r["closedByPullRequestsReferences"]["nodes"]]
    comments = []
    for c in r["comments"]["nodes"]:
        text = redact(clean(c["body"]),r["number"], pr_numbers)
        if not is_bot(c) and c["createdAt"] < cutoff and text:
            comments.append({"author_role": c["authorAssociation"], "text": text, "created_at": c["createdAt"]})
    tid = f"gh:{REPO}#{r['number']}"

    tickets.append({
        "ticket_id": tid,
        "source": "github",
        "project": REPO,
        "title": r["title"],
        "description": redact(body, r["number"], pr_numbers),
        "comments": comments,
        "ticket_type": label(labels, "sig/"),
        "component": label(labels, "area/"),
        "severity": label(labels, "priority/"),
        "labels_raw": labels,
        "created_at": r["createdAt"],
        "resolved_at": r["closedAt"],
        "url": r["url"],
        "split": "train" if r["closedAt"] < SPLIT_DATE else "test",
        "leak_suspect": bool(re.search(r"fixed by|should fix",
                                               body + " ".join(c["text"] for c in comments), re.I)),
    })
    truth.append({
        "ticket_id": tid,
        "root_cause_category": None,   # filled by LLM extraction (next step)
        "root_cause_text": None,       # filled by LLM extraction (next step)
        "fix_summary": release_note(pr["body"]) or pr["title"],
        "fix_pr_title": pr["title"],
        "fix_pr_body": clean(pr["body"]),
        "fix_ref": pr["url"],
        "fix_files": [f["path"] for f in pr["files"]["nodes"]],
        "resolution_status": "fixed",
        "label_source": "pending",
        "confidence": None,
    })

for name, rows in [("tickets", tickets), ("ground_truth", truth)]:
    (OUT / f"{name}.jsonl").write_text("\n".join(json.dumps(x) for x in rows), encoding="utf-8")

print("raw issues:", len(raw), "| kept:", len(tickets))
print("split:", Counter(t["split"] for t in tickets))
print("no ticket_type:", sum(t["ticket_type"] is None for t in tickets))
print("top types:", Counter(t["ticket_type"] for t in tickets).most_common(15))