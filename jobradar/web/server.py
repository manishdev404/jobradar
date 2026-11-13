"""Web UI for jobradar.

Serves a single page that shows every tracked job as a tile, tinted by how well
it matches the resume, and streams a live scan over Server-Sent Events so tiles
light up as they are scored.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import sources
from ..cursor import CursorError
from ..llm import RateLimited
from ..main import (ROOT, boards_due, get_profile, get_scorer, load_config, select_jobs,
                    start_crawl)
from ..resume import build_details, extract_text, parse_details
from ..relevance import apply_cap as apply_relevance_cap
from ..seniority import apply_cap
from ..store import STATUSES, Store

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Job Radar")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

_state = {"scanning": False, "started": 0.0}
_lock = threading.Lock()


def cfg():
    return load_config(ROOT / "config.yaml")


def store():
    return Store(ROOT / "jobradar.db")


@app.get("/")
def index():
    return FileResponse(str(STATIC / "index.html"))


# --- application history ------------------------------------------------------

class StatusIn(BaseModel):
    status: str
    notes: str | None = None


class ApplicationIn(BaseModel):
    company: str
    title: str = ""
    url: str = ""
    status: str = "applied"
    notes: str = ""
    location: str = ""


@app.get("/history")
def history_page():
    return FileResponse(str(STATIC / "history.html"))


@app.get("/api/statuses")
def statuses():
    return {"statuses": list(STATUSES)}


@app.post("/api/jobs/{job_id}/status")
def job_status(job_id: str, body: StatusIn):
    try:
        app_id = store().set_job_status(job_id, body.status, body.notes)
    except KeyError:
        raise HTTPException(404, "no such job")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": app_id, "status": body.status}


@app.get("/api/applications")
def applications():
    s = store()
    out = []
    for r in s.applications():
        row = dict(r)
        row["events"] = [dict(e) for e in s.events(r["id"])]
        out.append(row)
    return {"applications": out, "statuses": list(STATUSES)}


@app.post("/api/applications")
def add_application(body: ApplicationIn):
    if not body.company.strip():
        raise HTTPException(400, "company is required")
    try:
        app_id = store().add_application(body.company, body.title, body.url, body.status,
                                         notes=body.notes, location=body.location)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": app_id}


@app.patch("/api/applications/{app_id}")
def update_application(app_id: int, body: StatusIn):
    try:
        store().update_application(app_id, body.status, body.notes)
    except KeyError:
        raise HTTPException(404, "no such application")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": app_id, "status": body.status}


@app.delete("/api/applications/{app_id}")
def delete_application(app_id: int):
    store().delete_application(app_id)
    return {"deleted": app_id}


# --- AI apply engine ------------------------------------------------------------

_engine = None


def engine():
    """One engine per server: it owns the browser window."""
    global _engine
    if _engine is None:
        import os
        from ..apply.engine import Engine
        # JOBRADAR_APPLY_DRY_RUN=1: fill every page but stop before Submit.
        _engine = Engine(ROOT, llm_factory=lambda: get_scorer(cfg()),
                         dry_run=bool(os.environ.get("JOBRADAR_APPLY_DRY_RUN")))
    return _engine


class QueueIn(BaseModel):
    job_ids: list[str]


class ResumeIn(BaseModel):
    answer: str | None = None


def _apply_settings() -> dict:
    from ..apply.answers import Profile
    try:
        return Profile(ROOT).engine
    except FileNotFoundError:
        return {}


@app.get("/apply")
def apply_page():
    return FileResponse(str(STATIC / "apply.html"))


@app.post("/api/apply/queue")
def apply_enqueue(body: QueueIn):
    """Add jobs to the apply queue. A job below min_match_score_to_apply, at a
    company in skip_companies, or already applied to is refused with why."""
    s, rules = store(), _apply_settings()
    s.expire_old(float(cfg().get("expire_after_days", 45) or 0))   # old ones are refused as expired
    floor = rules.get("min_match_score_to_apply")
    skip = {c.lower() for c in rules.get("skip_companies") or []}
    added, refused = [], []
    for jid in body.job_ids:
        job = s.db.execute("SELECT company, title, score FROM jobs WHERE id = ?", (jid,)).fetchone()
        if job is None:
            refused.append({"id": jid, "why": "no such job"})
            continue
        if job["company"].lower() in skip:
            refused.append({"id": jid, "why": f"{job['company']} is in skip_companies"})
            continue
        if floor is not None and job["score"] is not None and job["score"] < float(floor):
            refused.append({"id": jid, "why": f"match {job['score']}/10 is below {floor}"})
            continue
        try:
            s.enqueue(jid)
            added.append(jid)
        except ValueError as e:
            refused.append({"id": jid, "why": str(e)})
    return {"added": added, "refused": refused}


@app.delete("/api/apply/queue/{app_id}")
def apply_dequeue(app_id: int):
    s = store()
    row = s.db.execute("SELECT status FROM applications WHERE id = ?", (app_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "not in the queue")
    if row["status"] in ("applying", "needs_you"):
        raise HTTPException(409, "being applied to now - use Skip")
    s.update_application(app_id, "not_applied", notes="removed from the apply queue")
    return {"removed": app_id}


@app.get("/api/apply/state")
def apply_state():
    s = store()
    queue = [dict(r) for r in s.apply_queue()]
    for q in queue:
        q.pop("notes", None)
    return {"engine": engine().snapshot(), "queue": queue,
            "done": [dict(r) for r in s.engine_done(20)],
            "settings": _apply_settings()}


@app.get("/api/apply/screen.jpg")
def apply_screen():
    from fastapi.responses import Response
    img = engine().screen()
    if not img:
        raise HTTPException(404, "no picture yet")
    return Response(img, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/apply/start")
def apply_start():
    return {"started": engine().start()}


@app.post("/api/apply/arrange")
def apply_arrange():
    """Put the engine's Chrome window on the left half of the screen."""
    return {"placed": engine().place_left()}


