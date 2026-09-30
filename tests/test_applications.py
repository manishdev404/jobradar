"""Application history, the experience gate and board freshness.

An applied job must stop costing tokens and stop alerting; a 'not applied'
mark must not hide a job; the experience gate must read requirements, not
company age; and a board read recently must be skipped without its jobs being
marked closed."""
import os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jobradar.store import Store
from jobradar.experience import required_years, too_senior, job_skills
from jobradar.applied import map_status

s = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
job = lambda i, **kw: {"id": i, "company": "Acme", "title": f"SDE {i}", "url": f"https://x/{i}",
                       "location": "Pune, India", "source": "t", "posted_at": None,
                       "description": kw.get("d", "")}
s.upsert([job("a", d="0-2 years of Java and React"), job("b"), job("c")])

# --- status flow -------------------------------------------------------------
app = s.set_job_status("a", "in_process")
row = s.application_for("a")
assert row["experience"] == "fresher (0 yrs)" and "java" in row["skills"], dict(row)
assert row["applied_at"] is not None
assert "a" not in [r["id"] for r in s.unscored(9)], "in-process job must not be scored"
assert "a" not in [r["id"] for r in s.unalerted()], "in-process job must not alert"
s.set_job_status("a", "applied")
assert s.set_job_status("a", "not_applied") == app, "one history row per job"
assert "a" in [r["id"] for r in s.unscored(9)], "'not applied' puts it back"
assert [e["status"] for e in s.events(app)] == ["in_process", "applied", "not_applied"]
try:
    s.set_job_status("a", "bogus"); raise AssertionError("bad status accepted")
except ValueError:
    pass

# an off-radar application is linked once a scan finds the posting
s.add_application("Acme", "Backend Dev", "https://x/d")
assert s.application_for("d") is None
s.upsert([job("d")]); s.db.execute("UPDATE jobs SET url='https://x/d' WHERE id='d'")
assert s.link_applications() == 1 and s.application_for("d") is not None

# --- board freshness ---------------------------------------------------------
s.mark_fetched("Acme", 4)
assert s.fresh_boards(12) == {"Acme"}
s.mark_fetched("Old", 1, ts=time.time() - 13 * 3600)
assert "Old" not in s.fresh_boards(12)
assert s.fresh_boards(0) == set(), "refresh_hours 0 disables skipping"

# --- experience gate ---------------------------------------------------------
assert required_years("x", "with over 35 years of experience moving $16 trillion") is None
assert required_years("x", "For over 20 years, Smartsheet has") is None
assert required_years("x", "journey 9 years back") is None
assert required_years("x", "Experience: 4-5 years\nBACKEND (Node.js)") == 4, "BACKEND is not 'years back'"
# Zoho detail pages embed the record as a JS string: var jobs = JSON.parse('...')
from jobradar import sources
_page = (r"<script>var jobs = JSON.parse('[{\x22Work_Experience\x22:\x224\-5 years\x22,"
         r"\x22Job_Description\x22:\x22<div>Years of Experience \\u2013 3+ years<\\\/div>\x22}]');</script>")
_real_request, sources._request = sources._request, lambda *a, **k: type("R", (), {"text": _page})()
_d = sources.fetch_details({"source": "zoho", "url": "https://x.zohorecruit.in/jobs/Careers/1/Y"})
sources._request = _real_request
assert _d == "Experience: 4-5 years\n<div>Years of Experience – 3+ years</div>", _d
assert required_years("Backend Developer", _d) == 4
assert required_years("x", "&lt;strong&gt;Experience:&lt;/strong&gt; 3–5 Years") == 3
assert required_years("x", "1+ years of Python; 3+ years overall") == 3, "strictest requirement wins"
assert required_years("x", "0-2 years of experience. 5+ years preferred") == 0, "nice-to-haves ignored"
assert required_years("x", "2+ years of Java experience is a plus") is None
assert required_years("SDE (0-1 yrs)", "") == 0
assert required_years("x", "bachelor's degree in 4 years") is None
assert too_senior({"title": "SDE", "description": "2+ years of experience"}, 1)
assert not too_senior({"title": "SDE", "description": "no figure stated"}, 1)
assert not too_senior({"title": "SDE", "description": "5+ years"}, None), "no limit set"
assert "aws" in job_skills("x", "Docker on AWS. Kafka a plus.")
# a level in the title is a bar even when the text states no years
for title, want in [("Software Engineer II", 2), ("SDE-2", 2), ("Intermediate Software Engineer", 2),
                    ("Machine Learning Engineer 3", 3), ("Senior Engineer IV", 3),
                    ("Software Engineer Intern 2027", None), ("SDE 1", None),
                    ("Software Engineer, Professional I", None)]:
    assert required_years(title, "") == want, (title, required_years(title, ""), want)
