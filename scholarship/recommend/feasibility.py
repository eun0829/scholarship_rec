# -*- coding: utf-8 -*-
"""[RECOMMEND] 조건부 공고의 '실현 가능성' 보조 라벨 (규칙 파일 + AI 보조). 판정(충족·조건부·미달)에는 관여하지 않는다.

  NORMAL    (보통)       : 흔한 어학시험, 서류·절차처럼 노력하면 채울 수 있는 것
  HARD      (어려움)     : 흔치 않은 외국어, 특수 신분처럼 학생이 바꾸기 어렵거나 대부분이 갖지 않은 것
  VERY_HARD (매우 어려움): 학교·학생 계열과 다른 계열 학생이 대상인 공고 (공학계 학교의 농업계 공고 등)
규칙은 config/feasibility_rules.json 에 있고(코드가 아니라 파일), AI 는 규칙이 정하지 못한 서술형 조건만 맡는다.
정렬용 점수는 낮을수록 쉽다. 규칙이 정한 값은 AI 가 덮어쓰지 않는다.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .feasibility_rules import Rules
from .present import CONFIRM, EXCLUDE, split_message

NORMAL, HARD, VERY_HARD = "NORMAL", "HARD", "VERY_HARD"
LEVEL_RANK = {NORMAL: 0, HARD: 1, VERY_HARD: 2}


def _clean(msg: str) -> str:
    return re.sub(r"^\s*\[[^\]]{1,30}\]\s*", "", msg or "").strip()


def _has(text_up: str, words: Iterable[str]) -> bool:
    return any(w.upper() in text_up for w in words)


def majors_for(rules: Rules, student_major: Optional[str] = None, override: Sequence[str] = ()) -> Tuple[str, ...]:
    """계열 비교 기준: 학생 계열이 있으면 학생 값, 없으면 환경변수(RECOMMEND_SCHOOL_MAJORS), 없으면 규칙 파일의 학교 계열."""
    if student_major:
        return (student_major,)
    return tuple(override) or rules.school_majors


def classify_domain(text: str, rules: Rules, majors: Sequence[str]) -> Optional[Tuple[str, str]]:
    """다른 계열 학생이 대상인 문장이면 (등급, 이유). 계열 기준이 없거나 단순 언급이면 None."""
    if not majors:
        return None
    up = text.upper()
    if not _has(up, rules.target_words):
        return None
    for code, fam in rules.families.items():
        if code in majors:
            continue
        if _has(up, fam["keywords"]):
            mine = "·".join(rules.major_label(m) for m in majors)
            return rules.mismatch_level, "{} 대상 공고라 {} 학생에게는 맞지 않음".format(fam["label"], mine)
    return None


def classify_text(msg: str, rules: Rules, majors: Sequence[str] = ()) -> Optional[Tuple[str, str]]:
    """확인 항목 문구 하나의 등급. 규칙으로 정하지 못하면 None (AI 보조 대상)."""
    kind, body = split_message(msg)
    body = _clean(body)
    if kind == EXCLUDE:
        return NORMAL, "본인이 해당하는지만 확인하면 됨"
    r = classify_domain(body, rules, majors)
    if r:
        return r
    up = body.upper()
    for rule in rules.rules:
        if _has(up, rule.include) and not _has(up, rule.exclude):
            return rule.level, rule.reason
    return None


def assess_texts(texts: Iterable[str], rules: Rules, majors: Sequence[str] = (), ai: Optional[Dict[str, Tuple[str, str]]] = None) -> Tuple[str, str, str]:
    """문구 목록 -> (등급, 이유, 출처 'rule'|'ai'|'default'). 가장 어려운 항목이 건의 등급이 된다."""
    ai = ai or {}
    best: Optional[Tuple[int, str, str, str]] = None
    for m in texts:
        r, src = classify_text(m, rules, majors), "rule"
        if r is None:
            r, src = ai.get(_clean(split_message(m)[1])), "ai"
        if r is None:
            r, src = (rules.default_level, "확인할 조건이 있음"), "default"
        cand = (LEVEL_RANK[r[0]], r[0], r[1], src)
        if best is None or cand[0] > best[0]:
            best = cand
    if best is None:
        return NORMAL, "추가로 확인할 조건이 없음", "rule"
    return best[1], best[2], best[3]


def item_texts(it: Dict[str, Any]) -> List[str]:
    """한 조건부 건에서 학생이 확인해야 하는 문구: 공고 공통 + 가장 확인이 적은 유형."""
    texts = list(it.get("to_confirm") or [])
    vs = it.get("matched_variants") or []
    if vs:
        best = min(vs, key=lambda v: len(v.get("to_confirm") or []))
        texts += list(best.get("to_confirm") or [])
    return texts


def unresolved(items: Iterable[Dict[str, Any]], rules: Rules, majors: Sequence[str] = ()) -> List[str]:
    """규칙으로 등급을 못 정한 '확인 필요' 문구 (AI 보조 대상, 중복 제거). 학생 값이 없는 CONFIRM 문구만."""
    out: List[str] = []
    seen = set()
    for it in items:
        for m in item_texts(it):
            kind, body = split_message(m)
            body = _clean(body)
            if kind == CONFIRM and body and body not in seen and classify_text(m, rules, majors) is None:
                seen.add(body)
                out.append(body)
    return out


def feasibility_of(it: Dict[str, Any], rules: Rules, majors: Sequence[str] = (), ai: Optional[Dict[str, Tuple[str, str]]] = None) -> Dict[str, Any]:
    texts = item_texts(it)
    level, reason, source = assess_texts(texts, rules, majors, ai)
    # 공고 제목·요약에 다른 계열 대상 표현이 있는 경우 (확인 문구에는 안 나오는 서술)
    for extra in (it.get("title"), it.get("summary")):
        r = classify_domain(str(extra or ""), rules, majors)
        if r and LEVEL_RANK[r[0]] > LEVEL_RANK[level]:
            level, reason, source = r[0], r[1], "rule"
    hard = sum(1 for m in texts if (classify_text(m, rules, majors) or (NORMAL,))[0] != NORMAL)
    return {"level": level, "label": rules.label(level), "reason": reason, "source": source, "_sort": (LEVEL_RANK[level], hard, len(texts))}
