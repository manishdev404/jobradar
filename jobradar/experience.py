"""Experience gate: drop postings that ask for more years than you have.

Seniority by title (seniority.py) misses "Software Engineer - 3 to 5 years",
which is most of what a candidate with under two years cannot land. The
requirement lives in the description, so it is read there - locally, before
scoring, so no tokens are spent on jobs that were never a fit.

The rule is deliberately lenient: the LOWEST year figure a posting mentions is
taken as its bar ("1+ years of Python, 3+ overall" reads as 1), and a posting
that states no figure is kept. A wrongly dropped job is invisible; a wrongly
kept one is just scored low.
"""
from __future__ import annotations

import html
import re

_YEARS = re.compile(
    r"(?<![\d.])(\d{1,2})(?:\.\d)?\s*(?:\+|plus)?\s*"
    r"(?:(?:-|–|—|to)\s*\d{1,2}(?:\.\d)?\s*\+?\s*)?"
    r"(?:years?|yrs?)\b",
    re.I,
)
# Company history, not a requirement: "for over 20 years", "9 years back".
_BEFORE_NOISE = re.compile(
    r"(for over|for more than|over the (?:past|last)|for the (?:past|last)|since|"
    r"founded|in business|history of|nearly|almost|degree in|program(?:me)? of|course of)\s*$",
    re.I)
_AFTER_NOISE = re.compile(r"^\s*(back|ago|old|of (?:history|heritage|legacy|growth))", re.I)
_MAX_PLAUSIBLE = 15   # "35 years of experience moving $16 trillion" is the company


def _plain(text: str) -> str:
    text = html.unescape(html.unescape(text or ""))   # some boards double-escape
    return re.sub(r"<[^>]+>", " ", text)


def required_years(title: str, description: str) -> int | None:
    """Lowest years-of-experience figure the posting asks for, or None."""
    text = f"{title or ''}\n{_plain(description)}"
    found = []
    for m in _YEARS.finditer(text):
        n = int(m.group(1))
        if n > _MAX_PLAUSIBLE:
            continue
        if _BEFORE_NOISE.search(text[max(0, m.start() - 30):m.start()]):
            continue
        if _AFTER_NOISE.search(text[m.end():m.end() + 25]):
            continue
        found.append(n)
    return min(found) if found else None


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
