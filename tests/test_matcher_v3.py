# -*- coding: utf-8 -*-
"""판정 엔진 v3 (4-A): 지역 요건, 성적, 학기 조건과 이수학기 파생값, 안전망(미구현 조건·면제), 유형 처리, 서비스 연결."""
import dataclasses

import pytest

from scholarship.common.ai.schema import normalize
from scholarship.common.config import Settings
from scholarship.recommend import matcher as M
from scholarship.recommend import matcher_v3 as V
from scholarship.recommend.matcher import CONDITIONAL, FAIL, MATCH, NO_MATCH, PASS, UNKNOWN
from scholarship.recommend.profile import ProfileError, StudentProfile, build_profile
from scholarship.recommend.service import Recommender, format_text
from tests.test_recommend_service import TODAY, Stub, cand
from tests.test_student_text import SAMPLE
from tests.test_ext02_trial import IDEAL, NATIONAL_LIKE
from scholarship.recommend.student_text import profile_from_text


def notice(**kw):
    base = {"category": "OUT", "apply_end": "2026-10-08", "summary": "x"}
    base.update(kw)
    out, _ = normalize(base, None, "", version=3)
    out["schema_version"] = 3
    return out


def prof(**kw):
    d = {"level": "UNIV", "gpa_scale": 4.5}
    d.update(kw)
    return build_profile(d).profile


def req(t, *areas, years=None):
    r = {"type": t, "areas": list(areas)}
    if years:
        r["min_years"] = years
    return r


def reg(mode, *reqs):
    return {"region": {"mode": mode, "requirements": list(reqs)}}


def one(ev, key, scope="공통"):
    cs = [c for c in (ev.common if scope == "공통" else next(v.checks for v in ev.variants if v.name == scope)) if c.key == key]
    assert len(cs) == 1, cs
    return cs[0]


SUWON = dict(residence_sido="경기도", residence_sigungu="수원시", residence_years=3.0, residence_years_open=True)


# ================================================================== 분기
def test_v3_notices_use_the_v3_path_and_v2_notices_keep_the_v2_path():
    p = prof(residence_sido="경기도", residence_sigungu="수원시")
    assert M.evaluate(notice(**reg("ANY", req("RESIDENCE_SELF", "경기도 수원시"))), p).verdict == MATCH               # v3: 시·도+시·군·구 문자열을 읽음
    old = {"schema_version": 2, "region": {"residence": ["경기도 수원시"]}}
    assert M.evaluate(old, p).verdict == NO_MATCH                                                              # v2 경로는 그대로 (기존 동작)
    assert M.is_v3({"schema_version": 3}) and not M.is_v3({"schema_version": 2}) and not M.is_v3({})
    assert M.evaluate(old, p).assumptions == []


# ================================================================== 지역
def test_region_single_requirement():
    n = notice(**reg("ANY", req("RESIDENCE_SELF", "경기도 수원시")))
    assert one(M.evaluate(n, prof(**SUWON)), "region").status == PASS
    assert one(M.evaluate(n, prof(residence_sido="서울특별시", residence_sigungu="서초구")), "region").status == FAIL
    c = one(M.evaluate(n, prof()), "region")
    assert c.status == UNKNOWN and "학생의 본인 주민등록 정보 없음" in c.message


def test_region_any_mode():
    n = notice(**reg("ANY", req("RESIDENCE_SELF", "서울특별시 서초구"), req("RESIDENCE_PARENT", "서울특별시 서초구")))
    both_other = prof(**SUWON, parent_residence_sido="경기도", parent_residence_sigungu="수원시")
    assert one(M.evaluate(n, both_other), "region").status == FAIL
    c = one(M.evaluate(n, both_other), "region")
    assert "[본인 주민등록 서울특별시 서초구] 또는 [부모 주민등록 서울특별시 서초구]" in c.message
    parent_ok = prof(**SUWON, parent_residence_sido="서울특별시", parent_residence_sigungu="서초구")
    assert one(M.evaluate(n, parent_ok), "region").status == PASS                                              # 하나만 해당해도 충족
    unknown_parent = one(M.evaluate(n, prof(**SUWON)), "region")                                               # 본인은 미달, 부모 정보 없음
    assert unknown_parent.status == UNKNOWN and "학생의 부모 주민등록 정보 없음" in unknown_parent.message


def test_region_all_mode():
    n = notice(**reg("ALL", req("RESIDENCE_SELF", "경기도 수원시"), req("GRADUATED_SCHOOL", "경기도 안양시")))
    assert one(M.evaluate(n, prof(**SUWON, hs_sido="경기도", hs_sigungu="안양시")), "region").status == PASS
    assert one(M.evaluate(n, prof(**SUWON, hs_sido="서울특별시", hs_sigungu="서초구")), "region").status == FAIL
    c = one(M.evaluate(n, prof(**SUWON)), "region")
    assert c.status == UNKNOWN and "졸업 고교 소재지" in c.message
    assert " 그리고 " in one(M.evaluate(n, prof(**SUWON, hs_sido="서울특별시", hs_sigungu="서초구")), "region").message


def test_region_unknown_mode_only_decides_when_both_readings_agree():
    n = notice(**reg("UNKNOWN", req("RESIDENCE_SELF", "경기도 수원시"), req("GRADUATED_SCHOOL", "경기도 안양시")))
    assert one(M.evaluate(n, prof(**SUWON, hs_sido="경기도", hs_sigungu="안양시")), "region").status == PASS
    mixed = one(M.evaluate(n, prof(**SUWON, hs_sido="서울특별시", hs_sigungu="서초구")), "region")
    assert mixed.status == UNKNOWN and "'또는'인지 '그리고'인지 공고에 불명확" in mixed.message
    assert one(M.evaluate(n, prof(residence_sido="서울특별시", residence_sigungu="서초구", hs_sido="서울특별시", hs_sigungu="강남구")), "region").status == FAIL


@pytest.mark.parametrize("student, need, want", [
    (dict(residence_years=2), 3, FAIL), (dict(residence_years=2), 2, PASS), (dict(residence_years=2), 1, PASS), (dict(residence_years=2.5), 3, FAIL),   # 정확한 값
    (dict(residence_years=3, residence_years_open=True), 3, PASS), (dict(residence_years=3, residence_years_open=True), 5, UNKNOWN),                   # '3년 이상'
    (dict(residence_years=1, residence_years_upper=3), 1, PASS), (dict(residence_years=1, residence_years_upper=3), 2, UNKNOWN),
    (dict(residence_years=1, residence_years_upper=3), 3, UNKNOWN), (dict(residence_years=1, residence_years_upper=3), 4, FAIL),                      # '1~3년'
    (dict(residence_years=0, residence_years_upper=11 / 12), 1, FAIL), (dict(residence_years=0, residence_years_upper=11 / 12), 3, FAIL),            # '1년 미만'
    ({}, 3, UNKNOWN),
])
def test_region_residence_period_bounds(student, need, want):
    n = notice(**reg("ANY", req("RESIDENCE_SELF", "경기도 수원시", years=need)))
    c = one(M.evaluate(n, prof(residence_sido="경기도", residence_sigungu="수원시", **student)), "region")
    assert c.status == want, c.message


def test_region_period_messages_explain_the_gap():
    n = notice(**reg("ANY", req("RESIDENCE_SELF", "경기도 수원시", years=5)))
    assert "3년 이상으로만 입력되어 5년 충족 여부 확인 필요" in one(M.evaluate(n, prof(**SUWON)), "region").message
    n2 = notice(**reg("ANY", req("RESIDENCE_SELF", "경기도 수원시", years=2)))
    assert "1~3년 구간이라 2년 충족 여부 확인 필요" in one(M.evaluate(n2, prof(residence_sido="경기도", residence_sigungu="수원시", residence_years=1, residence_years_upper=3)), "region").message
    assert "본인 거주 기간 정보 없음" in one(M.evaluate(n2, prof(residence_sido="경기도", residence_sigungu="수원시")), "region").message


def test_region_period_is_failed_even_when_place_is_unknown_but_not_the_other_way():
    n = notice(**reg("ANY", req("RESIDENCE_SELF", "경기도 수원시 영통구", years=3)))
    short = prof(residence_sido="경기도", residence_sigungu="수원시", residence_years=1)                             # 구 단위 확인 필요 + 기간 미달 -> FAIL
    assert one(M.evaluate(n, short), "region").status == FAIL
    assert one(M.evaluate(n, prof(**SUWON)), "region").status == UNKNOWN                                       # 구 단위 확인 필요 + 기간 충족 -> UNKNOWN


def test_parent_residence_period_cannot_be_verified():
    n = notice(**reg("ANY", req("RESIDENCE_PARENT", "경기도 수원시", years=3)))
    c = one(M.evaluate(n, prof(parent_residence_sido="경기도", parent_residence_sigungu="수원시")), "region")
    assert c.status == UNKNOWN and "부모 주민등록 거주·재학 기간은 입력받지 않아 확인 필요" in c.message                 # 부모 거주 기간은 입력 항목이 없음
    assert one(M.evaluate(n, prof(parent_residence_sido="서울특별시", parent_residence_sigungu="서초구")), "region").status == FAIL


def test_region_types_use_the_matching_student_fields():
    hs = notice(**reg("ANY", req("GRADUATED_SCHOOL", "경기도 안양시")))
    assert one(M.evaluate(hs, prof(hs_sido="경기도", hs_sigungu="안양시")), "region").status == PASS
    assert one(M.evaluate(hs, prof(residence_sido="경기도", residence_sigungu="안양시")), "region").status == UNKNOWN    # 거주지는 졸업 고교 소재지가 아님
    sc = notice(**reg("ANY", req("SCHOOL_LOCATION", "경기도 시흥시")))
    assert one(M.evaluate(sc, prof(school_sido="경기도", school_sigungu="시흥시")), "region").status == PASS
    assert one(M.evaluate(sc, prof(school_sido="서울특별시", school_sigungu="성북구")), "region").status == FAIL
    for t in ("PARENT_WORKPLACE", "ACTUAL_RESIDENCE"):
        c = one(M.evaluate(notice(**reg("ANY", req(t, "경기도 수원시"))), prof(**SUWON)), "region")
        assert c.status == UNKNOWN and "입력받지 않아 확인 필요" in c.message


def test_region_areas_are_alternatives_and_empty_areas_are_unknown():
    n = notice(**reg("ANY", req("RESIDENCE_SELF", "서울특별시 성북구", "경기도 수원시")))
    assert one(M.evaluate(n, prof(**SUWON)), "region").status == PASS
    assert one(M.evaluate(n, prof(residence_sido="부산광역시", residence_sigungu="해운대구")), "region").status == FAIL
    empty = one(M.evaluate(notice(**reg("ANY", {"type": "RESIDENCE_SELF", "areas": []})), prof(**SUWON)), "region")
    assert empty.status == UNKNOWN and "지역 정보가 공고에 불명확" in empty.message


def test_region_gu_level_requires_the_student_gu():
    n = notice(**reg("ANY", req("RESIDENCE_SELF", "경기도 수원시 영통구")))
    c = one(M.evaluate(n, prof(residence_sido="경기도", residence_sigungu="수원시")), "region")
    assert c.status == UNKNOWN and "구 단위까지 확인 필요" in c.message
    assert one(M.evaluate(n, prof(residence_sido="경기도", residence_sigungu="수원시 영통구")), "region").status == PASS
    assert one(M.evaluate(n, prof(residence_sido="경기도", residence_sigungu="수원시 팔달구")), "region").status == FAIL


def test_v2_style_region_inside_a_v3_notice_falls_back_to_the_v2_check():
    n = notice(region={"residence": ["춘천시"]})
    assert one(M.evaluate(n, prof(residence_sido="강원특별자치도", residence_sigungu="춘천시")), "region").message.startswith("주민등록 거주지 해당")
    assert one(M.evaluate(n, prof(residence_sido="경기도", residence_sigungu="수원시")), "region").status == FAIL


def test_region_notes_are_shown_as_a_reference():
    n = notice(region={"mode": "ANY", "requirements": [req("RESIDENCE_SELF", "경기도 수원시")], "notes": "졸업학교 조건은 거창군 소재 학교"})
    assert one(M.evaluate(n, prof(**SUWON)), "region").message.endswith("· 참고: 졸업학교 조건은 거창군 소재 학교")


# ================================================================== 성적
def gpa(**kw):
    return {"gpa": dict({"scale": 4.5, "basis": "LAST_SEMESTER"}, **kw)}


