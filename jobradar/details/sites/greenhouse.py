"""Greenhouse: the posting's content, its custom fields (employment type,
workplace, experience, salary - each company names them its own way), the
application deadline and the pay range, when the company publishes one.

Its job list dates a posting by its last update; first_published is kept
beside it, since an evergreen posting can be years old and edited weekly."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from ... import net
from ..dates import parse_date
from ..facts import employment_word, money, work_mode_word, years_from_field
from ..model import Details
from ..text import to_text
from . import site

_BOARD_URL = re.compile(r"(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)"
                        r"(?:/jobs/|&token=)(\d+)", re.I)


def _by_jid(job: dict) -> dict | None:
    """A company page showing a Greenhouse job (".../careers/job?gh_jid=123")
    draws the description with a script, so reading the page finds an empty
    shell. Greenhouse has it: try the board names the company is likely
    filed under - the site's name, then the company's."""
    url = job.get("url") or ""
    jid = (parse_qs(urlparse(url).query).get("gh_jid") or [""])[0]
    if not jid.isdigit():
        return None
    host = urlparse(url).netloc.lower().split(":")[0]
    parts = [p for p in host.split(".") if p not in ("www", "careers", "jobs", "boards", "job-boards")]
    name = re.sub(r"[^a-z0-9]", "", (job.get("company") or "").lower())
    tokens = [t for t in dict.fromkeys([parts[0] if parts else "", name,
                                        re.sub(r"(inc|labs|technologies|tech|india)$", "", name)]) if t]
    for tok in tokens:
        try:
            data = net.json(f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs/{jid}?pay_transparency=true")
        except Exception:  # noqa: BLE001 - wrong board name: try the next
            continue
        if data.get("content"):
            return data
    return None


def _api(job: dict) -> str | None:
    if job.get("api"):                    # a fresh list item carries its own
        return job["api"]
    m = _BOARD_URL.search(job.get("url") or "")
    return f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}" if m else None


def _field(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if v)
    return "" if value is None else str(value)


@site("greenhouse", urls=r"greenhouse\.io/|[?&]gh_jid=\d", sources=("greenhouse",))
def read(job: dict) -> Details:
    api = _api(job)
    if api:
        data = net.json(api + ("&" if "?" in api else "?") + "pay_transparency=true")
    else:
        data = _by_jid(job)
    if not data:
        return Details(error="no Greenhouse board has this job")
    d = Details(text=to_text(data.get("content")),
                first_posted=parse_date(data.get("first_published")),
                # the list's own date is kept; this only dates a job that had none
                posted_guess=parse_date(data.get("updated_at")),
                closes_at=parse_date(data.get("application_deadline")),
                location=(data.get("location") or {}).get("name") or "")
    for m in data.get("metadata") or []:
        name, value = str(m.get("name") or ""), _field(m.get("value"))
        if not value:
            continue
        if re.search(r"employment|job type|contract type|worker type|time type", name, re.I):
            d.employment_type = d.employment_type or employment_word(value)
        elif re.search(r"workplace|remote|work (?:type|mode|location|arrangement|model)|location type", name, re.I):
            d.work_mode = d.work_mode or work_mode_word(value)
        elif re.search(r"experience|years", name, re.I) and d.years_min is None:
            d.years_min, d.years_max = years_from_field(value)
        elif re.search(r"salary|compensation|pay range|ctc", name, re.I) and re.search(r"\d", value):
            d.salary = d.salary or value[:60]
    for r in data.get("pay_input_ranges") or []:
        try:
            d.salary = money(r["min_cents"] / 100, r["max_cents"] / 100, r.get("currency_type") or "")
            break
        except (KeyError, TypeError):
            continue
    return d
