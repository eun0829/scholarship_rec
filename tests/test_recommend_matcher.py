# -*- coding: utf-8 -*-
"""공고 조건 <-> 학생 프로필 판정. 실제 추출 결과(153381 신·구형식)로 variants 해석과 구형식 보정을 검증한다."""
import json
import os

import pytest

from scholarship.common.ai import schema
from scholarship.recommend import matcher as m
from scholarship.recommend.matcher import CONDITIONAL, FAIL, MATCH, NO_MATCH, PASS, UNKNOWN
from scholarship.recommend.profile import build_profile

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def P(**kw):
    return build_profile(kw).profile


def elig(**kw):
    base = {"category": "OUT", "apply_end": "2026-10-18", "target_levels": ["UNIV"]}
    base.update(kw)
    d, _ = schema.normalize(base, None)
    d["schema_version"] = 2
    return d


def real(name, version):
    raw = json.load(open(os.path.join(FIX, name), encoding="utf-8"))["elig"]
    d, _ = schema.normalize(raw, None)
    if version == 2:
        d["schema_version"] = 2                       # 저장된 ELIG_JSON 에는 schema_version 이 들어 있다
    return d


def statuses(checks, key):
    return [c.status for c in checks if c.key == key]


# ================================================================== 성적
def gpa(min_, scale, basis="LAST_SEMESTER", alts=None):
    return {"min": min_, "scale": scale, "basis": basis, "alternatives": alts or []}


def test_gpa_same_scale():
    assert m.check_gpa(gpa(3.0, 4.5), P(gpa_last=3.0, gpa_scale=4.5), "공통")[0].status == PASS
    assert m.check_gpa(gpa(3.0, 4.5), P(gpa_last=2.99, gpa_scale=4.5), "공통")[0].status == FAIL
    assert m.check_gpa(gpa(None, None), P(gpa_last=1.0), "공통") == []                    # 성적 기준 없음 -> 판정 항목 없음


def test_gpa_missing_student_value_is_unknown_not_fail():
    c = m.check_gpa(gpa(3.0, 4.5), P(level="UNIV"), "공통")[0]
    assert c.status == UNKNOWN and "평점 정보 없음" in c.message


def test_gpa_basis_selects_student_value():
    p = P(gpa_last=2.5, gpa_cumulative=3.8, gpa_scale=4.5)
    assert m.check_gpa(gpa(3.5, 4.5, "LAST_SEMESTER"), p, "공통")[0].status == FAIL
    assert m.check_gpa(gpa(3.5, 4.5, "CUMULATIVE"), p, "공통")[0].status == PASS
    assert m.check_gpa(gpa(3.5, 4.5, "EITHER"), p, "공통")[0].status == PASS            # 둘 중 높은 값
    assert m.check_gpa(gpa(3.5, 4.5, "UNKNOWN"), p, "공통")[0].status == FAIL           # 기준 불명 -> 직전학기 우선
    assert m.check_gpa(gpa(3.5, 4.5, "LAST_SEMESTER"), P(gpa_cumulative=3.8, gpa_scale=4.5), "공통")[0].status == UNKNOWN


def test_gpa_alternative_with_same_scale_is_preferred():
    g = gpa(4.0, 4.5, alts=[{"min": 3.7, "scale": 4.3}])                                  # 4.5 기준과 4.3 기준 병기
    assert m.check_gpa(g, P(gpa_last=4.1, gpa_scale=4.5), "공통")[0].status == PASS
    assert m.check_gpa(g, P(gpa_last=3.9, gpa_scale=4.5), "공통")[0].status == FAIL     # 같은 만점 기준(4.0/4.5)만 본다
    assert m.check_gpa(g, P(gpa_last=3.8, gpa_scale=4.3), "공통")[0].status == PASS     # 학생이 4.3 만점이면 4.3 기준 사용