@pytest.mark.parametrize("basis, student, mn, want", [
    ("LAST_SEMESTER", dict(gpa_last=3.79), 3.5, PASS), ("LAST_SEMESTER", dict(gpa_last=3.79), 3.9, FAIL), ("LAST_SEMESTER", dict(gpa_cumulative=3.9), 3.5, UNKNOWN),
    ("CUMULATIVE", dict(gpa_cumulative=3.4), 3.5, FAIL), ("CUMULATIVE", dict(gpa_cumulative=3.6), 3.5, PASS), ("CUMULATIVE", dict(gpa_last=3.9), 3.5, UNKNOWN),
    ("EITHER", dict(gpa_last=3.0, gpa_cumulative=3.8), 3.5, PASS), ("EITHER", dict(gpa_last=3.8, gpa_cumulative=3.0), 3.5, PASS),
    ("EITHER", dict(gpa_last=3.0, gpa_cumulative=3.1), 3.5, FAIL), ("EITHER", dict(gpa_last=3.0), 3.5, UNKNOWN),          # 한쪽만 알면 다른 쪽이 충족일 수 있음
    ("EITHER", dict(), 3.5, UNKNOWN),
    ("UNKNOWN", dict(gpa_last=3.0, gpa_cumulative=3.1), 3.5, FAIL), ("UNKNOWN", dict(gpa_last=3.0), 3.5, UNKNOWN), ("UNKNOWN", dict(gpa_last=3.9), 3.5, PASS),
])
def test_gpa_basis_rules(basis, student, mn, want):
    n = notice(**gpa(basis=basis, min=mn))
    assert one(M.evaluate(n, prof(**student)), "gpa").status == want


def test_gpa_messages():
    n = notice(**gpa(min=3.5))
    assert one(M.evaluate(n, prof(gpa_last=3.79)), "gpa").message == "성적 충족 (직전학기 평점 3.79/4.5, 기준 3.5/4.5 이상)"
    assert one(M.evaluate(n, prof(gpa_last=3.1)), "gpa").message == "성적 미달 (직전학기 평점 3.1/4.5, 기준 3.5/4.5 이상)"
    assert one(M.evaluate(n, prof()), "gpa").message == "직전학기 평점 정보 없음 (기준: 3.5/4.5 이상)"
    one_side = one(M.evaluate(notice(**gpa(basis="EITHER", min=3.5)), prof(gpa_last=3.0)), "gpa")
    assert "직전학기·전체 중 한쪽만 입력되어 다른 쪽이 충족일 수 있음" in one_side.message


def test_notice_without_scale_assumes_4_5_and_reports_it():
    n = notice(gpa={"min": 3.0, "basis": "LAST_SEMESTER"})                       # 만점 미기재
    assert n["gpa"].get("scale") is None
    ev = M.evaluate(n, prof(gpa_last=3.5))
    c = one(ev, "gpa")
    assert c.status == PASS and "(만점 미기재: 4.5 가정)" in c.message
    assert ev.assumptions == ["공고에 평점 만점 표기가 없어 4.5 만점으로 가정함"]
    assert M.evaluate(notice(**gpa(min=3.0)), prof(gpa_last=3.5)).assumptions == []                            # 만점이 적혀 있으면 가정 없음
    conv = M.evaluate(n, prof(gpa_scale=4.3, gpa_last=3.9))                                                    # 학생 만점이 다르면 비율 환산
    assert one(conv, "gpa").status == PASS


def test_gpa_alternatives_prefer_the_students_own_scale():
    n = notice(gpa={"min": 3.5, "scale": 4.5, "basis": "LAST_SEMESTER", "alternatives": [{"min": 3.3, "scale": 4.3}]})
    assert one(M.evaluate(n, prof(gpa_scale=4.3, gpa_last=3.4)), "gpa").status == PASS                 # 4.3 기준 3.3 이상만 본다
    assert one(M.evaluate(n, prof(gpa_scale=4.5, gpa_last=3.4)), "gpa").status == FAIL


def test_hundred_point_scale_needs_the_converted_score():
    n = notice(gpa={"min": 80, "scale": 100, "basis": "LAST_SEMESTER"})
    assert one(M.evaluate(n, prof(gpa_last=3.8, gpa_percent=85)), "gpa").status == PASS
    assert one(M.evaluate(n, prof(gpa_last=3.8, gpa_percent=70)), "gpa").status == FAIL
    c = one(M.evaluate(n, prof(gpa_last=3.8)), "gpa")
    assert c.status == UNKNOWN and "100점 환산 점수(직전학기 백분위)가 필요함" in c.message and "만점 단위가 달라" not in c.message


def hist(*rows):
    return [{"term": t, "gpa": g, "credits": c} for t, g, c in rows]


def last_n(mn=3.5, n=2, **kw):
    return notice(gpa=dict({"min": mn, "scale": 4.5, "basis": "LAST_N", "terms": n}, **kw))


def test_last_n_average_simple_and_weighted():
    agree = prof(term_history=hist(("2026-1", 3.9, 15), ("2025-2", 3.9, 15)))
    c = one(M.evaluate(last_n(), agree), "gpa")
    assert c.status == PASS and c.message.startswith("성적 충족 (직전 2개 학기 평균 평점 3.90/4.5, 기준 3.5/4.5 이상)")
    low = one(M.evaluate(last_n(), prof(term_history=hist(("2026-1", 3.0, 15), ("2025-2", 3.0, 15)))), "gpa")
    assert low.status == FAIL
    straddle = one(M.evaluate(last_n(), prof(term_history=hist(("2026-1", 3.9, 15), ("2025-2", 3.1, 18)))), "gpa")   # 단순 3.50(충족), 가중 3.46(미달)
    assert straddle.status == UNKNOWN and "단순 평균 3.50과 학점 가중 평균 3.46의 판정이 달라 확인 필요" in straddle.message
    no_credit = one(M.evaluate(last_n(), prof(term_history=hist(("2026-1", 3.9, None), ("2025-2", 3.1, None)))), "gpa")
    assert no_credit.status == PASS and "학점이 없어 가중 평균은 계산하지 못함" in no_credit.message


def test_last_n_uses_only_the_most_recent_n_terms():
    h = hist(("2026-1", 3.9, 15), ("2025-2", 3.9, 15), ("2025-1", 1.0, 15))
    assert one(M.evaluate(last_n(n=2), prof(term_history=h)), "gpa").status == PASS
    assert one(M.evaluate(last_n(n=3), prof(term_history=h)), "gpa").status == FAIL


def test_last_n_edge_cases():
    short = one(M.evaluate(last_n(n=3), prof(term_history=hist(("2026-1", 3.9, 15)))), "gpa")
    assert short.status == UNKNOWN and "학기별 성적 부족 (직전 3개 학기 필요, 입력 1개)" in short.message
    none = one(M.evaluate(last_n(), prof(gpa_last=3.9)), "gpa")
    assert none.status == UNKNOWN and "정보 없음" in none.message
    assert one(M.evaluate(notice(gpa={"min": 3.5, "scale": 4.5, "basis": "LAST_N"}), prof(gpa_last=3.9)), "gpa").message.startswith("직전 몇 개 학기 평균인지 공고에 불명확")
    n1 = last_n(n=1)                                                                                           # 1개 학기 평균 = 직전학기
    assert one(M.evaluate(n1, prof(gpa_last=3.9)), "gpa").status == PASS
    assert one(M.evaluate(n1, prof(term_history=hist(("2026-1", 3.2, 15)))), "gpa").status == FAIL
    hundred = notice(gpa={"min": 80, "scale": 100, "basis": "LAST_N", "terms": 2})
    assert "100점제 기준은 판정하지 않음" in one(M.evaluate(hundred, prof(term_history=hist(("2026-1", 3.9, 15), ("2025-2", 3.9, 15)))), "gpa").message


def test_entrance_score_basis_is_left_for_confirmation():
    c = one(M.evaluate(notice(**gpa(basis="ENTRANCE", min=3.0)), prof(gpa_last=3.9)), "gpa")
    assert c.status == UNKNOWN and "입학성적(수능·내신) 기준: 입력받지 않아 확인 필요" in c.message


# ================================================================== 학기 조건과 이수학기 파생값
@pytest.mark.parametrize("kw, remaining, final", [
    (dict(semesters_completed=7), 1, True), (dict(semesters_completed=6), 2, False), (dict(semesters_completed=3), 5, False), (dict(semesters_completed=0), 8, False),
    (dict(semesters_completed=8), 0, None), (dict(semesters_completed=9), 0, None),                                   # 8학기를 마침: 초과학기인지 졸업학기인지 모름
    (dict(semesters_completed=7, remaining_semesters=3), 3, False),                                                # 학적 값이 우선, 졸업학기는 그 값에서
    (dict(semesters_completed=7, remaining_semesters=1, final_semester=False), 1, False),                          # 둘 다 있으면 그대로
    (dict(semesters_completed=7, final_semester=False), 1, False),
])
def test_derive_terms_for_a_four_year_student(kw, remaining, final):
    p0 = StudentProfile(level="UNIV", **kw)
    p, notes = V.derive_terms(p0)
    assert (p.remaining_semesters, p.final_semester) == (remaining, final)
    changed = (p.remaining_semesters, p.final_semester) != (p0.remaining_semesters, p0.final_semester)
    assert bool(notes) == changed                                                                                  # 값을 새로 채웠을 때만 알림 문구가 있다


@pytest.mark.parametrize("kw", [
    dict(level="GRAD", semesters_completed=3), dict(level="UNIV", school_type="COLLEGE", semesters_completed=3), dict(level="UNIV", admission_type="TRANSFER", semesters_completed=1),
    dict(level="UNIV", status="GRADUATED", semesters_completed=8), dict(level="UNIV", extended_enrollment=True, semesters_completed=9),
    dict(level="UNIV", graduation_deferred=True, semesters_completed=8), dict(level="UNIV"),
])
def test_derive_terms_does_nothing_when_the_program_length_is_unknown(kw):
    p0 = StudentProfile(**kw)
    p, notes = V.derive_terms(p0)
    assert p == p0 and notes == []


def test_derive_terms_uses_the_regular_terms_setting_and_does_not_mutate():
    p0 = StudentProfile(level="UNIV", semesters_completed=7)
    p, _ = V.derive_terms(p0, regular_terms=10)
    assert p.remaining_semesters == 3 and p.final_semester is False and p0.remaining_semesters is None
    assert V.derive_terms(StudentProfile(level="UNIV", school_type="GENERAL", semesters_completed=7))[0].remaining_semesters == 1     # 일반대학이면 계산


def test_completed_and_remaining_terms_checks():
    p = prof(semesters_completed=7)
    assert one(M.evaluate(notice(completed_terms={"min": 6}), p), "terms").status == PASS
    assert one(M.evaluate(notice(completed_terms={"max": 6}), p), "terms").status == FAIL
    assert one(M.evaluate(notice(completed_terms={"min": 8}), p), "terms").message == "이수 학기 7학기 (기준 8학기 이상 미달)"
    assert one(M.evaluate(notice(completed_terms={"min": 2, "max": 8}), p), "terms").status == PASS
    assert one(M.evaluate(notice(completed_terms={"min": 2}), prof()), "terms").message == "이수 학기 정보 없음 (기준 2학기 이상)"
    assert one(M.evaluate(notice(remaining_terms={"min": 2}), p), "terms").status == FAIL                          # 이수 7 -> 남은 1
    assert one(M.evaluate(notice(remaining_terms={"max": 1}), p), "terms").status == PASS
    assert one(M.evaluate(notice(remaining_terms={"min": 2}), prof(semesters_completed=7, remaining_semesters=3)), "terms").status == PASS   # 학적 값 우선
    assert one(M.evaluate(notice(remaining_terms={"min": 2}), prof()), "terms").status == UNKNOWN


def test_derived_values_are_reported_only_when_the_notice_uses_them():
    p = prof(semesters_completed=7)
    used = M.evaluate(notice(remaining_terms={"min": 1}), p)
    assert used.assumptions == ["남은 학기·졸업학기를 이수학기 7와 정규 8학기로 계산함 (학적 값이 있으면 그 값을 씀)"]
    assert M.evaluate(notice(**gpa(min=3.0)), p).assumptions == []                                               # 학기 조건이 없으면 알릴 필요 없음
    assert M.evaluate(notice(enrollment={"min_remaining_semesters": 1}), p).assumptions != []
    assert M.evaluate(notice(enrollment={"excluded_status": ["졸업예정자"]}), p).assumptions != []
    assert M.evaluate(notice(remaining_terms={"min": 1}), prof(semesters_completed=7, remaining_semesters=1, final_semester=True)).assumptions == []


