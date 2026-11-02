"""Offline HTML and search fixtures; no real search/model claims."""
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

from coworkers.article_dates import excluded_page, parse_date, publication_evidence
from coworkers.agents import DailyTechUpgradeAgent
from coworkers.memory import Memory
from coworkers.search import SearchError, SearchTool, recent, rejection_reasons, verify
from tests.test_workflows import FakeAI, article

TODAY = date(2026, 11, 1)


def dates(html, provider=None):
    return publication_evidence(BeautifulSoup(html, "html.parser"), provider, TODAY)


class DateTests(unittest.TestCase):
    def test_supported_date_formats(self):
        for value in ["2026-10-20", "2026-10-20T12:30:00Z", "October 20, 2026", "Oct 20, 2026",
                      "20 October 2026", "Tue, 20 Oct 2026 12:00:00 GMT"]:
            with self.subTest(value=value):
                self.assertEqual(parse_date(value, TODAY), "2026-10-20")

    def test_invalid_partial_future_and_impossible_dates(self):
        for value in [None, "", "2026-02-30", "11/01/26", "October 20", "2026", "yesterday",
                      "2026-10-20T99:00:00Z", "2026-11-02", "2026-10-20garbage"]:
            with self.subTest(value=value):
                self.assertIsNone(parse_date(value, TODAY))

    def test_nested_jsonld_and_modified_separation(self):
        data = {"@graph": [{"@type": "WebSite", "datePublished": "2026-10-30"},
                           {"nested": {"@type": ["Article", "TechArticle"], "datePublished": "2026-09-01",
                                       "dateModified": "2026-10-31"}}]}
        result = dates('<script type="application/ld+json">' + json.dumps(data) + '</script>')
        self.assertEqual(result["published"], "2026-09-01")
        self.assertEqual(result["updated"], "2026-10-31")
        self.assertFalse(recent(dict(result, verified=True), TODAY))

    def test_all_publication_evidence_types(self):
        fixtures = ['<meta property="article:published_time" content="2026-10-20">',
                    '<meta property="og:published_time" content="2026-10-20">',
                    '<meta name="citation_publication_date" content="2026-10-20">',
                    '<meta itemprop="datePublished" content="2026-10-20">',
                    '<article><time itemprop="datePublished" datetime="2026-10-20">October 20, 2026</time></article>',
                    '<article><p>Published on October 20, 2026</p></article>',
                    '<main><p>Posted: 20 October 2026</p></main>']
        for html in fixtures:
            with self.subTest(html=html):
                self.assertEqual(dates(html)["published"], "2026-10-20")

    def test_unlabeled_time_updated_and_provider_only_rejected(self):
        for html in ['<time datetime="2026-10-20">October 20</time>',
                     '<meta property="article:modified_time" content="2026-10-20">',
                     '<article>Updated on October 20, 2026</article>',
                     '<article><p>Updated <time datetime="2026-10-20">October 20</time></p></article>']:
            self.assertIsNone(dates(html, "2026-10-20")["published"])

    def test_publication_time_not_hidden_by_nearby_update(self):
        result = dates('<article><time itemprop="datePublished" datetime="2026-09-01">September 1, 2026</time>'
                       '<p>Updated: October 31, 2026</p></article>')
        self.assertEqual(result["published"], "2026-09-01")
        self.assertEqual(result["updated"], "2026-10-31")

    def test_provider_candidate_match_conflict_and_invalid(self):
        html = '<meta property="article:published_time" content="2026-10-20">'
        self.assertEqual(dates(html, "2026-10-20")["provider_date"]["status"], "matches page")
        conflict = dates(html, "2026-10-31")
        self.assertEqual(conflict["published"], "2026-10-20")
        self.assertIn("conflicts", conflict["provider_date"]["status"])
        self.assertIsNone(dates("<article>Text</article>", "2026-10-20")["published"])
        self.assertIn("invalid", dates(html, "2026-02-30")["provider_date"]["status"])

    def test_conflicting_page_dates_rejected_and_malformed_json_ignored(self):
        html = '<meta name="pubdate" content="2026-10-20"><meta property="article:published_time" content="2026-10-21">'
        self.assertIsNone(dates(html)["published"])
        self.assertIn("Conflicting", dates(html)["date_verification"])
        self.assertEqual(dates('<script type="application/ld+json">invalid</script><p>Published: October 20, 2026</p>')["published"], "2026-10-20")

    def test_exact_30_day_boundary_and_malformed_recent(self):
        for days, eligible in [(0, True), (30, True), (31, False), (-1, False)]:
            self.assertEqual(recent(dict(verified=True, published=(TODAY - timedelta(days=days)).isoformat()), TODAY), eligible)
        self.assertFalse(recent(dict(verified=True, published="broken"), TODAY))


