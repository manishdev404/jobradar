"""
Your skills, ranked by how much you have actually done with them, and the
skills MERN and Python full-stack postings ask for that you should build next.

Ranking reads the resume, not a model. A skill counts for more the closer it
is to real work:
  * each work-experience bullet that uses it     3 points
  * each project that uses it (stack or bullets) 2 points
  * listed under TECHNICAL SKILLS                1 point

So "Spring Boot" used in a production payment service outranks "Python" that
only appears in the skills list.

Tiers make the ranking readable:
  strong   used at work more than once, or at work and in a project
  working  used at work or in a project
  listed   only named in the skills list - claimed, not shown

Demand reads the jobs Job Radar has collected (India, your level, your roles):
  * a job belongs to the MERN track when it names at least two of
    MongoDB, Express, React and Node
  * a job belongs to the Python track when Python is in its title or it
    pairs Python with Django, Flask or FastAPI

A skill to work on is one those jobs often ask for that your resume only lists,
or never mentions.

PDF extraction can glue words together ("withJava", "usingNext.js"), so a skill
also matches straight after a lowercase letter when it starts with a capital.
"""

from __future__ import annotations

import re
from pathlib import Path

from .resume import EXPERIENCE, _normalize, _sections


# ---------------------------------------------------------------------------
# Skill definitions
#
# (name, category, aliases)
#
# An alias starting with "=" is case-sensitive:
#   "=REST" -> REST
#   "=JS"   -> JS
#
# Python is intentionally included as a resume-listed skill. Because it is
# only listed in the current resume and not demonstrated in work/projects,
# the scoring system will naturally keep it near the bottom.
# ---------------------------------------------------------------------------

SKILLS: list[tuple[str, str, list[str]]] = [

    # -----------------------------------------------------------------------
    # Languages
    # -----------------------------------------------------------------------

    ("JavaScript", "Languages", [
        "javascript",
        "=JS",
    ]),

    ("TypeScript", "Languages", [
        "typescript",
    ]),

    ("Java", "Languages", [
        "java",
    ]),

    ("C++", "Languages", [
        "c++",
        "cpp",
    ]),

    ("SQL", "Languages", [
        "sql",
    ]),

    # Python is listed last intentionally, but final ranking is determined
    # by score rather than this list order.
    ("Python", "Languages", [
        "python",
    ]),

    # -----------------------------------------------------------------------
    # Frontend
    # -----------------------------------------------------------------------

    ("React", "Frontend", [
        "react",
        "react.js",
        "reactjs",
    ]),

    ("Next.js", "Frontend", [
        "next.js",
        "nextjs",
    ]),

    ("Tailwind CSS", "Frontend", [
        "tailwind css",
        "tailwindcss",
        "tailwind",
    ]),

    ("React Query", "Frontend", [
        "react query",
        "tanstack query",
    ]),

    ("Zustand", "Frontend", [
        "zustand",
    ]),

    ("Framer Motion", "Frontend", [
        "framer motion",
    ]),

    ("HTML", "Frontend", [
        "html",
        "html5",
    ]),

    ("CSS", "Frontend", [
        "css",
        "css3",
    ]),

    # -----------------------------------------------------------------------
    # Backend
    # -----------------------------------------------------------------------

    ("Node.js", "Backend", [
        "node.js",
        "nodejs",
        "=Node",
    ]),

    ("Express.js", "Backend", [
        "express.js",
        "expressjs",
        "express",
    ]),

    ("Spring Boot", "Backend", [
        "spring boot",
        "springboot",
    ]),

    ("REST APIs", "Backend", [
        "rest apis",
        "rest api",
        "restful apis",
        "restful api",
        "restful",
        "rest endpoints",
        "=REST",
    ]),

    ("Microservices", "Backend", [
        "microservices",
        "microservice",
    ]),

    ("RBAC", "Backend", [
        "rbac",
        "role-based access",
    ]),

    ("JWT", "Backend", [
        "jwt",
    ]),

    ("GraphQL", "Backend", [
        "graphql",
    ]),

    ("WebSockets", "Backend", [
        "websockets",
        "websocket",
    ]),

    # -----------------------------------------------------------------------
    # Databases
    # -----------------------------------------------------------------------

    ("PostgreSQL", "Databases", [
        "postgresql",
        "postgres",
    ]),

    ("MongoDB", "Databases", [
        "mongodb",
        "mongo",
    ]),

    ("MySQL", "Databases", [
        "mysql",
    ]),

    ("Firestore", "Databases", [
        "firestore",
        "cloud firestore",
    ]),

    ("Firebase", "Cloud", [
        "firebase",
    ]),

    # -----------------------------------------------------------------------
    # Cloud / DevOps
    # -----------------------------------------------------------------------

    ("AWS", "Cloud & DevOps", [
        "aws",
        "amazon web services",
    ]),

    ("Docker", "Cloud & DevOps", [
        "docker",
    ]),

    ("CI/CD", "Cloud & DevOps", [
        "ci/cd",
        "cicd",
        "ci cd",
    ]),

    ("Git", "Cloud & DevOps", [
        "git",
    ]),

    ("Linux", "Cloud & DevOps", [
        "linux",
    ]),

    # -----------------------------------------------------------------------
    # Testing / Development Tools
    # -----------------------------------------------------------------------

    ("Postman", "Testing", [
        "postman",
    ]),

    # -----------------------------------------------------------------------
    # AI / ML
    # -----------------------------------------------------------------------

    ("LLM APIs", "AI / ML", [
        "llm apis",
        "llm api",
        "llms",
        "llm",
        "large language model",
    ]),

    ("RAG", "AI / ML", [
        "=RAG",
        "retrieval-augmented",
        "retrieval augmented",
    ]),

    # -----------------------------------------------------------------------
    # Integrations
    # -----------------------------------------------------------------------

    ("Algolia", "Integrations", [
        "algolia",
    ]),

    ("PayU", "Integrations", [
        "payu",
    ]),

    ("SendGrid", "Integrations", [
        "sendgrid",
    ]),

    ("Shopify API", "Integrations", [
        "shopify api",
        "shopify",
    ]),

    # -----------------------------------------------------------------------
    # Core CS
    # -----------------------------------------------------------------------

    ("DSA", "Core CS", [
        "data structures & algorithms",
        "data structures and algorithms",
        "data structures",
        "=DSA",
    ]),

    ("System Design", "Core CS", [
        "system design",
    ]),

    ("DBMS", "Core CS", [
        "dbms",
    ]),

    ("Operating Systems", "Core CS", [
        "operating systems",
        "=OS",
    ]),

    ("Computer Networks", "Core CS", [
        "computer networks",
        "networking",
    ]),
]


