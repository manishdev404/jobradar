"""Job board adapters. Every adapter returns a list of normalized dicts."""
from __future__ import annotations

import hashlib
import html as html_mod
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

UA = {"User-Agent": "jobradar/0.1 (personal job tracker)"}
TIMEOUT = 25


def _job_id(company: str, title: str, url: str) -> str:
    return hashlib.sha1(f"{company}|{title}|{url}".encode()).hexdigest()[:16]


def _norm(company, title, url, location="", posted_at=None, description="", source="",
          domain=""):
    return {
        "id": _job_id(company, title, url),
        "company": company,
        "title": (title or "").strip(),
        "url": url,
        "location": (location or "").strip(),
        "posted_at": posted_at,          # epoch seconds, or None if the board hides it
        "description": (description or "")[:6000],
        "source": source,
        "domain": domain,
    }


def _request(method: str, url: str, **kw) -> requests.Response:
    """One retry on a connection failure. Hosts like Workday drop the odd
    connection under a burst of parallel requests; a single retry after a pause
    turns most of those into a normal fetch instead of a missing company."""
    for attempt in (0, 1):
        try:
            r = requests.request(method, url, timeout=TIMEOUT, **kw)
            r.raise_for_status()
            return r
        except (requests.ConnectionError, requests.Timeout):
            if attempt:
                raise
            time.sleep(3)
    raise AssertionError("unreachable")


def _get(url: str) -> Any:
    return _request("GET", url, headers=UA).json()


def _epoch(value) -> float | None:
    """Boards return ms ints, ISO strings, or nothing. Normalize to epoch seconds."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e11 else float(value)
    try:
        from datetime import datetime
        s = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


_POSTED_ISO = re.compile(r"(?:date ?posted|posted(?: on)?|date opened)\s*[:\-]?\s*"
                         r"(\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+(?:Z|[+-]\d\d:?\d\d)?)?)", re.I)
_POSTED_AGO = re.compile(r"posted\s*[:\-]?\s*(?:on\s*)?(today|just now|yesterday|"
                         r"(\d+)\+?\s*(minute|hour|day|week|month|year)s?\s*ago)", re.I)
_UNIT = {"minute": 60, "hour": 3600, "day": 86400, "week": 7 * 86400,
         "month": 30 * 86400, "year": 365 * 86400}


def posted_from_text(text: str | None, now: float | None = None) -> float | None:
    """When a posting went up, from words a board prints: "Posted 2 Days Ago",
    "Posted 30+ Days Ago" (counted as 30: at least that old), "Posted: 9
    months ago", "Date posted: 2025-12-17". None when it says nothing."""
    text, now = text or "", now or time.time()
    m = _POSTED_ISO.search(text)
    if m:
        return _epoch(m.group(1))
    m = _POSTED_AGO.search(text)
    if not m:
        return None
    word = m.group(1).lower()
    if word in ("today", "just now"):
        return now
    if word == "yesterday":
        return now - 86400
    return now - int(m.group(2)) * _UNIT[m.group(3).lower()]


# --------------------------------------------------------------------------
# Per-company boards
# --------------------------------------------------------------------------

def greenhouse(name: str, slug: str) -> list[dict]:
    # The list is fetched WITHOUT content: with it, one big board is megabytes
    # per scan. The description of a job that survives the filters is fetched
    # once, from `api`, by fetch_details.
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        job = _norm(name, j.get("title"), j.get("absolute_url"), loc,
                    _epoch(j.get("updated_at") or j.get("first_published")), "", "greenhouse")
        job["api"] = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{j.get('id')}"
        out.append(job)
    return out


def lever(name: str, slug: str) -> list[dict]:
    data = _get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    out = []
    for j in data:
        cats = j.get("categories") or {}
        # descriptionPlain is only the intro; requirements ("2+ years of...")
        # live in `lists`, so include them or the experience gate is blind.
        lists = " ".join(f"{x.get('text', '')}: {x.get('content', '')}"
                         for x in (j.get("lists") or []))
        desc = " ".join(p for p in (j.get("descriptionPlain", ""), lists,
                                    j.get("additionalPlain", "")) if p)
        out.append(_norm(name, j.get("text"), j.get("hostedUrl"),
                         cats.get("location", ""), _epoch(j.get("createdAt")),
                         desc, "lever"))
    return out


