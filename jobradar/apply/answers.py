"""What to type into an application form.

Answers come, in order, from: what you answered before (private/apply-answers.yaml),
a rule table over private/apply-profile.yaml, and - for free-text questions
only - the model, writing from the chosen resume. Nothing else: a question none
of them covers is asked of you, and your answer is kept for next time.
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

import yaml

YES, NO = "Yes", "No"

# Questions whose answers change every time or are secrets: never remembered.
NEVER_KEEP = re.compile(r"captcha|password|passcode|\botp\b|one time|verification|security code|"
                        r"enter the code|access code|card number|cvv")


def norm(text: str) -> str:
    """Label text reduced to what identifies the question."""
    text = (text or "").lower().replace("*", " ")
    text = re.sub(r"\(optional\)|\(required\)|required", " ", text)
    text = re.sub(r"[^a-z0-9+#.]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _yn(v) -> str:
    if isinstance(v, bool):
        return YES if v else NO
    return YES if str(v).strip().lower() in ("yes", "y", "true", "1") else NO


class Profile:
    """private/apply-profile.yaml plus the answers you have given the engine."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / "private" / "apply-profile.yaml"
        self.learned_path = self.root / "private" / "apply-answers.yaml"
        self.data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        self._lock = threading.Lock()
        self.learned: dict[str, str] = {}
        if self.learned_path.exists():
            self.learned = yaml.safe_load(self.learned_path.read_text(encoding="utf-8")) or {}

    def get(self, dotted: str, default=""):
        cur = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return default if cur is None else cur

    # -- account login -----------------------------------------------------
    @property
    def login_email(self) -> str:
        return (os.environ.get("JOBRADAR_APPLY_EMAIL") or os.environ.get("JOB_Email")
                or self.get("personal.email"))

    @property
    def login_password(self) -> str:
        pw = (os.environ.get("JOBRADAR_APPLY_PASSWORD") or os.environ.get("Job_passorrd")
              or os.environ.get("JOB_PASSWORD") or "")
        if not pw:
            raise RuntimeError("set JOBRADAR_APPLY_PASSWORD in .env - the job-site password")
        return pw

    @property
    def full_name(self) -> str:
        return f"{self.get('personal.first_name')} {self.get('personal.last_name')}".strip()

    @property
    def engine(self) -> dict:
        return self.data.get("engine") or {}

    # -- answers you gave --------------------------------------------------
    def recall(self, label: str) -> str | None:
        return self.learned.get(norm(label))

    def learn(self, label: str, answer: str) -> None:
        key = norm(label)
        if not key or NEVER_KEEP.search(key):
            return
        with self._lock:
            self.learned[key] = answer
            self.learned_path.write_text(
                "# Answers you gave the apply engine, reused on later applications.\n"
                "# Edit or delete a line to change what it types.\n"
                + yaml.safe_dump(self.learned, allow_unicode=True, sort_keys=True, width=200),
                encoding="utf-8")

    def skill_ratings(self) -> str:
        """Your own ratings from config.yaml `skills` (top = 10/10, good = 8/10)."""
        try:
            cfg = yaml.safe_load((self.root / "config.yaml").read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return ""
        sk = cfg.get("skills") or {}
        parts = []
        if sk.get("top"):
            parts.append("10/10: " + ", ".join(map(str, sk["top"])))
        if sk.get("good"):
            parts.append("8/10: " + ", ".join(map(str, sk["good"])))
        return "; ".join(parts)

    def facts(self) -> str:
        """The profile as text for the model - no secrets live in it - plus the
        answers you gave on earlier applications, so a question worded a little
        differently elsewhere is answered the way you answered it."""
        data = {k: v for k, v in self.data.items() if k != "engine"}
        if self.learned:
            data["answers_the_candidate_gave_before"] = dict(list(self.learned.items())[-80:])
        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=200)


