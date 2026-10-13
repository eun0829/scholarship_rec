# -*- coding: utf-8 -*-
"""[COMMON] 첨부파일명 분류 (요강 / 양식 / 보조 / 제외 / 미분류).

규칙 (계획서 기준)
  1) 제외 키워드(명단, 합격자 ...)가 있고 '강한 요강 키워드'(공고, 요강, 모집)가 없으면 EXCLUDED
  2) 강한 요강 키워드가 있으면 GUIDE  (예: '공고문 및 지원서' -> GUIDE)
  3) 그 외에는 가장 긴 키워드가 일치한 분류를 채택 (동률이면 GUIDE > FORM > AUX)
  4) 아무것도 안 걸리면 UNKNOWN  (분석 대상에 포함)
키워드는 config/file_keywords.json 에서 관리한다.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Tuple

GUIDE, FORM, AUX, EXCLUDED, UNKNOWN = "GUIDE", "FORM", "AUX", "EXCLUDED", "UNKNOWN"
PRIMARY_ROLES = (GUIDE, UNKNOWN)      # 자격요건 근거로 우선 사용
SECONDARY_ROLES = (FORM, AUX)         # 참고용 (낮은 우선순위)

DEFAULT_KEYWORDS = {
    "exclude": ["명단", "합격자", "선정자", "결과발표", "선발결과", "결과"],
    "strong_guide": ["공고", "요강", "모집"],
    "guide": ["안내", "계획", "시행", "포스터", "리플릿", "리플렛", "공문", "선발", "개요"],
    "form": ["지원서", "신청서", "서식", "양식", "동의서", "추천서", "서약서", "자기소개서", "확인서", "별지", "영수증", "이력서", "계획서", "원서"],
    "aux": ["매뉴얼", "작성요령", "작성방법", "참고", "예시", "faq", "문의"],
}


def load_keywords(path: Path = None) -> Dict[str, List[str]]:
    kw = {k: list(v) for k, v in DEFAULT_KEYWORDS.items()}
    if path and Path(path).is_file():
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for k in kw:
            if isinstance(data.get(k), list):
                kw[k] = [str(x) for x in data[k]]
    return kw


def normalize_name(name: str) -> str:
    n = unicodedata.normalize("NFKC", name or "")
    n = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", n)          # 확장자 제거
    n = re.sub(r"[\s_\-\.\(\)\[\]{}·,]+", "", n)        # 공백/특수문자 제거
    return n.lower()


def classify(filename: str, kw: Dict[str, List[str]]) -> Tuple[str, str]:
    """(분류, 근거 키워드)"""
    n = normalize_name(filename)
    hit = lambda words: [w for w in words if w.lower() in n]

    strong = hit(kw["strong_guide"])
    excl = hit(kw["exclude"])
    if excl and not strong:
        return EXCLUDED, excl[0]
    if strong:
        return GUIDE, strong[0]

    candidates = []
    for role, key, rank in ((GUIDE, "guide", 0), (FORM, "form", 1), (AUX, "aux", 2)):
        for w in hit(kw[key]):
            candidates.append((len(w), -rank, role, w))
    if candidates:
        candidates.sort(reverse=True)           # 긴 키워드 우선, 동률이면 rank가 작은(GUIDE) 쪽
        _, _, role, w = candidates[0]
        return role, w
    return UNKNOWN, ""
