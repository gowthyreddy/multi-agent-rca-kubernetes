"""Multi-agent RCA graph: triage -> (similar incidents || code investigator) -> synthesizer <-> grounding checker."""
import json, operator, os, re, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Annotated, TypedDict
import requests
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field
from rca_shared import CATEGORY_GUIDE, MODEL, Category, client, cost, load_jsonl, retrieve, ticket_text

MAX_STEPS, MAX_RETRIES, WINDOW = 14, 2, 150
REPO = "kubernetes/kubernetes"
GIT = ["git", "--git-dir", "data/k8s.git"]   # local bare clone: git clone --bare https://github.com/kubernetes/kubernetes.git data/k8s.git
GREP_EXCLUDE = [":(exclude)vendor/*", ":(exclude)*_test.go", ":(exclude)*zz_generated*", ":(exclude)api/openapi-spec/*"]
SHA_FILE = Path("data/base_sha.json")
gh = requests.Session()
gh.headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
lock = threading.Lock()

# ---------- code access, pinned to the commit the ticket was filed against ----------

def base_sha(created_at):
    with lock:
        shas = json.loads(SHA_FILE.read_text()) if SHA_FILE.exists() else {}
        if created_at not in shas:
            r = gh.get(f"https://api.github.com/repos/{REPO}/commits",
                       params={"sha": "master", "until": created_at, "per_page": 1}, timeout=30)
            r.raise_for_status()
            shas[created_at] = r.json()[0]["sha"]
            SHA_FILE.write_text(json.dumps(shas))
        return shas[created_at]

def git(*args):
    return subprocess.run(GIT + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace")

def has_commit(sha):
    return git("cat-file", "-e", f"{sha}^{{commit}}").returncode == 0

def ensure_commit(sha):
    """Tickets filed after the clone point at commits we don't have yet: fetch master once, then re-check."""
    if has_commit(sha):
        return
    with lock:   # one fetch even if several tickets hit this at once
        if not has_commit(sha):
            git("fetch", "--quiet", "origin", "+refs/heads/master:refs/heads/master")
    if not has_commit(sha):
        raise RuntimeError(f"commit {sha[:10]} missing from data/k8s.git even after fetch")

@lru_cache(maxsize=64)
def repo_tree(sha):
    return [p for p in git("ls-tree", "-r", "--name-only", sha).stdout.splitlines()
            if not p.startswith("vendor/") and not p.endswith("_test.go") and p.endswith((".go", ".proto", ".sh"))]

@lru_cache(maxsize=512)
def file_lines(sha, path):
    r = git("show", f"{sha}:{path}")
    return r.stdout.splitlines() if r.returncode == 0 else None

def search_code(sha, pattern):
    for flag in ("-E", "-F"):   # fall back to a literal search if the regex is invalid (exit code 128)
        r = git("grep", "-n", "-I", flag, "--max-count=3", "-e", pattern, sha, "--", *GREP_EXCLUDE)
        if r.returncode in (0, 1):
            break
    hits = [l.split(":", 1)[1][:220] for l in r.stdout.splitlines()]   # drop the "<sha>:" prefix
    return "\n".join(hits[:40]) + (f"\n... {len(hits) - 40} more matches, narrow the pattern" if len(hits) > 40 else "") or "no matches"

def find_files(tree, keyword):
    words = [w for w in re.split(r"[^a-z0-9_]+", keyword.lower()) if w]
    hits = sorted((p for p in tree if all(w in p.lower() for w in words)), key=len)
    return "\n".join(hits[:25]) or "no matching files"

def grep_file(sha, path, pattern):
    lines = file_lines(sha, path)
    if lines is None:
        return f"{path} not found at this commit"
    try:
        rx = re.compile(pattern, re.I)
    except re.error:
        rx = re.compile(re.escape(pattern), re.I)
    hits = [f"L{i}: {l.strip()[:200]}" for i, l in enumerate(lines, 1) if rx.search(l)]
    return "\n".join(hits[:40]) or "no matches"

def read_file(sha, path, start_line):
    lines = file_lines(sha, path)
    if lines is None:
        return f"{path} not found at this commit"
    s = max(1, start_line)
    return "\n".join(f"{i}: {l}" for i, l in enumerate(lines[s - 1:s - 1 + WINDOW], s)) + f"\n(file has {len(lines)} lines)"

def tool(name, desc, props):
    return {"type": "function", "function": {"name": name, "description": desc, "strict": True, "parameters": {
        "type": "object", "properties": props, "required": list(props), "additionalProperties": False}}}

S = {"type": "string"}
TOOLS = [
    tool("search_code", "Search file CONTENTS across the whole repo (no vendor/, no tests, no generated code) with a "
         "regex or literal, e.g. an error message, log line, metric or function name. Returns path:line: text.",
         {"pattern": S}),
    tool("find_files", "List source file paths (no vendor/, no tests) containing all words of the keyword.", {"keyword": S}),
    tool("grep_file", "Regex search inside one file; returns matching lines with line numbers.", {"path": S, "pattern": S}),
    tool("read_file", f"Read {WINDOW} lines of a file starting at start_line.", {"path": S, "start_line": {"type": "integer"}}),
    tool("report_findings", "Finish: report the code locations that explain the bug.", {"findings": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["path", "start_line", "end_line", "snippet", "observation"],
        "properties": {"path": S, "start_line": {"type": "integer"}, "end_line": {"type": "integer"},
                       "snippet": S, "observation": S}}}}),
]

