# -*- coding: utf-8 -*-
"""조사한 실제 공고 중 표현하기 어려웠던 사례를 v3 형식으로 쓴 것(AI 가 이렇게 써 주기를 기대하는 출력)이 정보 손실 없이 정규화되는지.
공고 번호는 드림스폰 idx. 스키마가 이 사례들을 담지 못하면 1단계의 목표(조건을 구조로 담기)가 달성되지 않은 것이다."""
from datetime import date

from scholarship.common.ai.schema import normalize

TODAY = date(2026, 9, 21)
BASE = {"category": "OUT", "apply_start": "2026-09-01", "apply_end": "2026-10-30", "summary": "요약"}


def run(**fields):
    out, issues = normalize(dict(BASE, **fields), TODAY, "", version=3)
    return out, issues


def types(out):
    return [r["type"] for r in out["region"]["requirements"]]


def test_9195_national_scholarship_waivers_and_relaxation():
    """국가장학금: 신입·편입·재입학·장애인은 성적 조건 미적용, 수급자·차상위는 70점으로 완화(그 외 80점), 소득 9분위 이하.
    완화는 '공통 기준 + 더 낮은 유형 기준'이 아니라 서로 겹치지 않는 유형으로 나눠 표현한다 (최상위 성적은 비움)."""
    out, issues = run(
        target_levels=["UNIV"], credits={"min_last_semester": 12}, income={"max_bracket": 9, "basis": "BRACKET"},
        waivers=[{"when": {"student": ["NEW_STUDENT", "TRANSFER", "REENTRY"], "special": ["DISABLED"]}, "skip": ["gpa", "credits"]}],
        variants=[{"name": "일반", "gpa": {"min": 80, "scale": 100, "basis": "LAST_SEMESTER"}},
                  {"name": "수급자·차상위", "special_groups": [{"code": "BASIC_LIVELIHOOD"}, {"code": "NEAR_POOR"}],
                   "gpa": {"min": 70, "scale": 100, "basis": "LAST_SEMESTER"}}],
        unmodeled_conditions=[{"text": "지역인재장학금: 내신 또는 수능 일반대 3등급, 전문대 4등급 이내", "section": "신청자격", "source": "본문", "kind": "ELIGIBILITY"}])
    assert issues == []
    assert out["waivers"][0]["when"] == {"student": ["NEW_STUDENT", "TRANSFER", "REENTRY"], "special": ["DISABLED"]} and out["waivers"][0]["skip"] == ["gpa", "credits"]
    general, relax = out["variants"]
    assert general["gpa"]["min"] == 80 and "special_groups" not in general
    assert relax["gpa"]["min"] == 70 and [s["code"] for s in relax["special_groups"]] == ["BASIC_LIVELIHOOD", "NEAR_POOR"]
    assert out["gpa"]["min"] is None and out["income"]["max_bracket"] == 9 and out["unmodeled_conditions"][0]["text"].startswith("지역인재장학금")


def test_relaxation_written_as_common_plus_lower_variant_is_flagged_for_review():
    """AI 가 완화를 '최상위 공통 기준 + 더 낮은 유형 기준'으로 쓰면 모순이라 검수 대상이 된다 (조용히 틀린 판정이 되지 않게)."""
    out, issues = run(target_levels=["UNIV"], gpa={"min": 80, "scale": 100, "basis": "LAST_SEMESTER"},
                      variants=[{"name": "수급자", "special_groups": [{"code": "BASIC_LIVELIHOOD"}], "gpa": {"min": 70, "scale": 100}}, {"name": "일반"}])
    assert any(i["field"] == "variants[0].gpa" and "모순" not in i["reason"] and "섞은 것인지" in i["reason"] for i in issues)


