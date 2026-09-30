"""One engine per job board, all built on Board.

Board.apply() is the whole application: open the posting, get to the form,
then page by page - sign in or sign up when a password box appears, fill,
press Next/Submit - until the board says the application is in. A board
engine only overrides what its board does differently. Anything the engine
cannot do (a CAPTCHA, an email to verify, a field it cannot work) pauses the
run for you instead of guessing.
"""
from __future__ import annotations

import re
import sqlite3
import time
from urllib.parse import parse_qs, urlparse

from . import forms


class DryRunDone(BaseException):
    """Dry run: everything is filled and the next click would submit."""

SUBMIT = [r"^submit( application| my application|your application)?\s*[›»→]?$", r"^submit\b",
          r"^send( application)?$", r"^apply( now)?$", r"^finish$", r"^complete application$"]
NEXT = [r"^save and continue$", r"^save & continue$", r"^next( step| page)?\s*[›»→]?$",
        r"^continue\s*[›»→]?$", r"^review( application)?$", r"^proceed$"]

VERIFY = re.compile(
    r"verify your (e-?mail|account|email address)|verification (e-?mail|link)|check your (e-?mail|inbox)|"
    r"confirm your e-?mail|activate your account|we('ve| have)? sent (you )?(an? )?(e-?mail|link|message)|"
    r"account (is )?not (yet )?(verified|activated)", re.I)
CODE = re.compile(r"(security|verification|one[- ]time|confirmation|access) code|enter the code|\botp\b|"
                  r"code (was |has been )?sent|pin (sent|code)", re.I)
BAD_LOGIN = re.compile(r"invalid|incorrect|wrong|not (be )?found|no account|doesn.?t (exist|match)|"
                       r"does not (exist|match)|not recogni[sz]ed|unable to sign in|couldn.?t find", re.I)
TAKEN = re.compile(r"already (exists|in use|registered|taken|have an account)|account with this e-?mail", re.I)


