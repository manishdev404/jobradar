"""The web scan fetches and scores side by side. A slow board must hold up only
its own fetch slot, every company must still be saved, and running out of
scoring quota must not stop the fetch."""
import json, os, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jobradar.web.server as srv
from jobradar.llm import RateLimited
from jobradar.store import Store

NAMES = ["Slow"] + [f"Co{i}" for i in range(11)]


def fetch(entry):
    if entry["name"] == "Slow":
        time.sleep(1.5)
    jobs = [{"id": f"{entry['name']}-{i}", "company": entry["name"], "title": f"Engineer {i}",
             "url": "u", "location": "Remote", "posted_at": None, "source": "greenhouse",
             "description": "python react", "domain": ""} for i in range(2)]
    return jobs, None


srv.sources.fetch_company = fetch
srv.sources.discover = lambda q, n: ([], [])
srv.get_profile = lambda c, scorer: "profile"
srv.cfg = lambda: {
    "companies": [{"name": n, "board": "greenhouse", "slug": n.lower()} for n in NAMES],
    "discovery": {"enabled": False},
    "filters": {},
    "llm": {"provider": "fake", "batch_size": 3},
    "scan_batch": 5,
    "fetch_concurrency": 4,
    "resume_path": "resume.example.txt",
}


class Scorer:
    def __init__(self, fail_after=None):
        self.fail_after = fail_after
        self.calls = 0
        self.threads = set()

    def score_batch(self, prof, jobs):
        self.threads.add(threading.current_thread().name)
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RateLimited("out of quota")
        return {j["id"]: (7, "fits", {"skills": 80}) for j in jobs}


def run(scorer):
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    srv.store = lambda: Store(tmp)
    srv.get_scorer = lambda c: scorer
    events = []
    for chunk in srv._scan_events(limit=100):
        for line in chunk.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events, Store(tmp)


# --- the happy path ----------------------------------------------------------
scorer = Scorer()
events, st = run(scorer)
types = [e["type"] for e in events]
assert "error" not in types, [e for e in events if e["type"] == "error"]
assert types[-1] == "done", types
assert srv._state["scanning"] is False, "scanning flag must reset"

# the slow board finished last; the other 11 did not wait for it
fetches = [e["company"] for e in events if e["type"] == "fetch"]
assert sorted(fetches) == sorted(NAMES), fetches
assert fetches[-1] == "Slow", fetches

# saved in groups of scan_batch as fetches landed: 5 + 5 + 2
fetched = [e for e in events if e["type"] == "fetched"]
assert [e["checked"] for e in fetched] == [5, 10, 12], fetched
assert [e["index"] for e in events if e["type"] == "batch"] == [1, 2, 3], types

# every job saved and scored, by the scoring thread
assert st.stats()["total"] == 24, st.stats()
assert st.unscored_count() == 0, "every saved job must be scored"
assert events[-1]["scored"] == 24, events[-1]
assert scorer.threads == {"scan-score"}, scorer.threads
print("parallel scan PASS")

# --- out of quota: scoring stops, fetching carries on ------------------------
events, st = run(Scorer(fail_after=1))
types = [e["type"] for e in events]
assert "throttled" in types, types
assert types[-1] == "done", types
assert st.stats()["total"] == 24, "fetching must finish even when scoring stops"
assert 0 < 24 - st.unscored_count() < 24, st.unscored_count()
assert srv._state["scanning"] is False
print("rate-limited scan PASS")
