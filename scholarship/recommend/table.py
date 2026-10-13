# -*- coding: utf-8 -*-
"""[RECOMMEND] 표 출력: 한글(한 글자가 두 칸)을 계산해 정렬하고, 긴 내용은 칸 안에서 줄바꿈한다.

- ascii: `+---+` 와 `|` 로 테두리를 그린다. 줄 그리기 문자(┌─┐)는 한국어 콘솔에서 폭이 어긋나기 쉬워 쓰지 않는다.
- md: 마크다운 표 (채팅·문서에 붙여넣기 좋다). 줄바꿈은 <br> 로, 폭 계산은 하지 않는다.
"""
from __future__ import annotations

import unicodedata
from typing import List, Optional, Sequence

ELLIPSIS = "..."


def display_width(s: str) -> int:
    """화면에서 차지하는 칸 수 (한글·한자·전각 문자는 2칸, 결합 문자는 0칸)."""
    w = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def pad(s: str, width: int, align: str = "l") -> str:
    gap = max(0, width - display_width(s))
    return " " * gap + s if align == "r" else (s + " " * gap)


def truncate(s: str, width: int) -> str:
    """칸 수 안으로 자르고 넘치면 끝에 ... 을 붙인다."""
    if display_width(s) <= width:
        return s
    keep = max(0, width - len(ELLIPSIS))
    out, w = "", 0
    for ch in s:
        cw = display_width(ch)
        if w + cw > keep:
            break
        out, w = out + ch, w + cw
    return out + ELLIPSIS


def wrap(text: str, width: int) -> List[str]:
    """칸 수에 맞춰 줄바꿈 (띄어쓰기 기준, 한 단어가 너무 길면 글자 단위로 자른다). 이미 있는 줄바꿈은 유지."""
    width = max(width, 2)
    out: List[str] = []
    for para in str(text if text is not None else "").split("\n"):
        line = ""
        for word in para.split(" "):
            while display_width(word) > width:                     # 칸보다 긴 단어(주소 등)는 글자 단위로
                room = width - (display_width(line) + 1 if line else 0)
                if room < 2:
                    out.append(line)
                    line, room = "", width
                head, w = "", 0
                for ch in word:
                    if w + display_width(ch) > room:
                        break
                    head, w = head + ch, w + display_width(ch)
                out.append((line + " " if line else "") + head)
                line, word = "", word[len(head):]
            if not line:
                line = word
            elif display_width(line) + 1 + display_width(word) <= width:
                line += " " + word
            else:
                out.append(line)
                line = word
        out.append(line)
    return out or [""]


def _fit_widths(natural: List[int], max_width: int, flex: Sequence[bool], mins: Sequence[int], per_col_overhead: int) -> List[int]:
    widths = list(natural)
    total = lambda: sum(widths) + per_col_overhead * len(widths) + 1
    while total() > max_width:
        cand = [i for i, w in enumerate(widths) if flex[i] and w > mins[i]]
        if not cand:
            break
        i = max(cand, key=lambda k: widths[k])
        widths[i] -= 1
    return widths


def render_table(headers: Sequence[str], rows: Sequence[Sequence[str]], max_width: int = 120, style: str = "ascii",
                 flex: Optional[Sequence[bool]] = None, align: Optional[Sequence[str]] = None, min_width: int = 6) -> str:
    """표 문자열. rows 의 칸에는 줄바꿈('\\n')이 들어 있어도 된다. flex=False 인 칸(예: 주소)은 줄이거나 자르지 않는다."""
    n = len(headers)
    rows = [[str(c) if c is not None else "" for c in r] + [""] * (n - len(r)) for r in rows]
    align = list(align or ["l"] * n)
    if style == "md":
        esc = lambda c: c.replace("|", "\\|").replace("\n", "<br>")
        L = ["| " + " | ".join(esc(h) for h in headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
        L += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
        return "\n".join(L)
    flex = list(flex or [True] * n)
    natural = [max([display_width(h)] + [display_width(line) for r in rows for line in r[i].split("\n")]) for i, h in enumerate(headers)]
    mins = [max(min_width, display_width(h)) for h in headers]                    # 머리글은 줄바꿈하지 않으므로 머리글 글자 수 아래로는 줄이지 않는다
    widths = _fit_widths(natural, max_width, flex, mins, 3)
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    head_sep = "+" + "+".join("=" * (w + 2) for w in widths) + "+"
    wrapped = [[wrap(c, widths[i]) if flex[i] else c.split("\n") for i, c in enumerate(r)] for r in rows]
    multi = any(len(cell) > 1 for r in wrapped for cell in r)
    L = [sep, "| " + " | ".join(pad(h, widths[i]) for i, h in enumerate(headers)) + " |", head_sep]
    for r in wrapped:
        for k in range(max(len(cell) for cell in r)):
            L.append("| " + " | ".join(pad(cell[k] if k < len(cell) else "", widths[i], align[i]) for i, cell in enumerate(r)) + " |")
        if multi:
            L.append(sep)
    if not multi:
        L.append(sep)
    return "\n".join(L)