class Board:
    name = "generic"
    submit_patterns = SUBMIT
    next_patterns = NEXT

    def __init__(self, run):
        self.run = run
        self.page = run.page

    # -- the flow ---------------------------------------------------------------
    def apply(self) -> None:
        run = self.run
        run.stage("opening the job")
        run.open_url(self.apply_url())
        if forms.submitted(self.page):
            return
        run.stage("getting to the form")
        self.open_form()
        self.work_pages()

    def apply_url(self) -> str:
        return self.run.job["url"]

    def open_form(self) -> None:
        """Press the posting's Apply button when the form is not on the page."""
        if self._has_form():
            return
        with_tab = self._click_opening_tab([r"^apply( now| for this (job|position|role))?\s*[›»→]?$",
                                            r"^apply online$", r"^i.?m interested$", r"^easy apply$"])
        if not with_tab:
            self.run.log("no Apply button - looking for the form on the page")
        self._settle()

    def work_pages(self, max_pages: int = 14) -> None:
        run, same = self.run, 0
        for step in range(max_pages):
            run.check()
            page = self.page
            self._settle(1.5)            # let a new page draw before reading it
            if forms.submitted(page):
                return
            if self._password_boxes():
                run.stage("signing in")
                self.sign_in()
                continue
            if self._code_prompt():
                self.enter_code()
                continue
            self.before_fill(page)
            run.stage(f"filling page {step + 1}" if step else "filling the form")
            missing = forms.fill(run, page)
            self.after_fill(page)
            missing = [f for f in missing if not self._now_filled(f)]
            if missing:
                names = "; ".join(f["label"][:60] or f["kind"] for f in missing[:6])
                run.wait("manual", f"I could not fill: {names}. Fill them in the Chrome window, "
                                   f"then press Continue.")
            if self._captcha():
                run.wait("captcha", "This form has a CAPTCHA. Solve it in the Chrome window, then "
                                    "press Continue.")
            before = self._signature()
            final = self.press_next()
            if final is None:
                run.wait("manual", "I cannot find the button to go on. Click it in the Chrome "
                                   "window (or finish the application there), then press Continue. "
                                   "I will remember which button it was.", learn_button=True)
                if forms.submitted(self.page):
                    return
                continue
            run.stage("submitting" if final else "next page")
            outcome = self.wait_outcome(before)
            if outcome == "done":
                return
            if outcome == "captcha":
                run.wait("captcha", "A CAPTCHA is showing. Solve it in the Chrome window, then press "
                                    "Continue.")
                if self._confirmed_after(8):
                    return
            elif outcome == "code":
                self.enter_code()
                if self._confirmed_after(10):
                    return
            elif outcome == "verify":
                self.verify_email()
            elif outcome == "stuck":
                errs = forms.errors(self.page)
                run.log("the form did not move on" + (f": {'; '.join(errs[:4])}" if errs else ""))
                same += 1
                if same >= 2:
                    run.wait("manual", "The form will not go on" +
                             (f" ({'; '.join(errs[:3])[:200]})" if errs else "") +
                             ". Fix it in the Chrome window and submit, then press Continue. "
                             "I will learn what you fill in.", learn_button=True)
                    if self._confirmed_after(5) or self.run.assisted:
                        return
                    same = 0
                continue
            same = 0
        if not forms.submitted(self.page):
            run.wait("manual", "This application has more pages than expected. Finish it in the "
                               "Chrome window, then press Continue.")

    # -- hooks for board engines ------------------------------------------------
    def before_fill(self, page) -> None:
        pass

    def after_fill(self, page) -> None:
        pass

    def press_next(self) -> bool | None:
        """True: pressed Submit. False: pressed Next. None: found neither."""
        submit = forms.find_first(self.page, self.submit_patterns)
        if submit is not None:
            self._submit_gate()
            forms.safe_click(submit)
            return True
        # buttons you pressed on this site before, when I could not find the way on
        for text in self.run.engine.learned_buttons(self.page.url):
            item = forms.find_first(self.page, [rf"^{re.escape(text)}$"])
            if item is None:
                continue
            final = bool(re.search(r"(?i)submit|send|finish|complete|apply", text))
            if final:
                self._submit_gate()
            self.run.log(f"pressing “{text}” (learned from you)")
            forms.safe_click(item)
            return final
        if forms.click_first(self.page, self.next_patterns):
            return False
        return None

    def _submit_gate(self) -> None:
        if self.run.engine.dry_run:
            self.run.engine.snap(force=True)
            raise DryRunDone()

    # -- sign in / sign up ----------------------------------------------------------
    def sign_in(self) -> None:
        """A password box is showing. Your rule: first SIGN IN with your job-apply
        email and password; only when that fails (no account yet) CREATE the
        account with the same email and password - then stop for you if the
        site sends a verification email."""
        run, page, prof = self.run, self.page, self.run.profile
        if run.engine.dry_run:
            # A dry run must not create accounts or send verification emails.
            run.log("dry run: reached the sign-in page - stopping before any login or signup")
            raise DryRunDone()
        login_failed = False
        for attempt in range(5):
            run.check()
            boxes = self._password_boxes()
            if not boxes:
                return
            if len(boxes) >= 2 and not login_failed:
                # A sign-up form (Workday opens on one): switch to sign in first.
                if self._go_to_sign_in():
                    continue
                run.log("no sign-in option on this page - creating the account")
                login_failed = True
            if len(boxes) == 1:
                self._fill_email()
                boxes[0].fill(prof.login_password)
                run.log("signing in with your job-apply email")
                if not self._click_sign_in_submit():
                    boxes[0].press("Enter")
                self._settle(5)
                text = forms.page_text(page)
                if VERIFY.search(text) or self._code_prompt():
                    self.verify_email()
                    continue
                if not self._password_boxes():
                    run.log("signed in")
                    return
                if login_failed:
                    break                     # the account exists but will not take the password
                login_failed = True
                run.log("sign-in did not work - creating an account with the same email and password")
                if not forms.click_first(page, [r"^create (an )?account", r"^sign ?up", r"^register",
                                                r"new user", r"don.?t have an account"],
                                         roles=("button", "link")):
                    break
                self._settle(3)
                continue
            # the sign-up form, after sign-in failed
            self._fill_email()
            for b in boxes:
                b.fill(prof.login_password)
            self._tick_required_boxes()
            run.log("creating an account with your job-apply email")
            if not forms.click_first(page, [r"^create account$", r"^sign ?up$", r"^register$",
                                            r"^create$", r"^submit$", r"^continue$", r"^next$"]):
                run.wait("manual", "Create the account in the Chrome window, then press Continue.")
            self._settle(5)
            text = forms.page_text(page)
            if TAKEN.search(text):
                break                         # exists, yet sign-in failed: the password differs
            if VERIFY.search(text) or self._code_prompt():
                self.verify_email()           # then the next loop signs in
                continue
            if not self._password_boxes():
                run.log("account created")
                return
        run.wait("manual", f"I could not sign in or create the account for {prof.login_email} "
                           "(the site may already have an account with a different password - "
                           "use 'Forgot password' there). Sign in in the Chrome window, then "
                           "press Continue.")

    def _go_to_sign_in(self) -> bool:
        loc = self.page.locator('[data-automation-id="signInLink"]:visible')
        if loc.count():
            loc.first.click()
        elif not forms.click_first(self.page, [r"^sign ?in$", r"^log ?in$", r"already have an account",
                                               r"^existing (user|candidate)"], roles=("link", "button")):
            return False
        self.run.log("switched to sign in")
        self._settle(3)
        return True

    def _click_sign_in_submit(self) -> bool:
        loc = self.page.locator('[data-automation-id="signInSubmitButton"]:visible, '
                                '[data-automation-id="click_filter"][aria-label="Sign In"]:visible')
        if loc.count():
            try:
                loc.last.click()
                return True
            except Exception:  # noqa: BLE001
                pass
        return forms.click_first(self.page, [r"^sign ?in$", r"^log ?in$", r"^login$", r"^continue$",
                                             r"^next$", r"^submit$"])

    def verify_email(self) -> None:
        email = self.run.profile.login_email
        if self._code_prompt():
            self.enter_code()
            return
        self.run.wait("verify_email",
                      f"A verification email was sent to {email}. Open it, click the link to verify, "
                      f"then press “I've verified my email”.")
        # Back to where we were: the board wants a fresh sign-in after verifying.
        try:
            self.page.reload(wait_until="domcontentloaded", timeout=45000)
        except Exception:  # noqa: BLE001
            pass
        self._settle()

    def enter_code(self) -> None:
        code = self.run.wait(
            "code", f"The site sent a code to {self.run.profile.login_email}. Type it here (or into "
                    f"the Chrome window) and press Continue.", needs_input=True)
        code = (code or "").strip()
        if code:
            singles = self.page.locator('input[maxlength="1"]:visible')
            if singles.count() >= len(code) > 1:
                for i, ch in enumerate(code):
                    singles.nth(i).fill(ch)
            else:
                box = self.page.locator(
                    'input[autocomplete="one-time-code"]:visible, input[name*="code" i]:visible, '
                    'input[id*="code" i]:visible, input[type="text"]:visible, input[type="number"]:visible').first
                box.fill(code)
            self.run.log("entered the code")
            if not forms.click_first(self.page, [r"^verify", r"^submit", r"^confirm", r"^continue",
                                                  r"^next"]):
                self.page.keyboard.press("Enter")
        self._settle(4)

    # -- helpers ----------------------------------------------------------------------
    def _settle(self, seconds: float = 2.5) -> None:
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        end = time.time() + seconds
        while time.time() < end:
            self.run.engine.snap()
            time.sleep(0.3)

    def _has_form(self) -> bool:
        return any(f["kind"] in ("text", "email", "file", "textarea") for f in forms.scan(self.page))

    def _click_opening_tab(self, patterns: list[str]) -> bool:
        """Click Apply; when the board opens the form in a new tab, follow it."""
        ctx = self.page.context
        before = len(ctx.pages)
        if not forms.click_first(self.page, patterns, timeout=4):
            return False
        time.sleep(2)
        if len(ctx.pages) > before:
            self.page = self.run.page = ctx.pages[-1]
            self.run.engine._page = self.page
            self.page.wait_for_load_state("domcontentloaded")
        return True

    def _password_boxes(self):
        loc = self.page.locator('input[type="password"]:visible')
        return [loc.nth(i) for i in range(loc.count())]

    def _fill_email(self) -> None:
        box = self.page.locator(
            'input[type="email"]:visible, input[autocomplete="username"]:visible, '
            'input[name*="email" i]:visible, input[id*="email" i]:visible, '
            'input[data-automation-id="email"]:visible, input[name*="user" i]:visible').first
        try:
            box.fill(self.run.profile.login_email, timeout=5000)
        except Exception:  # noqa: BLE001 - some sign-ins ask only the password
            pass

    def _tick_required_boxes(self) -> None:
        boxes = self.page.locator('input[type="checkbox"]:visible')
        for i in range(boxes.count()):
            try:
                if not boxes.nth(i).is_checked():
                    boxes.nth(i).check(timeout=2000)
            except Exception:  # noqa: BLE001
                pass

    def _code_prompt(self) -> bool:
        if not CODE.search(forms.page_text(self.page)[:6000]):
            return False
        return self.page.locator(
            'input[autocomplete="one-time-code"]:visible, input[name*="code" i]:visible, '
            'input[id*="code" i]:visible, input[maxlength="1"]:visible').count() > 0

    def _captcha(self) -> bool:
        # a picture-of-text CAPTCHA on the page itself, or the form saying one failed
        try:
            if self.page.locator('img[src*="captcha" i]:visible, input[name*="captcha" i]:visible, '
                                 'input[id*="captcha" i]:visible, [class*="captcha" i] img:visible').count():
                return True
        except Exception:  # noqa: BLE001
            pass
        if any(re.search(r"(?i)captcha", e) for e in forms.errors(self.page)):
            return True
        for fr in self.page.frames:
            u = fr.url
            if not (("recaptcha" in u and "bframe" in u) or ("hcaptcha" in u and "challenge" in u)
                    or "challenges.cloudflare.com" in u):
                continue
            try:
                box = fr.frame_element().bounding_box()
                if box and box["height"] > 100 and box["width"] > 100 and box["y"] > -500:
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _signature(self) -> str:
        try:
            labels = [f["label"] for f in forms.scan(self.page)]
        except Exception:  # noqa: BLE001
            labels = []
        return self.page.url + "|" + "|".join(labels)

    def _now_filled(self, f: dict) -> bool:
        for g in forms.scan(self.page):
            if g["label"] == f["label"] and g["kind"] == f["kind"]:
                return bool(g.get("value"))
        return True                      # gone from the page: nothing to fill

    def wait_outcome(self, before: str, timeout: float = 30) -> str:
        end, start = time.time() + timeout, time.time()
        while time.time() < end:
            self.run.engine.snap()
            time.sleep(0.7)
            page = self.page
            if forms.submitted(page):
                return "done"
            if self._captcha():
                return "captcha"
            if self._code_prompt():
                return "code"
            text = forms.page_text(page)[:5000]
            if VERIFY.search(text) and not self._has_form():
                return "verify"
            if time.time() - start > 3:
                if self._signature() != before:
                    return "moved"
                if forms.errors(page):
                    return "stuck"
        return "stuck"

    def _confirmed_after(self, seconds: float) -> bool:
        end = time.time() + seconds
        while time.time() < end:
            if forms.submitted(self.page):
                return True
            time.sleep(0.7)
        return False


