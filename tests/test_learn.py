"""Learning from you: what you do in the browser while the engine waits on you
is kept - answers per question, the button that moved the form on, and the
whole sequence as a recipe that is replayed the next time the engine is stuck
on the same page - and passwords never are. Uses a real (headless) browser."""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("learn tests SKIPPED (no playwright)")
    sys.exit(0)
from jobradar.apply.answers import Profile
from jobradar.apply.engine import Run
from jobradar.apply.learn import ANCHOR_JS, Recorder, site_key

root = Path(tempfile.mkdtemp())
(root / "private").mkdir()
(root / "private" / "apply-profile.yaml").write_text("personal: {first_name: Asha}\n", encoding="utf-8")
os.environ["JOBRADAR_APPLY_PASSWORD"] = "s3cret-pass"
profile = Profile(root)
rec = Recorder(profile)

FORM = """<html><body><h2>Screening</h2><form>
<label for="yrs">Years of experience with Kafka?</label><input id="yrs">
<label for="city">Preferred work city</label><select id="city"><option></option><option>Pune</option></select>
<fieldset><legend>Can you join in 30 days?</legend>
  <label><input type="radio" name="j" value="y">Yes</label><label><input type="radio" name="j" value="n">No</label></fieldset>
<label for="note">Untouched</label><input id="note">
<button type="button">Delete entry</button>
<button type="button">Proceed to screening</button>
</form></body></html>"""

# a sign-up page like Workday's: the engine used to get stuck here
ACCOUNT = """<html><body><h2>Create Account</h2>
<label for="em">Email Address</label><input id="em" value="a@x.in">
<label for="p1">Password</label><input id="p1" type="password">
<label for="p2">Verify New Password</label><input id="p2" type="password">
<label><input type="checkbox" id="ok">Yes, I have read and consent to the terms</label>
<div role="button" aria-label="Create Account" onclick="document.body.dataset.done =
  document.getElementById('p1').value + '|' + document.getElementById('ok').checked">Create Account</div>
</body></html>"""


class StubEngine:
    dry_run = False

    def __init__(self, page, recorder):
        self._page, self._recorder, self.lines = page, recorder, []

    def log(self, msg): self.lines.append(msg)
    def snap(self, force=False): pass
    def check(self): pass
    def anchor(self): return self._page.evaluate(ANCHOR_JS)
    def recipe_for(self, url, anchor): return self._recorder.recipe_for(url, anchor)
    def wait_for_you(self, *a, **k): raise AssertionError("asked you although a recipe exists")


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context()
    rec.attach(ctx)
    page = ctx.new_page()
    page.route("https://jobs.example.com/apply", lambda r: r.fulfill(body=FORM, content_type="text/html"))
    page.route("https://jobs.example.com/account", lambda r: r.fulfill(body=ACCOUNT, content_type="text/html"))

    # --- answers and the way on ------------------------------------------------------
    page.goto("https://jobs.example.com/apply")
    page.fill("#yrs", "0")                         # before the pause: not learned
    page.locator("#yrs").dispatch_event("change")
    rec.start()                                    # the engine pauses for you
    page.fill("#yrs", "1")
    page.locator("#yrs").dispatch_event("change")
    page.select_option("#city", "Pune")
    page.check("input[value=y]")
    page.click("text=Delete entry")
    page.click("text=Proceed to screening")
    page.title()                                   # let the last reports arrive
    lines = rec.stop(learn_button=True)

    # --- a recipe: you get past the sign-up page once ---------------------------------
    page.goto("https://jobs.example.com/account")
    anchor = page.evaluate(ANCHOR_JS)
    rec.start()
    page.fill("#p1", "s3cret-pass"); page.locator("#p1").dispatch_event("change")
    page.fill("#p2", "s3cret-pass"); page.locator("#p2").dispatch_event("change")
    page.check("#ok")
    page.get_by_role("button", name="Create Account").click()
    page.title()
    assert any("password" in s for s in rec.live()), rec.live()
    rec_lines = rec.stop(learn_button=True, anchor=anchor, url=page.url)

    # --- the next job: same page, fresh - the engine does it without you ---------------
    page.goto("https://jobs.example.com/account")
    eng = StubEngine(page, Recorder(Profile(root)))
    run = Run(eng, {"id": 1, "company": "Acme", "title": "SDE", "url": page.url}, page,
              Profile(root), None, {"path": Path("r.pdf"), "text": ""})
    run.wait("manual", "stuck on the sign-up page")          # must replay, not ask
    done = page.evaluate("document.body.dataset.done")
    browser.close()

learned = Profile(root)
assert learned.recall("Years of experience with Kafka?") == "1", learned.learned
assert learned.recall("Preferred work city") == "Pune"
assert learned.recall("Can you join in 30 days?") == "Yes"
assert learned.recall("Untouched") is None
assert rec.buttons_for("https://jobs.example.com/x")[0] == "Proceed to screening", rec.data
assert "Delete entry" not in rec.buttons_for("https://jobs.example.com/x"), "never a destructive button"
assert any("Proceed to screening" in l for l in lines), lines
assert "answers_the_candidate_gave_before" in learned.facts(), "the model sees what you taught it"

text = (root / "private" / "apply-learned.yaml").read_text(encoding="utf-8")
assert "s3cret-pass" not in text, "the password itself is never stored"
assert "Create Account" in text and any("recipe" in l for l in rec_lines), rec_lines
assert done == "s3cret-pass|true", f"replay typed the password, ticked, clicked: {done!r}"
assert any("doing what you showed me" in l for l in eng.lines), eng.lines
assert learned.recall("Password") is None
assert site_key("ebay.wd5.myworkdayjobs.com") == "myworkdayjobs.com"
print("learn tests PASS")
