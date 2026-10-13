# -*- coding: utf-8 -*-
"""5단계 후속 질문: 5-A 확인 필요 항목의 입력 후보(needs), 5-B 질문 생성·순위·답변 형식, 5-C 이어 답하기(recommend_step)와 변화."""
import copy
import json
from datetime import date

import pytest

import run_recommend
from scholarship.common.config import Settings
from scholarship.common.registry import load_registry
from scholarship.recommend import matcher as M
from scholarship.recommend import matrix as X
from scholarship.recommend import needs as N
from scholarship.recommend.followup import (DONE_ALL_DECLINED, DONE_MAX_ROUNDS, DONE_NO_QUESTIONS, answer_spec, build_follow_up, is_answered)
from scholarship.recommend.matcher import Check
from scholarship.recommend.profile import ProfileError
from scholarship.recommend.service import Recommender, diff_evaluations, format_table, format_text
from tests.test_matcher_v3 import SUWON, TODAY, notice, one, prof, reg, req, v3cand
from tests.test_recommend_service import Stub

UNKNOWN, PASS, FAIL = M.UNKNOWN, M.PASS, M.FAIL
REG = load_registry()


# ================================================================== 5-A: 확인 필요 항목이 풀리는 입력 후보
def needs_of(ev, key="region", scope="공통"):
    return one(ev, key, scope).needs or ()


def test_region_needs_follow_the_requirement_type():
    self_ = M.evaluate(notice(**reg("ANY", req("RESIDENCE_SELF", "경기도 수원시"))), prof())
    assert needs_of(self_) == ("RES",)
    assert needs_of(M.evaluate(notice(**reg("ANY", req("RESIDENCE_PARENT", "경기도 수원시"))), prof())) == ("RESP",)
    assert needs_of(M.evaluate(notice(**reg("ANY", req("GRADUATED_SCHOOL", "경기도 수원시"))), prof())) == ("HSL",)
    both = M.evaluate(notice(**reg("ANY", req("RESIDENCE_SELF", "경기도 수원시"), req("RESIDENCE_PARENT", "경기도 수원시"), req("GRADUATED_SCHOOL", "경기도 수원시"))), prof())
    assert needs_of(both) == ("RES", "RESP", "HSL")                                                                  # 요건 순서대로, 중복 없이


def test_region_needs_are_empty_when_no_student_input_can_resolve_it():
    assert needs_of(M.evaluate(notice(**reg("ANY", req("SCHOOL_LOCATION", "서울특별시"))), prof())) == ()           # 학교 소재지는 서버 설정
    assert needs_of(M.evaluate(notice(**reg("ANY", req("PARENT_WORKPLACE", "인천광역시"))), prof())) == ()          # 부모 근무지는 입력 항목이 없다
    assert needs_of(M.evaluate(notice(**reg("ANY", {"type": "RESIDENCE_SELF", "areas": []})), prof())) == ()       # 공고가 지역을 밝히지 않음
    assert needs_of(M.evaluate(notice(**reg("ANY", req("RESIDENCE_SELF", "경기도 수원시"))), prof(**SUWON))) == ()          # 판정이 났으면 후보 없음


def test_region_years_ask_for_the_period_only_when_the_place_is_settled():
    n = notice(**reg("ANY", req("RESIDENCE_SELF", "경기도 수원시", years=3)))
    assert needs_of(M.evaluate(n, prof(residence_sido="경기도", residence_sigungu="수원시"))) == ("RESY",)          # 지역은 맞고 기간만 모름
    assert needs_of(M.evaluate(n, prof())) == ("RES",)                                                              # 거주지부터 묻는다 (거주지가 맞아야 기간이 의미 있고, 맞으면 다음 회차에 기간을 묻는다)
    ambiguous = prof(residence_sido="경기도", residence_sigungu="수원시", residence_years=1.0, residence_years_upper=3.0)
    assert needs_of(M.evaluate(n, ambiguous)) == ()                                                                # '1~3년'은 다시 물어도 달라지지 않는다
    assert needs_of(M.evaluate(n, prof(residence_sido="서울특별시", residence_sigungu="서초구"))) == ()           # 거주지가 달라 미달이면 기간은 묻지 않는다
    parent = notice(**reg("ANY", req("RESIDENCE_PARENT", "경기도 수원시", years=3)))
    assert needs_of(M.evaluate(parent, prof(parent_residence_sido="경기도", parent_residence_sigungu="수원시"))) == ()   # 부모 거주 기간은 입력 항목이 없다


