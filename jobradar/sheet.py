"""Copy each job you mark "applied" into your Google Sheet tracker.

The sheet side is a small Apps Script web app (docs/sheet-sync.gs) pasted into
the sheet itself, so no Google Cloud project or key file is needed. Two lines
in .env turn it on:

    JOBRADAR_SHEET_URL=https://script.google.com/macros/s/.../exec
    JOBRADAR_SHEET_TOKEN=<the TOKEN written in the script>

The script writes to one tab only (TAB_ID in the script). A job already in
that tab (same link) has its row updated instead of getting a second row.

A row looks like the ones typed by hand:
    Job role  "Software Engineer  ID: R0138600"   (when the posting has an ID)
    Remark    "ATS 85% | 1+ yrs | JS+React+Node | Bengaluru Hybrid"

    python -m jobradar.sheet      # check the connection, writes nothing
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re

import requests


def _settings() -> tuple[str, str]:
    return ((os.environ.get("JOBRADAR_SHEET_URL") or "").strip(),
            (os.environ.get("JOBRADAR_SHEET_TOKEN") or "").strip())


def enabled() -> bool:
    return all(_settings())


# --- what goes in the row --------------------------------------------------

# "Job ID: 52921", "Req ID: R-1234", "Requisition ID 2027270", "Job Code: 14508"
_REF_TEXT = re.compile(
    r"\b(?:job|req|requisition|job requisition|reference|ref)\.?[ \t]*"
    r"(?:id|no\.?|number|code|#)[ \t]*[:#\-]?[ \t]*([A-Z]{0,4}[-_]?\d[\w\-]{2,20})", re.I)
# Boards whose link carries the ID the posting itself shows. Greenhouse,
# Lever, Ashby... links hold internal codes nobody quotes, so they give none.
_REF_URL = (
    re.compile(r"myworkdayjobs\.com/.*/job/[^?#]*_([A-Za-z]*-?\d[\w\-]*?)(?:/apply)?/?(?:[?#]|$)"),
    re.compile(r"oraclecloud\.com/.*/job/(\d+)"),
    re.compile(r"\.keka\.com/careers/jobdetails/(\d+)"),
)


def job_ref(url: str, description: str = "") -> str:
    """The posting's reference / requisition ID, or ""."""
    m = _REF_TEXT.search(description or "")
    if m:
        return m.group(1).strip("-_")
    for pat in _REF_URL:
        m = pat.search(url or "")
        if m:
            return m.group(1)
    return ""


_SHORT = {"javascript": "JS", "typescript": "TS", "node.js": "Node", "express.js": "Express"}
_COUNTRY = {"india", "in", "ind"}
_MODE = {"hybrid": "Hybrid", "remote": "Remote", "onsite": "WFO", "on-site": "WFO",
         "on site": "WFO", "office": "WFO"}


def _skills(job: dict, app: dict, mine: list[str], limit: int = 4) -> str:
    """Your skills the job names, in its order ("JS+React+Node+Java"); the
    job's own first few when none of yours are there."""
    listed = job.get("skills") or app.get("skills") or ""
    theirs = [s.strip() for s in listed.split(",") if s.strip()]
    if not theirs and job.get("description"):
        from .experience import job_skills   # local: experience imports store
        theirs = job_skills(job.get("title", ""), job["description"])
    yours = {s.lower(): s for s in mine}
    picked = [yours[s.lower()] for s in theirs if s.lower() in yours] or theirs
    return "+".join(_SHORT.get(s.lower(), s) for s in picked[:limit])


def _place(location: str, mode: str) -> str:
    """"India - Bengaluru" + "hybrid" -> "Bengaluru Hybrid"."""
    parts = [p.strip() for p in re.split(r"[,;|/()\-]", location or "") if p.strip()]
    city = next((p for p in parts if p.lower() not in _COUNTRY), parts[0] if parts else "")
    mode = _MODE.get((mode or "").strip().lower(), "")
    if mode.lower() == city.lower():          # "Remote" + remote
        mode = ""
    return " ".join(x for x in (city, mode) if x)


def _experience(job: dict, app: dict) -> str:
    lo, hi = job.get("years_min"), job.get("years_max")
    if lo is not None and hi is not None and hi != lo:
        return f"{lo}-{hi} yrs"
    if lo is not None:
        return "fresher (0 yrs)" if lo == 0 else f"{lo}+ yrs"
    label = job.get("experience") or app.get("experience") or ""
    return "" if label == "not stated" else label


def _ats(job: dict) -> str:
    """The skills part of the match score is the resume-vs-posting fit an ATS
    looks at; the overall score (0-10) when there is no breakdown."""
    try:
        skills = json.loads(job.get("breakdown") or "{}").get("skills")
    except ValueError:
        skills = None
    if skills is not None:
        return f"ATS {round(skills)}%"
    return f"ATS {job['score'] * 10}%" if job.get("score") is not None else ""


def entry(app: dict, job: dict | None, my_skills: list[str]) -> dict:
    """The sheet row for an application, from the posting it came from when
    Job Radar still has it."""
    job = job or {}
    ref = job_ref(app.get("url") or job.get("url", ""), job.get("description", ""))
    title = app.get("title") or ""
    if ref and ref not in title:
        title = f"{title}  ID: {ref}"
    remark = [_ats(job), _experience(job, app), _skills(job, app, my_skills),
              _place(app.get("location") or job.get("location", ""), job.get("work_mode", ""))]
    if app.get("notes"):
        remark.append(app["notes"])
    when = app.get("applied_at")
    return {
        "company": app.get("company") or "",
        "title": title,
        "role": app.get("title") or "",     # without the ID: finds a row typed by hand
        "ref": ref,                         # finds the posting under another link
        "url": app.get("url") or "",
        "notes": " | ".join(x for x in remark if x),
        # Local date, so a job applied to at 00:45 IST is dated that day.
        "date": dt.date.fromtimestamp(when).isoformat() if when else "",
    }


# --- sending ---------------------------------------------------------------

def _post(payload: dict) -> dict:
    url, token = _settings()
    # Apps Script answers a POST with a redirect to the result, which requests
    # follows as a GET - the way the script expects.
    r = requests.post(url, json={**payload, "token": token}, timeout=60)
    r.raise_for_status()
    try:
        out = r.json()
    except ValueError:
        # An HTML page instead of JSON: a sign-in page (deployment access not
        # "Anyone") or Google's own error page. Its title says which.
        title = re.search(r"<title>(.*?)</title>", r.text, re.S | re.I)
        raise RuntimeError("the sheet script answered with a web page"
                           + (f" ({title.group(1).strip()[:80]})" if title else "")) from None
    if not out.get("ok"):
        raise RuntimeError(out.get("error") or "the sheet script refused it")
    return out


def record_applied(row: dict) -> str:
    """Add (or update) the row in the sheet. Returns what was done, for the
    UI: "added to Google Sheet", ... - never raises, since the application is
    already saved in Job Radar either way."""
    if not enabled():
        return ""
    try:
        out = _post(row)
    except Exception as e:      # network, timeout, script error: report, don't fail the save
        return f"Google Sheet not updated: {e}"
    return {"added": "added to Google Sheet",
            "updated": "updated in Google Sheet",
            "already": "already in Google Sheet"}.get(out.get("action"), "sent to Google Sheet")


if __name__ == "__main__":
    if not enabled():
        raise SystemExit("Set JOBRADAR_SHEET_URL and JOBRADAR_SHEET_TOKEN in .env first.")
    out = _post({"ping": True})
    print(f"Connected: tab '{out.get('tab')}' has {out.get('rows')} rows.")
