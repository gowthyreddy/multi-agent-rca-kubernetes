import json
from pathlib import Path
import streamlit as st

CATS = ["logic_error", "race_condition", "missing_validation_or_nil_check", "incorrect_config_or_default",
        "resource_leak_or_exhaustion", "error_handling", "api_contract_or_compatibility",
        "version_skew_or_upgrade", "dependency_bug", "performance_regression",
        "state_or_cache_inconsistency", "security", "test_or_ci_issue", "other"]
VERDICTS = ["keep", "drop: input already states the fix", "drop: not a real bug", "drop: cause unclear"]
OUT = Path("data/clean/gold_reviews.jsonl")

tickets = {t["ticket_id"]: t for t in map(json.loads, open("data/clean/tickets.jsonl", encoding="utf-8"))}
truth = {g["ticket_id"]: g for g in map(json.loads, open("data/clean/ground_truth.jsonl", encoding="utf-8"))}
ids = open("data/clean/gold_candidates.txt").read().split()
done = {json.loads(l)["ticket_id"] for l in open(OUT, encoding="utf-8")} if OUT.exists() else set()
todo = [i for i in ids if i not in done]

st.set_page_config(layout="wide")
st.progress(len(done) / len(ids), f"{len(done)}/{len(ids)} reviewed")
if not todo:
    st.success("All reviewed. Run finalize_gold.py")
    st.stop()
tid = todo[0]
t, g = tickets[tid], truth[tid]

left, right = st.columns(2)
with left:
    st.subheader("What the agent sees")
    st.markdown(f"### [{t['title']}]({t['url']})")
    st.caption(f"type: {t['ticket_type']} · component: {t['component']}")
    if t.get("leak_suspect"):
        st.warning("leak_suspect: check if the input already states the fix")
    st.markdown(t["description"][:5000])
    for c in t["comments"]:
        st.divider()
        st.caption(c["author_role"])
        st.markdown(c["text"][:1500])

with right:
    st.subheader("Fix + extracted label")
    st.markdown(f"**Fix PR:** [{g['fix_pr_title']}]({g['fix_ref']})")
    with st.expander("PR description"):
        st.markdown(g["fix_pr_body"][:6000])
    st.caption("Files: " + ", ".join(g["fix_files"][:15]))
    st.info(f"Evidence: {g.get('evidence', '')}")
    with st.form(tid):
        text = st.text_area("Root cause", g["root_cause_text"], height=180)
        cat = st.selectbox("Category", CATS, index=CATS.index(g["root_cause_category"]))
        verdict = st.radio("Verdict", VERDICTS)
        if st.form_submit_button("Save & next"):
            with open(OUT, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ticket_id": tid, "verdict": verdict, "root_cause_text": text,
                                    "root_cause_category": cat,
                                    "edited_text": text != g["root_cause_text"],
                                    "edited_category": cat != g["root_cause_category"]}) + "\n")
            st.rerun()