def test_v3_enrollment_uses_derived_values_but_v2_does_not():
    last = prof(semesters_completed=7)
    v3 = notice(enrollment={"min_remaining_semesters": 2})
    assert one(M.evaluate(v3, last), "enrollment").status == FAIL
    v2 = {"schema_version": 2, "enrollment": {"min_remaining_semesters": 2}}
    assert one(M.evaluate(v2, last), "enrollment").status == UNKNOWN                                              # v2 판정은 바뀌지 않음
    grad = notice(enrollment={"excluded_status": ["졸업예정자"]})
    assert M.evaluate(grad, last).verdict == NO_MATCH and M.evaluate(grad, prof(semesters_completed=5)).verdict == MATCH
    assert M.evaluate(grad, prof(semesters_completed=7), regular_terms=10).verdict == MATCH                        # 정규 학기 수 설정이 반영됨


# ================================================================== 안전망: 아직 판정하지 않는 조건


def test_conditions_that_do_not_restrict_are_not_pending():
    for extra in ({"special_groups": [{"code": "MULTI_CHILD", "mode": "PREFERRED"}]}, {"transfer": {"mode": "ALLOWED"}}, {"age": {}}, {"school": {}},
                  {"income": {"basis": "BRACKET", "max_bracket": 8}}):
        assert not [c for c in M.evaluate(notice(**extra), prof(income_bracket=3)).common if c.key == "pending"], extra


# ================================================================== 안전망: 면제(waivers)


# ================================================================== 유형과 종합
def test_variants_are_judged_with_the_common_conditions():
    n = notice(**gpa(min=3.0), variants=[{"name": "서초", **reg("ANY", req("RESIDENCE_SELF", "서울특별시 서초구"))},
                                         {"name": "수원", **reg("ANY", req("RESIDENCE_SELF", "경기도 수원시"))}])
    ev = M.evaluate(n, prof(**SUWON, gpa_last=3.5))
    assert {v.name: v.verdict for v in ev.variants} == {"서초": NO_MATCH, "수원": MATCH} and ev.verdict == MATCH
    assert [v.name for v in ev.matched_variants] == ["수원"] and one(ev, "region", "수원").scope == "수원"
    low = M.evaluate(n, prof(**SUWON, gpa_last=2.0))                                                          # 공통 조건 미달이면 전체 미달
    assert low.verdict == NO_MATCH and any("성적 미달" in r for r in low.fail_reasons)
    none = M.evaluate(n, prof(residence_sido="부산광역시", residence_sigungu="해운대구", gpa_last=3.5))
    assert none.verdict == NO_MATCH and none.fail_reasons[0].startswith("해당하는 유형이 없음")


def test_variant_terms_gpa_and_assumptions():
    n = notice(variants=[{"name": "고학년", "completed_terms": {"min": 6}}, {"name": "성적", "gpa": {"min": 3.0, "basis": "LAST_SEMESTER"}}])
    ev = M.evaluate(n, prof(semesters_completed=7, gpa_last=3.5))
    assert {v.name: v.verdict for v in ev.variants} == {"고학년": MATCH, "성적": MATCH}
    assert ev.assumptions == ["공고에 평점 만점 표기가 없어 4.5 만점으로 가정함"]


def test_defer_keys_cover_the_new_keys():
    assert {"terms", "pending", "region", "gpa"} <= set(M.CHECK_KEYS)
    n = notice(**reg("ANY", req("RESIDENCE_SELF", "서울특별시 서초구")), completed_terms={"min": 9})
    p = prof(**SUWON, semesters_completed=7)
    assert M.evaluate(n, p).verdict == NO_MATCH
    assert M.evaluate(n, p, defer_keys=("region",)).verdict == NO_MATCH                                         # 학기 조건은 그대로 FAIL
    ev = M.evaluate(n, p, defer_keys=("region", "terms"))
    assert ev.verdict == CONDITIONAL and "AI 판단 예정" in one(ev, "region").message


# ================================================================== 실제 모양의 시나리오 (20건 시험 추출 결과의 형태)
def student_from(text):
    return profile_from_text(text)[0].profile


def test_realistic_parent_or_self_region_notice():
    n = notice(**IDEAL["9212"])
    seocho_parent = SAMPLE.replace("부모 주민등록: 경기도 수원시", "부모 주민등록: 서울특별시 서초구")
    ev = M.evaluate(n, student_from(seocho_parent))
    c = one(ev, "region")
    assert c.status == UNKNOWN and "부모 주민등록 서울특별시 서초구 1년 이상: 부모 주민등록 거주·재학 기간은 입력받지 않아 확인 필요" in c.message     # 부모 거주 기간은 확인 필요
    assert one(ev, "gpa").status == PASS and one(ev, "credits").status == PASS and ev.verdict == CONDITIONAL
    assert M.evaluate(n, student_from(SAMPLE)).verdict == NO_MATCH                                           # 수원: 본인·부모 모두 서초구가 아님


# ================================================================== 서비스 연결
def v3cand(src_id, **kw):
    kw.setdefault("scholarship_name", "테스트 장학 {}".format(src_id))        # 이름이 같으면 서비스가 같은 장학으로 보고 하나로 묶는다
    e = notice(**kw)
    return cand(src_id, e)


def run3(cands, text=SAMPLE, settings=None):
    from scholarship.recommend.student_text import parse_student_text
    data = parse_student_text(text).data
    return Recommender(Stub(cands), settings or Settings()).recommend(data, today=TODAY)


def test_service_marks_v3_and_lists_assumptions_and_excludes_region_mismatch():
    ok = v3cand(1, gpa={"min": 3.0, "basis": "LAST_SEMESTER"}, remaining_terms={"min": 1})
    far = v3cand(2, **reg("ANY", req("RESIDENCE_SELF", "경상남도 거창군")))
    res = run3([ok, far])
    assert [i["src_id"] for i in res["recommended"]] == ["1"] and [e["src_id"] for e in res["excluded"]] == ["2"]
    item = res["recommended"][0]
    assert item["schema_version"] == 3 and item["verdict"] == MATCH
    assert item["assumptions"] == ["남은 학기·졸업학기를 이수학기 7와 정규 8학기로 계산함 (학적 값이 있으면 그 값을 씀)", "공고에 평점 만점 표기가 없어 4.5 만점으로 가정함"]
    text = format_text(res)
    assert "※ 가정: 공고에 평점 만점 표기가 없어 4.5 만점으로 가정함" in text


def test_service_v2_items_are_unchanged_and_report_no_assumptions():
    old = cand(5)                                                   # elig() 는 schema_version 2
    item = run3([old])["recommended"][0]
    assert item["schema_version"] == 2 and item["assumptions"] == []


def test_service_uses_the_regular_terms_setting():
    n = v3cand(3, remaining_terms={"min": 3})
    assert run3([n])["excluded"][0]["src_id"] == "3"                                    # 이수 7학기 -> 남은 1학기
    s = Settings()
    s.recommend_regular_terms = 10                                                      # 정규 10학기면 남은 3학기
    assert run3([v3cand(3, remaining_terms={"min": 3})], settings=s)["recommended"][0]["src_id"] == "3"


def test_service_defer_keys_validation_accepts_new_keys():
    s = Settings()
    s.recommend_defer_keys = ("terms", "pending")
    run3([v3cand(4)], settings=s)
    s.recommend_defer_keys = ("nothing",)
    with pytest.raises(ProfileError, match="알 수 없는 항목"):
        run3([v3cand(4)], settings=s)


def test_regular_terms_environment_setting(monkeypatch):
    monkeypatch.setenv("RECOMMEND_REGULAR_TERMS", "6")
    assert Settings.from_env().recommend_regular_terms == 6
    monkeypatch.delenv("RECOMMEND_REGULAR_TERMS")
    assert Settings.from_env().recommend_regular_terms == 8


# ================================================================== 20건 실제 판정 결과 반영 (9058 재(휴)학, 9088 비수도권, 9141 중국)
@pytest.mark.parametrize("status, required, want", [
    ("ENROLLED", ["재학", "휴학"], PASS), ("LEAVE", ["재학", "휴학"], PASS), ("GRADUATED", ["재학", "휴학"], FAIL), (None, ["재학", "휴학"], UNKNOWN),   # 재학과 휴학은 동시에 될 수 없다: 둘 중 하나
    ("ENROLLED", ["재학"], PASS), ("LEAVE", ["재학"], FAIL), ("ENROLLED", ["휴학"], FAIL),
    ("ENROLLED", ["재학", "입학예정"], PASS), ("LEAVE", ["재학", "입학예정"], UNKNOWN),                   # 알 수 없는 표현이 확정된 충족을 깨지 않지만, 확정 미달도 막는다
    ("ENROLLED", ["입학예정"], UNKNOWN), ("GRADUATED", ["재학", "졸업"], PASS),
])
def test_required_status_means_any_of_the_listed_statuses(status, required, want):
    c = one(M.evaluate(notice(enrollment={"required_status": required}), prof(status=status)), "enrollment")
    assert c.status == want, c.message


def test_v2_still_reads_the_required_status_list_as_all_of_them():
    """v2 판정은 바뀌지 않는다 (v3 에서만 '둘 중 하나'로 읽음)."""
    ev = M.evaluate({"schema_version": 2, "enrollment": {"required_status": ["재학", "휴학"]}}, prof(status="ENROLLED"))
    assert sorted(c.status for c in ev.common if c.key == "enrollment") == [PASS, UNKNOWN]


def test_required_status_messages_and_graduating_phrase():
    fail = one(M.evaluate(notice(enrollment={"required_status": ["재학", "휴학"]}), prof(status="GRADUATED")), "enrollment")
    assert fail.message == "학적 상태가 대상이 아님 (필요: 재학 또는 휴학, 학생: 졸업·수료)"
    assert one(M.evaluate(notice(enrollment={"required_status": ["졸업예정"]}), prof(semesters_completed=7)), "enrollment").status == PASS          # 이수 7 -> 졸업학기
    assert one(M.evaluate(notice(enrollment={"required_status": ["졸업예정"]}), prof(semesters_completed=5)), "enrollment").status == FAIL
    assert M.evaluate(notice(enrollment={"required_status": ["졸업예정"]}), prof(semesters_completed=7)).assumptions                              # 계산값을 썼음을 알림


def test_remote_requirement_excluded_status_and_remaining_terms_are_kept():
    en = {"required_status": ["재학", "사이버대학 재학생"], "excluded_status": ["휴학생"], "min_remaining_semesters": 1}
    ev = M.evaluate(notice(enrollment=en), prof(status="ENROLLED", school_type="GENERAL", semesters_completed=3))
    msgs = {c.status: c.message for c in ev.common if c.key == "enrollment"}
    assert FAIL in msgs and "원격·사이버대학" in msgs[FAIL]                                                      # 학교 종류 요건은 따로 판정
    ok = M.evaluate(notice(enrollment={"required_status": ["재학"], "excluded_status": ["휴학생"], "min_remaining_semesters": 2}), prof(status="ENROLLED", semesters_completed=3))
    assert ok.verdict == MATCH and len([c for c in ok.common if c.key == "enrollment"]) == 3


def schooled(**kw):
    return prof(school_sido="경기도", school_sigungu="시흥시", school_type="GENERAL", **kw)


def test_school_location_by_region_group():
    n = notice(**reg("ANY", req("SCHOOL_LOCATION", "비수도권")))
    c = one(M.evaluate(n, schooled()), "region")
    assert c.status == FAIL and "지역 조건 미달 (필요: [학교 소재지 비수도권])" in c.message
    assert one(M.evaluate(n, prof(school_sido="부산광역시", school_sigungu="해운대구")), "region").status == PASS
    unset = one(M.evaluate(n, prof()), "region")
    assert unset.status == UNKNOWN and "학교 소재지 설정이 없음 (.env 의 RECOMMEND_SCHOOL_SIDO / RECOMMEND_SCHOOL_SIGUNGU)" in unset.message


def test_school_location_abroad_and_specific_city():
    abroad = notice(**reg("ANY", req("SCHOOL_LOCATION", "중국")))
    assert one(M.evaluate(abroad, schooled()), "region").status == FAIL
    seoul = notice(**reg("ANY", req("SCHOOL_LOCATION", "서울특별시")))
    assert one(M.evaluate(seoul, schooled()), "region").status == FAIL and one(M.evaluate(seoul, prof(school_sido="서울특별시", school_sigungu="성북구")), "region").status == PASS


