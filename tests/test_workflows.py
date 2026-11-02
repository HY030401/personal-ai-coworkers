"""Synthetic fixtures, never presented as real search or evaluation results."""
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from coworkers.agents import DailyTechUpgradeAgent, RUBRIC, nextLevelPersonalCoachForCareerLevel, validate_evaluation
from coworkers.llm import AIError
from coworkers.memory import Memory
from coworkers.search import authoritative, canonical, public_url, recent, verify


class FakeAI:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    def ask(self, task, data, json_output=False, response_schema=None):
        self.calls.append(data)
        value = next(self.outputs)
        if isinstance(value, Exception):
            raise value
        return value


class FakeSearch:
    def __init__(self, results):
        self.results = results
        self.queries = []

    def search(self, query, articles=False, domains=None):
        self.queries.append(query)
        return self.results


def article(index=1):
    return dict(title=f"Synthetic article {index}", url=f"https://openai.com/test/{index}", source="openai.com",
                published=date.today().isoformat(), verified=True, content="Synthetic RAG test text",
                verification="Synthetic fixture; no live request")


def evaluation(score=2):
    return dict(scores={k: score for k in RUBRIC}, feedback="Synthetic feedback based on test answer",
                strengths=["Python"], gaps=["RAG"], plan="Practice retrieval evaluation")


class Workflows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "memory.db"
        self.memory = Memory(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_profile_persists_after_reopening(self):
        self.memory.put("profile", {"career": "Engineer", "interests": "Python"})
        self.assertEqual(Memory(self.path).get("profile")["career"], "Engineer")

    def test_scheduler_claim_is_persistent_and_unique(self):
        self.assertTrue(self.memory.claim_day("2026-11-01"))
        self.assertFalse(Memory(self.path).claim_day("2026-11-01"))
        self.assertTrue(self.memory.claim_day("2026-11-02"))

    def test_recent_requires_real_page_date(self):
        item = article()
        self.assertTrue(recent(item))
        for changes in [{"published": None}, {"verified": False},
                        {"published": (date.today() - timedelta(days=31)).isoformat()},
                        {"published": (date.today() + timedelta(days=1)).isoformat()}]:
            self.assertFalse(recent(dict(item, **changes)))

    def test_domain_boundary_and_canonical(self):
        self.assertTrue(authoritative("https://research.google/blog/example"))
        self.assertFalse(authoritative("https://openai.com.evil.example/article"))
        self.assertEqual(canonical("https://openai.com/a/?utm_source=test#part"), "https://openai.com/a")

    def test_blocks_private_destinations(self):
        with patch("coworkers.search.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaises(ValueError):
                public_url("https://localhost/test")

    def test_verification_uses_page_metadata_not_search_title(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup('<title>Actual page title</title><meta property="article:published_time" content="2026-01-02T00:00:00Z"><p>Body</p>', "html.parser")
        with patch("coworkers.search.fetch_page", return_value=("https://openai.com/test", soup)):
            result = verify({"url": "https://openai.com/test", "title": "Untrusted search title"})
        self.assertEqual(result["title"], "Actual page title")
        self.assertEqual(result["published"], "2026-01-02")

    def test_brief_saves_and_does_not_repeat(self):
        search = FakeSearch([article(1), article(2), article(3)])
        ai = FakeAI([{"indices": [2, 0, 1]}, "Synthetic test brief"])
        agent = DailyTechUpgradeAgent(self.memory, search, ai)
        self.assertEqual(len(agent.run()["articles"]), 3)
        self.assertEqual(agent.run()["articles"], [])
        self.assertEqual(len(Memory(self.path).events("brief")), 1)

    def test_failed_reasoning_does_not_save_recommendations(self):
        agent = DailyTechUpgradeAgent(self.memory, FakeSearch([article()]), FakeAI([{"indices": [0]}, AIError("offline failure")]))
        with self.assertRaises(AIError):
            agent.run()
        self.assertEqual(self.memory.events("brief"), [])

    def test_invalid_ranking_uses_verified_fallback(self):
        agent = DailyTechUpgradeAgent(self.memory, FakeSearch([article()]), FakeAI([{"indices": [99]}, "Synthetic brief"]))
        result = agent.run()
        self.assertEqual(result["articles"], [article()])
        self.assertEqual(result["selection"]["fallback_reason"], "unknown_id")
        self.assertEqual(len(self.memory.events("brief")), 1)

    def test_multiturn_interview_and_shared_memory(self):
        ai = FakeAI(["Explain RAG", evaluation(2), "Follow-up on retrieval", evaluation(4)])
        coach = nextLevelPersonalCoachForCareerLevel(self.memory, FakeSearch([]), ai)
        coach.start("RAG", "Technical discussion")
        coach.answer("Retrieve relevant documents before generation")
        self.assertEqual(self.memory.weak_skills(), ["RAG"])
        with self.assertRaises(ValueError):
            coach.answer("Duplicate submission")
        self.assertEqual(coach.follow_up()["question"], "Follow-up on retrieval")
        coach.answer("Improved answer")
        self.assertEqual(self.memory.weak_skills(), [])
        self.assertEqual(len(Memory(self.path).events("answer")), 2)

    def test_weak_skill_and_feedback_reach_learning_agent(self):
        self.memory.add("answer", {"skill": "RAG", "score": 1})
        self.memory.add("learning", {"notes": "More implementation practice"})
        search = FakeSearch([article()])
        ai = FakeAI([{"indices": [0]}, "Synthetic brief"])
        DailyTechUpgradeAgent(self.memory, search, ai).run()
        self.assertIn("RAG", search.queries[0])
        self.assertEqual(ai.calls[0]["learning_feedback"][0]["notes"], "More implementation practice")

    def test_invalid_evaluations_rejected(self):
        for bad in [dict(evaluation(), scores={"correctness": 5}),
                    dict(evaluation(), scores={k: True for k in RUBRIC}),
                    dict(evaluation(), feedback=""), dict(evaluation(), gaps="RAG")]:
            with self.assertRaises(AIError):
                validate_evaluation(bad)

    def test_failed_evaluation_keeps_question_and_no_score(self):
        coach = nextLevelPersonalCoachForCareerLevel(self.memory, FakeSearch([]), FakeAI(["Question", AIError("failure")]))
        coach.start("Python", "Coding exercise")
        with self.assertRaises(AIError):
            coach.answer("Code")
        self.assertEqual(self.memory.get("interview")["question"], "Question")
        self.assertEqual(self.memory.events("answer"), [])

    def test_no_report_without_answers(self):
        coach = nextLevelPersonalCoachForCareerLevel(self.memory, FakeSearch([]), FakeAI([]))
        with self.assertRaises(ValueError):
            coach.report()


if __name__ == "__main__":
    unittest.main()
