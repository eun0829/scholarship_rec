# -*- coding: utf-8 -*-
"""[RECOMMEND] 5-B 후속 질문: 판정에서 '확인 필요'로 남은 항목을 학생에게 무엇을 물으면 풀 수 있는지 정리한다.

- 판정 항목의 needs(5-A)에서 이미 답했거나 건너뛴 항목을 걸러 남은 입력 항목을 모은다.
- 학생이 직접 답할 항목(source=student)은 질문으로, 학교 DB 항목(source=school_db)이 비어 있으면 질문하지 않고 data_gap 으로 알린다.
- 어떤 입력으로도 풀리지 않는 조건(공고의 문장 조건 등)은 document_only 로 '공고문에서 직접 확인할 것'을 보여 준다.
- 질문 순서: 일반 항목이 먼저, 민감 항목(소득·신분)은 마지막. 같은 그룹에서는 더 많은 공고를 풀어 주는 항목, 그 항목 하나로 공고의 남은 확인이 풀리는 항목, 사전 순서.
- 한 번에 최대 max_questions 개, 라운드는 최대 max_rounds 회. 서버는 상태를 저장하지 않으므로 호출하는 쪽이 round, declined 를 보내 준다.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .profile import StudentProfile
from .student_text import bucket_bounds

MAX_QUESTIONS = 5
MAX_ROUNDS = 3
DONE_NO_QUESTIONS, DONE_ALL_DECLINED, DONE_MAX_ROUNDS = "NO_QUESTIONS", "ALL_DECLINED", "MAX_ROUNDS"
UNKNOWN = "UNKNOWN"


def is_answered(item: Any, p: StudentProfile) -> bool:
    """학생이 이 항목에 이미 답했는가 (답하지 않음은 호출하는 쪽이 declined 로 알려 준다)."""
    if item.code == "SPECIAL":
        return bool(p.special_complete)                           # 신분 항목에 모두 답해야 '해당 없음'을 단정할 수 있다
    if item.code == "RESY":
        return p.residence_years is not None
    keys = [k for k in (item.profile_field or "").split("/") if k]
    return bool(keys) and all(getattr(p, k, None) is not None for k in keys)


def answer_spec(item: Any, registry: Any) -> Dict[str, Any]:
    """포털이 답을 JSON 으로 보낼 때 쓰는 키와 선택지."""
    keys = [k for k in (item.profile_field or "").split("/") if k]
    spec: Dict[str, Any] = {"keys": keys, "text": "- {}: 값".format(item.label)}
    if item.type == "place":
        spec.update(format="시·도와 시·군·구를 각각", example={keys[0]: "경기도", keys[1]: "수원시"} if len(keys) > 1 else None,
                    text="- {}: 경기도 수원시".format(item.label))
    elif item.type == "bucket":
        opts = []
        for label in item.enum:
            lo, hi, open_ = bucket_bounds(label)
            opts.append({"label": label, "values": {"residence_years": lo, "residence_years_upper": hi, "residence_years_open": open_}})
        spec.update(format="구간 하나를 고르고 values 의 키를 보냄", options=opts, text="- {}: {}".format(item.label, item.enum[-1]))
    elif item.code == "SPECIAL":
        opts = [{"label": it.label, "code": it.special_code} for it in registry.enabled() if it.special_code]
        spec.update(format="해당하는 신분 코드를 special_codes 에, 모든 항목에 답했으면 special_complete 를 true 로 (해당 없음 = 빈 목록 + true)", options=opts,
                    keys=["special_codes", "special_complete"], example={"special_codes": ["DISABLED"], "special_complete": True},
                    text="- {}: 해당 없음 (또는 항목 이름 나열)".format(item.label))
    elif item.code == "LANG":
        spec.update(format="{시험 이름: 점수 또는 등급 숫자}, 성적이 없으면 빈 객체 (독일어 등 CEFR 등급은 A1=1 ~ C2=6)", example={"TOEIC": 780, "HSK": 5, "독일어": 3}, text="- {}: TOEIC 780, HSK 5급, 독일어 B1 (없으면 '없음')".format(item.label))
    elif item.type == "int":
        spec.update(format="정수", range=list(item.range) if item.range else None, text="- {}: 24".format(item.label))
    return spec


def _head(message: str, n: int = 40) -> str:
    s = re.split(r" \(", message or "")[0].strip()
    return s if len(s) <= n else s[: n - 3] + "..."


def build_follow_up(entries: Sequence[Dict[str, Any]], p: StudentProfile, registry: Any, declined: Iterable[str] = (), round_no: int = 1,
                    max_questions: int = MAX_QUESTIONS, max_rounds: int = MAX_ROUNDS) -> Dict[str, Any]:
    """entries: 미달이 아닌 공고마다 {id, title, checks[Check]}. 반환: follow_up 블록(JSON 으로 내보낼 수 있는 dict)."""
    declined = set(declined or ())
    order = {it.code: i for i, it in enumerate(registry.items)}
    ask_notices: Dict[str, List[str]] = {}          # 질문 항목 -> 풀 수 있는 공고 id
    sole: Dict[str, int] = {}                       # 그 항목 하나로 공고의 질문 가능한 확인이 모두 풀리는 공고 수
    gap_notices: Dict[str, List[str]] = {}
    declined_hit: set = set()
    documents: List[Dict[str, Any]] = []

    for e in entries:
        per_notice_ask: set = set()
        text_only: List[str] = []
        for c in e["checks"]:
            if c.status != UNKNOWN:
                continue
            miss = [code for code in (c.needs or ()) if registry.get(code) is not None and not is_answered(registry.get(code), p)]
            if "RES" in miss:                       # 거주 기간은 본인 주민등록을 받은 뒤에 묻는다 (거주지가 맞지 않으면 필요 없다)
                miss = [m for m in miss if m != "RESY"]
            ask = [m for m in miss if registry.get(m).source == "student"]
            gap = [m for m in miss if registry.get(m).source != "student"]
            usable = [m for m in ask if m not in declined]
            declined_hit.update(m for m in ask if m in declined)
            if usable:
                per_notice_ask.update(usable)
            elif gap and not ask:
                for m in gap:
                    gap_notices.setdefault(m, [])
                    if e["id"] not in gap_notices[m]:
                        gap_notices[m].append(e["id"])
            elif not ask:
                text_only.append(_head(c.message))
        for code in per_notice_ask:
            ask_notices.setdefault(code, []).append(e["id"])
        if len(per_notice_ask) == 1:
            sole[next(iter(per_notice_ask))] = sole.get(next(iter(per_notice_ask)), 0) + 1
        if text_only:
            uniq = list(dict.fromkeys(text_only))
            documents.append({"id": e["id"], "title": e["title"], "count": len(uniq), "items": uniq[:3]})

    ranked = sorted(ask_notices, key=lambda m: (registry.get(m).sensitive, -len(ask_notices[m]), -sole.get(m, 0), order.get(m, 999)))
    if round_no >= max_rounds:
        questions_codes, done, reason = [], True, DONE_MAX_ROUNDS
    elif not ranked:
        questions_codes, done, reason = [], True, (DONE_ALL_DECLINED if declined_hit else DONE_NO_QUESTIONS)
    else:
        questions_codes, done, reason = ranked[:max_questions], False, None
    questions = []
    for code in questions_codes:
        it = registry.get(code)
        questions.append({"code": code, "label": it.label, "question": it.question, "type": it.type, "sensitive": bool(it.sensitive), "skippable": True,
                          "resolves": list(ask_notices[code]), "resolves_count": len(ask_notices[code]), "answer": answer_spec(it, registry)})
    gaps = [{"code": m, "label": registry.get(m).label, "field": registry.get(m).profile_field, "resolves_count": len(ids), "resolves": ids}
            for m, ids in sorted(gap_notices.items(), key=lambda kv: (-len(kv[1]), order.get(kv[0], 999)))]
    return {"round": round_no, "max_rounds": max_rounds, "max_questions": max_questions, "done": done, "done_reason": reason,
            "questions": questions, "remaining": max(0, len(ranked) - len(questions_codes)), "data_gap": gaps, "document_only": documents}
