"""Experience gate: drop postings that ask for more years than you have.

Seniority by title (seniority.py) misses "Software Engineer - 3 to 5 years",
which is most of what a candidate with under two years cannot land. The
requirement lives in the description, so it is read there - locally, before
scoring, so no tokens are spent on jobs that were never a fit.

The bar is the STRICTEST requirement the posting states, because a candidate
has to meet all of them:
  * each requirement counts by the low end of its range: "0-3 years" is 0,
    "1-2 years" is 1, "2+ years" is 2, "3 to 5 years" is 3;
  * with several requirements the highest wins: "1+ years of Python, 3+ years
    overall" is 3;
  * nice-to-haves do not count: "5+ years preferred", "a plus", "bonus";
  * a level in the title is a requirement too: "Engineer II" is 2.
With max_required_years: 1 (config.yaml) that keeps 0-2, 1-2 and 0-3 and drops
2+, 2-4 and 3+ - i.e. only roles open to under two years of experience.

A posting that states no figure at all is kept: many fresher and SDE-1 roles
never mention years, and the scorer still reads the whole posting.
"""
from __future__ import annotations

import html
import re

_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_NUM = r"(\d{1,2}|" + "|".join(_WORDS) + r")"
_YEARS = re.compile(
    rf"(?<![\d.])\b{_NUM}(?:\.\d)?\s*\)?\s*(?:\+|plus|or more)?\s*"   # ")" for "two (2) years"
    # the range, also written out: "1 year to 3 years" is 1, not 1 and 3
    r"(?:(?:(?:years?|yrs?)\s*)?(?:-|–|—|to)\s*(?:\d{1,2}|" + "|".join(_WORDS) + r")(?:\.\d)?\s*\+?\s*)?"
    r"-?\s*(?:years?|yrs?)\b",          # "3-year experience" as well as "3 years"
    re.I,
)
# Company history, not a requirement: "for over 20 years", "9 years back".
_BEFORE_NOISE = re.compile(
    r"(for over|for more than|over the (?:past|last)|for the (?:past|last)|since|"
    r"founded|in business|history of|nearly|almost|degree in|program(?:me)? of|course of)\s*$",
    re.I)
# Whole words: "4-5 years\nBACKEND (Node.js)" is a requirement, not "years back".
_AFTER_NOISE = re.compile(r"^\s*(back|ago|old|of (?:history|heritage|legacy|growth)|"
                          r"degree|program|programme|course|contract|term|warranty)\b|"
                          # "a 15 years full time education is required" is schooling -
                          # no word end: boards glue it on ("educationSummary")
                          r"^\s*(?:of\s+)?(?:full[- ]time\s+|regular\s+)?(?:education|schooling)", re.I)
_MAX_PLAUSIBLE = 15   # "35 years of experience moving $16 trillion" is the company


# Block tags end a line: without this every bullet of a posting runs together,
# and a "Nice to have" two bullets away cancels the "5+ years" requirement.
_BLOCK = re.compile(r"<\s*(?:br|/?p|/?li|/?div|/?h[1-6]|/?tr|/?ul|/?ol|/?section)\b[^>]*>", re.I)
_ODD_SPACE = re.compile(r"[\xa0  ​⁠﻿]")


def _plain(text: str) -> str:
    text = html.unescape(html.unescape(text or ""))   # some boards double-escape
    text = _BLOCK.sub("\n", text)
    return _ODD_SPACE.sub(" ", re.sub(r"<[^>]+>", " ", text))


# Level markers in a title are an experience bar even when no years are stated:
# "Software Engineer II", "SDE 2", "Intermediate Developer" are 2+ year roles.
# Digits only count right after a role word, so "Intern 2027" is not level 2.
_ROLE = r"(?:engineer|developer|sde|swe|programmer|analyst|consultant|professional)"
_TITLE_LEVELS = (
    (re.compile(rf"\b(?:iii|iv)\b|\b{_ROLE}\s*[-,]?\s*[3-9]\b|\bl[4-9]\b", re.I), 3),
    (re.compile(rf"\bii\b|\b{_ROLE}\s*[-,]?\s*2\b|\bl3\b|"
                r"\b(?:intermediate|advanced|experienced|mid[- ]?level|mid[- ]?senior)\b", re.I), 2),
)


def title_level_years(title: str) -> int | None:
    for pattern, years in _TITLE_LEVELS:
        if pattern.search(title or ""):
            return years
    return None


def required_years(title: str, description: str) -> int | None:
    """Years of experience the posting asks for, or None when it never says.
    A level in the title and the text's requirement are both bars; the higher
    wins ("Engineer II ... 1+ years of Go" is still level 2)."""
    level = title_level_years(title)
    text_years = _text_years(title, description)
    bars = [y for y in (level, text_years) if y is not None]
    return max(bars) if bars else None


_EXP_WORD = re.compile(r"experience|\bexp\b|experienced|work history|industry", re.I)
_BREAK = re.compile(r"[.;•\n](?:\s|$)|\n")


def _sentence(text: str, start: int, end: int) -> str:
    """The sentence or bullet around a match: "Experience: 3+ years." and the
    next line "1 year of Kafka" are separate claims."""
    before = [m.end() for m in _BREAK.finditer(text, max(0, start - 200), start)]
    after = _BREAK.search(text, end)
    return text[before[-1] if before else max(0, start - 200):
                after.start() if after else end + 200]