def test_realistic_9088_9141_9058_are_rejected_for_a_school_in_siheung_and_conditional_without_school_settings():
    notices = {
        "9088": notice(**reg("ANY", req("SCHOOL_LOCATION", "비수도권")), variants=[{"name": "신규선발"}]),
        "9141": notice(**reg("ANY", req("SCHOOL_LOCATION", "중국")), enrollment={"required_status": ["재학", "입학예정"]}, gpa={"min": 85, "scale": 100, "basis": "CUMULATIVE"}),
        "9058": notice(**reg("ANY", req("SCHOOL_LOCATION", "서울특별시")), enrollment={"required_status": ["재학", "휴학"]}, gpa={"min": 85, "scale": 100, "basis": "CUMULATIVE"}),
    }
    for idx, n in notices.items():
        assert M.evaluate(n, schooled(status="ENROLLED")).verdict == NO_MATCH, idx                              # 학교가 경기 시흥이면 이 장학은 대상이 아님
        ev = M.evaluate(n, prof(status="ENROLLED"))                                                           # 학교 설정이 없으면 단정하지 않음
        assert ev.verdict == CONDITIONAL and any("학교 소재지 설정이 없음" in m for m in ev.to_confirm), idx
    ev58 = M.evaluate(notices["9058"], prof(status="ENROLLED"))
    assert one(ev58, "enrollment").status == PASS                                                             # 재(휴)학 문제가 사라짐: 더 이상 '학적 조건 확인: 휴학'이 남지 않음


# ================================================================== 4-B1: 아직 판정하지 않는 자격 조건은 소득 확장뿐이다
@pytest.mark.parametrize("extra", [{"income": {"max_median_percent": 130, "basis": "MEDIAN_PERCENT"}}, {"income": {"max_asset_krw": 100000000}}, {"income": {"basis": "NARRATIVE"}}])
def test_income_extensions_are_still_never_ignored(extra):
    ev = M.evaluate(notice(**extra), prof(gpa_last=3.9))
    pend = [c for c in ev.common if c.key == "pending"]
    assert pend and pend[0].status == UNKNOWN and "소득 조건(중위소득·재산·직업 기준)" in pend[0].message and ev.verdict == CONDITIONAL
    assert M.evaluate(notice(), prof(gpa_last=3.9)).verdict == MATCH


def test_variant_level_nationality_gender_and_special_are_judged():
    n = notice(variants=[{"name": "외국인 유학생", "nationality": "외국인"}, {"name": "남학생", "gender": "남"}, {"name": "수급자", "special_groups": [{"code": "BASIC_LIVELIHOOD", "mode": "REQUIRED"}]},
                         {"name": "그냥"}])
    ev = M.evaluate(n, prof(nationality="KR", gender="M", special_complete=True, special_codes=[]))
    by = {v.name: v for v in ev.variants}
    assert {k: v.verdict for k, v in by.items()} == {"외국인 유학생": NO_MATCH, "남학생": MATCH, "수급자": NO_MATCH, "그냥": CONDITIONAL}
    assert any("국적 조건 미해당" in m for m in by["외국인 유학생"].fails) and any("신분 조건 미해당" in m for m in by["수급자"].fails)
    assert ev.verdict == MATCH and [v.name for v in ev.matched_variants] == ["남학생", "그냥"]
    unknown = {v.name: v.verdict for v in M.evaluate(n, prof()).variants}
    assert unknown["남학생"] == CONDITIONAL and unknown["수급자"] == CONDITIONAL                                  # 모르면 탈락시키지 않음


def test_exclusion_items_are_notes_but_status_exclusions_and_eligibility_text_still_decide():
    n = notice(exclusion_items=[{"type": "CONCURRENT_TUITION", "text": "본인 부담 등록금이 1만원 미만인 자 선발 제외"}, {"type": "OTHER", "text": "휴학생은 선발에서 제외"}],
               unmodeled_conditions=[{"text": "봉사 활동 증빙", "section": "신청자격", "source": "본문", "kind": "ELIGIBILITY"}])
    leave = M.evaluate(n, prof(status="LEAVE"))
    assert one(leave, "exclusion").status == FAIL and leave.verdict == NO_MATCH                                    # 학적 상태 문구는 판정
    ok = M.evaluate(n, prof(status="ENROLLED"))
    assert [c.key for c in ok.common if c.status == UNKNOWN] == ["other"] and ok.verdict == CONDITIONAL            # 자격 문장은 확인 항목, 중복수혜 제한은 판정에 넣지 않음
    assert [x["text"] for x in ok.notes] == ["본인 부담 등록금이 1만원 미만인 자 선발 제외"] and ok.notes[0]["label"] == "제외·중복수혜 제한"
    only_notes = M.evaluate(notice(exclusion_items=[{"type": "CONCURRENT_ANY", "text": "타 장학금 중복 수혜 불가"}]), prof())
    assert only_notes.verdict == MATCH and only_notes.notes                                                       # 판정에 영향 없음: 다른 조건이 모두 충족이면 충족


# ================================================================== 4-B1: 신입생 성적 면제 (공고에 면제 문구가 없어도)
def new_student(**kw):
    return prof(semesters_completed=0, **kw)


def test_new_students_are_exempt_from_grade_conditions_without_any_waiver_text():
    n = notice(**gpa(min=3.5), credits={"min_last_semester": 12})
    ev = M.evaluate(n, new_student())
    assert one(ev, "gpa").message == "신입생: 직전학기 성적이 없어 성적 조건 면제 (기준 3.5/4.5 이상)" and one(ev, "gpa").status == PASS
    assert one(ev, "credits").status == PASS and ev.verdict == MATCH
    assert M.evaluate(n, prof(semesters_completed=1)).verdict == CONDITIONAL                                       # 1학기를 마쳤으면 신입생이 아님 (성적 정보가 없으면 확인 필요)
    assert M.evaluate(n, prof(semesters_completed=1, gpa_last=2.0, credits_last=9)).verdict == NO_MATCH


@pytest.mark.parametrize("kw, want", [(dict(semesters_completed=0), True), (dict(semesters_completed=0, admission_type="NEW"), True), (dict(semesters_completed=0, admission_type="TRANSFER"), False),
                                      (dict(semesters_completed=3), False), ({}, None)])
def test_who_is_a_new_student(kw, want):
    from scholarship.recommend.conditions_v3 import is_new_student
    assert is_new_student(StudentProfile(**kw)) is want


def test_new_student_exemption_does_not_cover_entrance_scores_terms_or_unknown_status():
    ent = M.evaluate(notice(**gpa(basis="ENTRANCE", min=3.0)), new_student())
    assert one(ent, "gpa").status == UNKNOWN and "입학성적" in one(ent, "gpa").message                              # 입학성적 기준은 면제가 아니라 확인 필요
    terms = M.evaluate(notice(credits={"min_completed_semesters": 3}), new_student())
    assert one(terms, "terms").status == FAIL and terms.verdict == NO_MATCH                                       # 이수 학기 수는 성적 조건이 아님
    assert one(M.evaluate(notice(**gpa(min=3.5)), prof()), "gpa").status == UNKNOWN                               # 이수학기를 모르면 면제하지 않음
    assert one(M.evaluate(notice(**gpa(min=3.5)), prof(semesters_completed=0, admission_type="TRANSFER")), "gpa").status == UNKNOWN   # 편입생은 신입생이 아님


def test_new_student_in_a_variant_and_with_last_n_average():
    n = notice(variants=[{"name": "평균", "gpa": {"min": 3.5, "scale": 4.5, "basis": "LAST_N", "terms": 2}}])
    assert one(M.evaluate(n, new_student()), "gpa", "평균").status == PASS


# ================================================================== 4-B1: 면제(waivers)
def waiver(student=None, special=None, skip=("gpa", "credits")):
    when = {}
    if student:
        when["student"] = list(student)
    if special:
        when["special"] = list(special)
    return {"when": when, "skip": list(skip)}


def test_explicit_waivers_for_new_students_transfers_and_special_groups():
    n = notice(**gpa(min=4.4), credits={"min_last_semester": 20}, waivers=[waiver(["NEW_STUDENT", "TRANSFER"]), waiver(special=["DISABLED"])])
    low = dict(gpa_last=3.0, credits_last=10, semesters_completed=7)
    assert M.evaluate(n, prof(admission_type="NEW", special_complete=True, special_codes=[], **low)).verdict == NO_MATCH            # 면제 대상이 아님
    tr = M.evaluate(n, prof(admission_type="TRANSFER", **low))
    assert one(tr, "gpa").message == "성적 조건 면제 (신입생·편입생)" and one(tr, "credits").status == PASS and tr.verdict == MATCH
    dis = M.evaluate(n, prof(admission_type="NEW", special_codes=["DISABLED"], special_complete=True, **low))
    assert one(dis, "gpa").message == "성적 조건 면제 (장애)" and dis.verdict == MATCH
    new = M.evaluate(n, new_student())
    assert one(new, "gpa").status == PASS and new.verdict == MATCH


def test_waivers_that_may_apply_do_not_reject_but_known_misses_do():
    n = notice(**gpa(min=4.4), waivers=[waiver(["REENTRY", "RETURNING"], skip=["gpa"])])
    ev = M.evaluate(n, prof(gpa_last=3.0, semesters_completed=7, admission_type="NEW"))
    c = one(ev, "gpa")
    assert c.status == UNKNOWN and "면제 대상일 수 있음: 재입학생·복학생 - 확인 필요" in c.message and ev.verdict == CONDITIONAL        # 재입학·복학은 입력 항목이 없다
    sp = notice(**gpa(min=4.4), waivers=[waiver(special=["DISABLED"], skip=["gpa"])])
    assert M.evaluate(sp, prof(gpa_last=3.0, special_complete=True, special_codes=[])).verdict == NO_MATCH           # 신분에 모두 답했고 해당 없음: 면제 아님
    assert one(M.evaluate(sp, prof(gpa_last=3.0)), "gpa").status == UNKNOWN                                          # 신분을 모르면 면제일 수 있음


def test_waivers_cover_income_and_region_and_keep_other_items():
    n = notice(**reg("ANY", req("RESIDENCE_SELF", "서울특별시 서초구")), credits={"min_last_semester": 18}, waivers=[waiver(["TRANSFER"], skip=["region"])])
    ev = M.evaluate(n, prof(**SUWON, credits_last=12, admission_type="TRANSFER", semesters_completed=3))
    assert one(ev, "region").message == "지역 조건 면제 (편입생)" and one(ev, "credits").status == FAIL and ev.verdict == NO_MATCH      # 학점은 면제 대상이 아님
    inc = notice(income={"max_bracket": 3}, waivers=[waiver(["NEW_STUDENT"], skip=["income"])])
    assert one(M.evaluate(inc, new_student(income_bracket=8)), "income").message == "소득 조건 면제 (신입생)"


def test_waivers_apply_to_variants_too():
    n = notice(variants=[{"name": "A", "gpa": {"min": 4.4, "scale": 4.5, "basis": "LAST_SEMESTER"}}], waivers=[waiver(["TRANSFER"], skip=["gpa"])])
    ev = M.evaluate(n, prof(gpa_last=3.0, admission_type="TRANSFER", semesters_completed=3))
    assert one(ev, "gpa", "A").status == PASS and ev.verdict == MATCH
    assert M.evaluate(n, prof(gpa_last=3.0, admission_type="NEW", semesters_completed=3)).verdict == NO_MATCH


@pytest.mark.parametrize("mode, admission, want_gpa", [("NO_GPA_REQUIRED", "TRANSFER", PASS), ("NO_GPA_REQUIRED", "NEW", FAIL), ("NO_GPA_REQUIRED", None, UNKNOWN),
                                                      ("USE_PREV_SCHOOL", "TRANSFER", UNKNOWN), ("USE_PREV_SCHOOL", "NEW", FAIL), ("ALLOWED", "TRANSFER", FAIL)])
def test_transfer_mode_effects_on_grade_conditions(mode, admission, want_gpa):
    n = notice(**gpa(min=4.4), transfer={"mode": mode})
    p = prof(gpa_last=3.0, semesters_completed=3, **({"admission_type": admission} if admission else {}))
    assert one(M.evaluate(n, p), "gpa").status == want_gpa