@app.post("/api/apply/stop")
def apply_stop():
    engine().stop()
    return {"stopping": True}


@app.post("/api/apply/resume")
def apply_resume(body: ResumeIn):
    engine().resume(body.answer)
    return {"resumed": True}


@app.post("/api/apply/skip")
def apply_skip():
    engine().skip()
    return {"skipping": True}


@app.get("/api/apply/proof/{app_id}")
def apply_proof(app_id: int):
    row = store().db.execute("SELECT proof FROM applications WHERE id = ?", (app_id,)).fetchone()
    if row is None or not row["proof"]:
        raise HTTPException(404, "no screenshot")
    path = (ROOT / row["proof"]).resolve()
    if ROOT.resolve() not in path.parents or not path.exists():
        raise HTTPException(404, "no screenshot")
    return FileResponse(str(path))


@app.get("/api/skills")
def skills():
    """Resume skills ranked most-done first, and what MERN / Python jobs ask for."""
    from ..skill import report_for
    try:
        return report_for(ROOT, cfg(), store())
    except Exception as e:  # noqa: BLE001 - no resume: an empty panel, not a 500
        return {"skills": [], "tracks": [], "error": f"{type(e).__name__}: {e}"[:200]}


@app.get("/api/profile")
def profile():
    c = cfg()
    cache = ROOT / ".resume_details.json"
    if cache.exists():
        cached = json.loads(cache.read_text())
        if cached.get("name"):          # only a real parse is worth keeping
            return cached
    try:
        text = extract_text(ROOT / c["resume_path"])
    except Exception as e:  # noqa: BLE001 - no resume at all
        return {"name": "", "headline": "", "location": "", "experience": [],
                "error": f"{type(e).__name__}: {e}"[:200]}

    # Reading a name off a resume does not need a model. Parse locally first so
    # the panel is populated with scoring off, then let a model refine it.
    details = parse_details(text)
    try:
        scorer = get_scorer(c)
        if scorer is not None and hasattr(scorer, "complete"):
            better = build_details(text, scorer)
            if better.get("name"):
                details = better
    except Exception as e:  # noqa: BLE001 - keep the local parse, note why
        details["error"] = f"{type(e).__name__}: {e}"[:200]
    if details.get("name"):
        cache.write_text(json.dumps(details))
    return details


