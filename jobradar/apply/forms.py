"""Reading and filling an application form, whatever board it is on.

scan() tags every field on the page (data-jr=N) and returns what a person would
see: the question, the kind of control, its choices, whether it is required and
what it already holds. fill() answers each one through the run's answerer.
Board engines call these and add only what their board does differently.
"""
from __future__ import annotations

import re
import time

from .answers import OPEN, choose_option, first_alt, nearest_option, norm


def nearest(run, label: str, value: str | None, options: list[str]) -> str | None:
    """Your rule for choice fields: the option that matches, else the nearest
    one - by spelling, then by asking the model which choice means the answer."""
    if value is None or not options:
        return None
    hit = nearest_option(value, options)
    if hit is None:
        guess = run.infer(f"{label} (the candidate's answer is: {first_alt(value)}; "
                          f"pick the choice closest to it)", options)
        hit = choose_option(guess, options) if guess else None
    return hit

SCAN_JS = r"""
(root) => {
  root = root || document;
  const txt = (el) => (el ? (el.innerText || el.textContent || '') : '').replace(/\s+/g, ' ').trim();
  const shown = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const byId = (id) => id ? document.getElementById(id) : null;
  function labelOf(el) {
    const lb = el.getAttribute('aria-labelledby');
    if (lb) {
      const t = lb.split(/\s+/).map((i) => txt(byId(i))).filter(Boolean).join(' ');
      if (t) return t;
    }
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l && txt(l)) return txt(l);
    }
    const al = el.getAttribute('aria-label');
    if (al && al.trim().length > 1) return al.trim();
    const own = el.closest('label');
    if (own && txt(own)) return txt(own);
    let p = el.parentElement;
    for (let i = 0; i < 5 && p; i++, p = p.parentElement) {
      const l = p.querySelector(':scope > label, :scope > legend, :scope > div > label, [class*="label" i], [data-automation-id*="formLabel" i]');
      if (l && !l.contains(el) && txt(l)) return txt(l);
    }
    return el.getAttribute('placeholder') || el.getAttribute('name') || '';
  }
  // An upload's own label is its button ("Attach"); the question is the group's.
  const BTN = /^(attach|upload|browse|choose( a)? file|dropbox|google drive|enter manually|select file)/i;
  function fileLabel(el) {
    const grp = el.closest('[role="group"][aria-labelledby]');
    if (grp) { const t = txt(byId(grp.getAttribute('aria-labelledby'))); if (t) return t; }
    let p = el.parentElement;
    for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
      for (const l of p.querySelectorAll('label, legend, [class*="label" i], h3, h4')) {
        const t = txt(l);
        if (t && !BTN.test(t) && !l.contains(el)) return t;
      }
    }
    return labelOf(el);
  }
  function groupLabel(el) {
    const g = el.closest('fieldset, [role="radiogroup"], [role="group"]');
    if (g) {
      const lg = g.querySelector('legend') || byId(g.getAttribute('aria-labelledby'));
      if (lg && txt(lg)) return txt(lg);
      const al = g.getAttribute('aria-label'); if (al) return al;
    }
    let p = el.closest('label') ? el.closest('label').parentElement : el.parentElement;
    for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
      const l = p.querySelector(':scope > label:not(:has(input)), :scope > legend, :scope > p, :scope > div > label:not(:has(input)), [class*="question" i] > label');
      if (l && txt(l) && !l.contains(el)) return txt(l);
    }
    return el.name || '';
  }
  // Boards mark required questions with a star that often sits outside the
  // label text proper (a separate span, a ::after), so look near the field too.
  const STAR = /[*✱]\s*$|[*✱]\s*\(|\brequired\b/i;
  function required(el, label) {
    if (!el) return false;
    if (el.required || el.getAttribute('aria-required') === 'true' || STAR.test(label || '')) return true;
    const fs = el.closest('fieldset');
    if (fs && fs.querySelector(':scope > label[class*="required" i], :scope > legend[class*="required" i]')) return true;
    let p = el.parentElement;
    for (let i = 0; i < 4 && p; i++, p = p.parentElement) {
      const l = p.querySelector('label, legend, [class*="label" i]');
      if (l && !l.contains(el)) {
        if (STAR.test((l.textContent || '').trim())) return true;
        if (/required/i.test(String(l.className || ''))) return true;
        const after = getComputedStyle(l, '::after').content || '';
        return /[*✱]/.test(after);
      }
    }
    return false;
  }
  let n = Number(document.body.dataset.jrN || 0);
  const tag = (el) => { if (!el.dataset.jr) el.dataset.jr = String(++n); return el.dataset.jr; };
  const out = [], groups = {};
  for (const el of root.querySelectorAll('input, textarea, select, [role="combobox"], button[aria-haspopup="listbox"]')) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    // passwords are the sign-in step's job (from .env), never a form answer
    if (['hidden', 'submit', 'button', 'reset', 'image', 'search', 'password'].includes(type)) continue;
    if (el.disabled || el.readOnly && type !== 'file' && el.getAttribute('role') !== 'combobox') continue;
    if (type === 'radio' || type === 'checkbox') {
      const lab = el.closest('label') || (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`));
      if (!shown(el) && !(lab && shown(lab))) continue;
      const optLabel = txt(lab) || el.getAttribute('aria-label') || el.value;
      // A checkbox with no group around it is its own question ("I agree to...");
      // borrowing a nearby label would ask it as "Email Address".
      const lone = type === 'checkbox' && !el.closest('fieldset, [role="group"], [role="radiogroup"]') && optLabel;
      const glabel = lone ? optLabel : groupLabel(el);
      const key = (el.name || '') + '|' + glabel + (lone ? '|' + tag(el) : '');
      if (!groups[key]) {
        groups[key] = { id: tag(el), kind: type === 'radio' ? 'radio' : 'checkboxes', label: glabel,
                        required: false, options: [], optionIds: [], checked: [] };
        out.push(groups[key]);
      }
      const g = groups[key];
      g.options.push(optLabel); g.optionIds.push(tag(el));
      if (el.checked) g.checked.push(optLabel);
      if (el.required || el.getAttribute('aria-required') === 'true') g.required = true;
      continue;
    }
    if (type !== 'file' && !shown(el)) continue;
    // react-select and friends keep a hidden twin input for validation
    if (type !== 'file' && (el.getAttribute('aria-hidden') === 'true' ||
        (el.tabIndex === -1 && parseFloat(getComputedStyle(el).opacity) < 0.1))) continue;
    const label = type === 'file' ? fileLabel(el) : labelOf(el);
    const f = { id: tag(el), label, required: required(el, label), value: '', options: [] };
    if (type === 'file') {
      f.kind = 'file';
      f.accept = el.getAttribute('accept') || '';
      f.value = el.files && el.files.length ? el.files[0].name : '';
    } else if (el.tagName === 'SELECT') {
      f.kind = 'select';
      f.options = [...el.options].map((o) => o.text.trim());
      f.value = el.selectedIndex > 0 ? el.options[el.selectedIndex].text.trim() : '';
      f.multiple = el.multiple;
    } else if (el.getAttribute('role') === 'combobox' || el.getAttribute('aria-haspopup') === 'listbox' || el.getAttribute('aria-autocomplete')) {
      f.kind = el.tagName === 'BUTTON' ? 'listbox' : 'combobox';
      // The chosen value sits beside the input (react-select: two levels up);
      // stop before climbing into a wrapper that holds another dropdown.
      let chosen = null, up = el.parentElement;
      for (let i = 0; i < 4 && up && !chosen; i++, up = up.parentElement) {
        if (i && up.querySelectorAll('[role="combobox"]').length > 1) break;
        chosen = up.querySelector('[class*="single-value" i], [class*="singleValue" i], [class*="multi-value__label" i], [class*="selected-value" i], [data-automation-id="selectedItem"]');
      }
      f.value = chosen ? txt(chosen) : (el.tagName === 'BUTTON' ? '' : el.value);
      if (el.tagName === 'BUTTON') {
        const t = txt(el);
        f.value = /^select|^choose|^$/i.test(t) ? '' : t;
      }
    } else {
      f.kind = el.tagName === 'TEXTAREA' ? 'textarea' : (['number', 'email', 'tel', 'url', 'date'].includes(type) ? type : 'text');
      f.value = el.value || '';
    }
    out.push(f);
  }
  // Yes/No asked as a pair of buttons (Ashby and others): one question, two choices.
  const seen = new Set();
  for (const b of root.querySelectorAll('button')) {
    const box = b.parentElement;
    if (!box || seen.has(box) || !shown(b)) continue;
    const btns = [...box.children].filter((c) => c.tagName === 'BUTTON');
    const names = btns.map((c) => txt(c));
    if (btns.length < 2 || btns.length > 4 || !names.includes('Yes') || !names.includes('No')) continue;
    seen.add(box);
    const label = groupLabel(btns[0]);
    const on = btns.find((c) => c.getAttribute('aria-pressed') === 'true' || /(^|[\s_-])(active|selected|checked)/i.test(String(c.className)));
    out.push({ id: tag(btns[0]), kind: 'buttons', label, required: STAR.test(label) || required(btns[0], label),
               options: names, optionIds: btns.map(tag), value: on ? txt(on) : '' });
  }
  for (const g of out) if (g.kind === 'radio' || g.kind === 'checkboxes') {
    g.required = g.required || STAR.test(g.label) || required(document.querySelector(`[data-jr="${g.optionIds[0]}"]`), g.label);
    g.value = g.checked.join(', ');
  }
  document.body.dataset.jrN = String(n);
  return out;
}
"""

