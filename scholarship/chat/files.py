# -*- coding: utf-8 -*-
"""[CHAT] 출처의 원본 첨부파일 내려주기. 경로는 서버 안에서만 알고, 응답에는 파일 번호·이름·크기만 싣는다.

- DB 에 연결된 파일(공고 + 파일번호)만, NAS 기준 폴더 안에 있는 실제 파일만 내려준다 ('..' 이동·기준 폴더 밖 경로·심볼릭 링크 우회는 거부).
- ZIP 안의 개별 파일(경로에 '!' 가 있는 것)은 내려주지 않는다.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

MAX_BYTES = 100 * 1024 * 1024
NOTICE_ID = re.compile(r"^[A-Za-z0-9]{1,10}:[A-Za-z0-9_-]{1,30}$")
FILE_SEQ = re.compile(r"^[A-Za-z0-9._-]{1,30}$")


def safe_path(nas_root: Path, nas_path: Optional[str]) -> Optional[Path]:
    """내려줘도 되는 실제 파일이면 절대 경로, 아니면 None."""
    if not nas_path or "!" in nas_path or "\x00" in nas_path:
        return None
    try:
        root = Path(nas_root).resolve()
        p = Path(nas_path).resolve()
        if root != p and root not in p.parents:
            return None
        if not p.is_file() or p.stat().st_size > MAX_BYTES:
            return None
        return p
    except (OSError, ValueError):
        return None


def why_not(nas_root: Path, nas_path: Optional[str]) -> str:
    """내려줄 수 없는 이유 (점검용 짧은 말. 경로 자체는 담지 않는다)."""
    if not nas_path:
        return "DB에 파일 경로(NAS_PATH)가 없음"
    if "!" in nas_path:
        return "ZIP 안의 파일이라 개별 다운로드 불가"
    try:
        root, p = Path(nas_root).resolve(), Path(nas_path).resolve()
    except (OSError, ValueError):
        return "경로를 읽을 수 없음"
    if root != p and root not in p.parents:
        return "DB 경로가 NAS 기준 폴더(SCHOLARSHIP_NAS_ROOT) 밖임"
    if not p.is_file():
        return "기준 폴더 안이지만 파일이 없음"
    return "파일이 너무 큼"


def public_file(nas_root: Path, f: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """응답에 싣는 파일 정보 (경로는 싣지 않는다). 내려줄 수 없으면 None."""
    p = safe_path(nas_root, f.get("nas_path"))
    if p is None:
        return None
    name = (f.get("name") or p.name).rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    ext = (f.get("ext") or p.suffix.lstrip(".") or "").lower()
    return {"seq": str(f.get("seq")), "name": name, "ext": ext, "size": p.stat().st_size}
