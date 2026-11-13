"""Facts read locally from a posting - no model, no network.

Each fact comes from the board's own field when there is one (the readers set
those); these fill what is left from the posting's location and text."""
from __future__ import annotations

import re

from . import sections
from .text import to_text

# --- experience --------------------------------------------------------------

_NONE = {"", "none", "null", "na", "n/a", "-", "not stated", "not specified", "any", "0-0"}


def years_from_field(value) -> tuple[int | None, int | None]:
    """A board's own experience field as (low, high) years: "1-3 years",
    "2", "4 +", "Fresher", "6 months", "0 - 2 Yrs". (None, None) when it does
    not say. Read with the experience gate's own parser, so a field and the
    same words in a description always agree."""
    from ..experience import _text_years
    s = re.sub(r"\s+", " ", str(value if value is not None else "")).strip().lower()
    if s in _NONE:
        return None, None
    if re.search(r"\bfresher|\bentry[- ]level|\bno experience|\bfresh graduate", s) and not re.search(r"\d", s):
        return 0, 0
    if re.fullmatch(r"\d{1,2}(?:\.\d+)?", s):                 # a bare number is years (Keka)
        n = int(float(s))
        return n, None
    if re.search(r"\bmonths?\b", s) and not re.search(r"\byears?|yrs?\b", s):
        return 0, 0                                            # "6 months": under a year
    low = _text_years("", f"Experience: {s}")
    if low is None:
        return None, None
    m = re.search(r"\d{1,2}\s*(?:\+|plus)?\s*(?:years?|yrs?)?\s*(?:-|–|—|to)\s*(\d{1,2})", s)
    high = int(m.group(1)) if m and int(m.group(1)) >= low else None
    return low, high


def years_label(low: int | None) -> str:
    if low is None:
        return "not stated"
    return "fresher (0 yrs)" if low == 0 else f"{low}+ yrs"


# --- work mode ---------------------------------------------------------------

def work_mode_word(value) -> str:
    """A board's own work-mode value: Oracle's ORA_HYBRID, Ashby's OnSite,
    Lever's onsite, Workday's remoteType, a bare True for "is remote"."""
    if value is True:
        return "remote"
    if isinstance(value, dict):
        value = value.get("label") or value.get("name") or value.get("id") or ""
    if not isinstance(value, str):
        return ""
    v = re.sub(r"[^a-z]", "", value.lower())
    if not v or v in ("unspecified", "none", "false", "0", "notspecified"):
        return ""
    if "hybrid" in v or "flexible" in v:
        return "hybrid"
    if "remote" in v or "telecommute" in v or "wfh" in v or "workfromhome" in v or "anywhere" in v:
        return "remote"
    if "onsite" in v or "office" in v or "inperson" in v or "site" in v:
        return "onsite"
    return ""


# Words in a description only count in phrasing about where the work happens:
# "hybrid cloud", "remote debugging" and "onsite interviews" say nothing.
_MODE_FIELD = r"(?:work(?:ing|place)?\s*(?:mode|model|type|location|arrangement|style)|location\s*type|job\s*type)\s*[:\-–]\s*"
_HYBRID_TEXT = re.compile(
    rf"{_MODE_FIELD}hybrid\b|\bhybrid\s+(?:work(?:ing)?|role|model|mode|position|set[- ]?up|arrangement|"
    r"schedule|policy|opportunity|job|environment)\b|\(hybrid\)|\bthis (?:is a|role is) hybrid\b|"
    r"\b\d\s*days?\s+(?:a|per)\s+week\s+(?:in|from)\s+(?:the\s+)?office\b", re.I)
_REMOTE_TEXT = re.compile(
    rf"{_MODE_FIELD}remote\b|\b(?:fully|100%|completely|entirely)\s+remote\b|\bremote[- ](?:first|only)\b|"
    r"\bremote\s+(?:role|position|job|opportunity)\b|\bwork(?:ing)?\s+(?:from\s+home|remotely)\b|"
    r"\(remote\)|\bthis (?:is a|role is) remote\b", re.I)
