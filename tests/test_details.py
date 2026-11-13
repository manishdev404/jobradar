"""The posting reader (details/) and the scan's fast/slow queues, offline.
Every page and API reply here is a shape a real board returned."""
import json, os, sys, tempfile, threading, time
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobradar import details, net
from jobradar.details import browser, dates, facts, page, sections, sites
from jobradar.details.model import Details
from jobradar.details.sites.workday import api_url
from jobradar.details.text import to_text
from jobradar.experience import job_years

P = dates.parse_date

# --- text keeps headings and bullets on their own lines -------------------------
t = to_text("&lt;h3&gt;Requirements&lt;/h3&gt;&lt;ul&gt;&lt;li&gt;2+ years of Node.js&lt;/li&gt;"
            "&lt;li&gt;React&lt;/li&gt;&lt;/ul&gt;")
assert t == "Requirements\n• 2+ years of Node.js\n• React", repr(t)
assert to_text("<p>a</p><script>var x = {1: 2}</script><p>b</p>") == "a\n\nb"
assert to_text("<p>Job</p><script>var cut = {") == "Job", "a script the size cap cut off"

# --- dates, as boards print them ------------------------------------------------
assert P("Wed Nov 11 00:00:00 UTC 2026") == P("2026-11-11"), "SuccessFactors (Java's toString)"
assert P("Mon, 03 Nov 2025 10:00:00 +0000") == datetime(2025, 11, 3, 10, tzinfo=timezone.utc).timestamp()
assert P("Nov 3, 2025") == P("3rd November 2025") == P("03/11/2025") == P("2025-11-03")
assert P(1762164000000) == 1762164000.0 and P("1762164000") == 1762164000.0
assert P("Posted 30+ Days Ago") is None and P("1999-01-01") is None and P(True) is None
print("text and dates PASS")

# --- sections decide what a line means --------------------------------------------
POSTING = ("About us\nWe use Kafka.\nRequirements\n• 2+ years of Node.js\n• React\n"
           "Nice to have:\n• AWS\n• 3+ years of Kubernetes\nBenefits\n• Health insurance")
assert [k for k, _, _ in sections.split(POSTING)] == ["about", "requirements", "nice", "benefits"]
assert facts.skills_split(POSTING) == (["Node.js", "React"], ["AWS", "Kubernetes"]), \
    "the company's stack is not asked; a nice-to-have is not a must"
assert job_years({"title": "Backend Engineer", "description": POSTING}) == 2, \
    "3+ years under Nice to have is no bar"
assert sections.split("Requirements: 3+ years of Go\nPerks: free lunch") == [
    ("requirements", "Requirements", "3+ years of Go"), ("benefits", "Perks", "free lunch")]
assert sections.kind_of("• Bonus") is None, "a bullet is never a heading"

# --- facts from a board's fields and from words --------------------------------------
assert facts.years_from_field("1-3 years") == (1, 3)
assert facts.years_from_field("Fresher") == (0, 0) and facts.years_from_field("6 months") == (0, 0)
assert facts.years_from_field("2") == (2, None), "Keka's bare number"
assert facts.years_from_field("Experienced") == (None, None) and facts.years_from_field(None) == (None, None)
assert facts.work_mode_word("ORA_HYBRID") == "hybrid" and facts.work_mode_word("OnSite") == "onsite"
assert facts.work_mode_word(True) == "remote" and facts.work_mode_word({"remote": False}) == ""
assert facts.work_mode("Pune", "We build hybrid cloud apps and remote debugging tools") == ""
assert facts.work_mode("Pune", "This is a hybrid role, 3 days a week in office") == "hybrid"
assert facts.work_mode("Remote - India", "") == "remote"
assert facts.employment_word("FULL_TIME") == "full-time" and facts.employment_word("CONTRACTOR") == "contract"
assert facts.employment_word("Onroll Regular") == "full-time" and facts.employment_word("2") == ""
assert facts.money(180000, 400000, "INR", "YEAR") == "₹1.8L–4L /yr"
assert facts.money(120000, 150000, "USD", "per-year-salary") == "$120k–150k /yr"
assert facts.salary_in_text("Location: Pune\nCTC: 8-12 LPA") == "8-12 LPA"
assert facts.salary_in_text("We work 5 days a week") == ""
print("sections and facts PASS")

