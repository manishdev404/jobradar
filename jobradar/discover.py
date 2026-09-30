"""Find a company's job board, and re-find it when the old one has gone.

Three places are tried, most trusted first:
  1. a board given with the name (a Name|board|slug line) - checked, not believed
  2. the company's own careers page: links or embeds pointing at a readable
     board (the only way to find Workday / Oracle, whose URLs cannot be guessed)
  3. slugs guessed from the name and domain, probed on every hosted board

A board found by guessing only counts when it lists at least one job in India:
that is what tells razorpay's board apart from an unrelated 'razorpay' elsewhere,
and a board with no India jobs is not worth scanning anyway.

Input lines (one company each; '#' comments and blanks are skipped):
  Name
  Name|domain.com
  Name|board|slug|careers URL|confidence      (the prompt format)
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from . import sources
from .companies import careers_url, check_company, detect_board
from .store import Store

# Workable rate-limits a burst of misses (HTTP 429), so it is only used when a
# careers page links to it.
_GUESSED = ("greenhouse", "lever", "ashby", "keka")
_EXISTS = {"greenhouse": "https://boards-api.greenhouse.io/v1/boards/{}",
           "lever": "https://api.lever.co/v0/postings/{}?mode=json&limit=1",
           "ashby": "https://api.ashbyhq.com/posting-api/job-board/{}"}
_SUFFIX = re.compile(r"\b(technologies|technology|tech|labs?|software|solutions|systems|"
                     r"private|pvt|limited|ltd|inc|llc|corp(oration)?|india|global|group|"
                     r"hq|co|the|and)\b", re.I)
_EMBED = re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([A-Za-z0-9_-]+)", re.I)
_PAGE_TIMEOUT = 8
_PAGE_BYTES = 300_000          # board links sit in the markup, not deep in a 3 MB bundle
_local = threading.local()


def _session():
    """One keep-alive session per worker thread: repeated probes of the same
    board API then skip the TLS handshake, the bulk of a miss's cost."""
    import requests
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    return _local.s


def slug_guesses(name: str, domain: str = "") -> list[str]:
    """'Dream Sports Pvt Ltd', 'dreamsports.group' -> dreamsports, dream-sports, ..."""
    base = re.sub(r"\([^)]*\)", " ", name).replace("&", " and ").lower()
    words = re.findall(r"[a-z0-9]+", base)
    core = re.findall(r"[a-z0-9]+", _SUFFIX.sub(" ", base)) or words
    root = domain.lower().removeprefix("www.").split(".")[0] if domain else ""
    out = []
    for s in ("".join(words), "".join(core), root, "-".join(words), "".join(core) + "hq"):
        if s and s not in out and len(s) > 1:
            out.append(s)
    return out[:4]


def _domain_of(url: str) -> str:
    host = urlparse(url if "//" in url else f"https://{url}").hostname or ""
    host = host.lower().removeprefix("www.")
    for prefix in ("careers.", "jobs.", "career.", "hiring."):
        if host.startswith(prefix) and host.count(".") >= 2:
            host = host[len(prefix):]
    return "" if re.search(r"(greenhouse|lever|ashbyhq|workable|myworkdayjobs|keka|darwinbox|"
                           r"zohorecruit|oraclecloud|linkedin|naukri)\.", host) else host


def _boards_in(text: str) -> list[tuple[str, str]]:
    """Every readable (board, slug) mentioned in a page, in page order."""
    found = [("greenhouse", m.group(1)) for m in _EMBED.finditer(text)]
    for url in re.findall(r"https?://[^\s\"'<>)\\]+", text):
        board, slug = detect_board(url)
        if board != "other" and slug.lower() not in ("embed", "v1", "jobs", "api", "careers"):
            found.append((board, slug.strip("/")))
    seen, out = set(), []
    for b in found:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def _fetch(url: str) -> tuple[str, str] | None:
    """(final url, first _PAGE_BYTES of the page) or None."""
    try:
        with _session().get(url, headers=sources.BROWSER, timeout=_PAGE_TIMEOUT,
                            allow_redirects=True, stream=True) as r:
            if not r.ok:
                return None
            body = b""
            for chunk in r.iter_content(65536):
                body += chunk
                if len(body) >= _PAGE_BYTES:
                    break
            return r.url, body.decode(r.encoding or "utf-8", "replace")
    except Exception:  # noqa: BLE001 - a dead site is just 'nothing found'
        return None


