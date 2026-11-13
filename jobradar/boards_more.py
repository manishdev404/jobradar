"""More careers platforms, and a generic careers-page reader for the rest.

Imported at the end of sources.py, so every board here lands in sources.BOARDS
and the scan, the health check and find-boards pick it up like any other.

slug per board:
  smartrecruiters  company id            jobs.smartrecruiters.com/<id>
  bamboohr         subdomain             <sub>.bamboohr.com/careers
  recruitee        subdomain             <sub>.recruitee.com
  freshteam        subdomain             <sub>.freshteam.com/jobs
  breezy           host                  <sub>.breezy.hr (or its own domain)
  pinpoint         host                  <sub>.pinpointhq.com (or its own domain)
  teamtailor       host                  <sub>.teamtailor.com (or its own domain)
  jazzhr           host                  <sub>.applytojob.com
  jobvite          company path          jobs.jobvite.com/<path>
  icims            host                  careers-<x>.icims.com
  workdaysite      wdN/tenant/site       wdN.myworkdaysite.com/recruiting/<tenant>/<site>
  successfactors   host                  the company's SuccessFactors career site
  phenom           base url              the company's Phenom career site (…/en)
  page             careers page url      anything else: JobPosting data or job links
"""
from __future__ import annotations

import html as html_mod
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from . import net
from .details.page import jobpostings
from .sources import BOARDS, BROWSER, _epoch, _norm, _request

_JSON = net.JSON
_INDIA = re.compile(r"\b(india|bengaluru|bangalore|hyderabad|pune|gurgaon|gurugram|noida|delhi|"
                    r"chennai|mumbai|kolkata|ahmedabad|jaipur|kochi|trivandrum|thiruvananthapuram|"
                    r"coimbatore|chandigarh|mohali|indore|mysore|mysuru|visakhapatnam)\b", re.I)


def _json(url: str, **kw):
    return net.json(url, headers=_JSON, **kw)


def _page(url: str, limit: int = 1_500_000) -> tuple[str, str]:
    """(final url, html) - capped, since some careers pages are huge."""
    return net.page(url, limit=limit, headers=BROWSER)


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


_COUNTRY = {"IN": "India", "IND": "India", "US": "United States", "USA": "United States",
            "GB": "United Kingdom", "UK": "United Kingdom"}


def _join(*parts) -> str:
    """'Pune', 'IN' -> 'Pune, India': the India filter wants the country's name."""
    out = []
    for x in parts:
        x = str(x or "").strip()
        if x:
            out.append(_COUNTRY.get(x.upper(), x) if len(x) <= 3 else x)
    return ", ".join(out)


# --------------------------------------------------------------------------
# Platforms with an open JSON / RSS job list

def smartrecruiters(name: str, slug: str) -> list[dict]:
    out = []
    for offset in range(0, 1000, 100):
        data = _json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
                     f"?limit=100&offset={offset}&country=in")
        for j in data.get("content") or []:
            loc = j.get("location") or {}
            out.append(_norm(name, j.get("name"),
                             f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                             _join(loc.get("city"), loc.get("region"), loc.get("country"))
                             or loc.get("fullLocation") or "",
                             _epoch(j.get("releasedDate")), "", "smartrecruiters"))
        if offset + 100 >= int(data.get("totalFound") or 0):
            break
    return out


def bamboohr(name: str, slug: str) -> list[dict]:
    data = _json(f"https://{slug}.bamboohr.com/careers/list")
    out = []
    for j in data.get("result") or []:
        loc = j.get("location") or {}
        where = _join(loc.get("city"), loc.get("state"), (j.get("atsLocation") or {}).get("country"))
        if j.get("isRemote"):
            where = _join(where, "Remote")
        out.append(_norm(name, j.get("jobOpeningName"), f"https://{slug}.bamboohr.com/careers/{j.get('id')}",
                         where, None, "", "bamboohr"))
    return out


def recruitee(name: str, slug: str) -> list[dict]:
    data = _json(f"https://{slug}.recruitee.com/api/offers/")
    return [_norm(name, j.get("title"), j.get("careers_url") or j.get("url"),
                  _join(j.get("city"), j.get("country")), _epoch(j.get("published_at") or j.get("created_at")),
                  _text(j.get("description"))[:3000], "recruitee")
            for j in data.get("offers") or []]


def breezy(name: str, slug: str) -> list[dict]:
    data = _json(f"https://{slug}/json")
    out = []
    for j in data or []:
        loc = j.get("location") or {}
        country = loc.get("country") or {}
        out.append(_norm(name, j.get("name"), j.get("url"),
                         loc.get("name") or _join(loc.get("city"),
                                                  country.get("name") if isinstance(country, dict) else country),
                         _epoch(j.get("published_date")), "", "breezy"))
    return out


