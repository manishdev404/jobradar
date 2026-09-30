"""Picks which of your resumes to send for a job.

Deterministic and free: each resume's keywords are counted in the job title
(weighted) and description. The model is not needed to tell a Spring Boot job
from a React one, and a choice you can predict is one you can trust."""
from __future__ import annotations

import re
from pathlib import Path

from ..resume import extract_text

TITLE_WEIGHT = 4


def _hits(words: list[str], text: str) -> int:
    return sum(len(re.findall(rf"(?<![a-z0-9]){re.escape(w.lower())}(?![a-z0-9])", text))
               for w in words)


def pick(resumes: list[dict], root: Path, title: str, description: str) -> dict:
    """The best resume entry, with 'path', 'text' and 'why' added."""
    if not resumes:
        raise RuntimeError("no resumes listed in private/apply-profile.yaml")
    title_l, desc_l = (title or "").lower(), (description or "").lower()
    best, best_score = None, -1
    scores = []
    for r in resumes:
        kw = [str(k) for k in (r.get("keywords") or [])]
        score = _hits(kw, title_l) * TITLE_WEIGHT + min(_hits(kw, desc_l), 25)
        scores.append((r, score))
        if score > best_score:
            best, best_score = r, score
    default = next((r for r in resumes if r.get("default")), resumes[-1])
    default_score = next(s for r, s in scores if r is default)
    # A job that barely leans (or not at all) gets the general resume.
    if best_score <= 0 or best_score - default_score < 2:
        best = default
    path = Path(best["file"])
    path = path if path.is_absolute() else root / path
    if not path.exists():
        raise RuntimeError(f"resume file missing: {path}")
    why = ", ".join(f"{Path(r['file']).stem}={s}" for r, s in scores)
    return {**best, "path": path, "text": extract_text(path), "why": why}
