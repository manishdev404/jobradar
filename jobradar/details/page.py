"""Any job posting's page, read for what it says - no site-specific API.

In order, each filling what the one before left out:
  1. JSON-LD JobPosting   the structured copy most careers sites publish for search engines
  2. microdata            the same, written into the HTML (SuccessFactors)
  3. app data             the job object a page drawn by script carries
                          (__NEXT_DATA__, Phenom's phApp.ddo, a window.__STATE__)
  4. the page's text      its main or article block, else its body
  5. meta description     a page with nothing else still has a summary
"""
from __future__ import annotations

import html as html_mod
import json
import re

from .. import net
from .dates import parse_date, posted_from_text
from .facts import board_skills, employment_word, money, work_mode_word, years_from_field
from .model import Details
from .text import one_line, to_text

_LD = re.compile(r"<script\b[^>]*application/ld\+json[^>]*>(.*?)</script\s*>", re.S | re.I)
_JOB_WORDS = re.compile(r"experience|responsibilit|requirement|qualification|skills|you will|you'll", re.I)


# --- 1. JSON-LD --------------------------------------------------------------

def _loads(raw: str):
    raw = raw.strip()
    raw = re.sub(r"^\s*(?:<!--|<!\[CDATA\[)|(?:-->|\]\]>)\s*$", "", raw).strip()
    for attempt in (raw, re.sub(r",\s*([}\]])", r"\1", raw)):     # trailing commas
        try:
            return json.loads(attempt, strict=False)                # raw newlines in strings
        except ValueError:
            continue
    return None


def jobpostings(page: str) -> list[dict]:
    """Every schema.org JobPosting in the page's JSON-LD, however nested."""
    found = []
    for block in _LD.findall(page or ""):
        data = _loads(block)
        stack = [data] if data is not None else []
        while stack:
            d = stack.pop()
            if isinstance(d, dict):
                kind = d.get("@type")
                if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                    found.append(d)
                stack.extend(v for v in d.values() if isinstance(v, (list, dict)))
            elif isinstance(d, list):
                stack.extend(d)
    return found


