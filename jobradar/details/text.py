"""Posting HTML as readable text.

Headings and bullets keep their own lines. Without that every bullet of a
posting runs together, and neither the section split ("Requirements" vs
"Nice to have") nor the experience gate can tell one claim from the next."""
from __future__ import annotations

import html
import re

_DROP = re.compile(r"<(script|style|noscript|svg|template|head|iframe)\b[^>]*>.*?</\1\s*>", re.S | re.I)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_HEADING = re.compile(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]\s*>", re.S | re.I)
_ITEM = re.compile(r"<li\b[^>]*>", re.I)
_BLOCK = re.compile(r"<\s*(?:br|hr|/?p|/?div|/?ul|/?ol|/li|/?tr|/?table|/?section|/?article|/?header|"
                    r"/?footer|/?h[1-6]|/?dl|/?dt|/?dd|/?blockquote|/?pre)\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")
_ODD_SPACE = re.compile(r"[\xa0   ​⁠﻿ ]")
_ESCAPED = re.compile(r"&lt;\s*/?\s*[a-z]", re.I)


def to_text(s: str | None) -> str:
    """HTML, escaped HTML (Greenhouse's `content`) or plain text, as plain
    text with headings and bullets on their own lines."""
    if not s:
        return ""
    for _ in range(2):                    # some boards escape their HTML twice
        if _ESCAPED.search(s):
            s = html.unescape(s)
    if "<" in s:
        s = _COMMENT.sub(" ", s)
        s = _DROP.sub(" ", s)
        cut = re.search(r"<(?:script|style)\b", s, re.I)    # one a size cap left unclosed
        if cut:
            s = s[:cut.start()]
        s = _HEADING.sub(lambda m: "\n\n" + _TAG.sub(" ", m.group(1)) + "\n", s)
        s = _ITEM.sub("\n• ", s)
        s = _BLOCK.sub("\n", s)
        s = _TAG.sub(" ", s)
    return tidy(html.unescape(s))


def tidy(s: str) -> str:
    s = _ODD_SPACE.sub(" ", s).replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in s.split("\n")]
    out = "\n".join(lines)
    out = re.sub(r"•\s*\n+\s*", "• ", out)          # a bullet left alone on its line
    out = re.sub(r"\n(?:\s*•\s*)+(?=\n|$)", "\n", out)  # an empty bullet
    out = re.sub(r"\n\s*\n(?=• )", "\n", out)          # one list, not a blank line per item
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def one_line(s: str | None) -> str:
    return " ".join(to_text(s).split())


_JS_ESCAPE = re.compile(r"\\(x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|.)", re.S)
_JS_CHARS = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}


def js_unescape(s: str) -> str:
    """A single-quoted JS string literal's body as the text it stands for."""
    def one(m: re.Match) -> str:
        e = m.group(1)
        return chr(int(e[1:], 16)) if len(e) > 1 else _JS_CHARS.get(e, e)
    return _JS_ESCAPE.sub(one, s)
