"""Skill gate: jobs the role list misses, kept when they ask for your skills.

The role gate (roles.py) reads titles only, so a posting whose title names
none of your roles is dropped unread - "Product Engineer", "Engineer I",
"Member of Technical Staff - Platform", "Associate Engineer" - even when its
description asks for Node.js, React and PostgreSQL.

This gate gives such titles a second chance in two steps:
  1. candidate  - the title says it is an engineering/developer job
                  (`title_words`) and not one in another field (`exclude_words`:
                  QA, sales, hardware, data, mobile...). Only a title check, so
                  it runs with the other cheap checks, before any page is read.
  2. match      - once the full description has been read, the posting must
                  name at least `min_skills` of your top skills (config.yaml
                  `skills.top`, or `skill_match.skills`). Matching uses the
                  same skill names and aliases as the skill report (skill.py).

A job that passes is kept under the role `name` ("Skill match") with its own
priority and bonus, so it sorts after the roles you named yourself. The scorer
still reads it in full and judges the fit like any other job.
"""
from __future__ import annotations

import re

from .experience import _plain
from .skill import find_skills, resolve_skill

DEFAULTS = {
    "enabled": False,
    "name": "Skill match",
    "priority": 6,
    "bonus": 0,
    "min_skills": 3,
}


def settings(cfg_filters: dict | None) -> dict | None:
    """The `filters.skill_match` block with defaults filled in, or None when off."""
    raw = (cfg_filters or {}).get("skill_match") or {}
    if not raw.get("enabled"):
        return None
    return {**DEFAULTS, **raw}


def _has_word(title: str, words: list[str]) -> bool:
    t = re.sub(r"\s+", " ", (title or "").lower())
    # Whole words, plural allowed: "Engineers" is "engineer", "Engineering" is not.
    return any(re.search(rf"(?<![a-z0-9]){re.escape(w.lower())}s?(?![a-z0-9])", t)
               for w in words or [])


def is_candidate(title: str, sm: dict | None) -> bool:
    """A title worth reading the posting for: engineering, not another field."""
    if not sm:
        return False
    return _has_word(title, sm.get("title_words")) and not _has_word(title, sm.get("exclude_words"))


def wanted_skills(cfg: dict, sm: dict | None) -> set[str]:
    """Skill names a posting is matched against: skill_match.skills, else skills.top."""
    names = (sm or {}).get("skills") or (cfg.get("skills") or {}).get("top") or []
    return {s for s in (resolve_skill(n) for n in names) if s}


# Aliases that are fine in a resume but plain English in a posting: "American
# Express", "express your interest". In a posting these count only spelled out.
_STRICT = {"Express.js": re.compile(r"\bexpress\s?\.?\s?js\b", re.I)}


def skills_named(job: dict, wanted: set[str]) -> set[str]:
    """Which of `wanted` the posting names, in its title or description."""
    text = f"{job.get('title') or ''}\n{_plain(job.get('description') or '')}"
    return {s for s in find_skills(text) & wanted
            if s not in _STRICT or _STRICT[s].search(text)}


def role(job: dict, sm: dict | None, wanted: set[str]) -> dict | None:
    """The skill-match role for a candidate job that names enough of your skills."""
    if not sm or not wanted or not is_candidate(job.get("title") or "", sm):
        return None
    if len(skills_named(job, wanted)) < int(sm.get("min_skills", 3)):
        return None
    return {"role": sm["name"], "role_rank": int(sm["priority"]),
            "role_bonus": float(sm["bonus"])}
