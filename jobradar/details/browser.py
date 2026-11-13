"""Last resort for a posting drawn by JavaScript: render it in headless
Chromium and hand back the HTML a browser would see.

One browser, in its own thread - Playwright's sync API is bound to the thread
that started it - opened on first use and closed after a minute idle. A scan
asks for at most a handful of renders (Report caps them), so the cost is a few
seconds per page, only where the plain request found an empty shell.
"""
from __future__ import annotations

import queue
import threading

from ..net import BROWSER

IDLE_SECONDS = 60
_lock = threading.Lock()
_jobs: queue.Queue = queue.Queue()
_thread: threading.Thread | None = None
_broken = False          # no Playwright or no Chromium: stop asking for the rest of the run


def render(url: str, timeout: float = 25.0) -> str:
    """The page's HTML after its scripts ran, or "" when it cannot be had."""
    global _thread
    box: dict = {}
    done = threading.Event()
    with _lock:
        if _broken:
            return ""
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_main, daemon=True, name="details-browser")
            _thread.start()
        _jobs.put((url, box, done))
    if not done.wait(timeout + 20):
        return ""
    return box.get("html", "")


def _main() -> None:
    global _thread
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _give_up()
        return
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=BROWSER["User-Agent"])
            # Text is all that is wanted: skip what only paints the page.
            ctx.route("**/*", lambda route: route.abort()
                      if route.request.resource_type in ("image", "media", "font", "stylesheet")
                      else route.continue_())
            while True:
                try:
                    url, box, done = _jobs.get(timeout=IDLE_SECONDS)
                except queue.Empty:
                    with _lock:
                        if _jobs.empty():
                            _thread = None
                            break
                    continue
                page = None
                try:
                    page = ctx.new_page()
                    page.goto(url, timeout=15000, wait_until="domcontentloaded")
                    try:
                        page.wait_for_load_state("networkidle", timeout=4000)
                    except Exception:  # noqa: BLE001 - a page that never settles still has its text
                        pass
                    box["html"] = page.content()
                except Exception as e:  # noqa: BLE001 - one bad page must not end the browser
                    box["error"] = f"{type(e).__name__}: {e}"[:200]
                finally:
                    if page is not None:
                        try:
                            page.close()
                        except Exception:  # noqa: BLE001
                            pass
                    done.set()
            browser.close()
    except Exception:  # noqa: BLE001 - no browser installed: every render returns ""
        _give_up()


def _give_up() -> None:
    """Answer every waiting render with nothing, and every later one at once."""
    global _thread, _broken
    with _lock:
        _broken = True
        _thread = None
        while True:
            try:
                _, _, done = _jobs.get_nowait()
            except queue.Empty:
                return
            done.set()