_ONSITE_TEXT = re.compile(
    rf"{_MODE_FIELD}(?:on[- ]?site|in[- ]office|office)\b|\b(?:on[- ]?site|in[- ]office)\s+(?:role|position|job|only)\b|"
    r"\bwork(?:ing)?\s+from\s+(?:the\s+)?office\b|\bwfo\b|\b(?:5|five)\s+days?\s+(?:a|per)\s+week\s+(?:in|from)\s+"
    r"(?:the\s+)?office\b|\(on[- ]?site\)|\bthis (?:is an|role is) on[- ]?site\b", re.I)


def work_mode(location: str, text: str) -> str:
    """remote | hybrid | onsite, from the location the board gives first (its
    own word), else from how the description talks about where the work is."""
    loc = location or ""
    if re.search(r"\bhybrid\b", loc, re.I):
        return "hybrid"
    if re.search(r"\bremote\b|\bwork from home\b|\banywhere\b", loc, re.I):
        return "remote"
    if re.search(r"\bon[- ]?site\b|\bin[- ]office\b", loc, re.I):
        return "onsite"
    body = text or ""
    for mode, pat in (("hybrid", _HYBRID_TEXT), ("remote", _REMOTE_TEXT), ("onsite", _ONSITE_TEXT)):
        if pat.search(body):
            return mode
    return ""


# --- employment type ---------------------------------------------------------

def employment_word(value) -> str:
    """FULL_TIME, "Full time", "FullTime", "permanent", "Onroll Regular",
    "CONTRACTOR", "Intern" -> full-time / part-time / contract / temporary /
    internship; "" when it is something else (Keka's numeric codes)."""
    if isinstance(value, (list, tuple)):
        value = next((v for v in value if v), "")
    if isinstance(value, dict):
        value = value.get("label") or value.get("name") or ""
    if not isinstance(value, str):
        return ""
    v = re.sub(r"[^a-z]", "", value.lower())
    if not v or v.isdigit():
        return ""
    if "intern" in v or "apprentice" in v or "trainee" in v:
        return "internship"
    if "part" in v:
        return "part-time"
    if "contract" in v or "freelance" in v or "consultant" in v:
        return "contract"
    if "temp" in v or "fixedterm" in v or "seasonal" in v:
        return "temporary"
    if "full" in v or "permanent" in v or "regular" in v or v in ("fte", "employee", "onroll"):
        return "full-time"
    return ""


def employment_from_title(title: str) -> str:
    t = title or ""
    if re.search(r"\bintern(?:ship)?\b|\btrainee\b|\bapprentice", t, re.I):
        return "internship"
    if re.search(r"\b(?:contract(?:ual)?|freelance|c2h|contract[- ]to[- ]hire)\b", t, re.I):
        return "contract"
    if re.search(r"\bpart[- ]time\b", t, re.I):
        return "part-time"
    return ""


# --- salary --------------------------------------------------------------------

_SYMBOL = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}
# "YEAR", "Annual", Lever's "per-year-salary", Darwinbox's "Annual"
_PER = (("YEAR", "/yr"), ("ANNUAL", "/yr"), ("MONTH", "/mo"), ("WEEK", "/wk"), ("HOUR", "/hr"),
        ("DAY", "/day"))


def money(low, high=None, currency: str = "", unit: str = "") -> str:
    """₹1.8L–4L /yr, $120k–150k /yr. Lakhs for rupees, as Indian postings write them."""
    def num(v):
        try:
            return float(str(v).replace(",", ""))
        except (TypeError, ValueError):
            return None
    lo, hi = num(low), num(high)
    if not lo and not hi:
        return ""
    cur = (currency or "").upper()
    sym = _SYMBOL.get(cur, f"{cur} " if cur else "")

    def fmt(v):
        if sym == "₹" and v >= 100000:
            return f"{v / 100000:.1f}".rstrip("0").rstrip(".") + "L"
        if v >= 1000:
            return f"{v / 1000:.0f}k"
        return f"{v:g}"
    if lo and hi and hi != lo:
        amount = f"{sym}{fmt(lo)}–{fmt(hi)}"
    else:
        amount = f"{sym}{fmt(lo or hi)}"
    u = (unit or "").upper()
    per = next((p for word, p in _PER if word in u), "")
    return f"{amount} {per}".strip()


