"""Workday: every posting page has a JSON twin under /wday/cxs/. Its list says
only "Posted 30+ Days Ago"; the posting has the exact date."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from ... import net
from ..dates import parse_date, posted_from_text
from ..facts import employment_word, work_mode_word
from ..model import Details
from ..text import to_text
from . import site


def api_url(url: str) -> str | None:
    u = urlparse(url)
    path = re.sub(r"^/[a-z]{2}-[A-Z]{2}(?=/)", "", u.path)          # a locale: /en-US/Careers/job/...
    if u.netloc.endswith(".myworkdaysite.com"):
        m = re.match(r"^/recruiting/([^/]+)(/.+)$", path)          # /recruiting/<tenant>/<site>/job/...
        if not m:
            return None
        tenant, rest = m.groups()
    else:
        tenant, rest = u.netloc.split(".")[0], path
    return f"https://{u.netloc}/wday/cxs/{tenant}{rest}" if "/job/" in rest else None


@site("workday", urls=r"\.myworkday(?:jobs|site)\.com/", sources=("workday",))
def read(job: dict) -> Details:
    api = api_url(job.get("url") or "")
    if not api:
        return Details(error="not a Workday posting address")
    info = net.json(api).get("jobPostingInfo") or {}
    places = [info.get("location") or ""] + [str(x) for x in info.get("additionalLocations") or []]
    return Details(
        text=to_text(info.get("jobDescription")),
        posted_at=parse_date(info.get("startDate")),
        posted_guess=posted_from_text(info.get("postedOn")),
        employment_type=employment_word(info.get("timeType")),
        work_mode=work_mode_word(info.get("remoteType")),
        location="; ".join(p for p in places if p)[:200],
    )
