import json, os
from typing import Literal
import numpy as np
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
MODEL = os.environ.get("MODEL", "gpt-4.1-mini")
EMBED = os.environ.get("EMBED_MODEL", "text-embedding-3-small")
PRICE_IN, PRICE_OUT = 0.40, 1.60   # $/1M tokens for MODEL: set from the pricing page
client = OpenAI(max_retries=10)

Category = Literal[
    "logic_error", "race_condition", "missing_validation_or_nil_check", "incorrect_config_or_default",
    "resource_leak_or_exhaustion", "error_handling", "api_contract_or_compatibility",
    "version_skew_or_upgrade", "dependency_bug", "performance_regression",
    "state_or_cache_inconsistency", "security", "test_or_ci_issue", "other",
]

CATEGORY_GUIDE = """Categories. Pick the MOST SPECIFIC one; use logic_error only if nothing else fits:
- logic_error: wrong algorithm, calculation or condition in this project's own code
- race_condition: ordering, concurrency or timing between goroutines, controllers or requests
- missing_validation_or_nil_check: invalid input or state not checked (nil, bounds, invalid object accepted)
- incorrect_config_or_default: wrong default value, flag, timeout, permission or constant
- resource_leak_or_exhaustion: leaked memory/goroutines/fds/connections, unbounded growth
- error_handling: error ignored, misclassified, or logged/propagated wrongly
- api_contract_or_compatibility: API schema/field misuse or a broken contract between components
- version_skew_or_upgrade: breaks across versions/upgrades, or code relies on a deprecated/old API version
- dependency_bug: the defect lives in a third-party library or vendored dependency
- performance_regression: correct but too slow or too expensive
- state_or_cache_inconsistency: stale cache, informer or state out of sync
- security: authn/authz bypass, privilege escalation, secret exposure
- test_or_ci_issue: bug only in tests, test infrastructure or CI
- other"""

def load_jsonl(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]

def cost(usage):
    return usage.prompt_tokens / 1e6 * PRICE_IN + usage.completion_tokens / 1e6 * PRICE_OUT

def dir_hit(pred_paths, fix_files):
    # note: directory-prefix match, needs >=2 path segments so "pkg/" alone doesn't count
    gold_dirs = {os.path.dirname(f) for f in fix_files}
    pred_dirs = {p.rstrip("/") if "." not in os.path.basename(p) else os.path.dirname(p) for p in pred_paths}
    return any(gd.startswith(pd) for pd in pred_dirs if pd.count("/") >= 2 for gd in gold_dirs)

def ticket_text(t):
    comments = "\n---\n".join(c["text"] for c in t["comments"])[:3000]
    return f"TICKET: {t['title']}\n\nDESCRIPTION:\n{t['description'][:4000]}\n\nCOMMENTS:\n{comments or '(none)'}"

kb = load_jsonl("data/kb/kb.jsonl")
_E = np.load("data/kb/kb_emb.npy")
_kb_resolved = np.array([d["resolved_at"] for d in kb])

def retrieve(t, k):
    if k == 0:
        return []
    q = np.array(client.embeddings.create(model=EMBED, input=f"{t['title']}\n{t['description']}"[:6000]).data[0].embedding)
    scores = _E @ (q / np.linalg.norm(q))
    scores[_kb_resolved >= t["created_at"]] = -np.inf   # time-bounded: nothing resolved after the ticket was filed
    # note: brute-force cosine over a few hundred docs; Azure AI Search replaces this at deploy time
    return [kb[i] for i in np.argsort(-scores)[:k] if np.isfinite(scores[i])]