def test_9096_gochang_region_alternatives_prerequisite_and_age():
    """거창군: (본인 3년 이상 거주 / 관내 중·고 졸업 / 부모 3년 이상 거주) 중 하나 + 국가장학금 신청자 + 만 30세 미만."""
    out, issues = run(
        target_levels=["UNIV"], gpa={"min": 2.5, "scale": 4.5, "basis": "LAST_SEMESTER"}, credits={"min_last_semester": 12},
        region={"mode": "ANY", "requirements": [
            {"type": "RESIDENCE_SELF", "areas": ["경상남도 거창군"], "min_years": 3, "years_basis": "계속 거주"},
            {"type": "GRADUATED_SCHOOL", "areas": ["경상남도 거창군"]},
            {"type": "RESIDENCE_PARENT", "areas": ["경상남도 거창군"], "min_years": 3}]},
        prerequisites=[{"type": "NATIONAL_SCHOLARSHIP_APPLIED", "text": "해당 학기 신청자"}], age={"max": 29},
        school={"programs": ["NATIONAL_SCHOLARSHIP"]},
        exclusion_items=[{"type": "CONCURRENT_TUITION", "text": "본인 부담 등록금이 1만원 미만인 자 제외"}])
    assert issues == []
    assert out["region"]["mode"] == "ANY" and types(out) == ["RESIDENCE_SELF", "GRADUATED_SCHOOL", "RESIDENCE_PARENT"]
    assert out["region"]["requirements"][0]["min_years"] == 3 and "min_years" not in out["region"]["requirements"][1]
    assert out["prerequisites"][0]["type"] == "NATIONAL_SCHOLARSHIP_APPLIED" and out["age"] == {"max": 29}
    assert out["school"] == {"programs": ["NATIONAL_SCHOLARSHIP"]} and out["exclusions"] == ["본인 부담 등록금이 1만원 미만인 자 제외"]


def test_9098_preferred_special_groups_recommendation_and_excluded_schools():
    """사랑의 날개: 장애인 필수, 기초·차상위는 우대, 학교장·센터장 추천, 사이버·방통·사내대학 불가, 기수혜자 제외."""
    out, issues = run(
        target_levels=["UNIV"],
        special_groups=[{"code": "DISABLED", "scope": "SELF", "mode": "REQUIRED"}, {"code": "BASIC_LIVELIHOOD", "mode": "PREFERRED"},
                        {"code": "NEAR_POOR", "mode": "PREFERRED"}],
        recommendation={"required": True, "from": ["PRINCIPAL", "OTHER"]}, school={"excluded_types": ["CYBER", "OPEN_UNIV", "COMPANY_UNIV"]},
        review_criteria=["학업 우수·봉사정신", "자기소개서에 나눔 실천 의지를 구체적으로 표현"], obligations=["오프라인 장학금 전달식 참여"],
        exclusion_items=[{"type": "PRIOR_SAME", "text": "본 법인의 장학금 기수혜자 제외"}])
    assert issues == []
    modes = {s["code"]: s["mode"] for s in out["special_groups"]}
    assert modes == {"DISABLED": "REQUIRED", "BASIC_LIVELIHOOD": "PREFERRED", "NEAR_POOR": "PREFERRED"}      # 우대를 요건으로 오판하지 않음
    assert out["recommendation"] == {"required": True, "from": ["PRINCIPAL", "OTHER"]}
    assert out["school"]["excluded_types"] == ["CYBER", "OPEN_UNIV", "COMPANY_UNIV"] and out["other_conditions"] == []   # 심사·의무는 자격 조건 목록에 안 들어감


def test_9211_daejeon_variants_with_own_deadlines_and_apply_types():
    """대전시 인재육성장학: 유형마다 마감과 신청 방식(학교 경유/온라인)이 다름."""
    out, issues = run(
        apply_type="HYBRID", apply_end="2026-10-07", target_levels=["HIGH", "UNIV"],
        exclusion_items=[{"type": "PRIOR_RECENT", "text": "2026년 재단 상반기 장학생으로 선발된 자는 하반기 신청 불가"}],
        variants=[{"name": "외국인 유학생", "target_levels": ["UNIV"], "apply_end": "2026-09-23", "apply_type": "SCHOOL"},
                  {"name": "성취(고등학생)", "target_levels": ["HIGH"], "apply_end": "2026-09-23", "apply_type": "DIRECT"},
                  {"name": "성취(대학생)", "target_levels": ["UNIV"], "apply_end": "2026-09-08", "apply_type": "DIRECT"},
                  {"name": "과학", "apply_end": "2026-10-07", "apply_type": "DIRECT"}])
    assert issues == []
    assert out["apply_type"] == "HYBRID"
    ends = {v["name"]: (v["apply_end"], v["apply_type"]) for v in out["variants"]}
    assert ends == {"외국인 유학생": ("2026-09-23", "SCHOOL"), "성취(고등학생)": ("2026-09-23", "DIRECT"),
                    "성취(대학생)": ("2026-09-08", "DIRECT"), "과학": ("2026-10-07", "DIRECT")}               # 유형별 마감이 하나의 마감으로 뭉개지지 않음


