"""Oracle Recruiting Cloud. The list's ShortDescriptionStr is usually about
the company, so the posting itself is always read: responsibilities,
qualifications, dates, the on-site/hybrid/remote code and a skills list."""
from __future__ import annotations

import re

from ... import net
from ..dates import parse_date
from ..facts import board_skills, employment_word, work_mode_word
from ..model import Details
from ..text import to_text
from . import site

_URL = re.compile(r"https://([^/]+)/hcmUI/CandidateExperience/[^/]+/sites/([^/]+)/job/([^/?#]+)")


def _skills(items) -> list[str]:
    names = []
    for item in items or []:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            for key in ("Skill", "SkillName", "Name", "name", "skill", "Description"):
                if isinstance(item.get(key), str) and item[key].strip():
                    names.append(item[key])
                    break
    return board_skills(names)


@site("oracle", urls=r"\.oraclecloud\.com/hcmUI/", sources=("oracle",))
def read(job: dict) -> Details:
    m = _URL.match(job.get("url") or "")
    if not m:
        return Details(error="not an Oracle posting address")
    host, site_no, rid = m.groups()
    data = net.json(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
                    f'?expand=all&onlyData=true&finder=ById;Id="{rid}",siteNumber={site_no}')
    item = (data.get("items") or [{}])[0]
    parts = [to_text(item.get("ExternalDescriptionStr"))]
    for key, head in (("ExternalResponsibilitiesStr", "Responsibilities"),
                      ("ExternalQualificationsStr", "Qualifications")):
        body = to_text(item.get(key))
        if body:
            # the HTML often opens with its own "Responsibilities" line
            parts.append(body if body.lower().startswith(head.lower()) else f"{head}\n{body}")
    text = "\n\n".join(p for p in parts if p)
    if len(text) < 300:                       # nothing but the company blurb: still better than nothing
        text = "\n\n".join(p for p in (text, to_text(item.get("ShortDescriptionStr"))) if p)
    return Details(
        text=text,
        posted_at=parse_date(item.get("ExternalPostedStartDate")),
        closes_at=parse_date(item.get("ExternalPostedEndDate")),
        employment_type=employment_word(item.get("JobSchedule")),
        work_mode=work_mode_word(item.get("WorkplaceTypeCode") or item.get("WorkplaceType")),
        skills=_skills(item.get("skills")),
        location=item.get("PrimaryLocation") or "",
    )