class DiscoveryTests(unittest.TestCase):
    def test_excluded_destinations(self):
        for url in ["https://books.google.com/books?id=123", "https://aws.amazon.com/events/ai",
                    "https://ieee.org/conferences/2026", "https://openai.com/docs/overview",
                    "https://docs.langchain.com/start", "https://research.google/blog/",
                    "https://ibm.com/insights", "https://microsoft.com/webinars/ai"]:
            with self.subTest(url=url):
                self.assertIsNotNone(excluded_page(url))
        soup = BeautifulSoup('<script type="application/ld+json">{"@type":"Event","startDate":"2026-10-20"}</script>', "html.parser")
        self.assertIsNotNone(excluded_page("https://ieee.org/special-ai-day", soup))
        self.assertIsNone(excluded_page("https://aws.amazon.com/blogs/machine-learning/rag-evaluation/"))

    @patch.dict(os.environ, {"SEARCH_PROVIDER": "tavily", "TAVILY_API_KEY": "synthetic-test-key"})
    @patch("coworkers.search.verify")
    @patch("coworkers.search.requests.post")
    def test_tavily_filters_and_duplicate_diagnostics(self, post, checked):
        response = Mock()
        response.json.return_value = {"results": [article(), article(),
                                                  dict(article(2), url="https://books.google.com/books?id=2")]}
        post.return_value = response
        checked.side_effect = lambda result, **kwargs: result
        results = SearchTool().search("RAG research blog", articles=True)
        body = post.call_args.kwargs["json"]
        self.assertNotIn("time_range", body)
        self.assertTrue(body["include_published_date"])
        self.assertTrue(body["filter_by_published_date"])
        self.assertIn("start_date", body)
        self.assertIn("anthropic.com", body["include_domains"])
        self.assertNotIn("google.com", body["include_domains"])
        self.assertEqual(checked.call_count, 1)
        self.assertIn("Duplicate", results[1]["exclusion_reason"])
        self.assertIn("Books", results[2]["exclusion_reason"])

    @patch.dict(os.environ, {"SEARCH_PROVIDER": "tavily", "TAVILY_API_KEY": "synthetic-test-key"})
    @patch("coworkers.search.requests.post")
    def test_primary_and_all_fallback_requests_use_only_explicit_dates(self, post):
        # Empty results force the actual agent through all three request paths.
        post.return_value.json.return_value = {"results": []}
        with tempfile.TemporaryDirectory() as folder:
            with patch("coworkers.search.date") as clock:
                clock.today.return_value = TODAY
                DailyTechUpgradeAgent(Memory(Path(folder) / "m.db"), SearchTool(), FakeAI([])).run()
        self.assertEqual(post.call_count, 3)
        for call in post.call_args_list:
            body = call.kwargs["json"]
            self.assertNotIn("time_range", body)
            self.assertEqual(body["start_date"], (TODAY - timedelta(days=30)).isoformat())
            self.assertEqual(body["end_date"], TODAY.isoformat())
            self.assertTrue(body["filter_by_published_date"])
            self.assertTrue(body["include_domains"])
        self.assertIn("ibm.com", post.call_args_list[1].kwargs["json"]["include_domains"])

    def test_redirected_excluded_page_and_missing_date_reasons(self):
        soup = BeautifulSoup('<title>Docs</title><meta name="date" content="2026-10-20">', "html.parser")
        with patch("coworkers.search.fetch_page", return_value=("https://openai.com/docs/overview", soup)):
            result = verify(article(), article_mode=True)
        self.assertIn("Documentation", result["exclusion_reason"])
        self.assertFalse(recent(result))
        with patch("coworkers.search.fetch_page", side_effect=ValueError("Page is not HTML")):
            result = verify(article(), article_mode=True)
        self.assertIn("Page is not HTML", result["verification"])

    def test_search_fallback_and_cross_round_duplicates(self):
        search = Mock()
        undated = dict(article(1), published=None)
        search.search.side_effect = [[undated], [article(2)], [article(2), article(3), article(4)]]
        with tempfile.TemporaryDirectory() as folder:
            memory = Memory(Path(folder) / "test.db")
            ai = FakeAI([{"indices": [0, 1, 2]}, "Synthetic brief"])
            output = DailyTechUpgradeAgent(memory, search, ai).run()
        self.assertEqual(len(output["articles"]), 3)
        self.assertEqual(search.search.call_count, 3)
        self.assertNotEqual(search.search.call_args_list[0].args[0], search.search.call_args_list[1].args[0])
        self.assertIn("ibm.com", search.search.call_args_list[1].kwargs["domains"])
        self.assertTrue(any("Duplicate" in " ".join(c["rejection_reasons"]) for c in output["checks"]))

    def test_fallback_exhaustion_and_early_stop(self):
        for batches, expected_calls, outputs in [([[], [], []], 3, []),
                                                ([[article(1), article(2), article(3)]], 1,
                                                 [{"indices": [0, 1, 2]}, "Synthetic brief"])]:
            with tempfile.TemporaryDirectory() as folder:
                search = Mock()
                search.search.side_effect = batches
                result = DailyTechUpgradeAgent(Memory(Path(folder) / "m.db"), search, FakeAI(outputs)).run()
                self.assertEqual(search.search.call_count, expected_calls)
                self.assertEqual(len(result["search_attempts"]), expected_calls)

    def test_fallback_failure_preserves_verified_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            search = Mock()
            search.search.side_effect = [[article()], SearchError("Synthetic provider failure"), []]
            output = DailyTechUpgradeAgent(Memory(Path(folder) / "m.db"), search,
                                           FakeAI([{"indices": [0]}, "Synthetic brief"])).run()
            self.assertEqual(len(output["articles"]), 1)
            self.assertIn("error", output["search_attempts"][1])

    def test_duplicate_rejection_does_not_claim_valid_date_is_stale(self):
        result = dict(article(), exclusion_reason="Duplicate search result")
        self.assertEqual(rejection_reasons(result), ["Duplicate search result"])

    @patch.dict(os.environ, {"SEARCH_PROVIDER": "tavily", "TAVILY_API_KEY": "synthetic-test-key"})
    @patch("coworkers.search.verify")
    @patch("coworkers.search.requests.post")
    def test_career_search_does_not_receive_article_filters(self, post, checked):
        post.return_value.json.return_value = {"results": [dict(article(), url="https://employer.example/jobs/123")]}
        checked.return_value = article()
        SearchTool().search("engineering jobs")
        self.assertNotIn("time_range", post.call_args.kwargs["json"])
        self.assertNotIn("include_domains", post.call_args.kwargs["json"])
        checked.assert_called_once_with(post.return_value.json.return_value["results"][0])


if __name__ == "__main__":
    unittest.main()