class Greenhouse(Board):
    name = "greenhouse"

    def apply_url(self) -> str:
        url = self.run.job["url"]
        if "greenhouse.io" in url:
            return url
        # A company careers page embedding Greenhouse: go to the board itself.
        jid = (parse_qs(urlparse(url).query).get("gh_jid") or [""])[0]
        slug = self._slug()
        if jid and slug:
            return f"https://job-boards.greenhouse.io/{slug}/jobs/{jid}"
        return url

    def _slug(self) -> str:
        try:
            db = sqlite3.connect(str(self.run.engine.root / "jobradar.db"))
            row = db.execute("SELECT slug FROM companies WHERE lower(name) = ? AND board = 'greenhouse'",
                             (self.run.job["company"].lower(),)).fetchone()
            db.close()
            return row[0] if row else ""
        except sqlite3.Error:
            return ""


class Lever(Board):
    name = "lever"

    def apply_url(self) -> str:
        url = self.run.job["url"].rstrip("/")
        return url if url.endswith("/apply") else url + "/apply"


class Ashby(Board):
    name = "ashby"

    def apply_url(self) -> str:
        url = self.run.job["url"].rstrip("/")
        return url if url.endswith("/application") else url + "/application"


class Workday(Board):
    """Workday: Apply -> Autofill with Resume -> sign in / create account (one
    per company, with email verification) -> a wizard of pages -> Submit."""
    name = "workday"
    NEXT_BTN = '[data-automation-id="bottom-navigation-next-button"], [data-automation-id="pageFooterNextButton"]'

    def open_form(self) -> None:
        page = self.page
        btn = page.locator('[data-automation-id="adventureButton"]:visible').first
        try:
            btn.click(timeout=15000)
        except Exception:  # noqa: BLE001
            if not forms.click_first(page, [r"^apply$", r"^apply now$"], timeout=5):
                self.run.wait("manual", "I cannot find Workday's Apply button. Click it in the Chrome "
                                        "window, then press Continue.")
        self._settle()
        # Autofill with Resume fills My Experience from the resume for us.
        for sel in ('[data-automation-id="autofillWithResume"]', '[data-automation-id="applyManually"]'):
            loc = page.locator(f"{sel}:visible").first
            try:
                loc.click(timeout=4000)
                self.run.log("chose " + ("Autofill with Resume" if "autofill" in sel else "Apply Manually"))
                break
            except Exception:  # noqa: BLE001
                continue
        self._settle()
        # Workday opens on "Create Account" or "Sign In"; both have password boxes.

    def _settle(self, seconds: float = 2.5) -> None:
        """Workday draws each page after a spinner; wait for real controls."""
        super()._settle(seconds)
        end = time.time() + 45
        while time.time() < end:
            if self.page.locator('input:visible, textarea:visible, '
                                 '[data-automation-id="bottom-navigation-next-button"]:visible, '
                                 'button[aria-haspopup="listbox"]:visible').count():
                return
            if forms.submitted(self.page):
                return
            self.run.engine.snap()
            time.sleep(0.5)
        self.run.wait("manual", "Workday is still loading. When the page shows in the Chrome "
                                "window, press Continue.")

    def before_fill(self, page) -> None:
        # My Experience: the resume goes in Workday's own uploader.
        up = page.locator('input[data-automation-id="file-upload-input-ref"]')
        done = page.locator('[data-automation-id="file-upload-successful"], '
                            '[data-automation-id="fileUploadSuccessful"]')
        if up.count() and not done.count():
            up.first.set_input_files(str(self.run.resume["path"]))
            self.run.log(f"uploaded {self.run.resume['path'].name}")
            self._settle(4)
        self._source_prompt(page)

    def _source_prompt(self, page) -> None:
        """'How did you hear about us?' is a searchable multiselect in Workday."""
        box = page.locator('[data-automation-id="formField-source"] [data-automation-id="searchBox"]:visible, '
                           '[data-automation-id="formField-sourcePrompt"] [data-automation-id="searchBox"]:visible')
        if not box.count():
            return
        chosen = page.locator('[data-automation-id="formField-source"] [data-automation-id="selectedItem"], '
                              '[data-automation-id="formField-sourcePrompt"] [data-automation-id="selectedItem"]')
        if chosen.count():
            return
        for term in ("LinkedIn", "Career", "Website", "Job Board", "Other"):
            try:
                box.first.fill(term)
                box.first.press("Enter")
                time.sleep(1.5)
                opt = page.locator('[data-automation-id="promptOption"]:visible, '
                                   '[data-automation-id="menuItem"]:visible')
                if opt.count():
                    opt.first.click()
                    time.sleep(0.8)
                    self.run.log(f"How did you hear: {term}")
                    page.keyboard.press("Escape")
                    return
            except Exception:  # noqa: BLE001
                continue

    def press_next(self) -> bool | None:
        btn = self.page.locator(self.NEXT_BTN).filter(visible=True)
        if btn.count():
            label = (btn.first.inner_text() or "").strip().lower()
            if label.startswith("submit"):
                self._submit_gate()
            forms.safe_click(btn.first)
            return label.startswith("submit")
        return super().press_next()


class Generic(Board):
    """Keka, Zoho Recruit, Oracle, Darwinbox and any other board: the shared flow,
    which signs in, fills what it can and hands you anything it cannot."""
    name = "generic"


ENGINES = {"greenhouse": Greenhouse, "lever": Lever, "ashby": Ashby, "workday": Workday}


def board_for(source: str | None, url: str):
    """The engine for a job, by the board it was found on, else by its link."""
    if source in ENGINES:
        return ENGINES[source]
    host = urlparse(url).netloc.lower()
    for key, cls in (("greenhouse", Greenhouse), ("lever.co", Lever), ("ashbyhq", Ashby),
                     ("myworkdayjobs", Workday), ("workday", Workday)):
        if key in host:
            return cls
    return Generic
