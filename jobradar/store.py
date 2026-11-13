"""SQLite state. The whole point: remember what we have already seen, so a
later run can tell 'this is NEW' from 'this was here last week'."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    company     TEXT NOT NULL,
    title       TEXT NOT NULL,
    url         TEXT,
    location    TEXT,
    source      TEXT,
    posted_at   REAL,
    first_seen  REAL NOT NULL,
    last_seen   REAL NOT NULL,
    closed_at   REAL,
    score       INTEGER,
    reason      TEXT,
    notified    INTEGER NOT NULL DEFAULT 0,
    domain      TEXT,
    breakdown   TEXT,
    alerted     INTEGER NOT NULL DEFAULT 0,
    description TEXT,
    experience  TEXT,
    role        TEXT,
    role_rank   INTEGER,
    role_bonus  REAL NOT NULL DEFAULT 0,
    -- what the posting states as data (details/): read once, when first seen
    work_mode       TEXT,
    employment_type TEXT,
    salary          TEXT,
    closes_at       REAL,
    first_posted    REAL,
    years_min       INTEGER,
    years_max       INTEGER,
    skills          TEXT,
    skills_nice     TEXT
);
CREATE INDEX IF NOT EXISTS idx_first_seen ON jobs(first_seen);
CREATE TABLE IF NOT EXISTS runs (
    ts INTEGER PRIMARY KEY, new_count INTEGER, closed_count INTEGER, errors TEXT
);
-- One row per company board: when it was last fetched successfully, so a scan
-- can skip boards it read recently instead of re-reading every one each time.
-- One row per crawl (web Scan, `scan`, `run`, `alert`): how many companies
-- the DB held and what became of them, so the UI can show each crawl's reach.
CREATE TABLE IF NOT EXISTS crawls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    started_at  REAL NOT NULL,
    finished_at REAL,
    in_db       INTEGER NOT NULL DEFAULT 0,   -- every company in the table
    readable    INTEGER NOT NULL DEFAULT 0,   -- enabled with a board we can read
    skipped     INTEGER NOT NULL DEFAULT 0,   -- read within refresh_hours
    due         INTEGER NOT NULL DEFAULT 0,   -- set to be read this crawl
    ok          INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    new_jobs    INTEGER NOT NULL DEFAULT 0,
    closed_jobs INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS boards (
    company     TEXT PRIMARY KEY,
    fetched_at  REAL NOT NULL,
    job_count   INTEGER NOT NULL DEFAULT 0
);
-- How long each board took to read last time, failed reads included: a scan
-- puts the slow ones in their own queue so they never hold up the fast ones.
CREATE TABLE IF NOT EXISTS board_speed (
    company     TEXT PRIMARY KEY,
    secs        REAL NOT NULL,
    read_at     REAL NOT NULL
);
-- Your application history. job_id links to a tracked posting when there is
-- one; applications to jobs Job Radar never saw keep company/title/url only.
CREATE TABLE IF NOT EXISTS applications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT,
    company     TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'applied',
    applied_at  REAL,
    updated_at  REAL NOT NULL,
    notes       TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'manual',
    location    TEXT NOT NULL DEFAULT '',
    experience  TEXT NOT NULL DEFAULT '',
    skills      TEXT NOT NULL DEFAULT '',
    applied_by  TEXT NOT NULL DEFAULT '',   -- '' you, 'ai_engine', 'ai_assisted'
    resume      TEXT NOT NULL DEFAULT '',   -- the resume the AI engine sent
    proof       TEXT NOT NULL DEFAULT '',   -- screenshot of the submitted page
    queued_at   REAL                        -- when it joined the apply queue
);
CREATE INDEX IF NOT EXISTS idx_app_job ON applications(job_id);
-- Every company Job Radar knows about, scannable or not. The source of truth
-- for which boards a scan reads; companies.json is an export of this table.
CREATE TABLE IF NOT EXISTS companies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    board           TEXT NOT NULL DEFAULT 'other',
    slug            TEXT NOT NULL DEFAULT '',
    careers_url     TEXT NOT NULL DEFAULT '',
    domain          TEXT NOT NULL DEFAULT '',
    source          TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    note            TEXT NOT NULL DEFAULT '',
    india_jobs      INTEGER,
    india_tech_jobs INTEGER,
    checked_at      REAL,
    added_at        REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_company_name ON companies(lower(name));
-- Every status change, so the history shows when you applied, interviewed...
CREATE TABLE IF NOT EXISTS application_events (
    app_id  INTEGER NOT NULL,
    status  TEXT NOT NULL,
    at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_events ON application_events(app_id);
"""

