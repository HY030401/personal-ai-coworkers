"""Run the actual Streamlit script with isolated memory and no API calls."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class UITests(unittest.TestCase):
    def test_dashboard_shows_saved_gaps_above_score_threshold_after_restart(self):
        from coworkers.memory import Memory
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "test.db")
            memory = Memory(path)
            for skill, gaps in [("RAG", ["Reasoning depth", "Experimental design"]),
                                ("Python", ["Code robustness"])]:
                memory.add("answer", {"career": "Engineer", "session_id": "synthetic-session", "skill": skill,
                                      "score": 3, "evaluation": {"gaps": gaps}})
            records = memory.events("answer")
            with patch.dict(os.environ, {"DATABASE_PATH": path, "OPENAI_API_KEY": "", "TAVILY_API_KEY": ""}):
                for _ in range(2):
                    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15).run()
                    self.assertEqual(len(app.exception), 0)
                    text = " ".join(element.value for element in app.tabs[3].markdown)
                    for phrase in ["RAG", "Python", "Reasoning depth", "Experimental design", "Code robustness"]:
                        self.assertIn(phrase, text)
                    self.assertNotIn("No recorded gaps yet", text)
            self.assertEqual(Memory(path).events("answer"), records)

    def test_initial_render_profile_and_missing_key_error(self):
        with tempfile.TemporaryDirectory() as folder:
            env = {"DATABASE_PATH": str(Path(folder) / "test.db"), "OPENAI_API_KEY": "",
                   "TAVILY_API_KEY": "", "SEARCH_PROVIDER": "tavily"}
            with patch.dict(os.environ, env):
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15).run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual([t.label for t in app.tabs],
                                 ["Daily Learning", "Career Coach", "Learning History", "Progress Dashboard"])
                app.sidebar.text_input[0].set_value("Test learner")
                app.sidebar.button[0].click().run()
                self.assertEqual(len(app.exception), 0)
                from coworkers.memory import Memory
                self.assertEqual(Memory(env["DATABASE_PATH"]).get("profile")["name"], "Test learner")
                next(b for b in app.button if b.label == "Generate daily brief").click().run()
                self.assertEqual(len(app.exception), 0)
                self.assertTrue(any("TAVILY_API_KEY" in e.value for e in app.error))


if __name__ == "__main__":
    unittest.main()