# ================================================================== 4-B1: 필수 신분
def test_required_special_groups_are_alternatives_and_depend_on_how_much_the_student_answered():
    n = notice(special_groups=[{"code": "BASIC_LIVELIHOOD", "mode": "REQUIRED"}, {"code": "NEAR_POOR", "mode": "REQUIRED"}, {"code": "SINGLE_PARENT", "mode": "REQUIRED"}])
    assert one(M.evaluate(n, prof(special_codes=["NEAR_POOR"], special_complete=True)), "special").message == "신분 조건 해당 (차상위계층)"
    miss = one(M.evaluate(n, prof(special_codes=[], special_complete=True)), "special")
    assert miss.status == FAIL and miss.message == "신분 조건 미해당 (필요: 기초생활수급자 / 차상위계층 / 한부모가정 중 하나)"
    unknown = one(M.evaluate(n, prof()), "special")
    assert unknown.status == UNKNOWN and unknown.message.startswith("신분 확인 필요")
    partial = prof(special_codes=["MULTI_CHILD"], special_complete=False)                                        # 일부만 답했으면 '해당 없음'을 단정하지 않음
    assert one(M.evaluate(n, partial), "special").status == UNKNOWN
    flags = prof(is_basic_livelihood=False, is_near_poor=False, is_single_parent=False)                           # 개별 항목에 모두 '아니오'
    assert one(M.evaluate(n, flags), "special").status == FAIL


def test_special_groups_without_a_student_input_item_are_left_for_confirmation():
    n = notice(special_groups=[{"code": "WORKER_CHILD", "mode": "REQUIRED"}])
    assert one(M.evaluate(n, prof(special_codes=[], special_complete=True)), "special").status == UNKNOWN          # 근로자 자녀는 학생에게 묻지 않는 항목
    both = notice(special_groups=[{"code": "WORKER_CHILD", "mode": "REQUIRED"}, {"code": "DISABLED", "mode": "REQUIRED"}])
    assert one(M.evaluate(both, prof(special_codes=["DISABLED"], special_complete=True)), "special").status == PASS


def test_special_scope_note_for_disability_and_veterans():
    n = notice(special_groups=[{"code": "DISABLED", "scope": "SELF", "mode": "REQUIRED"}])
    assert "[본인·가족 구분은 확인 필요]" in one(M.evaluate(n, prof(special_codes=["DISABLED"], special_complete=True)), "special").message
    fam = notice(special_groups=[{"code": "DISABLED", "scope": "FAMILY", "mode": "REQUIRED"}])
    assert "본인·가족" not in one(M.evaluate(fam, prof(special_codes=["DISABLED"], special_complete=True)), "special").message


def test_special_and_income_together_decide_only_when_both_readings_agree():
    """'기초수급자·차상위 or 소득분위 1~4구간'을 AI 가 신분(필수)과 소득 구간으로 나눠 적은 경우: 두 해석에서 같은 결과일 때만 단정한다."""
    n = notice(special_groups=[{"code": "BASIC_LIVELIHOOD", "mode": "REQUIRED"}, {"code": "NEAR_POOR", "mode": "REQUIRED"}], income={"max_bracket": 4})
    yes = dict(special_codes=["NEAR_POOR"], special_complete=True)
    no = dict(special_codes=[], special_complete=True)
    both = one(M.evaluate(n, prof(income_bracket=2, **yes)), "special")
    assert both.status == PASS and not [c for c in M.evaluate(n, prof(income_bracket=2, **yes)).common if c.key == "income"]        # 합쳐서 한 항목
    assert one(M.evaluate(n, prof(income_bracket=8, **no)), "special").status == FAIL
    mixed = one(M.evaluate(n, prof(income_bracket=2, **no)), "special")                                           # 소득은 맞고 신분은 아님: 거짓 탈락시키지 않음
    assert mixed.status == UNKNOWN and "결합('또는'/'그리고')이 공고에 불명확해 한쪽만 충족이면 확인 필요" in mixed.message
    assert one(M.evaluate(n, prof(income_bracket=8, **yes)), "special").status == UNKNOWN


def test_variant_level_special_is_and_with_the_common_income():
    n = notice(income={"max_bracket": 9}, variants=[{"name": "수급자", "special_groups": [{"code": "BASIC_LIVELIHOOD", "mode": "REQUIRED"}]}, {"name": "일반"}])
    ev = M.evaluate(n, prof(income_bracket=3, special_codes=[], special_complete=True))
    assert one(ev, "income").status == PASS                                                                       # 공통 소득 조건은 따로 남음
    assert {v.name: v.verdict for v in ev.variants} == {"수급자": NO_MATCH, "일반": CONDITIONAL}


# ================================================================== 4-B1: 나이·성별·입학년도·편입·학교 종류·학과 계열
@pytest.mark.parametrize("age, student, want", [({"max": 29}, 24, PASS), ({"max": 29}, 29, PASS), ({"max": 29}, 30, FAIL), ({"min": 19}, 18, FAIL), ({"min": 19, "max": 25}, 22, PASS),
                                                ({"max": 29}, None, UNKNOWN)])
def test_age_condition(age, student, want):
    c = one(M.evaluate(notice(age=age), prof(**({"age": student} if student is not None else {}))), "age")
    assert c.status == want, c.message


def test_age_messages():
    assert one(M.evaluate(notice(age={"max": 29}), prof(age=30)), "age").message == "만 30세 (만 29세 이하 미달)"
    assert one(M.evaluate(notice(age={"min": 19, "max": 25}), prof(age=22)), "age").message == "만 22세 (만 19~25세 충족)"
    assert one(M.evaluate(notice(age={"max": 29}), prof()), "age").message == "나이 정보 없음 (기준: 만 29세 이하)"


@pytest.mark.parametrize("notice_gender, student, want", [("F", "F", PASS), ("F", "M", FAIL), ("M", "F", FAIL), ("M", "M", PASS), ("F", None, UNKNOWN)])
def test_gender_condition(notice_gender, student, want):
    c = one(M.evaluate(notice(gender=notice_gender), prof(**({"gender": student} if student else {}))), "gender")
    assert c.status == want


@pytest.mark.parametrize("years, student, want", [([2026], 2026, PASS), ([2026], 2021, FAIL), ([2025, 2026], 2025, PASS), ([2026], None, UNKNOWN)])
def test_entry_year_condition(years, student, want):
    assert one(M.evaluate(notice(entry_years=years), prof(**({"entry_year": student} if student else {}))), "entry").status == want


@pytest.mark.parametrize("admission, want", [("TRANSFER", FAIL), ("NEW", PASS), (None, UNKNOWN)])
def test_transfer_excluded_condition(admission, want):
    p = prof(**({"admission_type": admission} if admission else {}))
    assert one(M.evaluate(notice(transfer={"mode": "EXCLUDED"}), p), "transfer").status == want
    assert not [c for c in M.evaluate(notice(transfer={"mode": "ALLOWED"}), p).common if c.key == "transfer"]


@pytest.mark.parametrize("school, school_type, want", [
    ({"types": ["UNIV4"]}, "GENERAL", PASS), ({"types": ["COLLEGE"]}, "GENERAL", FAIL), ({"types": ["UNIV4", "COLLEGE"]}, "GENERAL", PASS), ({"types": ["UNIV4"]}, "COLLEGE", FAIL),
    ({"types": ["UNIV4"]}, "REMOTE", UNKNOWN), ({"types": ["UNIV4"]}, None, UNKNOWN),
    ({"excluded_types": ["CYBER"]}, "GENERAL", PASS), ({"excluded_types": ["CYBER", "OPEN_UNIV", "COMPANY_UNIV"]}, "GENERAL", PASS),         # 일반대학·전문대학은 사이버·방송통신·사내대학이 아님
    ({"excluded_types": ["CYBER"]}, "COLLEGE", PASS), ({"excluded_types": ["CYBER", "OPEN_UNIV"]}, "REMOTE", FAIL),
    ({"excluded_types": ["CYBER"]}, "REMOTE", UNKNOWN), ({"excluded_types": ["COMPANY_UNIV"]}, "REMOTE", PASS), ({"excluded_types": ["CYBER"]}, "OTHER", UNKNOWN),
    ({"types": ["UNIV4"], "excluded_types": ["CYBER"]}, "GENERAL", PASS), ({"types": ["UNIV4"], "excluded_types": ["CYBER", "OPEN_UNIV"]}, "COLLEGE", FAIL)])
def test_school_type_condition_uses_the_server_setting(school, school_type, want):
    c = one(M.evaluate(notice(school=school), prof(**({"school_type": school_type} if school_type else {}))), "school")
    assert c.status == want, c.message
    if school_type is None:
        assert "RECOMMEND_SCHOOL_TYPE" in c.message


@pytest.mark.parametrize("groups, student, want", [(["ENGINEERING"], "ENGINEERING", PASS), (["ENGINEERING", "NATURAL"], "NATURAL", PASS), (["ENGINEERING"], "SOCIAL", FAIL),
                                                  (["MEDICAL"], "ENGINEERING", FAIL), (["ENGINEERING"], None, UNKNOWN)])
def test_major_group_condition(groups, student, want):
    c = one(M.evaluate(notice(major_groups=groups), prof(**({"major_group": student} if student else {}))), "major_group")
    assert c.status == want, c.message


def test_major_group_messages_and_variants():
    assert one(M.evaluate(notice(major_groups=["ENGINEERING", "NATURAL"]), prof(major_group="ENGINEERING")), "major_group").message == "학과 계열 공학 해당 해당 (공고 대상: 공학 / 자연)".replace("해당 해당", "해당")
    assert one(M.evaluate(notice(major_groups=["MEDICAL"]), prof(major_group="ENGINEERING")), "major_group").message == "학과 계열 공학 대상 아님 (공고 대상: 의약)"
    n = notice(variants=[{"name": "공학", "major_groups": ["ENGINEERING"]}, {"name": "의약", "major_groups": ["MEDICAL"]}])
    assert {v.name: v.verdict for v in M.evaluate(n, prof(major_group="ENGINEERING")).variants} == {"공학": MATCH, "의약": NO_MATCH}


# ================================================================== 4-B1: 별도 표기 (판정에 영향 없음)
def test_prerequisites_are_listed_separately_and_never_change_the_verdict():
    n = notice(prerequisites=[{"type": "NATIONAL_SCHOLARSHIP_APPLIED", "text": "해당 학기 신청자"}, {"type": "BRACKET_CALCULATED", "text": "지원구간 산정 완료"},
                              {"type": "DEGREE_HELD", "text": "학사 학위 소지"}, {"type": "EXCHANGE_SELECTED", "text": "교환학생 선정"}])
    def st(**kw):
        ev = M.evaluate(n, prof(**kw))
        assert ev.verdict == MATCH and not [c for c in ev.common if c.status == UNKNOWN]                         # 선행 조건은 판정에 쓰이지 않는다
        return {x["type"]: x["status"] for x in ev.prerequisites}
    assert st() == {"NATIONAL_SCHOLARSHIP_APPLIED": "UNKNOWN", "BRACKET_CALCULATED": "UNKNOWN", "DEGREE_HELD": "UNKNOWN", "EXCHANGE_SELECTED": "UNKNOWN"}
    assert st(national_scholarship_applied=False, income_bracket=3, degree_held=False) == {"NATIONAL_SCHOLARSHIP_APPLIED": "TODO", "BRACKET_CALCULATED": "MET", "DEGREE_HELD": "UNMET",
                                                                                          "EXCHANGE_SELECTED": "UNKNOWN"}
    assert st(national_scholarship_applied=True, degree_held=True)["NATIONAL_SCHOLARSHIP_APPLIED"] == "MET" and st(degree_held=True)["DEGREE_HELD"] == "MET"
    first = M.evaluate(n, prof()).prerequisites[0]
    assert first == {"type": "NATIONAL_SCHOLARSHIP_APPLIED", "label": "국가장학금 신청", "text": "해당 학기 신청자", "status": "UNKNOWN"}


def test_preferred_special_groups_are_listed_as_preferences_not_conditions():
    n = notice(special_groups=[{"code": "BASIC_LIVELIHOOD", "mode": "PREFERRED"}, {"code": "MULTI_CHILD", "mode": "PREFERRED"}],
               variants=[{"name": "A", "target_levels": ["UNIV"], "special_groups": [{"code": "NEAR_POOR", "mode": "PREFERRED"}]}])
    ev = M.evaluate(n, prof(special_codes=["MULTI_CHILD"], special_complete=True))
    assert ev.verdict == MATCH and [(x["label"], x["applies"]) for x in ev.preferences] == [("기초생활수급자", False), ("다자녀가정", True)]
    assert [(x["label"], x["applies"]) for x in ev.variants[0].preferences] == [("차상위계층", False)]
    assert [x["applies"] for x in M.evaluate(n, prof()).preferences] == [None, None]                               # 모르면 모름
    assert M.evaluate(notice(), prof()).preferences == []


