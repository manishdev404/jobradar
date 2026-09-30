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
assert row["experience"] == "0+ yrs" and "java" in row["skills"], dict(row)
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
assert required_years("x", "&lt;strong&gt;Experience:&lt;/strong&gt; 3–5 Years") == 3
assert required_years("x", "1+ years of Python; 3+ years overall") == 1, "lowest bar wins"
assert required_years("SDE (0-1 yrs)", "") == 0
assert required_years("x", "bachelor's degree in 4 years") is None
assert too_senior({"title": "SDE", "description": "2+ years of experience"}, 1)
assert not too_senior({"title": "SDE", "description": "no figure stated"}, 1)
assert not too_senior({"title": "SDE", "description": "5+ years"}, None), "no limit set"
assert "aws" in job_skills("x", "Docker on AWS. Kafka a plus.")

# --- tracker import status mapping --------------------------------------------
for raw, want in [("A Refrral", "referral"), ("B Application Submitted", "applied"),
                  ("C JOb exppired", "expired"), ("D Rejected", "rejected"),
                  ("First round Rejetct", "rejected"), ("First technical round", "interview"),
                  ("H Draft", "not_applied"), ("E Message send", "contacted"), ("", "applied")]:
    assert map_status(raw) == want, (raw, map_status(raw), want)

print("application history, experience gate, freshness tests PASS")