# A figure is a nice-to-have only when its OWN clause says so: "5+ years
# preferred", "2+ years of Go is a plus". The clause ends at a comma, so in
# "4-6 years of experience, ideally with React" the "ideally" is about React.
_CLAUSE_END = re.compile(r"[,;()\n•]|\.(?:\s|$)")
_OPTIONAL = re.compile(r"preferred|a plus|nice[- ]to[- ]have|bonus|desirable|"
                       r"advantage|good[- ]to[- ]have", re.I)
# These qualify what follows them, so they only soften a figure that comes
# after: "ideally 3+ years" is soft, "7-10 years preferably in Java" is not.
_OPTIONAL_BEFORE = re.compile(r"ideally|preferably|optionally", re.I)
# The figure after the words: "Years of experience: 3+", "Experience (years): 2-4".
_YEARS_AFTER = re.compile(
    rf"\b(?:years?|yrs?)\s+of\s+(?:\w+\s+)?experience\s*(?:required)?\s*[:\-–—]\s*{_NUM}\b"
    r"(?!\s*(?:%|months?))",
    re.I,
)
# Keka and others print a bare field with no unit: "Experience: 5-6",
# "Experience: 4 +", "Experience (in years): 3", "Experience: 01". The low end counts.
_EXP_FIELD = re.compile(
    rf"(?:^|\n)\s*(?:work\s+|total\s+|relevant\s+)?experience\s*(?:\((?:in\s+)?(?:years?|yrs?)\))?"
    rf"\s*[:\-–—]\s*{_NUM}\s*(?:\+|(?:-|–|—|to)\s*\d{{1,2}})?\s*(?:\+)?\s*(?:years?|yrs?)?\s*(?=$|\n|[,.;])",
    re.I,
)


def _clause(text: str, start: int, end: int) -> tuple[str, str]:
    """(before, after) the figure, within its clause."""
    lo = max(0, start - 120)
    cuts = [m.end() for m in _CLAUSE_END.finditer(text, lo, start)]
    stop = _CLAUSE_END.search(text, end)
    return text[cuts[-1] if cuts else lo:start], text[end:stop.start() if stop else end + 120]


def _is_optional(text: str, start: int, end: int) -> bool:
    before, after = _clause(text, start, end)
    return bool(_OPTIONAL.search(before) or _OPTIONAL.search(after)
                or _OPTIONAL_BEFORE.search(before))


def _text_years(title: str, description: str) -> int | None:
    """The strictest requirement stated: the highest low-end across the
    posting's required figures. Figures in an experience sentence ("3+ years
    of experience") win over stray ones elsewhere; nice-to-haves never count."""
    text = f"{title or ''}\n{_plain(description)}"
    stated, stray = [], []
    for m in _YEARS.finditer(text):
        raw = m.group(1).lower()
        n = _WORDS[raw] if raw in _WORDS else int(raw)
        if n > _MAX_PLAUSIBLE:
            continue
        if _BEFORE_NOISE.search(text[max(0, m.start() - 30):m.start()]):
            continue
        if _AFTER_NOISE.search(text[m.end():m.end() + 25]):
            continue
        if _is_optional(text, m.start(), m.end()):
            continue
        sentence = _sentence(text, m.start(), m.end())
        (stated if _EXP_WORD.search(sentence) else stray).append(n)
    for m in _YEARS_AFTER.finditer(text):
        raw = m.group(1).lower()
        n = _WORDS[raw] if raw in _WORDS else int(raw)
        if n <= _MAX_PLAUSIBLE and not _is_optional(text, m.start(), m.end()):
            stated.append(n)
    for m in _EXP_FIELD.finditer(text):
        raw = m.group(1).lower()
        n = _WORDS[raw] if raw in _WORDS else int(raw)
        if n <= _MAX_PLAUSIBLE:
            stated.append(n)
    found = stated or stray
    return max(found) if found else None


def experience_label(title: str, description: str) -> str:
    """What the posting asks for, in words, for the UI and your history."""
    need = required_years(title, description)
    if need is None:
        return "not stated"
    return "fresher (0 yrs)" if need == 0 else f"{need}+ yrs"


def too_senior(job: dict, max_years: int | None) -> bool:
    """True when the posting's stated bar is above max_years."""
    if max_years is None:
        return False
    need = required_years(job.get("title", ""), job.get("description", ""))
    return need is not None and need > max_years


# Too ambiguous to count from prose: "Section C", "go live", "the API team".
_AMBIGUOUS = {"c", "go", "api", "rest", "git", "node", "spring", "express", "rag"}
# Specific enough to count from prose, and missing from localmatch's resume vocabulary.
_EXTRA = {"spring boot", "hibernate", "jpa", "redux", "firebase", ".net", "express.js",
          "react.js", "rest api", "rest apis", "system design", "data structures"}


def job_skills(title: str, description: str, limit: int = 8) -> list[str]:
    """Known skills a posting names, in the order it first names them."""
    from .localmatch import VOCAB, _mentions
    text = f"{title or ''}\n{_plain(description)}".lower()
    text = re.sub(r"[.,;:](?=\s|$)", " ", text)   # "...on AWS." must still count
    hits = []
    for skill in (VOCAB | _EXTRA) - _AMBIGUOUS:
        if _mentions(text, skill):
            hits.append((text.find(skill), skill))
    return [s for _, s in sorted(hits)][:limit]
