"""Blind human spot-check of the LLM-reviewed gold set.   streamlit run human_spotcheck.py
Random sample (fixed seed) of 25 kept + 5 dropped candidates. The reviewer never sees the LLM's verdict;
agreement is computed once all 30 are done and saved to results/human_spotcheck.json."""
import json, random
from pathlib import Path
import streamlit as st
from rca_shared import Category, load_jsonl

OUT = Path("data/clean/human_spotcheck.jsonl")
SUMMARY = Path("results/human_spotcheck.json")
CATS = list(Category.__args__)
VERDICTS = ["keep", "drop: input already states the fix", "drop: not a real bug", "drop: cause unclear"]
CORRECT = ["correct", "partially correct", "wrong"]

tickets = {t["ticket_id"]: t for t in load_jsonl("data/clean/tickets.jsonl")}
truth = {g["ticket_id"]: g for g in load_jsonl("data/clean/ground_truth.jsonl")}
llm = {r["ticket_id"]: r for r in load_jsonl("data/clean/gold_reviews.jsonl")}

rng = random.Random(7)
kept = sorted(i for i, r in llm.items() if r["verdict"] == "keep")
dropped = sorted(i for i, r in llm.items() if r["verdict"] != "keep")
sample = rng.sample(kept, 25) + rng.sample(dropped, 5)
rng.shuffle(sample)   # so the dropped ones aren't all at the end

done = {r["ticket_id"]: r for r in load_jsonl(OUT)} if OUT.exists() else {}
todo = [i for i in sample if i not in done]

st.set_page_config(page_title="Gold set spot-check", layout="wide")
st.progress(len(done) / len(sample), f"{len(done)}/{len(sample)} checked")

if not todo:
    rows = [done[i] for i in sample]
    k = [r for r in rows if llm[r["ticket_id"]]["verdict"] == "keep" and r["verdict"] == "keep"]
    summary = {
        "n": len(rows),
        "keep_drop_agreement": sum((r["verdict"] == "keep") == (llm[r["ticket_id"]]["verdict"] == "keep") for r in rows),
        "kept_by_both": len(k),
        "root_cause_correct": sum(r["root_cause"] == "correct" for r in k),
        "root_cause_partially": sum(r["root_cause"] == "partially correct" for r in k),
        "root_cause_wrong": sum(r["root_cause"] == "wrong" for r in k),
        "category_agreement": sum(r["category"] == llm[r["ticket_id"]]["root_cause_category"] for r in k),
        "disagreements": [{"ticket_id": r["ticket_id"], "llm": llm[r["ticket_id"]]["verdict"], "human": r["verdict"],
                           "root_cause": r.get("root_cause"), "human_category": r.get("category"),
                           "llm_category": llm[r["ticket_id"]]["root_cause_category"], "note": r.get("note")}
                          for r in rows if (r["verdict"] == "keep") != (llm[r["ticket_id"]]["verdict"] == "keep")
                          or r.get("root_cause") in ("wrong", "partially correct")
                          or (r["verdict"] == "keep" and r.get("category") != llm[r["ticket_id"]]["root_cause_category"])],
    }
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    st.success("Spot-check complete. Saved to results/human_spotcheck.json")
    st.markdown(f"""
| | Human agrees with LLM review |
|---|---|
| Keep vs drop decision | **{summary['keep_drop_agreement']}/{summary['n']}** |
| Root cause correct (kept by both) | **{summary['root_cause_correct']}/{summary['kept_by_both']}** (+{summary['root_cause_partially']} partially) |
| Category matches | **{summary['category_agreement']}/{summary['kept_by_both']}** |
""")
    if summary["disagreements"]:
        st.markdown("**Where you disagreed:**")
        st.json(summary["disagreements"])
    st.stop()

tid = todo[0]
t, g, label = tickets[tid], truth[tid], llm[tid]
left, right = st.columns(2)
with left:
    st.subheader("The issue")
    st.markdown(f"### [{t['title']}]({t['url']})")
    st.markdown(t["description"][:6000])
    for c in t["comments"]:
        st.divider()
        st.caption(c["author_role"])
        st.markdown(c["text"][:1500])
with right:
    st.subheader("The fix")
    st.markdown(f"**Fix PR:** [{g['fix_pr_title']}]({g['fix_ref']})")
    with st.expander("PR description"):
        st.markdown(g["fix_pr_body"][:6000])
    st.caption("Files changed: " + ", ".join(g["fix_files"][:15]))
    st.subheader("Candidate label")
    st.info(f"**Root cause:** {label['root_cause_text']}\n\n**Category:** `{label['root_cause_category']}`")
    with st.form(tid):
        verdict = st.radio("1. Should this ticket be in the gold eval set?", VERDICTS, index=None)
        root = st.radio("2. If kept: is the root cause above correct, judged against the fix PR?", CORRECT, index=None)
        cat = st.selectbox("3. If kept: which category fits best?", CATS, index=None, placeholder="choose one")
        note = st.text_input("Note (optional)")
        if st.form_submit_button("Save & next"):
            if verdict is None:
                st.error("Answer question 1.")
            elif verdict == "keep" and (root is None or cat is None):
                st.error("For a kept ticket, answer questions 2 and 3.")
            else:
                with open(OUT, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"ticket_id": tid, "verdict": verdict, "root_cause": root,
                                        "category": cat, "note": note}) + "\n")
                st.rerun()
