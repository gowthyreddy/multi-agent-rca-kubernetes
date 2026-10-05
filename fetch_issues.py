import json, os, time
from datetime import date
from pathlib import Path
import requests

TOKEN = os.environ["GITHUB_TOKEN"]
REPO = "kubernetes/kubernetes"
START, END = date(2023, 1, 1), date(2025, 12, 31)
OUT = Path("data/raw"); OUT.mkdir(parents=True, exist_ok=True)

QUERY = """
query($q: String!, $cursor: String) {
  rateLimit { remaining resetAt }
  search(query: $q, type: ISSUE, first: 25, after: $cursor) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes { ... on Issue {
      number title body url createdAt closedAt stateReason
      labels(first: 30) { nodes { name } }
      comments(first: 30) { nodes { author { login } authorAssociation body createdAt } }
      closedByPullRequestsReferences(first: 3) { nodes {
        number title body url merged mergedAt
        files(first: 50) { nodes { path } }
      } }
    } }
  }
}"""

def post(q, cursor):
    for attempt in range(5):
        r = requests.post("https://api.github.com/graphql",
                          json={"query": QUERY, "variables": {"q": q, "cursor": cursor}},
                          headers={"Authorization": f"Bearer {TOKEN}"}, timeout=60)
        if r.ok and "errors" not in r.json():
            return r.json()["data"]
        print("retry", attempt, r.status_code, r.text[:200])
        time.sleep(10 * (attempt + 1))
    raise RuntimeError("giving up on " + q)

def month_windows(start, end):
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        yield f"{y}-{m:02d}", f"{date(y, m, 1)}..{date(ny, nm, 1)}"
        y, m = ny, nm

for name, window in month_windows(START, END):
    path = OUT / f"{name}.jsonl"
    if path.exists():
        continue  # already fetched, so reruns resume here
    q = f"repo:{REPO} is:issue is:closed reason:completed label:kind/bug closed:{window}"
    rows, cursor = [], None
    while True:
        data = post(q, cursor)
        rows += data["search"]["nodes"]
        if not data["search"]["pageInfo"]["hasNextPage"]:
            break
        cursor = data["search"]["pageInfo"]["endCursor"]
    if data["search"]["issueCount"] > 1000:
        print(f"WARNING {name}: {data['search']['issueCount']} results, split into weeks")
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    print(name, len(rows), "issues | points left:", data["rateLimit"]["remaining"])