"""Run with: python -m streamlit run app.py"""
import os
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv

from coworkers.agents import DailyTechUpgradeAgent, RUBRIC, nextLevelPersonalCoachForCareerLevel
from coworkers.llm import AIError, Reasoner
from coworkers.memory import Memory
from coworkers.search import SearchError, SearchTool

load_dotenv()
st.set_page_config(page_title="Personal AI Coworkers", page_icon="🎓", layout="wide")
memory = Memory(os.getenv("DATABASE_PATH", "data/coworkers.db"))
learning = DailyTechUpgradeAgent(memory, SearchTool(), Reasoner())
coach = nextLevelPersonalCoachForCareerLevel(memory, SearchTool(), Reasoner())


def run_action(label, action):
    try:
        with st.spinner(label):
            return action()
    except (AIError, SearchError, ValueError) as exc:
        st.error(str(exc))
    except Exception:
        st.error("The operation failed. Check your local database permissions and configuration, then retry.")
    return None


def show_brief(brief):
    if brief.get("selection", {}).get("method") == "deterministic_topic_overlap":
        st.caption("Article selection used a local fallback: topic overlap (weak skills weighted twice), "
                   "then publication date and discovery order. The AI ranking was unusable.")
    st.markdown(brief["brief"])
    for article in brief["articles"]:
        st.subheader(article["title"])
        st.write(f'{article["source"]} · Published {article["published"]}')
        st.link_button("Read source", article["url"])
        st.caption(article["verification"])
    with st.expander("Search verification details"):
        st.json(brief.get("selection", {}))
        st.json(brief.get("search_attempts", []))
        st.json(brief.get("checks", []))


st.title("Personal AI Coworkers")
st.caption("Learn something useful. Practice for your next career step. Keep your progress locally.")
with st.sidebar:
    st.header("Settings / Profile")
    profile = memory.get("profile", {})
    with st.form("profile"):
        name = st.text_input("Name", profile.get("name", ""), max_chars=100)
        interests = st.text_area("Technical interests", profile.get("interests", "Python, RAG, agentic AI"), max_chars=1000)
        career = st.text_input("Target career", profile.get("career", "AI Software Engineer"), max_chars=200)
        goals = st.text_area("Learning and career goals", profile.get("goals", "Prepare for internships"), max_chars=2000)
        experience = st.selectbox("Experience", ["Beginner", "Intermediate", "Advanced"],
                                  index=["Beginner", "Intermediate", "Advanced"].index(profile.get("experience", "Beginner")))
        if st.form_submit_button("Save profile"):
            memory.put("profile", dict(name=name, interests=interests, career=career, goals=goals, experience=experience))
            st.success("Profile saved")
    st.caption("Keys are read from .env; no keys are stored in your profile.")
    st.write("Search provider:", os.getenv("SEARCH_PROVIDER", "tavily"))
    st.write("OpenAI key:", "Configured" if os.getenv("OPENAI_API_KEY") else "Missing")
    enabled = st.checkbox("Automatic daily brief", value=memory.get("auto_daily", False))
    memory.put("auto_daily", enabled)
    hour = st.number_input("Daily hour (server local time)", 0, 23, value=memory.get("daily_hour", 9))
    memory.put("daily_hour", hour)
    st.caption("Checks every 60 seconds during an active browser session. No runs while the app/session is closed. "
               "One automatic attempt per calendar day; failures require a manual retry. Manual runs can incur additional API charges.")
    st.caption("Profile, answers and retrieved content are sent to the configured AI/search providers as needed. "
               "SQLite is local and unencrypted. This is a single-user local app.")


@st.fragment(run_every="60s")
def daily_check():
    now = datetime.now().astimezone()
    if memory.get("auto_daily", False) and now.hour >= memory.get("daily_hour", 9):
        day = now.date().isoformat()
        if memory.claim_day(day):
            result = run_action("Preparing automatic daily brief…", learning.run)
            if result and result["articles"]:
                memory.finish_day(day)
                st.success("Automatic brief saved. Open Learning History to read it.")
            else:
                st.warning("Automatic brief did not complete. Use Generate daily brief to retry manually.")


daily_check()
daily, career_tab, history, dashboard = st.tabs(["Daily Learning", "Career Coach", "Learning History", "Progress Dashboard"])
with daily:
    st.header("Your 18-minute learning upgrade")
    st.write("Find up to three new authoritative articles from the past 30 days, guided by your interests and interview gaps.")
    if st.button("Generate daily brief", type="primary"):
        result = run_action("Searching, verifying and preparing your brief…", learning.run)
        if result:
            st.session_state["brief"] = result
    if "brief" in st.session_state:
        show_brief(st.session_state["brief"])
        if st.session_state["brief"].get("event_id"):
            with st.form("learning_feedback"):
                usefulness = st.slider("How useful was this brief?", 1, 5, 3)
                notes = st.text_input("What did you learn? What should we focus on next?")
                completed = st.checkbox("I completed this learning session")
                if st.form_submit_button("Save learning feedback"):
                    memory.add("learning", {"brief_id": st.session_state["brief"]["event_id"], "usefulness": usefulness,
                                           "notes": notes, "completed": completed})
                    st.success("Learning progress saved")