# -- the rule table -------------------------------------------------------------
# (pattern over the normalised label, answer). The answer is a dotted profile
# path, a callable taking (profile, kind), or a literal in a 1-tuple. First match
# wins, so narrow questions sit above the broad ones that would swallow them.

def _money(path):
    def f(p: Profile, kind: str, label: str):
        lpa = float(p.get(path) or 0)
        if kind == "number":
            # A number box in lakhs takes 6; one in rupees takes 600000.
            return f"{lpa:g}" if re.search(r"lakh|lpa|lac", label) else str(int(lpa * 100000))
        return f"{lpa:g} LPA"
    return f


def _years(path):
    def f(p: Profile, kind: str, label: str):
        v = p.get(path)
        return f"{v}" if kind in ("number", "choice") else f"{v} years"
    return f


def _notice(p: Profile, kind: str, label: str):
    d = p.get("current_job.notice_period_days")
    return str(d) if kind in ("number", "choice") else f"{d} days"


def _phone(p: Profile, kind: str, label: str):
    return str(p.get("personal.phone"))


def _decline(p, kind, label):
    return "Decline to self-identify"


RULES: list[tuple[str, object]] = [
    # contact and identity
    (r"\bfirst name\b|given name|^first$", "personal.first_name"),
    (r"\blast name\b|surname|family name|^last$", "personal.last_name"),
    (r"middle name", ("",)),
    (r"preferred (first )?name|nick ?name", "personal.preferred_name"),
    (r"full name|legal name|^name$|^your name|candidate name|signature",
     lambda p, k, l: p.full_name),
    (r"confirm.*e ?mail|e ?mail.*(again|confirm)|^e ?mail|e ?mail address|\bemail\b",
     lambda p, k, l: p.login_email),
    (r"device type", ("Mobile",)),
    (r"country (phone )?code|phone code|dial(l)?ing code|extension", "personal.phone_country_code"),
    (r"phone|mobile|contact (number|no)|whatsapp number|cell", _phone),
    (r"date of birth|\bdob\b|birth ?date", "personal.date_of_birth"),
    (r"pronoun", _decline),
    (r"gender|\bsex\b", "personal.gender"),
    (r"nationality|citizenship", "personal.nationality"),
    # links
    (r"linked ?in", "links.linkedin"),
    (r"git ?hub", "links.github"),
    (r"leet ?code", "links.leetcode"),
    (r"portfolio|personal (web)?site|^website|other website|blog", "links.portfolio"),
    # location
    (r"postal|zip ?code|pin ?code|\bpin\b", "personal.pincode"),
    (r"address line 1|street|^address$|current address|residential address|full address",
     "personal.full_address"),
    (r"address line 2", ("",)),
    (r"hometown|native", "personal.hometown"),
    (r"willing.*relocat|open to relocat|relocat", lambda p, k, l: _yn(p.get("preferences.willing_to_relocate"))),
    (r"preferred (work )?location|location preference", lambda p, k, l: ", ".join(p.get("preferences.preferred_locations") or [])),
    (r"^city|current city|city of residence|town/city", "personal.current_city"),
    (r"\bstate\b|province|\bregion\b", "personal.current_state"),
    (r"^country|country of residence|current country", "personal.country"),
    (r"current location|where are you (currently )?(located|based)|^location|your location|location \(city\)",
     lambda p, k, l: f"{p.get('personal.current_city')}, {p.get('personal.current_state')}, India"),
    # eligibility
    (r"sponsor|need (a )?(work )?visa|require (a )?(work )?visa|visa (support|required)",
     lambda p, k, l: _yn(p.get("preferences.need_visa_sponsorship"))),
    (r"authori[sz]ed|legally (eligible|allowed|able)|right to work|eligible to work|work permit",
     lambda p, k, l: _yn(p.get("preferences.authorized_to_work_in_india"))),
    (r"passport", lambda p, k, l: _yn(p.get("preferences.has_passport"))),
    (r"pan card|\bpan\b(?!.*number)", lambda p, k, l: _yn(p.get("preferences.pan_card_available"))),
    (r"shift", lambda p, k, l: _yn(p.get("preferences.willing_to_work_shifts"))),
    (r"travel", lambda p, k, l: _yn(p.get("preferences.willing_to_travel"))),
    # " || " separates fallbacks: a choice list takes the first it has
    (r"work (from|in the) office|on ?site|hybrid|in office|days a week|days per week|work mode|remote",
     ("Yes || Hybrid || Any || Flexible",)),
    # diversity - only what you gave; race asked by US-style forms is declined
    (r"disabilit|differently abled|pwd", "diversity.disability"),
    (r"veteran|ex ?servicem|armed forces|military|protected veteran", "diversity.veteran_or_ex_servicemen"),
    (r"caste|category|reservation", "diversity.category"),
    (r"race|ethnic|hispanic|latino|sexual orientation|transgender", _decline),
    # the current job
    (r"notice period|how soon can you join|days notice", _notice),
    (r"serving (your )?notice|currently on notice", lambda p, k, l: _yn(p.get("current_job.currently_serving_notice"))),
    (r"last working day", "current_job.last_working_day"),
    (r"when can you (start|join)|earliest (start|joining)|joining date|available to (start|join)|availability to (start|join)",
     "current_job.earliest_joining_date"),
    (r"start immediately|immediate joiner", lambda p, k, l: _yn(p.get("standard_answers.can_start_immediately"))),
    (r"expected (ctc|salary|compensation|pay|annual)|salary expectation|desired (salary|compensation)|expected package",
     _money("current_job.expected_ctc_lpa")),
    (r"negotiable", lambda p, k, l: _yn(p.get("current_job.expected_ctc_negotiable"))),
    (r"current (fixed )?(ctc|salary|compensation|pay|package)|present (ctc|salary)|annual (ctc|salary)|\bctc\b",
     _money("current_job.current_ctc_lpa")),
    (r"salary currency|currency", ("INR",)),
    (r"reason for (change|leaving|looking|switch)|why (are you )?(leaving|looking|changing)",
     "current_job.reason_for_change"),
    (r"offer in hand|other offers?|pending offers?|holding (an|any) offer",
     lambda p, k, l: _yn(p.get("current_job.has_offer_in_hand"))),
    (r"current (company|employer|organi[sz]ation)|present (company|employer)|most recent (company|employer)|^company( name)?$|^employer",
     "current_job.company"),
    (r"current (job )?(title|role|designation|position)|^job title|^title$|^designation",
     "current_job.title"),
    (r"relevant (years of )?experience", _years("current_job.relevant_experience_years")),
    (r"^(total |overall )?(years of )?(professional |work |overall |total )?experience( in years)?$|total (years of )?experience|overall experience|how many years of (professional |work |total )?experience( do you have)?$",
     _years("current_job.total_experience_years")),
    # education
    (r"backlog", "education.active_backlogs"),
    (r"(education(al)?|academic|career) gap|gap in (education|studies)", "education.education_gap"),
    (r"12th|xii|higher secondary|intermediate|senior secondary",
     lambda p, k, l: p.get("education.year_12th") if "year" in l else p.get("education.percentage_12th")),
    (r"10th|\bx\b.*(percent|marks|grade)|matric|secondary school",
     lambda p, k, l: p.get("education.year_10th") if "year" in l else p.get("education.percentage_10th")),
    (r"cgpa|\bgpa\b|grade point|aggregate|percentage", "education.cgpa"),
    (r"graduation (year|date)|year of (passing|graduation)|passing year|graduat.*year|batch",
     "education.graduation_year"),
    (r"school|college|university|institut", "education.college"),
    (r"discipline|major|branch|field of study|speciali[sz]ation|stream", "education.branch"),
    (r"highest (level of )?(education|qualification)|degree|qualification",
     lambda p, k, l: p.get("education.degree_full") if k == "choice" else p.get("education.degree")),
    (r"languages? (known|spoken)|languages", lambda p, k, l: ", ".join(p.get("personal.languages_known") or [])),
    # the usual yes/no screeners
    (r"how did you (hear|find|learn|come)|where did you (hear|find|see)|source of (application|hire)|^source$|referral source",
     lambda p, k, l: f"{p.get('standard_answers.how_did_you_hear')} || Company website || Careers || LinkedIn || Job board || Other"),
    (r"previously (worked|employed|applied)|worked (here|for us)|former employee|ever (been )?(employed|worked)|current or former",
     "standard_answers.previously_worked_here"),
    (r"relative|family member|related to (an|any) employee|know anyone", "standard_answers.relatives_at_company"),
    (r"referr(ed|al) by|employee referral|name of (the )?referr|referrer", ("",)),
    (r"were you referred|referral\?", ("No",)),
    (r"contract(ual)? (role|position|basis)|open to contract", "standard_answers.open_to_contract_roles"),
    (r"\bbond\b|service agreement", "standard_answers.willing_to_sign_bond"),
    # consent boxes - you asked the engine to apply, which is what they ask
    (r"privacy|consent|i agree|agree to|acknowledge|terms|certify|i confirm|declare|accurate|true and correct|data processing|gdpr",
     ("Yes",)),
    (r"whatsapp|sms|text message|receive (updates|communication)|future (jobs|opportunities)|talent (pool|community)",
     ("Yes",)),
]

