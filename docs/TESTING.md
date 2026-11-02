# Testing status

Checks run on November 1, 2026 using a project-local, ignored portable Python 3.11.9 runtime on Windows. The user reported a real Tavily run with 9/10 pages fetched but no eligible recent articles; this motivated the discovery/date changes. The revised pipeline has been tested offline only; no live search or model results are claimed here.

## Actual offline results

- `python -m unittest discover -v`: **58 tests passed**, including Streamlit AppTest initial rendering, all four tabs, profile saving and the missing-search-key error path.
- Progress regression tests cover qualitative-gap persistence through answer evaluation and database reopening, gaps despite an overall score of 3, low individual rubric scores, latest assessment per career/skill, historical payloads without optional fields, and unchanged stored answer events. A Streamlit AppTest verifies two saved evaluations' gaps appear on the Dashboard across fresh app instances.
- Mocked Career Coach evaluation tests cover ASCII/Unicode answers, an explicit JSON directive in the Responses input, API-versus-parsing-versus-validation errors, integer/bool/float/range checks, required text/list fields, separate follow-up generation, and preservation of pending sessions and learning history after failures.
- Ranking regression tests cover model order, duplicate/unknown/non-integer IDs, missing fields, incorrect counts, malformed JSON parsing, explicit candidate indices, one/two candidates, strict schema request construction, deterministic fallback and failed-summary database integrity. Career Coach JSON requests retain JSON-object mode.
- A request-level regression forces the primary and both fallback searches through the actual Tavily request construction, verifying exact rolling 30-day boundaries, no `time_range`, and preserved date/domain filters.
- Search-error regression tests cover HTTP validation/authentication/rate/quota/server categories, timeout/connection failures, malformed successful responses and non-JSON errors. Terminal diagnostics retain status, error type and sanitized error fields while omitting request inputs, headers, URLs, configured secrets and credential patterns.
- Article regression fixtures cover nested Article JSON-LD, publication/Open Graph/meta fields, labeled time and visible dates, provider/page agreement and conflicts, invalid/future/missing dates, publication versus modification, exact 30-day boundaries, excluded page types, duplicate diagnostics, domain/date request filters, fallback expansion/exhaustion/early stop and partial search failures. A regression check confirms Career Coach search receives no article-only filters.
- Workflow tests cover persistent profile/history, daily claim uniqueness, article dates/domain boundaries, page-derived metadata, private destination rejection, recommendation deduplication, failed reasoning, invalid ranking, multi-turn interview state, invalid score rejection, failed-evaluation recovery, shared skill gaps and learning feedback, and report prerequisites.
- `python -m compileall -q app.py coworkers tests`: passed.
- `python -m pip check`: no broken requirements.
- `git diff --check`: passed. Ignore checks confirm `.env`, `data/` and `.runtime/` are excluded, while `.env.example` remains available for tracking.
- Actual resolved main dependencies: Streamlit 1.66.1, OpenAI 2.54.0, python-dotenv 1.2.4, requests 2.34.2, BeautifulSoup 4.15.0. These are observed local versions, not a lockfile.

The first run caught an unclosed SQLite connection on Windows. Connection cleanup was fixed before the passing run. All model/search outputs used by automated workflow tests are synthetic; no test result establishes live provider behavior or assessment quality.

## Live checks still required

- OpenAI requests with a real key and accessible model.
- Revised Tavily discovery and Serper search with real keys. The user's earlier Tavily run established the original failure, not success of the revised code.
- Real article summaries and opportunity freshness.
- Multi-turn interview evaluation quality with actual model outputs.
- Daily automatic execution during an active browser session.
- Real sample results: follow `EVALUATION.md`. No fabricated results are included.

## Implementation references

### Progress Dashboard investigation

`coach.answer` stores the entire validated evaluation, including `evaluation.gaps`, inside the SQLite `events` row with kind `answer`. The Dashboard previously called only `Memory.weak_skills`, which reads the latest overall score for each skill and requires it to be below 3. It ignored saved qualitative gaps and individual rubric scores. Thus a score at least 3 could coexist with real recorded gaps and a misleading "No recorded gaps yet" message. This is a read/aggregation mismatch, not a missing write in the evaluation workflow.