def pinpoint(name: str, slug: str) -> list[dict]:
    data = _json(f"https://{slug}/postings.json")
    out = []
    for j in data.get("data") or []:
        loc = j.get("location") or {}
        out.append(_norm(name, j.get("title"), j.get("url") or f"https://{slug}/postings/{j.get('id')}",
                         loc.get("name") or _join(loc.get("city"), loc.get("province")),
                         None, _text(j.get("description"))[:3000], "pinpoint"))
    return out


def teamtailor(name: str, slug: str) -> list[dict]:
    """Teamtailor career sites publish an RSS feed of every open job."""
    _, xml = _page(f"https://{slug}/jobs.rss")
    out = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
        def tag(t):
            m = re.search(rf"<{t}[^>]*>(.*?)</{t}>", item, re.S)
            return _text(re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", m.group(1), flags=re.S)) if m else ""
        where = _join(tag("tt:city"), tag("tt:country")) or " ".join(
            _text(x) for x in re.findall(r"<tt:location>(.*?)</tt:location>", item, re.S))
        out.append(_norm(name, tag("title"), tag("link"), where, _epoch(tag("pubDate")),
                         tag("description")[:3000], "teamtailor"))
    return out


# --------------------------------------------------------------------------
# Platforms read from their job-list HTML

def jobvite(name: str, slug: str) -> list[dict]:
    base, page = _page(f"https://jobs.jobvite.com/{slug}/jobs")
    out, seen = [], set()
    # Two layouts: table cells, or one link per job holding name / location divs.
    for m in re.finditer(r'<(?:td|div)[^>]*class="[^"]*jv-job-list-name[^"]*"[^>]*>(.*?)</(?:td|div)>',
                         page, re.S):
        before = page[max(0, m.start() - 600):m.start()]
        inner = m.group(1)
        # the link is inside the cell (table layout) or wraps it (link layout)
        links = re.findall(r'href="([^"]*/job/[^"]+)"', inner) or \
            re.findall(r'<a[^>]+href="([^"]*/job/[^"]+)"', before)[-1:]
        if not links:
            continue
        url = urljoin(base, links[0])
        if url in seen:
            continue
        seen.add(url)
        loc = re.search(r'class="[^"]*jv-job-list-location[^"]*"[^>]*>(.*?)</(?:td|div)>',
                        page[m.end():m.end() + 800], re.S)
        out.append(_norm(name, _text(inner), url, _text(loc.group(1)) if loc else "", None, "",
                         "jobvite"))
    return out


def jazzhr(name: str, slug: str) -> list[dict]:
    base, page = _page(f"https://{slug}/apply")
    out = []
    for block in re.findall(r'<li class="list-group-item">(.*?)</li>\s*(?=<li class="list-group-item">|</ul>)',
                            page, re.S):
        a = re.search(r'<a[^>]+href="([^"]+/apply/[^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not a:
            continue
        loc = re.search(r'fa-map-marker[^>]*></i>(.*?)</li>', block, re.S)
        out.append(_norm(name, _text(a.group(2)), urljoin(base, a.group(1)),
                         _text(loc.group(1)) if loc else "", None, "", "jazzhr"))
    return out


def freshteam(name: str, slug: str) -> list[dict]:
    base, page = _page(f"https://{slug}.freshteam.com/jobs")
    out = []
    for m in re.finditer(r'<a([^>]+href="(/jobs/[^"]+)"[^>]*)>(.*?)</a>', page, re.S):
        attrs, href, block = m.groups()
        title = re.search(r'class="job-title"[^>]*>(.*?)</', block, re.S)
        loc = (re.search(r'data-portal-location="([^"]*)"', attrs)
               or re.search(r'class="location-info"[^>]*>(.*?)</div>', block, re.S))
        if title:
            out.append(_norm(name, _text(title.group(1)), urljoin(base, href),
                             _text(loc.group(1)) if loc else "", None, "", "freshteam"))
    return out


def icims(name: str, slug: str) -> list[dict]:
    out, seen = [], set()
    for pr in range(0, 5):
        base, page = _page(f"https://{slug}/jobs/search?ss=1&in_iframe=1&pr={pr}")
        found = 0
        for m in re.finditer(r'<a[^>]+href="([^"]*/jobs/(\d+)/[^"]*/job[^"]*)"[^>]*>(.*?)</a>', page, re.S):
            url, jid, inner = m.groups()
            title = _text(inner)
            if not title or jid in seen or title.lower() in ("apply", "view job"):
                continue
            seen.add(jid)
            found += 1
            near = _text(page[m.end():m.end() + 1500])
            loc = re.search(r"(?:Job Locations?|Location)\s*:?\s*([A-Za-z ,\-()]{3,80})", near)
            out.append(_norm(name, title, urljoin(base, url).split("?")[0],
                             loc.group(1).strip() if loc else "", None, "", "icims"))
        if not found:
            break
    return out


def workdaysite(name: str, slug: str) -> list[dict]:
    """Workday's other host: wdN.myworkdaysite.com/recruiting/<tenant>/<site>."""
    wd, tenant, site = slug.split("/")
    host = f"https://{wd}.myworkdaysite.com"
    out = []
    for offset in range(0, 200, 20):
        data = _request("POST", f"{host}/wday/cxs/{tenant}/{site}/jobs",
                        headers={**_JSON, "Content-Type": "application/json"},
                        json={"appliedFacets": {}, "limit": 20, "offset": offset,
                              "searchText": ""}).json()
        posts = data.get("jobPostings") or []
        for j in posts:
            out.append(_norm(name, j.get("title"),
                             f"{host}/recruiting/{tenant}/{site}{j.get('externalPath') or ''}",
                             j.get("locationsText", ""), None,
                             " ".join(j.get("bulletFields") or []), "workday"))
        if len(posts) < 20:
            break
    return out


def successfactors(name: str, slug: str) -> list[dict]:
    """SuccessFactors career sites (the /search/ kind), searched for India."""
    out = []
    for start in range(0, 250, 25):
        base, page = _page(f"https://{slug}/search/?q=&locationsearch=India&startrow={start}")
        rows = re.findall(r'<tr class="data-row[^"]*"[^>]*>(.*?)</tr>', page, re.S)
        for row in rows:
            a = re.search(r'class="jobTitle-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', row, re.S) or \
                re.search(r'href="([^"]+)"[^>]*class="jobTitle-link"[^>]*>(.*?)</a>', row, re.S)
            if not a:
                continue
            loc = re.search(r'class="jobLocation"[^>]*>(.*?)</span>', row, re.S)
            when = re.search(r'class="jobDate"[^>]*>(.*?)</span>', row, re.S)
            out.append(_norm(name, _text(a.group(2)), urljoin(base, a.group(1)),
                             _text(loc.group(1)) if loc else "",
                             _epoch(_text(when.group(1))) if when else None, "", "successfactors"))
        if len(rows) < 25:
            break
    return out


def phenom(name: str, slug: str) -> list[dict]:
    """Phenom career sites embed their search results as JSON (phApp.ddo)."""
    base = slug.rstrip("/")
    out, seen = [], set()
    for offset in range(0, 200, 10):
        _, page = _page(f"{base}/search-results?keywords=&location=India&from={offset}&s=1")
        m = re.search(r"phApp\.ddo\s*=\s*(\{.*?\});\s*phApp", page, re.S)
        if not m:
            break
        data = json.loads(m.group(1))
        jobs = ((data.get("eagerLoadRefineSearch") or {}).get("data") or {}).get("jobs") or []
        for j in jobs:
            jid = j.get("jobSeqNo") or j.get("jobId")
            if jid in seen:
                continue
            seen.add(jid)
            where = j.get("cityStateCountry") or j.get("location") or _join(j.get("city"), j.get("country"))
            out.append(_norm(name, j.get("title"), j.get("applyUrl") or f"{base}/job/{jid}",
                             where, _epoch(j.get("postedDate")),
                             _text(j.get("descriptionTeaser") or "")[:3000], "phenom"))
        if len(jobs) < 10:
            break
    return out


# --------------------------------------------------------------------------
# Any other careers page: schema.org JobPosting data, else links that read
# like job titles. Jobs the page draws with JavaScript are invisible here.

_JOBWORD = re.compile(
    r"\b(engineer|developer|programmer|architect|sde|sdet|devops|sre|analyst|scientist|designer|"
    r"manager|lead|head|director|executive|associate|intern|trainee|officer|specialist|consultant|"
    r"administrator|tester|qa|support|technician|coordinator|accountant|recruiter|writer|"
    r"marketing|sales|product owner|full ?stack|front ?end|back ?end)\b", re.I)
_NOT_JOB = re.compile(r"^(careers?|jobs?|join us|view (all|more|jobs?)|apply( now)?|see (all )?openings|"
                      r"open positions|life at|our (team|culture)|learn more|read more|home|about|contact|"
                      r"blog|login|sign in|privacy|terms)\b", re.I)


# A link guessed from a careers page is a job only when its address looks like
# a posting ("/careers/sde-1", "/jobs/1790") or sits on a hiring system:
# "/hire-reactjs-developer" is a sales page, "/full-stack-development-courses"
# a course, "/roadmap/html" a tutorial.
_JOB_PATH = re.compile(r"/(jobs?|careers?|openings?|positions?|vacanc(?:y|ies)|roles?|requisitions?|"
                       r"postings?|apply|job-?details?|opportunit(?:y|ies))(?=[/\-_#?.]|$)", re.I)
_ATS_HOST = re.compile(r"(^|\.)(rippling|gohire|lever|greenhouse|ashbyhq|workable|recruitee|breezy|keka|"
                       r"zohorecruit|darwinbox|freshteam|smartrecruiters|bamboohr|teamtailor|jobvite|"
                       r"ycombinator|wellfound|angel)\.", re.I)
# Job sites listing other companies' jobs: never the employer (a re-posted
# Accenture job is not a Rozgar job, and Accenture may be blocked).
_AGGREGATOR = re.compile(r"(^|\.)(rozgar|cutshort|naukri|indeed|foundit|monsterindia|shine|timesjobs|"
                         r"glassdoor|instahyre|internshala|apna|hirist|iimjobs|linkedin|jooble|"
                         r"freshersworld|placementindia)\.", re.I)
_NOT_JOB_TEXT = re.compile(r"\b(course|courses|bootcamp|roadmap|tutorials?|interviews?|hire|services|"
                           r"development company|consultation|jobs in|certification|training program)\b",
                           re.I)
# Named somewhere else: keep that place, so the India filter drops it rather
# than a careers page that merely mentions India marking it India.
_ABROAD = re.compile(r"\b(israel|ramat gan|tel aviv|united states|usa|new york|san francisco|seattle|"
                     r"austin|boston|chicago|canada|toronto|vancouver|united kingdom|london|ireland|"
                     r"dublin|germany|berlin|munich|france|paris|netherlands|amsterdam|poland|warsaw|"
                     r"spain|madrid|portugal|lisbon|singapore|dubai|uae|australia|sydney|melbourne|"
                     r"japan|tokyo|philippines|manila|vietnam|brazil|mexico)\b", re.I)


def _is_aggregator(url: str) -> bool:
    return bool(_AGGREGATOR.search(urlparse(url).netloc.lower()))


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self._href, self._buf = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._buf = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self._href is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, re.sub(r"\s+", " ", "".join(self._buf)).strip(),
                               self.getpos()[0]))
            self._href = None


