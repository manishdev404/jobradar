"""BambooHR: the careers page is drawn by script; /careers/<id>/detail is the
posting as data."""
from __future__ import annotations

import re

from ... import net
from ..dates import parse_date
from ..facts import employment_word
from ..model import Details
from ..text import to_text
from . import site

# BambooHR's location types: in office, remote, hybrid
_MODES = {"0": "onsite", "1": "remote", "2": "hybrid"}


@site("bamboohr", urls=r"\.bamboohr\.com/careers/\d", sources=("bamboohr",))
def read(job: dict) -> Details:
    m = re.match(r"https?://([^/]+)/careers/(\d+)", job.get("url") or "")
    if not m:
        return Details(error="not a BambooHR posting address")
    host, jid = m.groups()
    opening = (net.json(f"https://{host}/careers/{jid}/detail").get("result") or {}).get("jobOpening") or {}
    loc = opening.get("location") or {}
    country = (opening.get("atsLocation") or {}).get("country")
    pay = opening.get("compensation")
    return Details(
        text=to_text(opening.get("description")),
        posted_at=parse_date(opening.get("datePosted")),
        employment_type=employment_word(opening.get("employmentStatusLabel")),
        work_mode=_MODES.get(str(opening.get("locationType")), ""),
        salary=str(pay)[:60] if pay and re.search(r"\d", str(pay)) else "",
        location=", ".join(str(x) for x in (loc.get("city"), loc.get("state"), country) if x),
    )