# --- the generic page reader ----------------------------------------------------------
# JSON-LD with the trailing comma and raw newline real careers sites leave in it
LD = """<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",
"title":"SDE 1","description":"<p>Build APIs.</p>
<h3>Requirements</h3><ul><li>0-2 years of experience</li><li>Node.js</li></ul>",
"datePosted":"2026-11-01","validThrough":"2026-12-31","employmentType":"FULL_TIME",
"jobLocationType":"TELECOMMUTE",
"baseSalary":{"@type":"MonetaryAmount","currency":"INR","value":{"minValue":600000,"maxValue":900000,"unitText":"YEAR"}},
"experienceRequirements":{"@type":"OccupationalExperienceRequirements","monthsOfExperience":12},
"jobLocation":{"@type":"Place","address":{"addressLocality":"Pune","addressCountry":"IN"}},}</script>"""
d = page.read("https://x.com/job/1", html=LD)
assert (d.posted_at, d.closes_at) == (P("2026-11-01"), P("2026-12-31"))
assert (d.employment_type, d.work_mode, d.salary) == ("full-time", "remote", "₹6L–9L /yr")
assert d.years_min == 1 and d.location == "Pune, IN" and "• 0-2 years of experience" in d.text

# microdata, as SuccessFactors writes it
SF = """<html><body><div itemscope itemtype="http://schema.org/JobPosting">
<span itemprop="title">Software Engineer</span>
<meta itemprop="datePosted" content="Wed Nov 11 00:00:00 UTC 2026">
<span class="jobdescription" itemprop="description"><p>We need 1+ years of Java.</p>
<div><p>Spring Boot</p></div></span></div></body></html>"""
d = page.read("https://x.com/job/2", html=SF)
assert d.posted_at == P("2026-11-11") and "1+ years of Java" in d.text and "Spring Boot" in d.text

# the job object a page drawn by script carries
NEXT = ('<script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": {"job": {
    "title": "Backend Dev", "postedDate": "2026-11-05", "employmentType": "Full-time",
    "workplaceType": "Hybrid", "skills": ["Node", "PostgreSQL"],
    "description": "<p>Responsibilities</p><ul><li>Build services</li></ul><p>Requirements</p>"
                   "<ul><li>2+ years experience with Node.js and PostgreSQL</li></ul>" + " We ship." * 30}}}})
        + "</script>")
d = page.read("https://x.com/jobs/3", html=NEXT)
assert d.posted_at == P("2026-11-05") and (d.work_mode, d.employment_type) == ("hybrid", "full-time")
assert d.skills == ["Node.js", "PostgreSQL"], d.skills
PHENOM = ('<script>phApp.ddo = {"jobDetail": {"data": {"job": {"description": "' + "Requirements: 1+ years "
          "of experience in React. " * 12 + '", "postedDate": "2026-10-30", "type": "Full time", '
          '"ml_skills": ["react", "redux"]}}}}; phApp.sessionParams = {};</script>')
d = page.read("https://careers.x.com/job/4", html=PHENOM)
assert d.posted_at == P("2026-10-30") and d.employment_type == "full-time" and "React" in d.skills

# a page whose only "text" is a script the size cap cut: no posting, not code as text
CODE = ('<html><head><title>X</title></head><body><div>Apply now</div><script>var fingerPrint = {'
        + '"a.js":"b.js",' * 400)
d = page.read("https://x.com/jobs/5", html=CODE)
assert "fingerPrint" not in d.text and not d.ok()
print("page reader PASS")

