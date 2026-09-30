"""The company table: imports from any source land in one row per company, a
readable board is never downgraded by a later link-only import, and only
enabled readable boards reach a scan."""
import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jobradar.store import Store
from jobradar.companies import careers_url, config_companies, detect_board, import_file

# --- board detection from any careers / job link -------------------------------
for url, want in [
    ("https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/1", ("greenhouse", "razorpaysoftwareprivatelimited")),
    ("https://jobs.lever.co/fampay/abc", ("lever", "fampay")),
    ("https://adobe.wd5.myworkdayjobs.com/en-US/external_experienced/job/x", ("workday", "adobe/wd5/external_experienced")),
    ("https://fynd.keka.com/careers/jobdetails/1", ("keka", "fynd")),
    ("https://myhr.darwinbox.in/ms/candidate/careers/a1", ("darwinbox", "myhr.darwinbox.in")),
    ("https://wissen.zohorecruit.in/jobs/Careers/1/x", ("zoho", "wissen.zohorecruit.in")),
    ("https://careers.cisco.com/global/en/job/1", ("other", "")),
    ("https://acme.com/careers?gh_jid=12", ("other", "")),        # embedded board, slug unknown
]:
    assert detect_board(url) == want, (url, detect_board(url), want)
assert careers_url("keka", "fynd") == "https://fynd.keka.com/careers"

# --- one row per company, merged across sources ------------------------------
d = Path(tempfile.mkdtemp())
s = Store(d / "t.db")
(d / "c.json").write_text(json.dumps([
    {"name": "Fynd", "board": "keka", "slug": "fynd", "source": "sheet"},
    {"name": "Broken Co", "board": "lever", "slug": "nope", "enabled": False, "note": "board not found"},
]), encoding="utf-8")
(d / "list.txt").write_text("fynd|other|x|https://fynd.keka.com/careers|low\n"
                            "Cisco|other||https://careers.cisco.com|low\n"
                            "Razorpay|greenhouse|razorpaysoftwareprivatelimited||high\n"
                            "not a line\n", encoding="utf-8")
assert import_file(d / "c.json", s)["added"] == 2
got = import_file(d / "list.txt", s)
assert got["added"] == 2, got                                   # Cisco, Razorpay; fynd merges
rows = {c["name"]: c for c in s.companies()}
assert rows["Fynd"]["board"] == "keka" and rows["Fynd"]["slug"] == "fynd", "not downgraded to other"
assert rows["Cisco"]["enabled"] == 0 and rows["Cisco"]["careers_url"], "unreadable kept, not scanned"
assert rows["Razorpay"]["careers_url"].endswith("/razorpaysoftwareprivatelimited")

# --- only enabled, readable boards are scanned; export round-trips ---------------
scan = {c["name"] for c in config_companies(s)}
assert scan == {"Fynd", "Razorpay"}, scan
assert s.export_companies(d / "out.json") == 4
s2 = Store(d / "t2.db")
assert import_file(d / "out.json", s2)["added"] == 4
assert {c["name"] for c in config_companies(s2)} == scan, "export -> import keeps what is scanned"
print("company table tests PASS")
