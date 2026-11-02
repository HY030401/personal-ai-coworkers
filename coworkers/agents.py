"""Readable agent workflows: retrieve memory, use tools, reason, then save."""
from datetime import date
import logging
from uuid import uuid4

from coworkers.llm import AIError, AIOutputError
from coworkers.ranking import deterministic_indices, log_ranking_failure, ranking_failure, ranking_schema
from coworkers.search import PRIMARY_DOMAINS, EXPANDED_DOMAINS, SearchError, canonical, recent, rejection_reasons

RUBRIC = {
    "correctness": "Technical accuracy: 0 incorrect, 1 major errors, 2 partial, 3 mostly correct, 4 correct and precise",
    "reasoning": "Explanation: 0 absent, 1 unsupported, 2 some reasoning, 3 clear tradeoffs, 4 rigorous reasoning",
    "application": "Practical solution: 0 absent, 1 infeasible, 2 incomplete, 3 workable, 4 robust with edge cases",
}

logger = logging.getLogger(__name__)


class DailyTechUpgradeAgent:
    def __init__(self, memory, search, ai):
        self.memory, self.search, self.ai = memory, search, ai

    def run(self):
        profile = self.memory.get("profile", {})
        gaps = self.memory.weak_skills()
        reflections = self.memory.events("learning")[:3]
        seen = {canonical(a["url"]) for brief in self.memory.events("brief") for a in brief["articles"]}
        topics = [t.strip() for t in profile.get("interests", "agentic AI, Python").split(",") if t.strip()]
        focus = (gaps + topics) or ["agentic AI"]
        # Bounded expansion: stop at three new articles; never relax the date rule.
        plans = [
            (" ".join(focus[:2]) + " technical research blog article", PRIMARY_DOMAINS),
            (focus[0] + " engineering research announcement technical blog", EXPANDED_DOMAINS),
            (focus[-1] + " implementation tutorial research insights article", EXPANDED_DOMAINS),
        ]
        results, candidates, discovered, attempts = [], [], set(), []
        for query, domains in plans:
            try:
                batch = self.search.search((query + " -site:books.google.com -inurl:events -inurl:docs")[:400],
                                           articles=True, domains=domains)
            except SearchError as exc:
                if not attempts:
                    raise
                attempts.append({"query": query, "domains": domains, "error": str(exc)})
                continue
            attempts.append({"query": query, "domains": domains, "results": len(batch)})
            for result in batch:
                item = dict(result)
                identity = canonical(item["url"])
                if identity in seen:
                    item["exclusion_reason"] = "Previously recommended article"
                elif identity in discovered:
                    item["exclusion_reason"] = "Duplicate result across discovery searches or redirects"
                discovered.add(identity)
                results.append(item)
                if not rejection_reasons(item):
                    candidates.append(item)
            if len(candidates) >= 3:
                break
        if not candidates:
            return {"articles": [], "brief": "No new articles passed page, authoritative-source and 30-day date verification. Try broader interests later.",
                    "checks": self.checks(results), "search_attempts": attempts}
        # Supply explicit application indices; provider result IDs are irrelevant.
        ranking_candidates = [{"candidate_index": i, "title": item["title"], "published": item["published"],
                               "content": item.get("content", "")} for i, item in enumerate(candidates)]
        ranking, failure = None, None
        try:
            ranking = self.ai.ask(
                f'Return exactly {{"indices": [...]}} with {min(3, len(candidates))} distinct integer candidate_index '
                f'values from 0 through {len(candidates) - 1}, ordered most relevant first. '
                'Use only candidate_index, never provider IDs, URLs, titles or one-based numbering. '
                'When fewer than three candidates exist, include every candidate once. '
                'Rank by interests, career goals and weak skills. No additional fields.',
                {"profile": profile, "weak_skills": gaps, "learning_feedback": reflections,
                 "candidates": ranking_candidates}, True, response_schema=ranking_schema(len(candidates)))
            failure = ranking_failure(ranking, len(candidates))
        except AIOutputError as exc:
            failure = exc.category
        if failure:
            log_ranking_failure(failure, len(candidates), ranking)
            indices = deterministic_indices(candidates, profile, gaps)
        else:
            indices = ranking["indices"]
        selection = {"method": "deterministic_topic_overlap" if failure else "llm_structured_ranking",
                     "fallback_reason": failure, "candidate_count": len(candidates), "indices": indices}
        selected = [candidates[i] for i in indices]
        brief = self.ai.ask("Create a 15-20 minute learning brief in Markdown. For each supplied article include its exact "
                            "title, original publication date, source, URL, summary, technical insights, key takeaways and "
                            "explicit relevance to the profile and career. Include a timed plan "
                            "totaling 18 minutes (read 8, practice 7, reflect 3). Cite only supplied URLs. "
                            "Explain when fewer than three eligible articles were available.",
                            {"profile": profile, "gaps": gaps, "learning_feedback": reflections, "articles": selected})
        output = {"day": date.today().isoformat(), "articles": selected, "brief": brief,
                  "checks": self.checks(results), "search_attempts": attempts, "selection": selection}
        output["event_id"] = self.memory.add("brief", output)
        return output

    @staticmethod
    def checks(results):
        return [{"title": r.get("title", "Untitled"), "url": r["url"], "verified": r["verified"],
                 "published": r.get("published"), "verification": r["verification"],
                 "eligible_recent": recent(r), "rejection_reasons": rejection_reasons(r),
                 "publication_evidence": r.get("publication_evidence", []), "updated": r.get("updated"),
                 "modified_evidence": r.get("modified_evidence", []), "provider_date": r.get("provider_date"),
                 "discovery_query": r.get("discovery_query")} for r in results]


