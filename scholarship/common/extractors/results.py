# -*- coding: utf-8 -*-
"""[COMMON] 추출기 공통 타입."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


class ExtractError(Exception):
    """추출 실패 (암호/배포용 문서, 미지원 형식, 손상 파일 등). 메시지는 DB ERR_MSG 에 저장된다."""


@dataclass
class ImagePart:
    media_type: str          # image/jpeg, image/png ...
    data: bytes
    label: str = ""          # AI 입력에서 이미지를 구분하는 이름


@dataclass
class ExtractResult:
    text: str = ""
    method: str = "TEXT"                     # TEXT / VISION / NONE
    images: List[ImagePart] = field(default_factory=list)
    pages: Optional[int] = None
    warnings: List[str] = field(default_factory=list)
    partial: bool = False                    # 일부 내용만 추출됨 (예: HWP 변환 실패 후 미리보기 텍스트만 확보)
