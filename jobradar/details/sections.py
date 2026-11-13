"""A posting split into the sections it is written in.

Which section a line sits in changes what it means: "3+ years with Kafka"
under Requirements is a bar, under Nice to have it is not; "React" under
About us is the company's stack, under What you'll need it is a skill asked.

Kinds: about, role, requirements, nice, benefits, other, and intro for the
text before the first heading.
"""
from __future__ import annotations

import re

# Checked in order: "Preferred qualifications" is nice before it is
# requirements, "About the role" is the role before it is about the company.
_KINDS = (
    ("nice", r"nice[- ]to[- ]haves?|good[- ]to[- ]haves?|bonus(?: points| skills)?|pluses|"
             r"preferred(?: qualifications| skills| experience| requirements)?|"
             r"desired (?:skills|qualifications|experience)|desirable(?: skills)?|"
             r"(?:it'?s |it is |it would be |would be )?(?:a )?(?:plus|great|nice|bonus) if(?: you(?: have)?)?|"
             r"additional (?:skills|qualifications)|added advantage|extra credit|optional"),
    ("role", r"(?:about|the) (?:the )?(?:role|job|position|opportunity)|"
             r"job (?:description|summary|overview|purpose|responsibilities)|"
             r"role (?:overview|summary|description|and responsibilities)|position (?:summary|overview)|"
             r"(?:key |main |primary |core |your )?(?:responsibilities|duties|accountabilities)|"
             r"what you(?:'ll| will) (?:do|be doing|work on|own)|what the role (?:involves|offers)|"
             r"day[- ]to[- ](?:day|day responsibilities)|in this role(?: you will)?|your (?:role|impact|mission)|"
             r"the (?:impact|work) you(?:'ll| will) (?:make|have|do)|overview|summary|the role|role|mission"),
    ("requirements",
             r"(?:basic |minimum |required |key |essential |core |mandatory )?(?:requirements|qualifications|"
             r"skills(?: required| needed)?|skill ?set|competencies)|"
             r"(?:skills|qualifications|requirements) (?:and|&) (?:experience|qualifications|skills)|"
             r"must[- ]haves?|what (?:you(?:'ll| will)? need|we(?:'re| are) looking for|you(?:'ll| will)? bring|"
             r"you bring|you have|we need|we expect)|who you are|about you|you(?: have| are| should have)|"
             r"(?:the )?ideal candidate|(?:desired |candidate |ideal candidate )profile|"
             r"eligibility(?: criteria)?|(?:required |relevant )?experience(?: required)?|"
             r"technical (?:skills|requirements|expertise)|education(?: and experience)?|"
             r"(?:to be successful|to succeed) in this role|is this you|requirement"),
    ("benefits", r"(?:our |the |employee )?benefits|perks(?: and benefits)?|what we offer|we offer|"
                 r"why (?:join us|work (?:with|for|at) us|you(?:'ll| will) love (?:it|working) here)|"
                 r"what(?:'s| is) in it for you|compensation(?: and benefits)?|rewards|life at [\w& .-]+"),
    ("about", r"about (?:us|the company|the team|our company|our team|[\w&.'’ -]{2,40})|who we are|"
              r"company (?:overview|description|profile)|our (?:story|mission|culture|company|team)|"
              r"the company|the team"),
    ("other", r"equal (?:opportunity|employment)(?: employer)?|eeo(?: statement)?|how to apply|"
              r"application process|additional information|disclaimer|diversity(?: and inclusion)?|"
              r"hiring process|interview process|location|job details|details"),
)
_MATCHERS = [(kind, re.compile(rf"(?:{pat})", re.I)) for kind, pat in _KINDS]
_BULLET = re.compile(r"^[•\-*·▪◦●–]\s")
_LEAD = re.compile(r"^[\W\d_]+(?=[A-Za-z])")       # "1. ", "### ", "★ "


def kind_of(line: str) -> str | None:
    """The section a heading line opens, or None when the line is not one."""
    s = line.strip()
    if not s or len(s) > 70 or _BULLET.match(s):
        return None
    s = _LEAD.sub("", s).rstrip(" :.-–—").strip()
    if not s or len(s.split()) > 9:
        return None
    for kind, pat in _MATCHERS:
        if pat.fullmatch(s):
            return kind
    return None


def _inline(line: str) -> tuple[str, str] | None:
    """A heading that starts its own content: "Requirements: 3+ years of Go"."""
    m = re.match(r"^([^:•]{3,45}):\s+(\S.*)$", line.strip())
    if m and kind_of(m.group(1)):
        return m.group(1), m.group(2)
    return None


def split(text: str) -> list[tuple[str, str, str]]:
    """[(kind, heading, body)], in order; text before the first heading is 'intro'."""
    out: list[tuple[str, str, list[str]]] = [("intro", "", [])]
    for line in (text or "").split("\n"):
        kind = kind_of(line)
        if kind:
            out.append((kind, line.strip(), []))
            continue
        inline = _inline(line)
        if inline:
            out.append((kind_of(inline[0]), inline[0], [inline[1]]))
            continue
        out[-1][2].append(line)
    return [(k, h, "\n".join(b).strip()) for k, h, b in out if h or "".join(b).strip()]


def without(text: str, kinds: set[str]) -> str:
    """The text minus the sections of these kinds."""
    parts = split(text)
    if len(parts) <= 1:
        return text or ""
    return "\n".join(f"{h}\n{b}" if h else b for k, h, b in parts if k not in kinds).strip()


def only(text: str, kinds: set[str]) -> str:
    return "\n".join(b for k, _, b in split(text) if k in kinds).strip()