class nextLevelPersonalCoachForCareerLevel:
    def __init__(self, memory, search, ai):
        self.memory, self.search, self.ai = memory, search, ai

    def opportunities(self, kind, location):
        profile = self.memory.get("profile", {})
        results = self.search.search(f'{profile.get("career", "Software Engineer")} {kind} {location} apply requirements')
        verified = [r for r in results if r["verified"]]
        if not verified:
            return {"items": results, "analysis": "No opportunity pages could be fetched. No job analysis was generated."}
        analysis = self.ai.ask("Analyze the supplied opportunity pages for technical requirements. Identify whether each is "
                               "a specific listing or a general listing page; do not claim it is open unless the text proves it. "
                               "Extract required versus preferred skills, career fit and an actionable preparation plan. "
                               "Use only supplied source URLs. Suggest learning resources from supplied sources only.",
                               {"profile": profile, "pages": verified, "weak_skills": self.memory.weak_skills()})
        output = {"items": results, "analysis": analysis}
        self.memory.add("opportunities", output)
        return output

    def start(self, skill, style, job_text=""):
        if not skill.strip():
            raise ValueError("Enter a skill to practice.")
        session = {"session_id": str(uuid4()), "skill": skill.strip(), "style": style,
                   "career": self.memory.get("profile", {}).get("career", "Software Engineer"),
                   "job_text": job_text[:16000], "turns": [], "active": True}
        session["question"] = self.question(session)
        self.memory.put("interview", session)
        self.memory.add("session", {"session_id": session["session_id"], "skill": skill, "career": session["career"]})
        return session

    def question(self, session):
        return self.ai.ask("Ask ONE personalized technical interview question or coding exercise according to style. "
                           "For coding include input/output examples, constraints and edge cases, but no solution. "
                           "For later turns ask a follow-up addressing earlier feedback. Do not evaluate an unanswered question.",
                           {"session": session, "profile": self.memory.get("profile", {}),
                            "weak_skills": self.memory.weak_skills()})

    def answer(self, answer):
        session = self.memory.get("interview")
        if not session or not session["active"]:
            raise ValueError("Start an interview first.")
        if not session.get("question"):
            raise ValueError("Request a follow-up before submitting another answer.")
        if not answer.strip():
            raise ValueError("Enter an answer before requesting evaluation.")
        evaluation = self.ai.ask("Evaluate the actual answer with the rubric. Return JSON with scores (a mapping for each "
                                 "rubric criterion to an integer 0-4), feedback (string with evidence from the answer), "
                                 "strengths (list of strings), gaps (list of strings), plan (string with specific practice). "
                                 "Do not claim code was executed. Grade explanations and code by inspection only.",
                                 {"question": session["question"], "answer": answer, "rubric": RUBRIC}, True)
        validate_evaluation(evaluation)
        score = round(sum(evaluation["scores"].values()) / len(RUBRIC), 2)
        turn = {"session_id": session["session_id"], "career": session["career"], "skill": session["skill"],
                "question": session["question"], "answer": answer, "evaluation": evaluation,
                "score": score, "rubric": RUBRIC}
        self.memory.add("answer", turn)
        session["turns"].append(turn)
        session["question"] = None
        self.memory.put("interview", session)
        return turn

    def follow_up(self):
        session = self.memory.get("interview")
        session["question"] = self.question(session)
        self.memory.put("interview", session)
        return session

    def report(self):
        answers = self.memory.events("answer")
        if not answers:
            raise ValueError("Complete an interview answer before generating a report.")
        resources = [a for b in self.memory.events("brief")[:5] for a in b["articles"]]
        report = self.ai.ask("Generate a Career Readiness Report in Markdown grounded in the recorded answers. "
                             "Include sample counts, score trend, strengths, gaps, limitations and a seven-day preparation plan. "
                             "Scores are AI practice assessments, not probabilities of employment. Recommend resources only "
                             "from the supplied articles; if empty say run Daily Learning to discover verified resources. "
                             "Separate careers and skills when comparing scores.",
                             {"profile": self.memory.get("profile", {}), "answers": answers[:50], "resources": resources})
        self.memory.add("report", {"text": report})
        return report


def validate_evaluation(value):
    if not isinstance(value, dict) or not isinstance(value.get("scores"), dict) or set(value["scores"]) != set(RUBRIC):
        logger.warning("Interview evaluation failed stage=validation category=rubric_structure")
        raise AIError("Evaluation did not match the rubric. Nothing was saved.")
    if any(type(s) is not int or not 0 <= s <= 4 for s in value["scores"].values()):
        logger.warning("Interview evaluation failed stage=validation category=score_type_or_range")
        raise AIError("Evaluation scores must be integers from 0 to 4. Nothing was saved.")
    if any(not isinstance(value.get(k), str) or not value[k].strip() for k in ("feedback", "plan")):
        logger.warning("Interview evaluation failed stage=validation category=feedback_or_plan")
        raise AIError("Evaluation feedback was incomplete. Nothing was saved.")
    if any(not isinstance(value.get(k), list) or any(not isinstance(s, str) for s in value[k]) for k in ("strengths", "gaps")):
        logger.warning("Interview evaluation failed stage=validation category=strengths_or_gaps")
        raise AIError("Evaluation strengths or gaps were invalid. Nothing was saved.")
