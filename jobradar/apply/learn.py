"""Learning from you.

When the engine gets stuck it pauses and you finish the step in the Chrome
window. Everything you do there - only while it is paused for you - is
watched and kept:

- a field you fill (question -> your answer) goes to private/apply-answers.yaml,
  so the same question is filled for you on every later application, and the
  model sees it as context for similar questions;
- the button you press when the engine could not find how to go on is kept per
  site and tried first next time;
- the whole sequence (click this, fill that, type the password there) is kept
  as a RECIPE for that page on that site, in private/apply-learned.yaml. The
  next time the engine is stuck on the same page it replays your recipe
  instead of asking you.

Never kept: passwords (only *that* you typed one - replay types it from .env),
uploads, verification codes, CAPTCHA text.
"""
from __future__ import annotations

import re
import threading
from urllib.parse import urlparse

import yaml

from .answers import NEVER_KEEP, norm

# Injected into every page. It reports; Python decides what to keep.
RECORD_JS = r"""
(() => {
  if (window.__jrRecorder) return;
  window.__jrRecorder = true;
  const txt = (e) => (e ? (e.innerText || e.textContent || '') : '').replace(/\s+/g, ' ').trim();
  const byId = (id) => id ? document.getElementById(id) : null;
  function labelOf(el) {
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(/\s+/).map((i) => txt(byId(i))).filter(Boolean).join(' '); if (t) return t; }
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l && txt(l)) return txt(l); }
    const al = el.getAttribute('aria-label'); if (al && al.length > 1) return al;
    const own = el.closest('label');
    if (own && txt(own)) return txt(own);
    const g = el.closest('fieldset, [role="radiogroup"], [role="group"]');
    if (g) { const lg = g.querySelector('legend') || byId(g.getAttribute('aria-labelledby')); if (lg && txt(lg)) return txt(lg); }
    let p = el.parentElement;
    for (let i = 0; i < 5 && p; i++, p = p.parentElement) {
      const l = p.querySelector(':scope > label, :scope > legend, [class*="label" i]');
      if (l && !l.contains(el) && txt(l)) return txt(l);
    }
    return el.getAttribute('placeholder') || el.name || '';
  }
  const send = (name, ...args) => { try { window[name] && window[name](...args); } catch (e) {} };
  document.addEventListener('change', (ev) => {
    const el = ev.target;
    if (!el || !el.matches || !el.matches('input, select, textarea')) return;
    const t = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (t === 'password') { send('jrLearnField', labelOf(el), '', 'password'); return; }
    if (['file', 'hidden'].includes(t)) return;
    let value = el.value, label = labelOf(el);
    if (el.tagName === 'SELECT') value = el.selectedIndex >= 0 ? el.options[el.selectedIndex].text : '';
    if (t === 'radio' || t === 'checkbox') {
      if (!el.checked) return;
      const own = el.closest('label') || (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`));
      value = txt(own) || el.value;
      const g = el.closest('fieldset, [role="radiogroup"], [role="group"]');
      const gl = g && (g.querySelector('legend') || byId(g.getAttribute('aria-labelledby')));
      if (gl && txt(gl)) label = txt(gl);            // a group: the question is its legend
      else if (t === 'checkbox') { label = value; value = 'Yes'; }   // a lone box: "I agree..." = yes
    }
    send('jrLearnField', label, value, t);
  }, true);
  document.addEventListener('click', (ev) => {
    const opt = ev.target.closest && ev.target.closest('[role="option"], [data-automation-id="promptOption"]');
    if (opt) {
      // a choice in a custom dropdown: the question is the open dropdown's label
      const open = document.querySelector('[aria-expanded="true"][role="combobox"], [aria-expanded="true"][aria-haspopup="listbox"], [aria-expanded="true"]');
      send('jrLearnField', open ? labelOf(open) : '', txt(opt), 'option');
      return;
    }
    const b = ev.target.closest && ev.target.closest('button, a, [role="button"], input[type="submit"]');
    if (b) send('jrLearnClick', (txt(b) || b.getAttribute('aria-label') || b.value || '').slice(0, 80),
                location.hostname);
  }, true);
})();
"""