# Pipeline order. 'not_applied' records a decision (or a reset) without an
# application, so it is the one status that leaves a job in scoring, digests
# and alerts.
STATUSES = ("not_applied", "queued", "applying", "needs_you", "failed", "in_process",
            "referral_asked", "referral_got", "applied", "ai_applied", "contacted", "assessment",
            "interview", "offer", "rejected", "expired", "withdrawn", "over_exp")

# The AI apply engine's own stages, a referral asked for but not yet given,
# and a job set aside as asking for more experience. None of them is an application yet, so none of them sets applied_at -
# that waits for the submit.
PRE_APPLY = ("not_applied", "queued", "applying", "needs_you", "failed", "referral_asked",
             "over_exp")
# What the apply queue holds, the job being worked on first.
QUEUE = ("applying", "needs_you", "queued")

# Jobs with a real application are done: no tokens, emails or alerts for them.
_NOT_APPLIED = ("id NOT IN (SELECT job_id FROM applications "
                "WHERE job_id IS NOT NULL AND status != 'not_applied')")

FACT_COLUMNS = ("work_mode", "employment_type", "salary", "closes_at", "first_posted",
                "years_min", "years_max", "skills", "skills_nice")


def _joined(v) -> str:
    """A skill list is stored as "Node.js, React" - short, and readable as is."""
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v if x)
    return v or ""


def _facts(j: dict) -> tuple:
    """A job's posting facts, in FACT_COLUMNS order."""
    return (j.get("work_mode") or "", j.get("employment_type") or "", (j.get("salary") or "")[:80],
            j.get("closes_at"), j.get("first_posted"), j.get("years_min"), j.get("years_max"),
            _joined(j.get("skills")), _joined(j.get("skills_nice")))