def ashby(name: str, slug: str) -> list[dict]:
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=false")
    out = []
    for j in data.get("jobs", []):
        out.append(_norm(name, j.get("title"), j.get("jobUrl"), j.get("location", ""),
                         _epoch(j.get("publishedAt")), j.get("descriptionPlain", ""), "ashby"))
    return out


def workable(name: str, slug: str) -> list[dict]:
    data = _get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
    out = []
    for j in data.get("jobs", []):
        out.append(_norm(name, j.get("title"), j.get("url" ) or j.get("application_url"),
                         j.get("location", ""), _epoch(j.get("published_on")),
                         j.get("description", ""), "workable"))
    return out


BOARDS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workable": workable}


def fetch_company(entry: dict) -> tuple[list[dict], str | None]:
    """Returns (jobs, error). Never raises - one dead board must not kill the run."""
    board = entry.get("board", "greenhouse").lower()
    fn = BOARDS.get(board)
    if not fn:
        return [], f"unknown board '{board}'"
    try:
        jobs = fn(entry["name"], entry["slug"])
        for j in jobs:
            j["domain"] = entry.get("domain", "")
        return jobs, None
    except Exception as e:  # noqa: BLE001
        return [], f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------
# Aggregators (keyless, for resume-driven discovery)
# --------------------------------------------------------------------------

def remotive(query: str, limit: int = 40) -> list[dict]:
    data = _get(f"https://remotive.com/api/remote-jobs?search={requests.utils.quote(query)}&limit={limit}")
    return [
        _norm(j.get("company_name", "?"), j.get("title"), j.get("url"),
              j.get("candidate_required_location", "Remote"),
              _epoch(j.get("publication_date")), j.get("description", ""), "remotive")
        for j in data.get("jobs", [])[:limit]
    ]


def arbeitnow(query: str, limit: int = 40) -> list[dict]:
    data = _get("https://www.arbeitnow.com/api/job-board-api")
    q = query.lower()
    out = []
    for j in data.get("data", []):
        hay = f"{j.get('title','')} {' '.join(j.get('tags') or [])}".lower()
        if q in hay:
            out.append(_norm(j.get("company_name", "?"), j.get("title"), j.get("url"),
                             j.get("location", ""), _epoch(j.get("created_at")),
                             j.get("description", ""), "arbeitnow"))
        if len(out) >= limit:
            break
    return out


def discover(queries: list[str], max_per_query: int = 40) -> tuple[list[dict], list[str]]:
    jobs, errors = [], []
    for q in queries:
        for fn in (remotive, arbeitnow):
            try:
                jobs.extend(fn(q, max_per_query))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{fn.__name__}({q}): {type(e).__name__}: {e}")
            time.sleep(0.6)   # be polite to free endpoints
    return jobs, errors

# --------------------------------------------------------------------------
# Enterprise boards. Large companies rarely use Greenhouse/Lever; most sit on
# Workday or Oracle Recruiting Cloud, each with a public JSON endpoint.
# --------------------------------------------------------------------------

def _post(url: str, body: dict) -> Any:
    return _request("POST", url, headers={**UA, "Content-Type": "application/json",
                                          "Accept": "application/json"}, json=body).json()


def workday(name: str, slug: str) -> list[dict]:
    """slug is 'tenant/wdN/site', e.g. 'adobe/wd5/external_experienced'."""
    tenant, wd, site = slug.split("/")
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    out: list[dict] = []
    # Workday pages 20 at a time and caps hard; a few pages is plenty per run.
    for offset in range(0, 200, 20):
        data = _post(api, {"appliedFacets": {}, "limit": 20,
                           "offset": offset, "searchText": ""})
        postings = data.get("jobPostings") or []
        if not postings:
            break
        for j in postings:
            path = j.get("externalPath") or ""
            out.append(_norm(
                name, j.get("title"), f"{base}/{site}{path}",
                j.get("locationsText", ""), posted_from_text(j.get("postedOn")),
                j.get("bulletFields") and " ".join(j["bulletFields"]) or "", "workday"))
        if len(postings) < 20:
            break
    return out


