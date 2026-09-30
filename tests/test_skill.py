"""skill.py: resume skills ranked by use, and what MERN / Python jobs ask for."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobradar.skill import find_skills, job_tracks, resume_skills, skill_report

# --- matching survives PDF-glued text, and longer names win overlaps ---------
assert find_skills("building backend services withJava, Spring Boot") >= {"Java", "Spring Boot"}
assert find_skills("appliedGitfor version control") == {"Git"}
assert find_skills("usingNext.js, React, Node.js (Express)") == {"Next.js", "React", "Node.js",
                                                               "Express.js"}
assert "Java" not in find_skills("JavaScript and TypeScript"), "JavaScript is not Java"
assert find_skills("Tailwind CSS") == {"Tailwind CSS"}, "Tailwind CSS is not also CSS"
assert find_skills("C++ and SQL") == {"C++", "SQL"}
assert "REST APIs" not in find_skills("the rest of the team"), "'rest' is English"
assert "Express.js" not in find_skills("Expression of interest"), "a word, not the skill"
assert "Golang" not in find_skills("go live next week")
assert find_skills("Firebaseon amicroservices") >= {"Firebase"}

# --- ranking: work beats projects beats a bare listing ------------------------
RESUME = """Test Person
EXPERIENCE
Acme May 2025 – Present
Software Engineer Bengaluru, India
•Built a payment service with Java, Spring Boot and PostgreSQL behind REST APIs
for sellers in production.
•Wrote PostgreSQL migrations and Spring Boot jobs for settlements and payouts.
PROJECTS
Helper App|React.js, Node.js, Express.js, MongoDB/github
•Built REST APIs in Express.js with MongoDB geospatial queries.
Tracker|Next.js, React, Firebase
•Dashboard in Next.js
TECHNICAL SKILLS
Languages: Java, Python, SQL
Backend: Spring Boot, Node.js, Express.js, Docker
"""
rows = resume_skills(RESUME)
by = {r["name"]: r for r in rows}
assert rows[0]["name"] in ("Spring Boot", "PostgreSQL"), [r["name"] for r in rows[:3]]
assert by["Spring Boot"]["tier"] == "strong" and by["Spring Boot"]["work"] == 2
assert by["React"]["tier"] == "working" and by["React"]["projects"] == 2
assert by["Python"]["tier"] == "listed" and by["Docker"]["tier"] == "listed"
scores = [r["score"] for r in rows]
assert scores == sorted(scores, reverse=True), "most done first, least done last"
assert rows.index(by["Python"]) > rows.index(by["React"]) > rows.index(by["Java"])

# --- tracks and what to build next ------------------------------------------------
assert job_tracks("MERN Stack Developer", "") == {"mern"}
assert job_tracks("Full Stack Developer", "React, Node.js and MongoDB") == {"mern"}
assert job_tracks("Python Developer", "") == {"python"}
assert job_tracks("Backend Engineer", "Python with Django and PostgreSQL") == {"python"}
assert job_tracks("Backend Engineer", "Python scripting for tests") == set(), \
    "Python alone in the text is not a Python web role"

jobs = [
    {"title": "Python Developer", "description": "Python, Django, Docker, Kubernetes, REST APIs"},
    {"title": "Python Backend Engineer", "description": "Python, FastAPI, Docker, PostgreSQL"},
    {"title": "MERN Developer", "description": "React, Node.js, Express, MongoDB, Redux, NoSQL"},
]
rep = skill_report(RESUME, jobs)
py = next(t for t in rep["tracks"] if t["key"] == "python")
mern = next(t for t in rep["tracks"] if t["key"] == "mern")
assert py["jobs"] == 2 and mern["jobs"] == 1
work_on = [a["name"] for a in py["work_on"]]
assert set(work_on[:2]) == {"Python", "Docker"}, work_on     # listed only, asked by every job
assert "Django" in work_on and "FastAPI" in work_on           # never on the resume
assert "PostgreSQL" not in work_on and "REST APIs" not in work_on, "already shown at work"
mern_on = [a["name"] for a in mern["work_on"]]
assert "Redux" in mern_on and "NoSQL" not in mern_on, "MongoDB counts as NoSQL"
assert skill_report(RESUME, [])["tracks"][0]["jobs"] == 0, "no jobs collected yet"

# --- your own top skills (skills.top in config.yaml) --------------------------
from jobradar.skill import pin_top, resolve_skill

# spellings as typed, resolved to the skill's name
for raw, want in [("node", "Node.js"), ("Postgress", "PostgreSQL"), ("rest api", "REST APIs"),
                  ("nest", None), ("mongos", "MongoDB"), ("java script", "JavaScript"),
                  ("type script", "TypeScript"), ("ci/cd", "CI/CD"), ("llm api", "LLM APIs"),
                  ("express js", "Express.js"), ("my sql", "MySQL"), ("Java", "Java")]:
    assert resolve_skill(raw) == want, (raw, resolve_skill(raw), want)

rows, unknown = pin_top(resume_skills(RESUME), ["Python", "node", "AWS", "Kotlin-ish", "python"])
assert [r["name"] for r in rows[:3]] == ["Python", "Node.js", "AWS"], "yours first, in your order"
assert all(r["tier"] == "top" and r["rating"] == 10 for r in rows[:3])
assert rows[3]["tier"] != "top", "only the listed skills are pinned"
assert unknown == ["Kotlin-ish"], unknown
assert len([r for r in rows if r["name"] == "Python"]) == 1, "pinned once, not duplicated"
assert rows[2]["work"] == 0, "a top skill the resume never shows is still added"

rep = skill_report(RESUME, jobs, top_skills=["Python", "Docker"])
py = next(t for t in rep["tracks"] if t["key"] == "python")
assert "Python" not in [a["name"] for a in py["work_on"]], "a top skill is not a gap"
assert next(a for a in py["asked"] if a["name"] == "Python")["yours"] == "top"
assert pin_top(rows, None) == (rows, []), "no list, no change"

# 8/10 ("good") comes right after 10/10, and a skill in both keeps 10/10
rows, unknown = pin_top(resume_skills(RESUME), ["Java"], ["git", "dsa", "oops", "networking", "Java"])
assert [(r["name"], r["rating"]) for r in rows[:5]] == [
    ("Java", 10), ("Git", 8), ("DSA", 8), ("OOP", 8), ("Computer Networks", 8)], rows[:5]
assert rows[1]["tier"] == "good" and unknown == []

print("skill tests PASS")
