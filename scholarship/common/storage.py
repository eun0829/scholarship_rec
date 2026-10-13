# -*- coding: utf-8 -*-
"""[COMMON] NAS 저장 경로 규칙. 원본 파일명은 경로에 쓰지 않고 번호만 사용한다."""
from __future__ import annotations

import re
from pathlib import Path


def safe_token(value: str) -> str:
    """경로 구성 요소로 안전한 문자만 남긴다 (경로 탈출 방지)."""
    return re.sub(r"[^0-9A-Za-z_\-]", "_", str(value or ""))[:60] or "_"


def file_ext(filename: str) -> str:
    m = re.search(r"\.([A-Za-z0-9]{1,5})\s*$", filename or "")
    return m.group(1).lower() if m else ""


def notice_dir(nas_root: Path, src_cd: str, src_id: str) -> Path:
    # 예: /info/ai_bundle/scholarship/ext01/153666
    return Path(nas_root) / safe_token(src_cd).lower() / safe_token(src_id)


def attachment_path(nas_root: Path, src_cd: str, src_id: str, file_seq: str, ext: str) -> Path:
    # 예: .../ext01/153666/3021.hwp
    return notice_dir(nas_root, src_cd, src_id) / "{}.{}".format(safe_token(file_seq), safe_token(ext).lower())


def body_image_path(nas_root: Path, src_cd: str, src_id: str, index: int, ext: str) -> Path:
    # 예: .../ext01/153666/body_img_1.jpg
    return notice_dir(nas_root, src_cd, src_id) / "body_img_{}.{}".format(index, safe_token(ext).lower() or "img")