def test_9237_school_varies_deadline_and_unconvertible_term_condition():
    """안중근의사숭모회: 학교별 추천 마감, 지도교수 추천, 만점 미기재 성적, 학기 조건은 변환이 불확실해 원문 보존."""
    out, issues = run(
        apply_type="SCHOOL", deadline_kind="SCHOOL_VARIES", target_levels=["UNIV"], grades=[2, 3],
        gpa={"min": 3.6, "basis": "CUMULATIVE"}, credits={"min_last_semester": 15},
        recommendation={"required": True, "from": ["PROFESSOR"]},
        unmodeled_conditions=[{"text": "2026년 2학기 기준 2학년 2학기 ~ 3학년 2학기 재학생", "section": "지원대상", "source": "본문", "kind": "ELIGIBILITY"},
                              {"text": "안중근 의사 관련 활동 실적이나 계획이 있는 자", "section": "신청자격", "source": "본문", "kind": "REVIEW"}])
    assert issues == []                                                                                   # 만점 미기재는 검수 사유가 아님(4.5 가정)
    assert out["deadline_kind"] == "SCHOOL_VARIES" and out["gpa"]["scale"] is None and out["gpa"]["scale_stated"] is False
    assert out["recommendation"]["from"] == ["PROFESSOR"] and "completed_terms" not in out                # 확실하지 않은 변환은 하지 않음
    assert out["other_conditions"] == ["2026년 2학기 기준 2학년 2학기 ~ 3학년 2학기 재학생"]                 # 자격(ELIGIBILITY)만 확인 필요 목록에 보임


def test_9120_ok_remaining_terms_and_unmodeled_tuition_condition():
    """OK배정: 잔여 2학기 이상, 누적 평점 4.0/4.5(3.7/4.3), 소득 8분위, '등록금을 장학금으로 해결한 자'는 사전에 없는 조건."""
    out, issues = run(
        target_levels=["UNIV", "GRAD"], remaining_terms={"min": 2}, income={"max_bracket": 8, "basis": "BRACKET"},
        gpa={"min": 4.0, "scale": 4.5, "basis": "CUMULATIVE", "alternatives": [{"min": 3.7, "scale": 4.3}]},
        unmodeled_conditions=[{"text": "26-1학기 등록금 전액을 교내·외 장학금으로 해결한 자", "section": "신청자격", "source": "본문", "kind": "ELIGIBILITY"}])
    assert issues == []
    assert out["remaining_terms"] == {"min": 2} and out["gpa"]["alternatives"] == [{"min": 3.7, "scale": 4.3}]
    assert out["unmodeled_conditions"][0]["kind"] == "ELIGIBILITY" and "등록금 전액" in out["other_conditions"][0]


def test_9217_residence_dong_level_and_special_any_of():
    """인천 SK행복: 특정 동 거주(본인 또는 부모), 기초·차상위·한부모·중증장애 중 하나, 편입생은 편입 전 학교 성적."""
    out, issues = run(
        target_levels=["HIGH", "UNIV"],
        region={"mode": "ANY", "requirements": [
            {"type": "RESIDENCE_SELF", "areas": ["인천광역시 신현원창동", "석남1동"]}, {"type": "RESIDENCE_PARENT", "areas": ["인천광역시 신현원창동", "석남1동"]}]},
        special_groups=[{"code": "BASIC_LIVELIHOOD"}, {"code": "NEAR_POOR"}, {"code": "SINGLE_PARENT"}, {"code": "DISABLED"}],
        transfer={"mode": "USE_PREV_SCHOOL"}, gpa={"min": 2.5, "scale": 4.5, "basis": "LAST_SEMESTER"}, income={"max_bracket": 6})
    assert issues == []
    assert types(out) == ["RESIDENCE_SELF", "RESIDENCE_PARENT"] and "신현원창동" in out["region"]["requirements"][0]["areas"][0]
    assert [s["code"] for s in out["special_groups"]] == ["BASIC_LIVELIHOOD", "NEAR_POOR", "SINGLE_PARENT", "DISABLED"]
    assert out["transfer"] == {"mode": "USE_PREV_SCHOOL"}