def _str(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "\n".join(s for s in (_str(x) for x in v) if s)
    if isinstance(v, dict):
        return _str(v.get("description") or v.get("name") or "")
    return ""


def _address(loc) -> str:
    out = []
    for place in (loc if isinstance(loc, list) else [loc]):
        a = (place or {}).get("address") if isinstance(place, dict) else None
        if isinstance(a, dict):
            country = a.get("addressCountry")
            country = country.get("name") if isinstance(country, dict) else country
            parts = [a.get("addressLocality"), a.get("addressRegion"), country]
            line = ", ".join(str(p) for p in parts if p)
            if line and line not in out:
                out.append(line)
    return "; ".join(out[:3])


def _salary(bs) -> str:
    if not isinstance(bs, dict):
        return ""
    v = bs.get("value")
    if isinstance(v, dict):
        return money(v.get("minValue") or v.get("value"), v.get("maxValue"), bs.get("currency") or "",
                     v.get("unitText") or "")
    return money(v, None, bs.get("currency") or "", bs.get("unitText") or "")


def from_jsonld(p: dict) -> Details:
    body = to_text(_str(p.get("description")))
    parts = [body]
    # Some sites put requirements beside the description instead of in it.
    for key, head in (("responsibilities", "Responsibilities"), ("qualifications", "Qualifications"),
                      ("experienceRequirements", "Experience"), ("educationRequirements", "Education"),
                      ("skills", "Skills")):
        extra = to_text(_str(p.get(key)))
        if len(extra) > 20 and extra[:80] not in body:
            parts.append(f"{head}\n{extra}")
    d = Details(text="\n\n".join(x for x in parts if x), reader="page")
    d.posted_at = parse_date(p.get("datePosted"))
    d.closes_at = parse_date(p.get("validThrough"))
    d.employment_type = employment_word(p.get("employmentType"))
    if "TELECOMMUTE" in str(p.get("jobLocationType") or "").upper():
        d.work_mode = "remote"
    d.salary = _salary(p.get("baseSalary") or p.get("estimatedSalary"))
    d.location = _address(p.get("jobLocation"))
    exp = p.get("experienceRequirements")
    if isinstance(exp, dict) and exp.get("monthsOfExperience") not in (None, ""):
        try:
            d.years_min = int(float(exp["monthsOfExperience"])) // 12
        except (TypeError, ValueError):
            pass
    skills = p.get("skills")
    if isinstance(skills, list):
        d.skills = board_skills([_str(s) for s in skills])
    return d


# --- 2. microdata ----------------------------------------------------------------

_PROP = re.compile(r"<([a-zA-Z][\w-]*)\b[^>]*\bitemprop=[\"']([\w]+)[\"'][^>]*>", re.I)
_WANT = {"title", "description", "datePosted", "validThrough", "employmentType",
         "experienceRequirements", "qualifications", "responsibilities", "skills",
         "addressLocality", "addressRegion", "addressCountry"}
_VOID = {"meta", "link", "img", "br", "hr", "input"}


def element(page: str, start: int) -> str:
    """The element whose start tag begins at `start`, through its end tag."""
    m = re.compile(r"<([a-zA-Z][\w-]*)").match(page, start)
    if not m:
        return ""
    tag = m.group(1).lower()
    if tag in _VOID:
        return page[start:page.find(">", start) + 1]
    depth = 0
    for t in re.compile(rf"<(/?){tag}\b[^>]*?(/?)>", re.I).finditer(page, start):
        if t.group(1):
            depth -= 1
        elif not t.group(2):
            depth += 1
        if depth == 0:
            return page[start:t.end()]
    return page[start:start + 200_000]


def microdata(page: str) -> Details:
    props: dict[str, str] = {}
    for m in _PROP.finditer(page or ""):
        name = m.group(2)
        if name not in _WANT or name in props:
            continue
        attr = re.search(r"\b(?:content|datetime)=[\"']([^\"']*)[\"']", m.group(0))
        props[name] = html_mod.unescape(attr.group(1)) if attr else element(page, m.start())
    if not props:
        return Details()
    d = Details(text=to_text(props.get("description")), reader="page")
    for key, head in (("responsibilities", "Responsibilities"), ("qualifications", "Qualifications")):
        extra = to_text(props.get(key))
        if len(extra) > 20 and extra[:80] not in d.text:
            d.text = f"{d.text}\n\n{head}\n{extra}".strip()
    d.posted_at = parse_date(one_line(props.get("datePosted")))
    d.closes_at = parse_date(one_line(props.get("validThrough")))
    d.employment_type = employment_word(one_line(props.get("employmentType")))
    d.location = ", ".join(one_line(props[k]) for k in ("addressLocality", "addressRegion", "addressCountry")
                           if props.get(k))
    return d


# --- 3. app data ---------------------------------------------------------------------

_SCRIPT_JSON = re.compile(r"<script\b[^>]*type=[\"']application/json[\"'][^>]*>(.*?)</script\s*>", re.S | re.I)
_ASSIGN = re.compile(r"(?:window\.|self\.)?(?:__NEXT_DATA__|__INITIAL_STATE__|__PRELOADED_STATE__|"
                     r"__APOLLO_STATE__|__STATE__|__DATA__|__INITIAL_DATA__|phApp\.ddo|jobData)\s*=\s*")
_DESC_KEYS = ("description", "jobDescription", "job_description", "descriptionHtml", "description_html",
              "jobDescriptionHtml", "externalDescription", "descriptionPlain", "fullDescription",
              "content", "body", "jd")
_DATE_KEYS = ("datePosted", "postedDate", "postingDate", "publishedAt", "published_at", "publishDate",
              "datePublished", "postedOn", "posted_on", "releasedDate", "createdAt", "created_at",
              "dateCreated", "openDate")
_CLOSE_KEYS = ("validThrough", "closingDate", "closeDate", "expiryDate", "expirationDate",
               "applicationDeadline", "application_deadline")
_TYPE_KEYS = ("employmentType", "employment_type", "jobType", "job_type", "timeType", "commitment", "type")
_MODE_KEYS = ("workplaceType", "workplace_type", "remoteType", "locationType", "workMode", "work_mode",
              "isRemote", "remote")
_SKILL_KEYS = ("skills", "ml_skills", "skillNames", "requiredSkills", "skillset")
_YEAR_KEYS = ("experience", "minExperience", "min_experience", "experienceRequired", "workExperience",
              "yearsOfExperience", "experienceYears")
_PAY_KEYS = ("salary", "salaryRange", "compensation", "baseSalary", "pay")


def _blobs(page: str):
    for m in _SCRIPT_JSON.finditer(page or ""):
        data = _loads(m.group(1))
        if data is not None:
            yield data
    dec = json.JSONDecoder()
    for m in _ASSIGN.finditer(page or ""):
        i = m.end()
        if i < len(page) and page[i] in "{[":
            try:
                yield dec.raw_decode(page, i)[0]
            except ValueError:
                continue


def _job_node(data, budget: int = 80_000) -> tuple[dict | None, str]:
    """The object holding the longest posting-like description, and its key."""
    best, key, longest = None, "", 0
    stack = [data]
    while stack and budget > 0:
        node = stack.pop()
        budget -= 1
        if isinstance(node, dict):
            for k in _DESC_KEYS:
                v = node.get(k)
                if isinstance(v, str) and len(v) > max(longest, 200) and _JOB_WORDS.search(v):
                    best, key, longest = node, k, len(v)
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            stack.extend(x for x in node if isinstance(x, (dict, list)))
    return best, key


def _first(node: dict, keys: tuple[str, ...]):
    for k in keys:
        if node.get(k) not in (None, "", []):
            return node[k]
    return None


def embedded(page: str) -> Details:
    best, key = None, ""
    for blob in _blobs(page):
        node, k = _job_node(blob)
        if node is not None and (best is None or len(str(node[k])) > len(str(best[key]))):
            best, key = node, k
    if best is None:
        return Details()
    d = Details(text=to_text(best[key]), reader="page")
    for k, head in (("qualifications", "Qualifications"), ("requirements", "Requirements"),
                    ("responsibilities", "Responsibilities")):
        extra = to_text(_str(best.get(k)))
        if len(extra) > 20 and extra[:80] not in d.text:
            d.text = f"{d.text}\n\n{head}\n{extra}"
    d.posted_at = parse_date(_first(best, _DATE_KEYS))
    d.closes_at = parse_date(_first(best, _CLOSE_KEYS))
    d.employment_type = employment_word(_first(best, _TYPE_KEYS))
    d.work_mode = work_mode_word(_first(best, _MODE_KEYS))
    skills = _first(best, _SKILL_KEYS)
    if isinstance(skills, list):
        d.skills = board_skills([_str(s) if not isinstance(s, str) else s for s in skills][:30])
    years = _first(best, _YEAR_KEYS)
    if isinstance(years, (str, int, float)):
        d.years_min, d.years_max = years_from_field(years)
    pay = _first(best, _PAY_KEYS)
    if isinstance(pay, dict):
        d.salary = money(pay.get("min") or pay.get("minimum") or pay.get("minValue"),
                         pay.get("max") or pay.get("maximum") or pay.get("maxValue"),
                         pay.get("currency") or "", pay.get("interval") or pay.get("unit") or "")
    elif isinstance(pay, str) and re.search(r"\d", pay):
        d.salary = pay[:60]
    return d


# --- 4. and 5. the page's own text -----------------------------------------------------

_NOISE = re.compile(r"<(script|style|noscript|svg|nav|header|footer|aside|form|iframe|template|button|select)"
                    r"\b[^>]*>.*?</\1\s*>", re.S | re.I)
_POSTING_BLOCK = re.compile(r"<([a-zA-Z][\w-]*)\b[^>]*(?:id|class)=[\"'][^\"']*(?:job[-_]?description|"
                            r"jobdescription|job[-_]?details|job[-_]?content|posting[-_]?(?:body|content|"
                            r"description)|description[-_]?(?:body|content|text)|job[-_]?body)[^\"']*[\"']", re.I)
_META_DESC = re.compile(r"<meta[^>]+(?:name|property)=[\"'](?:description|og:description|twitter:description)"
                        r"[\"'][^>]*content=[\"']([^\"']{80,})[\"']", re.I)


def _strip_code(h: str) -> str:
    """Markup without its scripts and styles - also one the size cap cut off
    before its closing tag, whose code would otherwise read as text."""
    h = _NOISE.sub(" ", h)
    m = re.search(r"<(?:script|style)\b", h, re.I)
    return h[:m.start()] if m else h


def looks_like_code(text: str) -> bool:
    """JSON or JavaScript that got through as page text."""
    sample = text[:4000]
    return len(sample) > 200 and sum(sample.count(c) for c in "{};=") / len(sample) > 0.02


def main_text(page: str) -> str:
    body = re.search(r"<body[^>]*>(.*)</body>", page or "", re.S | re.I)
    h = _strip_code(body.group(1) if body else page or "")
    for pat in (_POSTING_BLOCK, re.compile(r"<(main|article)\b", re.I)):
        m = pat.search(h)
        if m:
            text = to_text(element(h, m.start()))
            if len(text) >= 300:
                return text
    return to_text(h)


def meta_description(page: str) -> str:
    found = [to_text(m.group(1)) for m in _META_DESC.finditer(page or "")]
    return max(found, key=len) if found else ""


def read(url: str, html: str | None = None) -> Details:
    """What the posting's own page says. Raises on a failed request; the
    caller (details.read) turns that into the reading's error."""
    if html is None:
        _, html = net.page(url, limit=800_000)
    d = Details(reader="page")
    posts = jobpostings(html)
    if posts:
        d.fill(from_jsonld(posts[0]))
    d.fill(microdata(html))
    d.fill(embedded(html))
    if not d.ok():
        text = main_text(html)
        if len(text) > len(d.text) and not looks_like_code(text):
            d.text = text
    if not d.ok():
        meta = meta_description(html)
        if len(meta) > len(d.text):
            d.text = meta
    if d.posted_at is None:
        published = re.search(r"<meta[^>]+property=[\"']article:published_time[\"'][^>]*content=[\"']([^\"']+)",
                              html, re.I)
        d.posted_guess = (parse_date(published.group(1)) if published else None) or \
            posted_from_text(one_line(_strip_code(html))[:200_000])
    d.text = d.text[:20000]
    d.reader = d.reader or "page"
    if not d.text:
        d.error = "no posting text on the page"
    return d