assert required_years("Engineer II", "1+ years of Go") == 2, "title level beats a lower text figure"
# the user's own rule: keep when the LOWER bound of the range is under 2 years
for text, keep in [("0-2 years of experience", True), ("1-2 years of experience", True),
                   ("0-3 years of experience", True), ("2+ years of experience", False),
                   ("3+ years of experience", False), ("Experience: 2 - 4 Yrs", False),
                   ("Freshers welcome", True)]:
    assert too_senior({"title": "Software Engineer", "description": text}, 1) is (not keep), text
assert required_years("x", "Experience: 3+ years. 1 year of Kafka exposure a plus") == 3, \
    "the experience line wins over a stray figure"

# Postings that got through before: a nice-to-have word in a NEIGHBOURING
# bullet or clause used to cancel the real requirement. Each is real text.
for desc, want in [
    # "ideally" is about React, not about the years
    ("<p>What You'll Bring</p><ul><li>4–6 years of software development experience , "
     "ideally with strong React.js experience</li></ul>", 4),
    # a "Nice to have" bullet above must not reach the next bullet
    ("<li>Nice to have: Python knowledge</li><li>At-least 5+ years hands-on software "
     "development experience</li>", 5),
    # "Preferred" belongs to another section
    ("<h3>Required:</h3><ul><li>Minimum 6-8 years of software development experience</li></ul>"
     "<h3>Preferred:</h3><ul><li>Azure</li></ul>", 6),
    ("4+ years of experience in software engineering, with at least 2-3 years actively "
     "contributing to open-source projects, ideally within the Apache ecosystem", 4),
    ("a minimum IT experience of 7-10 years preferably in Java/J2EE applications", 7),
    ("Previous experience in Storage is a nice-to-have\n5-10 years of developing large "
     "scale distributed systems", 5),
    ("Basic Qualifications&nbsp;6+&nbsp;years of professional experience", 6),
    ("You&nbsp;have&nbsp;1–3&nbsp;years&nbsp;of&nbsp;experience", 1),
    ("Requirements: 1 year to 3 years experience in building UI heavy features", 1),
    ("at least two (2) years of experience with Java", 2),
    ("Years of experience: 3+", 3),
    # still soft: the nice-to-have wording is in the figure's own clause
    ("Ideally 3+ years of backend experience", None),
    ("0-2 years of experience\n5+ years preferred", 0),
]:
    assert required_years("Software Engineer", desc) == want, \
        (desc[:60], required_years("Software Engineer", desc), want)
    if want is not None and want >= 2:
        assert too_senior({"title": "Software Engineer", "description": desc}, 1), desc[:60]

# --- tracker import status mapping --------------------------------------------
for raw, want in [("A Refrral", "referral"), ("B Application Submitted", "applied"),
                  ("C JOb exppired", "expired"), ("D Rejected", "rejected"),
                  ("First round Rejetct", "rejected"), ("First technical round", "interview"),
                  ("H Draft", "not_applied"), ("E Message send", "contacted"), ("", "applied")]:
    assert map_status(raw) == want, (raw, map_status(raw), want)

print("application history, experience gate, freshness tests PASS")

# --- roles: specific before catch-all; only listed roles pass ---------------------
from jobradar.roles import classify
from jobradar.main import passes_filters
ROLES = [
    {"name": "Backend", "priority": 1, "match": ["backend", "back end", "java developer"]},
    {"name": "Full stack", "priority": 2, "match": ["full stack", "fullstack", "mern"]},
    {"name": "Frontend", "priority": 4, "bonus": -1, "match": ["frontend", "front end", "react developer"]},
    {"name": "Software engineer", "priority": 3, "match": ["software engineer", "sde"]},
]
for title, want in [("Backend Software Engineer", "Backend"), ("Full Stack Developer", "Full stack"),
                    ("Software Engineer - Frontend", "Frontend"), ("SDE I", "Software engineer"),
                    ("Mechanical Engineer", None), ("Talent Intern", None),
                    ("Frontend Engineer (React)", "Frontend")]:
    got = classify(title, ROLES)
    assert (got or {}).get("role") == want, (title, got, want)
assert classify("Frontend Engineer", ROLES)["role_bonus"] == -1
_f = {"roles": ROLES, "max_required_years": 1}
_j = lambda t: {"title": t, "location": "Pune, India", "description": ""}
assert passes_filters(_j("Backend Developer"), _f)
assert not passes_filters(_j("Sales Engineer"), _f), "a role not listed is dropped"
assert not passes_filters(_j("Staff Software Engineer"), _f), "senior title dropped"
print("roles tests PASS")
