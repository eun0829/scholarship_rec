# -*- coding: utf-8 -*-
"""[RECOMMEND] 6-A 포털 연동 시험용 샘플 (docs/samples/api_samples.json): 학생 30명의 첫 호출 요청과 기대 결과.
고정한 공고 20건(data/v3_matrix), 학교 시흥, 기준일 2026-10-11 로 만든다. 코드가 원본이고 파일이 다르면 테스트가 실패한다.
다시 쓰기: python -m scholarship.recommend.samples --write
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

from ..common.config import Settings
from ..common.registry import load_registry
from . import matrix as X
from .followup import MAX_ROUNDS
from .request import handle_recommend
from .service import Recommender
from .student_text import parse_student_text

TODAY = date(2026, 10, 11)
SCHOOL = "시흥"


class NoticeSource:
    def __init__(self, notices: Dict[str, Dict[str, Any]]) -> None:
        self.n = notices

    def list_candidates(self, today: date) -> List[Dict[str, Any]]:
        return [{"src_cd": "EXT02", "src_id": i, "title": v["title"], "origin_url": v.get("url"), "category": "OUT", "elig": v["elig"], "status": None,
                 "apply_end": date(2026, 12, 31), "apply_start": None, "org_deadline": None, "summary": None} for i, v in self.n.items()]


def _keys(item: Any) -> List[str]:
    return [k for k in (item.profile_field or "").split("/") if k]


def first_keys() -> set:
    """첫 호출에 보내는 키: 학교 DB 항목(성적 만점 포함) + 본인 주민등록"""
    out = {"gpa_scale"}
    for it in load_registry().enabled():
        if it.kind == "student" and (it.source == "school_db" or it.code == "RES"):
            out |= set(_keys(it))
    return out


def answer_keys(code: str) -> List[str]:
    if code == "SPECIAL":
        return ["special_codes", "special_complete", "is_basic_livelihood", "is_near_poor", "is_single_parent"]
    if code == "RESY":
        return ["residence_years", "residence_years_upper", "residence_years_open"]
    return _keys(load_registry().get(code))


def settings() -> Settings:
    sc = X.SCHOOLS[SCHOOL]
    s = Settings()
    s.recommend_school_sido, s.recommend_school_sigungu, s.recommend_school_type, s.recommend_school_programs = sc["sido"], sc["sigungu"], sc["type"], tuple(sc["programs"])
    return s


def _counts(r: Dict[str, Any]) -> Dict[str, int]:
    return {k: r["counts"][k] for k in ("match", "conditional", "no_match")}


def build_samples() -> Dict[str, Any]:
    rec = Recommender(NoticeSource(X.load_notices()), settings())
    fk = first_keys()
    out: List[Dict[str, Any]] = []
    for st in X.load_students():
        full = parse_student_text(st.text).data
        profile = {k: v for k, v in full.items() if k in fk}
        first = {"request_id": st.id, "profile": profile, "today": TODAY.isoformat()}
        status, r = handle_recommend(rec, first)
        assert status == 200
        asked: List[str] = [q["code"] for q in r["follow_up"]["questions"]]
        declined: List[str] = []
        rounds, final = 1, r
        while not final["follow_up"]["done"] and rounds < MAX_ROUNDS:
            rounds += 1
            answers: Dict[str, Any] = {}
            for q in final["follow_up"]["questions"]:
                have = {k: full[k] for k in answer_keys(q["code"]) if k in full}
                if have:
                    answers.update(have)
                else:
                    declined.append(q["code"])
            status, final = handle_recommend(rec, {"request_id": st.id, "profile": profile, "answers": answers, "declined": declined, "round": rounds, "today": TODAY.isoformat()})
            profile = {**profile, **answers}
            asked += [q["code"] for q in final["follow_up"]["questions"]]
        out.append({"name": st.id, "description": st.desc, "first_request": first, "full_profile": full,
                    "expected_first": {"counts": _counts(r), "question_codes": [q["code"] for q in r["follow_up"]["questions"]]},
                    "expected_final": {"counts": _counts(final), "rounds": rounds, "asked_codes": asked, "declined": declined}})
    return {"meta": {"school": SCHOOL, "today": TODAY.isoformat(), "notices": len(X.load_notices()),
                     "note": "고정한 공고 20건 기준. 공고가 바뀌면 판정 개수는 달라진다. 필드 구조와 호출 흐름을 맞춰 보는 용도"}, "students": out}


def samples_json() -> str:
    return json.dumps(build_samples(), ensure_ascii=False, indent=1) + "\n"


if __name__ == "__main__":
    if "--write" in sys.argv:
        (Path(__file__).resolve().parents[2] / "docs" / "samples" / "api_samples.json").write_text(samples_json(), encoding="utf-8")
        print("docs/samples/api_samples.json 갱신")
    else:
        print(samples_json(), end="")