def test_gpa_different_scales_are_converted_with_margin():
    g = gpa(3.7, 4.3)                                                                     # 4.5 만점 학생에게 환산: 3.7*4.5/4.3 = 3.872 (경계 ±0.09)
    assert m.check_gpa(g, P(gpa_last=4.0, gpa_scale=4.5), "공통")[0].status == PASS
    assert m.check_gpa(g, P(gpa_last=3.7, gpa_scale=4.5), "공통")[0].status == FAIL
    assert m.check_gpa(g, P(gpa_last=3.85, gpa_scale=4.5), "공통")[0].status == UNKNOWN  # 경계 근처는 판정하지 않음
    assert m.check_gpa(gpa(3.5, None), P(gpa_last=4.0, gpa_scale=4.5), "공통")[0].status == UNKNOWN   # 공고 만점 불명


def test_gpa_hundred_point_scale_needs_percent():
    g = gpa(80, 100)
    assert m.check_gpa(g, P(gpa_last=4.2, gpa_scale=4.5), "공통")[0].status == UNKNOWN   # 4.x <-> 100점은 단순 환산 금지
    assert m.check_gpa(g, P(gpa_last=4.2, gpa_scale=4.5, gpa_percent=85), "공통")[0].status == PASS
    assert m.check_gpa(g, P(gpa_last=4.2, gpa_scale=4.5, gpa_percent=75), "공통")[0].status == FAIL


# ================================================================== 학점 / 학기
def test_credits_and_semesters():
    cr = {"min_last_semester": 12, "min_completed_semesters": 5}
    out = m.check_credits(cr, P(credits_last=15, semesters_completed=4), "공통")
    assert [c.status for c in out] == [PASS, FAIL]
    assert [c.status for c in m.check_credits(cr, P(level="UNIV"), "공통")] == [UNKNOWN, UNKNOWN]
    assert m.check_credits({}, P(), "공통") == []
    en = m.check_enrollment({"min_remaining_semesters": 2}, P(remaining_semesters=1), "공통")
    assert en[0].status == FAIL


# ================================================================== 소득 (eligible_types 는 '또는')
INC = {"max_bracket": 2.0, "eligible_types": ["국민기초생활수급자", "차상위계층", "학자금 지원구간 2구간 이내"]}


def test_income_or_semantics():
    assert m.check_income(INC, P(is_basic_livelihood=True), "공통")[0].status == PASS           # 수급자면 지원구간 몰라도 통과
    assert m.check_income(INC, P(income_bracket=1), "공통")[0].status == PASS
    assert m.check_income(INC, P(income_bracket=5, is_basic_livelihood=False, is_near_poor=False), "공통")[0].status == FAIL
    c = m.check_income(INC, P(income_bracket=5), "공통")[0]                                        # 수급·차상위 여부 미입력 -> 탈락시키지 않음
    assert c.status == UNKNOWN and "소득 정보 부족" in c.message
    assert m.check_income(INC, P(level="UNIV"), "공통")[0].status == UNKNOWN


def test_income_max_bracket_alone_and_other_types():
    assert m.check_income({"max_bracket": 3, "eligible_types": []}, P(income_bracket=4), "공통")[0].status == FAIL
    assert m.check_income({"max_bracket": 3, "eligible_types": []}, P(income_bracket=3), "공통")[0].status == PASS
    assert m.check_income({"eligible_types": ["한부모가족"]}, P(is_single_parent=True), "공통")[0].status == PASS
    assert m.check_income({"eligible_types": ["다자녀가구"]}, P(income_bracket=1), "공통")[0].status == UNKNOWN   # 모르는 유형
    assert m.check_income({}, P(), "공통") == []
    # 지원구간 표현이 없는 eligible_types + max_bracket -> max_bracket 을 선택지로 추가
    assert m.check_income({"max_bracket": 2, "eligible_types": ["기초생활수급자"]}, P(income_bracket=2, is_basic_livelihood=False), "공통")[0].status == PASS


