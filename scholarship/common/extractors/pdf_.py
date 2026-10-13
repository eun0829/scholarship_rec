# -*- coding: utf-8 -*-
"""[COMMON] PDF -> 텍스트 (PyMuPDF). 페이지당 글자 수가 적으면 스캔형으로 보고 이미지로 전환."""
from __future__ import annotations

import re

from .results import ExtractError, ExtractResult, ImagePart

try:
    import pymupdf as fitz
except ImportError:                       # 구버전 PyMuPDF
    import fitz  # type: ignore


def extract(path, settings, label: str = "pdf", **_) -> ExtractResult:
    try:
        doc = fitz.open(str(path))
    except Exception as e:
        raise ExtractError("PDF 열기 실패: {}".format(e))
    try:
        return _extract(doc, settings, label)
    finally:
        doc.close()                      # Windows 에서는 열린 파일을 삭제/이동할 수 없으므로 반드시 닫는다


def _extract(doc, settings, label: str) -> ExtractResult:
    if doc.needs_pass:
        raise ExtractError("암호가 걸린 PDF")
    n = doc.page_count
    texts = []
    for page in doc:
        try:
            texts.append(page.get_text("text", sort=True))
        except TypeError:
            texts.append(page.get_text("text"))
    nonspace = sum(len(re.sub(r"\s", "", t)) for t in texts)
    joined = "\n\n".join("[p.{}]\n{}".format(i + 1, t.strip()) for i, t in enumerate(texts) if t.strip())

    if n == 0:
        raise ExtractError("빈 PDF")
    if nonspace / float(n) >= settings.pdf_min_chars_per_page:
        return ExtractResult(text=joined, method="TEXT", pages=n)

    # 스캔형: 앞쪽 페이지를 이미지로 변환해 AI(멀티모달)에 전달
    images = []
    limit = min(n, settings.max_pdf_pages_vision)
    for i in range(limit):
        pix = doc[i].get_pixmap(dpi=130)
        images.append(ImagePart("image/png", pix.tobytes("png"), "{} p.{}".format(label, i + 1)))
    warns = ["스캔형 PDF로 판단 -> 이미지 {}장 전달".format(limit)]
    if n > limit:
        warns.append("전체 {}쪽 중 앞 {}쪽만 전달".format(n, limit))
    return ExtractResult(text=joined, method="VISION", images=images, pages=n, warnings=warns)
