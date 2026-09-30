"""jobradar CLI.

  scan     fetch every source, diff against the DB, score new jobs
  digest   email everything not yet reported (this is what cron runs weekly)
  run      scan + digest in one shot
  list     print recent finds in the terminal
  test     send a sample email to prove SMTP works
"""
from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
import sys
import time
from pathlib import Path

import yaml

from . import alerts as alerts_mod
from . import digest as digest_mod
from . import sources
from .budget import Budget
from .cursor import CursorError
from .llm import RateLimited, make_scorer
from .experience import experience_label, too_senior
from .localmatch import LocalScorer
from .resume import build_profile, extract_text
from .relevance import apply_cap as apply_relevance_cap
from .roles import classify
from .seniority import apply_cap, is_senior
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
PROFILE_CACHE = ROOT / ".resume_profile.json"


def load_config(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"No config at {path}. Copy config.example.yaml to config.yaml and edit it.")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    # The company list lives in the `companies` table of jobradar.db (see
    # companies.py); companies.json is its readable export. Entries written in
    # config.yaml itself still work and come first. On first run the table is
    # seeded from companies_file, so an existing JSON list is picked up.
    from .companies import config_companies, import_file
    store = Store(path.parent / "jobradar.db")
    seed = cfg.get("companies_file")
    if seed and not store.companies():
        jpath = Path(seed) if Path(seed).is_absolute() else path.parent / seed
        if jpath.exists():
            import_file(jpath, store)
    listed = cfg.get("companies") or []
    names = {c["name"].lower() for c in listed}
    for c in config_companies(store):          # enabled, readable boards only
        if c["name"].lower() not in names:
            listed.append(c)
            names.add(c["name"].lower())
    cfg["companies"] = listed
    store.db.close()
    return cfg


def get_scorer(cfg: dict):
    spec = cfg.get("llm", {})
    provider = spec.get("provider", "none")
    if provider == "local":
        # The keyword matcher works off the resume itself, not a model name.
        path = Path(cfg["resume_path"])
        return make_scorer("local", extract_text(path if path.is_absolute()
                                                 else ROOT / path))
    return make_scorer(provider, spec.get("model", ""), spec.get("model_params"))


def get_profile(cfg: dict, scorer, force: bool = False) -> str:
    """The resume profile plus config's candidate_notes - what you are looking
    for, which a resume does not say. Notes go first so the 1,400-character
    prompt cut can never drop them."""
    notes = (cfg.get("candidate_notes") or "").strip()
    base = _resume_profile(cfg, scorer, force)
    return f"TARGET: {notes}\n{base}" if notes else base


def _resume_profile(cfg: dict, scorer, force: bool = False) -> str:
    resume_path = ROOT / cfg["resume_path"] if not Path(cfg["resume_path"]).is_absolute() \
        else Path(cfg["resume_path"])
    stamp = resume_path.stat().st_mtime if resume_path.exists() else 0
    summarizer = scorer if (scorer is not None and hasattr(scorer, "complete")) else None
    # The cache key includes whether a model wrote it. Without this, a raw-text
    # profile written during a `provider: none` run gets reused by a later
    # model run - and raw text truncates mid-resume, hiding the experience
    # section that says how senior the candidate actually is.
    kind = "model" if summarizer else "raw"
    if PROFILE_CACHE.exists() and not force:
        cached = json.loads(PROFILE_CACHE.read_text())
        if cached.get("mtime") == stamp and cached.get("kind") == kind:
            return cached["profile"]
    text = extract_text(resume_path)
    try:
        profile = build_profile(text, summarizer)
    except Exception:  # noqa: BLE001
        # Out of quota or provider down. A summary is a nicety; the run's job
        # is finding openings. Fall back to the resume text and do not cache
        # it, so a proper summary is built once the provider recovers.
        return build_profile(text, None)
    PROFILE_CACHE.write_text(json.dumps({"mtime": stamp, "kind": kind, "profile": profile}))
    return profile