# ================================================================== 지역
def test_region_name_normalization():
    rg = {"residence": ["춘천시"]}
    assert m.check_region(rg, P(residence_sido="강원특별자치도", residence_sigungu="춘천"), "공통")[0].status == PASS
    assert m.check_region(rg, P(residence_sido="경기도", residence_sigungu="시흥시"), "공통")[0].status == FAIL
    assert m.check_region(rg, P(residence_sido="강원도"), "공통")[0].status == UNKNOWN                # 시·군 정보가 없으면 판정 불가
    sido = {"residence": ["경기도"]}
    assert m.check_region(sido, P(residence_sido="경기"), "공통")[0].status == PASS
    assert m.check_region(sido, P(residence_sido="서울특별시"), "공통")[0].status == FAIL
    multi = {"residence": ["춘천시", "홍천군"]}                                                       # 여러 지역은 '또는'
    assert m.check_region(multi, P(residence_sigungu="홍천"), "공통")[0].status == PASS


def test_region_sub_level_and_school_location():
    c = m.check_region({"residence": ["혈동2리", "팔미3리"]}, P(residence_sido="강원특별자치도", residence_sigungu="춘천시"), "공통")[0]
    assert c.status == UNKNOWN and "세부 거주지" in c.message                                          # 리 단위는 코드가 판정하지 않음
    out = m.check_region({"school_location": ["춘천시"]}, P(school_sigungu="시흥시"), "공통")
    assert out[0].status == FAIL and "학교 소재지" in out[0].message


def test_region_duration_notes():
    many = {"residence": ["춘천시"], "notes": "기본 1년 이상. 주소이전(3개월~1년 미만), 세인트존스(3년 이상)는 별도"}
    p = P(residence_sido="강원특별자치도", residence_sigungu="춘천시", residence_years=5)
    out = m.check_region(many, p, "공통")
    assert [c.status for c in out] == [PASS, UNKNOWN] and "거주 기간" in out[1].message          # 기간이 여러 개면 코드가 판정하지 않음
    one = {"residence": ["춘천시"], "notes": "공고일 현재 1년 이상 거주"}
    assert m.check_region(one, p, "공통")[1].status == PASS
    assert m.check_region(one, P(residence_sigungu="춘천시", residence_years=0.5), "공통")[1].status == FAIL
    assert m.check_region(one, P(residence_sigungu="춘천시"), "공통")[1].status == UNKNOWN
    assert len(m.check_region({"residence": [], "notes": "3년"}, p, "공통")) == 0                      # 지역 조건이 없으면 notes 무시


# ================================================================== 학적 / 대상
def test_levels_and_grades():
    assert m.check_levels(["UNIV"], P(level="UNIV"), "공통")[0].status == PASS
    assert m.check_levels(["HIGH"], P(level="UNIV"), "공통")[0].status == FAIL
    assert m.check_levels(["UNIV"], P(), "공통")[0].status == UNKNOWN and m.check_levels([], P(level="UNIV"), "공통") == []
    assert m.check_grades([3, 4], P(grade=2), "공통")[0].status == FAIL and m.check_grades([3, 4], P(grade=4), "공통")[0].status == PASS


def test_enrollment_required_and_excluded():
    en = {"required_status": ["재학"], "excluded_status": ["휴학", "졸업", "초과학기", "학점은행제", "사이버대학"]}
    ok = m.check_enrollment(en, P(status="ENROLLED", extended_enrollment=False, school_type="GENERAL"), "공통")
    assert all(c.status == PASS for c in ok)
    leave = m.check_enrollment(en, P(status="LEAVE"), "공통")
    assert FAIL in [c.status for c in leave] and any("재학생만" in c.message for c in leave)
    unknown = m.check_enrollment(en, P(status="ENROLLED"), "공통")
    assert {c.status for c in unknown} == {PASS, UNKNOWN}                                            # 초과학기/학교종류를 모르면 확인 필요
    assert m.check_enrollment({"required_status": ["원격대학 재학생"]}, P(school_type="GENERAL"), "공통")[-1].status == FAIL