def boards_on_careers_page(domain: str, page: str = "") -> list[tuple[str, str]]:
    """Boards linked from the company's careers pages: the known careers link
    first, then the usual paths, then one hop from the home page to whatever
    it calls careers / jobs."""
    urls = [page] if page else []
    if domain:
        urls += [f"https://{domain}/careers", f"https://careers.{domain}"]
    tried: set[str] = set()
    for url in urls + ([f"https://www.{domain}"] if domain else []):
        if url in tried:
            continue
        tried.add(url)
        got = _fetch(url)
        if got is None:
            continue
        final, text = got
        hits = _boards_in(final + " " + text)
        if hits:
            return hits
        if url.startswith(f"https://www.{domain}") and url.count("/") == 2:   # home page
            for href in re.findall(r"href=[\"']([^\"'#]+)", text):
                if re.search(r"career|jobs|join-us|work-with-us|hiring", href, re.I):
                    nxt = urljoin(final, href)
                    if nxt not in tried and len(tried) < 6:
                        tried.add(nxt)
                        page2 = _fetch(nxt)
                        if page2 is not None:
                            hits = _boards_in(" ".join(page2))
                            if hits:
                                return hits
    return []


def _core(name: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", _SUFFIX.sub(" ", name.lower())))


def same_company(name: str, board_name: str, domain: str = "") -> bool:
    """Is the name a board publishes this company? 'Razorpay Software Private
    Limited' is Razorpay; 'Lighthouse' is not 'Lighthouse Storage'."""
    theirs = _core(board_name)
    root = domain.lower().removeprefix("www.").split(".")[0] if domain else ""
    return bool(theirs) and (_core(name) in theirs or (len(root) > 3 and root in theirs))


def _exists(board: str, slug: str, name: str = "", domain: str = "") -> bool:
    """One quick request, no retry: most guesses are misses and a miss on the
    JSON boards is a fast 404. Greenhouse also names the company, so a guessed
    slug that belongs to someone else is caught here. Boards without such an
    endpoint count as maybe."""
    if board not in _EXISTS:
        return True
    try:
        r = _session().get(_EXISTS[board].format(slug), headers=sources.UA,
                           timeout=_PAGE_TIMEOUT)
        if board == "greenhouse" and r.ok and name:
            return same_company(name, r.json().get("name") or "", domain)
        return r.ok
    except Exception:  # noqa: BLE001
        return False


def _try(name: str, board: str, slug: str, guessed: bool = False,
         domain: str = "") -> dict | None:
    if guessed and not _exists(board, slug, name, domain):
        return None
    got = check_company({"name": name, "board": board, "slug": slug})
    return {**got, "board": board, "slug": slug} if got["status"] == "ok" else None


def find_board(name: str, domain: str = "", hint: tuple[str, str] | None = None,
               page: str = "") -> dict | None:
    """{'board', 'slug', 'india', 'tech', 'how'} or None. A board named by
    the company's own careers page is accepted even with no India jobs today;
    a guessed one needs at least one."""
    fallback = None
    if hint and hint[0] in sources.BOARDS and hint[1]:
        got = _try(name, *hint)
        if got and got["india"]:
            return {**got, "how": "given"}
    for board, slug in boards_on_careers_page(domain, page)[:4]:
        got = _try(name, board, slug)
        if got and got["india"]:
            return {**got, "how": "careers page"}
        fallback = fallback or (got and {**got, "how": "careers page"})
    for i, slug in enumerate(slug_guesses(name, domain)):
        for board in _GUESSED:
            got = _try(name, board, slug, guessed=True, domain=domain)
            if got and got["india"]:
                return {**got, "how": "name"}
        if i > 1:                   # the HR suites use the plain company name
            continue
        for host in (f"{slug}.darwinbox.in", f"{slug}.zohorecruit.in", f"{slug}.zohorecruit.com"):
            got = _try(name, "darwinbox" if "darwinbox" in host else "zoho", host)
            if got and got["india"]:
                return {**got, "how": "name"}
    return fallback


def parse_lines(text: str) -> list[dict]:
    out, seen = [], set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = [x.strip() for x in line.split("|")]
        item = {"name": p[0], "domain": "", "hint": None, "page": ""}
        if len(p) >= 3 and p[1].lower() in sources.BOARDS:
            item["hint"] = (p[1].lower(), p[2].strip("/"))
            item["page"] = p[3] if len(p) > 3 and p[3].startswith("http") else ""
            item["domain"] = _domain_of(item["page"])
        elif len(p) >= 2:
            item["domain"] = _domain_of(p[1]) if "." in p[1] else ""
        if item["name"] and item["name"].lower() not in seen:
            seen.add(item["name"].lower())
            out.append(item)
    return out


def _search(item: dict) -> dict | None:
    try:
        return find_board(item["name"], item["domain"], item["hint"], item["page"])
    except Exception:  # noqa: BLE001 - one odd site must not stop the run
        return None


def run(items: list[dict], store: Store, source: str, workers: int = 16,
        progress=print) -> dict:
    """Find boards for `items` and save them. A company already on file with a
    working board is left alone; one whose board is gone (or never readable)
    is searched again and updated when a new board turns up."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    have = {c["name"].lower(): c for c in store.companies()}
    todo = []
    counts = {"added": 0, "updated": 0, "kept": 0, "not found": 0}
    for it in items:
        c = have.get(it["name"].lower())
        if c and c["enabled"] and c["board"] != "other":
            counts["kept"] += 1
            continue
        if c:
            it["domain"] = it["domain"] or c["domain"] or _domain_of(c["careers_url"])
            if c["board"] == "other" and c["careers_url"].startswith("http"):
                it["page"] = it["page"] or c["careers_url"]
        todo.append(it)
    missing: list[str] = []
    # A board already on file under another name is the same company
    # ('Groww Invest' finding greenhouse/groww): never a second row.
    taken = {(c["board"], c["slug"].lower()): c["name"] for c in have.values()
             if c["board"] != "other"}
    counts["same board as another name"] = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_search, it): it for it in todo}
        for n, fut in enumerate(as_completed(futures), 1):
            it, got = futures[fut], fut.result()
            existed = it["name"].lower() in have
            if got is None or (not existed and not got["india"]):
                counts["not found"] += 1
                missing.append(it["name"])
                continue
            owner = taken.get((got["board"], got["slug"].lower()))
            if owner and owner.lower() != it["name"].lower():
                counts["same board as another name"] += 1
                continue
            taken[(got["board"], got["slug"].lower())] = it["name"]
            store.upsert_company({
                "name": it["name"], "board": got["board"], "slug": got["slug"],
                "careers_url": careers_url(got["board"], got["slug"]),
                "domain": it["domain"], "source": source, "enabled": True,
                "india_jobs": got["india"], "india_tech_jobs": got["tech"],
                "checked_at": time.time(), "note": f"found via {got['how']}"})
            counts["updated" if existed else "added"] += 1
            progress(f"[{n}/{len(todo)}] {it['name']}: {got['board']}/{got['slug']} "
                     f"({got['india']} India, {got['tech']} tech, via {got['how']})")
    store.db.commit()
    counts["missing"] = sorted(missing)
    return counts


def run_file(path: Path, store: Store, workers: int = 24) -> dict:
    return run(parse_lines(path.read_text(encoding="utf-8")), store, f"discover:{path.name}",
               workers)


def run_disabled(store: Store, workers: int = 24) -> dict:
    """Search again for every company whose board is gone or unreadable."""
    items = [{"name": c["name"], "domain": c["domain"], "hint": None, "page": ""}
             for c in store.companies() if not c["enabled"] or c["board"] == "other"]
    return run(items, store, "discover:refind", workers)