def _jsonld_jobs(page: str) -> list[dict]:
    """Every JobPosting in the page's JSON-LD (tolerant of the trailing commas
    and raw newlines careers sites leave in it)."""
    return jobpostings(page)


def page(name: str, slug: str) -> list[dict]:
    if _is_aggregator(slug):
        return []                    # a job site, not an employer
    base, html = _page(slug)
    if _is_aggregator(base):
        return []
    page_india = bool(_INDIA.search(_text(html)))
    out, seen = [], set()
    for d in _jsonld_jobs(html):
        locs = d.get("jobLocation") or []
        locs = locs if isinstance(locs, list) else [locs]
        where = "; ".join(_join(a.get("addressLocality"), a.get("addressRegion"),
                                a.get("addressCountry") if isinstance(a.get("addressCountry"), str)
                                else (a.get("addressCountry") or {}).get("name"))
                          for a in ((l or {}).get("address") or {} for l in locs) if isinstance(a, dict))
        url = d.get("url") or base
        if (d.get("title"), url) in seen:
            continue
        seen.add((d.get("title"), url))
        out.append(_norm(name, _text(d.get("title")), url, where or ("India" if page_india else ""),
                         _epoch(d.get("datePosted")), _text(d.get("description"))[:3000], "page"))
    if out:
        return out
    parser = _Links()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - broken markup: keep what was parsed
        pass
    lines = html.splitlines()
    host = urlparse(base).netloc
    for href, text, line in parser.links:
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        if not (4 <= len(text) <= 90) or not _JOBWORD.search(text) or _NOT_JOB.search(text):
            continue
        if _NOT_JOB_TEXT.search(text):
            continue
        url = urljoin(base, href)
        where = urlparse(url)
        if where.netloc not in (host, "") and not re.search(r"job|career|apply|posting", url, re.I):
            continue
        if _is_aggregator(url):
            continue
        if not (_JOB_PATH.search(where.path + ("#" + where.fragment if where.fragment else ""))
                or _ATS_HOST.search(where.netloc)):
            continue
        if url in seen:
            continue
        seen.add(url)
        near = _text(" ".join(lines[max(0, line - 3):line + 3]))
        city = _INDIA.search(text) or _ABROAD.search(text) or _INDIA.search(near) or _ABROAD.search(near)
        out.append(_norm(name, text, url, city.group(0) if city else ("India" if page_india else ""),
                         None, "", "page"))
    return out[:150]


BOARDS.update({
    "smartrecruiters": smartrecruiters, "bamboohr": bamboohr, "recruitee": recruitee,
    "breezy": breezy, "pinpoint": pinpoint, "teamtailor": teamtailor, "jobvite": jobvite,
    "jazzhr": jazzhr, "freshteam": freshteam, "icims": icims, "workdaysite": workdaysite,
    "successfactors": successfactors, "phenom": phenom, "page": page,
})