def test_enrollment_excluded_phrase_with_enrolled_word_does_not_fail_every_student():
    """'졸업연기자(0학점 재학생)' 처럼 제외 문구에 '재학'이 들어 있어도 재학생이 통째로 탈락하면 안 된다 (회귀)."""
    en = {"required_status": ["재학생"], "excluded_status": ["졸업연기자(0학점 재학생)", "학사학위 취득유예생", "신청 당시 등록금 미납 또는 분납 상태인 재학생"]}
    out = m.check_enrollment(en, P(status="ENROLLED", graduation_deferred=False, tuition_paid=True), "공통")
    assert all(c.status == PASS for c in out)
    out = m.check_enrollment(en, P(status="ENROLLED", graduation_deferred=True, tuition_paid=True), "공통")
    assert any(c.status == FAIL and "졸업유예" in c.message for c in out)
    out = m.check_enrollment(en, P(status="ENROLLED", graduation_deferred=False, tuition_paid=False), "공통")
    assert any(c.status == FAIL and "등록금 미납" in c.message for c in out)


# ================================================================== 문장 조건 / 제외
def test_text_conditions_are_never_decided_by_code():
    out = m.check_text_conditions([], ["TOEIC 750 이상", "추천서 제출"], [], P(level="UNIV"), "공통")
    assert [c.status for c in out] == [UNKNOWN, UNKNOWN] and all("확인 필요" in c.message for c in out)


def test_special_status_matching():
    sp = ["가족돌봄청년"]
    assert m.check_text_conditions(sp, [], [], P(special_status=["가족돌봄"]), "공통")[0].status == PASS
    assert m.check_text_conditions(sp, [], [], P(special_status=["다문화"]), "공통")[0].status == FAIL
    assert m.check_text_conditions(sp, [], [], P(special_status=[]), "공통")[0].status == FAIL        # '해당 없음'을 명시
    assert m.check_text_conditions(sp, [], [], P(), "공통")[0].status == UNKNOWN                      # 미입력


def test_exclusions():
    ex = ["2026년 2학기 등록금 자부담금이 없는 전액 면제자", "직전학기 재단 장학생", "휴학생"]
    out = m.check_text_conditions([], [], ex, P(tuition_fully_waived=True, status="ENROLLED"), "공통")
    assert [c.status for c in out] == [FAIL, UNKNOWN, PASS]
    out = m.check_text_conditions([], [], ex, P(tuition_fully_waived=False, status="LEAVE"), "공통")
    assert [c.status for c in out] == [PASS, UNKNOWN, FAIL]


def test_nationality_and_major():
    assert m.check_nationality("대한민국 국적자", P(nationality="KR"), "공통")[0].status == PASS
    assert m.check_nationality("외국인 유학생", P(nationality="KR"), "공통")[0].status == FAIL
    assert m.check_nationality("재외동포", P(nationality="KR"), "공통")[0].status == UNKNOWN
    assert m.check_majors(["컴퓨터공학과"], P(major="컴퓨터공학과"), "공통")[0].status == PASS
    assert m.check_majors(["컴퓨터공학과"], P(major="전자공학"), "공통")[0].status == UNKNOWN       # 표기가 달라 탈락시키지 않음
    assert m.check_majors(["컴퓨터공학과"], P(), "공통")[0].status == UNKNOWN