def test_special_needs_and_the_special_income_combination():
    sp = [{"code": "DISABLED", "mode": "REQUIRED"}]
    assert needs_of(M.evaluate(notice(special_groups=sp), prof()), "special") == ("SPECIAL",)
    assert needs_of(M.evaluate(notice(special_groups=[{"code": "WORKER_CHILD", "mode": "REQUIRED"}]), prof()), "special") == ()   # 입력 항목이 없는 신분
    both = notice(special_groups=sp, income={"max_bracket": 4})
    assert needs_of(M.evaluate(both, prof()), "special") == ("SPECIAL", "INC")
    assert needs_of(M.evaluate(both, prof(special_codes=[], special_complete=True)), "special") == ("INC",)             # 신분은 알고 소득만 모름
    assert needs_of(M.evaluate(both, prof(income_bracket=3)), "special") == ("SPECIAL",)                                # 소득은 알고 신분만 모름
    assert needs_of(M.evaluate(both, prof(special_codes=["DISABLED"], special_complete=True, income_bracket=9)), "special") == ()   # 둘 다 알고 결합만 모호하면 질문으로 풀 수 없다


@pytest.mark.parametrize("g, want", [
    ({"min": 3.0, "scale": 4.5, "basis": "LAST_SEMESTER"}, ("GL",)), ({"min": 3.0, "scale": 4.5, "basis": "CUMULATIVE"}, ("GC",)),
    ({"min": 3.0, "scale": 4.5, "basis": "EITHER"}, ("GL", "GC")), ({"min": 3.0, "scale": 4.5, "basis": "LAST_N", "terms": 4}, ("GN",)),
    ({"min": 80, "scale": 100, "basis": "LAST_SEMESTER"}, ("SP",)), ({"min": 85, "scale": 100, "basis": "CUMULATIVE"}, ("GCP",)),
    ({"min": 3.0, "scale": 4.5, "basis": "LAST_SEMESTER", "alternatives": [{"min": 80, "scale": 100}]}, ("GL", "SP")),
    ({"min": 3.0, "scale": 4.5, "basis": "ENTRANCE"}, ()), (None, ("GL", "GC"))])
def test_grade_needs_by_basis_and_scale(g, want):
    assert N.gpa_needs(g) == want


@pytest.mark.parametrize("key, want", [("age", ("AGE",)), ("gender", ("GEN",)), ("language", ("LANG",)), ("income", ("INC",)), ("major_group", ("MAJG",)), ("grade", ("YEAR",)),
                                       ("terms", ("SEM",)), ("new_student", ("SEM", "TRF")), ("transfer", ("TRF",)), ("entry", ("ENT",)),
                                       ("other", ()), ("pending", ()), ("exclusion", ()), ("major", ()), ("school", ())])
def test_default_needs_by_check_key(key, want):
    assert N.needs_for(Check(key, UNKNOWN, "x"), None) == want


def test_v2_region_needs_and_annotation_only_fills_unknown_checks():
    assert N.needs_for(Check("region", UNKNOWN, "지역 확인 필요"), None) == ("RES",)
    assert N.needs_for(Check("region", UNKNOWN, "학교 소재지 정보가 없음"), None) == ()
    cs = [Check("age", UNKNOWN, "x"), Check("age", PASS, "x"), Check("region", UNKNOWN, "x", needs=("HSL",))]
    N.annotate(cs)
    assert [c.needs for c in cs] == [("AGE",), None, ("HSL",)]                                                       # 판정이 난 항목과 이미 정해진 후보는 건드리지 않는다
    done = Check("special", UNKNOWN, "x", needs=())
    N.annotate([done])
    assert done.needs == ()                                                                                          # '입력으로 풀 수 없다'고 정해 둔 빈 후보를 기본값으로 덮어쓰지 않는다


def test_every_unknown_check_across_the_matrix_has_candidates_unless_it_is_a_text_condition():
    """3000쌍의 모든 '확인 필요' 항목은 입력 후보가 있거나, 입력으로 풀 수 없는 종류(문장 조건·소득 확장·학과 문구·서버 설정·제외)여야 한다."""
    text_only = {"other", "pending", "major", "school", "exclusion"}
    unresolvable = ("입력받지 않아", "설정이 없음", "불명확", "근로자 자녀", "기타 신분")             # 부모 거주 기간, 졸업 학교 재학 기간, 학교 소재지 설정, 결합 모호 같은 것은 입력 항목이 없다
    notices, students = X.load_notices(), X.load_students()
    empty, unmarked = set(), 0
    for p in X.run(notices, students):
        for c in p.evaluation.common + [c for v in p.evaluation.variants for c in v.checks]:
            if c.status != UNKNOWN:
                continue
            unmarked += c.needs is None                                                                    # 모든 확인 필요 항목은 판정이 끝나면 표시되어 있어야 한다
            if not c.needs and c.key not in text_only and not any(m in c.message for m in unresolvable):
                empty.add((c.key, c.message[:70]))
    assert unmarked == 0 and empty == set(), (unmarked, sorted(empty)[:5])