ERRORS_JS = r"""
() => [...document.querySelectorAll('[role="alert"], [class*="error" i], [aria-invalid="true"], [data-automation-id*="error" i]')]
  .filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
  .map((e) => (e.innerText || e.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim())
  .filter((t) => t && t.length < 300)
"""

DONE = re.compile(
    r"thank(s| you) for (applying|your (application|interest))|application (has been |was )?"
    r"(submitted|received|sent)|we('ve| have) received your application|successfully (applied|submitted)|"
    r"you('ve| have) (successfully )?applied|application complete|your application is (in|complete)",
    re.I)


def keyboard(frame):
    """A Page has a keyboard; a Frame reaches it through its page."""
    return frame.keyboard if hasattr(frame, "keyboard") else frame.page.keyboard


def scan(frame) -> list[dict]:
    return frame.evaluate(SCAN_JS)


def el(frame, fid: str):
    return frame.locator(f'[data-jr="{fid}"]').first


def page_text(frame) -> str:
    try:
        return frame.evaluate("() => document.body.innerText || ''")
    except Exception:  # noqa: BLE001 - mid-navigation
        return ""


def submitted(frame) -> bool:
    return bool(DONE.search(page_text(frame)))


def errors(frame) -> list[str]:
    try:
        return [e for e in frame.evaluate(ERRORS_JS) if not re.fullmatch(r"(?i)error", e)][:8]
    except Exception:  # noqa: BLE001
        return []


