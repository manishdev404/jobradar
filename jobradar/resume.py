"""Resume in, profile out.

Two products come from the same text:

  build_profile  - a compact candidate summary the scorer reads before every
                   batch. Batch prompts cut it at 1,400 characters, so a model
                   summary is asked to fit well inside that; the raw-text
                   fallback is only there so a run without quota still works.
  parse_details  - name, headline, location and roles for the web UI's profile
                   panel. Local and regex-only, so the panel works with no model
                   configured; build_details lets a model refine it.

PDF extraction mangles text in predictable ways (spaces scattered through an
all-caps name, words run together), so the local parser prefers sources that
survive extraction - a LinkedIn slug over the header line - and leaves a field
empty rather than guess.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

SECTIONS = {
    "summary", "professional summary", "profile", "objective", "about", "about me",
    "education", "skills", "technical skills", "core skills", "key skills",
    "experience", "work experience", "professional experience", "employment",
    "employment history", "work history", "internships", "internship",
    "projects", "personal projects", "academic projects", "certifications",
    "certificates", "achievements", "awards", "publications", "languages",
    "interests", "hobbies", "activities", "leadership", "volunteering",
    "volunteer experience", "coursework", "relevant coursework",
    "extracurricular activities", "positions of responsibility", "contact",
    "competitive programming",
}
EXPERIENCE = {"experience", "work experience", "professional experience", "employment",
              "employment history", "work history", "internships", "internship"}
SUMMARY = ("summary", "professional summary", "profile", "objective", "about", "about me")

# Words that mark a line as a job title rather than an employer.
TITLE_WORDS = re.compile(
    r"\b(engineer|developer|intern|analyst|scientist|manager|designer|consultant|"
    r"architect|lead|associate|specialist|administrator|researcher|programmer|sde)\b", re.I)

_MONTH = (r"(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+)")
_DATES = re.compile(
    rf"\s*\(?({_MONTH}?\d{{4}}\s*[-–—to]+\s*(?:{_MONTH}?\d{{4}}|present|current|now|date))\)?\s*$",
    re.I,
)
_BULLET = re.compile(r"^\s*[•●▪◦*\-–]\s*")
_SPLIT_ROLE = re.compile(r"\s+(?:at|@|\||—|–|-)\s+|,\s+")
_TRAILING_PLACE = re.compile(r"\s+\S+,\s*[A-Za-z ]+$")   # "... Bengaluru, India"


# ---------------------------------------------------------------------------
# text extraction

def extract_text(path: Path | str) -> str:
    """PDF, DOCX or plain text -> text. Raises if the file is missing."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    if suffix == ".docx":
        return _docx_text(path)
    return path.read_text(encoding="utf-8", errors="replace")


def _docx_text(path: Path) -> str:
    """Paragraph text from a .docx without a python-docx dependency."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    paras = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
        p = p.replace("<w:tab/>", "\t")
        paras.append("".join(re.findall(r"<w:t(?: [^>]*)?>([^<]*)</w:t>", p)))
    return "\n".join(paras)


# ---------------------------------------------------------------------------
# scoring profile

PROFILE_PROMPT = """Summarize this resume for a recruiter screening job postings against it.
Plain text, at most 900 characters, no preamble. Cover, in this order:
- level: student / new grad / years of professional experience
- roles held, most recent first, with dates
- core skills and tools
- domains worked in
- education (degree, graduation year)
- location and any stated relocation or remote preference
The text may hold several versions of the same resume; merge them, do not repeat.