def oracle_cloud(name: str, slug: str) -> list[dict]:
    """slug is 'host/siteNumber', e.g. 'eeho.fa.us2.oraclecloud.com/CX_1'."""
    host, site = slug.split("/", 1)
    out: list[dict] = []
    # requisitionList is only returned when explicitly expanded.
    for offset in (0, 200, 400):
        url = (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
               f"?onlyData=true&expand=requisitionList.secondaryLocations"
               f"&finder=findReqs;siteNumber={site},limit=200,offset={offset}")
        items = (_get(url).get("items") or [{}])[0]
        reqs = items.get("requisitionList") or []
        if not reqs:
            break
        for j in reqs:
            rid = j.get("Id") or ""
            out.append(_norm(name, j.get("Title"),
                             f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{rid}",
                             j.get("PrimaryLocation") or "", _epoch(j.get("PostedDate")),
                             j.get("ShortDescriptionStr") or "", "oracle"))
        if len(reqs) < 200:
            break
    return out


def atlassian_board(name: str, slug: str) -> list[dict]:
    """Atlassian publishes its own JSON feed rather than using a hosted board."""
    data = _get("https://www.atlassian.com/endpoint/careers/listings")
    out = []
    for j in data:
        portal = j.get("portalJobPost") or {}
        locs = j.get("locations") or []
        out.append(_norm(name, j.get("title"), portal.get("portalUrl"),
                         "; ".join(locs[:2]), _epoch(portal.get("updatedDate")),
                         re.sub(r"<[^>]+>", " ", j.get("overview") or ""), "atlassian"))
    return out


BOARDS.update({"workday": workday, "oracle": oracle_cloud, "atlassian": atlassian_board})


# --------------------------------------------------------------------------
# Indian HR suites. Common with Indian startups; none publishes a documented
# jobs API, so these read what each careers page itself loads. Their pages
# serve a stub to non-browser clients, hence the browser User-Agent.

BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"}
_GUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def keka(name: str, slug: str) -> list[dict]:
    """slug is the subdomain: 'fynd' for fynd.keka.com/careers."""
    base = f"https://{slug}.keka.com/careers/"
    page = _request("GET", base, headers=BROWSER).text
    m = re.search(rf"/ats/documents/({_GUID})/", page) or re.search(_GUID, page)
    if not m:
        raise ValueError("no Keka portal id on the careers page")
    org = m.group(1) if m.groups() else m.group(0)
    data = _request("GET", f"{base}api/embedjobs/default/active/{org}",
                    headers={**BROWSER, "Accept": "application/json"}).json()
    out = []
    for j in data or []:
        locs = "; ".join(", ".join(x for x in (l.get("city") or l.get("name"), l.get("countryName"))
                                   if x) for l in j.get("jobLocations") or [])
        # Keka states the bar in its own field; put it where the experience
        # gate reads, ahead of the description.
        desc = f"Experience: {j.get('experience') or 'not stated'}\n{j.get('description') or ''}"
        out.append(_norm(name, j.get("title"), f"{base}jobdetails/{j.get('id')}", locs,
                         _epoch(j.get("publishedOn")), desc, "keka"))
    return out