_COMPILED = [(re.compile(p), a) for p, a in RULES]

# A long sentence with a box to tick is a consent, whatever words it also uses
# ("...your stated preferences" must not read as the question "State").
CONSENT = re.compile(r"consent|i agree|agree to|acknowledge|privacy (notice|policy)|terms|"
                     r"i confirm|i certify|i declare|true and correct|by (checking|ticking|submitting)")

# Open questions answered in prose from the resume, never from a rule.
OPEN = re.compile(
    r"tell us|about (yourself|you)|describe|why (do you|are you|would you|this|our|us)|"
    r"what (interests|excites|motivates|makes)|cover letter|summary|introduc|explain|"
    r"proud|challeng|achievement|how would you|share (a|an|your)|anything else|additional information|"
    r"message|note to|hiring (team|manager)|recruiter|motivation|interest in")


def rule_answer(p: Profile, label: str, kind: str) -> str | None:
    """None: no rule covers it. '' : a rule says to leave it blank."""
    key = norm(label)
    if not key:
        return None
    if len(key.split()) > 12 and CONSENT.search(key):
        return YES
    for rx, ans in _COMPILED:
        if not rx.search(key):
            continue
        if isinstance(ans, tuple):
            val = ans[0]
        elif callable(ans):
            val = ans(p, kind, key)
        else:
            val = p.get(ans)
        if val in ("from_resume",):
            return None
        return "" if val is None else str(val)
    return None


