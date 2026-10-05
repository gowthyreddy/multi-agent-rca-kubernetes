"""Correctness + leakage tests. No LLM calls, no cost.   python test_pipeline.py   (or: pytest test_pipeline.py)"""
import json, re
from datetime import datetime
from types import SimpleNamespace
import rca_shared
from graph import MAX_RETRIES, file_lines, git, grounding_checker, has_commit, mentioned_paths, search_code
from rca_shared import dir_hit, load_jsonl, ticket_text

TICKETS = {t["ticket_id"]: t for t in load_jsonl("data/clean/tickets.jsonl")}
GOLD = [g for g in load_jsonl("data/clean/ground_truth.jsonl") if g.get("gold")]
SHAS = json.load(open("data/base_sha.json"))
ts = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))

# ---------- unit: grounding checker ----------

EV = [{"id": "ticket", "paths": []}, {"id": "code-1", "paths": ["pkg/a/b/c.go"]}, {"id": "kb-1", "paths": ["pkg/x/y/z.go"]}]

def test_grounding_passes_when_every_claim_and_file_is_backed():
    d = {"claims": [{"text": "a", "evidence_ids": ["code-1"]}, {"text": "b", "evidence_ids": ["ticket", "kb-1"]}],
         "suspected_files": ["pkg/a/b/c.go", "pkg/a/b/sibling.go", "pkg/x/y/"]}
    assert grounding_checker({"diagnosis": d, "evidence": EV}) == {"grounded": True, "feedback": ""}

def test_grounding_rejects_unknown_ids_unbacked_claims_and_files():
    d = {"claims": [{"text": "a", "evidence_ids": ["kb-999"]}, {"text": "uncited claim", "evidence_ids": []}],
         "suspected_files": ["pkg/q/r.go"]}
    out = grounding_checker({"diagnosis": d, "evidence": EV, "retries": 0})
    assert out["retries"] == 1
    for needle in ("kb-999", "uncited claim", "pkg/q/r.go"):
        assert needle in out["feedback"], needle

def test_grounding_gives_up_after_max_retries_and_flags_it():
    d = {"claims": [{"text": "a", "evidence_ids": ["kb-999"]}], "suspected_files": []}
    assert grounding_checker({"diagnosis": d, "evidence": EV, "retries": MAX_RETRIES}) == {"grounded": False, "feedback": ""}

# ---------- unit: metrics + parsing ----------

def test_dir_hit():
    fix = ["pkg/registry/core/service/ipallocator/cidrallocator.go"]
    assert dir_hit(["pkg/registry/core/service/ipallocator/bitmap.go"], fix)   # same directory
    assert dir_hit(["pkg/registry/core/service/"], fix)                        # parent directory
    assert not dir_hit(["pkg/"], fix)                                          # too broad to count
    assert not dir_hit(["pkg/kubelet/kubelet.go"], fix)

def test_mentioned_paths():
    t = {"title": "x", "description": "see https://github.com/kubernetes/kubernetes/blob/abc1234/pkg/kubelet/kubelet.go#L10 "
                                      "and staging/src/k8s.io/api/core/v1/types.go, not vendor/foo/bar.go", "comments": []}
    assert mentioned_paths(t) == ["pkg/kubelet/kubelet.go", "staging/src/k8s.io/api/core/v1/types.go"]

# ---------- leakage: retrieval never returns incidents resolved after the ticket was filed ----------

def test_retrieval_is_time_bounded():
    j = max(range(len(rca_shared.kb)), key=lambda i: rca_shared.kb[i]["resolved_at"])   # newest KB doc
    fake = SimpleNamespace(embeddings=SimpleNamespace(
        create=lambda **_: SimpleNamespace(data=[SimpleNamespace(embedding=rca_shared._E[j].tolist())])))
    real, rca_shared.client = rca_shared.client, fake
    try:
        doc = rca_shared.kb[j]
        before = {"title": "", "description": "", "created_at": doc["resolved_at"]}          # filed at resolve time
        after = {"title": "", "description": "", "created_at": "2099-01-01T00:00:00Z"}
        assert doc not in rca_shared.retrieve(before, 5), "retrieved an incident resolved after the ticket"
        assert rca_shared.retrieve(after, 5)[0] is doc, "identical embedding should rank first when allowed"
    finally:
        rca_shared.client = real

# ---------- leakage: data invariants over the real gold set ----------

def test_gold_inputs_never_reference_the_fixing_pr():
    for g in GOLD:
        n = g["fix_ref"].rstrip("/").rsplit("/", 1)[-1]
        assert not re.search(rf"(?:#|pull/){n}\b", ticket_text(TICKETS[g["ticket_id"]])), g["ticket_id"]

def test_gold_comments_predate_the_fix():
    # build_dataset.py keeps comments posted before the fixing PR was opened (pre-fix discussion, NOT a strict
    # 24h triage window; see sensitivity_24h.py for that). The fix PR itself is redacted (test above).
    for g in GOLD:
        t = TICKETS[g["ticket_id"]]
        for c in t["comments"]:
            assert ts(c["created_at"]) < ts(t["resolved_at"]), g["ticket_id"]

def test_knowledge_base_is_train_only_and_disjoint_from_gold():
    kb_ids = {d["ticket_id"] for d in rca_shared.kb}
    assert not kb_ids & {g["ticket_id"] for g in GOLD}
    assert all(TICKETS[i]["split"] == "train" for i in kb_ids)

def test_code_snapshot_predates_every_gold_ticket():
    shas = {SHAS[TICKETS[g["ticket_id"]]["created_at"]] for g in GOLD}
    dates = dict(l.split() for l in git("show", "-s", "--format=%H %cI", *shas).stdout.split("\n") if l.strip())
    for g in GOLD:
        t = TICKETS[g["ticket_id"]]
        assert ts(dates[SHAS[t["created_at"]]]) <= ts(t["created_at"]), g["ticket_id"]

# ---------- integration: local git code tools ----------

def test_code_tools_read_the_pinned_commit():
    sha = SHAS["2025-01-24T09:56:39Z"]
    assert has_commit(sha) and not has_commit("0" * 40)
    assert "ipallocator/interfaces.go" in search_code(sha, "range is full")
    assert search_code(sha, "unbalanced(") != ""                         # invalid regex falls back to literal
    assert file_lines(sha, "pkg/registry/core/service/ipallocator/bitmap.go")
    assert file_lines(sha, "does/not/exist.go") is None

if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {e!r}"[:300])
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(failed)