def _kind(f: dict) -> str:
    if f["kind"] in ("select", "radio", "checkboxes", "combobox", "listbox", "buttons"):
        return "choice"
    return {"number": "number", "textarea": "textarea"}.get(f["kind"], "text")


def is_resume_field(f: dict) -> bool:
    """Any upload that is not plainly for something else takes the resume -
    including 'Autofill from resume' boxes whose label reads 'Name'."""
    label = norm(f["label"])
    return not re.search(r"cover|transcript|photo|picture|certificate|marksheet|id proof|"
                         r"aadhaar|passport|\bpan\b|payslip|salary slip|offer letter|relieving", label)


def answer(run, f: dict, fallback: bool = True) -> str | None:
    """What goes in this field, or None to leave it alone. fallback=False stops
    before the model and you - a dropdown asks those once its list is open."""
    label, kind = f["label"], _kind(f)
    options = f.get("options") or []
    if re.search(r"(?i)captcha", label):
        return None                     # yours to solve; the engine pauses for it
    v = run.profile.recall(label)
    if v is None:
        from .answers import rule_answer
        v = rule_answer(run.profile, label, kind)
    if v is None and OPEN.search(norm(label)) and (f["required"] or kind == "textarea"):
        # Written from the job description and your resume together.
        v = run.write(label)
    if not fallback:
        return v
    # Your rule: a question no rule covers is answered by the AI from your
    # resume, profile and skill ratings - optional ones too; you are asked
    # only when a required one is a fact nothing on file states.
    if v is None and (f["required"] or is_question(f)):
        v = run.infer(label, options)
        if v is not None and options and nearest_option(v, options) is None:
            v = None
    if v is None and f["required"]:
        v = run.ask(label, options)
    return v