# -- matching an answer to a list of choices -----------------------------------

_NUM = r"(\d+(?:\.\d+)?)"


def _range(option: str) -> tuple[float, float] | None:
    o = option.lower().replace(",", "")
    if re.search(r"immediate", o):
        return (0, 0)
    m = re.search(_NUM + r"\s*(?:-|–|to)\s*" + _NUM, o)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    m = re.search(r"(?:less than|under|below|up ?to|<|within|maximum|max)\s*" + _NUM, o) or \
        re.search(_NUM + r"\s*(?:or less|or fewer|and below|or below)", o)
    if m:
        return (0, float(m.group(1)))
    m = re.search(r"(?:more than|over|above|>|at least|minimum)\s*" + _NUM, o) or \
        re.search(_NUM + r"\s*(?:\+|or more|and above|plus)", o)
    if m:
        return (float(m.group(1)), float("inf"))
    m = re.fullmatch(r"\D*" + _NUM + r"\D*", o)
    if m:
        v = float(m.group(1))
        return (v, v)
    return None


_DECLINE = re.compile(r"decline|prefer not|don.?t wish|do not wish|not to (say|answer|disclose)|rather not|choose not")


def choose_option(value: str, options: list[str]) -> str | None:
    """The option that says what `value` says, or None if none does. A value
    with " || " fallbacks takes the first alternative the list offers."""
    if value is not None and " || " in str(value):
        for alt in str(value).split(" || "):
            hit = choose_option(alt, options)
            if hit is not None:
                return hit
        return None
    return _choose(value, options)


