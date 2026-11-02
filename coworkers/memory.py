"""Small SQLite store. Open a connection per operation for Streamlit reruns."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class Memory:
    def __init__(self, path="data/coworkers.db"):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, kind TEXT NOT NULL,
                    created TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS daily_runs (
                    day TEXT PRIMARY KEY, status TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def add(self, kind, payload):
        with self.connect() as db:
            cursor = db.execute("INSERT INTO events(kind,created,payload) VALUES (?,?,?)",
                                (kind, datetime.now(timezone.utc).isoformat(), json.dumps(payload)))
            return cursor.lastrowid

    def events(self, kind):
        with self.connect() as db:
            rows = db.execute("SELECT id,created,payload FROM events WHERE kind=? ORDER BY id DESC", (kind,)).fetchall()
        return [dict(id=r[0], created=r[1], **json.loads(r[2])) for r in rows]

    def weak_skills(self):
        scores = {}
        for turn in reversed(self.events("answer")):
            scores[turn["skill"]] = turn["score"]
        return [skill for skill, score in scores.items() if score < 3]

    def interview_weaknesses(self):
        """Read recorded evidence from the latest assessment per career/skill.

        No migration or score changes: historical payloads without optional
        evaluation fields still contribute their recorded overall score.
        """
        latest, priorities = set(), []
        for turn in self.events("answer"):
            skill = turn.get("skill")
            if not isinstance(skill, str) or not skill.strip():
                continue
            career = turn.get("career", "Unspecified career")
            identity = (career, skill)
            if identity in latest:
                continue
            latest.add(identity)
            evaluation = turn.get("evaluation")
            evaluation = evaluation if isinstance(evaluation, dict) else {}
            raw_gaps = evaluation.get("gaps", [])
            gaps = list(dict.fromkeys(g.strip() for g in raw_gaps if isinstance(g, str) and g.strip())) if isinstance(raw_gaps, list) else []
            scores = evaluation.get("scores", {})
            scores = scores if isinstance(scores, dict) else {}
            rubric_gaps = {name: score for name, score in scores.items()
                           if name in ("correctness", "reasoning", "application")
                           and type(score) in (int, float) and 0 <= score < 3}
            score = turn.get("score")
            low_overall = type(score) in (int, float) and 0 <= score < 3
            if gaps or rubric_gaps or low_overall:
                priorities.append({"career": career, "skill": skill, "gaps": gaps,
                                   "rubric_gaps": rubric_gaps, "score": score,
                                   "low_overall": low_overall, "assessment_id": turn["id"]})
        return priorities

    def claim_day(self, day):
        # A primary key prevents two browser sessions from scheduling the same day.
        with self.connect() as db:
            return db.execute("INSERT OR IGNORE INTO daily_runs VALUES (?, 'attempted')", (day,)).rowcount == 1

    def finish_day(self, day):
        with self.connect() as db:
            db.execute("UPDATE daily_runs SET status='complete' WHERE day=?", (day,))
