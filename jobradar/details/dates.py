"""Dates as boards print them, as epoch seconds."""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y",
            "%d %b, %Y", "%d %B, %Y", "%d-%b-%Y", "%d-%B-%Y", "%Y/%m/%d",
            # numeric dates are day first, as Indian boards write them
            "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y")
# Java's Date.toString(), which SuccessFactors prints: "Wed Nov 11 00:00:00 UTC 2026"
_JAVA = re.compile(r"^[A-Za-z]{3} ([A-Za-z]{3}) (\d{1,2}) [\d:]{8} [A-Za-z+\-\d]+ (\d{4})$")
_ORDINAL = re.compile(r"(\d)(st|nd|rd|th)\b", re.I)


def parse_date(value) -> float | None:
    """Epoch seconds from what boards return: epoch seconds or milliseconds,
    ISO 8601, RFC 2822 ("Mon, 03 Nov 2025 10:00:00 +0000", RSS), Java's
    Date.toString(), "Nov 3, 2025", "3rd November 2025", "03/11/2025".
    None when it is not a plausible date."""
    if value in (None, "", 0) or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _plausible(value / 1000 if value > 1e11 else float(value))
    s = _ORDINAL.sub(r"\1", str(value).strip())
    if re.fullmatch(r"\d{10}(\.\d+)?|\d{13}", s):
        return parse_date(float(s))
    try:
        return _plausible(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        pass
    m = _JAVA.match(s)
    if m:
        s = f"{m.group(1)} {m.group(2)} {m.group(3)}"
    if re.match(r"^[A-Za-z]{3}, \d", s):
        try:
            return _plausible(parsedate_to_datetime(s).timestamp())
        except (TypeError, ValueError):
            pass
    for fmt in _FORMATS:
        try:
            return _plausible(datetime.strptime(s, fmt).timestamp())
        except ValueError:
            continue
    return None


def _plausible(ts: float) -> float | None:
    """Job postings are from this century and not from the future."""
    return ts if 946684800 <= ts <= time.time() + 2 * 365 * 86400 else None


def iso_day(ts: float | None) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d") if ts else ""


_POSTED_ISO = re.compile(r"(?:date ?posted|posted(?: on)?|date opened)\s*[:\-]?\s*"
                         r"(\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+(?:Z|[+-]\d\d:?\d\d)?)?)", re.I)
_POSTED_AGO = re.compile(r"posted\s*[:\-]?\s*(?:on\s*)?(today|just now|yesterday|"
                         r"(\d+)\+?\s*(minute|hour|day|week|month|year)s?\s*ago)", re.I)
_UNIT = {"minute": 60, "hour": 3600, "day": 86400, "week": 7 * 86400,
         "month": 30 * 86400, "year": 365 * 86400}


def posted_from_text(text: str | None, now: float | None = None) -> float | None:
    """When a posting went up, from words a board prints: "Posted 2 Days Ago",
    "Posted 30+ Days Ago" (counted as 30: at least that old), "Posted: 9
    months ago", "Date posted: 2025-12-17". None when it says nothing."""
    text, now = text or "", now or time.time()
    m = _POSTED_ISO.search(text)
    if m:
        return parse_date(m.group(1))
    m = _POSTED_AGO.search(text)
    if not m:
        return None
    word = m.group(1).lower()
    if word in ("today", "just now"):
        return now
    if word == "yesterday":
        return now - 86400
    return now - int(m.group(2)) * _UNIT[m.group(3).lower()]