# ---------------------------------------------------------------------------
# Market-only skills
#
# Not on the resume, but MERN / Python jobs ask for them. Without these the
# "Build next" list could never name Django or Kubernetes - a skill has to be
# known to be counted in a job description.
# ---------------------------------------------------------------------------

SKILLS += [
    ("Golang", "Languages", ["golang", "=Go"]),
    ("Redux", "Frontend", ["redux", "redux toolkit"]),
    ("Angular", "Frontend", ["angular", "angularjs"]),
    ("Vue", "Frontend", ["vue", "vue.js", "vuejs"]),
    ("Django", "Backend", ["django"]),
    ("Flask", "Backend", ["flask"]),
    ("FastAPI", "Backend", ["fastapi"]),
    ("Kafka", "Backend", ["kafka"]),
    ("Redis", "Databases", ["redis"]),
    ("NoSQL", "Databases", ["nosql"]),
    ("Azure", "Cloud & DevOps", ["azure"]),
    ("GCP", "Cloud & DevOps", ["gcp", "google cloud"]),
    ("Kubernetes", "Cloud & DevOps", ["kubernetes", "k8s"]),
    ("Jest", "Testing", ["jest"]),
    ("Pytest", "Testing", ["pytest"]),
    ("Unit testing", "Testing", ["unit testing", "unit tests", "unit test"]),
    ("OOP", "Core CS", ["=OOPs", "=OOPS", "=OOP", "object-oriented", "object oriented"]),
]


_INFO = {
    name: (category, aliases)
    for name, category, aliases in SKILLS
}


# Common English words where glued matching can produce false positives.
_NO_GLUE_AFTER = {
    "express",
    "react",
    "java",
    "spring",
    "node",
    "rest",
    "flask",
    "angular",
    "redux",
}


# ---------------------------------------------------------------------------
# Job tracks
# ---------------------------------------------------------------------------