@app.get("/api/state")
def state():
    c, s = cfg(), store()
    rows = s.db.execute(
        """SELECT id, company, title, url, location, score, reason, domain, source,
                  breakdown, posted_at, first_seen, closed_at, experience, role, role_rank, role_bonus,
                  (SELECT a.status FROM applications a WHERE a.job_id = jobs.id
                   ORDER BY a.updated_at DESC LIMIT 1) AS app_status
           FROM jobs
           WHERE closed_at IS NULL          -- an expired posting is not a job
           ORDER BY score + role_bonus DESC NULLS LAST, role_rank, company, title"""
    ).fetchall()
    jobs = _mosaic([dict(r) for r in rows], limit=4000)
    scored = [j for j in jobs if j["score"] is not None]
    threshold = c.get("llm", {}).get("min_score", 6)
    # Companies tile: boards read successfully out of every company in the DB;
    # its tooltip adds how many of those have a board Job Radar can read.
    listed = {e["name"] for e in c.get("companies", [])}
    read = listed & set(s.board_ages())
    return {
        "jobs": jobs,
        "companies": [e["name"] for e in c.get("companies", [])],
        "crawls": s.recent_crawls(8),
        "stats": {
            "crawled": len(read),
            "listed": len(listed),
            "in_db": s.db.execute("SELECT COUNT(*) FROM companies").fetchone()[0],
            "checked": len(scored),
            "total": len(jobs),
            "closed": s.db.execute(
                "SELECT COUNT(*) FROM jobs WHERE closed_at IS NOT NULL").fetchone()[0],
            "would_hire": len([j for j in scored if j["score"] >= threshold]),
            "avg": round(sum(j["score"] for j in scored) / len(scored) / 10, 2) if scored else 0,
            "threshold": threshold,
        },
        "scanning": _state["scanning"],
        "scan_days": _state.get("days", 0),
        "scan_started": _state.get("started", 0),
        "scoring": c.get("llm", {}).get("provider", "none") not in ("none", None, ""),
    }


def _mosaic(jobs: list[dict], limit: int) -> list[dict]:
    """Round-robin the jobs across companies.

    Straight SQL order groups every Stripe role together, which reads as a block
    of identical logos. Dealing one job per company at a time gives the grid its
    mosaic look and puts every company on screen early. Scored jobs are dealt
    first so results stay visible near the top.
    """
    from collections import OrderedDict, deque

    buckets: OrderedDict[str, deque] = OrderedDict()
    for j in sorted(jobs, key=lambda x: (x["score"] is None, -(x["score"] or 0))):
        buckets.setdefault(j["company"], deque()).append(j)

    out: list[dict] = []
    while buckets and len(out) < limit:
        for company in list(buckets):
            if not buckets[company]:
                del buckets[company]
                continue
            out.append(buckets[company].popleft())
            if len(out) >= limit:
                break
    return out


