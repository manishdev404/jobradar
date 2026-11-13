"""Zoho Recruit: a posting page embeds its whole record as
`var jobs = JSON.parse('...')` - experience, job type, remote flag, salary.
Newer career sites without it are read as a plain page."""
from __future__ import annotations

import json
import re

from ... import net
from .. import page
from ..dates import parse_date
from ..facts import board_skills, employment_word, years_from_field
from ..model import Details
from ..text import js_unescape, to_text
from . import site

_RECORD = re.compile(r"var jobs = JSON\.parse\('((?:[^'\\]|\\.)*)'\)", re.S)


def from_record(rec: dict) -> Details:
    """One Zoho job record - from the posting page or the careers list."""
    low, high = years_from_field(rec.get("Work_Experience"))
    pay = rec.get("Salary")
    skills = rec.get("Required_Skills")
    if isinstance(skills, str):
        skills = [s for s in re.split(r"[,;\n]", skills) if s.strip()]
    return Details(
        text=to_text(rec.get("Job_Description")),
        posted_at=parse_date(str(rec.get("Date_Opened") or "")[:10]),
        years_min=low,
        years_max=high,
        employment_type=employment_word(rec.get("Job_Type")),
        work_mode="remote" if rec.get("Remote_Job") is True else "",
        salary=str(pay)[:60] if pay not in (None, "", "None") and re.search(r"\d", str(pay)) else "",
        skills=board_skills(skills if isinstance(skills, list) else []),
        location=", ".join(str(x) for x in (rec.get("City"), rec.get("State"), rec.get("Country")) if x),
    )


@site("zoho", urls=r"\.zohorecruit\.(?:in|com|eu|com\.au)/", sources=("zoho",))
def read(job: dict) -> Details:
    url = job.get("url") or ""
    # read whole: the record sits near the end of pages that run past 1.5 MB
    _, html = net.page(url, limit=10_000_000)
    m = _RECORD.search(html)
    if not m:
        return page.read(url, html=html)
    return from_record((json.loads(js_unescape(m.group(1))) or [{}])[0])
