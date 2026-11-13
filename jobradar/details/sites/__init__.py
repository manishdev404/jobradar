"""Readers for job sites that publish each posting as data.

One module per site. Each registers itself with @site: the addresses it
reads, matched against the posting's URL, and the boards whose listings it
reads when the URL says nothing. A new site is one small module plus its
name in the import at the bottom.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from ..model import Details


@dataclass(frozen=True)
class Site:
    name: str
    read: Callable[[dict], Details]
    urls: re.Pattern | None
    sources: tuple[str, ...]


SITES: list[Site] = []


def site(name: str, urls: str | None = None, sources: tuple[str, ...] = ()):
    def register(fn: Callable[[dict], Details]):
        SITES.append(Site(name, fn, re.compile(urls, re.I) if urls else None, sources))
        return fn
    return register


def find(job: dict) -> Site | None:
    """The reader for a posting: by its address first - a job listed on a
    Phenom site can live on Workday, a careers page can link Greenhouse -
    then by the board that listed it."""
    url = job.get("url") or ""
    for s in SITES:
        if s.urls and s.urls.search(url):
            return s
    src = job.get("source") or ""
    for s in SITES:
        if src in s.sources:
            return s
    return None


from . import ashby, bamboohr, darwinbox, greenhouse, lever, oracle, smartrecruiters, workday, zoho  # noqa: E402,F401