# Which page this is, so a recipe is replayed only where it was taught: the
# active step of a progress bar (Workday), else the first heading, plus the
# first few questions on the page.
ANCHOR_JS = r"""
() => {
  const txt = (e) => (e ? (e.innerText || e.textContent || '') : '').replace(/\s+/g, ' ').trim();
  const shown = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const step = document.querySelector('[data-automation-id="progressBarActiveStep"], [aria-current="step"]');
  let head = step ? txt(step) : '';
  if (!head) { const h = [...document.querySelectorAll('h1, h2, h3')].find(shown); head = h ? txt(h) : ''; }
  const labels = [...document.querySelectorAll('label')].filter(shown).map(txt).filter(Boolean).slice(0, 4);
  const pw = [...document.querySelectorAll('input[type="password"]')].filter(shown).length;
  return { head: head.slice(0, 80), labels, pw };
}
"""

# A pressed button worth remembering as "the way on": short, and not one that
# adds, removes or uploads something.
_NAV_OK = re.compile(r"^[\w &'’.,:/+-]{2,40}$")
_NAV_BAD = re.compile(r"(?i)delete|remove|cancel|upload|attach|add( another)?$|^add |edit|back|"
                      r"sign ?out|log ?out|privacy|terms|cookie|close|dropbox|google drive")
# Clicks a recipe must never replay.
_NEVER_REPLAY = re.compile(r"(?i)delete|remove|cancel|withdraw|sign ?out|log ?out|upload|attach|"
                           r"select files?|browse|dropbox|google drive|privacy|terms|cookie|"
                           r"back to|forgot")
_SENSITIVE = re.compile(r"(?i)passcode|otp|one[- ]time|verification|security code|captcha|"
                        r"aadhaar|\bpan\b.*number|card number|cvv|account number|ifsc")


def site_key(host: str) -> str:
    """ebay.wd5.myworkdayjobs.com -> myworkdayjobs.com: a lesson from one
    Workday company helps every Workday company."""
    host = (host or "").lower()
    for board in ("myworkdayjobs.com", "greenhouse.io", "lever.co", "ashbyhq.com", "keka.com",
                  "zohorecruit", "oraclecloud.com", "darwinbox"):
        if board in host:
            return board
    return host


def anchor_key(anchor: dict) -> str:
    """One page kind as a string: 'create account/sign in | email address, password ...'."""
    labels = ", ".join(norm(l) for l in (anchor.get("labels") or []))
    return f"{norm(anchor.get('head', ''))} | {labels} | pw{anchor.get('pw', 0)}"