MERN = (
    "MongoDB",
    "Express.js",
    "React",
    "Node.js",
)

PY_WEB = (
    "Django",
    "Flask",
    "FastAPI",
)

TRACKS = {
    "mern": "MERN full stack",
    "python": "Python full stack / backend",
}

# "top" and "good" are yours to set in config.yaml (skills.top, skills.good):
# rated 10/10 and 8/10, shown first in your order, whatever the resume shows.
TIERS = (
    "top",
    "good",
    "strong",
    "working",
    "listed",
)

TOP_RATING = 10
GOOD_RATING = 8
RATED = {TOP_RATING: "top", GOOD_RATING: "good"}

# A capitalised skill glued to a small word by PDF extraction: "appliedGitfor".
_GLUED_WORD = re.compile(
    r"(?:for|and|to|with|in|on|as|at|via|of|from|using)(?![A-Za-z])"
)

# Using the right-hand skills is using the left-hand one: a MongoDB user
# knows NoSQL. The implied skill takes their best tier.
IMPLIES = {
    "NoSQL": ("MongoDB", "Firestore", "Firebase", "Redis"),
    "CSS": ("Tailwind CSS",),
    "HTML": ("React", "Next.js"),
    "JavaScript": ("TypeScript", "React", "Node.js"),
    "SQL": ("PostgreSQL", "MySQL"),
}


# ---------------------------------------------------------------------------
# Skill matching
# ---------------------------------------------------------------------------

def _patterns():
    out = []

    for name, _, aliases in SKILLS:
        for alias in aliases:
            flags = 0 if alias.startswith("=") else re.I

            out.append(
                (
                    name,
                    alias.lstrip("="),
                    re.compile(
                        re.escape(alias.lstrip("=")),
                        flags,
                    ),
                )
            )

    return out


_PATTERNS = _patterns()


def _ok_edges(
    text: str,
    s: int,
    e: int,
    alias: str,
) -> bool:
    """
    Handle normal word boundaries plus common PDF extraction problems.

    Examples:
        withJava       -> Java should match
        usingNext.js   -> Next.js should match
        gjava          -> Java should NOT match
        C++            -> C++ should match
    """

    before = text[s - 1] if s else " "
    after = text[e] if e < len(text) else " "

    word_end = alias[-1].isalnum()

    # PDF extraction may glue:
    #   "withJava"
    #
    # Accept when a lowercase character is immediately followed by the
    # capitalized skill name.
    ok_before = (
        not before.isalnum()
        or (
            before.islower()
            and text[s].isupper()
        )
    )

    if not ok_before:
        return False

    # Prevent C from accidentally matching C++ / C#.
    if after in "+#" and word_end:
        return False

    # Normal boundary.
    if not after.isalnum():
        return True

    # Allow camel-case / glued technology names:
    #
    # ReactQuery
    # withJava
    # usingNext.js
    if after.isupper() and text[e - 1].islower():
        return True

    # appliedGitfor -> Git, glued to a small word.
    if text[s].isupper() and _GLUED_WORD.match(text, e):
        return True

    # Examples:
    # Firebaseon
    # Algoliareal-time
    #
    # These can occur because of PDF extraction.
    return (
        text[s].isupper()
        and len(alias) >= 6
        and alias.lower() not in _NO_GLUE_AFTER
        and after.islower()
    )


def find_skills(text: str) -> set[str]:
    """
    Find skill names in text.

    Longer aliases win overlaps so:
        JavaScript -> JavaScript, not Java
        Tailwind CSS -> Tailwind CSS, not CSS
    """

    return set(skills_in_order(text))


def skills_in_order(text: str) -> list[str]:
    """
    Skill names in text, in the order they first appear.

    A posting tends to list its must-haves first, so a cut list keeps them.
    """

    spans = []

    for name, alias, pattern in _PATTERNS:
        for match in pattern.finditer(text or ""):
            if _ok_edges(
                text,
                match.start(),
                match.end(),
                alias,
            ):
                spans.append(
                    (
                        match.start(),
                        match.end(),
                        name,
                    )
                )

    # Longer matches first.
    spans.sort(
        key=lambda x: (
            -(x[1] - x[0]),
            x[0],
        )
    )

    taken: list[tuple[int, int]] = []
    first: dict[str, int] = {}

    for start, end, name in spans:

        # Ignore overlapping shorter matches.
        if any(
            start < taken_end
            and taken_start < end
            for taken_start, taken_end in taken
        ):
            continue

        taken.append((start, end))
        first[name] = min(first.get(name, start), start)

    return sorted(first, key=first.get)


