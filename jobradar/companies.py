"""The company list: import from anywhere, keep in SQLite, export to JSON.

The `companies` table in jobradar.db is the source of truth for which boards a
scan reads. It holds every company Job Radar knows about - including ones on
careers sites it cannot read yet (board 'other', saved with their link and not
scanned), so nothing you collected is lost.

Imports accept:
  .json  a list of {name, board, slug, ...} (e.g. companies.json)
  .txt   Name|board|slug|careers URL|confidence lines (the Claude prompt format)
  .xlsx  / .csv  an application tracker with Company and Job Link columns;
         the board is worked out from the link
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from . import sources
from .store import Store

# (board, pattern on the URL, how to build the slug from the match)
_PATTERNS = (
    ("greenhouse", r"(?:job-)?boards(?:-api)?\.greenhouse\.io/(?:v1/boards/)?([A-Za-z0-9_-]+)",
     lambda m: m.group(1)),
    ("greenhouse", r"[?&]gh_jid=", None),                       # embedded: slug unknown
    ("lever", r"jobs\.lever\.co/([A-Za-z0-9_.-]+)", lambda m: m.group(1)),
    ("ashby", r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", lambda m: m.group(1)),
    ("workable", r"apply\.workable\.com/([A-Za-z0-9_-]+)", lambda m: m.group(1)),
    ("workday", r"([A-Za-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)",
     lambda m: f"{m.group(1)}/{m.group(2)}/{m.group(3)}"),
    ("oracle", r"([A-Za-z0-9.-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/[^/]+/sites/([^/?#]+)",
     lambda m: f"{m.group(1)}/{m.group(2)}"),
    ("keka", r"([a-z0-9-]+)\.keka\.com", lambda m: m.group(1)),
    ("darwinbox", r"([a-z0-9.-]+\.darwinbox\.(?:in|com))", lambda m: m.group(1)),
    ("zoho", r"([a-z0-9.-]+\.zohorecruit\.(?:in|com|eu))", lambda m: m.group(1)),
)
_READABLE = set(sources.BOARDS) - {"atlassian"}


def detect_board(url: str) -> tuple[str, str]:
    """(board, slug) for a careers or job link; ('other', '') when the site
    is not one Job Radar can read."""
    for board, pattern, slug in _PATTERNS:
        m = re.search(pattern, url or "", re.I)
        if m:
            return (board, slug(m)) if slug else ("other", "")
    return "other", ""


def careers_url(board: str, slug: str) -> str:
    return {
        "greenhouse": f"https://job-boards.greenhouse.io/{slug}",
        "lever": f"https://jobs.lever.co/{slug}",
        "ashby": f"https://jobs.ashbyhq.com/{slug}",
        "workable": f"https://apply.workable.com/{slug}",
        "workday": "https://{0}.{1}.myworkdayjobs.com/{2}".format(*slug.split("/"))
        if slug.count("/") == 2 else "",
        "oracle": "https://{0}/hcmUI/CandidateExperience/en/sites/{1}".format(*slug.split("/", 1))
        if "/" in slug else "",
        "keka": f"https://{slug}.keka.com/careers",
        "darwinbox": f"https://{slug}/ms/candidate/careers",
        "zoho": f"https://{slug}/jobs/Careers",
    }.get(board, "")


def _clean_name(name: str) -> str:
    name = re.sub(r"\s*\((?:job id|jobid)[^)]*\)\s*", " ", str(name or ""), flags=re.I)
    return re.sub(r"\s+", " ", name).strip(" .,-|")


def _row(name, board, slug, url, source, **extra) -> dict:
    board = (board or "other").lower()
    if board not in _READABLE and board != "atlassian":
        board, slug = "other", ""
    row = {"name": _clean_name(name), "board": board, "slug": slug or "",
           "careers_url": url or careers_url(board, slug or ""), "source": source}
    row.update({k: v for k, v in extra.items() if v not in (None, "")})
    return row


def read_json(path: Path) -> list[dict]:
    out = []
    for c in json.loads(path.read_text(encoding="utf-8")):
        extra = {k: c.get(k) for k in ("domain", "enabled", "note", "india_jobs",
                                         "india_tech_jobs", "checked_at")}
        out.append(_row(c.get("name"), c.get("board"), c.get("slug"), c.get("careers_url"),
                        c.get("source") or f"import:{path.name}", **extra))
    return out


def read_pipe(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        p = [x.strip() for x in line.split("|")]
        if len(p) >= 3 and p[0]:
            out.append(_row(p[0], p[1], p[2].strip("/"), p[3] if len(p) > 3 else "",
                            f"list:{path.name}",
                            note=f"confidence: {p[4]}" if len(p) > 4 and p[4] else ""))
    return out


def read_tracker(path: Path) -> list[dict]:
    """Company + Job Link columns of an application tracker (.xlsx / .csv)."""
    from .applied import _columns, _rows   # same header detection as import-applied
    out = []
    for sheet, rows in _rows(path):
        if not rows:
            continue
        cols = _columns(rows[0])
        if "company" not in cols:
            continue
        for r in rows[1:]:
            name = r[cols["company"]] if cols["company"] < len(r) else None
            link = r[cols["url"]] if "url" in cols and cols["url"] < len(r) else None
            if not name:
                continue
            link = str(link or "").strip()
            link = link if link.lower().startswith("http") else ""
            board, slug = detect_board(link)
            out.append(_row(name, board, slug, careers_url(board, slug) or link,
                            f"tracker:{path.name}",
                            note="" if board != "other" else "careers site not supported yet"))
    return out


def import_file(path: Path, store: Store) -> dict:
    suffix = path.suffix.lower()
    rows = (read_json(path) if suffix == ".json" else
            read_pipe(path) if suffix == ".txt" else read_tracker(path))
    counts = {"added": 0, "updated": 0, "unchanged": 0}
    for r in rows:
        counts[store.upsert_company(r, commit=False)] += 1
    store.db.commit()
    return counts


def config_companies(store: Store) -> list[dict]:
    """Enabled, readable companies in the shape config.yaml's list uses."""
    return [{"name": c["name"], "board": c["board"], "slug": c["slug"],
             **({"domain": c["domain"]} if c["domain"] else {})}
            for c in store.companies(enabled_only=True)]