def is_question(f: dict) -> bool:
    """A real question the form asks (worth answering even when optional), as
    opposed to an optional detail field like 'Middle name' or 'Website 2'."""
    label = (f.get("label") or "").strip()
    return label.endswith("?") or bool(re.match(
        r"(?i)(do|does|are|have|has|how|what|which|will|would|can|could|is|rate|please (rate|describe|specify))\b",
        label))


def _cover_rule(fields: list[dict]) -> None:
    """Your rule: when the only upload is a cover letter, the resume goes there."""
    files = [f for f in fields if f["kind"] == "file"]
    if files and not any(is_resume_field(f) for f in files):
        for f in files:
            if "cover" in norm(f["label"]):
                f["label"] = "Resume (cover letter slot)"


def fill(run, frame, skip: set[str] | None = None) -> list[dict]:
    """Answer every field on the page. Returns the required fields still empty.

    Uploads go first and the page is read again after them: boards parse the
    resume and redraw the form (Ashby's autofill replaces every field), so
    anything read before an upload may no longer exist. A second pass catches
    fields that appear only once others are answered."""
    skip = skip or set()
    fields = scan(frame)
    _cover_rule(fields)
    uploaded = False
    sent: set[str] = set()              # labels we put a file in
    for f in fields:
        if f["kind"] == "file" and f["id"] not in skip:
            run.check()
            try:
                if _fill_one(run, frame, f):
                    sent.add(f["label"])
                    uploaded |= not f["value"]
            except Exception as e:  # noqa: BLE001
                run.log(f"could not upload to '{f['label'][:60]}': {type(e).__name__}")
    if uploaded:
        time.sleep(2)
    tried: set[str] = set()
    missing: list[dict] = []
    for _pass in range(2):
        if _pass:
            time.sleep(1.5)             # late fields: consent boxes, follow-up questions
        missing = []
        for f in scan(frame):
            if f["id"] in skip or f["kind"] == "file":
                if (f["kind"] == "file" and f["required"] and not f["value"]
                        and is_resume_field(f) and f["label"] not in sent):
                    missing.append(f)
                continue
            key = f"{f['kind']}|{f['label']}"
            if key in tried:
                # answered on the first pass: never click it again (a second
                # click on a toggle undoes it); report it only if still empty
                if not f.get("value") and f["required"] and f["kind"] not in ("buttons",):
                    missing.append(f)
                continue
            if f.get("value") and f["kind"] not in ("checkboxes", "buttons", "select", "listbox",
                                                      "combobox"):
                continue
            tried.add(key)
            run.check()
            try:
                done = _fill_one(run, frame, f)
            except Exception as e:  # noqa: BLE001 - one odd field must not end the application
                run.log(f"could not fill '{f['label'][:60]}': {type(e).__name__}")
                done = False
            if not done and f["required"]:
                missing.append(f)
    return missing