# --- which reader reads a posting: its address first, then its board ------------------------
def name_of(url, source="page"):
    found = sites.find({"url": url, "source": source})
    return found.name if found else None


assert name_of("https://cisco.wd5.myworkdayjobs.com/Cisco_Careers/job/Bangalore/X_1", "phenom") == "workday"
assert name_of("https://www.acme.com/careers?gh_jid=123") == "greenhouse"
assert name_of("https://jobs.lever.co/acme/0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b") == "lever"
assert name_of("https://acme.com/x", "oracle") == "oracle"
assert name_of("https://careers.acme.com/job/1", "phenom") is None, "no API: its page is read"
assert api_url("https://cisco.wd5.myworkdayjobs.com/en-US/Cisco_Careers/job/Bangalore/SE_123") == \
    "https://cisco.wd5.myworkdayjobs.com/wday/cxs/cisco/Cisco_Careers/job/Bangalore/SE_123"
assert api_url("https://wd3.myworkdaysite.com/recruiting/acme/External/job/Pune/Dev_1") == \
    "https://wd3.myworkdaysite.com/wday/cxs/acme/External/job/Pune/Dev_1"

# --- each site reader on its API's reply ---------------------------------------------------------
replies: dict = {}
LONG = " Work on distributed systems with a small team." * 10


def fake_json(url, headers=None, **kw):
    for key, reply in replies.items():
        if url.startswith(key):
            return reply
    raise AssertionError(f"unexpected request {url}")


net.json = fake_json
net.page = lambda url, **kw: (url, "")          # no posting page to fall back on

replies["https://cisco.wd5.myworkdayjobs.com/wday/cxs/cisco/Cisco_Careers/job/Bangalore/SE_1"] = {
    "jobPostingInfo": {"jobDescription": f"<p>Requirements</p><ul><li>3+ years of Go</li></ul><p>{LONG}</p>",
                       "startDate": "2026-10-20", "postedOn": "Posted 30+ Days Ago", "timeType": "Full time",
                       "location": "Bangalore, India"}}
job = {"url": "https://cisco.wd5.myworkdayjobs.com/Cisco_Careers/job/Bangalore/SE_1", "source": "phenom",
       "title": "Software Engineer", "posted_at": time.time() - 30 * 86400, "description": "teaser"}
d = details.read(job)
details.apply(job, d)
assert d.reader == "workday" and job["posted_at"] == P("2026-10-20"), "the exact date beats '30+ days ago'"
assert job["employment_type"] == "full-time" and job_years(job) == 3

replies["https://h.fa.oraclecloud.com/hcmRestApi/"] = {"items": [{
    "ExternalDescriptionStr": "", "ExternalResponsibilitiesStr": "<p>Responsibilities</p><p>• Build APIs</p>",
    "ExternalQualificationsStr": f"<p>• 4+ years of Java</p><p>{LONG}</p>",
    "ExternalPostedStartDate": "2026-08-20T11:27:25+00:00", "ExternalPostedEndDate": "2027-01-01T00:00:00+00:00",
    "JobSchedule": "Full time", "WorkplaceTypeCode": "ORA_HYBRID", "PrimaryLocation": "Pune, India",
    "skills": [{"Skill": "Java"}, {"Skill": "Spring Boot"}]}]}
d = details.read({"url": "https://h.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/7607",
                  "source": "oracle", "title": "Dev"})
assert d.ok() and (d.work_mode, d.employment_type) == ("hybrid", "full-time")
assert d.closes_at == P("2027-01-01T00:00:00+00:00") and d.skills == ["Java", "Spring Boot"]
assert d.text.startswith("Responsibilities\n") and "\n\nQualifications\n• 4+ years of Java" in d.text

