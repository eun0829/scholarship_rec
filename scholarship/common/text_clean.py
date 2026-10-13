# -*- coding: utf-8 -*-
"""[COMMON] 본문 HTML -> 텍스트. 표는 '행 | 셀' 형태로 구조를 살린다.
(샘플 검증: 42,942자 -> 1,606자, 신청기간 줄바꿈 끊김 없음)"""
from __future__ import annotations

import html
import re
from typing import List, Tuple

from bs4 import BeautifulSoup

BLOCK = ["p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table"]


def _norm(s: str) -> str:
    s = s.replace("\xa0", " ")
    return re.sub(r"[ \t\r\f\v]+", " ", s)


def clean_html(raw: str) -> Tuple[str, List[str]]:
    """(정리된 텍스트, 이미지 src 목록)"""
    raw = (raw or "").strip().strip('"')
    soup = BeautifulSoup(raw, "html.parser")
    for t in soup(["script", "style"]):
        t.decompose()
    imgs = [i.get("src") for i in soup.find_all("img") if i.get("src")]

    for s in soup.find_all(["s", "del", "strike"]):       # 취소선은 표시를 남김
        s.insert_before("[취소선:")
        s.insert_after("]")
    for br in soup.find_all("br"):
        br.replace_with("\n")

    for tbl in soup.find_all("table"):
        lines = []
        for tr in tbl.find_all("tr"):
            cells = []
            for td in tr.find_all(["td", "th"]):
                for b in td.find_all(["p", "div", "li"]):
                    b.insert_after("\n")
                txt = _norm(td.get_text(""))
                txt = re.sub(r"\n\s*", " / ", txt.strip())
                if txt:
                    cells.append(txt)
            if cells:
                lines.append(" | ".join(cells))
        tbl.replace_with("\n[표]\n" + "\n".join(lines) + "\n[/표]\n")

    for b in soup.find_all(BLOCK):
        b.insert_after("\n")

    text = _norm(soup.get_text(""))
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return html.unescape(text), imgs


def normalize_title(title: str) -> str:
    """제목 비교용 정리: HTML 엔티티 해제, 연속 공백 -> 한 칸, 앞뒤 공백 제거."""
    return re.sub(r"\s+", " ", html.unescape(title or "")).strip()