def select_jobs(all_jobs: list[dict], cfg: dict, store: Store) -> list[dict]:
    """Apply the filters, reading full descriptions where a board lists none.

    Cheap checks (title, location) run first. Then every NEW survivor whose
    board gave no description - Workday and Oracle list endpoints never do -
    has its detail page read, so the experience gate sees "3+ years" instead
    of nothing. Jobs already stored were vetted when first seen, so a repeat
    run only pays for postings that are actually new."""
    f = cfg.get("filters", {})
    cheap = [j for j in all_jobs if passes_filters(j, f, check_experience=False)]
    if f.get("max_required_years") is not None:
        known = store.known_ids()
        need = [j for j in cheap if j["id"] not in known and j.get("source") in sources.DETAILS
                and len(j.get("description") or "") < 300]
        if need:
            with ThreadPoolExecutor(max_workers=int(cfg.get("fetch_concurrency", 10))) as pool:
                for job, text in zip(need, pool.map(sources.fetch_details, need)):
                    if text:
                        job["description"] = text
    kept = [j for j in cheap if not too_senior(j, f.get("max_required_years"))]
    for j in kept:
        j["experience"] = experience_label(j["title"], j.get("description") or "")
        j.update(classify(j["title"], f.get("roles")) or {})
    return kept


def is_blocked(company: str, blocked: list[str] | None) -> bool:
    """Whole-word match, so "ibm" blocks "IBM India" but not "Ibmx Labs"."""
    name = (company or "").lower()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(b.lower())}(?![a-z0-9])", name)
               for b in blocked or [])


def passes_filters(job: dict, f: dict, check_experience: bool = True) -> bool:
    if is_blocked(job.get("company", ""), f.get("blocked_companies")):
        return False
    title = job["title"].lower()
    # Configured roles replace the plain title_include list.
    if f.get("roles") and classify(job["title"], f["roles"]) is None:
        return False
    inc = [] if f.get("roles") else [s.lower() for s in f.get("title_include") or []]
    exc = [s.lower() for s in f.get("title_exclude") or []]
    locs = [s.lower() for s in f.get("locations") or []]
    if inc and not any(s in title for s in inc):
        return False
    if any(s in title for s in exc):
        return False
    # Whole words: "india" must not match "Indianapolis" or "Indiana".
    if locs and not any(re.search(rf"\b{re.escape(s)}\b", job["location"].lower()) for s in locs):
        return False
    # A senior title (Staff, Principal, VP, Lead...) is an experience bar too;
    # seniority.is_senior matches whole words, so "VP-" and "Staff Software
    # Engineer" are caught where the substring list above misses them.
    # "Member of Technical Staff" is an entry title at many companies, not Staff.
    plain_title = re.sub(r"member of technical staff", "", job["title"], flags=re.I)
    if f.get("max_required_years") is not None and is_senior(plain_title):
        return False
    # Read from the full fetched description, before the store trims it.
    if check_experience and too_senior(job, f.get("max_required_years")):
        return False
    return True


# ---------------------------------------------------------------------------

def boards_due(cfg: dict, store: Store, fresh: set[str], full: bool = False) -> list[dict]:
    """Boards to read this run: not fresh, stalest first, at most boards_per_run.
    With a long company list this spreads the reading over several runs
    instead of every board coming due in the same one."""
    entries = [e for e in cfg.get("companies", [])
               if e["name"] not in fresh and not is_blocked(e["name"], cfg.get("filters", {})
                                                            .get("blocked_companies"))]
    cap = int(cfg.get("boards_per_run", 0) or 0)
    if full or not cap or len(entries) <= cap:
        return entries
    ages = store.board_ages()
    return sorted(entries, key=lambda e: ages.get(e["name"], 0))[:cap]


def start_crawl(cfg: dict, store: Store, source: str, fresh: set[str],
                entries: list[dict]) -> int:
    """Open this crawl's row in the crawl history the web UI shows."""
    listed = {e["name"] for e in cfg.get("companies", [])}
    return store.start_crawl(source, readable=len(listed), skipped=len(listed & fresh),
                             due=len(entries))