# ---------------------------------------------------------------------------
# Resume side
# ---------------------------------------------------------------------------

_BULLET = re.compile(
    r"^\s*[•●▪◦*]\s*"
)

_PROJECT_HEAD = re.compile(
    r"^([^•|]{3,80})\|(.+)$"
)

_SKILL_SECTIONS = {
    "skills",
    "technical skills",
    "core skills",
    "key skills",
}

# "Solved 400+ DSA problems" is shown work, like a project.
_PRACTICE = {
    "competitive programming",
    "achievements",
    "awards",
}


def _bullets(lines: list[str]) -> list[str]:
    """
    Join wrapped PDF lines back into complete bullets.
    """

    out: list[str] = []

    for line in lines:

        if _BULLET.match(line) or not out:
            out.append(
                _BULLET.sub("", line)
            )
        else:
            out[-1] += " " + line

    return out


def resume_skills(text: str) -> list[dict]:
    """
    Return every skill found in the resume.

    Scoring:
        work bullet = 3
        project = 2
        technical-skills listing = 1

    Therefore a skill only listed in TECHNICAL SKILLS, such as Python,
    receives only 1 point unless it is demonstrated elsewhere.
    """

    sections = _sections(
        _normalize(text).splitlines()
    )

    work: dict[str, set[str]] = {}
    projects: dict[str, set[str]] = {}
    listed: set[str] = set()

    # -----------------------------------------------------------------------
    # Experience
    # -----------------------------------------------------------------------

    for name, body in sections.items():

        if name in EXPERIENCE:

            unique_bullets = {
                re.sub(
                    r"\s+",
                    " ",
                    bullet,
                ).strip().lower(): bullet
                for bullet in _bullets(body)
            }

            for bullet in unique_bullets.values():

                # Company / title lines are not work evidence.
                if len(bullet) < 40:
                    continue

                for skill in find_skills(bullet):
                    work.setdefault(
                        skill,
                        set(),
                    ).add(
                        bullet.lower()
                    )

        # -------------------------------------------------------------------
        # Practice (competitive programming, achievements)
        # -------------------------------------------------------------------

        elif name in _PRACTICE:

            for line in body:
                for skill in find_skills(line):
                    projects.setdefault(
                        skill,
                        set(),
                    ).add(name)

        # -------------------------------------------------------------------
        # Projects
        # -------------------------------------------------------------------

        elif "project" in name:

            current = None

            for line in body:

                head = _PROJECT_HEAD.match(line)

                if head and not _BULLET.match(line):
                    current = re.sub(
                        r"\s+",
                        " ",
                        head.group(1),
                    ).strip().lower()

                if current:

                    for skill in find_skills(line):
                        projects.setdefault(
                            skill,
                            set(),
                        ).add(current)

        # -------------------------------------------------------------------
        # Technical Skills
        # -------------------------------------------------------------------

        elif name in _SKILL_SECTIONS:

            for line in body:
                listed |= find_skills(line)

    # -----------------------------------------------------------------------
    # Build ranking
    # -----------------------------------------------------------------------

    out = []

    for skill in set(work) | set(projects) | listed:

        work_count = len(
            work.get(skill, ())
        )

        project_count = len(
            projects.get(skill, ())
        )

        is_listed = skill in listed

        score = (
            3 * work_count
            + 2 * project_count
            + int(is_listed)
        )

        if work_count >= 2 or (
            work_count and project_count
        ):
            tier = "strong"

        elif work_count or project_count:
            tier = "working"

        else:
            tier = "listed"

        out.append(
            {
                "name": skill,
                "category": _INFO[skill][0],
                "score": score,
                "tier": tier,
                "work": work_count,
                "projects": project_count,
                "listed": is_listed,
            }
        )

    # Highest actual evidence first.
    #
    # Python will NOT be artificially pushed upward just because it appears
    # earlier in SKILLS. Since it is only listed, it receives score = 1.
    out.sort(
        key=lambda row: (
            -row["score"],
            row["name"].lower(),
        )
    )

    return out


