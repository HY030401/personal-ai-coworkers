"""Offline ranking contracts and recovery; OpenAI is always mocked."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from coworkers.agents import DailyTechUpgradeAgent
from coworkers.llm import AIError, AIOutputError, Reasoner
from coworkers.memory import Memory
from coworkers.ranking import deterministic_indices, ranking_failure, ranking_schema
from tests.test_workflows import FakeAI, FakeSearch, article


class RankingTests(unittest.TestCase):
    def run_agent(self, candidates, outputs):
        with tempfile.TemporaryDirectory() as folder:
            memory = Memory(Path(folder) / "m.db")
            result = DailyTechUpgradeAgent(memory, FakeSearch(candidates), FakeAI(outputs)).run()
            return result, memory.events("brief")

    def test_valid_indices_preserve_model_order(self):
        result, saved = self.run_agent([article(1), article(2), article(3)],
                                       [{"indices": [2, 0, 1]}, "Synthetic brief"])
        self.assertEqual([a["title"] for a in result["articles"]],
                         ["Synthetic article 3", "Synthetic article 1", "Synthetic article 2"])
        self.assertEqual(result["selection"]["method"], "llm_structured_ranking")
        self.assertEqual(len(saved), 1)

    def test_invalid_categories(self):
        for ranking, category in [([], "not_an_object"), ({"ids": [0]}, "missing_indices"),
                                  ({"indices": "0"}, "indices_not_a_list"),
                                  ({"indices": []}, "empty_indices"),
                                  ({"indices": [True]}, "non_integer_id"),
                                  ({"indices": ["private-provider-id"]}, "non_integer_id"),
                                  ({"indices": [99]}, "unknown_id"),
                                  ({"indices": [-1]}, "unknown_id"),
                                  ({"indices": [0, 0]}, "duplicate_ids"),
                                  ({"indices": [0]}, "wrong_selection_count"),
                                  ({"indices": [0, 1], "extra": "text"}, "unexpected_fields")]:
            with self.subTest(category=category):
                self.assertEqual(ranking_failure(ranking, 2), category)

    def test_duplicate_unknown_and_wrong_shape_fall_back_safely(self):
        for ranking in [{"indices": [0, 0]}, {"indices": [99]}, {"ids": ["private-id"]}]:
            with self.subTest(ranking=ranking):
                with self.assertLogs("coworkers.ranking", level="WARNING") as logs:
                    result, saved = self.run_agent([article(1), article(2)], [ranking, "Synthetic brief"])
                self.assertEqual(len(result["articles"]), 2)
                self.assertEqual(result["selection"]["method"], "deterministic_topic_overlap")
                output = " ".join(logs.output)
                self.assertIn("candidate_count=2", output)
                self.assertIn("expected={indices: 2 distinct integer IDs in 0..1}", output)
                self.assertNotIn("private-id", output)
                self.assertNotIn("openai.com", output)
                self.assertEqual(len(saved), 1)

    def test_one_and_two_candidates_have_matching_schema_and_count(self):
        for count in (1, 2):
            with self.subTest(count=count):
                indices = list(range(count))
                schema = ranking_schema(count)["properties"]["indices"]
                self.assertEqual(schema["items"]["enum"], indices)
                self.assertEqual(schema["minItems"], count)
                self.assertEqual(schema["maxItems"], count)
                result, _ = self.run_agent([article(i) for i in indices],
                                           [{"indices": indices}, "Synthetic brief"])
                self.assertEqual(len(result["articles"]), count)
                self.assertIsNone(result["selection"]["fallback_reason"])

    def test_malformed_json_output_falls_back(self):
        with self.assertLogs("coworkers.ranking", level="WARNING"):
            result, _ = self.run_agent([article()], [AIOutputError("malformed_json"), "Synthetic brief"])
        self.assertEqual(result["selection"]["fallback_reason"], "malformed_json")
        self.assertEqual(result["articles"], [article()])

    def test_fallback_is_deterministic_and_uses_only_supplied_candidates(self):
        candidates = [dict(article(1), title="Python", content="Python"),
                      dict(article(2), title="RAG evaluation", content="RAG")]
        result = deterministic_indices(candidates, {"interests": "Python"}, ["RAG"])
        self.assertEqual(result, [1, 0])
        self.assertEqual(result, deterministic_indices(candidates, {"interests": "Python"}, ["RAG"]))

    def test_failed_summary_after_fallback_does_not_write_history(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = Memory(Path(folder) / "m.db")
            agent = DailyTechUpgradeAgent(memory, FakeSearch([article()]),
                                          FakeAI([{"indices": [99]}, AIError("Synthetic summary failure")]))
            with self.assertLogs("coworkers.ranking", level="WARNING"):
                with self.assertRaises(AIError):
                    agent.run()
            self.assertEqual(memory.events("brief"), [])

    def test_transport_failure_does_not_trigger_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = Memory(Path(folder) / "m.db")
            agent = DailyTechUpgradeAgent(memory, FakeSearch([article()]), FakeAI([AIError("Synthetic API failure")]))
            with self.assertRaises(AIError):
                agent.run()
            self.assertEqual(memory.events("brief"), [])

    @patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-private-key"})
    @patch("openai.OpenAI")
    def test_reasoner_sends_strict_schema_and_parses_json(self, client):
        create = client.return_value.responses.create
        create.return_value = SimpleNamespace(output_text='{"indices":[0]}')
        schema = ranking_schema(1)
        self.assertEqual(Reasoner().ask("Rank", {}, True, response_schema=schema), {"indices": [0]})
        format = create.call_args.kwargs["text"]["format"]
        self.assertEqual(format, {"type": "json_schema", "name": "article_ranking", "strict": True, "schema": schema})
        # Existing career-coach JSON calls retain their previous JSON-object mode.
        Reasoner().ask("Evaluate", {}, True)
        self.assertEqual(create.call_args.kwargs["text"]["format"], {"type": "json_object"})

    @patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-private-key"})
    @patch("openai.OpenAI")
    def test_reasoner_malformed_json_has_safe_typed_error(self, client):
        client.return_value.responses.create.return_value = SimpleNamespace(output_text="private raw malformed response")
        with self.assertRaises(AIOutputError) as caught:
            Reasoner().ask("Rank", {}, True, response_schema=ranking_schema(1))
        self.assertEqual(caught.exception.category, "malformed_json")
        self.assertNotIn("private raw", str(caught.exception))

    def test_provider_ids_are_not_in_ranking_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            ai = FakeAI([{"indices": [0]}, "Synthetic brief"])
            agent = DailyTechUpgradeAgent(Memory(Path(folder) / "m.db"),
                                          FakeSearch([dict(article(), id="provider-private-id")]), ai)
            agent.run()
            payload = ai.calls[0]["candidates"][0]
            self.assertEqual(payload["candidate_index"], 0)
            self.assertNotIn("id", payload)
            self.assertNotIn("provider-private-id", json.dumps(payload))