# ================================================================== 5-B: 질문 생성
def entry(nid, *checks, title=None):
    return {"id": nid, "title": title or "공고 " + nid, "checks": list(checks)}


def ck(*needs, key="region", msg="확인 필요"):
    return Check(key, UNKNOWN, msg, needs=tuple(needs))


def fu(entries, profile=None, **kw):
    return build_follow_up(entries, profile or prof(), REG, **kw)


def codes(f):
    return [q["code"] for q in f["questions"]]


def test_questions_are_ranked_by_how_many_notices_they_resolve_with_sensitive_items_last():
    f = fu([entry("A", ck("RESP")), entry("B", ck("RESP")), entry("C", ck("HSL")), entry("D", ck("SPECIAL", key="special")), entry("E", ck("SPECIAL", key="special")),
            entry("F", ck("SPECIAL", key="special")), entry("G", ck("AGE", key="age"))])
    assert codes(f) == ["RESP", "HSL", "AGE", "SPECIAL"]                                                              # 신분은 3건을 풀어도 민감해서 마지막
    assert [q["resolves_count"] for q in f["questions"]] == [2, 1, 1, 3] and f["questions"][0]["resolves"] == ["A", "B"]
    assert [q["sensitive"] for q in f["questions"]] == [False, False, False, True]


def test_ties_prefer_the_item_that_alone_settles_a_notice_then_registry_order():
    f = fu([entry("A", ck("AGE", key="age"), ck("RESP")), entry("B", ck("AGE", key="age")), entry("C", ck("RESP")), entry("D", ck("HSL"))])
    counts = {q["code"]: q["resolves_count"] for q in f["questions"]}
    assert counts == {"RESP": 2, "AGE": 2, "HSL": 1}
    assert codes(f)[:2] == ["RESP", "AGE"] and codes(f)[2] == "HSL"                       # RESP·AGE 는 2건씩 / RESP 는 C 를 혼자 확정하고 AGE 는 B 를 혼자 확정 -> 같으면 사전 순서(RESP 먼저)


def test_a_notice_counts_once_per_item_even_with_several_checks():
    f = fu([entry("A", ck("RESP"), ck("RESP", key="region", msg="다른 요건"))])
    assert f["questions"][0]["resolves"] == ["A"] and f["questions"][0]["resolves_count"] == 1


def test_answered_items_are_not_asked_and_the_check_moves_to_the_document_list():
    answered = prof(parent_residence_sido="경기도", parent_residence_sigungu="수원시")
    f = fu([entry("A", ck("RESP", msg="세부 지역 확인 필요"))], answered)
    assert f["questions"] == [] and f["document_only"] == [{"id": "A", "title": "공고 A", "count": 1, "items": ["세부 지역 확인 필요"]}] and f["done_reason"] == DONE_NO_QUESTIONS
    half = prof(parent_residence_sido="경기도")                                                                       # 시·도만 답했으면 시·군·구를 더 묻는다
    assert codes(fu([entry("A", ck("RESP"))], half)) == ["RESP"]


def test_declined_items_are_not_asked_again_and_the_end_reason_says_so():
    f = fu([entry("A", ck("RESP")), entry("B", ck("AGE", key="age"))], declined=["RESP"])
    assert codes(f) == ["AGE"] and f["done"] is False
    f = fu([entry("A", ck("RESP"))], declined=["RESP"])
    assert f["questions"] == [] and f["done"] is True and f["done_reason"] == DONE_ALL_DECLINED


def test_school_db_items_become_data_gaps_not_questions():
    f = fu([entry("A", ck("GL", key="gpa")), entry("B", ck("GL", "SP", key="gpa")), entry("C", ck("INC", key="income"))])
    assert f["questions"] == [] and f["done_reason"] == DONE_NO_QUESTIONS
    assert [(g["code"], g["resolves_count"], g["field"]) for g in f["data_gap"]] == [("GL", 2, "gpa_last"), ("SP", 1, "gpa_percent"), ("INC", 1, "income_bracket")]
    filled = fu([entry("A", ck("GL", key="gpa", msg="만점이 달라 비교할 수 없음"))], prof(gpa_last=3.5))                 # 값이 있는데도 확인 필요면 공고문 확인
    assert filled["data_gap"] == [] and filled["document_only"][0]["items"] == ["만점이 달라 비교할 수 없음"]