def _fill_one(run, frame, f: dict) -> bool:
    kind = f["kind"]
    if kind == "file":
        if f["value"]:
            return True
        if is_resume_field(f):
            el(frame, f["id"]).set_input_files(str(run.resume["path"]))
            run.log(f"uploaded {run.resume['path'].name}")
            time.sleep(2.5)          # boards parse it and prefill fields
            return True
        if "cover" in norm(f["label"]) and f["required"]:
            # Your rule: a required cover-letter upload gets the resume.
            el(frame, f["id"]).set_input_files(str(run.resume["path"]))
            run.log(f"cover letter required - uploaded {run.resume['path'].name}")
            return True
        return False
    if f["value"] and kind in ("select", "listbox", "combobox"):
        # A preset choice (Workday's country defaults to the US) is kept only
        # when it agrees with what you told us.
        from .answers import rule_answer
        want = run.profile.recall(f["label"]) or rule_answer(run.profile, f["label"], "choice")
        if not want or choose_option(want, [f["value"]]) is not None:
            return True
        f = {**f, "value": ""}
        v = want
    elif f["value"] and kind not in ("checkboxes", "buttons"):
        return True                              # prefilled - by the board or by us
    elif kind in ("combobox", "listbox"):
        return pick_from_list(run, frame, el(frame, f["id"]), f, answer(run, f, fallback=False))
    else:
        v = answer(run, f)
    if v is None:
        return False
    if v == "":
        return not f["required"]
    loc = el(frame, f["id"])
    label = f["label"][:60]
    if kind in ("text", "email", "tel", "url", "number", "date", "textarea"):
        text = first_alt(v)
        if kind == "number":
            m = re.search(r"-?\d+(?:\.\d+)?", text)
            text = m.group(0) if m else text
        loc.fill(text)
        run.log(f"{label}: {text[:50]}")
        if kind == "text" and re.search(r"location|city|where are you based", norm(f["label"])):
            _pick_suggestion(run, frame, loc, text)
        return True
    if kind == "select":
        pick = nearest(run, f["label"], v, f["options"])
        if pick is None:
            pick = run.ask(f["label"], f["options"]) if f["required"] else None
            pick = choose_option(pick, f["options"]) if pick else None
        if pick is None:
            return False
        loc.select_option(label=pick)
        run.log(f"{label}: {pick}")
        return True
    if kind in ("radio", "checkboxes"):
        if kind == "checkboxes" and len(f["options"]) == 1:
            # a lone box: consent, "I agree" - ticked when the answer is yes
            if choose_option(v, ["Yes", "No"]) == "Yes" and not f["value"]:
                _check(frame, f["optionIds"][0])
                run.log(f"ticked: {label}")
            return True
        if f["value"]:
            return True
        pick = nearest(run, f["label"], v, f["options"])
        if pick is None and f["required"]:
            ans = run.ask(f["label"], f["options"])
            pick = choose_option(ans, f["options"])
        if pick is None:
            return False
        _check(frame, f["optionIds"][f["options"].index(pick)])
        run.log(f"{label}: {pick}")
        return True
    if kind in ("combobox", "listbox"):
        return pick_from_list(run, frame, loc, f, v)
    if kind == "buttons":
        pick = nearest(run, f["label"], v, f["options"])
        if pick is None and f["required"]:
            pick = choose_option(run.ask(f["label"], f["options"]), f["options"])
        if pick is None:
            return False
        el(frame, f["optionIds"][f["options"].index(pick)]).click()
        run.log(f"{label}: {pick}")
        return True
    return False


SUGGESTIONS = ('[role="option"]:visible, .dropdown-location:visible, .pac-item:visible, '
               '[class*="suggestion" i] li:visible, [class*="autocomplete" i] li:visible, '
               '[class*="dropdown-results" i] > div:visible')


def _pick_suggestion(run, frame, loc, text: str) -> None:
    """Typed a place: if the box offers suggestions, take the closest one."""
    try:
        loc.press("End")
        time.sleep(1.5)
        opts = frame.locator(SUGGESTIONS)
        texts = [opts.nth(i).inner_text().strip() for i in range(min(opts.count(), 15))]
        if not texts:
            return
        pick = nearest_option(text, texts) or nearest_option(text.split(",")[0], texts) or texts[0]
        opts.nth(texts.index(pick)).click()
        run.log(f"picked suggestion: {pick[:50]}")
    except Exception:  # noqa: BLE001 - no suggestions is fine; the text stays
        pass


def _check(frame, fid: str) -> None:
    box = el(frame, fid)
    try:
        box.check(timeout=3000)
    except Exception:  # noqa: BLE001 - styled boxes hide the input; click what shows
        box.evaluate("(e) => (e.closest('label') || document.querySelector(`label[for=\"${e.id}\"]`) || e).click()")


NO_OPTIONS = re.compile(r"(?i)^(no (options|results|matches)( found)?|loading\.*|searching\.*|type to search.*)$")


def visible_options(frame) -> tuple[list, list[str]]:
    """The open dropdown's choices: (locators, texts)."""
    loc = frame.locator('[role="option"]:visible, [class*="select__option"]:visible, '
                        '[data-automation-id="promptOption"]:visible, [role="listbox"] li:visible')
    items, texts = [], []
    for i in range(min(loc.count(), 400)):
        try:
            t = loc.nth(i).inner_text().strip()
        except Exception:  # noqa: BLE001
            continue
        if t and not NO_OPTIONS.match(t) and t not in texts:
            items.append(loc.nth(i))
            texts.append(t)
    return items, texts