`Memory.interview_weaknesses` now reads the latest answer per career/skill and combines recorded qualitative gaps, individual rubric scores below 3, and overall scores below 3. The Dashboard displays this evidence with career/skill labels and explains the latest-assessment scope. The score-only `weak_skills` method remains unchanged for existing agent personalization. Missing qualitative data is not fabricated; report-only prose is not parsed into structured gaps. No schema migration or historical score edits are involved. All regression databases are temporary; no live APIs or user data were used.

### Career Coach evaluation investigation

Pipeline: the Streamlit evaluation form calls `coach.answer`; it reads the saved active session/question, calls `Reasoner.ask` in JSON-object mode, parses JSON, validates the three rubric scores and required fields, computes their mean, then saves the answer event and updated interview. Follow-up generation is a separate button calling `follow_up`; it is not part of answer evaluation. No evaluation data is persisted before successful parsing and validation.

The generic error originated in the final broad exception handler of `Reasoner.ask`, then was displayed by `app.run_action`. A confirmed request-construction omission was that JSON mode placed its explicit JSON directive only in `instructions`, while the separate input string contained only serialized question/answer/rubric data and could lack the word JSON. A mocked input-prerequisite check failed before the fix for ASCII and Unicode answers and passed afterward. JSON-object requests now prepend a constant JSON instruction to the input. Text and strict-schema requests are unchanged. This fixes the concrete input-contract defect; the exact HTTP error from the user's previous live call remains unconfirmed because its body/status were not recorded. The encoding warning is not evidence of the cause.

The evaluation prompt and validator agree: correctness/reasoning/application integer scores 0-4 (bools/floats rejected), nonempty feedback and plan strings, strengths/gaps arrays of strings. JSON mode does not itself enforce this schema, so local validation remains required. Safe logs distinguish request-data, API-request, response-parsing and scoring-validation stages using fixed categories, exception class and HTTP status only. No raw exception/body, question, answer, profile, credentials or response text is logged. The real SQLite database was not opened or modified for testing. A user-run live evaluation is still needed to confirm behavior with the configured account; none was made during this fix.

### Article ranking investigation

The old failure was raised when `indices` was not a nonempty list of integers in `0..candidate_count-1`. Missing/non-object output became an empty list; bool/string IDs and unknown IDs also failed. Duplicate IDs and excess selections did not cause that error: they were silently deduplicated/truncated. Fewer than three candidates were allowed, but the prompt did not explicitly describe that case or label each candidate index. The request used JSON-object mode, which did not enforce the expected `indices` schema, and passed provider IDs inside candidates alongside the implicit array indices. These contract weaknesses are confirmed in code. The exact offending live output/condition remains unknown because the prior error recorded no safe diagnostics; no new live call was made.

The ranking request now supplies explicit `candidate_index` values with provider IDs omitted and uses a strict JSON schema with integer enums and exactly `min(3, candidate_count)` entries. Runtime validation additionally enforces uniqueness. Malformed/empty output and invalid rankings log only a failure category, candidate count, expected format and bounded integer IDs. Local fallback computes topic overlap (weak-skill matches weighted twice), ties by original publication date and then discovery order. It only orders already-verified candidates and is labeled in the saved brief and UI. API/transport/schema-support failures remain errors; no fallback masks them. Briefs are still saved only after successful summary generation. The default configured model supports structured outputs; a custom model must support Responses JSON-schema output to use this ranking path.

The silent terminal failure was established in code: `SearchTool.search` caught HTTP/transport/JSON/schema errors and replaced all of them with a generic `SearchError` without logging. `app.run_action` then displayed that exception, so no traceback escaped to Streamlit. It now logs a sanitized warning and returns a categorized UI error. Article verification and search parameters were not changed by this diagnostic fix.

The provider diagnostic supplied by the user established the HTTP 400 cause: "When time_range is set, start_date or end_date cannot be set". The earlier documentation review identified valid individual parameters but did not establish that they could be combined. The shared Tavily request builder now sends only explicit `start_date` and `end_date` for article searches, covering primary and fallback requests. No live calls were made for this fix; live success after the fix has not been verified. Publication verification and authoritative-source filtering remain unchanged.

- [OpenAI Responses API](https://developers.openai.com/api/reference/python/resources/responses/methods/create)
- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)
- [Streamlit fragments](https://docs.streamlit.io/develop/concepts/architecture/fragments)
- [Tavily search API](https://docs.tavily.com/documentation/api-reference/endpoint/search)