# ---------- graph state and nodes ----------

class State(TypedDict, total=False):
    ticket: dict
    triage: dict
    evidence: Annotated[list, operator.add]
    diagnosis: dict
    feedback: str
    retries: int
    grounded: bool
    review: dict
    cost: Annotated[float, operator.add]
    tool_calls: Annotated[int, operator.add]

class Triage(BaseModel):
    summary: str = Field(description="One sentence: what is broken, observed behavior vs expected")
    components: list[str] = Field(description="Kubernetes components involved, e.g. kubelet, kube-proxy, scheduler")
    code_identifiers: list[str] = Field(description="Functions, types, files, flags, feature gates or log messages quoted in the ticket")

class Claim(BaseModel):
    text: str
    evidence_ids: list[str] = Field(description="ids of evidence items that support this claim")

class Diagnosis(BaseModel):
    root_cause: str = Field(description="1-3 sentences: WHY the bug happens (the defect and mechanism), not the symptom")
    root_cause_category: Category
    suspected_files: list[str] = Field(description="Repo paths of the defective code, taken from the evidence")
    claims: list[Claim] = Field(description="The reasoning chain; every claim cites evidence ids")

def llm_parse(schema, system, user):
    r = client.chat.completions.parse(model=MODEL, response_format=schema,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    return r.choices[0].message.parsed, cost(r.usage)

PATH_RX = re.compile(r"(?:blob/[0-9a-f]{7,40}/|blob/master/|blob/release-[\d.]+/)?((?:pkg|cmd|staging|plugin)/[\w./-]+\.(?:go|proto|sh))")

def mentioned_paths(t):
    return sorted({m.group(1) for m in PATH_RX.finditer(ticket_text(t))})

def triage(state):
    t = state["ticket"]
    tr, c = llm_parse(Triage, "Extract structured facts from a Kubernetes bug report.", ticket_text(t))
    # the ticket itself is citable evidence: claims restating the report cite "ticket"
    ticket_ev = {"id": "ticket", "source": "ticket", "paths": mentioned_paths(t), "url": t.get("url"),
                 "text": f"{t['title']}\n{t['description'][:1500]}"}
    return {"triage": tr.model_dump(), "evidence": [ticket_ev], "cost": c}

def similar_incidents(state):
    hits = retrieve(state["ticket"], 5)
    return {"evidence": [{"id": h["chunk_id"], "source": "past_incident", "text": h["text"], "url": h["url"],
                          "paths": h["text"].split("FILES: ", 1)[-1].split(", ")} for h in hits]}

INVESTIGATOR = f"""You are a Kubernetes engineer locating the code responsible for a bug report.
The repository is checked out at the commit the ticket was filed against.
Work efficiently, reading beats searching:
1. If the ticket quotes an error message, log line, metric, flag or function name, search_code for it first:
   that jumps straight to the code that emits or defines it. Search a distinctive fragment, not a whole sentence.
2. If FILES MENTIONED IN THE TICKET are listed, grep_file them for the function or message.
3. find_files only when you know a file name but not its location.
4. Spend most of your calls reading the relevant function and its callers until you can name the exact defect
   (wrong condition, missing check, wrong ordering, missing lock, wrong key...).
Never guess a path you have not seen in tool output or the mentioned-files list.
You have at most {MAX_STEPS} tool calls. Finish with report_findings: the exact locations and, for each,
what in that code explains the bug. If you found nothing convincing, report what you checked."""

def code_investigator(state):
    t = state["ticket"]
    sha = base_sha(t["created_at"])
    ensure_commit(sha)
    tree = repo_tree(sha)
    run = {"search_code": lambda a: search_code(sha, a["pattern"]),
           "find_files": lambda a: find_files(tree, a["keyword"]),
           "grep_file": lambda a: grep_file(sha, a["path"], a["pattern"]),
           "read_file": lambda a: read_file(sha, a["path"], a["start_line"])}
    msgs = [{"role": "system", "content": INVESTIGATOR},
            {"role": "user", "content": f"{ticket_text(t)}\n\nTRIAGE:\n{json.dumps(state['triage'])}\n\n"
                                        f"FILES MENTIONED IN THE TICKET (present at this commit):\n"
                                        + ("\n".join(p for p in mentioned_paths(t) if file_lines(sha, p)) or "(none)")}]
    total, findings = 0.0, []
    emit = get_stream_writer()   # live progress for the UI; a no-op in batch runs
    for step in range(MAX_STEPS):
        last = step == MAX_STEPS - 1
        r = client.chat.completions.create(model=MODEL, messages=msgs, tools=TOOLS, parallel_tool_calls=False,
            tool_choice={"type": "function", "function": {"name": "report_findings"}} if last else "required")
        total += cost(r.usage)
        m = r.choices[0].message
        msgs.append(m.model_dump(exclude_none=True))
        call = m.tool_calls[0]
        args = json.loads(call.function.arguments)
        if call.function.name == "report_findings":
            findings = args["findings"]
            break
        emit({"tool": call.function.name, "args": args})
        out = run[call.function.name](args)
        msgs.append({"role": "tool", "tool_call_id": call.id, "content": out[:8000]})
    evidence = [{"id": f"code-{i}", "source": "code", "paths": [f["path"]],
                 "url": f"https://github.com/{REPO}/blob/{sha}/{f['path']}#L{f['start_line']}-L{f['end_line']}",
                 "text": f"{f['path']} L{f['start_line']}-{f['end_line']} @ {sha[:10]}\n"
                         f"OBSERVATION: {f['observation']}\n{f['snippet'][:800]}"}
                for i, f in enumerate(findings, 1)]
    return {"evidence": evidence, "cost": total, "tool_calls": step + 1}

SYNTHESIZER = """You are an SRE writing the root cause of a Kubernetes bug report at triage time.
You get evidence: the ticket itself (ticket), code findings from the repository at the ticket's commit (code-*)
and similar past incidents (kb-*). Cite `ticket` for anything taken from the report itself. Explain the underlying defect and its mechanism, not the symptom.
Rules: every claim must cite evidence ids from the list; never cite an id that is not listed.
suspected_files must be paths that appear in the evidence. Prefer code evidence over past incidents;
use a past incident only if it is genuinely the same kind of bug.

""" + CATEGORY_GUIDE

def synthesizer(state):
    ev = "\n\n".join(f"[{e['id']}] ({e['source']})\n{e['text']}" for e in state["evidence"]) or "(no evidence)"
    user = f"{ticket_text(state['ticket'])}\n\nEVIDENCE:\n{ev}"
    if state.get("feedback"):
        user += f"\n\nYOUR PREVIOUS ANSWER FAILED THE GROUNDING CHECK. Fix these problems:\n{state['feedback']}"
    d, c = llm_parse(Diagnosis, SYNTHESIZER, user)
    return {"diagnosis": d.model_dump(), "cost": c}

def grounding_checker(state):
    d, ev = state["diagnosis"], state["evidence"]
    ids = {e["id"] for e in ev}
    paths = {p for e in ev for p in e["paths"]}
    dirs = {os.path.dirname(p) for p in paths}
    problems = [f"claim cites unknown id {i}" for c in d["claims"] for i in c["evidence_ids"] if i not in ids]
    problems += [f"claim has no evidence: {c['text'][:80]}" for c in d["claims"] if not c["evidence_ids"]]
    problems += [f"suspected file not in evidence: {f}" for f in d["suspected_files"]
                 if f not in paths and os.path.dirname(f) not in dirs and f.rstrip("/") not in dirs]
    if problems and state.get("retries", 0) < MAX_RETRIES:
        return {"feedback": "\n".join(problems), "retries": state.get("retries", 0) + 1}
    return {"grounded": not problems, "feedback": ""}

def human_review(state):
    # pauses the run; the UI resumes it with Command(resume={"action": ..., "root_cause": ..., ...})
    return {"review": interrupt({"diagnosis": state["diagnosis"]})}

def build_graph(checkpointer=None):
    """Batch eval: build_graph(). UI: build_graph(checkpointer) adds a human review step before END."""
    g = StateGraph(State)
    for name, fn in [("triage", triage), ("similar_incidents", similar_incidents),
                     ("code_investigator", code_investigator), ("synthesizer", synthesizer),
                     ("grounding_checker", grounding_checker)]:
        g.add_node(name, fn)
    done = END
    if checkpointer:
        g.add_node("human_review", human_review)
        g.add_edge("human_review", END)
        done = "human_review"
    g.add_edge(START, "triage")
    g.add_edge("triage", "similar_incidents")
    g.add_edge("triage", "code_investigator")
    g.add_edge(["similar_incidents", "code_investigator"], "synthesizer")
    g.add_edge("synthesizer", "grounding_checker")
    g.add_conditional_edges("grounding_checker", lambda s: "synthesizer" if s.get("feedback") else done,
                            ["synthesizer", done])
    return g.compile(checkpointer=checkpointer)

# ---------- batch run over the gold set ----------

if __name__ == "__main__":
    LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else None   # python graph.py 10 -> pilot
    OUT, TRACES = Path("results/graph.jsonl"), Path("results/graph_traces.jsonl")
    tickets = {t["ticket_id"]: t for t in load_jsonl("data/clean/tickets.jsonl")}
    gold = [g for g in load_jsonl("data/clean/ground_truth.jsonl") if g.get("gold")]
    done = {r["ticket_id"] for r in load_jsonl(OUT)} if OUT.exists() else set()
    todo = [g["ticket_id"] for g in gold if g["ticket_id"] not in done][:LIMIT]
    app = build_graph()

    def one(tid):
        start = time.time()
        s = app.invoke({"ticket": tickets[tid], "evidence": [], "cost": 0.0, "tool_calls": 0, "retries": 0})
        d = s["diagnosis"]
        rec = {"ticket_id": tid, "root_cause": d["root_cause"], "root_cause_category": d["root_cause_category"],
               "suspected_files": d["suspected_files"],
               "citations": sorted({i for c in d["claims"] for i in c["evidence_ids"]}),
               "retrieved": [e["id"] for e in s["evidence"]], "grounded": s["grounded"], "retries": s["retries"],
               "tool_calls": s["tool_calls"], "latency_s": round(time.time() - start, 2), "cost_usd": s["cost"]}
        with lock:
            with open(OUT, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            with open(TRACES, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ticket_id": tid, "triage": s["triage"], "evidence": s["evidence"], "diagnosis": d}) + "\n")
        print(f"{tid} -> {d['root_cause_category']} | tools={s['tool_calls']} retries={s['retries']} "
              f"grounded={s['grounded']} ${s['cost']:.4f}", flush=True)

    with ThreadPoolExecutor(max_workers=2) as pool:  # note: 200k TPM org limit; 4 workers tripped 429s
        for fut in [pool.submit(one, tid) for tid in todo]:
            try:
                fut.result()
            except Exception as e:   # one bad ticket shouldn't kill the batch; rerun resumes it
                print("FAILED:", repr(e)[:300], flush=True)