RESUME:
{text}"""


def build_profile(text: str, summarizer=None) -> str:
    """Compact candidate profile for the scorer. With no summarizer (scoring
    off, or the model call failed upstream) this is the cleaned resume text."""
    clean = _normalize(text)
    if summarizer is None:
        return clean
    reply = summarizer.complete(PROFILE_PROMPT.format(text=clean[:12000]), max_tokens=500)
    reply = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.S).strip()
    if not reply:
        raise ValueError("empty profile summary from model")
    return reply


def _normalize(text: str) -> str:
    lines = (re.sub(r"[ \t]+", " ", ln).strip() for ln in (text or "").splitlines())
    return "\n".join(ln for ln in lines if ln)


# ---------------------------------------------------------------------------
# profile panel details

def parse_details(text: str) -> dict:
    """Local, model-free parse. Every field empty when not found."""
    sections = _sections(_normalize(text).splitlines())
    header = sections.get("", [])
    experience = _experience(sections)
    return {
        "name": _name(text, header),
        "headline": experience[0]["role"] if experience else _summary_line(sections),
        "location": _location(header[1:]),
        "experience": experience,
        "error": "",
    }


def _heading(line: str) -> str | None:
    key = re.sub(r"\s+", " ", line.strip().rstrip(":").lower())
    return key if key in SECTIONS else None


def _sections(lines: list[str]) -> dict[str, list[str]]:
    """Split into {heading: lines}. Lines before the first heading go under "".
    A repeated heading (several resumes in one file) extends the same section."""
    out: dict[str, list[str]] = {"": []}
    cur = ""
    for ln in lines:
        h = _heading(ln)
        if h is not None:
            cur = h
            out.setdefault(cur, [])
        else:
            out[cur].append(ln)
    return out


def _name(text: str, header: list[str]) -> str:
    # A LinkedIn slug survives PDF extraction; an all-caps header often does not.
    m = re.search(r"linkedin\.com/in/([A-Za-z0-9\-_%]+)", text or "", re.I)
    if m:
        parts = [p for p in re.split(r"[-_]", m.group(1)) if p.isalpha()]
        if len(parts) >= 2:
            return " ".join(p.capitalize() for p in parts)
    if not header:
        return ""
    first = re.split(r"\s*[|•·]\s*", header[0])[0].strip()
    if not first or "@" in first or re.search(r"\d", first) or len(first) > 60:
        return ""
    return first.title() if first.isupper() else first


def _location(lines: list[str]) -> str:
    """A comma-separated place name in the header: letters only, no contact info."""
    for ln in lines[:4]:
        for seg in re.split(r"\s*[|•·]\s*", ln):
            seg = seg.strip()
            if ("," in seg and not re.search(r"\d|@|https?:|www\.|\.com|/", seg)
                    and re.fullmatch(r"[A-Za-z .,'\-]+", seg)):
                return seg
    return ""


def _experience(sections: dict[str, list[str]]) -> list[dict]:
    roles, seen = [], set()
    for name, body in sections.items():
        if name not in EXPERIENCE:
            continue
        for i, ln in enumerate(body):
            if _BULLET.match(ln):
                continue
            m = _DATES.search(ln)
            if not m:
                continue
            role = ln[:m.start()].strip(" ,|–—-")
            if not role:
                continue
            nxt = body[i + 1] if i + 1 < len(body) else ""
            org = ""
            if nxt and not _BULLET.match(nxt) and not _DATES.search(nxt):
                org = nxt.strip()
                # "Acme Corp  May 2025 - Present / Software Engineer  City, Country":
                # the dated line is the employer when only the next line reads as a title.
                if TITLE_WORDS.search(org) and not TITLE_WORDS.search(role):
                    role, org = _TRAILING_PLACE.sub("", org), role
            else:
                # "Engineer at Acme" / "Engineer | Acme" on the one line
                parts = _SPLIT_ROLE.split(role, maxsplit=1)
                if len(parts) == 2:
                    role, org = parts[0].strip(), parts[1].strip()
            entry = {"role": role, "org": org, "years": m.group(1).strip()}
            key = tuple(entry.values())
            if key not in seen:
                seen.add(key)
                roles.append(entry)
    return roles


def _summary_line(sections: dict[str, list[str]]) -> str:
    for name in SUMMARY:
        body = sections.get(name)
        if body:
            first = re.split(r"(?<=[.!?])\s", " ".join(body), maxsplit=1)[0]
            return first[:140].strip()
    return ""


DETAILS_PROMPT = """Extract from this resume, as JSON only, no prose:
{{"name": "", "headline": "most recent job title", "location": "city, region, country",
  "experience": [{{"role": "", "org": "", "years": "e.g. Jun 2025 - Aug 2025"}}]}}
Leave a field empty when the resume does not state it. Never use a degree as the headline.

RESUME:
{text}"""


def build_details(text: str, scorer) -> dict:
    """Model-refined details, falling back field by field to the local parse."""
    details = parse_details(text)
    reply = scorer.complete(DETAILS_PROMPT.format(text=_normalize(text)[:8000]), max_tokens=700)
    reply = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.S)
    m = re.search(r"\{.*\}", reply, re.S)
    if not m:
        return details
    try:
        got = json.loads(m.group(0))
    except json.JSONDecodeError:
        return details
    for key in ("name", "headline", "location"):
        val = got.get(key)
        if isinstance(val, str) and val.strip():
            details[key] = val.strip()
    exp = got.get("experience")
    if isinstance(exp, list):
        clean = [{"role": str(e.get("role", "")).strip(), "org": str(e.get("org", "")).strip(),
                  "years": str(e.get("years", "")).strip()}
                 for e in exp if isinstance(e, dict) and e.get("role")]
        if clean:
            details["experience"] = clean
    return details