def test_school_programs_show_whether_the_school_participates_without_changing_the_verdict():
    n = notice(school={"programs": ["NATIONAL_SCHOLARSHIP", "LOCAL_TALENT"]})
    def sp(**kw):
        ev = M.evaluate(n, prof(**kw))
        assert ev.verdict == MATCH
        return [(x["label"], x["school"]) for x in ev.school_programs]
    assert sp() == [("국가장학금 참여대학", "UNKNOWN"), ("지역인재장학금 참여대학", "UNKNOWN")]                         # 설정 없음
    assert sp(school_programs=["NATIONAL_SCHOLARSHIP"]) == [("국가장학금 참여대학", "PARTICIPATES"), ("지역인재장학금 참여대학", "NOT")]
    assert sp(school_programs=[]) == [("국가장학금 참여대학", "NOT"), ("지역인재장학금 참여대학", "NOT")]


def test_notes_collect_obligations_review_recommendation_and_procedure_without_duplicates():
    n = notice(obligations=["장학증서 수여식 필참"], review_criteria=["자기소개서"], recommendation={"required": True, "from": ["PROFESSOR", "PRINCIPAL"]},
               exclusion_items=[{"type": "PRIOR_SAME", "text": "기수혜자 제외"}],
               unmodeled_conditions=[{"text": "장학증서 수여식 필참", "section": "활동사항", "source": "본문", "kind": "OBLIGATION"},
                                     {"text": "혜택은 4학기까지", "section": "장학혜택", "source": "본문", "kind": "BENEFIT_LIMIT"},
                                     {"text": "서류는 우편 제출", "section": "접수", "source": "본문", "kind": "PROCEDURE"}])
    ev = M.evaluate(n, prof())
    assert ev.verdict == MATCH
    assert [(x["kind"], x["text"]) for x in ev.notes] == [("EXCLUSION", "기수혜자 제외"), ("BENEFIT_LIMIT", "혜택은 4학기까지"), ("PROCEDURE", "서류는 우편 제출"),
                                                         ("OBLIGATION", "장학증서 수여식 필참"), ("REVIEW", "자기소개서"), ("RECOMMENDATION", "추천서 필요 (지도교수·학교장)")]


def test_variants_carry_their_own_application_dates():
    n = notice(variants=[{"name": "대학생", "apply_start": "2026-09-01", "apply_end": "2026-09-08"}, {"name": "과학", "apply_end": "2026-10-07"}, {"name": "기타"}])
    ev = M.evaluate(n, prof())
    assert [(v.name, v.apply_start, v.apply_end) for v in ev.variants] == [("대학생", "2026-09-01", "2026-09-08"), ("과학", None, "2026-10-07"), ("기타", None, None)]
    assert M.evaluate({"schema_version": 2, "variants": [{"name": "A"}]}, prof()).variants[0].apply_end is None


# ================================================================== 20건 실제 모양의 시나리오 (4-B1)
def student_from(text):
    return profile_from_text(text)[0].profile


def test_realistic_remote_region_notice():
    n = notice(**IDEAL["9096"])
    suwon = M.evaluate(n, student_from(SAMPLE))
    assert suwon.verdict == NO_MATCH and "지역 조건 미달" in suwon.fail_reasons[0]
    geochang = SAMPLE.replace("본인 주민등록: 경기도 수원시", "본인 주민등록: 경상남도 거창군")
    ev = M.evaluate(n, student_from(geochang))
    assert ev.verdict == MATCH and [c.key for c in ev.common if c.status == PASS] == ["levels", "gpa", "credits", "region", "age"]                   # 나이(만 24세 <= 29)까지 판정
    assert [(x["label"], x["status"]) for x in ev.prerequisites] == [("국가장학금 신청", "UNKNOWN")] and [x["school"] for x in ev.school_programs] == ["UNKNOWN"]
    assert [x["kind"] for x in ev.notes] == ["EXCLUSION"]
    old = student_from(geochang.replace("만 나이: 24", "만 나이: 31"))
    assert M.evaluate(n, old).verdict == NO_MATCH and "만 31세" in M.evaluate(n, old).fail_reasons[0]


def test_realistic_required_special_notice():
    n = notice(**IDEAL["9098"])
    ev = M.evaluate(n, student_from(SAMPLE))
    assert ev.verdict == NO_MATCH and ev.fail_reasons[0] == "신분 조건 미해당 (필요: 장애 중 하나)"
    assert [(x["label"], x["applies"]) for x in ev.preferences] == [("기초생활수급자", False), ("차상위계층", False)]
    disabled = student_from(SAMPLE.replace("해당 항목: 해당 없음", "해당 항목: 장애, 차상위"))
    ok = M.evaluate(n, disabled)
    assert ok.verdict == CONDITIONAL and [(x["label"], x["applies"]) for x in ok.preferences] == [("기초생활수급자", False), ("차상위계층", True)]


def test_realistic_national_scholarship_with_variants_and_waivers():
    n = notice(**dict(NATIONAL_LIKE, apply_end="2026-10-08"))
    ev = M.evaluate(n, student_from(SAMPLE))
    assert ev.verdict == CONDITIONAL and one(ev, "income").status == PASS
    assert {v.name: v.verdict for v in ev.variants} == {"국가장학금Ⅰ·다자녀유형": CONDITIONAL, "수급자·차상위": NO_MATCH}                           # 수급자·차상위 신분이 아님
    low = student_from(SAMPLE + "- 직전학기 백분위: 70\n")
    by = {v.name: v for v in M.evaluate(n, low).variants}
    assert by["국가장학금Ⅰ·다자녀유형"].verdict == CONDITIONAL and any("면제 대상일 수 있음: 신입생·편입생·재입학생" in c.message for c in by["국가장학금Ⅰ·다자녀유형"].checks)   # 재입학 여부를 모름
    first = student_from(SAMPLE.replace("이수학기: 7", "이수학기: 0") + "- 직전학기 백분위: 70\n")
    assert {v.name: v.verdict for v in M.evaluate(n, first).variants}["국가장학금Ⅰ·다자녀유형"] == MATCH                                          # 신입생: 성적 면제


# ================================================================== 조건이 하나도 없는 유형은 '충족'이 아니라 '확인 필요' (20건 실제 판정에서 발견: 9195·9210·9071)
NAME_ONLY_MESSAGE = "이 유형의 선발 조건이 공고 본문에 없음 (공고문 확인 필요)"


def name_only_checks(ev, name):
    return [c for v in ev.variants if v.name == name for c in v.checks if c.message == NAME_ONLY_MESSAGE]


def test_a_variant_without_any_structured_condition_is_unknown_not_a_match():
    n = notice(variants=[{"name": "주소이전", "apply_end": "2026-10-07"}, {"name": "성적", "gpa": {"min": 3.0, "scale": 4.5, "basis": "LAST_SEMESTER"}}])
    ev = M.evaluate(n, prof(gpa_last=3.5))
    by = {v.name: v for v in ev.variants}
    assert (by["주소이전"].verdict, by["성적"].verdict) == (CONDITIONAL, MATCH)
    assert [(c.key, c.status, c.message) for c in by["주소이전"].checks] == [("other", UNKNOWN, NAME_ONLY_MESSAGE)]
    assert ev.verdict == MATCH and [v.name for v in ev.matched_variants] == ["성적", "주소이전"]                 # 조건이 확인된 유형이 충족이면 전체는 충족, 조건 미상 유형은 조건부로 구분


def test_notices_whose_variants_all_lack_conditions_are_conditional():
    n = notice(variants=[{"name": "장학생", "amount": "100만원"}, {"name": "특기생", "apply_start": "2026-09-01", "apply_end": "2026-10-07"}])
    ev = M.evaluate(n, prof())
    assert {v.name: v.verdict for v in ev.variants} == {"장학생": CONDITIONAL, "특기생": CONDITIONAL} and ev.verdict == CONDITIONAL      # 금액·날짜는 조건이 아니다
    assert all(name_only_checks(ev, v.name) for v in ev.variants)


@pytest.mark.parametrize("extra, student", [
    ({"gpa": {"min": 3.0, "scale": 4.5, "basis": "LAST_SEMESTER"}}, dict(gpa_last=3.5)), ({"credits": {"min_last_semester": 12}}, dict(credits_last=15)),
    ({"credits": {"min_completed_semesters": 2}}, dict(semesters_completed=7)), ({"income": {"max_bracket": 5}}, dict(income_bracket=3)),
    ({"income": {"basis": "NARRATIVE"}}, {}), ({"region": {"mode": "ANY", "requirements": [req("RESIDENCE_SELF", "경기도 수원시")]}}, SUWON),
    ({"special_groups": [{"code": "BASIC_LIVELIHOOD", "mode": "REQUIRED"}]}, dict(special_codes=["BASIC_LIVELIHOOD"], special_complete=True)),
    ({"target_levels": ["UNIV"]}, {}), ({"major_groups": ["ENGINEERING"]}, dict(major_group="ENGINEERING")), ({"entry_years": [2021]}, dict(entry_year=2021)),
    ({"age": {"max": 29}}, dict(age=24)), ({"gender": "남"}, dict(gender="M")), ({"nationality": "내국인"}, dict(nationality="KR")),
    ({"transfer": {"mode": "EXCLUDED"}}, dict(admission_type="NEW")), ({"completed_terms": {"min": 2}}, dict(semesters_completed=7)),
    ({"remaining_terms": {"min": 1}}, dict(semesters_completed=7)), ({"other_conditions": ["봉사 활동 증빙"]}, {})])
def test_any_structured_condition_makes_a_variant_not_name_only(extra, student):
    ev = M.evaluate(notice(variants=[dict({"name": "A"}, **extra), {"name": "B"}]), prof(**student))
    assert name_only_checks(ev, "A") == [] and len(name_only_checks(ev, "B")) == 1


def test_empty_default_values_and_common_conditions_do_not_count_as_variant_conditions():
    n = notice(**gpa(min=3.0), variants=[{"name": "A", "gpa": {"basis": "UNKNOWN"}, "region": {"mode": "UNKNOWN"}, "income": {}, "special_groups": [{"code": "NEAR_POOR", "mode": "PREFERRED"}]}])
    assert len(name_only_checks(M.evaluate(n, prof(gpa_last=3.5)), "A")) == 1                                   # 공통 조건·우대 신분·빈 기본값은 유형의 조건이 아니다


def test_realistic_national_scholarship_variants_without_conditions_are_conditional():
    n = notice(variants=[{"name": "국가장학금Ⅰ유형(일반)", "gpa": {"min": 80, "scale": 100, "basis": "LAST_SEMESTER"}},
                         {"name": "국가장학금Ⅰ유형(수급자·차상위)", "special_groups": [{"code": "BASIC_LIVELIHOOD", "mode": "REQUIRED"}, {"code": "NEAR_POOR", "mode": "REQUIRED"}]},
                         {"name": "다자녀 국가장학금", "special_groups": [{"code": "MULTI_CHILD", "mode": "REQUIRED"}]}, {"name": "국가장학금Ⅱ유형"}, {"name": "지역인재장학금"}])
    ev = M.evaluate(n, prof(gpa_last=3.8, special_codes=[], special_complete=True))
    assert {v.name: v.verdict for v in ev.variants} == {"국가장학금Ⅰ유형(일반)": CONDITIONAL, "국가장학금Ⅰ유형(수급자·차상위)": NO_MATCH, "다자녀 국가장학금": NO_MATCH,
                                                      "국가장학금Ⅱ유형": CONDITIONAL, "지역인재장학금": CONDITIONAL}              # 4학년 수도권 학생에게 '지역인재장학금 충족'이라고 하지 않는다
    assert ev.verdict == CONDITIONAL