# ================================================================== 종합 판정 (variants 없음)
def test_simple_notice_match_conditional_and_no_match():
    e = elig(target_levels=["UNIV"], enrollment={"required_status": ["재학"]}, gpa={"min": 3.0, "scale": 4.5, "basis": "LAST_SEMESTER"})
    assert m.evaluate(e, P(level="UNIV", status="ENROLLED", gpa_last=3.5)).verdict == MATCH
    ev = m.evaluate(e, P(level="UNIV", status="ENROLLED"))
    assert ev.verdict == CONDITIONAL and any("평점 정보 없음" in x for x in ev.to_confirm)
    ev = m.evaluate(e, P(level="UNIV", status="ENROLLED", gpa_last=2.0))
    assert ev.verdict == NO_MATCH and ev.fail_reasons and "성적 미달" in ev.fail_reasons[0]
    e2 = elig(other_conditions=["TOEIC 750 이상"])                                                      # 문장 조건이 있으면 '충족 확정'은 없다
    assert m.evaluate(e2, P(level="UNIV")).verdict == CONDITIONAL


# ================================================================== 종합 판정 (variants) - 실제 153381 신형식
BOMNAE = lambda: real("ai_result_153381_v2.json", 2)


def test_bomnae_non_resident_is_not_rejected_because_notice_allows_children_of_residents():
    """공고가 '시민 또는 그 자녀' 라서 본인 주민등록이 다르다고 탈락시키면 부모가 춘천에 사는 학생이 억울하게 걸러진다 (실제 결과로 확인된 문제)."""
    ev = m.evaluate(BOMNAE(), P(level="UNIV", status="ENROLLED", gpa_last=3.2, credits_last=15,
                                residence_sido="경기도", residence_sigungu="시흥시"))
    assert ev.verdict == CONDITIONAL and ev.fail_reasons == []
    assert any("대체 요건이 있을 수 있어 확인 필요" in c and "춘천시" in c for c in ev.to_confirm)


def test_region_without_alternative_wording_still_rejects():
    e = elig(region={"residence": ["대전광역시"], "notes": None})
    ev = m.evaluate(e, P(level="UNIV", residence_sido="경기도", residence_sigungu="시흥시"))
    assert ev.verdict == NO_MATCH and "주민등록 거주지 조건 미달" in ev.fail_reasons[0]
    only_period = elig(region={"residence": ["대전광역시"], "notes": "공고일 현재 1년 이상 거주"})          # 기간 표현만 있고 대체 요건은 없음
    assert m.evaluate(only_period, P(level="UNIV", residence_sido="경기도", residence_sigungu="시흥시")).verdict == NO_MATCH


@pytest.mark.parametrize("notes", ["시민 또는 그 자녀", "본인 또는 부모의 주민등록 기준", "관내 고등학교 졸업(예정)자 포함", "출신 학교가 해당 지역인 경우", "가족 중 1인 이상 거주"])
def test_region_alternative_qualifiers_downgrade_fail(notes):
    c = m.check_region({"residence": ["춘천시"], "notes": notes}, P(residence_sido="경기도", residence_sigungu="시흥시"), "공통")[0]
    assert c.status == UNKNOWN and "대체 요건" in c.message and notes in c.message
    assert m.check_region({"residence": ["춘천시"], "notes": notes}, P(residence_sigungu="춘천시"), "공통")[0].status == PASS   # 본인이 해당하면 그대로 충족


def test_gpa_unknown_message_says_which_gpa_is_needed():
    g = {"min": 3.5, "scale": 4.5, "basis": "CUMULATIVE", "alternatives": []}
    c = m.check_gpa(g, P(gpa_last=3.2, gpa_scale=4.5), "공통")[0]                        # 직전학기 평점만 있고 전체 평점이 필요한 공고
    assert c.status == UNKNOWN and c.message.startswith("전체(누적) 평점 정보 없음")
    g["basis"] = "LAST_SEMESTER"
    assert m.check_gpa(g, P(gpa_cumulative=3.8, gpa_scale=4.5), "공통")[0].message.startswith("직전학기 평점 정보 없음")
    g["basis"] = "UNKNOWN"
    assert m.check_gpa(g, P(level="UNIV"), "공통")[0].message.startswith("평점 정보 없음")


