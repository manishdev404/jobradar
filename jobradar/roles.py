"""Target roles, from config.yaml `filters.roles`.

Each role names the title words that identify it, a priority (1 = most wanted)
and an optional rank bonus. A job whose title matches no role is dropped before
scoring. Roles are tried in config order, so list the specific ones (backend,
full stack, frontend) before the catch-all (software engineer): "Software
Engineer - Frontend" is a frontend role, not a generic one.

Priority and bonus only change ORDER - lists and emails sort by score + bonus,
then priority. The stored model score is never altered, so re-running `recap`
cannot compound a penalty.
"""
from __future__ import annotations

import re


def _matches(title: str, words: list[str]) -> bool:
    # Boards pad titles: "Software  Engineer" (two spaces) is a software engineer.
    t = re.sub(r"\s+", " ", title.lower())
    # A plural is the same role: "Node Js Developers", "Backend Engineers".
    return any(re.search(rf"(?<![a-z]){re.escape(w.lower())}s?(?![a-z])", t) for w in words)


def classify(title: str, roles: list[dict] | None) -> dict | None:
    """The first configured role whose words appear in the title, or None."""
    for role in roles or []:
        if _matches(title or "", role.get("match") or []):
            return {"role": role["name"], "role_rank": int(role.get("priority", 9)),
                    "role_bonus": float(role.get("bonus", 0))}
    return None