def _scan_events(limit: int, full: bool = False, days: int = 0, smart: bool = False):
    """Generator of SSE events. Fetch first, then score, emitting per job.
    days: only jobs posted in the last `days` days are scored (the page's
    "posted" filter); every job is still saved, so none looks closed."""
    c = cfg()
    started = time.time()
    q: queue.Queue = queue.Queue()

    def emit(kind, **data):
        q.put(f"data: {json.dumps({'type': kind, **data})}\n\n")

    def work():
        # sqlite3 connections are bound to the thread that created them, so the
        # worker opens its own rather than borrowing the request thread's.
        s = store()
        crawl = None
        try:
            # Same rules as cmd_scan: skip boards read within refresh_hours,
            # unless full (the web Scan button always asks for full).
            fresh = set() if full or smart else s.fresh_boards(float(c.get("refresh_hours", 0) or 0))
            entries = boards_due(c, s, fresh, full, smart=smart)
            crawl = start_crawl(c, s, "web", fresh, entries)
            checked = 0
            # One click walks every company in batches (scan_batch, default 50):
            # each batch is fetched, filtered, saved and scored before the next
            # starts on its own, so results show up batch by batch.
            size = int(c.get("scan_batch", 50) or 50)
            batches = [entries[i:i + size] for i in range(0, len(entries), size)] or [[]]
            scoring = c.get("llm", {}).get("provider", "none") != "none"
            scorer = prof = None
            done = 0
            for k, chunk in enumerate(batches, 1):
                first = (k - 1) * size + 1
                emit("batch", index=k, count=len(batches), companies=len(chunk),
                     first=first, last=first + len(chunk) - 1, total=len(entries))
                all_jobs, healthy, counts = [], [], {}
                with ThreadPoolExecutor(max_workers=int(c.get("fetch_concurrency", 10))) as pool:
                    for entry, (jobs, err) in zip(chunk, pool.map(sources.fetch_company, chunk)):
                        if not err:
                            healthy.append(entry["name"])
                            counts[entry["name"]] = len(jobs)
                            all_jobs.extend(jobs)
                        emit("fetch", company=entry["name"], count=len(jobs), error=err or "")
                disc = c.get("discovery", {})
                if k == 1 and disc.get("enabled"):
                    jobs, _ = sources.discover(disc.get("queries", []), disc.get("max_per_query", 40))
                    all_jobs.extend(jobs)
                    emit("fetch", company="discovery", count=len(jobs), error="")

                kept = select_jobs(all_jobs, c, s, days)
                desc = {j["id"]: j["description"] for j in kept}
                new = s.upsert(kept)
                closed = s.mark_closed(healthy)
                s.expire_old(float(c.get("expire_after_days", 45) or 0))
                for name in healthy:
                    s.mark_fetched(name, counts[name])
                s.link_applications()
                s.add_to_crawl(crawl, ok=len(healthy), failed=len(chunk) - len(healthy),
                               new_jobs=len(new), closed_jobs=len(closed))
                checked += len(chunk)
                emit("fetched", new=len(new), closed=len(closed), total=len(kept),
                     skipped=len(fresh), batch=k, batches=len(batches),
                     checked=checked, due=len(entries))

                if not scoring:
                    continue
                if scorer is None:
                    scorer = get_scorer(c)
                    prof = get_profile(c, scorer)
                rows = s.unscored(limit)
                if days:
                    since = time.time() - days * 86400
                    rows = [r for r in rows if (r["posted_at"] or 0) >= since]
                emit("scoring", count=len(rows), backlog=s.unscored_count(), batch=k,
                     batches=len(batches))

                # A batched backend (Cursor) judges many postings per agent run, so
                # chunk; a per-job backend gets chunks of one and behaves as before.
                batched = hasattr(scorer, "score_batch")
                per = int(c.get("llm", {}).get("batch_size", getattr(scorer, "batch_size", 25)))
                chunks = ([rows[i:i + per] for i in range(0, len(rows), per)]
                          if batched else [[r] for r in rows])
                for group in chunks:
                    payload = []
                    for row in group:
                        job = dict(row)
                        job["description"] = desc.get(row["id"]) or row["description"] or ""
                        payload.append(job)
                    emit("looking", id=group[0]["id"], company=group[0]["company"],
                         title=(f"scoring {len(group)} jobs" if len(group) > 1
                                else group[0]["title"]),
                         domain=group[0]["domain"] or "")
                    try:
                        if batched:
                            results = scorer.score_batch(prof, payload)
                        else:
                            results = {group[0]["id"]: scorer.score(prof, payload[0])}
                    except (RateLimited, CursorError) as e:
                        # Out of quota: keep fetching the remaining batches so
                        # every job is found; they get scored on a later run.
                        emit("throttled", message=str(e), scored=done)
                        scoring = False
                        break
                    for row in group:
                        if row["id"] not in results:
                            continue            # dropped by the model: retried next run
                        score, reason, breakdown = results[row["id"]]
                        # Same code-enforced gates as the CLI scan.
                        score, reason = apply_cap(score, row["title"], reason)
                        score, reason = apply_relevance_cap(score, breakdown, reason)
                        s.save_score(row["id"], score, reason, breakdown)
                        done += 1
                        emit("scored", id=row["id"], score=score, reason=reason,
                             breakdown=breakdown, company=row["company"], title=row["title"],
                             domain=row["domain"] or "", url=row["url"] or "")

            emit("done", scored=done, elapsed=round(time.time() - started, 1))
        except Exception as e:  # noqa: BLE001 - always close the stream cleanly
            emit("error", message=f"{type(e).__name__}: {e}"[:300])
        finally:
            if crawl is not None:
                s.finish_crawl(crawl)
            with _lock:
                _state["scanning"] = False
            q.put(None)

    with _lock:
        if _state["scanning"]:
            yield f"data: {json.dumps({'type': 'error', 'message': 'a scan is already running'})}\n\n"
            return
        _state["scanning"] = True
        _state["started"] = started
        _state["days"] = days            # the page's time range, locked until done

    threading.Thread(target=work, daemon=True).start()
    while True:
        item = q.get()
        if item is None:
            break
        yield item


@app.get("/api/search")
def search(q: str = "", limit: int = 4000):
    """Full-text search across title, company, location and description.

    Descriptions are far too large to ship to the browser (thousands of jobs
    x several KB), so matching happens in SQLite and only the ids come back.
    Every word must match somewhere, so extra words narrow the result.
    """
    words = [w for w in q.lower().split() if w][:6]
    if not words:
        return {"ids": None}          # null means "no query", not "no matches"
    s = store()
    clauses = " AND ".join(
        "(LOWER(title) LIKE ? OR LOWER(company) LIKE ? OR LOWER(location) LIKE ?"
        " OR LOWER(COALESCE(description,'')) LIKE ?)" for _ in words)
    params: list[str] = []
    for w in words:
        params.extend([f"%{w}%"] * 4)
    rows = s.db.execute(
        f"SELECT id FROM jobs WHERE closed_at IS NULL AND {clauses} LIMIT ?",
        (*params, limit),
    ).fetchall()
    return {"ids": [r["id"] for r in rows]}


@app.get("/api/scan")
def scan(limit: int = 120, full: bool = False, days: int = 0, smart: bool = False):
    return StreamingResponse(
        _scan_events(limit, full, days, smart),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
