# Evaluation methodology

| Area | Procedure | Measure |
|---|---|---|
| Source accuracy | Open every recommendation; compare title, date, domain and URL | 100% traceable identities; missing metadata excluded |
| Recentness and repetition | Compare dates and repeat a run with the same results | All within 30 days; zero repeated normalized URLs |
| Relevance | User rates briefs and records goal alignment | Aim for mean usefulness at least 4/5 across five real briefs |
| Shared memory | Assess a weak skill, restart, request a brief | Skill persists and influences ranking/search |
| Learning | Explain a concept before/after the exercise; record completion | Qualitative improvement; completion alone is not mastery |
| Job accuracy | Check actual employer listing and extracted requirements | No invented jobs; stale/general pages labeled |
| Feedback quality | Human independently scores ten real answers using the same rubric | Per-criterion absolute error and agreement within one point |
| Progress | Repeat comparable questions across at least three sessions | Compare same career/skill while controlling for difficulty |
| Reliability | Missing keys, HTTP failure, invalid JSON, restart, duplicate daily claims | Clear errors, no fabricated score, persistent memory |

Rubric dimensions: correctness, reasoning and application, each 0-4. Overall score is their mean; latest skill scores below 3 guide learning. Readiness is a practice assessment, not an employment forecast.

## Collect real sample results

1. Configure your keys and record date/time, model, search provider and sanitized profile.
2. Generate a brief. Save actual titles/dates/URLs and verify every reference manually.
3. Find an opportunity. Confirm the employer listing, availability and eligibility, and compare extracted skills.
4. Complete two interview turns. Preserve actual questions, answers, rubric scores, feedback and follow-up; have a reviewer score independently.
5. Restart and confirm saved history/session. Generate a report and inspect its claims against recorded answers.
6. Enable scheduling with the current server hour and an active browser session. Observe a saved brief or explicit failure; confirm a rerun does not make another automatic attempt that day. This does not prove unattended scheduling.
7. Repeat over several sessions/days. Keep private evidence in ignored `data/`; only share reviewed, sanitized artifacts.

```text
Run date/time/timezone:
App commit/version:
Model/search provider:
Sanitized profile/goal:
Actual titles/dates/source URLs:
Manual verification results:
Generated brief/opportunity analysis:
Actual interview question/answer:
AI scores/human scores:
Follow-up/preparation plan:
Restart/scheduling observations:
Observed limitations/failures:
```

Synthetic test fixtures are engineering evidence only and must not be submitted as real articles, jobs or learner performance.