def test_text_conditions_go_to_the_document_list_with_a_short_head():
    f = fu([entry("A", ck(key="other", msg="확인 필요: 선원법 적용 선박에 승선한 선원 및 가족 (공고문 참고)"), ck(key="other", msg="확인 필요: 승무경력"), title="선원가족장학")])
    d = f["document_only"][0]
    assert d["id"] == "A" and d["title"] == "선원가족장학" and d["count"] == 2 and d["items"][0] == "확인 필요: 선원법 적용 선박에 승선한 선원 및 가족"
    assert f["questions"] == [] and f["data_gap"] == []


def test_residence_period_is_asked_only_after_the_residence_is_known():
    entries = [entry("A", ck("RES", "RESY"))]
    assert codes(fu(entries)) == ["RES"]
    assert codes(fu(entries, prof(residence_sido="경기도", residence_sigungu="수원시"))) == ["RESY"]                # 거주지를 답했으면 기간이 남는다
    assert codes(fu([entry("A", ck("RESY"))], prof(residence_sido="경기도", residence_sigungu="수원시"))) == ["RESY"]


def test_question_limits_rounds_and_remaining():
    es = [entry("A", ck("RESP")), entry("B", ck("HSL")), entry("C", ck("AGE", key="age")), entry("D", ck("LANG", key="language")), entry("E", ck("SPECIAL", key="special"))]
    f = fu(es, max_questions=2)
    assert len(f["questions"]) == 2 and f["remaining"] == 3 and f["done"] is False and f["max_questions"] == 2
    last = fu(es, round_no=3)
    assert last["questions"] == [] and last["done"] is True and last["done_reason"] == DONE_MAX_ROUNDS and last["remaining"] == 5     # 최대 회차에서는 더 묻지 않는다
    assert fu(es, round_no=2)["done"] is False and fu(es, round_no=2, max_rounds=2)["done_reason"] == DONE_MAX_ROUNDS


def test_individual_special_items_are_never_asked_one_by_one():
    f = fu([entry("A", ck("SPECIAL", key="special"))])
    assert codes(f) == ["SPECIAL"] and f["questions"][0]["sensitive"] is True and f["questions"][0]["skippable"] is True


@pytest.mark.parametrize("code, keys", [("RES", ["residence_sido", "residence_sigungu"]), ("RESP", ["parent_residence_sido", "parent_residence_sigungu"]),
                                        ("HSL", ["hs_sido", "hs_sigungu"]), ("AGE", ["age"]), ("LANG", ["language_scores"]), ("RESY", ["residence_years"]),
                                        ("SPECIAL", ["special_codes", "special_complete"])])
def test_answer_specs_tell_the_portal_which_json_keys_to_send(code, keys):
    spec = answer_spec(REG.get(code), REG)
    assert spec["keys"] == keys and spec["text"].startswith("- " + REG.get(code).label + ":")


def test_answer_spec_options_and_examples():
    res = answer_spec(REG.get("RES"), REG)
    assert res["example"] == {"residence_sido": "경기도", "residence_sigungu": "수원시"} and res["text"] == "- 본인 주민등록: 경기도 수원시"
    opts = {o["label"]: o["values"] for o in answer_spec(REG.get("RESY"), REG)["options"]}
    assert opts["3년 이상"] == {"residence_years": 3.0, "residence_years_upper": None, "residence_years_open": True}
    assert opts["1~3년"] == {"residence_years": 1.0, "residence_years_upper": 3.0, "residence_years_open": False}
    sp = answer_spec(REG.get("SPECIAL"), REG)
    assert {o["code"] for o in sp["options"]} == {"BASIC_LIVELIHOOD", "NEAR_POOR", "DISABLED", "SINGLE_PARENT", "DEFECTOR", "MULTICULTURAL", "VETERAN", "MULTI_CHILD"}
    assert sp["example"] == {"special_codes": ["DISABLED"], "special_complete": True}
    assert answer_spec(REG.get("AGE"), REG)["range"] == [0, 100] and answer_spec(REG.get("LANG"), REG)["example"] == {"TOEIC": 780, "HSK": 5, "독일어": 3}


@pytest.mark.parametrize("code, kw, want", [
    ("RES", {}, False), ("RES", dict(residence_sido="경기도"), False), ("RES", dict(residence_sido="경기도", residence_sigungu="수원시"), True),
    ("SPECIAL", {}, False), ("SPECIAL", dict(special_codes=["DISABLED"]), False), ("SPECIAL", dict(special_codes=[], special_complete=True), True),
    ("RESY", {}, False), ("RESY", dict(residence_years=3.0), True), ("AGE", dict(age=0), True), ("LANG", {}, False), ("LANG", dict(language_scores={}), True)])
def test_is_answered(code, kw, want):
    assert is_answered(REG.get(code), prof(**kw)) is want
