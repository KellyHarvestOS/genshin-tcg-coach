"""Minimal MediaWiki wikitext helpers: balanced template extraction and text cleanup."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Template:
    name: str
    positional: list[str] = field(default_factory=list)
    named: dict[str, str] = field(default_factory=dict)
    raw: str = ""


def _split_top_level(body: str) -> list[str]:
    """Split template body on '|' that are not nested inside {{ }} or [[ ]]."""
    parts, depth_t, depth_l, cur, i = [], 0, 0, [], 0
    while i < len(body):
        two = body[i:i + 2]
        if two == "{{":
            depth_t += 1; cur.append(two); i += 2; continue
        if two == "}}":
            depth_t -= 1; cur.append(two); i += 2; continue
        if two == "[[":
            depth_l += 1; cur.append(two); i += 2; continue
        if two == "]]":
            depth_l -= 1; cur.append(two); i += 2; continue
        ch = body[i]
        if ch == "|" and depth_t == 0 and depth_l == 0:
            parts.append("".join(cur)); cur = []
        else:
            cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return parts


def find_templates(text: str, name: str | None = None) -> list[Template]:
    """Return top-level templates (optionally filtered by name) in order of appearance."""
    out: list[Template] = []
    i = 0
    while True:
        start = text.find("{{", i)
        if start < 0:
            break
        depth, j = 0, start
        while j < len(text):
            if text.startswith("{{", j):
                depth += 1; j += 2
            elif text.startswith("}}", j):
                depth -= 1; j += 2
                if depth == 0:
                    break
            else:
                j += 1
        raw = text[start:j]
        body = raw[2:-2]
        parts = _split_top_level(body)
        tname = parts[0].strip()
        tpl = Template(tname, raw=raw)
        for p in parts[1:]:
            m = re.match(r"^\s*([^=\{\[\|]+?)\s*=(.*)$", p, re.S)
            if m:
                tpl.named[m.group(1).strip()] = m.group(2).strip()
            else:
                tpl.positional.append(p.strip())
        if name is None or tname == name:
            out.append(tpl)
        i = j if j > start else start + 2
    return out


_COLOR_RE = re.compile(r"\{\{Цвет\|([^{}|]+)(?:\|[^{}]*)?\}\}")
_ICON_RE = re.compile(r"\{\{Icon/СП7\|[^{}]*\}\}")
_LINK_RE = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]")


def clean(text: str) -> str:
    """Wikitext -> readable plain text (keeps element names from {{Цвет}})."""
    t = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    t = _ICON_RE.sub("", t)
    t = _COLOR_RE.sub(lambda m: m.group(1), t)
    t = _LINK_RE.sub(lambda m: m.group(1), t)
    t = re.sub(r"\{\{СП7 Помощник\|([^{}]*)\}\}", r"\1", t)
    t = re.sub(r"<br\s*/?>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = t.replace("'''", "").replace("''", "")
    t = re.sub(r"\{\{[^{}]*\}\}", "", t)  # drop leftover simple templates
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return t.strip()
