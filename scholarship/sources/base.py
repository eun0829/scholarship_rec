# -*- coding: utf-8 -*-
"""[COMMON] 소스 어댑터 인터페이스.

외부01(학교 DB), 외부02(드림스폰 크롤링)는 '가져오는 방법'만 다르다.
어댑터는 공통 입력 형식(SourceNotice)을 만들어 주고, 파일 다운로드 방법만 책임진다.
정리/추출/AI/저장은 모두 common 파이프라인이 처리한다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List

from ..common.models import SourceAttachment, SourceNotice


class SourceAdapter(ABC):
    src_cd: str = ""
    full_scope: bool = False          # True: 전체 목록을 매번 본다 -> 사라진 글 정리를 게시일 범위가 아니라 전체 활성 글 기준으로 함
    deactivation_safe: bool = True    # False: 이번 수집이 비정상(목록 0건, 오류 등)이라 사라진 글 정리를 건너뜀

    @abstractmethod
    def fetch_notices(self, days: int) -> List[SourceNotice]:
        """최근 days 일 이내 공고(본문+첨부 목록)를 최신순으로 돌려준다."""

    @abstractmethod
    def download_attachment(self, att: SourceAttachment, dest: Path, max_bytes: int) -> int:
        """첨부를 dest 에 저장하고 바이트 수를 돌려준다. 실패 시 예외."""

    @abstractmethod
    def download_body_image(self, src: str, dest: Path, max_bytes: int) -> int:
        """본문 이미지(src 는 본문에 적힌 값)를 dest 에 저장."""

    def close(self) -> None:
        pass
