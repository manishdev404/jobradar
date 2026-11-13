"""Darwinbox: the posting's own endpoint has the description its list lacks,
plus experience, salary and a remote flag."""
from __future__ import annotations

import html
import re

from ... import net
from ..dates import parse_date
from ..facts import employment_word, money
from ..model import Details
from ..text import to_text
from . import site


def _years(months) -> int | None:
    try:
        return int(float(months)) // 12
    except (TypeError, ValueError):
        return None


@site("darwinbox", urls=r"\.darwinbox\.(?:in|com)/ms/candidate", sources=("darwinbox",))
def read(job: dict) -> Details:
    m = re.match(r"https://([^/]+)/ms/candidate/careers/([^/?#]+)", job.get("url") or "")
    if not m:
        return Details(error="not a Darwinbox posting address")
    host, jid = m.groups()
    data = net.json(f"https://{host}/ms/candidateapi/job/{jid}")
    j = ((data.get("message") or {}).get("job") or [{}])[0]
    # The detail endpoint counts MONTHS (60-120) where the list says years (5-10).
    low, high = _years(j.get("experience_from_num")), _years(j.get("experience_to_num"))
    pay = money(j.get("salary_min"), j.get("salary_max"), j.get("salary_currency") or "",
                j.get("salary_timeframe") or "") or str(j.get("salary_range") or "")[:60]
    return Details(
        text=to_text(html.unescape(j.get("jd") or "")),
        posted_at=parse_date(j.get("job_posting_on") or j.get("posted_on")),
        years_min=low,
        years_max=high if high is not None and low is not None and high >= low else None,
        work_mode="remote" if str(j.get("is_remote")) == "1" else "",
        employment_type=employment_word(j.get("emp_type")),
        salary=pay,
    )