def _open(frame, loc, f: dict, typed: str | None) -> tuple[list, list[str]]:
    """Open the list, or - with `typed` - type into it. Typing uses real key
    presses (they open the menu); a second click would only close it again."""
    if typed is not None and f["kind"] == "combobox":
        try:
            loc.focus()
            loc.fill("")
            loc.press_sequentially(typed[:40], delay=35)
        except Exception:  # noqa: BLE001 - a read-only combobox
            pass
        time.sleep(1.5)             # search-as-you-type lists load from the server
        return visible_options(frame)
    items, texts = visible_options(frame)
    if texts:
        return items, texts          # already open
    try:
        loc.click(timeout=5000)
    except Exception:  # noqa: BLE001 - covered by its own menu: focus it instead
        loc.focus()
        keyboard(frame).press("ArrowDown")
    time.sleep(0.7)
    return visible_options(frame)


def pick_from_list(run, frame, loc, f: dict, value: str | None) -> bool:
    """A custom dropdown: open it and read the choices first, typing to narrow
    only a long or server-searched list. With no rule answer, the model and
    then you are asked - with the real choices in front of them."""
    label = f["label"][:60]
    items, texts = _open(frame, loc, f, None)
    if value is not None and f["kind"] == "combobox" and choose_option(value, texts) is None:
        items, texts = _open(frame, loc, f, first_alt(value))
    pick = choose_option(value, texts) if value is not None else None
    if pick is None and value is not None:
        if not texts:
            items, texts = _open(frame, loc, f, "")
        pick = nearest(run, f["label"], value, texts)
    if pick is None and value is None and not f["required"]:
        # optional: the AI answers it if it is a real question, else leave it
        ans = run.infer(f["label"], texts[:80]) if texts and is_question(f) else None
        pick = nearest_option(ans, texts) if ans else None
        if pick is None:
            keyboard(frame).press("Escape")
            return False
    if pick is None and f["required"]:
        if not texts:                        # nothing typed matched: show everything
            items, texts = _open(frame, loc, f, "")
        keyboard(frame).press("Escape")
        time.sleep(0.3)
        ans = run.infer(f["label"], texts[:80]) if texts else None
        if ans is None or choose_option(ans, texts) is None:
            ans = run.ask(f["label"], texts[:80])
        pick = choose_option(ans, texts) if ans is not None else None
        if pick is None and ans and f["kind"] == "combobox":
            items, texts = _open(frame, loc, f, ans)
            pick = choose_option(ans, texts)
        elif pick is not None:
            time.sleep(0.3)
            items, texts = _open(frame, loc, f, None)
            if pick not in texts and f["kind"] == "combobox":
                items, texts = _open(frame, loc, f, pick)
    if pick is None or pick not in texts:
        keyboard(frame).press("Escape")
        return False
    items[texts.index(pick)].click()
    run.log(f"{label}: {pick}")
    time.sleep(0.4)
    return True


def find_first(frame, patterns: list[str], roles=("button", "link"), timeout: float = 0):
    """The first visible, enabled button or link whose name matches a pattern."""
    end = time.time() + timeout
    while True:
        for pat in patterns:
            rx = re.compile(pat, re.I)
            for role in roles:
                loc = frame.get_by_role(role, name=rx)
                for i in range(min(loc.count(), 5)):
                    item = loc.nth(i)
                    try:
                        if item.is_visible() and item.is_enabled():
                            return item
                    except Exception:  # noqa: BLE001 - detached mid-check
                        continue
        if time.time() >= end:
            return None
        time.sleep(0.5)


def safe_click(item) -> None:
    """Click like a person; when a banner, overlay or sticky footer covers the
    button, click through it, and as a last resort let the page click it."""
    try:
        item.scroll_into_view_if_needed(timeout=5000)
    except Exception:  # noqa: BLE001
        pass
    try:
        item.click(timeout=8000)
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        item.click(timeout=5000, force=True)
        return
    except Exception:  # noqa: BLE001
        pass
    item.evaluate("(e) => e.click()")


def click_first(frame, patterns: list[str], roles=("button", "link"), timeout: float = 0) -> bool:
    item = find_first(frame, patterns, roles, timeout)
    if item is None:
        return False
    safe_click(item)
    return True
