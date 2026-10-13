# -*- coding: utf-8 -*-
"""[COMMON] DOCX -> 텍스트 (python-docx). 문단과 표를 문서 순서대로 출력."""
from __future__ import annotations

import re

from .results import ExtractError, ExtractResult


def extract(path, settings=None, **_) -> ExtractResult:
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError:
        raise ExtractError("python-docx 미설치")
    try:
        doc = Document(str(path))
    except Exception as e:
        raise ExtractError("DOCX 열기 실패: {}".format(e))

    lines = []
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            t = Paragraph(child, doc).text.strip()
            if t:
                lines.append(t)
        elif tag == "tbl":
            rows = []
            for row in Table(child, doc).rows:
                seen, cells = set(), []
                for c in row.cells:
                    if id(c._tc) in seen:              # 가로 병합 셀 중복 제거
                        continue
                    seen.add(id(c._tc))
                    txt = re.sub(r"\s*\n\s*", " / ", c.text.strip())
                    if txt:
                        cells.append(txt)
                if cells:
                    rows.append(" | ".join(cells))
            if rows:
                lines.append("[표]\n" + "\n".join(rows) + "\n[/표]")
    text = "\n".join(lines).strip()
    if not text:
        raise ExtractError("DOCX에서 추출된 텍스트 없음")
    return ExtractResult(text=text, method="TEXT")
