"""What one job posting says, as fields rather than one blob of text.

A board's own data - Oracle's WorkplaceTypeCode, Keka's salary range, a
JSON-LD datePosted - is kept as a value, so nothing downstream has to guess it
back out of the description with a pattern."""
from __future__ import annotations

from dataclasses import dataclass, field, fields


@dataclass
class Details:
    text: str = ""                    # the posting, headings and bullets on their own lines
    posted_at: float | None = None    # from a dated field: the page's data or the board's API
    posted_guess: float | None = None # read from words on the page ("Posted 3 days ago")
    first_posted: float | None = None # first published, when the board also has an updated date
    closes_at: float | None = None    # applications close
    years_min: int | None = None      # experience asked, from the board's own field
    years_max: int | None = None
    work_mode: str = ""               # remote | hybrid | onsite
    employment_type: str = ""         # full-time | part-time | contract | temporary | internship
    salary: str = ""
    skills: list[str] = field(default_factory=list)   # the board's own skill list
    location: str = ""
    reader: str = ""                  # which reader produced it
    rendered: bool = False            # read through the headless browser
    error: str = ""                   # why reading failed, when it did

    def ok(self) -> bool:
        """Enough text to judge the posting by."""
        return len(self.text) >= 300

    def fill(self, other: "Details") -> "Details":
        """Keep every field this one has; take the rest from `other`. Text is
        the exception: a fuller text wins when this one is too thin to use."""
        for f in fields(self):
            if f.name in ("text", "reader", "rendered", "error"):
                continue
            if getattr(self, f.name) in (None, "", []) and getattr(other, f.name) not in (None, "", []):
                setattr(self, f.name, getattr(other, f.name))
        if not self.ok() and len(other.text) > len(self.text):
            self.text = other.text
            self.reader = other.reader or self.reader
            self.rendered = other.rendered
        self.reader = self.reader or other.reader
        if not self.ok() and not self.error:
            self.error = other.error
        if self.ok():
            self.error = ""
        return self
