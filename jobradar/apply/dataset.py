"""Every application, written down as training data.

learn.py keeps what you do while the engine waits on you, to reuse straight
away. This keeps EVERYTHING, every application, for later: one JSON line per
application in private/apply-dataset/applications.jsonl -

- the job (company, title, link, board) and the resume used;
- every form page: its link, each field seen (label, kind, options, required),
  what ended up in it and who decided it - your profile, an answer you gave
  before, the AI, you just now, or the board itself;
- the button pressed to go on, and how the page ended (next page, stuck with
  these errors, CAPTCHA, submitted);
- each time it waited on you and, when you did the step in Chrome, your steps;
- the result: applied, failed (why), skipped, stopped.

That is what a small model needs to learn to fill forms and press the right
button the way you would. Never kept: passwords, codes, CAPTCHA text, card or
ID numbers - those are written as "[not kept]".
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from .answers import NEVER_KEEP, norm

SECRET = re.compile(r"(?i)passcode|password|\botp\b|one[- ]time|verification|security code|captcha|"
                    r"aadhaar|\bpan\b.*number|card number|cvv|account number|ifsc|access code")
HIDDEN = "[not kept]"
_lock = threading.Lock()


def _safe(label: str, value) -> str:
    value = "" if value is None else str(value)
    if value and (SECRET.search(label or "") or NEVER_KEEP.search(norm(label or ""))):
        return HIDDEN
    return value[:2000]


class Trace:
    """One application, from opening the job to the result."""

    def __init__(self, root: Path, app: dict, board: str):
        self.path = Path(root) / "private" / "apply-dataset" / "applications.jsonl"
        self.started = time.time()
        self.rec = {
            "app_id": app.get("id"), "company": app.get("company"), "title": app.get("title"),
            "url": app.get("url"), "board": board,
            "site": urlparse(app.get("url") or "").netloc.lower(),
            "resume": "", "pages": [], "pauses": [], "result": "", "why": "",
            "assisted": False, "seconds": 0,
        }
        self.sources: dict[str, str] = {}      # field label -> who decided its answer

    # -- while applying --------------------------------------------------------------
    def resume(self, name: str) -> None:
        self.rec["resume"] = name

    def source(self, label: str, who: str) -> None:
        """Who answered this field: profile, remembered, ai, ai_written, you."""
        if label and who:
            self.sources[label] = who

    def page(self, url: str, fields: list[dict]) -> None:
        """A form page as it stood once filled: every field and what is in it."""
        out = []
        for f in fields:
            label = f.get("label") or ""
            value = f.get("value") or ""
            if f.get("kind") == "file":
                value = value and "[file]"
            out.append({
                "label": label[:300], "kind": f.get("kind"), "required": bool(f.get("required")),
                "options": [str(o)[:120] for o in (f.get("options") or [])][:60],
                "value": _safe(label, value),
                "by": self.sources.get(label) or ("board" if value else "empty"),
            })
        self.rec["pages"].append({"url": url, "fields": out, "pressed": "", "final": None,
                                  "outcome": "", "errors": []})

    def pressed(self, text: str, final: bool | None) -> None:
        if self.rec["pages"]:
            pg = self.rec["pages"][-1]
            pg["pressed"], pg["final"] = (text or "").strip()[:120], final

    def outcome(self, what: str, errors: list[str] | None = None) -> None:
        if self.rec["pages"]:
            self.rec["pages"][-1]["outcome"] = what
            self.rec["pages"][-1]["errors"] = [e[:200] for e in (errors or [])][:8]

    def pause(self, kind: str, message: str, steps: list[dict] | None = None,
              answer: str | None = None) -> None:
        """The engine waited on you. Your steps in Chrome are the best examples."""
        done = []
        for s in steps or []:
            if "click" in s:
                done.append({"click": s["click"][:120]})
            elif "password" in s:
                done.append({"password": HIDDEN})
            elif "fill" in s:
                done.append({"fill": s["fill"][:300], "value": _safe(s["fill"], s.get("value"))})
        self.rec["pauses"].append({"kind": kind, "message": message[:400],
                                   "answer": _safe(message, answer) if answer else "",
                                   "your_steps": done,
                                   "page": len(self.rec["pages"])})

    # -- the end -----------------------------------------------------------------------
    def finish(self, result: str, why: str = "", assisted: bool = False) -> None:
        self.rec.update(result=result, why=why[:500], assisted=assisted,
                        seconds=round(time.time() - self.started),
                        finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(self.rec, ensure_ascii=False)
            with _lock, self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass                                # never let the record stop an application


class NoTrace(Trace):
    """For runs outside the engine (tests): records nothing."""

    def __init__(self):  # noqa: D107 - deliberately empty
        self.sources = {}
        self.rec = {"pages": [], "pauses": []}

    def finish(self, *a, **k) -> None:
        pass


def summary(root: Path) -> dict:
    """How much there is to learn from so far."""
    path = Path(root) / "private" / "apply-dataset" / "applications.jsonl"
    out = {"applications": 0, "applied": 0, "failed": 0, "pages": 0, "fields": 0,
           "your_steps": 0, "sites": set()}
    if not path.exists():
        return {**out, "sites": 0}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        out["applications"] += 1
        out["applied"] += r.get("result") == "applied"
        out["failed"] += r.get("result") == "failed"
        out["pages"] += len(r.get("pages") or [])
        out["fields"] += sum(len(p.get("fields") or []) for p in r.get("pages") or [])
        out["your_steps"] += sum(len(p.get("your_steps") or []) for p in r.get("pauses") or [])
        out["sites"].add(r.get("site"))
    return {**out, "sites": len(out["sites"])}
