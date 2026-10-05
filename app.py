"""Streamlit UI: instant RAG answer, live multi-agent investigation, human review.   streamlit run app.py"""
import json, os, re, time, uuid
from datetime import datetime, timezone
from pathlib import Path
import requests
import streamlit as st
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from baseline import quick_diagnosis
from graph import REPO, build_graph
from rca_shared import Category, load_jsonl

FEEDBACK = Path("data/feedback.jsonl")
CATEGORIES = list(Category.__args__)
BOTS = {"k8s-ci-robot", "k8s-triage-robot"}
STEP_LABEL = {"triage": "Triage: extracted components and code identifiers",
              "similar_incidents": "Retrieved similar past incidents",
              "code_investigator": "Code investigation finished",
              "synthesizer": "Drafted diagnosis",
              "grounding_checker": "Grounding check"}

st.set_page_config(page_title="K8s Incident Copilot", layout="wide")

@st.cache_resource
def graph():
    # note: in-memory checkpointer survives Streamlit reruns but not a restart; Postgres/Cosmos saver on Azure
    return build_graph(checkpointer=InMemorySaver())

@st.cache_data
def known_tickets():
    return {t["ticket_id"]: t for t in load_jsonl("data/clean/tickets.jsonl")}

@st.cache_data(ttl=600)
def fetch_ticket(number):
    tid = f"gh:{REPO}#{number}"
    if tid in known_tickets():   # already cleaned + leak-redacted by build_dataset.py
        return known_tickets()[tid]
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    issue = s.get(f"https://api.github.com/repos/{REPO}/issues/{number}", timeout=30)
    issue.raise_for_status()
    i = issue.json()
    comments = s.get(i["comments_url"], params={"per_page": 50}, timeout=30).json()
    return {"ticket_id": tid, "title": i["title"], "url": i["html_url"], "created_at": i["created_at"],
            "description": re.sub(r"<!--.*?-->", "", i["body"] or "", flags=re.S).strip(),
            "comments": [{"author_role": c["author_association"], "text": c["body"], "created_at": c["created_at"]}
                         for c in comments if c["user"]["login"] not in BOTS and not c["user"]["login"].endswith("[bot]")]}

def render_diagnosis(d, evidence):
    by_id = {e["id"]: e for e in evidence}
    link = lambda i: f"[`{i}`]({by_id[i]['url']})" if by_id.get(i, {}).get("url") else f"`{i}`"
    st.markdown(f"**Category:** `{d['root_cause_category']}`")
    st.markdown(f"**Root cause:** {d['root_cause']}")
    st.markdown("**Suspected files:** " + (", ".join(f"`{f}`" for f in d["suspected_files"]) or "none"))
    if d.get("claims"):
        st.markdown("**Reasoning (every claim is grounded in evidence):**")
        for c in d["claims"]:
            st.markdown(f"- {c['text']} " + " ".join(link(i) for i in c["evidence_ids"]))

# ---------------- input ----------------
st.title("K8s Incident Copilot")
st.caption("Root-cause analysis for kubernetes/kubernetes issues: code read at the commit the issue was filed against.")
with st.sidebar:
    number = st.text_input("GitHub issue number", placeholder="e.g. 129797")
    st.caption("Or pick an eval ticket:")
    pick = st.selectbox("Eval ticket", [""] + sorted(known_tickets(), reverse=True), label_visibility="collapsed")
    go = st.button("Diagnose", type="primary", width="stretch")

