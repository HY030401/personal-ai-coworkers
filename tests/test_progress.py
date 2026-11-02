"""Persistent weakness evidence, using only synthetic temporary databases."""
import tempfile
import unittest
from pathlib import Path

from coworkers.agents import nextLevelPersonalCoachForCareerLevel
from coworkers.memory import Memory
from tests.test_workflows import FakeAI, FakeSearch, evaluation


class ProgressTests(unittest.TestCase):
    def test_high_score_qualitative_gaps_persist_from_actual_answer_workflow(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "m.db"
            memory = Memory(path)
            feedback = dict(evaluation(3), gaps=["Reasoning depth", "Code robustness", "Experimental design"])
            coach = nextLevelPersonalCoachForCareerLevel(memory, FakeSearch([]), FakeAI(["Synthetic question", feedback]))
            coach.start("RAG", "Technical discussion")
            coach.answer("Synthetic answer")
            records = memory.events("answer")
            restarted = Memory(path)
            self.assertEqual(restarted.weak_skills(), [])
            self.assertEqual(restarted.interview_weaknesses()[0]["gaps"], feedback["gaps"])
            self.assertEqual(restarted.interview_weaknesses()[0]["score"], 3)
            self.assertEqual(restarted.events("answer"), records)

    def test_low_rubric_score_visible_even_when_average_is_three(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = Memory(Path(folder) / "m.db")
            memory.add("answer", {"career": "Engineer", "skill": "Python", "score": 3,
                                  "evaluation": {"gaps": [], "scores": {"correctness": 4, "reasoning": 2, "application": 3}}})
            priority = memory.interview_weaknesses()[0]
            self.assertEqual(priority["rubric_gaps"], {"reasoning": 2})
            self.assertFalse(priority["low_overall"])

    def test_latest_assessment_clears_old_gaps_without_changing_history(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = Memory(Path(folder) / "m.db")
            memory.add("answer", {"career": "Engineer", "skill": "RAG", "score": 2,
                                  "evaluation": {"gaps": ["Old gap"]}})
            memory.add("answer", {"career": "Engineer", "skill": "RAG", "score": 4,
                                  "evaluation": dict(evaluation(4), gaps=[])})
            records = memory.events("answer")
            self.assertEqual(memory.interview_weaknesses(), [])
            self.assertEqual(memory.events("answer"), records)

    def test_historical_payloads_without_evaluation_and_separate_careers(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = Memory(Path(folder) / "m.db")
            memory.add("answer", {"career": "Researcher", "skill": "RAG", "score": 2})
            memory.add("answer", {"career": "Engineer", "skill": "RAG", "score": 4,
                                  "evaluation": {"gaps": ["Experimental design", "Experimental design", " "]}})
            priorities = memory.interview_weaknesses()
            self.assertEqual(len(priorities), 2)
            self.assertEqual(priorities[0]["gaps"], ["Experimental design"])
            self.assertEqual(priorities[1]["gaps"], [])
            self.assertTrue(priorities[1]["low_overall"])

    def test_missing_or_malformed_gap_fields_do_not_fabricate_weaknesses(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = Memory(Path(folder) / "m.db")
            for index, value in enumerate([{}, {"gaps": "not a list"}, {"gaps": [None, " ", 4]}, {"scores": None}]):
                memory.add("answer", {"skill": f"Skill {index}", "score": 4, "evaluation": value})
            self.assertEqual(memory.interview_weaknesses(), [])