class Store:
    def __init__(self, path: str | Path = "jobradar.db"):
        # The scan, the web app and the scheduled task share this file; wait for
        # another writer's commit instead of failing after sqlite's default 5 s.
        self.db = sqlite3.connect(str(path), timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()
        self.db.commit()
        self.last_upsert_ts = time.time()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created."""
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(jobs)")}
        for col, ddl in (("domain", "TEXT"), ("breakdown", "TEXT"),
                         ("alerted", "INTEGER NOT NULL DEFAULT 0"),
                         ("description", "TEXT"), ("experience", "TEXT"),
                         ("role", "TEXT"), ("role_rank", "INTEGER"),
                         ("role_bonus", "REAL NOT NULL DEFAULT 0"),
                         ("work_mode", "TEXT"), ("employment_type", "TEXT"), ("salary", "TEXT"),
                         ("closes_at", "REAL"), ("first_posted", "REAL"), ("years_min", "INTEGER"),
                         ("years_max", "INTEGER"), ("skills", "TEXT"), ("skills_nice", "TEXT")):
            if col not in have:
                self.db.execute(f"ALTER TABLE jobs ADD COLUMN {col} {ddl}")
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(applications)")}
        for col in ("location", "experience", "skills", "applied_by", "resume", "proof"):
            if col not in have:
                self.db.execute(
                    f"ALTER TABLE applications ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
        if "queued_at" not in have:
            self.db.execute("ALTER TABLE applications ADD COLUMN queued_at REAL")
        # 'referral' split into asked / got; an old one was an application made
        # through a referral.
        for table in ("applications", "application_events"):
            self.db.execute(f"UPDATE {table} SET status = 'referral_got' WHERE status = 'referral'")

    # -- ingest -------------------------------------------------------------
    def upsert(self, jobs: list[dict]) -> list[dict]:
        """Insert jobs, returning only the ones never seen before."""
        now = time.time()
        self.last_upsert_ts = now
        fresh = []
        for j in jobs:
            row = self.db.execute("SELECT id FROM jobs WHERE id = ?", (j["id"],)).fetchone()
            if row:
                self.db.execute(
                    "UPDATE jobs SET last_seen = ?, closed_at = NULL WHERE id = ?", (now, j["id"])
                )
            else:
                self.db.execute(
                    """INSERT INTO jobs (id, company, title, url, location, source,
                                         posted_at, first_seen, last_seen, domain, description,
                                         experience, role, role_rank, role_bonus,
                                         work_mode, employment_type, salary, closes_at, first_posted,
                                         years_min, years_max, skills, skills_nice)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (j["id"], j["company"], j["title"], j["url"], j["location"],
                     j["source"], j["posted_at"], now, now, j.get("domain", ""),
                     # Kept whole: `recheck` re-reads requirements from it, and a
                     # requirement past a cut is invisible. The scoring prompt
                     # takes only a short excerpt, so this costs no tokens.
                     (j.get("description") or "")[:20000], j.get("experience") or "",
                     j.get("role"), j.get("role_rank"), j.get("role_bonus") or 0,
                     *_facts(j)),
                )
                fresh.append(j)
        self.db.commit()
        return fresh

    def known_ids(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT id FROM jobs")}

    def mark_closed(self, companies: list[str], run_ts: float | None = None) -> list[sqlite3.Row]:
        """A tracked job that stopped appearing on its board is treated as closed.
        Only applies to companies we actually fetched successfully this run.

        run_ts defaults to the timestamp of the last upsert, so 'was not seen in
        the run that just finished' cannot be skewed by the caller's clock."""
        run_ts = run_ts if run_ts is not None else getattr(self, "last_upsert_ts", time.time())
        if not companies:
            return []
        marks = ",".join("?" * len(companies))
        rows = self.db.execute(
            f"""SELECT * FROM jobs WHERE company IN ({marks})
                AND closed_at IS NULL AND last_seen < ?""",
            (*companies, run_ts),
        ).fetchall()
        for r in rows:
            self.db.execute("UPDATE jobs SET closed_at = ? WHERE id = ?", (run_ts, r["id"]))
        self.db.commit()
        return rows

    # -- scoring ------------------------------------------------------------
    def save_score(self, job_id: str, score: int, reason: str,
                   breakdown: dict | None = None) -> None:
        self.db.execute(
            "UPDATE jobs SET score = ?, reason = ?, breakdown = ? WHERE id = ?",
            (score, reason, json.dumps(breakdown or {}), job_id),
        )
        self.db.commit()

    def unscored(self, limit: int) -> list[sqlite3.Row]:
        return self.db.execute(
            f"SELECT * FROM jobs WHERE score IS NULL AND closed_at IS NULL AND {_NOT_APPLIED} "
            "ORDER BY first_seen DESC LIMIT ?", (limit,)
        ).fetchall()

    # -- reporting ----------------------------------------------------------
    def unscored_count(self) -> int:
        return self.db.execute(
            f"SELECT COUNT(*) FROM jobs WHERE score IS NULL AND closed_at IS NULL AND {_NOT_APPLIED}"
        ).fetchone()[0]

    def since(self, seconds: float, min_score: int = 0) -> list[sqlite3.Row]:
        cutoff = time.time() - seconds
        return self.db.execute(
            """SELECT * FROM jobs WHERE first_seen >= ? AND closed_at IS NULL
               AND (score IS NULL OR score >= ?)
               ORDER BY score + role_bonus DESC NULLS LAST, role_rank, first_seen DESC""",
            (cutoff, min_score),
        ).fetchall()

    def closed_since(self, seconds: float) -> list[sqlite3.Row]:
        cutoff = time.time() - seconds
        return self.db.execute(
            "SELECT * FROM jobs WHERE closed_at >= ? ORDER BY closed_at DESC", (cutoff,)
        ).fetchall()

    def unnotified(self, min_score: int, require_scored: bool = False) -> list[sqlite3.Row]:
        """require_scored=True when a scorer is configured: a job the run had no
        budget left to score must wait for the next run rather than arrive
        unranked. Without it, a first scan of a big watchlist would email every
        job that overflowed max_to_score."""
        unscored = "AND score IS NOT NULL" if require_scored else ""
        return self.db.execute(
            f"""SELECT * FROM jobs WHERE notified = 0 AND closed_at IS NULL
                {unscored} AND {_NOT_APPLIED} AND (score IS NULL OR score >= ?)
                ORDER BY score + role_bonus DESC NULLS LAST, role_rank, first_seen DESC""",
            (min_score,),
        ).fetchall()

    def unalerted(self) -> list[sqlite3.Row]:
        """Jobs never sent as an instant alert. Separate from `notified` so the
        weekly digest and the alerts do not consume each other's queue."""
        return self.db.execute(
            f"""SELECT * FROM jobs WHERE alerted = 0 AND closed_at IS NULL AND {_NOT_APPLIED}
               ORDER BY first_seen DESC"""
        ).fetchall()

    def baselined_companies(self) -> set[str]:
        """Companies that have been through at least one alert run. Anything
        else is being seen for the first time, so its whole board is history,
        not news."""
        return {r["company"] for r in
                self.db.execute("SELECT DISTINCT company FROM jobs WHERE alerted = 1")}

    def mark_alerted(self, ids: list[str]) -> None:
        self.db.executemany("UPDATE jobs SET alerted = 1 WHERE id = ?", [(i,) for i in ids])
        self.db.commit()

    def mark_notified(self, ids: list[str]) -> None:
        self.db.executemany("UPDATE jobs SET notified = 1 WHERE id = ?", [(i,) for i in ids])
        self.db.commit()

    def log_run(self, new_count: int, closed_count: int, errors: list[str]) -> None:
        self.db.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?)",
                        (int(time.time()), new_count, closed_count, "\n".join(errors)))
        self.db.commit()

    # -- crawl history ------------------------------------------------------
    def start_crawl(self, source: str, readable: int, skipped: int, due: int) -> int:
        in_db = self.db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        cur = self.db.execute(
            "INSERT INTO crawls (source, started_at, in_db, readable, skipped, due) "
            "VALUES (?,?,?,?,?,?)", (source, time.time(), in_db, readable, skipped, due))
        self.db.commit()
        return cur.lastrowid

    def add_to_crawl(self, crawl_id: int, ok: int = 0, failed: int = 0,
                     new_jobs: int = 0, closed_jobs: int = 0) -> None:
        """Add one batch's counts; a batched crawl calls this once per batch."""
        self.db.execute(
            "UPDATE crawls SET ok = ok + ?, failed = failed + ?, new_jobs = new_jobs + ?, "
            "closed_jobs = closed_jobs + ? WHERE id = ?",
            (ok, failed, new_jobs, closed_jobs, crawl_id))
        self.db.commit()

    def finish_crawl(self, crawl_id: int) -> None:
        self.db.execute("UPDATE crawls SET finished_at = ? WHERE id = ?", (time.time(), crawl_id))
        self.db.commit()

    def recent_crawls(self, n: int = 10) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM crawls ORDER BY started_at DESC LIMIT ?", (n,))]

    def stats(self) -> dict:
        row = self.db.execute(
            "SELECT COUNT(*) total, SUM(closed_at IS NULL) open FROM jobs"
        ).fetchone()
        return {"total": row["total"] or 0, "open": row["open"] or 0}

    # -- board freshness ----------------------------------------------------
    def mark_fetched(self, company: str, job_count: int, ts: float | None = None) -> None:
        self.db.execute(
            "INSERT INTO boards (company, fetched_at, job_count) VALUES (?,?,?) "
            "ON CONFLICT(company) DO UPDATE SET fetched_at = excluded.fetched_at, "
            "job_count = excluded.job_count",
            (company, ts or time.time(), job_count),
        )
        self.db.commit()

    def board_ages(self) -> dict[str, float]:
        """company -> when its board was last read successfully."""
        return {r["company"]: r["fetched_at"] for r in
                self.db.execute("SELECT company, fetched_at FROM boards")}

    def board_speeds(self) -> dict[str, float]:
        """company -> seconds its board took to read last time."""
        return {r["company"]: r["secs"] for r in self.db.execute("SELECT company, secs FROM board_speed")}

    def save_board_speeds(self, secs: dict[str, float]) -> None:
        now = time.time()
        self.db.executemany(
            "INSERT INTO board_speed (company, secs, read_at) VALUES (?,?,?) "
            "ON CONFLICT(company) DO UPDATE SET secs = excluded.secs, read_at = excluded.read_at",
            [(c, round(s, 2), now) for c, s in secs.items()])
        self.db.commit()

    def active_companies(self) -> set[str]:
        """Companies worth reading on every scan: India tech jobs at their last
        check, or a job of theirs still open in the list."""
        return {r[0] for r in self.db.execute(
            "SELECT name FROM companies WHERE india_tech_jobs > 0 "
            "UNION SELECT company FROM jobs WHERE closed_at IS NULL")}

    def fresh_boards(self, max_age_hours: float) -> set[str]:
        """Companies fetched successfully within max_age_hours - safe to skip."""
        if not max_age_hours or max_age_hours <= 0:
            return set()
        cutoff = time.time() - max_age_hours * 3600
        return {r["company"] for r in
                self.db.execute("SELECT company FROM boards WHERE fetched_at >= ?", (cutoff,))}

    # -- application history ------------------------------------------------
    def add_application(self, company: str, title: str = "", url: str = "",
                        status: str = "applied", applied_at: float | None = None,
                        notes: str = "", job_id: str | None = None,
                        source: str = "manual", location: str = "",
                        experience: str = "", skills: str = "") -> int:
        if status not in STATUSES:
            raise ValueError(f"unknown status '{status}' (one of: {', '.join(STATUSES)})")
        if job_id is None:
            job_id = self._match_job(company, title, url)
        now = time.time()
        if applied_at is None and status not in PRE_APPLY:
            applied_at = now
        cur = self.db.execute(
            """INSERT INTO applications (job_id, company, title, url, status, applied_at,
                                         updated_at, notes, source, location, experience, skills)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job_id, company.strip(), (title or "").strip(), (url or "").strip(), status,
             applied_at, now, notes or "", source, location or "", experience or "",
             skills or ""),
        )
        self._log_event(cur.lastrowid, status, now)
        if status == "queued":
            self.db.execute("UPDATE applications SET queued_at = ? WHERE id = ?",
                            (now, cur.lastrowid))
        self.db.commit()
        return cur.lastrowid

    def update_application(self, app_id: int, status: str | None = None,
                           notes: str | None = None) -> None:
        if status is not None and status not in STATUSES:
            raise ValueError(f"unknown status '{status}'")
        row = self.db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
        if row is None:
            raise KeyError(app_id)
        new_status = status if status is not None else row["status"]
        now = time.time()
        # The first move past "not applied" is the application date.
        applied_at = row["applied_at"]
        if applied_at is None and new_status not in PRE_APPLY:
            applied_at = now
        self.db.execute(
            "UPDATE applications SET status = ?, notes = ?, applied_at = ?, updated_at = ? "
            "WHERE id = ?",
            (new_status, notes if notes is not None else row["notes"], applied_at, now, app_id),
        )
        if new_status != row["status"]:
            self._log_event(app_id, new_status, now)
            if new_status == "queued" and row["status"] not in QUEUE:
                # Joining the queue from any dropdown: to the back of the line.
                self.db.execute("UPDATE applications SET queued_at = ?, applied_by = '' "
                                "WHERE id = ?", (now, app_id))
        self.db.commit()

    def set_job_status(self, job_id: str, status: str, notes: str | None = None) -> int:
        """The UI's dropdown: record a status for a tracked posting, creating
        its history row on first use with the posting's details snapshotted -
        the posting itself may close and vanish from the board later."""
        existing = self.application_for(job_id)
        if existing is not None:
            self.update_application(existing["id"], status=status, notes=notes)
            return existing["id"]
        job = self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise KeyError(job_id)
        from .experience import experience_label, job_skills   # local: avoids a cycle
        return self.add_application(
            job["company"], job["title"], job["url"] or "", status=status,
            notes=notes or "", job_id=job_id, source="radar",
            location=job["location"] or "",
            experience=job["experience"] or experience_label(job["title"],
                                                             job["description"] or ""),
            skills=", ".join(job_skills(job["title"], job["description"] or "")),
        )

    def descriptions(self, ids: list[str]) -> dict[str, str]:
        """id -> saved description, for the jobs that have one."""
        out: dict[str, str] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            out.update({r["id"]: r["description"] for r in self.db.execute(
                f"SELECT id, description FROM jobs WHERE id IN ({marks}) AND description != ''",
                chunk)})
        return out

    def set_description(self, job_id: str, text: str, experience: str) -> None:
        self.db.execute("UPDATE jobs SET description = ?, experience = ? WHERE id = ?",
                        (text[:20000], experience, job_id))
        self.db.commit()

    def save_facts(self, job: dict, commit: bool = True) -> None:
        """A re-read posting's text, date and facts (recheck)."""
        sets = ", ".join(f"{c} = ?" for c in FACT_COLUMNS)
        self.db.execute(f"UPDATE jobs SET description = ?, posted_at = ?, {sets} WHERE id = ?",
                        ((job.get("description") or "")[:20000], job.get("posted_at"), *_facts(job),
                         job["id"]))
        if commit:
            self.db.commit()

    def expire_old(self, days: float) -> int:
        """Postings the board says went up more than `days` ago are marked
        expired - a queued one leaves the queue. Jobs you already acted on
        (applied, in process, referral...) keep their status. Returns how many."""
        acted = """AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.job_id = j.id
                               AND a.status NOT IN ('not_applied', 'queued', 'failed'))"""
        # A posting whose own closing date has passed is expired whatever its age.
        closed = self.db.execute(
            f"""SELECT j.id FROM jobs j WHERE j.closes_at > 0 AND j.closes_at < ? AND j.closed_at IS NULL
                {acted}""", (time.time(),)).fetchall()
        for r in closed:
            self.set_job_status(r["id"], "expired", "applications closed")
        if not days or days <= 0:
            return len(closed)
        rows = self.db.execute(
            f"""SELECT j.id FROM jobs j WHERE j.posted_at > 0 AND j.posted_at < ? {acted}""",
            (time.time() - days * 86400,)).fetchall()
        for r in rows:
            self.set_job_status(r["id"], "expired", f"posted more than {days:g} days ago")
        return len(closed) + len(rows)

    # -- apply queue (the AI apply engine) ------------------------------------
    def enqueue(self, job_id: str) -> int:
        """Put a tracked posting in the apply queue. A job already applied to
        (by you or the engine) is refused rather than applied to twice."""
        existing = self.application_for(job_id)
        if existing is not None and existing["status"] not in PRE_APPLY:
            raise ValueError(f"already {existing['status'].replace('_', ' ')}")
        if existing is not None and existing["status"] in QUEUE:
            return existing["id"]              # already queued: it keeps its place
        app_id = self.set_job_status(job_id, "queued")
        self.db.execute("UPDATE applications SET queued_at = ?, applied_by = '' WHERE id = ?",
                        (time.time(), app_id))
        self.db.commit()
        return app_id

    def apply_queue(self) -> list[sqlite3.Row]:
        """The queue, the job in progress first, then oldest queued first."""
        marks = ",".join("?" * len(QUEUE))
        return self.db.execute(
            f"""SELECT a.*, j.source AS board, j.score AS score, j.domain AS domain
                FROM applications a LEFT JOIN jobs j ON j.id = a.job_id
                WHERE a.status IN ({marks})
                ORDER BY a.status = 'queued', a.queued_at, a.id""", QUEUE).fetchall()

    def next_queued(self) -> sqlite3.Row | None:
        return self.db.execute(
            """SELECT a.*, j.source AS board, j.description AS description, j.score AS score
               FROM applications a LEFT JOIN jobs j ON j.id = a.job_id
               WHERE a.status = 'queued' ORDER BY a.queued_at, a.id LIMIT 1""").fetchone()

    def requeue_stuck(self) -> int:
        """A job left mid-apply by a restart goes back to the front of the queue."""
        stuck = [r["id"] for r in self.db.execute(
            "SELECT id FROM applications WHERE status IN ('applying', 'needs_you')")]
        for app_id in stuck:
            self.update_application(app_id, "queued")      # logs the event too
        return len(stuck)

    def engine_done(self, n: int = 15) -> list[sqlite3.Row]:
        """What the engine finished most recently - applied or failed."""
        return self.db.execute(
            """SELECT * FROM applications WHERE queued_at IS NOT NULL
               AND (applied_by != '' OR status = 'failed')
               ORDER BY updated_at DESC LIMIT ?""", (n,)).fetchall()

    def ai_applied_since(self, ts: float) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM applications WHERE applied_by != '' AND applied_at >= ?",
            (ts,)).fetchone()[0]

    def finish_ai_application(self, app_id: int, applied_by: str = "ai_engine",
                              resume: str = "", proof: str = "", notes: str = "") -> None:
        """The engine submitted it: 'AI apply done', with what, and the proof."""
        self.update_application(app_id, status="ai_applied", notes=notes or None)
        self.db.execute("UPDATE applications SET applied_by = ?, resume = ?, proof = ? "
                        "WHERE id = ?", (applied_by, resume, proof, app_id))
        self.db.commit()

    def delete_application(self, app_id: int) -> None:
        self.db.execute("DELETE FROM application_events WHERE app_id = ?", (app_id,))
        self.db.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        self.db.commit()

    def _log_event(self, app_id: int, status: str, ts: float) -> None:
        self.db.execute("INSERT INTO application_events (app_id, status, at) VALUES (?,?,?)",
                        (app_id, status, ts))

    def events(self, app_id: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT status, at FROM application_events WHERE app_id = ? ORDER BY at",
            (app_id,)).fetchall()

    def applications(self) -> list[sqlite3.Row]:
        return self.db.execute(
            """SELECT a.*, j.score AS score, j.closed_at AS job_closed_at
               FROM applications a LEFT JOIN jobs j ON j.id = a.job_id
               ORDER BY COALESCE(a.applied_at, a.updated_at) DESC"""
        ).fetchall()

    def application_for(self, job_id: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM applications WHERE job_id = ? ORDER BY updated_at DESC LIMIT 1",
            (job_id,),
        ).fetchone()

    def has_application(self, company: str, title: str, url: str) -> bool:
        """Import dedupe: same link, or same company + role when there is no link."""
        if url:
            q = "SELECT 1 FROM applications WHERE url = ?"
            return self.db.execute(q, (url.strip(),)).fetchone() is not None
        q = "SELECT 1 FROM applications WHERE lower(company) = ? AND lower(title) = ?"
        return self.db.execute(q, (company.strip().lower(),
                                   (title or "").strip().lower())).fetchone() is not None

    def link_applications(self) -> int:
        """Attach unlinked applications to postings that have since been tracked,
        so a job you applied to off-radar is recognised when a scan finds it."""
        linked = 0
        for a in self.db.execute("SELECT * FROM applications WHERE job_id IS NULL").fetchall():
            jid = self._match_job(a["company"], a["title"], a["url"])
            if jid:
                self.db.execute("UPDATE applications SET job_id = ? WHERE id = ?", (jid, a["id"]))
                linked += 1
        self.db.commit()
        return linked

    def _match_job(self, company: str, title: str, url: str) -> str | None:
        if url:
            row = self.db.execute("SELECT id FROM jobs WHERE url = ?", (url.strip(),)).fetchone()
            if row:
                return row["id"]
        if company and title:
            row = self.db.execute(
                "SELECT id FROM jobs WHERE lower(company) = ? AND lower(title) = ? "
                "ORDER BY closed_at IS NOT NULL, last_seen DESC LIMIT 1",
                (company.strip().lower(), title.strip().lower()),
            ).fetchone()
            if row:
                return row["id"]
        return None

    # -- companies ----------------------------------------------------------
    _COMPANY_FIELDS = ("board", "slug", "careers_url", "domain", "source", "enabled", "note",
                       "india_jobs", "india_tech_jobs", "checked_at")

    def upsert_company(self, c: dict, commit: bool = True) -> str:
        """Insert, or update the row with the same name (case-insensitive).
        An update never downgrades: a readable board is not replaced by
        'other', and empty values do not blank out stored ones.
        Returns 'added', 'updated' or 'unchanged'."""
        name = (c.get("name") or "").strip()
        if not name:
            return "unchanged"
        row = self.db.execute("SELECT * FROM companies WHERE lower(name) = ?",
                              (name.lower(),)).fetchone()
        vals = {k: c[k] for k in self._COMPANY_FIELDS if k in c and c[k] not in (None, "")}
        if "enabled" in vals:
            vals["enabled"] = 1 if vals["enabled"] not in (False, 0, "0", "false") else 0
        if row is None:
            vals.setdefault("board", "other")
            vals.setdefault("enabled", 0 if vals["board"] == "other" else 1)
            cols = ["name", "added_at", *vals]
            self.db.execute(
                f"INSERT INTO companies ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                (name, time.time(), *vals.values()))
            result = "added"
        else:
            if row["board"] != "other" and vals.get("board") == "other":
                for k in ("board", "slug", "enabled"):
                    vals.pop(k, None)
            if row["source"]:
                vals.pop("source", None)       # where it was FIRST found stays put
            changed = {k: v for k, v in vals.items() if row[k] != v}
            if changed:
                self.db.execute(
                    f"UPDATE companies SET {', '.join(f'{k} = ?' for k in changed)} WHERE id = ?",
                    (*changed.values(), row["id"]))
            result = "updated" if changed else "unchanged"
        if commit:
            self.db.commit()
        return result

    def companies(self, enabled_only: bool = False) -> list[dict]:
        where = "WHERE enabled = 1 AND board != 'other'" if enabled_only else ""
        return [dict(r) for r in self.db.execute(
            f"SELECT * FROM companies {where} ORDER BY lower(name)")]

    def export_companies(self, path: str | Path) -> int:
        """Write the whole table to a JSON file - the readable copy of the DB."""
        rows = self.companies()
        for r in rows:
            r["enabled"] = bool(r["enabled"])
            r.pop("id", None)
        Path(path).write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n",
                              encoding="utf-8", newline="\n")
        return len(rows)
