"""Everything one job posting says.

    read(job)        the posting, from the best reader for it: its site's own
                     data (sites/, by address, then by board), else its page
                     (page.py), else - a page drawn by script - the page
                     rendered in a headless browser (browser.py). Never raises.
    apply(job, d)    a reading merged into the job the scan saves
    complete(job)    what the text itself says: work mode, salary, must-have
                     and nice-to-have skills (facts.py)
    needs_page(job)  whether the job list's entry is too thin to judge by
    Report           what one scan read, per board
"""
from __future__ import annotations

import re
import threading
from collections import Counter, defaultdict

from . import browser, page, sites
from .facts import complete, years_label  # noqa: F401 - part of the package's interface
from .model import Details
from .text import to_text

# Boards whose job list carries a title or a summary, not the posting.
SUMMARY_ONLY = {"greenhouse", "workday", "oracle", "smartrecruiters", "bamboohr", "breezy", "jobvite",
                "jazzhr", "freshteam", "icims", "successfactors", "phenom", "page", "darwinbox"}


def needs_page(job: dict) -> bool:
    return (job.get("source") in SUMMARY_ONLY or len(job.get("description") or "") < 300
            or "gh_jid=" in (job.get("url") or "") or not job.get("posted_at"))


def _safe(fn, name: str) -> Details:
    try:
        d = fn()
        d.reader = d.reader or name
        return d
    except Exception as e:  # noqa: BLE001 - one bad posting must not stop a scan
        return Details(reader=name, error=f"{type(e).__name__}: {e}"[:200])


_GONE = re.compile(r"\b(?:404|410)\b")              # the posting is no more
_DEAD = re.compile(r"\b(?:401|403|404|410)\b")       # ... or the page is closed to us


def read(job: dict, report: "Report | None" = None) -> Details:
    url = job.get("url") or ""
    found = sites.find(job)
    d = _safe(lambda: found.read(job), found.name) if found else Details()
    if not d.ok() and url.startswith("http") and not _GONE.search(d.error):
        static = _safe(lambda: page.read(url), "page")
        d = d.fill(static)
        # An empty shell, not a dead link, on a board whose list lacks the
        # posting: worth one render while the scan has any left. A board whose
        # list carries the whole posting (Keka, Lever...) has nothing more to show.
        if (not d.ok() and report is not None and (job.get("source") or "page") in SUMMARY_ONLY
                and not _DEAD.search(static.error) and report.take_render()):
            html = browser.render(url)
            if html:
                shown = _safe(lambda: page.read(url, html=html), "browser")
                shown.rendered = True
                d = d.fill(shown)
    if report is not None:
        report.add(job, d)
    return d


def apply(job: dict, d: Details) -> dict:
    """Merge a reading into the job. The posting's own text replaces a
    thinner list summary; a dated field beats the list's date; the list's
    date beats a date guessed from the page's words."""
    if len(d.text) > len(to_text(job.get("description") or "")):
        job["description"] = d.text
    if d.posted_at:
        job["posted_at"] = d.posted_at
    elif not job.get("posted_at") and d.posted_guess:
        job["posted_at"] = d.posted_guess
    for key in ("first_posted", "closes_at", "years_min", "years_max", "work_mode", "employment_type",
                "salary"):
        value = getattr(d, key)
        if value not in (None, ""):
            job[key] = value
    if d.skills:
        job["board_skills"] = list(dict.fromkeys((job.get("board_skills") or []) + d.skills))
    if d.location and not job.get("location"):
        job["location"] = d.location
    return job


class Report:
    """What one scan read, per board: postings read, how many gave a usable
    text and a date, how many came back thin and why. It also holds the
    scan's allowance of browser renders."""

    def __init__(self, browser_max: int = 0):
        self._lock = threading.Lock()
        self.boards: dict[str, Counter] = defaultdict(Counter)
        self.errors: Counter = Counter()
        self._renders = max(0, int(browser_max or 0))

    def take_render(self) -> bool:
        with self._lock:
            if self._renders <= 0:
                return False
            self._renders -= 1
            return True

    def add(self, job: dict, d: Details) -> None:
        with self._lock:
            c = self.boards[job.get("source") or "?"]
            c["read"] += 1
            c["full"] += d.ok()
            c["dated"] += bool(d.posted_at or d.posted_guess or job.get("posted_at"))
            c["rendered"] += d.rendered
            if not d.ok():
                c["thin"] += 1
                why = d.error.split(":")[0] if d.error else "short text"
                self.errors[f"{job.get('source') or '?'}: {why}"] += 1

    def summary(self) -> dict:
        with self._lock:
            total = sum(self.boards.values(), Counter())
            thin = sorted(((b, c["thin"]) for b, c in self.boards.items() if c["thin"]), key=lambda x: -x[1])
            return {"read": total["read"], "full": total["full"], "dated": total["dated"],
                    "rendered": total["rendered"], "thin": total["thin"], "thin_by_board": thin[:5],
                    "errors": self.errors.most_common(6)}

    def line(self) -> str:
        s = self.summary()
        if not s["read"]:
            return "postings read: none needed"
        out = (f"postings read: {s['read']} · full text {s['full']} ({s['full'] * 100 // s['read']}%) · "
               f"dated {s['dated']}")
        if s["rendered"]:
            out += f" · {s['rendered']} through the browser"
        if s["thin_by_board"]:
            out += " · thin: " + ", ".join(f"{b} {n}" for b, n in s["thin_by_board"])
        return out