# ================================================================== 학교 종류 문구
def test_school_type_messages_are_readable():
    only_ex = one(M.evaluate(notice(school={"excluded_types": ["CYBER", "OPEN_UNIV", "COMPANY_UNIV"]}), prof(school_type="GENERAL")), "school")
    assert only_ex.message == "학교 종류 해당 (본교: 일반대학; 공고 제외: 사이버대학 / 방송통신대학 / 사내대학)"
    both = one(M.evaluate(notice(school={"types": ["UNIV4"], "excluded_types": ["CYBER"]}), prof(school_type="GENERAL")), "school")
    assert both.message == "학교 종류 해당 (본교: 일반대학; 공고 대상: 4년제 대학, 제외: 사이버대학)"
    fail = one(M.evaluate(notice(school={"types": ["COLLEGE"]}), prof(school_type="GENERAL")), "school")
    assert fail.message == "학교 종류가 대상이 아님 (본교: 일반대학; 공고 대상: 전문대학)"


# ================================================================== 4-B2 일치 시험에서 찾은 엔진 오류
def test_a_100_point_cumulative_condition_is_never_compared_with_the_last_term_percentile():
    """9058 '전체 학년 평점 평균 백분위 85점': 직전학기 백분위로 누적 기준을 판정하면 안 된다 (거짓 탈락·거짓 충족)."""
    n = notice(gpa={"min": 85, "scale": 100, "basis": "CUMULATIVE"})
    low = M.evaluate(n, prof(gpa_cumulative=3.0, gpa_percent=70))
    c = one(low, "gpa")
    assert c.status == UNKNOWN and "100점제 기준: 100점 환산 점수(누적 백분위)가 필요함" in c.message and low.verdict == CONDITIONAL
    assert one(M.evaluate(n, prof(gpa_cumulative=4.3, gpa_percent=95)), "gpa").status == UNKNOWN                   # 직전학기 백분위가 높아도 충족으로 단정하지 않음
    last = notice(gpa={"min": 85, "scale": 100, "basis": "LAST_SEMESTER"})
    assert one(M.evaluate(last, prof(gpa_last=3.0, gpa_percent=70)), "gpa").status == FAIL and one(M.evaluate(last, prof(gpa_last=3.0, gpa_percent=88)), "gpa").status == PASS   # 직전학기 기준은 그대로


@pytest.mark.parametrize("last_gpa, cum_gpa, percent, want", [(3.5, 3.5, 90, PASS), (3.5, 3.5, 70, UNKNOWN), (3.5, None, 70, UNKNOWN), (None, 3.5, 90, UNKNOWN)])
def test_either_basis_with_a_100_point_scale_uses_the_percentile_for_the_last_term_only(last_gpa, cum_gpa, percent, want):
    """직전학기와 누적 평점이 같은 값이어도 종류를 구분한다 (같은 숫자는 같은 객체일 수 있다)."""
    kw = {"gpa_percent": percent}
    if last_gpa is not None:
        kw["gpa_last"] = last_gpa
    if cum_gpa is not None:
        kw["gpa_cumulative"] = cum_gpa
    c = one(M.evaluate(notice(gpa={"min": 85, "scale": 100, "basis": "EITHER"}), prof(**kw)), "gpa")
    assert c.status == want, c.message


GRAD_REQ = {"type": "GRADUATED_SCHOOL", "areas": ["경상남도 거창군"], "years_basis": "거창군 소재 중학교 또는 고등학교 3년 재학 후 졸업"}


def test_a_graduated_school_requirement_with_a_period_is_not_confirmed_by_the_place_alone():
    """9096 '거창군 소재 중·고교 3년 재학 후 졸업': 학교 소재지만 입력받으므로 재학 기간은 확인 필요."""
    n = notice(region={"mode": "ANY", "requirements": [GRAD_REQ]})
    c = one(M.evaluate(n, prof(hs_sido="경상남도", hs_sigungu="거창군")), "region")
    assert c.status == UNKNOWN and "기간 조건('거창군 소재 중학교 또는 고등학교 3년 재학" in c.message and "입력받지 않아 확인 필요" in c.message
    assert one(M.evaluate(n, prof(hs_sido="서울특별시", hs_sigungu="서초구")), "region").status == FAIL            # 장소가 다르면 그대로 미달
    no_number = notice(region={"mode": "ANY", "requirements": [dict(GRAD_REQ, years_basis="졸업 학교 소재지 기준")]})
    assert one(M.evaluate(no_number, prof(hs_sido="경상남도", hs_sigungu="거창군")), "region").status == PASS       # 기간 숫자가 없는 설명은 영향 없음
    numeric = notice(region={"mode": "ANY", "requirements": [dict(GRAD_REQ, min_years=3)]})
    assert one(M.evaluate(numeric, prof(hs_sido="경상남도", hs_sigungu="거창군")), "region").status == UNKNOWN       # 숫자 기간이 있어도 졸업 학교의 재학 기간은 알 수 없음
    own = notice(region={"mode": "ANY", "requirements": [req("RESIDENCE_SELF", "경상남도 거창군", years=3), GRAD_REQ]})
    assert one(M.evaluate(own, prof(residence_sido="경상남도", residence_sigungu="거창군", residence_years=3.0, residence_years_open=True)), "region").status == PASS   # 다른 요건이 충족이면 충족


def cross_notice(**extra):
    return notice(special_groups=[{"code": "BASIC_LIVELIHOOD", "mode": "REQUIRED"}, {"code": "SINGLE_PARENT", "mode": "REQUIRED"}],
                  variants=[{"name": "고등학생", "target_levels": ["HIGH"]}, {"name": "대학생", "target_levels": ["UNIV"], "income": {"max_bracket": 6}}], **extra)


@pytest.mark.parametrize("codes, bracket, want", [(["SINGLE_PARENT"], 3, MATCH), (["SINGLE_PARENT"], 8, CONDITIONAL), ([], 3, CONDITIONAL), ([], 8, NO_MATCH)])
def test_special_at_the_top_and_income_inside_a_variant_are_combined_like_the_same_level_case(codes, bracket, want):
    """9217: 신분은 공고 전체, 소득 6구간은 '대학생' 유형 안에 있어도 '또는/그리고'가 불명확하므로 한쪽만 충족이면 확인 필요 (둘 다 미달이어야 미달)."""
    ev = M.evaluate(cross_notice(), prof(special_codes=codes, special_complete=True, income_bracket=bracket))
    assert not [c for c in ev.common if c.key == "special"]                                                       # 공통에서는 판정하지 않고 유형 안에서 소득과 함께 판정
    v = next(v for v in ev.variants if v.name == "대학생")
    assert v.verdict == want and ev.verdict == want
    assert len([c for c in v.checks if c.key == "special"]) == 1 and not [c for c in v.checks if c.key == "income"]
    msg = [c for c in v.checks if c.key == "special"][0].message
    assert ("결합('또는'/'그리고')이 공고에 불명확해 한쪽만 충족이면 확인 필요" in msg) == (want == CONDITIONAL)                      # 한쪽만 충족이면 안내
    assert ("신분 조건 해당" in msg) == bool(codes) and ("소득 조건 충족" in msg) == (bracket <= 6)                                    # 어느 쪽이 충족인지 문구에 드러난다


def test_special_pushed_into_variants_still_applies_to_variants_without_income():
    ev = M.evaluate(cross_notice(), prof(level="HIGH", special_codes=[], special_complete=True))
    high = next(v for v in ev.variants if v.name == "고등학생")
    assert [c.status for c in high.checks if c.key == "special"] == [FAIL] and high.verdict == NO_MATCH            # 소득이 없는 유형은 신분을 그대로 적용
    ok = M.evaluate(cross_notice(), prof(level="HIGH", special_codes=["SINGLE_PARENT"], special_complete=True))
    assert next(v for v in ok.variants if v.name == "고등학생").verdict == MATCH


def test_special_stays_common_without_an_income_bound_in_any_variant_or_when_income_is_common():
    no_income = notice(special_groups=[{"code": "DISABLED", "mode": "REQUIRED"}], variants=[{"name": "A", "target_levels": ["UNIV"]}])
    ev = M.evaluate(no_income, prof(special_codes=[], special_complete=True))
    assert one(ev, "special").status == FAIL and ev.verdict == NO_MATCH                                           # 소득 상한이 유형에 없으면 예전 그대로 공통 판정
    common_income = notice(special_groups=[{"code": "DISABLED", "mode": "REQUIRED"}], income={"max_bracket": 6}, variants=[{"name": "A", "target_levels": ["UNIV"], "income": {"max_bracket": 4}}])
    ev2 = M.evaluate(common_income, prof(special_codes=[], special_complete=True, income_bracket=3))
    assert [c.key for c in ev2.common if c.key in ("special", "income")] == ["special"] and "소득 조건 충족" in one(ev2, "special").message       # 공통 소득과는 같은 곳에서 결합


def test_a_variant_with_its_own_required_special_keeps_the_top_level_special_as_a_separate_condition():
    n = notice(special_groups=[{"code": "BASIC_LIVELIHOOD", "mode": "REQUIRED"}],
               variants=[{"name": "X", "target_levels": ["UNIV"], "income": {"max_bracket": 6}, "special_groups": [{"code": "DISABLED", "mode": "REQUIRED"}]}])
    ev = M.evaluate(n, prof(special_codes=["DISABLED"], special_complete=True, income_bracket=3))
    x = ev.variants[0]
    assert sorted(c.status for c in x.checks if c.key == "special") == sorted([PASS, FAIL]) and x.verdict == NO_MATCH    # 공고 전체 신분(기초수급자)은 따로 적용된다


# ================================================================== 누적 백분위 입력 (공고의 전체 평점 기준이 100점제일 때)
@pytest.mark.parametrize("cum_pct, want", [(86, PASS), (85, PASS), (84, FAIL)])
def test_cumulative_percentile_is_compared_with_a_cumulative_100_point_condition(cum_pct, want):
    n = notice(gpa={"min": 85, "scale": 100, "basis": "CUMULATIVE"})
    c = one(M.evaluate(n, prof(gpa_cumulative=3.0, gpa_cumulative_percent=cum_pct, gpa_percent=99)), "gpa")      # 직전학기 백분위(99)는 쓰지 않는다
    assert c.status == want, c.message
    assert one(M.evaluate(n, prof(gpa_cumulative_percent=cum_pct)), "gpa").status == want                          # 누적 평점 없이 누적 백분위만으로도 비교


def test_cumulative_percentile_is_not_used_for_last_term_conditions_and_works_with_either():
    last = notice(gpa={"min": 85, "scale": 100, "basis": "LAST_SEMESTER"})
    assert one(M.evaluate(last, prof(gpa_last=3.0, gpa_cumulative_percent=99)), "gpa").status == UNKNOWN              # 직전학기 기준은 직전학기 백분위가 필요
    either = notice(gpa={"min": 85, "scale": 100, "basis": "EITHER"})
    assert one(M.evaluate(either, prof(gpa_last=3.0, gpa_cumulative=3.0, gpa_percent=70, gpa_cumulative_percent=90)), "gpa").status == PASS
    assert one(M.evaluate(either, prof(gpa_last=3.0, gpa_cumulative=3.0, gpa_percent=70, gpa_cumulative_percent=80)), "gpa").status == FAIL
    mixed = notice(gpa={"min": 3.5, "scale": 4.5, "basis": "CUMULATIVE", "alternatives": [{"min": 85, "scale": 100}]})
    assert one(M.evaluate(mixed, prof(gpa_cumulative_percent=90)), "gpa").status == UNKNOWN                        # 4.5 만점 기준이 섞여 있으면 누적 평점 없이 판정하지 않는다


# ================================================================== 신입생만 대상 (new_student_only): AI 가 구조화한 조건을 엔진이 읽지 않던 누락 (9066)
@pytest.mark.parametrize("kw, want", [(dict(semesters_completed=0), PASS), (dict(semesters_completed=3), FAIL), (dict(semesters_completed=0, admission_type="TRANSFER"), FAIL), ({}, UNKNOWN)])
def test_new_student_only_notices(kw, want):
    ev = M.evaluate(notice(new_student_only=True), prof(**kw))
    c = one(ev, "new_student")
    assert c.status == want and ev.verdict == {PASS: MATCH, FAIL: NO_MATCH, UNKNOWN: CONDITIONAL}[want], c.message