replies["https://boards-api.greenhouse.io/v1/boards/acme/jobs/123"] = {
    "content": "&lt;p&gt;" + LONG + "&lt;/p&gt;", "first_published": "2022-01-25T14:26:53-05:00",
    "updated_at": "2026-10-28T11:41:10-04:00", "application_deadline": None, "location": {"name": "Bengaluru"},
    "metadata": [{"name": "Employment Type", "value": "Full-time"},
                 {"name": "Workplace Type", "value": "Hybrid"},
                 {"name": "Years of Experience", "value": "0-2 years"}],
    "pay_input_ranges": [{"min_cents": 120000000, "max_cents": 180000000, "currency_type": "INR"}]}
listed = P("2026-10-28T11:41:10-04:00")
job = {"url": "https://www.acme.com/careers?gh_jid=123", "source": "greenhouse", "title": "SDE",
       "api": "https://boards-api.greenhouse.io/v1/boards/acme/jobs/123", "posted_at": listed}
details.apply(job, details.read(job))
assert job["posted_at"] == listed and job["first_posted"] == P("2022-01-25T14:26:53-05:00"), \
    "the list's date stays; first published is kept beside it"
assert (job["work_mode"], job["employment_type"], job["salary"]) == ("hybrid", "full-time", "₹12L–18L")
assert (job["years_min"], job["years_max"]) == (0, 2)

replies["https://api.smartrecruiters.com/v1/companies/AbhiBus/postings/744000152892832"] = {
    "jobAd": {"sections": {
        "companyDescription": {"title": "Company Description", "text": "<p>A bus ticketing company on React.</p>"},
        "jobDescription": {"title": "Job Description", "text": "<p>Build booking flows.</p>"},
        "qualifications": {"title": "Qualifications",
                           "text": f"<ul><li>0-1 years of experience</li><li>Node.js and MongoDB</li></ul><p>{LONG}</p>"}}},
    "releasedDate": "2026-10-01T10:00:00.000Z", "typeOfEmployment": {"label": "Full-time"},
    "experienceLevel": {"id": "internship"}, "location": {"city": "Hyderabad", "remote": False, "hybrid": True}}
job = {"url": "https://jobs.smartrecruiters.com/AbhiBus/744000152892832", "source": "smartrecruiters",
       "title": "Software Engineer Intern"}
details.apply(job, details.read(job))
details.complete(job)
assert (job["employment_type"], job["work_mode"]) == ("internship", "hybrid")
assert job["skills"] == ["Node.js", "MongoDB"], "React is the company's, not asked"

replies["https://acme.darwinbox.in/ms/candidateapi/job/a1"] = {"message": {"job": [{
    "jd": "&lt;p&gt;" + LONG + "&lt;/p&gt;", "experience_from_num": "12", "experience_to_num": "36",
    "salary_min": "600000", "salary_max": "900000", "salary_currency": "INR", "salary_timeframe": "Annual",
    "is_remote": "1", "emp_type": "Onroll Regular", "job_posting_on": "1762164000"}]}}
d = details.read({"url": "https://acme.darwinbox.in/ms/candidate/careers/a1", "source": "darwinbox"})
assert (d.years_min, d.years_max) == (1, 3), "months, not years"
assert (d.salary, d.work_mode, d.employment_type, d.posted_at) == ("₹6L–9L /yr", "remote", "full-time", 1762164000.0)
print("site readers PASS")

# --- never raises; the report says what came back thin and why ----------------------------------------


def boom(*a, **k):
    raise ConnectionError("boom")


net.json = boom
rep = details.Report()
d = details.read({"url": "https://h.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1",
                  "source": "oracle"}, rep)
assert not d.ok() and d.error.startswith("ConnectionError: boom") and d.reader == "oracle"
assert rep.summary()["thin"] == 1 and "thin: oracle 1" in rep.line(), rep.line()

# a page drawn by script: one render, while the scan's allowance lasts
real_render = browser.render
browser.render = lambda url, timeout=25.0: (
    "<html><body><main>" + "<p>Requirements: 1+ years of React and Node.js. We ship daily.</p>" * 8
    + "</main></body></html>")
