# -*- coding: utf-8 -*-
"""[COMMON] HWPX(zip+XML) -> 텍스트. 표준 라이브러리 + lxml 만 사용.

※ 실제 HWPX 샘플로 검증되지 않았음 (구조는 공개 규격 기준). 샘플을 받으면 반드시 대조할 것.
"""
from __future__ import annotations

import re
import zipfile
from typing import List

from lxml import etree

from .results import ExtractError, ExtractResult


def _ln(el) -> str:
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


def _render_p(p) -> str:
    out: List[str] = []

    def walk(el):
        for ch in el:
            n = _ln(ch)
            if n == "t":
                out.append("".join(ch.itertext()))
            elif n == "tbl":
                out.append("\n" + _render_tbl(ch) + "\n")
            elif n == "p":                              # 글상자(drawText/subList) 안의 문단
                out.append("\n" + _render_p(ch))
            elif n == "lineBreak":
                out.append("\n")
            else:
                walk(ch)
    walk(p)
    return "".join(out)


def _render_tbl(tbl) -> str:
    rows = []
    for tr in tbl:
        if _ln(tr) != "tr":
            continue
        cells = []
        for tc in tr:
            if _ln(tc) != "tc":
                continue
            parts = []
            for sub in tc:
                if _ln(sub) == "subList":
                    parts += [_render_p(x).strip() for x in sub if _ln(x) == "p"]
            txt = re.sub(r"[ \t]+", " ", " / ".join(x for x in parts if x)).strip()
            if txt:
                cells.append(txt)
        if cells:
            rows.append(" | ".join(cells))
    return "[표]\n" + "\n".join(rows) + "\n[/표]"


def extract(path, settings=None, **_) -> ExtractResult:
    try:
        with zipfile.ZipFile(str(path)) as zf:
            secs = sorted((n for n in zf.namelist() if re.match(r"Contents/section\d+\.xml$", n)),
                          key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[-1]).group(1)))
            blobs = [zf.read(n) for n in secs]
    except zipfile.BadZipFile:
        raise ExtractError("HWPX 압축 구조 손상")
    if not blobs:
        raise ExtractError("HWPX 본문(section) 없음 (암호/배포용 문서일 수 있음)")
    lines = []
    for blob in blobs:
        root = etree.fromstring(blob)
        for p in root:
            if _ln(p) == "p":
                t = _render_p(p)
                if t.strip():
                    lines.append(t.rstrip())
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if not text:
        raise ExtractError("HWPX에서 추출된 텍스트 없음")
    return ExtractResult(text=text, method="TEXT")
