"""Lever: the list already holds each whole posting; the same shape answers
for one posting a careers page links to (jobs.lever.co/<site>/<id>)."""
from __future__ import annotations

import re

from ... import net
from ..dates import parse_date
from ..facts import employment_word, money, work_mode_word
from ..model import Details
from ..text import to_text
from . import site


def from_posting(p: dict) -> Details:
    """One Lever posting. descriptionPlain is only the intro; the requirements
    ("2+ years of...") live in `lists`, each under its own heading."""
    parts = [to_text(p.get("description") or p.get("descriptionPlain"))]
    for block in p.get("lists") or []:
        body = to_text(block.get("content"))
        if body:
            parts.append(f"{(block.get('text') or '').strip()}\n{body}".strip())
    parts.append(to_text(p.get("additional") or p.get("additionalPlain")))
    cats = p.get("categories") or {}
    pay = p.get("salaryRange") or {}
    return Details(
        text="\n\n".join(x for x in parts if x),
        posted_at=parse_date(p.get("createdAt")),
        work_mode=work_mode_word(p.get("workplaceType")),
        employment_type=employment_word(cats.get("commitment")),
        salary=money(pay.get("min"), pay.get("max"), pay.get("currency") or "", pay.get("interval") or "")
        if pay else "",
        location=cats.get("location") or "",
    )


@site("lever", urls=r"jobs\.(?:eu\.)?lever\.co/", sources=("lever",))
def read(job: dict) -> Details:
    m = re.search(r"jobs\.(eu\.)?lever\.co/([^/?#]+)/([0-9a-f-]{36})", job.get("url") or "")
    if not m:
        return Details(error="not a Lever posting address")
    eu, company, posting = m.groups()
    return from_posting(net.json(f"https://api.{eu or ''}lever.co/v0/postings/{company}/{posting}"))