# --------------------------------------------------------------------------
# Health check: one light request per company.

_INDIA = re.compile(r"\b(india|bengaluru|bangalore|hyderabad|pune|gurgaon|gurugram|noida|delhi|"
                    r"chennai|mumbai|kolkata|ahmedabad|jaipur|kochi|trivandrum|thiruvananthapuram|"
                    r"coimbatore|chandigarh|mohali|indore|mysore|mysuru|visakhapatnam)\b", re.I)
_TECH = re.compile(r"software|developer|sde|backend|back end|full ?stack|frontend|front end|engineer",
                   re.I)
_GONE = {404, 410, 422}          # the board does not exist (Workday answers 422 for a bad site)


def _light_jobs(c: dict) -> list[tuple[str, str]]:
    """(title, location) pairs from one cheap request; Workday is asked for
    India matches only instead of paging its whole board."""
    import requests
    board, slug = c["board"], c["slug"]
    if board == "workday":
        tenant, wd, site = slug.split("/")
        r = requests.post(f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
                          json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "India"},
                          headers={**sources.UA, "Accept": "application/json"}, timeout=20)
        r.raise_for_status()
        return [(j.get("title", ""), j.get("locationsText", "") or "")
                for j in r.json().get("jobPostings") or []]
    jobs = sources.BOARDS[board](c["name"], slug)
    return [(j["title"], j["location"] or "") for j in jobs]


def check_company(c: dict) -> dict:
    """{'status': 'ok' | 'gone' | 'error', 'india': n, 'tech': n}"""
    import requests
    try:
        pairs = _light_jobs(c)
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else 0
        return {"status": "gone" if code in _GONE else "error", "note": f"HTTP {code}"}
    except (requests.ConnectionError, requests.Timeout) as e:
        return {"status": "error", "note": type(e).__name__}
    except Exception as e:  # noqa: BLE001 - a page without a job list, bad slug shape...
        return {"status": "gone", "note": f"{type(e).__name__}: {e}"[:120]}
    india = [(t, l) for t, l in pairs if _INDIA.search(l)]
    return {"status": "ok", "india": len(india),
            "tech": sum(1 for t, _ in india if _TECH.search(t))}


def check_all(store: Store, workers: int = 10, only_unchecked: bool = False) -> dict:
    """Check every readable company; record India counts, disable boards that
    do not exist, re-enable ones that came back. Errors change nothing."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    rows = [c for c in store.companies() if c["board"] not in ("other", "atlassian")
            and (not only_unchecked or c["checked_at"] is None)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(check_company, rows))
    counts = {"ok": 0, "gone": 0, "error": 0}
    now = time.time()
    for c, r in zip(rows, results):
        counts[r["status"]] += 1
        if r["status"] == "ok":
            store.upsert_company({"name": c["name"], "enabled": True, "india_jobs": r["india"],
                                  "india_tech_jobs": r["tech"], "checked_at": now,
                                  "note": "" if c["note"].startswith("board not found") else c["note"]},
                                 commit=False)
            if c["note"].startswith("board not found"):
                store.db.execute("UPDATE companies SET note = '' WHERE lower(name) = ?",
                                 (c["name"].lower(),))
        elif r["status"] == "gone":
            store.db.execute("UPDATE companies SET enabled = 0, note = ?, checked_at = ? "
                             "WHERE lower(name) = ?",
                             (f"board not found ({r['note']})", now, c["name"].lower()))
    store.db.commit()
    return counts