def darwinbox(name: str, slug: str) -> list[dict]:
    """slug is the host: 'acme.darwinbox.in' for acme.darwinbox.in/ms/candidate/careers."""
    api = f"https://{slug}/ms/candidateapi/job"
    out: list[dict] = []
    for page in range(1, 11):                       # 10 pages is far past any real board
        data = _request("GET", f"{api}?page={page}&limit=50",
                        headers={**BROWSER, "Accept": "application/json"}).json()
        msg = data.get("message") or {}
        jobs = msg.get("jobs") or [] if isinstance(msg, dict) else []
        for j in jobs:
            locs = j.get("officelocation_show_arr") or ""
            if not locs or locs.lower() == "multiple locations":
                locs = "; ".join(x.strip() for x in j.get("tool_tip_locations") or [])
            lo, hi = j.get("experience_from_num"), j.get("experience_to_num")
            exp = f"Experience: {lo}-{hi} years" if lo not in (None, "") else ""
            out.append(_norm(name, j.get("title"), f"https://{slug}/ms/candidate/careers/{j.get('id')}",
                             locs, _epoch(j.get("job_posting_on")), exp, "darwinbox"))
        total = msg.get("jobscount", 0) if isinstance(msg, dict) else 0
        if not jobs or len(out) >= int(total or 0):
            break
    return out


def zoho(name: str, slug: str) -> list[dict]:
    """slug is the host: 'acme.zohorecruit.in' for acme.zohorecruit.in/jobs/Careers.
    The careers page embeds the whole list as JSON in a hidden input."""
    page = _request("GET", f"https://{slug}/jobs/Careers", headers=BROWSER).text
    jobs = None
    for tag in re.findall(r"<input[^>]*>", page):
        if 'id="jobs"' in tag or "Posting_Title" in tag:
            v = re.search(r'value="([^"]*)"', tag)
            if v and "Posting_Title" in v.group(1):
                import json as _json
                jobs = _json.loads(html_mod.unescape(v.group(1)))
                break
    if jobs is None:
        raise ValueError("no job list on the Zoho Recruit careers page")
    out = []
    for j in jobs:
        if j.get("Publish") is False:
            continue
        title = j.get("Posting_Title") or j.get("Job_Opening_Name") or ""
        loc = ", ".join(x for x in (j.get("City"), j.get("State"), j.get("Country")) if x)
        if j.get("Remote_Job"):
            loc = f"{loc} (Remote)" if loc else "Remote"
        desc = f"Experience: {j.get('Work_Experience') or 'not stated'}\n{j.get('Job_Description') or ''}"
        url = f"https://{slug}/jobs/Careers/{j.get('id')}/{re.sub(r'[^A-Za-z0-9]+', '-', title).strip('-')}"
        out.append(_norm(name, title, url, loc, _epoch(j.get("Date_Opened")), desc, "zoho"))
    return out


BOARDS.update({"keka": keka, "darwinbox": darwinbox, "zoho": zoho})


# --------------------------------------------------------------------------
# Job details. Workday and Oracle list endpoints carry no description, so the
# experience a posting asks for is invisible until its detail page is read.
# Called once per NEW job that already passed the title/location filters.

def _workday_details(url: str) -> str:
    u = urlparse(url)
    tenant = u.netloc.split(".")[0]
    data = _get(f"https://{u.netloc}/wday/cxs/{tenant}{u.path}")
    info = data.get("jobPostingInfo") or {}
    when = info.get("startDate") or ""
    return (f"Date posted: {when}\n" if when else "") + (info.get("jobDescription") or "")


def _oracle_details(url: str) -> str:
    m = re.match(r"https://([^/]+)/hcmUI/CandidateExperience/[^/]+/sites/([^/]+)/job/([^/?#]+)",
                 url or "")
    if not m:
        return ""
    host, site, rid = m.groups()
    data = _get(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
                f'?expand=all&onlyData=true&finder=ById;Id="{rid}",siteNumber={site}')
    item = (data.get("items") or [{}])[0]
    return "\n".join(item.get(k) or "" for k in (
        "ExternalQualificationsStr", "ExternalResponsibilitiesStr", "ExternalDescriptionStr"))


def _greenhouse_details(job: dict) -> str:
    # Only a freshly fetched job carries its API url; a stored one already has
    # its description saved.
    return _get(job["api"]).get("content") or "" if job.get("api") else ""