def test_new_student_only_messages_and_variants_and_false():
    assert one(M.evaluate(notice(new_student_only=True), prof(semesters_completed=3)), "new_student").message == "신입생만 대상 (이수 3학기)"
    assert one(M.evaluate(notice(new_student_only=True), prof(semesters_completed=0, admission_type="TRANSFER")), "new_student").message == "신입생만 대상 (편입생)"
    assert one(M.evaluate(notice(new_student_only=True), prof(semesters_completed=0)), "new_student").message == "신입생 대상 (이수 0학기)"
    assert not [c for c in M.evaluate(notice(), prof(semesters_completed=3)).common if c.key == "new_student"]       # 신입생만 대상이 아니면 판정하지 않는다
    v = notice(variants=[{"name": "신입", "target_levels": ["UNIV"], "new_student_only": True}])
    assert "new_student_only" not in v["variants"][0]                                                              # 유형 단위 신입생 조건은 스키마에 없어 정규화에서 버려진다 (이름뿐인 유형은 조건부)


# ================================================================== 어학 요건 (language_requirements): 같은 종류의 누락 (9141)
def lang_notice(*reqs):
    return notice(language_requirements=list(reqs))


@pytest.mark.parametrize("scores, want", [({"HSK": 5.0}, PASS), ({"HSK": 6.0}, PASS), ({"HSK": 4.0}, FAIL), ({"TOEIC": 900.0}, FAIL), ({}, FAIL), (None, UNKNOWN)])
def test_language_level_requirement(scores, want):
    c = one(M.evaluate(lang_notice({"exam": "HSK", "level": "5급"}), prof(**({"language_scores": scores} if scores is not None else {}))), "language")
    assert c.status == want, c.message


@pytest.mark.parametrize("scores, want", [({"TOEIC": 700.0}, PASS), ({"TOEIC": 699.0}, FAIL), ({"토익": 800.0}, PASS), ({"TOEFL": 100.0}, FAIL)])
def test_language_score_requirement_and_exam_aliases(scores, want):
    assert one(M.evaluate(lang_notice({"exam": "TOEIC", "min_score": 700}), prof(language_scores=scores)), "language").status == want


def test_language_alternatives_and_lower_is_better_levels():
    alt = lang_notice({"exam": "TOEIC", "min_score": 700}, {"exam": "TOEFL", "min_score": 80})
    assert one(M.evaluate(alt, prof(language_scores={"TOEFL": 85.0})), "language").status == PASS                    # 나열된 시험 중 하나만 충족
    assert one(M.evaluate(alt, prof(language_scores={"TOEFL": 70.0, "TOEIC": 600.0})), "language").status == FAIL
    assert one(M.evaluate(alt, prof()), "language").status == UNKNOWN
    jlpt = lang_notice({"exam": "JLPT", "level": "N2"})
    assert one(M.evaluate(jlpt, prof(language_scores={"JLPT": 1.0})), "language").status == PASS                      # N1 이 N2 보다 높다
    assert one(M.evaluate(jlpt, prof(language_scores={"JLPT": 3.0})), "language").status == FAIL
    assert one(M.evaluate(lang_notice({"exam": "OPIc", "level": "IM2"}), prof(language_scores={"OPIC": 5.0})), "language").status == UNKNOWN   # 등급 숫자가 없으면 해석하지 않는다


def test_language_messages_and_no_requirement():
    c = one(M.evaluate(lang_notice({"exam": "HSK", "level": "5급"}), prof(language_scores={"HSK": 4.0})), "language")
    assert c.message == "어학 요건 미달 (HSK 4 (필요: HSK 5급 이상))"
    assert one(M.evaluate(lang_notice({"exam": "TOEIC", "min_score": 700}), prof(language_scores={"TOEFL": 90.0})), "language").message == "어학 요건 미달 (TOEIC 성적이 없음 (필요: TOEIC 700점 이상))"
    assert one(M.evaluate(lang_notice({"exam": "TOEIC", "min_score": 700}), prof()), "language").message == "어학 요건 확인 필요 (어학 성적 정보 없음 (필요: TOEIC 700점 이상))"
    assert not [c for c in M.evaluate(notice(), prof()).common if c.key == "language"]


# ================================================================== '3학년 진급 재학생' (대상 학년을 [N-1, N]으로 읽은 경우)
def test_promotion_grades_keep_both_the_current_and_the_promoted_year():
    n = notice(grades=[2, 3], target_levels=["UNIV"])
    assert {g: one(M.evaluate(n, prof(grade=g)), "grade").status for g in (1, 2, 3, 4)} == {1: FAIL, 2: PASS, 3: PASS, 4: FAIL}


# ================================================================== '진급' 문장이 있고 대상 학년이 [N] 하나뿐일 때 (9144: '신규장학생으로 선발된 3학년 진급 재학생')
def promo_notice(**kw):
    return notice(grades=[3], target_levels=["UNIV"], unmodeled_conditions=[{"text": "신규장학생으로 선발된 3학년 진급 재학생", "section": "자격", "source": "본문", "kind": "ELIGIBILITY"}], **kw)


def test_promotion_wording_keeps_the_one_year_below_student_in_the_second_term_open():
    n = promo_notice()
    c = one(M.evaluate(n, prof(grade=2, semesters_completed=3)), "grade")                 # 2학년 2학기: 곧 3학년이 될 학생일 수 있다
    assert c.status == UNKNOWN and "'진급' 표현" in c.message and "진급 예정이면 해당" in c.message
    assert M.evaluate(n, prof(grade=2, semesters_completed=3)).verdict != NO_MATCH
    assert one(M.evaluate(n, prof(grade=2)), "grade").status == UNKNOWN                    # 학기를 모르면 닫지 않는다
    assert one(M.evaluate(n, prof(grade=3, semesters_completed=4)), "grade").status == PASS


def test_promotion_wording_does_not_open_first_term_students_or_two_years_below():
    n = promo_notice()
    assert one(M.evaluate(n, prof(grade=2, semesters_completed=2)), "grade").status == FAIL     # 2학년 1학기(이수 2)는 아직 진급 전이 아니다
    assert one(M.evaluate(n, prof(grade=1, semesters_completed=1)), "grade").status == FAIL     # 두 학년 아래
    assert one(M.evaluate(n, prof(grade=4, semesters_completed=7)), "grade").status == FAIL     # 대상 학년을 지난 학생
    assert M.evaluate(n, prof(grade=2, semesters_completed=2)).verdict == NO_MATCH


def test_without_promotion_wording_one_year_below_is_still_rejected():
    n = notice(grades=[3], target_levels=["UNIV"])
    assert one(M.evaluate(n, prof(grade=2, semesters_completed=3)), "grade").status == FAIL
    assert M.evaluate(n, prof(grade=2, semesters_completed=3)).verdict == NO_MATCH


def test_tier_and_best_variant():
    from scholarship.recommend.service import best_variant_and_tier
    from scholarship.recommend import matcher as M

    class V:
        def __init__(self, name, verdict, n=0):
            self.name, self.verdict, self.to_confirm = name, verdict, ["x"] * n

    class E:
        def __init__(self, verdict, mv, tc=()):
            self.verdict, self.matched_variants, self.to_confirm = verdict, mv, list(tc)

    assert best_variant_and_tier(E(M.NO_MATCH, [])) == (None, "NONE")
    assert best_variant_and_tier(E(M.MATCH, [])) == (None, "HIGH")
    info, tier = best_variant_and_tier(E(M.MATCH, [V("A", M.CONDITIONAL, 2), V("B", M.MATCH)]))
    assert info["name"] == "B" and tier == "HIGH"                                   # 충족 유형이 묻히지 않고 대표가 된다
    assert best_variant_and_tier(E(M.MATCH, [V("B", M.MATCH)], ["공통"]))[1] == "PARTIAL"
    info, tier = best_variant_and_tier(E(M.CONDITIONAL, [V("A", M.CONDITIONAL, 3), V("C", M.CONDITIONAL, 1)]))
    assert info["name"] == "C" and tier == "CHECK"


@pytest.mark.parametrize("done, expected, note", [(9, True, True), (10, True, True), (8, None, False), (7, False, False), (0, False, False)])
def test_extended_enrollment_is_derived_from_completed_terms(done, expected, note):
    p, notes = V.derive_terms(StudentProfile(level="UNIV", semesters_completed=done))
    assert p.extended_enrollment is expected and any("정규학기 초과" in n for n in notes) is note


def test_extended_enrollment_is_not_guessed_for_other_students_and_never_overrides_known_values():
    assert V.derive_terms(StudentProfile(level="UNIV", semesters_completed=10, admission_type="TRANSFER"))[0].extended_enrollment is None
    assert V.derive_terms(StudentProfile(level="GRAD", semesters_completed=10))[0].extended_enrollment is None
    assert V.derive_terms(StudentProfile(level="UNIV", semesters_completed=10, extended_enrollment=False))[0].extended_enrollment is False
    assert V.derive_terms(StudentProfile(level="UNIV"))[0].extended_enrollment is None


def test_excluded_extended_enrollment_notice_is_decided_by_completed_terms():
    n = notice(enrollment={"required_status": [], "excluded_status": ["초과학기"], "min_remaining_semesters": None, "notes": None})
    assert one(M.evaluate(n, prof(semesters_completed=3)), "enrollment").status == PASS
    assert one(M.evaluate(n, prof(semesters_completed=10)), "enrollment").status == FAIL
    assert one(M.evaluate(n, prof(semesters_completed=8)), "enrollment").status == UNKNOWN


def test_conditions_prefixed_with_a_failed_variant_name_are_dropped_from_the_common_confirms():
    n = notice(target_levels=["UNIV"], other_conditions=["고등학생: 담임교사가 특별추천한 학생", "신청 전 재단 홈페이지 가입"],
               variants=[dict(name="대학생", target_levels=["UNIV"]), dict(name="고등학생", target_levels=["HIGH"])])
    ev = M.evaluate(n, prof(level="UNIV"))
    assert not any("담임교사" in t for t in ev.to_confirm) and any("재단 홈페이지" in t for t in ev.to_confirm)


def test_conditions_prefixed_with_a_variant_that_is_not_failed_stay_in_the_common_confirms():
    n = notice(target_levels=["UNIV"], other_conditions=["Ⅱ유형: 대학별 선발 기준에 따름"],
               variants=[dict(name="Ⅰ유형", target_levels=["UNIV"]), dict(name="Ⅱ유형", other_conditions=["대학별 기준"])])
    ev = M.evaluate(n, prof(level="UNIV"))
    assert any("대학별 선발 기준" in t for t in ev.to_confirm)          # 조건부 유형의 문장은 공통 판정을 보수적으로 유지


def test_short_withdrawal_exclusion_is_passed_for_an_enrolled_student():
    n = notice(exclusions=["자퇴"])
    assert not any("자퇴" in t for t in M.evaluate(n, prof(status="ENROLLED")).to_confirm)
    assert any("자퇴" in t for t in M.evaluate(n, prof(status=None)).to_confirm)


def test_withdrawal_in_enrollment_excluded_status_is_passed_for_an_enrolled_student():
    n = notice(enrollment={"required_status": ["재학"], "excluded_status": ["자퇴", "휴학"], "min_remaining_semesters": None, "notes": None})
    assert not any("자퇴" in t for t in M.evaluate(n, prof(status="ENROLLED")).to_confirm)
    assert any("자퇴" in t for t in M.evaluate(n, prof(status=None)).to_confirm)


# ================================================================== 어학 요건: 유럽언어기준(CEFR) 등급 (9235 DAAD 독일어 B1)
DAAD = {"exam": "독일어(유럽언어기준 CEFR)", "level": "B1 이상"}


@pytest.mark.parametrize("scores,want", [({"독일어": 3.0}, PASS), ({"독일어": 5.0}, PASS), ({"독일어": 2.0}, FAIL), ({"TOEIC": 800.0}, FAIL), (None, UNKNOWN)])
def test_language_cefr_level(scores, want):
    c = one(M.evaluate(lang_notice(DAAD), prof(**({"language_scores": scores} if scores is not None else {}))), "language")
    assert c.status == want


def test_language_cefr_messages_and_unknown_language():
    c = one(M.evaluate(lang_notice(DAAD), prof(language_scores={"독일어": 4.0})), "language")
    assert c.message == "어학 요건 충족 (독일어(유럽언어기준 CEFR) B2 (필요: 독일어(유럽언어기준 CEFR) B1 이상))"       # '이상 이상' 으로 겹치지 않는다
    assert one(M.evaluate(lang_notice({"exam": "CEFR", "level": "B1"}), prof(language_scores={"독일어": 4.0})), "language").status == UNKNOWN   # 어느 언어인지 모름
    assert one(M.evaluate(lang_notice({"exam": "HSK", "level": "5급 이상"}), prof(language_scores={"HSK": 4.0})), "language").message == "어학 요건 미달 (HSK 4 (필요: HSK 5급 이상))"