def resolve_skill(name: str) -> str | None:
    """"node", "Postgress", "rest api" -> the skill's name in SKILLS, or None."""

    key = re.sub(r"[\s.]+", "", (name or "").lower())

    for skill, _, aliases in SKILLS:
        forms = [skill] + [alias.lstrip("=") for alias in aliases]

        if key in {re.sub(r"[\s.]+", "", form.lower()) for form in forms}:
            return skill

    # A common misspelling: "postgress", "mongos".
    for skill, _, aliases in SKILLS:
        forms = [skill] + [alias.lstrip("=") for alias in aliases]

        if any(
            len(key) >= 4
            and re.sub(r"[\s.]+", "", form.lower()).startswith(key[:-1])
            for form in forms
        ):
            return skill

    return None


def pin_top(
    rows: list[dict],
    top: list[str] | None,
    good: list[str] | None = None,
) -> tuple[list[dict], list[str]]:
    """
    Put your own rated skills first: top (10/10) then good (8/10), each in
    your order. The rest follow, ranked from the resume.

    A rated skill the resume never shows is still added: you know your skills
    better than a PDF does. Returns (rows, names that match no known skill).
    """

    groups = [(TOP_RATING, top or []), (GOOD_RATING, good or [])]

    if not any(names for _, names in groups):
        return rows, []

    by_name = {row["name"]: row for row in rows}
    pinned: list[dict] = []
    unknown: list[str] = []

    for rating, raw in ((r, n) for r, names in groups for n in names):

        skill = resolve_skill(raw)

        if skill is None:
            unknown.append(raw)
            continue

        if any(row["name"] == skill for row in pinned):
            continue                    # the first (higher) rating wins

        row = dict(
            by_name.get(skill)
            or {
                "name": skill,
                "category": _INFO[skill][0],
                "score": 0,
                "work": 0,
                "projects": 0,
                "listed": False,
            }
        )

        row["tier"] = RATED[rating]
        row["rating"] = rating
        pinned.append(row)

    names = {row["name"] for row in pinned}
    rest = [row for row in rows if row["name"] not in names]

    return pinned + rest, unknown


# ---------------------------------------------------------------------------
# Market side
# ---------------------------------------------------------------------------

def _plain(text: str) -> str:
    from .experience import _plain as plain

    return plain(text)


def job_tracks(
    title: str,
    description: str,
) -> set[str]:
    """
    Determine which job tracks a posting belongs to.
    """

    text = (
        f"{title}\n"
        f"{_plain(description)}"
    )

    found = find_skills(text)
    tracks = set()

    # MERN:
    # At least two of MongoDB, Express, React and Node.
    if (
        sum(
            skill in found
            for skill in MERN
        ) >= 2
        or re.search(
            r"\bmern\b",
            text,
            re.I,
        )
    ):
        tracks.add("mern")

    # Python:
    # Python in title OR Python + Django/Flask/FastAPI.
    title_skills = find_skills(
        title or ""
    )

    if (
        "Python" in title_skills
        or (
            "Python" in found
            and found & set(PY_WEB)
        )
    ):
        tracks.add("python")

    return tracks


def market_demand(
    jobs: list[dict],
) -> dict[str, dict]:
    """
    Return market demand by track.

    Structure:
        {
            "mern": {
                "jobs": n,
                "demand": {
                    "React": share,
                    ...
                }
            },
            ...
        }
    """

    counts = {
        track: {}
        for track in TRACKS
    }

    totals = {
        track: 0
        for track in TRACKS
    }

    for job in jobs:

        tracks = job_tracks(
            job.get("title", ""),
            job.get("description") or "",
        )

        if not tracks:
            continue

        found = find_skills(
            f"{job.get('title', '')}\n"
            f"{_plain(job.get('description') or '')}"
        )

        for track in tracks:

            totals[track] += 1

            for skill in found:
                counts[track][skill] = (
                    counts[track].get(skill, 0)
                    + 1
                )

    result = {}

    for track in TRACKS:

        total = totals[track]

        if total:

            demand = {
                skill: count / total
                for skill, count in sorted(
                    counts[track].items(),
                    key=lambda item: -item[1],
                )
            }

        else:
            demand = {}

        result[track] = {
            "jobs": total,
            "demand": demand,
        }

    return result


# ---------------------------------------------------------------------------
# Complete skill report
# ---------------------------------------------------------------------------

