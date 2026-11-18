"""Marking a job "applied" also sends it to the Google Sheet - once, and only
for "applied"; the save never fails because the sheet did. The row reads like
the ones typed by hand: role with the posting's ID, remark with the fit."""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jobradar.sheet as sheet
import jobradar.web.server as srv
from jobradar.store import Store

# --- the posting's reference ID ---------------------------------------------
ref = sheet.job_ref
assert ref("https://barclays.wd3.myworkdayjobs.com/ext/job/Pune/Software-Engineer_JR-0000121951-1") == "JR-0000121951-1"
assert ref("https://paypal.wd1.myworkdayjobs.com/jobs/job/Bangalore/Software-Engineer_R0138600") == "R0138600"
assert ref("https://cisco.wd5.myworkdayjobs.com/C/job/Bangalore-India/Software-Engineer_2027270-1/apply") == "2027270-1"
assert ref("https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/js/job/340730") == "340730"
assert ref("https://toddle.keka.com/careers/jobdetails/70686") == "70686"
assert ref("https://job-boards.greenhouse.io/capco/jobs/8354687") == "", "internal code, not a job ID"
assert ref("https://jobs.lever.co/cin7/510ff0bd-2b01-4645-bf6f-89be209ad154") == ""
assert ref("https://x.com/j", "Location: Bangalore (Hybrid) Job ID: 52921 Profile") == "52921"
assert ref("https://x.com/j", "Req ID: R-11085\nAbout us") == "R-11085"
assert ref("https://x.com/j", "We have 2000 jobs and reference checks") == ""

# --- city and work mode -------------------------------------------------------
assert sheet._place("India - Bengaluru", "hybrid") == "Bengaluru Hybrid"
assert sheet._place("Pune, Gera Commerzone SEZ", "") == "Pune"
assert sheet._place("Bangalore, Karnataka, India", "onsite") == "Bangalore WFO"
assert sheet._place("Remote", "remote") == "Remote"
assert sheet._place("India", "") == "India", "no city named: the country beats nothing"

# --- the whole row ------------------------------------------------------------
mine = ["JavaScript", "React", "Node.js", "Java", "Spring Boot", "AWS"]
row = sheet.entry(
    {"company": "Acme", "title": "Software Engineer", "location": "India - Bengaluru", "notes": "",
     "url": "https://acme.wd3.myworkdayjobs.com/x/job/Blr/Software-Engineer_R11085",
     "applied_at": 1794772500.0, "skills": ""},
    {"breakdown": '{"skills": 88}', "score": 9, "years_min": 1, "years_max": 3,
     "skills": "Python, JavaScript, React, Node.js, Docker, AWS", "work_mode": "hybrid"},
    mine)
assert row["title"] == "Software Engineer  ID: R11085", row
assert row["notes"] == "ATS 88% | 1-3 yrs | JS+React+Node+AWS | Bengaluru Hybrid", row
assert row["date"] == "2026-11-16", row      # local date of applied_at (IST machine)
bare = sheet.entry({"company": "Beta", "title": "Dev", "url": "b", "notes": "via referral"}, None, mine)
assert bare["title"] == "Dev" and bare["notes"] == "via referral" and bare["date"] == "", bare

# --- when it is sent ------------------------------------------------------------
tmp = Path(tempfile.mkdtemp()) / "t.db"
srv.store = lambda: Store(tmp)
srv.cfg = lambda: {"skills": {"top": mine}}
Store(tmp).upsert([{"id": "j1", "company": "Acme", "title": "SDE 1", "url": "https://acme.com/jobs/1",
                    "location": "Bengaluru", "posted_at": None, "source": "greenhouse",
                    "description": "", "domain": "acme.com"}])

sent = []
real_record, real_enabled = sheet.record_applied, sheet.enabled
sheet.enabled = lambda: True
sheet.record_applied = lambda r: sent.append(r) or "added to Google Sheet"

# not applied yet: nothing goes to the sheet
out = srv.job_status("j1", srv.StatusIn(status="referral_asked"))
assert out["sheet"] == "" and sent == [], (out, sent)

# the move to applied: one row, with the posting's details
out = srv.job_status("j1", srv.StatusIn(status="applied"))
assert out["sheet"] == "added to Google Sheet", out
assert [(r["company"], r["title"], r["url"]) for r in sent] == [("Acme", "SDE 1", "https://acme.com/jobs/1")], sent
assert sent[0]["date"], "the applied date goes along"

# applied again, or a later stage: no second row
srv.job_status("j1", srv.StatusIn(status="applied"))
srv.update_application(out["id"], srv.StatusIn(status="interview"))
assert len(sent) == 1, sent

# the History page's dropdown and "add" form send too
srv.update_application(out["id"], srv.StatusIn(status="applied"))
srv.add_application(srv.ApplicationIn(company="Beta", title="Dev", url="b", status="applied"))
srv.add_application(srv.ApplicationIn(company="Gamma", title="Dev", url="g", status="not_applied"))
assert [r["company"] for r in sent] == ["Acme", "Acme", "Beta"], sent

# no .env settings: nothing sent; a broken sheet never raises
sheet.record_applied, sheet.enabled = real_record, real_enabled
os.environ.pop("JOBRADAR_SHEET_URL", None)
assert sheet.record_applied({"company": "A"}) == ""
os.environ["JOBRADAR_SHEET_URL"] = "http://127.0.0.1:9/exec"   # nothing listens there
os.environ["JOBRADAR_SHEET_TOKEN"] = "t"
assert sheet.record_applied({"company": "A"}).startswith("Google Sheet not updated"), "must not raise"
print("sheet tests PASS")
