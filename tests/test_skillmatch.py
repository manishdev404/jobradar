"""The skill gate: a title that names no role is kept when its posting asks
for enough of your top skills, and only then."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jobradar import skillmatch
import jobradar.main as M
from jobradar.store import Store

SM_CFG = {"enabled": True, "min_skills": 3, "priority": 6, "bonus": 0,
          "title_words": ["engineer", "developer", "member of technical staff"],
          "exclude_words": ["qa", "test", "sales", "data", "android"]}
CFG = {"skills": {"top": ["node", "React", "postgress", "TypeScript", "Java", "AWS"]},
       "filters": {"roles": [{"name": "Backend", "priority": 1, "bonus": 3,
                              "match": ["backend"]}],
                   "skill_match": SM_CFG,
                   "locations": ["india"]}}
sm = skillmatch.settings(CFG["filters"])
wanted = skillmatch.wanted_skills(CFG, sm)

# --- settings and skill names ---------------------------------------------
assert skillmatch.settings({}) is None, "off unless enabled"
assert skillmatch.settings({"skill_match": {"enabled": False}}) is None
assert sm["name"] == "Skill match", "a default name is filled in"
assert wanted == {"Node.js", "React", "PostgreSQL", "TypeScript", "Java", "AWS"}, wanted

# --- candidate titles: engineering, not another field ----------------------
assert skillmatch.is_candidate("Product Engineer", sm)
assert skillmatch.is_candidate("Member of Technical Staff - Platform", sm)
assert not skillmatch.is_candidate("QA Engineer", sm), "excluded field"
assert not skillmatch.is_candidate("Data Engineer", sm)
assert not skillmatch.is_candidate("Android Developer", sm)
assert not skillmatch.is_candidate("Account Executive", sm), "not an engineering title"
assert not skillmatch.is_candidate("Engineering Recruiter", sm), "whole words: 'engineering'"
assert not skillmatch.is_candidate("Software Engineer in Test", sm), \
    "an excluded word anywhere in the title wins"
assert skillmatch.is_candidate("Product  Engineers", sm), "plural and padded spaces"

# --- role titles: plurals and padded spaces (roles.py) ----------------------
from jobradar.roles import classify
R = [{"name": "Backend", "match": ["node js developer"]},
     {"name": "SWE", "match": ["software engineer"]}]
assert classify("Node Js Developers", R)["role"] == "Backend"
assert classify("Software  Engineer", R)["role"] == "SWE"
assert classify("Software Engineering Advisor", R) is None, "'engineering' is not 'engineer'"

# --- the posting decides -----------------------------------------------------
fit = {"title": "Product Engineer",
       "description": "<p>You will build APIs in <b>Node.js</b> and TypeScript, "
                      "with a React front end and PostgreSQL.</p>"}
thin = {"title": "Product Engineer", "description": "Build things with Node.js and Go."}
role = skillmatch.role(fit, sm, wanted)
assert role == {"role": "Skill match", "role_rank": 6, "role_bonus": 0.0}, role
assert skillmatch.role(thin, sm, wanted) is None, "one skill is not enough"
assert skillmatch.role({**fit, "title": "QA Engineer"}, sm, wanted) is None
assert skillmatch.skills_named({"title": "Java Developer", "description": "JavaScript only"},
                               wanted) == {"Java"}, "JavaScript must not count as Java"
w2 = wanted | {"Express.js"}
assert "Express.js" not in skillmatch.skills_named(
    {"title": "Engineer", "description": "Join American Express. Express your interest."}, w2), \
    "plain-English 'express' is not the framework"
assert "Express.js" in skillmatch.skills_named(
    {"title": "Engineer", "description": "APIs in Node.js and Express.js"}, w2)

# --- wired into the scan filters --------------------------------------------
f = CFG["filters"]
job = {"id": "p1", "company": "Acme", "title": "Product Engineer", "location": "Bengaluru, India",
       "url": "u", "source": "greenhouse", "posted_at": None, "description": fit["description"]}
assert M.passes_filters(job, f, check_experience=False), "a candidate passes the title checks"
assert not M.passes_filters({**job, "title": "Sales Engineer"}, f, check_experience=False)
assert M.job_role(job, CFG)["role"] == "Skill match"
assert M.job_role({**job, "title": "Backend Engineer"}, CFG)["role"] == "Backend", \
    "a named role always wins over the skill match"

off = {**CFG, "filters": {k: v for k, v in f.items() if k != "skill_match"}}
assert not M.passes_filters(job, off["filters"], check_experience=False), \
    "without skill_match, a title naming no role is dropped as before"

st = Store(os.path.join(tempfile.mkdtemp(), "s.db"))
long_desc = fit["description"] + " " + "x" * 400      # long enough: no page read
kept = M.select_jobs([{**job, "description": long_desc},
                      {**job, "id": "p2", "description": "Go and Rust. " + "y" * 400}],
                     CFG, st)
assert [j["id"] for j in kept] == ["p1"], kept
assert kept[0]["role"] == "Skill match" and kept[0]["role_rank"] == 6

print("skillmatch ok")
