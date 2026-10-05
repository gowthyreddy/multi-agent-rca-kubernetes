import json, os, sys
from pathlib import Path
from typing import Literal
from openai import OpenAI
from pydantic import BaseModel, Field

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=5)
MODEL = os.environ["MODEL"]
PRICE_IN, PRICE_OUT = 0.40, 1.60   # $ per 1M tokens: set from the pricing page for your MODEL
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else None   # python extract_rca.py 20  -> pilot run
OUT = Path("data/clean/rca_extracted.jsonl")

Category = Literal[
    "logic_error", "race_condition", "missing_validation_or_nil_check", "incorrect_config_or_default",
    "resource_leak_or_exhaustion", "error_handling", "api_contract_or_compatibility",
    "version_skew_or_upgrade", "dependency_bug", "performance_regression",
    "state_or_cache_inconsistency", "security", "test_or_ci_issue", "other",
]

class RCA(BaseModel):
    is_real_bug: bool = Field(description="False for docs, flaky tests, feature requests, refactors/cleanups, "
                                          "or version bumps with no user-visible defect")
    evidence: str = Field(description="Short quote from the issue or PR that states or shows the cause")
    cause_source: Literal["explicit", "inferred", "unclear"] = Field(
        description="explicit: the issue/PR states the cause in words; "
                    "inferred: you deduced it from the diff or file names; unclear: you are guessing")
    root_cause_text: str = Field(description="1-3 sentences: WHY the bug happened, not what the fix changed")
    root_cause_category: Category

SYSTEM = """You label root causes for software bug reports.
You get a GitHub issue and the merged PR that fixed it.
Explain the underlying cause (the defect in the code or config), not the symptom and not the patch.
Use only information in the issue and PR.

Categories. Pick the MOST SPECIFIC one; use logic_error only if nothing else fits:
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

tickets = {t["ticket_id"]: t for t in map(json.loads, open("data/clean/tickets.jsonl", encoding="utf-8"))}
truth = [json.loads(l) for l in open("data/clean/ground_truth.jsonl", encoding="utf-8")]
done = {json.loads(l)["ticket_id"] for l in open(OUT, encoding="utf-8")} if OUT.exists() else set()
todo = [g for g in truth if g["ticket_id"] not in done][:LIMIT]

tok_in = tok_out = 0
with open(OUT, "a", encoding="utf-8") as out:
    for i, g in enumerate(todo):
        t = tickets[g["ticket_id"]]
        user = (f"ISSUE TITLE: {t['title']}\n\nISSUE DESCRIPTION:\n{t['description'][:4000]}\n\n"
                f"FIX PR TITLE: {g['fix_pr_title']}\n\nFIX PR DESCRIPTION:\n{g['fix_pr_body'][:6000]}\n\n"
                f"FILES CHANGED:\n" + "\n".join(g["fix_files"][:30]))
        resp = client.chat.completions.parse(
            model=MODEL, response_format=RCA,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}])
        rca = resp.choices[0].message.parsed
        out.write(json.dumps({"ticket_id": g["ticket_id"], **rca.model_dump()}) + "\n")
        out.flush()
        tok_in += resp.usage.prompt_tokens
        tok_out += resp.usage.completion_tokens
        cost = tok_in / 1e6 * PRICE_IN + tok_out / 1e6 * PRICE_OUT
        print(f"{i+1}/{len(todo)} {g['ticket_id']} {rca.root_cause_category} | ${cost:.3f}")

if todo:
    print(f"avg ${cost/len(todo):.4f}/ticket -> est. ${cost/len(todo)*len(truth):.2f} for all {len(truth)}")