class Recorder:
    """Collects what you do in the browser while the engine waits on you."""

    def __init__(self, profile):
        self.profile = profile
        self.path = profile.root / "private" / "apply-learned.yaml"
        self._lock = threading.Lock()
        self.on = False
        self.steps: list[dict] = []
        self.data = {}
        if self.path.exists():
            self.data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}

    # -- wiring -------------------------------------------------------------------
    def attach(self, ctx) -> None:
        ctx.expose_binding("jrLearnField", self._field)
        ctx.expose_binding("jrLearnClick", self._click)
        ctx.add_init_script(RECORD_JS)

    def _field(self, source, label, value, kind) -> None:
        if not self.on:
            return
        label, value = (label or "").strip(), (value or "").strip()
        if kind == "password":
            with self._lock:
                if not self.steps or self.steps[-1].get("password") is None:
                    self.steps.append({"password": label or "password"})
            return
        if not label or not value or len(value) > 1500 or _SENSITIVE.search(label):
            return
        try:
            if value == self.profile.login_password:
                return
        except RuntimeError:
            pass
        with self._lock:
            # typing then retyping one box is one answer, the last one
            if self.steps and self.steps[-1].get("fill") == label:
                self.steps[-1]["value"] = value
            else:
                self.steps.append({"fill": label, "value": value})

    def _click(self, source, text, host) -> None:
        if self.on and text:
            with self._lock:
                self.steps.append({"click": text.strip(), "site": site_key(host)})

    # -- a pause for you ------------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            self.steps, self.on = [], True

    def live(self) -> list[str]:
        """What has been seen so far, for the Apply status page."""
        with self._lock:
            steps = list(self.steps)
        out = []
        for s in steps[-12:]:
            if "click" in s:
                out.append(f"clicked “{s['click']}”")
            elif "password" in s:
                out.append(f"typed the password ({s['password'][:40]}) - it is not stored")
            else:
                out.append(f"{s['fill'][:50]}: {s['value'][:40]}")
        return out

    def stop(self, learn_button: bool, anchor: dict | None = None, url: str = "") -> list[str]:
        """Keep what you did. Returns plain-language lines for the step log."""
        with self._lock:
            self.on = False
            steps = list(self.steps)
        lines = []
        for s in steps:
            if "fill" in s and norm(s["fill"]) and self.profile.recall(s["fill"]) != s["value"] \
                    and not NEVER_KEEP.search(norm(s["fill"])):
                self.profile.learn(s["fill"], s["value"])
                lines.append(f"learned from you: {s['fill'][:60]} -> {s['value'][:40]}")
        clicks = [(s["site"], s["click"]) for s in steps if "click" in s]
        if learn_button:
            nav = [(site, t) for site, t in clicks if _NAV_OK.match(t) and not _NAV_BAD.search(t)]
            if nav:
                site, text = nav[-1]                 # the last press is the one that moved on
                if self.remember_button(site, text):
                    lines.append(f"learned from you: on {site} the way on is “{text}”")
        recipe = [s for s in steps
                  if not ("click" in s and (_NEVER_REPLAY.search(s["click"]) or len(s["click"]) > 60))]
        recipe = [{k: v for k, v in s.items() if k != "site"} for s in recipe][:30]
        if anchor and url and any("click" in s or "password" in s for s in recipe):
            self._save_recipe(site_key(urlparse(url).netloc), anchor, recipe)
            lines.append(f"learned from you: a {len(recipe)}-step recipe for "
                         f"“{anchor.get('head') or 'this page'}” - I will do it myself next time")
        return lines

    # -- storage ----------------------------------------------------------------------
    def _write(self) -> None:
        self.path.write_text(
            "# What the apply engine learned from you finishing steps by hand.\n"
            "# buttons: per site, the buttons that move an application on.\n"
            "# recipes: per site and page, the steps you took when it was stuck -\n"
            "#   replayed the next time it is stuck on the same page. Delete one to forget it.\n"
            + yaml.safe_dump(self.data, allow_unicode=True, sort_keys=True, width=200),
            encoding="utf-8")

    def remember_button(self, site: str, text: str) -> bool:
        with self._lock:
            buttons = self.data.setdefault("buttons", {}).setdefault(site, [])
            if text in buttons:
                return False
            buttons.append(text)
            self._write()
            return True

    def _save_recipe(self, site: str, anchor: dict, steps: list[dict]) -> None:
        with self._lock:
            recipes = self.data.setdefault("recipes", {}).setdefault(site, {})
            recipes[anchor_key(anchor)] = {"page": anchor.get("head") or "", "steps": steps}
            self._write()

    def buttons_for(self, url: str) -> list[str]:
        site = site_key(urlparse(url).netloc)
        return list((self.data.get("buttons") or {}).get(site, []))

    def recipe_for(self, url: str, anchor: dict) -> list[dict]:
        site = site_key(urlparse(url).netloc)
        found = ((self.data.get("recipes") or {}).get(site) or {}).get(anchor_key(anchor))
        return list(found["steps"]) if found else []
