"""The main apply engine.

One worker thread owns one real (visible) Chrome window and works through the
apply queue a job at a time:

    queued -> applying -> (needs_you -> applying)* -> applied | failed

For each job it picks the resume, hands the page to the engine for that job's
board (sites/), and records the result in the application history as applied
by the AI engine. Whenever a step needs you - an email to verify, a CAPTCHA,
a question it has no answer for - it pauses with a button on the Apply status
page and carries on when you press it.

The web server only talks to it through start/stop/resume/skip and snapshot();
Playwright objects never leave the worker thread.
"""
from __future__ import annotations

import collections
import os
import re
import threading
import time
import traceback
from pathlib import Path

from ..store import Store
from . import resumes, sites
from .answers import Profile, choose_option, norm
from .learn import ANCHOR_JS, Recorder, anchor_key
from .writer import Writer


# Skip and Stop are BaseExceptions on purpose: every "one odd field must not
# end the application" handler catches Exception, and must not swallow these.
class Skipped(BaseException):
    """You pressed Skip: leave this job and take the next."""


class Stopped(BaseException):
    """You pressed Stop while the engine waited on you: back to the queue."""


class NeedsYou(Exception):
    """A board engine hit something only you can do; the run pauses on it."""


class Run:
    """One job's application: what a board engine works with."""

    def __init__(self, engine: "Engine", app: dict, page, profile: Profile,
                 writer: Writer, resume: dict):
        self.engine, self.app, self.page = engine, app, page
        self.profile, self.writer, self.resume = profile, writer, resume
        self.job = {"company": app["company"], "title": app["title"], "url": app["url"],
                    "description": app.get("description") or ""}
        self.assisted = False       # you finished a step by hand
        self.written: dict[str, str] = {}
        self.replayed: set[str] = set()   # pages whose recipe this job already tried

    def log(self, msg: str) -> None:
        self.engine.log(msg)

    def stage(self, name: str) -> None:
        self.engine.set_stage(name)

    def check(self) -> None:
        self.engine.check()

    # -- answers -------------------------------------------------------------
    def write(self, question: str) -> str | None:
        key = norm(question)
        if key in self.written:
            return self.written[key]
        try:
            self.log(f"writing an answer from your resume: {question[:70]}")
            text = self.writer.prose(question, self.job, self.resume["text"])
        except Exception as e:  # noqa: BLE001 - no model: ask instead
            self.log(f"model unavailable ({type(e).__name__}) - asking you")
            return None
        self.written[key] = text
        return text or None

    def infer(self, question: str, options: list[str]) -> str | None:
        try:
            ans = self.writer.infer(question, options, self.job, self.resume["text"])
        except Exception:  # noqa: BLE001
            return None
        if ans is not None:
            self.log(f"from your resume: {question[:60]} -> {ans[:40]}")
        return ans

    def ask(self, question: str, options: list[str] | None = None) -> str | None:
        """Pause until you answer on the Apply status page. The answer is kept
        and reused for the same question on every later application."""
        options = [o for o in (options or []) if o and o.strip()]
        ans = self.engine.wait_for_you(
            "question", f"The form asks: {question}", options=options, needs_input=True)
        if ans is None or not str(ans).strip():
            return None
        ans = str(ans).strip()
        if options:
            ans = choose_option(ans, options) or ans
        self.profile.learn(question, ans)
        self.log(f"you answered: {question[:60]} -> {ans[:40]}")
        return ans

    def wait(self, kind: str, message: str, needs_input: bool = False,
             learn_button: bool = False) -> str | None:
        """Pause for something you do - verify an email, solve a CAPTCHA,
        finish a page in the browser window - then continue. A step you finish
        by hand is watched and learned (see learn.py); learn_button also keeps
        the button you pressed to move on."""
        learn = ""
        if kind == "manual":
            # You showed me this page before: do it your way, don't ask again.
            # Once per page per job - if the replay did not get us past it,
            # the next time we land here you are asked (and taught anew).
            anchor = self.engine.anchor()
            key = anchor_key(anchor) if anchor else ""
            steps = self.engine.recipe_for(self.page.url, anchor) if key not in self.replayed else []
            if steps:
                self.replayed.add(key)
                self.replay(steps)
                return None
            self.assisted = True
            learn = "button" if learn_button else "fields"
        return self.engine.wait_for_you(kind, message, needs_input=needs_input, learn=learn)

    def replay(self, steps: list[dict]) -> None:
        """Repeat what you did when stuck on this page before."""
        from . import forms
        self.log(f"stuck here before - doing what you showed me ({len(steps)} steps)")
        filled = False
        for st in steps:
            self.check()
            if "click" in st:
                item = forms.find_first(self.page, [rf"^{re.escape(st['click'])}$"],
                                        roles=("button", "link"), timeout=4)
                if item is None:
                    self.log(f"  no “{st['click']}” on this page - skipped")
                    continue
                forms.safe_click(item)
                self.log(f"  clicked “{st['click']}”")
                filled = False
                time.sleep(1.5)
                self.engine.snap(force=True)
            elif "password" in st:
                boxes = self.page.locator('input[type="password"]:visible')
                for i in range(boxes.count()):
                    if not boxes.nth(i).input_value():
                        boxes.nth(i).fill(self.profile.login_password)
                self.log("  typed the password from .env")
            elif "fill" in st and not filled:
                # your answers are learned, so a fill pass types them all
                forms.fill(self, self.page)
                filled = True
        time.sleep(1.5)
        self.engine.snap(force=True)

    def open_url(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:  # noqa: BLE001 - some boards never go idle
            pass
        self.engine.snap(force=True)


class Engine:
    GAP_SECONDS = 15          # pause between applications

    def __init__(self, root: Path, llm_factory=None, dry_run: bool = False,
                 db: Path | None = None, headless: bool = False):
        self.root = Path(root)
        self.db = Path(db) if db else self.root / "jobradar.db"
        self.headless = headless
        # Dry run: fill every page, stop before the final Submit, record nothing.
        self.dry_run = dry_run
        self.llm_factory = llm_factory
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._stop = False
        self._skip = False
        self._answer: str | None = None
        self.state = {"running": False, "phase": "stopped", "current": None, "waiting": None,
                      "error": "", "started_at": None, "done_this_run": 0}
        self._log: collections.deque = collections.deque(maxlen=300)
        self._screen: bytes = b""
        self._screen_at = 0.0
        self._page = None
        self._recorder: Recorder | None = None

    def learned_buttons(self, url: str) -> list[str]:
        return self._recorder.buttons_for(url) if self._recorder else []

    def anchor(self) -> dict:
        """Which page the browser is on, as learn.py tells pages apart."""
        try:
            return self._page.evaluate(ANCHOR_JS) if self._page else {}
        except Exception:  # noqa: BLE001 - mid-navigation
            return {}

    def recipe_for(self, url: str, anchor: dict) -> list[dict]:
        return self._recorder.recipe_for(url, anchor) if self._recorder and anchor else []

    # -- control, from the web server -----------------------------------------
    def start(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._stop = False
                return False
            self._stop = self._skip = False
            self.state.update(running=True, phase="starting", error="", started_at=time.time(),
                              done_this_run=0)
            self._thread = threading.Thread(target=self._main, daemon=True, name="apply-engine")
            self._thread.start()
            return True

    def stop(self) -> None:
        with self._lock:
            self._stop = True
            if self.state["running"]:
                self.state["phase"] = "stopping"
        self._wake.set()

    def resume(self, answer: str | None = None) -> None:
        with self._lock:
            self._answer = answer
        self._wake.set()

    def skip(self) -> None:
        with self._lock:
            self._skip = True
        self._wake.set()

    def snapshot(self) -> dict:
        with self._lock:
            snap = {**self.state, "log": list(self._log)[-120:], "screen_at": self._screen_at}
        rec = self._recorder
        if snap["waiting"] and rec is not None and rec.on:
            # what you have done so far in Chrome, shown live on the waiting card
            snap["waiting"] = {**snap["waiting"], "learning": rec.live()}
        return snap

    def screen(self) -> bytes:
        return self._screen

    # -- used by the worker and board engines ------------------------------------
    def log(self, msg: str) -> None:
        with self._lock:
            self._log.append({"at": time.time(), "msg": msg})
        self.snap()

    def set_stage(self, name: str) -> None:
        with self._lock:
            if self.state["current"]:
                self.state["current"]["stage"] = name
        self.log(f"— {name}")

    def check(self) -> None:
        """Stop lets the job in hand finish (or stops at its next pause);
        Skip drops it at once."""
        if self._skip:
            raise Skipped()

    def snap(self, force: bool = False) -> None:
        """Keep the latest picture of the browser for the Apply status page."""
        if self._page is None or threading.current_thread() is not self._thread:
            return
        now = time.time()
        if not force and now - self._screen_at < 0.8:
            return
        try:
            self._screen = self._page.screenshot(type="jpeg", quality=55, timeout=4000)
            self._screen_at = now
        except Exception:  # noqa: BLE001 - mid-navigation; the next one lands
            pass

    def wait_for_you(self, kind: str, message: str, options: list[str] | None = None,
                     needs_input: bool = False, learn: str = "") -> str | None:
        store = self._store
        app_id = self.state["current"]["app_id"] if self.state["current"] else None
        if self._skip:
            raise Skipped()
        if self._stop:
            raise Stopped()
        with self._lock:
            self._answer = None
            self._wake.clear()
            self.state["waiting"] = {"kind": kind, "message": message, "options": options or [],
                                     "input": needs_input, "since": time.time()}
            self.state["phase"] = "waiting"
        if app_id:
            store.update_application(app_id, "needs_you")
        self.log(f"waiting for you: {message[:120]}")
        rec = self._recorder if learn else None
        if rec:
            anchor, url = self.anchor(), self._page.url if self._page else ""
            rec.start()
        while not self._wake.wait(1.0):
            self.snap(force=True)          # also delivers the recorder's reports
        if rec:
            self.snap(force=True)
            if self._skip:
                rec.on = False             # a skipped job teaches nothing
            else:
                for line in rec.stop(learn_button=(learn == "button"), anchor=anchor, url=url):
                    self.log(line)
        with self._lock:
            self.state["waiting"] = None
            self.state["phase"] = "working"
            answer = self._answer
        if app_id:
            store.update_application(app_id, "applying")
        if self._skip:
            raise Skipped()
        if self._stop:
            raise Stopped()
        self.log("continuing")
        return answer

    # -- the worker -------------------------------------------------------------
    def _main(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            with self._lock:
                self.state.update(running=False, phase="stopped",
                                  error="Playwright is not installed: pip install playwright "
                                        "&& python -m playwright install chromium")
            return
        self._store = Store(self.db)
        n = self._store.requeue_stuck()
        if n:
            self.log(f"{n} job(s) left mid-apply went back to the queue")
        try:
            profile = Profile(self.root)
            profile.login_password          # fail now, not halfway into a signup
            llm = self.llm_factory() if self.llm_factory else None
            writer = Writer(llm, profile)
            with sync_playwright() as pw:
                ctx = pw.chromium.launch_persistent_context(
                    str(self.root / "private" / "browser-profile"), headless=self.headless,
                    viewport={"width": 1280, "height": 860}, accept_downloads=False,
                    args=["--disable-blink-features=AutomationControlled",
                          # A window behind VS Code is "occluded" and Chrome
                          # slows its timers: dropdowns never open, uploads
                          # never finish. Keep it running at full speed.
                          "--disable-backgrounding-occluded-windows",
                          "--disable-renderer-backgrounding",
                          "--disable-background-timer-throttling"])
                self._recorder = Recorder(profile)
                self._recorder.attach(ctx)     # learns from steps you finish by hand
                self._page = ctx.pages[0] if ctx.pages else ctx.new_page()
                self.log("browser open")
                try:
                    self._work(ctx, profile, writer)
                finally:
                    self._page = None
                    ctx.close()
        except Exception as e:  # noqa: BLE001 - report it on the page, never die silently
            with self._lock:
                self.state["error"] = f"{type(e).__name__}: {e}"[:400]
            self.log(f"engine stopped: {type(e).__name__}: {e}")
        finally:
            with self._lock:
                self.state.update(running=False, phase="stopped", current=None, waiting=None)
            self._store.requeue_stuck()

    def _work(self, ctx, profile: Profile, writer: Writer) -> None:
        idle_logged = False
        while not self._stop:
            limit = int(profile.engine.get("max_applications_per_day") or 0)
            today = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
            if limit and self._store.ai_applied_since(today) >= limit:
                with self._lock:
                    self.state["phase"] = "daily limit reached"
                self._wake.wait(60)
                continue
            app = self._store.next_queued()
            if app is None:
                with self._lock:
                    self.state.update(phase="idle", current=None)
                if not idle_logged:
                    self.log("queue empty - add jobs with “Add to apply queue”")
                    idle_logged = True
                self._wake.wait(3)
                self._wake.clear()
                continue
            idle_logged = False
            self._apply_one(ctx, dict(app), profile, writer)
            with self._lock:
                self._skip = False
            if not self._stop:
                self._wake.wait(self.GAP_SECONDS)
                self._wake.clear()

    def _apply_one(self, ctx, app: dict, profile: Profile, writer: Writer) -> None:
        store = self._store
        app_id = app["id"]
        # A fresh tab per job: nothing from the last board leaks into this one.
        page = ctx.new_page()
        for old in ctx.pages:
            if old is not page:
                try:
                    old.close()
                except Exception:  # noqa: BLE001
                    pass
        self._page = page
        try:
            page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass
        board = sites.board_for(app.get("board"), app.get("url") or "")
        with self._lock:
            self.state.update(phase="working", current={
                "app_id": app_id, "company": app["company"], "title": app["title"],
                "url": app["url"], "board": board.name, "resume": "", "stage": "starting",
                "started_at": time.time()})
            self._log.clear()
        store.update_application(app_id, "applying")
        self.log(f"applying: {app['title']} at {app['company']} ({board.name})")
        resume = None
        try:
            if not app.get("url"):
                raise NeedsYou("this job has no link to apply on")
            resume = resumes.pick(profile.data.get("resumes") or [], self.root,
                                  app["title"], app.get("description") or "")
            with self._lock:
                self.state["current"]["resume"] = resume["path"].name
            self.log(f"resume: {resume['path'].name} ({resume['why']})")
            run = Run(self, app, page, profile, writer, resume)
            try:
                board(run).apply()
            except sites.DryRunDone:
                store.update_application(app_id, "queued")
                self.log("dry run: every page filled - stopped before Submit")
                self._proof(page, f"dry-{app_id}")
                with self._lock:
                    self._stop = True
                return
            proof = self._proof(page, app_id)
            how = "ai_assisted" if run.assisted else "ai_engine"
            store.finish_ai_application(
                app_id, applied_by=how, resume=resume["path"].name, proof=proof,
                notes=f"Applied by AI engine ({board.name}) with {resume['path'].name}"
                      + ("; you finished a step by hand" if run.assisted else ""))
            with self._lock:
                self.state["done_this_run"] += 1
            self.log("✓ applied")
        except Skipped:
            store.update_application(app_id, "not_applied", notes="skipped in the apply queue")
            self.log("skipped - removed from the queue")
        except Stopped:
            store.update_application(app_id, "queued")
            self.log("stopped - job kept in the queue")
        except Exception as e:  # noqa: BLE001 - one job failing must not stop the queue
            if "has been closed" in str(e) or type(e).__name__ == "TargetClosedError":
                # The Chrome window was closed: not this job's fault. Back to
                # the queue, and stop - there is no browser to go on with.
                store.update_application(app_id, "queued")
                self.log("the Chrome window was closed - job kept in the queue; press Start again")
                with self._lock:
                    self._stop = True
                    self.state["error"] = "The Chrome window was closed. Press Start to open it again."
                return
            why = str(e) if isinstance(e, NeedsYou) else f"{type(e).__name__}: {e}"
            self._proof(page, app_id)
            store.update_application(app_id, "failed", notes=f"AI engine: {why}"[:500])
            self.log(f"✗ failed: {why[:200]}")
            if os.environ.get("JOBRADAR_APPLY_DEBUG"):
                self.log(traceback.format_exc()[-1500:])

    def _proof(self, page, app_id) -> str:
        path = self.root / "private" / "apply-proof" / f"{app_id}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(path), full_page=True, timeout=10000)
        except Exception:  # noqa: BLE001
            return ""
        self.snap(force=True)
        return str(path.relative_to(self.root))
