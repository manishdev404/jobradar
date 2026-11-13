"""The extra platform readers and the generic careers-page reader, on canned
pages (no network): titles, links and India locations come out right."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jobradar import boards_more as bm
from jobradar.companies import careers_url, detect_board

PAGES, JSON = {}, {}
bm._page = lambda url, limit=0: (url, PAGES[url])
bm._json = lambda url, **kw: JSON[url.split("?")[0]]

# --- country codes become names, so the India filter matches --------------------
assert bm._join("Pune", "IN") == "Pune, India"
assert bm._join("Remote", "in") == "Remote, India"
assert bm._join("", "Bengaluru", None) == "Bengaluru"

# --- JSON boards -------------------------------------------------------------------
JSON["https://acme.bamboohr.com/careers/list"] = {"result": [
    {"id": 7, "jobOpeningName": "Backend Engineer", "location": {"city": "Pune", "state": "MH"},
     "atsLocation": {"country": "IN"}, "isRemote": False}]}
j = bm.bamboohr("Acme", "acme")[0]
assert (j["title"], j["location"], j["url"]) == \
    ("Backend Engineer", "Pune, MH, India", "https://acme.bamboohr.com/careers/7"), j

JSON["https://api.smartrecruiters.com/v1/companies/Acme/postings"] = {"totalFound": 1, "content": [
    {"id": "9", "name": "SDE 1", "location": {"city": "Bengaluru", "region": "KA", "country": "in"}}]}
j = bm.smartrecruiters("Acme", "Acme")[0]
assert j["location"] == "Bengaluru, KA, India" and j["url"].endswith("/Acme/9"), j

# --- HTML boards ---------------------------------------------------------------------
PAGES["https://acme.freshteam.com/jobs"] = (
    '<a href="/jobs/AbC/sde-1" class="heading" data-portal-location="Delhi, India">'
    '<div class="job-title">SDE-1</div></a>')
j = bm.freshteam("Acme", "acme")[0]
assert (j["title"], j["location"], j["url"]) == \
    ("SDE-1", "Delhi, India", "https://acme.freshteam.com/jobs/AbC/sde-1"), j

PAGES["https://jobs.jobvite.com/acme/jobs"] = (
    '<a href="/acme/job/o1"><div class="jv-job-list-name">Full Stack Developer</div>'
    '<div class="jv-job-list-location">Hyderabad, India</div></a>')
j = bm.jobvite("Acme", "acme")[0]
assert (j["title"], j["location"]) == ("Full Stack Developer", "Hyderabad, India"), j

# --- generic page: JobPosting data first ---------------------------------------------
PAGES["https://acme.com/careers"] = (
    '<script type="application/ld+json">{"@type": "JobPosting", "title": "React Developer", '
    '"url": "https://acme.com/careers/react", "jobLocation": {"address": '
    '{"addressLocality": "Noida", "addressCountry": "IN"}}}</script>')
j = bm.page("Acme", "https://acme.com/careers")[0]
assert (j["title"], j["location"], j["url"]) == \
    ("React Developer", "Noida, India", "https://acme.com/careers/react"), j

# --- generic page: job-like links, nav links ignored ---------------------------------
PAGES["https://beta.in/careers"] = """<html><body><p>Offices in Bengaluru</p>
<a href="/careers">Careers</a> <a href="/about">About us</a>
<a href="/careers/backend-engineer">Backend Engineer</a>
<a href="/careers/apply">Apply now</a></body></html>"""
jobs = bm.page("Beta", "https://beta.in/careers")
assert [j["title"] for j in jobs] == ["Backend Engineer"], jobs
assert jobs[0]["location"] == "Bengaluru", "a city next to the link is the job's location"
PAGES["https://gamma.in/careers"] = ("<p>We are hiring across India.</p>" + "\n" * 10 +
                                     '<a href="/jobs/qa">QA Engineer</a>')
assert bm.page("Gamma", "https://gamma.in/careers")[0]["location"] == "India", \
    "no city near the link: a page that mentions India counts as India"

# --- links to the new platforms are recognised; their own sites are not ---------------
assert detect_board("https://zeni.bamboohr.com/careers/12") == ("bamboohr", "zeni")
assert detect_board("https://www.bamboohr.com/pricing") == ("other", "")
assert detect_board("https://wd3.myworkdaysite.com/recruiting/magna/Magna/job/x") == \
    ("workdaysite", "wd3/magna/Magna")
assert careers_url("jobvite", "acme") == "https://jobs.jobvite.com/acme/jobs"
print("extra boards tests PASS")
