"""Error diagnostics must remain helpful without exposing credentials."""
import json
import os
import unittest
from unittest.mock import patch

import requests

from coworkers.search import SearchError, SearchTool


@patch.dict(os.environ, {"SEARCH_PROVIDER": "tavily", "TAVILY_API_KEY": "synthetic-private-key"})
class SearchErrorTests(unittest.TestCase):
    def response(self, status, payload):
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(payload).encode()
        response.url = "https://api.tavily.com/search?api_key=never-log-this-url"
        return response

    @patch("coworkers.search.requests.post")
    def test_http_categories_and_sanitized_provider_validation_message(self, post):
        for status, category in [(400, "request validation"), (422, "request validation"),
                                 (401, "authentication/access"), (403, "authentication/access"),
                                 (429, "rate limit"), (432, "quota/billing"), (433, "quota/billing"),
                                 (500, "provider unavailable")]:
            with self.subTest(status=status):
                post.return_value = self.response(status, {"detail": [{"msg": "Invalid include_domains_mode; synthetic-private-key "
                                  "Bearer arbitrary-token api_key=other-secret https://example.com/?secret=hidden",
                                  "input": "never-log-input", "headers": {"Authorization": "never-log-header"}}]})
                with self.assertLogs("coworkers.search", level="WARNING") as logs:
                    with self.assertRaises(SearchError) as caught:
                        SearchTool().search("private user query", articles=True)
                self.assertIn(category, str(caught.exception))
                self.assertIn(f"HTTP {status}", str(caught.exception))
                output = " ".join(logs.output)
                self.assertIn(f"status={status}", output)
                self.assertIn("error_type=HTTPError", output)
                self.assertIn("Invalid include_domains_mode", output)
                for secret in ["synthetic-private-key", "arbitrary-token", "other-secret", "never-log-input",
                               "never-log-header", "never-log-this-url", "private user query", "secret=hidden"]:
                    self.assertNotIn(secret, output + str(caught.exception))

    @patch("coworkers.search.requests.post")
    def test_transport_errors_do_not_log_exception_text(self, post):
        for error, category in [(requests.Timeout("synthetic-private-key"), "timeout"),
                                (requests.ConnectionError("secret request headers"), "connection")]:
            post.side_effect = error
            with self.assertLogs("coworkers.search", level="WARNING") as logs:
                with self.assertRaises(SearchError) as caught:
                    SearchTool().search("test")
            self.assertIn(category, str(caught.exception))
            self.assertIn("status=none", " ".join(logs.output))
            self.assertNotIn(str(error), " ".join(logs.output))

    @patch("coworkers.search.requests.post")
    def test_non_json_body_is_omitted(self, post):
        response = self.response(400, {})
        response._content = b"<html>synthetic-private-key secret page</html>"
        post.return_value = response
        with self.assertLogs("coworkers.search", level="WARNING") as logs:
            with self.assertRaises(SearchError):
                SearchTool().search("test")
        self.assertIn("body omitted", " ".join(logs.output))
        self.assertNotIn("secret page", " ".join(logs.output))

    @patch("coworkers.search.requests.post")
    def test_malformed_success_response_has_distinct_category(self, post):
        post.return_value = self.response(200, {"unexpected": "secret-unrelated-content"})
        with self.assertLogs("coworkers.search", level="WARNING") as logs:
            with self.assertRaises(SearchError) as caught:
                SearchTool().search("test")
        self.assertIn("invalid response", str(caught.exception))
        self.assertIn("error_type=KeyError", " ".join(logs.output))
        self.assertNotIn("secret-unrelated-content", " ".join(logs.output))
