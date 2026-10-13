# -*- coding: utf-8 -*-
"""[COMMON] 형식별 첨부 추출기 디스패처.

pip 로 설치되는 패키지만 사용한다 (pyhwp, PyMuPDF, python-docx, openpyxl, Pillow).
구형 .doc/.xls/.ppt 는 LibreOffice 가 필요하므로 지원하지 않는다 (SKIPPED 로 기록).
"""
from __future__ import annotations

from pathlib import Path

from .results import ExtractError, ExtractResult, ImagePart   # noqa: F401  (재노출)

IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}
SUPPORTED_EXT = {"hwp", "hwpx", "pdf", "docx", "xlsx", "zip"} | IMAGE_EXT
UNSUPPORTED_HINT = {
    "doc": "구형 DOC는 미지원 (DOCX/PDF 로 변환 필요)",
    "xls": "구형 XLS는 미지원",
    "ppt": "PPT는 미지원", "pptx": "PPTX는 미지원",
    "hwt": "HWT는 미지원", "txt": None,
}


def extract(path, ext: str, settings, kw=None, depth: int = 0, label: str = "") -> ExtractResult:
    ext = (ext or "").lower()
    label = label or Path(str(path)).name
    if ext == "hwp":
        from . import hwp
        return hwp.extract(path, settings)
    if ext == "hwpx":
        from . import hwpx
        return hwpx.extract(path, settings)
    if ext == "pdf":
        from . import pdf_
        return pdf_.extract(path, settings, label=label)
    if ext == "docx":
        from . import docx_
        return docx_.extract(path, settings)
    if ext == "xlsx":
        from . import xlsx_
        return xlsx_.extract(path, settings)
    if ext in IMAGE_EXT:
        from . import image_
        return image_.extract(path, settings, label=label)
    if ext == "zip":
        from . import zip_
        return zip_.extract(path, settings, kw=kw, depth=depth, label=label)
    if ext == "txt":
        for enc in ("utf-8", "cp949"):
            try:
                return ExtractResult(text=Path(str(path)).read_text(encoding=enc).strip(), method="TEXT")
            except UnicodeDecodeError:
                continue
        raise ExtractError("TXT 인코딩 해석 실패")
    raise ExtractError(UNSUPPORTED_HINT.get(ext) or "지원하지 않는 형식: .{}".format(ext))