def _darwinbox_details(url: str) -> str:
    m = re.match(r"https://([^/]+)/ms/candidate/careers/([^/?#]+)", url or "")
    if not m:
        return ""
    host, jid = m.groups()
    data = _request("GET", f"https://{host}/ms/candidateapi/job/{jid}",
                    headers={**BROWSER, "Accept": "application/json"}).json()
    job = ((data.get("message") or {}).get("job") or [{}])[0]
    lo, hi = job.get("experience_from_num"), job.get("experience_to_num")
    # Keep the structured experience line first: this replaces the list's text.
    # The detail endpoint counts MONTHS (60-120) where the list says years (5-10).
    try:
        exp = f"Experience: {int(lo) // 12}-{int(hi or lo) // 12} years\n"
    except (TypeError, ValueError):
        exp = ""
    return exp + html_mod.unescape(job.get("jd") or "")


_JS_ESCAPE = re.compile(r"\\(x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|.)", re.S)
_JS_CHARS = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}


def _js_unescape(s: str) -> str:
    """A single-quoted JS string literal's body as the text it stands for."""
    def one(m: re.Match) -> str:
        e = m.group(1)
        return chr(int(e[1:], 16)) if len(e) > 1 else _JS_CHARS.get(e, e)
    return _JS_ESCAPE.sub(one, s)


def _zoho_details(url: str) -> str:
    # Some boards' careers-page list carries only the title and city; the
    # detail page embeds the full record as `var jobs = JSON.parse('...')`.
    page = _request("GET", url, headers=BROWSER).text
    m = re.search(r"var jobs = JSON\.parse\('((?:[^'\\]|\\.)*)'\)", page, re.S)
    if not m:
        return ""
    import json as _json
    job = (_json.loads(_js_unescape(m.group(1))) or [{}])[0]
    opened = str(job.get("Date_Opened") or "")[:10]
    return ((f"Date opened: {opened}\n" if re.match(r"\d{4}-\d\d-\d\d", opened) else "")
            + f"Experience: {job.get('Work_Experience') or 'not stated'}\n"
            f"{job.get('Job_Description') or ''}")


DETAILS = {"workday": _workday_details, "oracle": _oracle_details,
           "greenhouse": _greenhouse_details, "darwinbox": _darwinbox_details,
           "zoho": _zoho_details}


def _greenhouse_by_jid(job: dict) -> str:
    """A company page showing a Greenhouse job (".../careers/job?gh_jid=123")
    draws the description with a script, so reading the page finds an empty
    shell. Greenhouse has it: try the board names the company is likely
    filed under - the site's name, then the company's."""
    url = job.get("url") or ""
    jid = (parse_qs(urlparse(url).query).get("gh_jid") or [""])[0]
    if not jid.isdigit():
        return ""
    host = urlparse(url).netloc.lower().split(":")[0]
    parts = [p for p in host.split(".") if p not in ("www", "careers", "jobs", "boards", "job-boards")]
    name = re.sub(r"[^a-z0-9]", "", (job.get("company") or "").lower())
    tokens = [t for t in dict.fromkeys([parts[0] if parts else "", name,
                                        re.sub(r"(inc|labs|technologies|tech|india)$", "", name)]) if t]
    for tok in tokens:
        try:
            content = _get(f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs/{jid}").get("content")
        except Exception:  # noqa: BLE001 - wrong board name: try the next
            continue
        if content:
            return content
    return ""


def fetch_details(job: dict) -> str:
    """Full description of one posting, for the experience gate. Never raises:
    a failed lookup leaves the job as it was ("experience not stated").

    Every board gets a reader: its own when it has one, else the posting's
    page (its JobPosting data, else the page text)."""
    try:
        text = _greenhouse_by_jid(job)
        if text:
            return text
        fn = DETAILS.get(job.get("source", ""))
        if fn is None:
            from .boards_more import _page_details
            return _page_details(job["url"]) if job.get("url") else ""
        return fn(job) if fn is _greenhouse_details else fn(job["url"])
    except Exception:  # noqa: BLE001
        return ""


# More platforms and the generic careers-page reader register themselves into
# BOARDS (and DETAILS) on import; kept in their own module for size.
from . import boards_more  # noqa: E402,F401