def cmd_scan_batched(cfg: dict, store: Store, size: int, full: bool = False,
                     quiet: bool = False) -> dict:
    """Scan in batches of `size` companies: each batch is fetched, filtered,
    saved and scored before the next starts, so new jobs appear in the web UI
    batch by batch instead of all at the end of a long scan."""
    fresh = set() if full else store.fresh_boards(float(cfg.get("refresh_hours", 0) or 0))
    entries = boards_due(cfg, store, fresh, full)
    batches = [entries[i:i + size] for i in range(0, len(entries), size)] or [[]]
    total = {"new": 0, "closed": 0, "errors": []}
    crawl = start_crawl(cfg, store, "scan", fresh, entries)
    try:
        for k, chunk in enumerate(batches, 1):
            if not quiet:
                print(f"\n=== batch {k}/{len(batches)}: {len(chunk)} companies "
                      f"({chunk[0]['name'] if chunk else '-'} ... "
                      f"{chunk[-1]['name'] if chunk else '-'})", flush=True)
            got = cmd_scan(cfg, store, quiet=quiet, full=full, entries=chunk,
                           discovery=(k == 1), crawl_id=crawl)
            for key in ("new", "closed"):
                total[key] += got[key]
            total["errors"] += got["errors"]
    finally:
        store.finish_crawl(crawl)
    if not quiet:
        print(f"\nall batches: {total['new']} new jobs, {total['closed']} closed, "
              f"{len(total['errors'])} errors", flush=True)
    return total