def test_defer_keys_turn_fail_into_ai_review_items():
    e = elig(region={"residence": ["대전광역시"]}, gpa={"min": 3.0, "scale": 4.5, "basis": "LAST_SEMESTER"})
    p = P(level="UNIV", status="ENROLLED", gpa_last=2.0, residence_sido="경기도", residence_sigungu="시흥시")
    assert m.evaluate(e, p).verdict == NO_MATCH
    ev = m.evaluate(e, p, defer_keys={"region"})                                            # 거주지는 나중에 AI 가 판단 -> 성적은 여전히 탈락
    assert ev.verdict == NO_MATCH and all("성적" in r for r in ev.fail_reasons)
    ev = m.evaluate(e, P(level="UNIV", status="ENROLLED", gpa_last=3.5, residence_sido="경기도", residence_sigungu="시흥시"), defer_keys={"region"})
    assert ev.verdict == CONDITIONAL and any("AI 판단 예정" in c and "대전광역시" in c for c in ev.to_confirm)
    ev = m.evaluate(e, p, defer_keys={"region", "gpa"})
    assert ev.verdict == CONDITIONAL and len(ev.to_confirm) == 2
    v = elig(variants=[{"name": "A", "region": {"residence": ["대전광역시"]}}])                # 유형별 조건에도 적용
    assert m.evaluate(v, P(level="UNIV", residence_sido="경기도", residence_sigungu="시흥시")).verdict == NO_MATCH
    assert m.evaluate(v, P(level="UNIV", residence_sido="경기도", residence_sigungu="시흥시"), defer_keys={"region"}).verdict == CONDITIONAL


def test_bomnae_high_gpa_resident_matches_top_variant_but_overall_needs_confirmation():
    ev = m.evaluate(BOMNAE(), P(level="UNIV", status="ENROLLED", gpa_last=4.2, credits_last=15,
                                residence_sido="강원특별자치도", residence_sigungu="춘천시", residence_years=5))
    byname = {v.name: v.verdict for v in ev.variants}
    assert byname["성적우수"] == MATCH and byname["애향-저소득"] == CONDITIONAL         # 소득 정보가 없으니 확인 필요
    assert ev.matched_variants[0].name == "성적우수"                                    # 확정 유형이 맨 앞
    assert ev.verdict == CONDITIONAL                                                   # 공통 조건에 확인 항목(거주 기간 등)이 있어 전체는 조건부
    assert any("거주 기간" in c for c in ev.to_confirm)


def test_bomnae_low_gpa_low_income_student_is_not_rejected_by_gpa():
    """이전 구조의 문제: 최상위 gpa.min 3.0 때문에 성적이 낮은 저소득 학생이 탈락했다. 이제는 저소득 유형으로 통과해야 한다."""
    ev = m.evaluate(BOMNAE(), P(level="UNIV", status="ENROLLED", gpa_last=2.0, credits_last=12,
                                residence_sido="강원특별자치도", residence_sigungu="춘천시", is_basic_livelihood=True))
    byname = {v.name: v.verdict for v in ev.variants}
    assert byname["애향-저소득"] == MATCH
    assert byname["성적우수"] == NO_MATCH and byname["애향-통리반장 자녀"] == NO_MATCH and byname["원격대학"] == NO_MATCH
    assert ev.verdict == CONDITIONAL and [v.name for v in ev.matched_variants][0] == "애향-저소득"
    unmatched = [v for v in ev.variants if v.verdict == NO_MATCH]
    assert any("성적 미달" in v.fails[0] for v in unmatched)


def test_bomnae_graduate_student_and_leave_are_rejected_by_common():
    base = dict(status="ENROLLED", gpa_last=4.2, residence_sido="강원특별자치도", residence_sigungu="춘천시")
    assert m.evaluate(BOMNAE(), P(level="GRAD", **base)).verdict == NO_MATCH
    leave = m.evaluate(BOMNAE(), P(level="UNIV", **{**base, "status": "LEAVE"}))
    assert leave.verdict == NO_MATCH and any("재학생만" in r for r in leave.fail_reasons)


