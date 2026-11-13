"""Ashby: the board's list holds each whole posting, with work mode, employment
type and the pay summary the company chose to show."""
from __future__ import annotations

import re

from ... import net
from ..dates import parse_date
from ..facts import employment_word, work_mode_word
from ..model import Details
from ..text import to_text
from . import site


def from_posting(p: dict) -> Details:
    comp = p.get("compensation") or {}
    pay = comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary") or ""
    if p.get("shouldDisplayCompensationOnJobPostings") is False:
        pay = ""
    return Details(
        text=to_text(p.get("descriptionHtml") or p.get("descriptionPlain")),
        posted_at=parse_date(p.get("publishedAt")),
        work_mode=work_mode_word(p.get("workplaceType")) or ("remote" if p.get("isRemote") else ""),
        employment_type=employment_word(p.get("employmentType")),
        salary=str(pay)[:60],
        location=p.get("location") or "",
    )


@site("ashby", urls=r"jobs\.ashbyhq\.com/", sources=("ashby",))
def read(job: dict) -> Details:
    m = re.search(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})", job.get("url") or "")
    if not m:
        return Details(error="not an Ashby posting address")
    org, posting = m.groups()
    data = net.json(f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true")
    for p in data.get("jobs") or []:
        if p.get("id") == posting or posting in (p.get("jobUrl") or ""):
            return from_posting(p)
    return Details(error="the posting is no longer on its Ashby board")