def cmd_scan(cfg: dict, store: Store, quiet: bool = False, full: bool = False,
             entries: list[dict] | None = None, discovery: bool = True,
             crawl_id: int | None = None, source: str = "scan") -> dict:
    """Read the due boards, save what passes the filters and score it. Called
    without `entries` it is a whole crawl and records its own crawl row;
    cmd_scan_batched passes each batch plus the crawl it belongs to."""
    all_jobs: list[dict] = []
    errors: list[str] = []
    healthy_companies: list[str] = []

    # A board read within refresh_hours is skipped: postings rarely change by
    # the hour, and skipping leaves its jobs untouched (a skipped board is not
    # "healthy" this run, so nothing of it is marked closed). --full reads all.
    fresh = set()
    if entries is None:
        fresh = set() if full else store.fresh_boards(float(cfg.get("refresh_hours", 0) or 0))
        entries = boards_due(cfg, store, fresh, full)
    own_crawl = crawl_id is None
    if own_crawl:
        crawl_id = start_crawl(cfg, store, source, fresh, entries)
    if fresh and not quiet:
        print(f"  skipping {len(fresh)} boards read in the last {cfg.get('refresh_hours')}h "
              f"(scan --full to read them anyway)", flush=True)

    # Boards are independent and fetching is network-bound, so fetch them in
    # parallel. map() keeps config order for the printout; fetch_company never
    # raises, so one dead board cannot take down the pool.
    with ThreadPoolExecutor(max_workers=int(cfg.get("fetch_concurrency", 10))) as pool:
        fetched = list(pool.map(sources.fetch_company, entries))

    for entry, (jobs, err) in zip(entries, fetched):
        if err:
            errors.append(f"{entry['name']}: {err}")
        else:
            healthy_companies.append(entry["name"])
            all_jobs.extend(jobs)
        if not quiet:
            print(f"  {entry['name']:<20} {len(jobs):>4} open" + (f"  ⚠ {err}" if err else ""),
                  flush=True)

    disc = cfg.get("discovery", {})
    if discovery and disc.get("enabled"):
        jobs, errs = sources.discover(disc.get("queries", []), disc.get("max_per_query", 40))
        all_jobs.extend(jobs)
        errors.extend(errs)
        if not quiet:
            print(f"  {'discovery':<20} {len(jobs):>4} found")

    kept = select_jobs(all_jobs, cfg, store)
    # keep descriptions around for scoring, keyed by id
    desc = {j["id"]: j["description"] for j in kept}

    new = store.upsert(kept)
    closed = store.mark_closed(healthy_companies)
    counts = {e["name"]: len(j) for e, (j, err) in zip(entries, fetched) if not err}
    for name in healthy_companies:
        store.mark_fetched(name, counts.get(name, 0))
    store.add_to_crawl(crawl_id, ok=len(healthy_companies),
                       failed=len(entries) - len(healthy_companies),
                       new_jobs=len(new), closed_jobs=len(closed))
    if own_crawl:
        store.finish_crawl(crawl_id)
    # A job you applied to before Job Radar tracked it is recognised now.
    store.link_applications()

    # --- score the new arrivals -------------------------------------------
    llm_cfg = cfg.get("llm", {})
    scored = 0
    # Gate on there being unscored work, NOT on this run finding new jobs: a run
    # that was rate-limited leaves a backlog, and the next run must clear it even
    # if no new postings appeared.
    if llm_cfg.get("provider", "none") != "none":
        rows = []
        scorer = None
        profile = ""
        pending = store.unscored(llm_cfg.get("max_to_score", 5000))
        if pending:
            try:
                scorer = get_scorer(cfg)
                profile = get_profile(cfg, scorer)
            except Exception as e:  # noqa: BLE001
                # No scoring available. New and closed openings are still
                # detected and reported; they simply arrive unranked.
                errors.append(f"scoring unavailable: {type(e).__name__}")
                if not quiet:
                    print(f"  scoring unavailable ({type(e).__name__}); "
                          f"reporting openings unranked", flush=True)
                scorer = None
                pending = []

            # --- stage 1: free triage ---------------------------------------
            # A posting with no overlapping skills and a senior title is not a
            # judgement call. Settle those locally and spend the token budget
            # on the ones where the model's reading actually changes the
            # answer. Roughly halves what reaches the API.
            floor = int(llm_cfg.get("triage_floor", 3))
            if llm_cfg.get("triage", True) and not isinstance(scorer, LocalScorer):
                local = LocalScorer(extract_text(ROOT / cfg["resume_path"]))
                rows, settled, blind = [], 0, 0
                for row in pending:
                    job = dict(row)
                    job["description"] = desc.get(row["id"]) or row["description"] or ""
                    # Triage judges skill overlap, and a title alone rarely
                    # names a stack. Without a description it cannot tell
                    # "Software Engineer, Model Runtime" from "Head of Sales",
                    # so those go to the model rather than being guessed at.
                    if len(job["description"]) < 120:
                        rows.append(row)
                        blind += 1
                        continue
                    s_local, why, bd = local.score(profile, job)
                    if s_local >= floor:
                        rows.append(row)
                    else:
                        store.save_score(row["id"], s_local, f"triage: {why}", bd)
                        settled += 1
                if not quiet:
                    note = f", {blind} had no description to judge" if blind else ""
                    print(f"  triage settled {settled} locally, "
                          f"{len(rows)} go to the model{note}", flush=True)
            else:
                rows = pending

            # --- stage 2: stay inside the daily budget ----------------------
            budget = Budget(store.db, int(llm_cfg.get("daily_token_limit", 500_000)))
            size = int(llm_cfg.get("batch_size", 25))
            per_batch = int(llm_cfg.get("tokens_per_batch", 3400))
            affordable = max(0, budget.remaining() // per_batch) * size
            if len(rows) > affordable:
                if not quiet:
                    print(f"  budget: {budget.report()} -> planning {affordable} "
                          f"of {len(rows)} this run", flush=True)
                rows = rows[:affordable]
            elif not quiet:
                print(f"  budget: {budget.report()}", flush=True)

        # Scoring is network-bound and each job is independent, so run a small
        # pool. Kept modest on purpose: free tiers rate-limit, and the client
        # already backs off on 429. SQLite writes stay on this thread.
        # A batched backend (Cursor) scores many jobs per call, so the thread
        # pool would only multiply expensive round trips. Batch when offered.
        titles = {r["id"]: r["title"] for r in pending}

        def apply(job_id, result):
            nonlocal scored
            score, reason, breakdown = result
            # Two gates, both guaranteed in code rather than asked for in the
            # prompt: a senior role cannot ride a strong skills match, and a
            # wrong-field role cannot ride a strong seniority match.
            score, reason = apply_cap(score, titles.get(job_id, ""), reason)
            score, reason = apply_relevance_cap(score, breakdown, reason)
            store.save_score(job_id, score, reason, breakdown)
            scored += 1

        try:
            if hasattr(scorer, "score_batch"):
                size = int(llm_cfg.get("batch_size", getattr(scorer, "batch_size", 25)))

                def run_chunk(chunk):
                    payload = []
                    for row in chunk:
                        job = dict(row)
                        job["description"] = (desc.get(row["id"]) or row["description"] or "")
                        payload.append(job)
                    return scorer.score_batch(profile, payload)

                dropped = []
                spent_before = getattr(scorer, "tokens_used", 0)
                for start in range(0, len(rows), size):
                    # The upfront plan uses an estimate; this is the real gate.
                    # Checked before every request, so a batch that costs more
                    # than expected can never carry the run past the cap.
                    if not budget.can_afford(per_batch):
                        if not quiet:
                            print(f"    budget reached - {budget.report()}; "
                                  f"{len(rows) - start} jobs wait for the next run",
                                  flush=True)
                        break
                    chunk = rows[start:start + size]
                    try:
                        results = run_chunk(chunk)
                    except (RateLimited, CursorError):
                        raise            # provider-level: stop, keep what we have
                    except Exception as e:  # noqa: BLE001
                        # A single unusable reply must not end the run. Those
                        # jobs stay unscored and are retried below.
                        if not quiet:
                            print(f"    batch failed ({type(e).__name__}), "
                                  f"deferring {len(chunk)}", flush=True)
                        dropped.extend(chunk)
                        continue
                    finally:
                        # Record actual spend immediately, so the next check
                        # sees the true figure rather than the estimate.
                        now_used = getattr(scorer, "tokens_used", 0)
                        if now_used > spent_before:
                            budget.record(now_used - spent_before)
                            spent_before = now_used
                    for row in chunk:
                        got = results.get(row["id"])
                        if got is None:
                            # The model skipped this row. Collect it rather than
                            # writing a zero, and retry in smaller groups after.
                            dropped.append(row)
                        else:
                            apply(row["id"], got)
                    if not quiet:
                        print(f"    scored {scored}/{len(rows)}"
                              + (f" ({len(dropped)} dropped)" if dropped else ""), flush=True)

                # Models reliably drop a row or two from a long array. One retry
                # in smaller groups recovers nearly all of them; anything still
                # missing stays unscored and is picked up by the next run.
                if dropped:
                    if not quiet:
                        print(f"    retrying {len(dropped)} dropped", flush=True)
                    for start in range(0, len(dropped), 8):
                        if not budget.can_afford(per_batch):
                            break
                        chunk = dropped[start:start + 8]
                        try:
                            results = run_chunk(chunk)
                        except Exception:  # noqa: BLE001 - retry is best effort
                            break
                        finally:
                            now_used = getattr(scorer, "tokens_used", 0)
                            if now_used > spent_before:
                                budget.record(now_used - spent_before)
                                spent_before = now_used
                        for row in chunk:
                            got = results.get(row["id"])
                            if got is not None:
                                apply(row["id"], got)
            else:
                workers = int(llm_cfg.get("concurrency", 3))

                def work(row):
                    job = dict(row)
                    job["description"] = (desc.get(row["id"]) or row["description"] or "")
                    return row, scorer.score(profile, job)

                with ThreadPoolExecutor(max_workers=workers) as pool:
                    for row, result in pool.map(work, rows):
                        apply(row["id"], result)
                        if not quiet:
                            print(f"    scored {result[0]}/10  {row['title'][:55]}", flush=True)
        except (RateLimited, CursorError) as e:
            # Not fatal. Scores already written are committed, and whatever was
            # not reached stays unscored so the next run picks it up.
            errors.append(f"scoring stopped early: {e}")
            if not quiet:
                print(f"    ! {e} - {scored} scored, rest will wait for the next run",
                      flush=True)

    store.log_run(len(new), len(closed), errors)
    if not quiet:
        cost = ""
        if scored and scorer is not None and hasattr(scorer, "tokens_used"):
            cost = f" · {scorer.tokens_used:,} tokens"
        elif scored and scorer is not None and hasattr(scorer, "cost_usd"):
            cost = f" · ${scorer.cost_usd():.4f}"
        print(f"\n{len(new)} new · {len(closed)} closed · {scored} scored · "
              f"{len(errors)} errors{cost}")
    return {"new": len(new), "closed": len(closed), "errors": errors}


def cmd_digest(cfg: dict, store: Store, days: int, dry_run: bool,
               new_only: bool = False) -> None:
    llm_cfg = cfg.get("llm", {})
    min_score = llm_cfg.get("min_score", 0)
    scoring_on = llm_cfg.get("provider", "none") not in ("none", None, "")
    pending = store.unnotified(min_score, require_scored=scoring_on)
    # Frequent runs (every couple of hours) mail only new openings: repeating
    # the week's closures each time would send the same email all day.
    closed = [] if new_only else store.closed_since(days * 86400)

    # Cap what one email shows. The first run after a fresh scan has the entire
    # board pending - thousands of jobs - and mailing all of them is useless.
    # Everything pending is still marked notified, so this run establishes the
    # baseline and later emails carry only genuinely new postings.
    cap = int(cfg.get("digest", {}).get("max_jobs", 60))
    new = pending[:cap]
    overflow = len(pending) - len(new)
    label = time.strftime("Update %d %b %Y, %H:%M" if new_only else "Week of %d %b %Y")
    notes = []
    if overflow > 0:
        notes.append(f"Showing the top {len(new)} of {len(pending)} tracked openings; "
                     f"{overflow} more are on file. Later emails carry only new ones.")
    html, text = digest_mod.render(new, closed, label, notes)

    if dry_run:
        _ = notes
        out = ROOT / "digest_preview.html"
        out.write_text(html)
        print(text)
        print(f"\nPreview written to {out}")
        return
    if not pending and not closed:
        print("Nothing to report; no email sent.")
        return

    count = len(pending)
    subject = f"Job Radar — {count} new opening{'s' if count != 1 else ''}"
    digest_mod.send(cfg["email"], subject, html, text)
    store.mark_notified([r["id"] for r in pending])   # all of them, not just the shown ones
    print(f"Sent to {cfg['email']['to']}: {len(new)} shown of {count} new, "
          f"{len(closed)} closed.")


def cmd_alert(cfg: dict, store: Store, dry_run: bool) -> None:
    """Check only the prioritised companies, then mail anything new at once.

    This runs daily, so it deliberately does NOT do a full scan: it fetches
    just the watched boards and skips aggregator discovery. A dozen companies
    is seconds of network and a handful of scoring batches, against minutes and
    ~130 batches for the whole board.
    """
    watch = alerts_mod.watched_names(cfg)
    narrow = dict(cfg)
    narrow["companies"] = [e for e in cfg.get("companies", []) if e["name"] in watch]
    narrow["discovery"] = {"enabled": False}
    cmd_scan(narrow, store, quiet=True, source="alert")
    baselined = store.baselined_companies()
    pending = store.unalerted()
    rows = alerts_mod.pick(pending, cfg, baselined)
    fresh = {r["company"] for r in pending} - baselined
    if fresh and not dry_run:
        print(f"Baselining {len(fresh)} new compan(y/ies): "
              f"{', '.join(sorted(fresh))[:100]} — alerts start from their next opening.")
    if not rows:
        print("Nothing new at watched companies.")
        # Everything seen this run becomes the new baseline either way, so a
        # job that was below threshold is not re-examined forever.
        store.mark_alerted([r["id"] for r in store.unalerted()])
        return
    cap = int(cfg.get("alerts", {}).get("max_per_alert", 15))
    label = time.strftime("%d %b %Y, %H:%M")
    if dry_run:
        html, text = alerts_mod.render(rows[:cap], label)
        Path(ROOT / "alert_preview.html").write_text(html)
        print(text or "(nothing)")
        print(f"\n{len(rows)} would alert; preview at alert_preview.html")
        return
    alerts_mod.send(cfg, rows[:cap], label)
    store.mark_alerted([r["id"] for r in store.unalerted()])
    print(f"Alerted {len(rows)} new opening(s) at watched companies.")


def cmd_recap(store: Store, dry_run: bool) -> None:
    """Re-apply the seniority and relevance caps to existing scores.

    Costs nothing: both are arithmetic on data already stored, so they need no
    model. Use after changing a threshold, or to repair scores written before
    a gate existed.
    """
    rows = store.db.execute(
        "SELECT id, title, score, reason, breakdown FROM jobs WHERE score IS NOT NULL"
    ).fetchall()
    changed = []
    for r in rows:
        try:
            bd = json.loads(r["breakdown"]) if r["breakdown"] else {}
        except (TypeError, ValueError):
            bd = {}
        new_score, new_reason = apply_cap(r["score"], r["title"], r["reason"] or "")
        new_score, new_reason = apply_relevance_cap(new_score, bd, new_reason)
        if new_score != r["score"]:
            changed.append((r["id"], new_score, new_reason, r["title"], r["score"]))
    if dry_run:
        for _, ns, _, title, old in changed[:15]:
            print(f"  {old} -> {ns}  {title[:60]}")
        print(f"\n{len(changed)} of {len(rows)} scores would change (0 tokens)")
        return
    for jid, ns, nr, _, _ in changed:
        store.db.execute("UPDATE jobs SET score = ?, reason = ? WHERE id = ?", (ns, nr, jid))
    store.db.commit()
    print(f"Capped {len(changed)} jobs (senior title or wrong field). No tokens used.")


def cmd_list(store: Store, days: int) -> None:
    rows = store.since(days * 86400)
    if not rows:
        print("Nothing new.")
        return
    for r in rows:
        s = f"{r['score']}/10" if r["score"] is not None else "  - "
        print(f"[{s}] {r['title'][:60]:<60} {r['company'][:18]:<18} {r['url']}")


def cmd_recheck(cfg: dict, store: Store) -> None:
    """Re-apply the current filters to every open job, re-reading pages where
    the board lists no description. For after a rule change: jobs that no
    longer qualify are closed (hidden), the rest get a fresh experience label.
    Local only - no tokens."""
    f = cfg.get("filters", {})
    rows = [dict(r) for r in store.db.execute("SELECT * FROM jobs WHERE closed_at IS NULL")]
    need = [j for j in rows if j["source"] in sources.DETAILS]
    with ThreadPoolExecutor(max_workers=int(cfg.get("fetch_concurrency", 10))) as pool:
        for job, text in zip(need, pool.map(sources.fetch_details, need)):
            if text:
                job["description"] = text
    now = time.time()
    closed = 0
    for j in rows:
        if passes_filters(j, f):
            role = classify(j["title"], f.get("roles")) or {}
            store.db.execute(
                "UPDATE jobs SET experience = ?, role = ?, role_rank = ?, role_bonus = ? "
                "WHERE id = ?",
                (experience_label(j["title"], j["description"] or ""), role.get("role"),
                 role.get("role_rank"), role.get("role_bonus", 0), j["id"]))
        else:
            store.db.execute("UPDATE jobs SET closed_at = ? WHERE id = ?", (now, j["id"]))
            closed += 1
    store.db.commit()
    print(f"Rechecked {len(rows)} open jobs: {closed} no longer match and were hidden, "
          f"{len(rows) - closed} kept.")


def cmd_companies(store: Store) -> None:
    from collections import Counter
    rows = store.companies()
    scanned = [c for c in rows if c["enabled"] and c["board"] != "other"]
    print(f"{len(rows)} companies on file, {len(scanned)} scanned.")
    print("  by board:  " + ", ".join(f"{b} {n}" for b, n in
                                      Counter(c["board"] for c in rows).most_common()))
    print("  by source: " + ", ".join(f"{s} {n}" for s, n in
                                      Counter(c["source"] or "?" for c in rows).most_common()))
    off = [c for c in rows if not c["enabled"] and c["board"] != "other"]
    if off:
        print(f"  {len(off)} on a readable board but disabled (board not found when checked)")


def cmd_applied(store: Store, status: str = "") -> None:
    rows = [r for r in store.applications() if not status or r["status"] == status]
    if not rows:
        print("No applications recorded.")
        return
    for r in rows:
        when = time.strftime("%Y-%m-%d", time.localtime(r["applied_at"])) if r["applied_at"] \
            else "    -     "
        print(f"{when}  {r['status']:<11} {r['company'][:22]:<22} {r['title'][:45]:<45} {r['url']}")
    print(f"\n{len(rows)} applications")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="jobradar")
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    sub = p.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scan")
    sc.add_argument("--full", action="store_true", help="read every board, even fresh ones")
    sc.add_argument("--batch", type=int, default=0,
                    help="scan N companies at a time; each batch is saved and scored before the next")
    ia = sub.add_parser("import-applied", help="import an .xlsx/.csv application tracker")
    ia.add_argument("path")
    sub.add_parser("recheck", help="re-apply filters to stored jobs (no tokens)")
    ic = sub.add_parser("import-companies",
                        help="add companies from .json / .txt (Name|board|slug|url) / .xlsx")
    ic.add_argument("path")
    sub.add_parser("export-companies", help="write the company table to companies.json")
    sub.add_parser("companies", help="summary of the company table")
    sub.add_parser("skills", help="your resume skills, most done first, and what MERN / "
                                  "Python jobs ask for that you should build next")
    cc = sub.add_parser("check-companies",
                        help="check every board once: India job counts, disable missing boards")
    cc.add_argument("--new", action="store_true", help="only companies never checked")
    fb = sub.add_parser("find-boards",
                        help="find the job board of each company in a file (Name, Name|domain "
                             "or Name|board|slug|url) and add it; --disabled re-finds boards "
                             "that have gone")
    fb.add_argument("path", nargs="?")
    fb.add_argument("--disabled", action="store_true",
                    help="search again for companies whose board is gone or unreadable")
    fb.add_argument("--workers", type=int, default=24, help="companies searched at once")
    ap =sub.add_parser("applied", help="list your application history")
    ap.add_argument("--status", default="", help="only this status, e.g. applied")
    d = sub.add_parser("digest"); d.add_argument("--days", type=int, default=7)
    d.add_argument("--new-only", action="store_true", help="email only new openings")
    d.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("run"); r.add_argument("--days", type=int, default=7)
    r.add_argument("--new-only", action="store_true", help="email only new openings")
    r.add_argument("--dry-run", action="store_true")
    l = sub.add_parser("list"); l.add_argument("--days", type=int, default=7)
    a = sub.add_parser("alert"); a.add_argument("--dry-run", action="store_true")
    rc = sub.add_parser("recap"); rc.add_argument("--dry-run", action="store_true")
    sub.add_parser("test")
    args = p.parse_args(argv)

    cfg = load_config(Path(args.config))
    store = Store(ROOT / "jobradar.db")

    if args.cmd == "scan":
        size = args.batch or int(cfg.get("scan_batch", 0) or 0)
        if size:
            cmd_scan_batched(cfg, store, size, full=args.full)
        else:
            cmd_scan(cfg, store, full=args.full)
    elif args.cmd == "import-applied":
        from .applied import import_file
        got = import_file(Path(args.path), store)
        print(f"Imported {got['added']} applications ({got['skipped']} already there) "
              f"from sheets: {', '.join(got['sheets']) or 'none with a Company column'}.")
    elif args.cmd == "import-companies":
        from .companies import import_file
        got = import_file(Path(args.path), store)
        n = store.export_companies(ROOT / "companies.json")
        print(f"{got['added']} added, {got['updated']} updated, {got['unchanged']} unchanged. "
              f"companies.json rewritten ({n} companies).")
    elif args.cmd == "export-companies":
        print(f"companies.json written ({store.export_companies(ROOT / 'companies.json')} companies).")
    elif args.cmd == "companies":
        cmd_companies(store)
    elif args.cmd == "skills":
        from .skill import print_report, report_for
        print_report(report_for(ROOT, cfg, store))
    elif args.cmd == "check-companies":
        from .companies import check_all
        got = check_all(store, workers=int(cfg.get("fetch_concurrency", 10)), only_unchecked=args.new)
        n = store.export_companies(ROOT / "companies.json")
        print(f"checked: {got['ok']} ok, {got['gone']} not found (disabled), "
              f"{got['error']} unreachable (left as they were). companies.json rewritten ({n}).")
        cmd_companies(store)
    elif args.cmd == "find-boards":
        from . import discover
        if not args.path and not args.disabled:
            p.error("find-boards needs a file or --disabled")
        got = (discover.run_file(Path(args.path), store, args.workers) if args.path
               else discover.run_disabled(store, args.workers))
        missing = got.pop("missing")
        if missing:
            out = Path(args.path).with_suffix(".missing.txt") if args.path \
                else ROOT / "refind.missing.txt"
            out.write_text("\n".join(missing) + "\n", encoding="utf-8")
            print(f"No readable board with India jobs for {len(missing)}; listed in {out}.")
        n = store.export_companies(ROOT / "companies.json")
        print(", ".join(f"{v} {k}" for k, v in got.items()) + f". companies.json rewritten ({n}).")
        cmd_companies(store)
    elif args.cmd == "recheck":
        cmd_recheck(cfg, store)
    elif args.cmd == "applied":
        cmd_applied(store, args.status)
    elif args.cmd == "digest":
        cmd_digest(cfg, store, args.days, args.dry_run, args.new_only)
    elif args.cmd == "run":
        cmd_scan(cfg, store, source="run")
        cmd_digest(cfg, store, args.days, args.dry_run, args.new_only)
    elif args.cmd == "list":
        cmd_list(store, args.days)
    elif args.cmd == "recap":
        cmd_recap(store, args.dry_run)
    elif args.cmd == "alert":
        cmd_alert(cfg, store, args.dry_run)
    elif args.cmd == "test":
        digest_mod.send(cfg["email"], "Job Radar — test",
                        "<p>SMTP works. Your weekly digest will arrive here.</p>",
                        "SMTP works.")
        print(f"Test email sent to {cfg['email']['to']}.")


if __name__ == "__main__":
    main()