def test_variant_level_restriction_excludes_wrong_school_level():
    e = elig(target_levels=["HIGH", "UNIV"], variants=[
        {"name": "성적향상", "target_levels": ["HIGH"], "other_conditions": ["학교장 추천"]},
        {"name": "대학생 일반", "gpa": {"min": 3.0, "scale": 4.5, "basis": "LAST_SEMESTER"}}])
    ev = m.evaluate(e, P(level="UNIV", gpa_last=3.5))
    byname = {v.name: v.verdict for v in ev.variants}
    assert byname == {"성적향상": NO_MATCH, "대학생 일반": MATCH} and ev.verdict == MATCH
    assert m.evaluate(e, P(level="UNIV", gpa_last=2.0)).verdict == NO_MATCH                       # 해당 유형이 하나도 없음
    assert "해당하는 유형이 없음" in m.evaluate(e, P(level="UNIV", gpa_last=2.0)).fail_reasons[0]


def test_variant_conditions_are_additional_to_common():
    e = elig(gpa={"min": 3.0, "scale": 4.5, "basis": "LAST_SEMESTER"},                         # 공통: 모든 유형 3.0 이상
             variants=[{"name": "A", "credits": {"min_last_semester": 12}}, {"name": "B"}])
    assert m.evaluate(e, P(level="UNIV", gpa_last=2.5)).verdict == NO_MATCH                    # 공통 미달이면 유형은 볼 필요 없음
    ev = m.evaluate(e, P(level="UNIV", gpa_last=3.5, credits_last=9))
    assert {v.name: v.verdict for v in ev.variants} == {"A": NO_MATCH, "B": MATCH} and ev.verdict == MATCH


def test_duplicate_messages_are_collapsed():
    e = elig(enrollment={"excluded_status": ["학점은행제", "사이버대학", "방송대"]})
    ev = m.evaluate(e, P(level="UNIV"))
    assert len(ev.to_confirm) == 1                                                             # '원격대학 제외' 안내가 한 번만


# ================================================================== 구형식(schema_version 없음) 보정
OLD = lambda: real("ai_result_153381_v1.json", 1)


def test_legacy_result_does_not_reject_by_merged_gpa():
    d = OLD()
    assert not m.is_v2(d) and m.legacy_dims(d) == {"gpa", "credits", "income"}
    ev = m.evaluate(d, P(level="UNIV", status="ENROLLED", gpa_last=2.0, credits_last=9,
                         residence_sido="강원특별자치도", residence_sigungu="춘천시"))
    assert ev.verdict == CONDITIONAL                                                           # 구조가 합쳐져 있어 성적 미달로 단정하지 않음
    assert any("구형식 결과" in c for c in ev.to_confirm)
    assert not any("미달" in r for r in ev.fail_reasons)


def test_legacy_result_still_rejects_by_non_variable_conditions():
    d = OLD()
    ev = m.evaluate(d, P(level="UNIV", status="ENROLLED", gpa_last=4.0, residence_sido="경기도", residence_sigungu="시흥시"))
    assert ev.verdict == CONDITIONAL and any("대체 요건" in c for c in ev.to_confirm)                    # '시민 또는 자녀' 문구가 있어 탈락시키지 않음
    assert m.evaluate(d, P(level="UNIV", status="LEAVE", residence_sigungu="춘천시")).verdict == NO_MATCH


def test_is_v2_detection():
    assert m.is_v2({"schema_version": 2}) and m.is_v2({"variants": [{"name": "A"}]})
    assert not m.is_v2({}) and not m.is_v2({"schema_version": 1, "variants": []})


def test_new_format_real_result_unaffected_by_legacy_rules():
    d = BOMNAE()
    assert m.is_v2(d) and m.legacy_dims(d) == set()