def nearest_option(value: str, options: list[str]) -> str | None:
    """choose_option, then the closest spelling. Yes/no answers never take a
    'close' spelling - Yes must not become 'Yes, but...' by accident of letters."""
    hit = choose_option(value, options)
    if hit is not None or value is None:
        return hit
    v = norm(first_alt(value))
    if v in ("yes", "no") or not v:
        return None
    import difflib
    by_norm = {norm(o): o for o in options if o and o.strip()}
    close = difflib.get_close_matches(v, list(by_norm), n=1, cutoff=0.45)
    return by_norm[close[0]] if close else None


def first_alt(value: str) -> str:
    """What a free-text box gets when the answer carries choice fallbacks."""
    return str(value).split(" || ")[0] if value is not None else ""


def _choose(value: str, options: list[str]) -> str | None:
    opts = [o for o in options if o and o.strip() and not re.fullmatch(
        r"(?i)\s*(select|choose|please select|--+|select\.\.\.|select one)\s*\.*\s*", o)]
    if not opts or value is None:
        return None
    v = norm(str(value))
    if not v:
        return None
    by_norm = {norm(o): o for o in opts}
    if v in by_norm:
        return by_norm[v]
    if v.startswith("decline"):
        for o in opts:
            if _DECLINE.search(o.lower()):
                return o
        return None
    if v in ("yes", "no"):
        for o in opts:
            n = norm(o)
            if n == v or n.startswith(v + " ") or n in (("y", "true") if v == "yes" else ("n", "false")):
                return o
        if v == "yes":
            # consent lists say it their own way: "I confirm", "I agree", "Acknowledged"
            for o in opts:
                if re.search(r"^(i )?(confirm|agree|accept|acknowledge|consent|understand|have read)|^ok",
                             norm(o)):
                    return o
        return None
    # numbers: 15 days of notice sits in "0-15 days", 1.3 years in "1-2 years"
    m = re.fullmatch(_NUM + r"( (days?|years?|yrs?|lpa|months?))?", v)
    if m:
        num = float(m.group(1))
        for o in opts:
            r = _range(o)
            if r and r[0] <= num <= r[1]:
                return o
    # containment: "B.E." in "Bachelor's degree (B.E./B.Tech)", "Male" == "Man"
    words = set(v.split())
    best, score = None, 0.0
    for n, o in by_norm.items():
        if not n:
            continue
        if re.search(rf"(^|\s){re.escape(v)}($|\s)", n) or re.search(rf"(^|\s){re.escape(n)}($|\s)", v):
            s = 0.9 + min(len(n), len(v)) / 1000
        else:
            ow = set(n.split())
            s = len(words & ow) / max(len(words | ow), 1)
        if s > score:
            best, score = o, s
    if score >= 0.5:
        return best
    synonyms = {"male": ("man", "m"), "female": ("woman", "f"),
                "bachelor of engineering": ("bachelor", "bachelors", "b.e", "b.tech", "undergraduate"),
                "b.e.": ("bachelor", "bachelors", "b.tech"),
                "company careers page": ("career", "careers", "company website", "website", "linkedin")}
    for alt in synonyms.get(v, ()):
        for n, o in by_norm.items():
            if alt in n.split() or n.startswith(alt):
                return o
    return None