with career_tab:
    st.header("Career coach")
    kind = st.selectbox("Opportunity type", ["internship", "job", "research opportunity"])
    location = st.text_input("Location / remote preference", "San Jose, CA or remote")
    if st.button("Find real opportunities"):
        result = run_action("Searching and analyzing opportunity pages…", lambda: coach.opportunities(kind, location))
        if result:
            st.session_state["opportunities"] = result
    if "opportunities" in st.session_state:
        result = st.session_state["opportunities"]
        st.markdown(result["analysis"])
        st.caption("A fetched page confirms reachability, not that a vacancy remains open. Confirm eligibility and availability on the employer page.")
        for item in result["items"]:
            st.write(item.get("title", "Search result"))
            if item["verified"]:
                st.link_button("Open opportunity source", item["url"])
            st.caption(item["verification"])
    st.subheader("Practice interview")
    with st.expander("Scoring rubric (0–4 per criterion)"):
        st.json(RUBRIC)
        st.caption("AI assessment by inspection; submitted code is never executed. Scores are practice feedback, not hiring predictions.")
    with st.form("new_interview"):
        skill = st.text_input("Skill", "RAG")
        style = st.selectbox("Practice style", ["Technical discussion", "Coding exercise"])
        job_text = st.text_area("Optional job description", max_chars=16000)
        if st.form_submit_button("Start new interview"):
            run_action("Preparing a personalized question…", lambda: coach.start(skill, style, job_text))
    session = memory.get("interview")
    if session:
        st.caption(f'Session {session["session_id"][:8]} · {session["career"]} · {session["skill"]}')
        for turn in session["turns"]:
            with st.expander(f'Answer {session["turns"].index(turn) + 1}: {turn["score"]}/4'):
                st.markdown(turn["question"])
                st.text(turn["answer"])
                st.json(turn["evaluation"])
        if session.get("active") and session.get("question"):
            st.markdown(session["question"])
            with st.form("answer_" + str(len(session["turns"]))):
                answer = st.text_area("Your answer / code", max_chars=16000)
                if st.form_submit_button("Evaluate answer"):
                    if run_action("Evaluating against the rubric…", lambda: coach.answer(answer)):
                        st.rerun()
        elif session.get("active"):
            if st.button("Ask a follow-up"):
                if run_action("Preparing follow-up…", coach.follow_up):
                    st.rerun()
        if session.get("active") and st.button("End interview"):
            session["active"] = False
            memory.put("interview", session)
            st.rerun()
    if st.button("Generate Career Readiness Report"):
        report = run_action("Reviewing recorded progress…", coach.report)
        if report:
            st.session_state["report"] = report
    if "report" in st.session_state:
        st.markdown(st.session_state["report"])
        st.download_button("Download report", st.session_state["report"], "career-readiness.md")

with history:
    st.header("Saved learning and reports")
    briefs = memory.events("brief")
    if not briefs:
        st.info("Your saved briefs will appear here after a successful run.")
    for brief in briefs:
        with st.expander(brief["created"] + " · Daily brief"):
            show_brief(brief)
    for report in memory.events("report"):
        with st.expander(report["created"] + " · Career report"):
            st.markdown(report["text"])
    st.subheader("Learning reflections")
    st.dataframe(memory.events("learning"), width="stretch")

with dashboard:
    st.header("Progress based on recorded practice")
    answers = memory.events("answer")
    a, b, c = st.columns(3)
    a.metric("Interview answers", len(answers))
    b.metric("Saved briefs", len(memory.events("brief")))
    c.metric("Completed learning sessions", len({e["brief_id"] for e in memory.events("learning") if e["completed"]}))
    weaknesses = memory.interview_weaknesses()
    st.write("Skills to prioritize:", ", ".join(dict.fromkeys(w["skill"] for w in weaknesses)) or "No recorded gaps yet")
    for weakness in weaknesses:
        evidence = list(weakness["gaps"])
        evidence.extend(f"{name}: {score}/4" for name, score in weakness["rubric_gaps"].items())
        if weakness["low_overall"]:
            evidence.append(f'Overall practice score: {weakness["score"]}/4')
        st.write(f'{weakness["skill"]} ({weakness["career"]}): ' + "; ".join(evidence))
    st.caption("Priorities use recorded gaps and scores below 3/4 from the latest assessment for each career and skill. "
               "Older feedback remains in interview history; readiness reports may discuss broader history.")
    if answers:
        rows = [{"time": t["created"], "career": t["career"], "skill": t["skill"], "score": t["score"],
                 "session": t["session_id"][:8]} for t in reversed(answers)]
        st.dataframe(rows, width="stretch")
        st.caption("Compare scores within the same career and skill. Questions vary in difficulty; this is a practice log.")
    else:
        st.info("Complete an interview answer to begin tracking skill progress.")
