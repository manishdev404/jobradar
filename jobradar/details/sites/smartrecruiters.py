"""SmartRecruiters: its posting pages carry little readable text, but every
posting is public data - sections, employment type, remote/hybrid flags."""
from __future__ import annotations

import re

from ... import net
from ..dates import parse_date
from ..facts import employment_word
from ..model import Details
from ..text import to_text
from . import site


@site("smartrecruiters", urls=r"smartrecruiters\.com/", sources=("smartrecruiters",))
def read(job: dict) -> Details:
    m = re.search(r"smartrecruiters\.com/([^/?#]+)/(\d+)", job.get("url") or "")
    if not m:
        return Details(error="not a SmartRecruiters posting address")
    company, posting = m.groups()
    data = net.json(f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting}")
    sections = (data.get("jobAd") or {}).get("sections") or {}
    parts = []
    for key, head in (("companyDescription", "About the company"), ("jobDescription", "Job Description"),
                      ("qualifications", "Qualifications"), ("additionalInformation", "Additional Information")):
        sec = sections.get(key) or {}
        body = to_text(sec.get("text"))
        if body:
            parts.append(f"{head}\n{body}")
    loc = data.get("location") or {}
    mode = "remote" if loc.get("remote") else "hybrid" if loc.get("hybrid") else ""
    kind = employment_word((data.get("typeOfEmployment") or {}).get("label"))
    if (data.get("experienceLevel") or {}).get("id") == "internship":
        kind = "internship"
    return Details(
        text="\n\n".join(parts),
        posted_at=parse_date(data.get("releasedDate")),
        employment_type=kind,
        work_mode=mode,
        location=", ".join(str(x) for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x),
    )