def skill_report(
    resume_text: str,
    jobs: list[dict],
    min_share: float = 0.15,
    top: int = 15,
    work_on_max: int = 8,
    top_skills: list[str] | None = None,
    good_skills: list[str] | None = None,
) -> dict:
    """
    Return:
      * ranked resume skills (your top skills first when given)
      * market demand
      * skills worth building next
    """

    mine, unknown = pin_top(
        resume_skills(resume_text),
        top_skills,
        good_skills,
    )

    tier = {
        row["name"]: row["tier"]
        for row in mine
    }

    for skill, via in IMPLIES.items():
        best = [TIERS.index(tier[v]) for v in via if v in tier]

        if skill in tier:
            best.append(TIERS.index(tier[skill]))

        if best:
            tier[skill] = TIERS[min(best)]

    market = market_demand(
        jobs
    )

    tracks = []

    for key, label in TRACKS.items():

        market_data = market[key]

        asked = [
            {
                "name": skill,
                "share": round(share, 2),
                "yours": tier.get(
                    skill,
                    "missing",
                ),
            }
            for skill, share
            in market_data["demand"].items()
        ]

        work_on = [
            item
            for item in asked
            if (
                item["share"] >= min_share
                and item["yours"] in (
                    "listed",
                    "missing",
                )
            )
        ]

        tracks.append(
            {
                "key": key,
                "label": label,
                "jobs": market_data["jobs"],
                "asked": asked[:top],
                "work_on": work_on[:work_on_max],
            }
        )

    return {
        "skills": mine,
        "tracks": tracks,
        "unknown_top": unknown,
    }


# ---------------------------------------------------------------------------
# CLI output
# ---------------------------------------------------------------------------

def print_report(
    report: dict,
) -> None:

    print(
        "Your skills, most done first "
        "(3 per work bullet, 2 per project, "
        "1 if listed):"
    )

    for tier in TIERS:

        rows = [
            row
            for row in report["skills"]
            if row["tier"] == tier
        ]

        if rows:

            print(
                f"\n  {tier.upper()}"
                + {"top": f"  ({TOP_RATING}/10, skills.top in config.yaml)",
                   "good": f"  ({GOOD_RATING}/10, skills.good in config.yaml)"}.get(tier, "")
            )

            for row in rows:

                listed_text = (
                    " · listed"
                    if row["listed"]
                    else ""
                )

                mark = (
                    f"{row['rating']}/10"
                    if row.get("rating")
                    else f"{row['score']:>3}"
                )

                print(
                    f"  {mark:>5}  "
                    f"{row['name']:<22} "
                    f"{row['category']:<15} "
                    f"work {row['work']} · "
                    f"projects {row['projects']}"
                    f"{listed_text}"
                )

    if report.get("unknown_top"):
        print(
            "\n  not recognised in skills.top / skills.good (check the spelling): "
            + ", ".join(report["unknown_top"])
        )

    for track in report["tracks"]:

        print(
            f"\n{track['label']} - "
            f"{track['jobs']} collected jobs"
        )

        if not track["jobs"]:
            print(
                "    no jobs of this kind collected yet"
            )
            continue

        print(
            "    most asked: "
            + ", ".join(
                f"{item['name']} "
                f"{item['share']:.0%} "
                f"({item['yours']})"
                for item in track["asked"][:10]
            )
        )

        print(
            "    work on:    "
            + (
                ", ".join(
                    f"{item['name']} "
                    f"{item['share']:.0%}"
                    for item in track["work_on"]
                )
                or "nothing - you cover what these jobs ask"
            )
        )


# ---------------------------------------------------------------------------
# Application entry point
# ---------------------------------------------------------------------------

def report_for(
    root: Path,
    cfg: dict,
    store,
) -> dict:
    """
    Build the skill report using the configured resume and all stored jobs.
    """

    from .resume import extract_text

    text = extract_text(
        root / cfg["resume_path"]
    )

    jobs = [
        dict(row)
        for row in store.db.execute(
            """
            SELECT title, description
            FROM jobs
            WHERE length(description) > 200
            """
        )
    ]

    return skill_report(
        text,
        jobs,
        top_skills=(cfg.get("skills") or {}).get("top"),
        good_skills=(cfg.get("skills") or {}).get("good"),
    )