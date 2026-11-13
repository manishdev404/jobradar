"""Job board adapters. Every adapter returns a list of normalized dicts."""
from __future__ import annotations

import hashlib
import html as html_mod
import re
import time
from typing import Any

import requests

from . import net
from .details.dates import parse_date, posted_from_text  # noqa: F401 - posted_from_text is shared
from .details.facts import board_skills, employment_word, money, years_from_field
from .details.sites.ashby import from_posting as _ashby_posting
from .details.sites.lever import from_posting as _lever_posting
from .details.sites.zoho import from_record as _zoho_record

UA = net.UA
TIMEOUT = net.TIMEOUT


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
    """See net.request: pooled connections, one retry on a dropped
    connection or a busy host."""
    return net.request(method, url, **kw)


def _get(url: str) -> Any:
    return _request("GET", url, headers=UA).json()


def _epoch(value) -> float | None:
    """Boards return ms ints, ISO strings, RSS dates, "Nov 3, 2025" - or
    nothing. Normalize to epoch seconds."""
    return parse_date(value)


def _facts(job: dict, **found) -> dict:
    """Keep what a board's list states as data - work mode, employment type,
    salary, experience, skills - on the job, so nothing has to guess it from
    the description later."""
    for key, value in found.items():
        if value not in (None, "", []):
            job[key] = value
    return job


def _with(job: dict, d) -> dict:
    """A list item's facts, read by the same reader as the posting itself."""
    return _facts(job, work_mode=d.work_mode, employment_type=d.employment_type, salary=d.salary,
                  closes_at=d.closes_at, years_min=d.years_min, years_max=d.years_max,
                  board_skills=d.skills)


# --------------------------------------------------------------------------
# Per-company boards
# --------------------------------------------------------------------------

def greenhouse(name: str, slug: str) -> list[dict]:
    # The list is fetched WITHOUT content: with it, one big board is megabytes
    # per scan. The description of a job that survives the filters is fetched
    # once, from `api`, by the posting reader (details/sites/greenhouse.py).
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
        # Each item is the whole posting; its `lists` carry the requirements
        # ("2+ years of..."), each under its own heading.
        d = _lever_posting(j)
        out.append(_with(_norm(name, j.get("text"), j.get("hostedUrl"), d.location, d.posted_at,
                               d.text, "lever"), d))
    return out


def ashby(name: str, slug: str) -> list[dict]:
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
    out = []
    for j in data.get("jobs", []):
        d = _ashby_posting(j)
        out.append(_with(_norm(name, j.get("title"), j.get("jobUrl"), j.get("location", ""),
                               d.posted_at, d.text, "ashby"), d))
    return out


def workable(name: str, slug: str) -> list[dict]:
    data = _get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
    out = []
    for j in data.get("jobs", []):
        job = _norm(name, j.get("title"), j.get("url" ) or j.get("application_url"),
                    j.get("location", ""), _epoch(j.get("published_on")),
                    j.get("description", ""), "workable")
        out.append(_facts(job, work_mode="remote" if j.get("telecommuting") else "",
                          employment_type=employment_word(j.get("employment_type"))))
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

BROWSER = net.BROWSER
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
        job = _norm(name, j.get("title"), f"{base}jobdetails/{j.get('id')}", locs,
                    _epoch(j.get("publishedOn")), desc, "keka")
        low, high = years_from_field(j.get("experience"))
        pay = j.get("salaryRange") or {}
        out.append(_facts(job, years_min=low, years_max=high,
                          salary=money(pay.get("minimum"), pay.get("maximum"), pay.get("currency") or ""),
                          board_skills=board_skills(j.get("skillNames") or [])))
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
            job = _norm(name, j.get("title"), f"https://{slug}/ms/candidate/careers/{j.get('id')}",
                        locs, _epoch(j.get("job_posting_on")), exp, "darwinbox")
            low, high = years_from_field(f"{lo}-{hi} years" if hi not in (None, "") else lo)
            out.append(_facts(job, years_min=low, years_max=high))
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
        out.append(_with(_norm(name, title, url, loc, _epoch(j.get("Date_Opened")), desc, "zoho"),
                         _zoho_record(j)))
    return out


BOARDS.update({"keka": keka, "darwinbox": darwinbox, "zoho": zoho})


# --------------------------------------------------------------------------
# One posting's full text and facts live in details/: a reader per site with
# its own posting data, the posting's page, and a headless browser for pages
# drawn by script.

def fetch_details(job: dict) -> str:
    """Full description of one posting. Never raises: a failed lookup leaves
    the job as it was. For callers that only want the text; the scan uses
    details.read, which keeps every field."""
    from . import details
    return details.read(job).text


# More platforms and the generic careers-page reader register themselves into
# BOARDS on import; kept in their own module for size.
from . import boards_more  # noqa: E402,F401
