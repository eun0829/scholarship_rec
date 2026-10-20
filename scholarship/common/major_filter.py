# -*- coding: utf-8 -*-
"""[COLLECT] 계열이 공학과 무관한 공고(농업, 예체능)를 AI 추출 전에 건너뛰기 위한 판정.

이 대학은 공학 중심이라 농업·예체능 계열 학생만 대상인 공고는 추천 대상이 아니다. 오판으로 자격 있는 공고를 숨기지 않도록 보수적으로 판정한다.
- 제목에 그 계열이 '계열·계·전공·학과·학부·대학' 같은 말과 함께 있거나, '예체능'처럼 계열 이름 자체인 말이 있으면 건너뛴다.
- 본문은 앞부분(body_chars)만 보고, 그 계열 학생만 대상이라는 문장('대상·한정·전용')이 있고 '우대·가능·무관' 같은 완화 표현이 없을 때만 건너뛴다.
- 디자인·공학·전공 무관처럼 공학 쪽 학생도 해당한다는 말이 제목이나 본문 앞부분에 하나라도 있으면 건너뛰지 않는다.
키워드는 config/major_exclude.json 에서 고친다 (코드 수정 불필요).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_RULES: Dict[str, Any] = {
    "categories": {"농업": ["농업", "농림", "농학", "농생명", "수산", "축산", "임업", "원예", "영농"],
                   "예체능": ["예체능", "예술", "음악", "미술", "체육", "무용", "연극", "국악", "영화", "스포츠", "만화"]},
    "keep": ["디자인", "공학", "이공", "자연과학", "전공 무관", "전공무관", "전 계열", "전계열", "계열 무관", "계열무관", "전공 제한 없", "전공제한없", "모든 전공", "전 학과"],
    "exclusive_cues": ["대상", "한정", "전용", "해당자", "에 한함", "에한함", "만 지원", "만 신청"],
    "soft_cues": ["우대", "가산", "가능", "무관", "포함", "등도", "우선"],
    "body_chars": 800,
    "strict_categories": ["농업"],
    "strict_keep": ["전공 무관", "전공무관", "전 계열", "전계열", "계열 무관", "계열무관", "전공 제한 없", "전공제한없", "모든 전공", "전 학과", "공학 및", "공학및", "공학·", "공학계열", "이공계", "이공"],
    "strict_ignore": ["농어촌", "농어업인", "농업인", "농촌", "어업인", "농업협동조합", "농협"],
}
_FIELD_SUFFIX = r"(?:\s*관련)?\s*(?:계열|계\b|계에|전공|학과|학부|대학|분야\s*전공)"
_SELF_NAMED = {"예체능"}                                            # 말 자체가 계열 이름이라 접미어가 필요 없는 말


def load_rules(path: Optional[Path] = None) -> Dict[str, Any]:
    rules = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v) for k, v in DEFAULT_RULES.items()}
    if path and Path(path).is_file():
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for k in rules:
            if k in data and isinstance(data[k], type(rules[k])):
                rules[k] = data[k]
    return rules


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _has_any(text: str, words: List[str]) -> bool:
    text = re.sub(r"전공(?=[가-힣])", "전공 ", text or "")            # '전공학생' 의 '공학' 을 공학 전공으로 오해하지 않게 띄운다
    return any(w and w in text for w in words)


def _match(text: str, words: List[str]) -> Optional[str]:
    """text 에서 계열 말 + 계열 접미어 (또는 계열 이름 자체)가 있으면 그 말."""
    for w in words:
        if not w:
            continue
        if w in _SELF_NAMED and w in text:
            return w
        if re.search(re.escape(w) + _FIELD_SUFFIX, text):
            return w
    return None


def _drop(text: str, r: Dict[str, Any]) -> str:
    """신분·지역 표현(농어촌, 농업인 자녀, 농협 등)은 계열이 아니므로 판정 전에 지운다."""
    for w in sorted(r.get("strict_ignore") or [], key=len, reverse=True):
        text = text.replace(w, " ")
    return text


def skip_reason(title: str, body: str, rules: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """건너뛸 이유 (예: '농업 계열 전용'). 건너뛰지 않으면 None."""
    r = rules or DEFAULT_RULES
    head = (body or "")[: int(r.get("body_chars") or 800)]
    cats: Dict[str, List[str]] = r.get("categories") or {}
    for name in r.get("strict_categories") or []:                       # 확실하게 제외하는 계열: 우대 표현이나 공학 말이 있어도 제외 (전공 무관 표현이 있을 때만 남김)
        words = cats.get(name) or []
        t_title, t_head = _drop(title or "", r), _drop(head, r)
        if _has_any(t_title + "\n" + t_head, r.get("strict_keep") or []):
            continue
        if any(w in t_title for w in words):
            return "{} 계열 (제목)".format(name)
        hits = {w for w in words if w in t_head}
        if _match(t_head, words) or len(hits) >= 2:
            return "{} 계열 (본문)".format(name)
    if _has_any((title or "") + "\n" + head, r.get("keep") or []):
        return None
    for name, words in cats.items():
        if name in (r.get("strict_categories") or []):
            continue
        if _match(title or "", words):
            return "{} 계열 (제목)".format(name)
    sentences = [s for s in re.split(r"[\n.。]+", head) if s.strip()]
    for name, words in cats.items():
        if name in (r.get("strict_categories") or []):
            continue
        for s in sentences:
            if not _match(s, words):
                continue
            if _has_any(s, r.get("exclusive_cues") or []) and not _has_any(s, r.get("soft_cues") or []):
                return "{} 계열 전용 (본문)".format(name)
    return None
