"""Import an application tracker you kept by hand (.xlsx or .csv).

Columns are found by header name, not position, so a sheet with extra or
reordered columns still imports. Recognised headers (case-insensitive,
substring): company, role/title/position, status, link/url, date, remark/notes.

Free-text statuses are mapped onto the pipeline in store.STATUSES; the original
text is kept in notes so nothing is lost in translation.
"""
from __future__ import annotations

import csv
import datetime as dt
import re
from pathlib import Path

from .store import Store

# First match wins, so the more specific stages come first.
_STATUS_RULES = (
    (r"over.?exp|over.?qualif|too senior|more experience|exp\w* mismatch", "over_exp"),
    (r"\brej|declin|not selected", "rejected"),
    (r"exp+ire|closed|no longer", "expired"),
    (r"withdr", "withdrawn"),
    (r"offer", "offer"),
    (r"interview|round|technical|\bhr\b", "interview"),
    (r"assess|test|assignment|exam|\boa\b", "assessment"),
    (r"message|mail|contact|reach", "contacted"),
    (r"ask\w*\W+(for\W+)?(an?\W+)?re+f+e?r+a?l|re+f+e?r+a?l\W+ask", "referral_asked"),
    (r"re+f+e?r+a?l|\breff?\b", "referral_got"),
    (r"in (the )?process|in progress", "in_process"),
    (r"draft|not applied|to apply|need to apply|saved", "not_applied"),
    (r"submit|applied|apply", "applied"),
)

_HEADERS = {
    "company": ("company", "conapmey", "employer", "organisation", "organization"),
    "title": ("role", "title", "position", "job name"),
    "status": ("status", "stage"),
    "url": ("link", "url"),
    "date": ("date", "data"),          # "Data" is a common typo for Date
    "notes": ("remark", "note", "comment", "response", "responce"),
}


def map_status(raw: str | None) -> str:
    text = (raw or "").strip().lower()
    for pattern, status in _STATUS_RULES:
        if re.search(pattern, text):
            return status
    return "applied"


def _columns(header: list) -> dict[str, int]:
    cols: dict[str, int] = {}
    for i, h in enumerate(header):
        h = str(h or "").strip().lower()
        if not h:
            continue
        for key, names in _HEADERS.items():
            if key not in cols and any(n in h for n in names):
                cols[key] = i
                break
    return cols


def _rows(path: Path) -> list[tuple[str, list[list]]]:
    """[(sheet name, rows)] including the header row."""
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as f:
            return [(path.stem, [list(r) for r in csv.reader(f)])]
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    return [(ws.title, [list(r) for r in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]


def _date(value) -> float | None:
    if isinstance(value, dt.datetime):
        return value.timestamp()
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day).timestamp()
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return dt.datetime.strptime(text, fmt).timestamp()
        except ValueError:
            pass
    return None


def _cell(row: list, cols: dict[str, int], key: str):
    i = cols.get(key)
    return row[i] if i is not None and i < len(row) else None


def import_file(path: Path, store: Store) -> dict:
    """Returns counts: added, skipped (already imported), sheets used."""
    added = skipped = 0
    used = []
    for sheet, rows in _rows(path):
        if not rows:
            continue
        cols = _columns(rows[0])
        if "company" not in cols:
            continue                     # not an application sheet
        used.append(sheet)
        for row in rows[1:]:
            company = str(_cell(row, cols, "company") or "").strip()
            if not company:
                continue
            title = str(_cell(row, cols, "title") or "").strip()
            url = str(_cell(row, cols, "url") or "").strip()
            url = url if url.lower().startswith("http") else ""
            raw_status = str(_cell(row, cols, "status") or "").strip()
            remark = str(_cell(row, cols, "notes") or "").strip()
            if store.has_application(company, title, url):
                skipped += 1
                continue
            notes = "; ".join(x for x in (raw_status and f"status: {raw_status}", remark) if x)
            store.add_application(
                company, title, url, status=map_status(raw_status),
                applied_at=_date(_cell(row, cols, "date")), notes=notes,
                source=f"import:{path.name}#{sheet}",
            )
            added += 1
    return {"added": added, "skipped": skipped, "sheets": used}