_AMOUNT = r"(?:₹|inr|rs\.?|\$|usd)?\s?\d[\d,]*(?:\.\d+)?\s?(?:k|l|lakhs?|lacs?|lpa|cr|crores?)?"
_SALARY = re.compile(
    rf"(?:{_AMOUNT}\s?(?:-|–|—|to)\s?{_AMOUNT}|(?:₹|inr|rs\.?)\s?\d[\d,]*(?:\.\d+)?\s?(?:k|l|lakhs?|lacs?|lpa)?)"
    r"(?:\s?(?:per annum|p\.?a\.?|/\s?(?:year|yr|annum|month|mo)|per (?:year|month)|annually|lpa))?", re.I)
_PAY_WORD = re.compile(r"salary|ctc|compensation|stipend|pay(?:\s+range|\s+scale)?\b|package|lpa|per annum|₹|\binr\b", re.I)


def salary_in_text(text: str) -> str:
    """A pay range the posting states in words, on a line about pay:
    "CTC: 8-12 LPA", "Stipend: ₹25,000/month", "Salary: INR 10,00,000 - 15,00,000"."""
    for line in (text or "").split("\n"):
        if not _PAY_WORD.search(line):
            continue
        for m in _SALARY.finditer(line):
            got = m.group(0).strip(" ,.;")
            if re.search(r"\d", got) and (re.search(r"[₹$]|inr|rs\.?|lpa|lakh|lac|k\b|per annum|/", got, re.I)
                                          or re.search(r"salary|ctc|stipend|pay", line, re.I)):
                if re.fullmatch(r"\d{1,2}", got) or re.fullmatch(r"\d{4}", got):
                    continue                     # "5 days", a year
                return got[:60]
    return ""


# --- skills ----------------------------------------------------------------------

_NOT_ASKED = {"nice", "benefits", "about", "other"}


def skills_split(text: str) -> tuple[list[str], list[str]]:
    """(must-have, nice-to-have) skills in page order. A skill named only
    under a Nice-to-have heading is nice; the company's About section and the
    benefits do not count. A posting with no headings asks for all it names."""
    from ..skill import skills_in_order
    parts = sections.split(text)
    asked = "\n".join(b for k, _, b in parts if k not in _NOT_ASKED)
    nice_text = "\n".join(b for k, _, b in parts if k == "nice")
    must = skills_in_order(asked)
    nice = [s for s in skills_in_order(nice_text) if s not in must]
    return must, nice


def board_skills(names: list[str]) -> list[str]:
    """A board's own skill list, in the skill report's names where it knows them."""
    from ..skill import resolve_skill
    out = []
    for n in names or []:
        name = resolve_skill(str(n)) or str(n).strip()
        if name and name not in out and len(name) <= 40:
            out.append(name)
    return out


# --- a whole job -------------------------------------------------------------------

def complete(job: dict) -> dict:
    """Fill the facts a job still lacks from its own location and text.
    Values a reader already set (the board's data) are kept."""
    text = to_text(job.get("description") or "")
    if not job.get("work_mode"):
        job["work_mode"] = work_mode(job.get("location") or "", text)
    if not job.get("employment_type"):
        job["employment_type"] = employment_from_title(job.get("title") or "")
    if not job.get("salary"):
        job["salary"] = salary_in_text(text)
    must, nice = skills_split(text)
    for s in board_skills(job.get("board_skills") or []):
        if s not in must:
            must.append(s)
    job["skills"] = must
    job["skills_nice"] = [s for s in nice if s not in must]
    return job
