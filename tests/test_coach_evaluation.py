"""Mocked Responses API tests. No real credentials, network or user databases."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import BadRequestError

from coworkers.agents import nextLevelPersonalCoachForCareerLevel
from coworkers.llm import AIError, AIOutputError, Reasoner
from coworkers.memory import Memory
from tests.test_workflows import evaluation


@patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-private-key"})
class CoachEvaluationTests(unittest.TestCase):
    @patch("openai.OpenAI")
    def test_json_mode_input_has_explicit_json_instruction(self, client):
        # Model a JSON-mode input prerequisite, not provider success or live evidence.
        def create(**kwargs):
            if kwargs.get("text", {}).get("format", {}).get("type") == "json_object":
                self.assertIn("json", kwargs["input"].lower(),
                              "JSON mode needs an explicit JSON instruction in input, not only instructions")
                return SimpleNamespace(output_text=json.dumps(evaluation(3)))
            return SimpleNamespace(output_text="Explain retrieval evaluation.")

        client.return_value.responses.create.side_effect = create
        for answer in ["Retrieve relevant documents and measure recall.", "Use retrieval; compare precision and recall — then iterate."]:
            with self.subTest(answer_kind="ASCII" if answer.isascii() else "Unicode"):
                with tempfile.TemporaryDirectory() as folder:
                    memory = Memory(Path(folder) / "m.db")
                    coach = nextLevelPersonalCoachForCareerLevel(memory, None, Reasoner())
                    coach.start("RAG", "Technical discussion")
                    turn = coach.answer(answer)
                    self.assertEqual(turn["score"], 3)
                    self.assertEqual(memory.events("answer")[0]["answer"], answer)
                    self.assertIsNone(memory.get("interview")["question"])
                    # Evaluation is one call. A follow-up is generated only on request.
                    calls_before = client.return_value.responses.create.call_count
                    coach.follow_up()
                    self.assertEqual(client.return_value.responses.create.call_count, calls_before + 1)
                    self.assertEqual(len(memory.events("answer")), 1)
                    self.assertEqual(len(memory.get("interview")["turns"]), 1)

    def session(self, memory):
        memory.put("interview", {"active": True, "session_id": "synthetic-session", "career": "Engineer",
                                 "skill": "RAG", "question": "Explain retrieval.", "turns": []})
        memory.add("learning", {"notes": "Synthetic existing learning history"})
        return memory.get("interview"), memory.events("learning")

    @patch("openai.OpenAI")
    def test_api_failure_is_distinct_and_preserves_session_and_history(self, client):
        response = httpx.Response(400, request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
        client.return_value.responses.create.side_effect = BadRequestError(
            "private-body synthetic-private-key", response=response, body={"message": "private-body"})
        with tempfile.TemporaryDirectory() as folder:
            memory = Memory(Path(folder) / "m.db")
            before, history = self.session(memory)
            coach = nextLevelPersonalCoachForCareerLevel(memory, None, Reasoner())
            with self.assertLogs("coworkers.llm", level="WARNING") as logs:
                with self.assertRaises(AIError) as caught:
                    coach.answer("private submitted answer")
            self.assertIn("request_validation", str(caught.exception))
            self.assertIn("HTTP 400", str(caught.exception))
            output = " ".join(logs.output)
            self.assertIn("stage=api_request", output)
            self.assertIn("error_type=BadRequestError", output)
            for private in ["private-body", "synthetic-private-key", "private submitted answer", "Explain retrieval"]:
                self.assertNotIn(private, output + str(caught.exception))
            self.assertEqual(memory.get("interview"), before)
            self.assertEqual(memory.events("learning"), history)
            self.assertEqual(memory.events("answer"), [])

    @patch("openai.OpenAI")
    def test_malformed_evaluation_is_parsing_failure_with_no_writes(self, client):
        client.return_value.responses.create.return_value = SimpleNamespace(output_text="private malformed model body")
        with tempfile.TemporaryDirectory() as folder:
            memory = Memory(Path(folder) / "m.db")
            before, history = self.session(memory)
            with self.assertLogs("coworkers.llm", level="WARNING") as logs:
                with self.assertRaises(AIOutputError):
                    nextLevelPersonalCoachForCareerLevel(memory, None, Reasoner()).answer("test answer")
            self.assertIn("stage=response_parsing category=malformed_json", " ".join(logs.output))
            self.assertNotIn("private malformed", " ".join(logs.output))
            self.assertEqual(memory.get("interview"), before)
            self.assertEqual(memory.events("learning"), history)
            self.assertEqual(memory.events("answer"), [])

    @patch("openai.OpenAI")
    def test_invalid_scores_and_required_fields_are_validation_failures(self, client):
        invalid = [dict(evaluation(), scores={"correctness": 3}),
                   dict(evaluation(), scores={"correctness": 2.5, "reasoning": 2, "application": 2}),
                   dict(evaluation(), scores={"correctness": True, "reasoning": 2, "application": 2}),
                   dict(evaluation(), scores={"correctness": 5, "reasoning": 2, "application": 2}),
                   dict(evaluation(), feedback=""), dict(evaluation(), plan=None),
                   dict(evaluation(), strengths="RAG"), dict(evaluation(), gaps=[4])]
        for value in invalid:
            with self.subTest(case=invalid.index(value)):
                client.return_value.responses.create.return_value = SimpleNamespace(output_text=json.dumps(value))
                with tempfile.TemporaryDirectory() as folder:
                    memory = Memory(Path(folder) / "m.db")
                    before, history = self.session(memory)
                    with self.assertLogs("coworkers.agents", level="WARNING") as logs:
                        with self.assertRaises(AIError):
                            nextLevelPersonalCoachForCareerLevel(memory, None, Reasoner()).answer("test answer")
                    self.assertIn("stage=validation", " ".join(logs.output))
                    self.assertEqual(memory.get("interview"), before)
                    self.assertEqual(memory.events("learning"), history)
                    self.assertEqual(memory.events("answer"), [])