if go:
    n = number.strip().lstrip("#") or pick.rsplit("#", 1)[-1]
    if not n.isdigit():
        st.error("Enter an issue number or pick a ticket.")
        st.stop()
    try:
        ticket = fetch_ticket(int(n))
    except requests.HTTPError as e:
        st.error(f"Could not fetch issue #{n}: {e}")
        st.stop()
    st.session_state.clear()
    st.session_state.update(ticket=ticket, thread={"configurable": {"thread_id": str(uuid.uuid4())}})

    st.subheader(ticket["title"])
    st.markdown(f"[{ticket['ticket_id']}]({ticket['url']}) · filed {ticket['created_at'][:10]}")
    left, right = st.columns(2)

    with left:
        st.markdown("#### Quick answer (single agent, ~2s)")
        with st.spinner("Retrieving similar incidents…"):
            q, hits, qcost = quick_diagnosis(ticket)
        st.session_state.quick = (q.model_dump(), [{"id": h["chunk_id"], "url": h["url"]} for h in hits])
        render_diagnosis(*st.session_state.quick)

    with right:
        st.markdown("#### Deep investigation (multi-agent)")
        start = time.time()
        with st.status("Investigating…", expanded=True) as status:
            inputs = {"ticket": ticket, "evidence": [], "cost": 0.0, "tool_calls": 0, "retries": 0}
            for mode, chunk in graph().stream(inputs, st.session_state.thread, stream_mode=["updates", "custom"]):
                if mode == "custom":
                    arg = next(iter(chunk["args"].values()))
                    st.write(f"`{chunk['tool']}` → `{str(arg)[:90]}`")
                    continue
                for node, update in chunk.items():
                    if node == "grounding_checker" and update and update.get("feedback"):
                        st.write("Grounding check failed, sending back to the synthesizer")
                    elif node in STEP_LABEL:
                        st.write(STEP_LABEL[node])
            status.update(label=f"Done in {time.time() - start:.0f}s", state="complete", expanded=False)
        s = graph().get_state(st.session_state.thread).values
        st.session_state.deep = s
        render_diagnosis(s["diagnosis"], s["evidence"])
        st.session_state.stats = f"{s['tool_calls']} tool calls · {s.get('retries', 0)} grounding retries · ${s['cost'] + qcost:.3f}"

# ---------------- results + review (re-rendered on every Streamlit rerun) ----------------
if "deep" in st.session_state and not go:
    t = st.session_state.ticket
    st.subheader(t["title"])
    st.markdown(f"[{t['ticket_id']}]({t['url']}) · filed {t['created_at'][:10]}")
    left, right = st.columns(2)
    with left:
        st.markdown("#### Quick answer (single agent)")
        render_diagnosis(*st.session_state.quick)
    with right:
        st.markdown("#### Deep investigation (multi-agent)")
        render_diagnosis(st.session_state.deep["diagnosis"], st.session_state.deep["evidence"])

if "deep" in st.session_state:
    s = st.session_state.deep
    st.caption(st.session_state.stats + (" · grounded" if s.get("grounded") else " · NOT fully grounded"))
    with st.expander(f"Evidence ({len(s['evidence'])} items)"):
        for e in s["evidence"]:
            st.markdown(f"**{e['id']}** ({e['source']})" + (f" · [open]({e['url']})" if e.get("url") else ""))
            st.code(e["text"][:1200], language=None)

    st.divider()
    st.markdown("### Review")
    if st.session_state.get("reviewed"):
        st.success(f"Saved: {st.session_state.reviewed}. Thanks, this becomes labeled data.")
    else:
        d = s["diagnosis"]
        with st.form("review"):
            action = st.radio("Verdict", ["approve", "edit", "reject"], horizontal=True)
            root = st.text_area("Root cause (edit if needed)", d["root_cause"], height=110)
            cat = st.selectbox("Category", CATEGORIES, index=CATEGORIES.index(d["root_cause_category"]))
            note = st.text_input("Note (optional)")
            if st.form_submit_button("Submit review"):
                decision = {"action": action, "root_cause": root, "root_cause_category": cat, "note": note}
                graph().invoke(Command(resume=decision), st.session_state.thread)
                with open(FEEDBACK, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"ticket_id": st.session_state.ticket["ticket_id"],
                                        "reviewed_at": datetime.now(timezone.utc).isoformat(),
                                        "model_diagnosis": d, **decision}) + "\n")
                st.session_state.reviewed = action
                st.rerun()