rep = details.Report(browser_max=1)
d = details.read({"url": "https://spa.example.com/jobs/1", "source": "page", "title": "Frontend Dev"}, rep)
assert d.ok() and d.rendered and rep.summary()["rendered"] == 1
assert not details.read({"url": "https://spa.example.com/jobs/2", "source": "page"}, rep).rendered, \
    "the allowance is spent"
browser.render = real_render

# merge rules: a guess never replaces the list's date; a dated field does
job = {"title": "Engineer", "description": "short", "posted_at": 1762000000.0}
details.apply(job, Details(text="x" * 400, posted_guess=1761000000.0))
assert job["description"] == "x" * 400 and job["posted_at"] == 1762000000.0
details.apply(job, Details(posted_at=1763000000.0))
assert job["posted_at"] == 1763000000.0
print("report, browser and merge PASS")

# --- the scan keeps the facts: gate, closing date, storage ------------------------------------------
from jobradar import main as m
from jobradar.store import Store

st = Store(Path(tempfile.mkdtemp()) / "d.db")
base = {"company": "Acme", "url": "u", "location": "Pune", "source": "lever", "posted_at": time.time(),
        "description": "Requirements\n• Node.js\n• React\n" + "We build things. " * 30, "domain": ""}
jobs = [dict(base, id="ok", title="Engineer"),
        dict(base, id="senior", title="Engineer", years_min=3),
        dict(base, id="closed", title="Engineer", closes_at=time.time() - 86400)]
kept = m.select_jobs(jobs, {"filters": {"max_required_years": 1}}, st)
assert [j["id"] for j in kept] == ["ok"], "the board's 3 years and a closed posting are both out"
assert kept[0]["skills"] == ["Node.js", "React"] and kept[0]["experience"] == "not stated"
st.upsert(kept)
row = st.db.execute("SELECT skills, work_mode, employment_type FROM jobs WHERE id = 'ok'").fetchone()
assert row["skills"] == "Node.js, React"
st.upsert([dict(base, id="late", title="Engineer", closes_at=time.time() - 60)])
assert st.expire_old(0) == 1 and st.application_for("late")["status"] == "expired"
print("scan facts PASS")

# --- fast and slow queues ---------------------------------------------------------------------------------
calls = []


def fake_fetch(entry):
    lane = threading.current_thread().name.split("_")[0]
    calls.append((entry["name"], lane, net._timeout()))
    if entry["name"] == "Flaky" and lane == "fast":
        return [], "ReadTimeout: HTTPSConnectionPool(host='x', port=443): Read timed out. (read timeout=8)"
    return [{"id": entry["name"]}], None


m.sources.fetch_company = fake_fetch
st.save_board_speeds({"Slowpoke": 30.0})
lanes: dict = {}
out = list(m.fetch_boards([{"name": n} for n in ("A", "B", "Slowpoke", "Flaky")],
                          {"fetch_concurrency": 4}, st, lanes))
assert sorted(e["name"] for e, _, _ in out) == ["A", "B", "Flaky", "Slowpoke"], "each board once"
assert all(err is None for _, _, err in out), "the slow queue's second try succeeded"
seen: dict = {}
for name, lane, timeout in calls:
    seen.setdefault(name, []).append((lane, timeout))
assert seen["A"] == [("fast", 8.0)], seen
assert seen["Slowpoke"] == [("slow", net.TIMEOUT)], "slow last time: straight to the slow queue"
assert seen["Flaky"] == [("fast", 8.0), ("slow", net.TIMEOUT)], "timed out fast: tried again slow"
assert lanes == {"fast": 3, "slow": 1, "moved": 1}, lanes
assert set(st.board_speeds()) == {"A", "B", "Flaky", "Slowpoke"}
print("fast/slow queues PASS")
