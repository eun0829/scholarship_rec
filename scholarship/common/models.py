# -*- coding: utf-8 -*-
"""[COMMON] 소스(외부01/외부02/...)가 파이프라인에 넘기는 공통 입력 형식."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional


@dataclass
class SourceAttachment:
    file_seq: str            # 원본 첨부번호 (EXT01: BBS_ATCHMNFL_SEQ)
    sort_no: int
    org_file_nm: str         # 원본 파일명
    download_url: str = ""   # 소스가 다운로드 방법을 따로 가지면 비워도 됨
    extra: Dict[str, str] = field(default_factory=dict)   # 소스 전용 다운로드 정보 (예: EXT02 POST 파라미터). 저장하지 않음


@dataclass
class SourceNotice:
    src_cd: str                              # EXT01 / EXT02
    src_id: str                              # 원본 글번호
    title: str
    dept_nm: str = ""
    posted_date: Optional[date] = None
    origin_url: str = ""                     # 학생에게 보여줄 원문 링크
    body_html: str = ""                      # 본문 (HTML 또는 일반 텍스트)
    attachments: List[SourceAttachment] = field(default_factory=list)
    # ---- 소스가 구조화해서 넘기는 값 (선택). EXT01 은 사용하지 않는다.
    source_fields: Optional[Dict[str, Any]] = None   # 출처가 제공한 구조화 항목 -> SRC_FIELDS_JSON (AI 해석과 분리해 저장)
    hints: Optional[Dict[str, Any]] = None           # 코드가 확정한 값 (마감일, 인원, 학교급 ...) -> AI 결과와 교차 검증
    hash_basis: Optional[str] = None                 # 변경 감지 해시에 쓸 문자열 (본문 대신). 조회수/D-day 같은 변동 값 제외용
    source_note: str = ""                            # AI 입력에 붙일 출처 설명
    stub: bool = False                               # True: 이미 저장된 글이라 상세를 조회하지 않음 (목록에서 확인만)
