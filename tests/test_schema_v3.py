# -*- coding: utf-8 -*-
"""스키마 v3 정규화: 확장 조건, 표준 값이 아닐 때 '사전에 없는 조건'으로 옮기기, v2 호환(v3 가 꺼져 있으면 결과가 같음)."""
import copy
import json
from datetime import date

import pytest

from scholarship.common.ai import schema, schema_v3
from scholarship.common.ai.schema import normalize

TODAY = date(2026, 9, 21)

V3_SAMPLE = {
    "category": "OUT", "scholarship_name": "문주장학재단 AI·ICT 인재장학", "foundation": "서초구", "apply_type": "DIRECT",
    "apply_start": "2026-09-21", "apply_end": "2026-10-08", "target_levels": ["UNIV"], "grades": [2, 3, 4],
    "gpa": {"min": 3.0, "scale": 4.5, "basis": "LAST_SEMESTER", "scale_stated": True},
    "credits": {"min_last_semester": 12},
    "region": {"mode": "ANY", "requirements": [
        {"type": "RESIDENCE_SELF", "areas": ["서울특별시 서초구"], "min_years": 1},
        {"type": "residence_parent", "areas": ["서울특별시 서초구"], "min_years": 1}]},
    "major_groups": ["ENGINEERING"], "majors": ["AI·빅데이터 등 ICT 관련 전공"],
    "exclusion_items": [{"type": "CONCURRENT_TUITION", "text": "타 장학금 수령 후 실 납부금액 100만원 미만 제외"}],
    "obligations": ["장학증서 수여식 필참"],
    "unmodeled_conditions": [{"text": "수여식 필참", "section": "활동사항", "source": "본문", "kind": "OBLIGATION"}],
    "summary": "서초구 거주 ICT 전공 대학생 등록금 지원",
}


def n3(obj, **kw):
    return normalize(obj, kw.pop("posted", TODAY), "", version=3)


def reasons(issues):
    return " | ".join("{}: {}".format(i["field"], i["reason"]) for i in issues)


# ------------------------------------------------------------------ v3 정상 입력
def test_v3_sample_is_normalized_without_issues():
    out, issues = n3(V3_SAMPLE)
    assert issues == []
    assert out["gpa"] == {"min": 3.0, "scale": 4.5, "basis": "LAST_SEMESTER", "alternatives": [], "notes": None, "scale_stated": True}
    reg = out["region"]
    assert reg["mode"] == "ANY" and [r["type"] for r in reg["requirements"]] == ["RESIDENCE_SELF", "RESIDENCE_PARENT"]   # 소문자 입력도 표준화
    assert reg["requirements"][0] == {"type": "RESIDENCE_SELF", "areas": ["서울특별시 서초구"], "min_years": 1}
    assert reg["residence"] == [] and reg["school_location"] == []                                   # v2 필드는 그대로 비어 있음
    assert out["major_groups"] == ["ENGINEERING"] and out["obligations"] == ["장학증서 수여식 필참"]
    assert out["exclusion_items"] == [{"type": "CONCURRENT_TUITION", "text": "타 장학금 수령 후 실 납부금액 100만원 미만 제외"}]
    assert out["unmodeled_conditions"] == [{"text": "수여식 필참", "section": "활동사항", "source": "본문", "kind": "OBLIGATION"}]


def test_v3_fills_v2_lists_for_display_compat():
    out, _ = n3(V3_SAMPLE)
    assert out["exclusions"] == ["타 장학금 수령 후 실 납부금액 100만원 미만 제외"]               # v2 판정 코드가 '제외 조건 확인'을 계속 보여줌
    assert out["other_conditions"] == []                                                              # 의무(OBLIGATION)는 자격이 아니므로 복사하지 않음
    o2 = copy.deepcopy(V3_SAMPLE)
    o2["unmodeled_conditions"].append({"text": "역사 관련 활동 실적이 있는 자", "kind": "ELIGIBILITY", "section": "신청자격", "source": "본문"})
    out2, _ = n3(o2)
    assert out2["other_conditions"] == ["역사 관련 활동 실적이 있는 자"]
    o3 = copy.deepcopy(o2)
    o3["exclusions"], o3["other_conditions"] = ["AI 가 직접 적은 제외"], ["AI 가 직접 적은 조건"]
    out3, _ = n3(o3)
    assert out3["exclusions"] == ["AI 가 직접 적은 제외"] and out3["other_conditions"] == ["AI 가 직접 적은 조건"]   # AI 가 적었다면 덮어쓰지 않음


def test_v3_keys_are_omitted_when_empty():
    out, issues = n3({"category": "OUT", "apply_end": "2026-10-08", "summary": "x"})
    assert set(out) == set(schema.TEMPLATE)                                                          # v3 키가 하나도 안 생김
    assert out["region"].keys() == {"residence", "school_location", "notes"}
    assert out["gpa"].keys() == {"min", "scale", "basis", "alternatives", "notes"}
    assert out["income"].keys() == {"max_bracket", "eligible_types", "notes"}


# ------------------------------------------------------------------ v2 호환
def test_v2_mode_ignores_v3_keys_completely():
    a, ia = normalize(V3_SAMPLE, TODAY, "")
    b, ib = normalize(V3_SAMPLE, TODAY, "", version=2)
    assert a == b and ia == ib
    assert set(a) == set(schema.TEMPLATE)
    assert a["region"].keys() == {"residence", "school_location", "notes"} and "scale_stated" not in a["gpa"]
    assert a["exclusions"] == [] and a["other_conditions"] == []                                      # v2 모드에서는 채우지 않음
    assert schema.SCHEMA_VERSION == 2 and schema.SCHEMA_VERSION_V3 == 3


def test_v2_shaped_input_gives_same_v2_fields_in_v3_mode():
    v2_in = {"category": "OUT", "apply_type": "SCHOOL", "apply_start": "2026-09-04", "apply_end": "2026-10-25", "target_levels": ["UNIV"],
             "grades": [3, 4], "gpa": {"min": 80, "scale": 100, "basis": "EITHER"}, "credits": {"min_last_semester": 12},
             "income": {"max_bracket": 8, "eligible_types": ["수급자"]}, "region": {"residence": ["춘천시"], "school_location": ["강원"]},
             "majors": ["공학"], "special_status": ["한부모"], "other_conditions": ["TOEIC 700"], "exclusions": ["휴학생"],
             "variants": [{"name": "A", "gpa": {"min": 3.0, "scale": 4.5}}, {"name": "B"}], "summary": "요약"}
    o2, i2 = normalize(v2_in, TODAY, "", version=2)
    o3, i3 = normalize(v2_in, TODAY, "", version=3)
    assert {k: o3[k] for k in schema.TEMPLATE} == {k: o2[k] for k in schema.TEMPLATE} and set(o3) == set(o2)    # v3 로 처리해도 v2 입력의 결과는 같음
    assert i2 == i3


def test_v2_still_flags_missing_scale_but_v3_does_not():
    obj = {"category": "OUT", "apply_end": "2026-10-08", "gpa": {"min": 3.6, "basis": "CUMULATIVE"}}
    _, i2 = normalize(obj, TODAY, "", version=2)
    out, i3 = n3(obj)
    assert any(i["field"] == "gpa.scale" for i in i2)
    assert not any(i["field"] == "gpa.scale" for i in i3)                                            # 4.5 가정(결정)이라 검수 사유가 아님
    assert out["gpa"]["scale"] is None and out["gpa"]["scale_stated"] is False                       # 대신 '만점 미기재'로 표시


def test_hybrid_and_new_gpa_basis_only_in_v3():
    obj = {"category": "OUT", "apply_end": "2026-10-08", "apply_type": "HYBRID", "gpa": {"min": 3.0, "scale": 4.5, "basis": "LAST_N", "terms": 2}}
    o2, _ = normalize(obj, TODAY, "", version=2)
    o3, i3 = n3(obj)
    assert o2["apply_type"] == "UNKNOWN" and o2["gpa"]["basis"] == "UNKNOWN"
    assert o3["apply_type"] == "HYBRID" and o3["gpa"]["basis"] == "LAST_N" and o3["gpa"]["terms"] == 2 and i3 == []


# ------------------------------------------------------------------ 지역 / 성적 / 소득
def test_region_mode_required_when_multiple_requirements():
    two = {"requirements": [{"type": "RESIDENCE_SELF", "areas": ["수원시"]}, {"type": "SCHOOL_LOCATION", "areas": ["비수도권"]}]}
    out, issues = n3({"category": "OUT", "apply_end": "2026-10-08", "region": two})
    assert out["region"]["mode"] == "UNKNOWN" and "ANY" in reasons(issues) and any(i["field"] == "region.mode" for i in issues)
    one, issues1 = n3({"category": "OUT", "apply_end": "2026-10-08", "region": {"requirements": two["requirements"][:1]}})
    assert one["region"]["mode"] == "ALL" and not any(i["field"] == "region.mode" for i in issues1)  # 요건이 하나면 구분이 필요 없음
    both, i2 = n3({"category": "OUT", "apply_end": "2026-10-08", "region": dict(two, mode="all")})
    assert both["region"]["mode"] == "ALL" and i2 == []


def test_region_requirement_details_and_caps():
    reg = {"mode": "ANY", "requirements": [
        {"type": "RESIDENCE_PARENT", "areas": "경기도 수원시", "min_years": "3", "years_basis": "고교 졸업일 기준 " + "가" * 80},
        {"type": "GRADUATED_SCHOOL", "areas": ["거제시"], "min_years": -1},
        {"type": "엉뚱한유형", "areas": ["서울"]}, "문자열 항목", {"areas": ["x"]}] + [{"type": "SCHOOL_LOCATION", "areas": [str(i)]} for i in range(12)]}
    out, issues = n3({"category": "OUT", "apply_end": "2026-10-08", "region": reg})
    reqs = out["region"]["requirements"]
    assert len(reqs) == schema_v3.MAX_REQUIREMENTS and reqs[0]["min_years"] == 3 and reqs[0]["areas"] == ["경기도 수원시"]
    assert len(reqs[0]["years_basis"]) == 40 and "min_years" not in reqs[1]
    assert any("지역 요건(유형 불명: 엉뚱한유형)" in u["text"] for u in out["unmodeled_conditions"])      # 버리지 않고 사전에 없는 조건으로 이동


def test_gpa_last_n_requires_terms():
    obj = {"category": "OUT", "apply_end": "2026-10-08", "gpa": {"min": 3.5, "scale": 4.5, "basis": "LAST_N"}}
    out, issues = n3(obj)
    assert "terms" not in out["gpa"] and any(i["field"] == "gpa.terms" for i in issues)
    for bad in (0, 13, "많이"):
        obj["gpa"]["terms"] = bad
        out, issues = n3(obj)
        assert "terms" not in out["gpa"] and any(i["field"] == "gpa.terms" for i in issues)
    obj["gpa"]["terms"] = "2"
    out, issues = n3(obj)
    assert out["gpa"]["terms"] == 2 and issues == []
    obj["gpa"].update(basis="LAST_SEMESTER", terms=2)                                                # N개 학기가 아닌데 terms 가 있으면 무시
    assert "terms" not in n3(obj)[0]["gpa"]


def test_income_extras():
    inc = {"max_bracket": 5, "eligible_types": ["수급자"], "basis": "median_percent", "bracket_term": "last_term",
           "max_median_percent": 130, "max_asset_krw": "409,000,000"}
    out, _ = n3({"category": "OUT", "apply_end": "2026-10-08", "income": inc})
    assert out["income"]["basis"] == "MEDIAN_PERCENT" and out["income"]["bracket_term"] == "LAST_TERM"
    assert out["income"]["max_median_percent"] == 130 and out["income"]["max_asset_krw"] == 409000000
    bad, _ = n3({"category": "OUT", "apply_end": "2026-10-08", "income": {"basis": "몰라", "max_median_percent": -5, "max_asset_krw": 0}})
    assert not ({"basis", "max_median_percent", "max_asset_krw"} & set(bad["income"]))


# ------------------------------------------------------------------ 학기·신분·기타 조건
def test_term_ranges_age_and_entry():
    obj = {"category": "OUT", "apply_end": "2026-10-08", "completed_terms": {"min": 4, "max": "6"}, "remaining_terms": {"max": 2},
           "age": {"max": 39, "min": 15}, "entry_years": [2025, "2021", 1800, "x"], "new_student_only": True, "transfer": {"mode": "use-prev-school"}}
    out, issues = n3(obj)
    assert issues == []
    assert out["completed_terms"] == {"min": 4, "max": 6} and out["remaining_terms"] == {"max": 2} and out["age"] == {"min": 15, "max": 39}
    assert out["entry_years"] == [2021, 2025] and out["new_student_only"] is True and out["transfer"] == {"mode": "USE_PREV_SCHOOL"}
    bad, issues = n3({"category": "OUT", "apply_end": "2026-10-08", "age": {"min": 40, "max": 20}, "completed_terms": {"min": 99}, "new_student_only": "예"})
    assert any(i["field"] == "age" for i in issues) and "completed_terms" not in bad and "new_student_only" not in bad


def test_special_groups_defaults_dedupe_and_unknown_codes_move():
    obj = {"category": "OUT", "apply_end": "2026-10-08", "special_groups": [
        {"code": "basic_livelihood"}, {"code": "BASIC_LIVELIHOOD", "scope": "self", "mode": "required"},
        {"code": "DISABLED", "scope": "FAMILY", "mode": "PREFERRED"}, "NEAR_POOR",
        {"code": "OTHER", "text": "군인 가족 " + "가" * 80}, {"code": "외계인", "text": "화성 출신"}]}
    out, issues = n3(obj)
    sg = out["special_groups"]
    assert sg[0] == {"code": "BASIC_LIVELIHOOD", "scope": "SELF", "mode": "REQUIRED"} and len([s for s in sg if s["code"] == "BASIC_LIVELIHOOD"]) == 1
    assert {"code": "DISABLED", "scope": "FAMILY", "mode": "PREFERRED"} in sg and {"code": "NEAR_POOR", "scope": "SELF", "mode": "REQUIRED"} in sg
    other = [s for s in sg if s["code"] == "OTHER"][0]
    assert len(other["text"]) == 60
    assert any("신분 조건(표준 코드 아님): 화성 출신" == u["text"] for u in out["unmodeled_conditions"])      # 조건이 사라지지 않음


def test_waivers_valid_and_invalid():
    obj = {"category": "OUT", "apply_end": "2026-10-08", "waivers": [
        {"when": {"student": ["new_student", "TRANSFER", "alien"], "special": ["disabled"]}, "skip": ["GPA", "credits", "몰라"]},
        {"when": {"student": ["REENTRY"]}, "skip": []}, {"when": {}, "skip": ["gpa"]}, "문자열"]}
    out, _ = n3(obj)
    assert out["waivers"] == [{"when": {"student": ["NEW_STUDENT", "TRANSFER"], "special": ["DISABLED"]}, "skip": ["gpa", "credits"]}]
    assert sum("면제 조건(해석 불가)" in u["text"] for u in out["unmodeled_conditions"]) == 3             # 해석 못 한 면제 3개는 사전에 없는 조건으로


@pytest.mark.parametrize("raw, want", [("여자", "F"), ("여학생", "F"), ("F", "F"), ("female", "F"), ("남학생", "M"), ("M", "M"), ("남", "M")])
def test_gender_words(raw, want):
    assert n3({"category": "OUT", "apply_end": "2026-10-08", "gender": raw})[0]["gender"] == want


def test_gender_unknown_is_omitted():
    assert "gender" not in n3({"category": "OUT", "apply_end": "2026-10-08", "gender": "무관"})[0]


def test_major_school_language_prereq_recommendation():
    obj = {"category": "OUT", "apply_end": "2026-10-08", "major_groups": ["engineering", "NATURAL", "이상한계열"],
           "school": {"types": ["univ4"], "excluded_types": ["CYBER", "open_univ"], "programs": ["national_scholarship", "외계사업"]},
           "language_requirements": [{"exam": "TOEIC", "min_score": "700"}, {"exam": "HSK", "level": "5급"}, {"min_score": 5}],
           "prerequisites": ["국가장학금 신청 필수", {"type": "ADMISSION_OFFER", "text": "해외 대학 입학허가"}, {"type": "degree_held"}, {}],
           "recommendation": {"from": ["professor", "President"]}, "deadline_kind": "school_varies"}
    out, _ = n3(obj)
    assert out["major_groups"] == ["ENGINEERING", "NATURAL"]
    assert out["school"] == {"types": ["UNIV4"], "excluded_types": ["CYBER", "OPEN_UNIV"], "programs": ["NATIONAL_SCHOLARSHIP"]}
    assert out["language_requirements"] == [{"exam": "TOEIC", "min_score": 700}, {"exam": "HSK", "level": "5급"}]
    assert out["prerequisites"] == [{"type": "OTHER", "text": "국가장학금 신청 필수"}, {"type": "ADMISSION_OFFER", "text": "해외 대학 입학허가"},
                                    {"type": "DEGREE_HELD"}]
    assert out["recommendation"] == {"required": True, "from": ["PROFESSOR", "PRESIDENT"]}             # 추천 주체를 적었으면 필요한 것으로
    assert out["deadline_kind"] == "SCHOOL_VARIES"
    texts = [u["text"] for u in out["unmodeled_conditions"]]
    assert "전공 계열(표준 값 아님): 이상한계열" in texts and "참여 사업(표준 값 아님): 외계사업" in texts
    explicit, _ = n3({"category": "OUT", "apply_end": "2026-10-08", "recommendation": {"required": False}})
    assert explicit["recommendation"] == {"required": False, "from": []}


def test_exclusion_items_obligations_review_and_caps():
    obj = {"category": "OUT", "apply_end": "2026-10-08",
           "exclusion_items": [{"type": "prior_same", "text": "직전학기 본 재단 장학생 제외"}, {"type": "???", "text": "기타 제한"}, {"type": "OTHER"}, "문자열 제한"],
           "obligations": ["수여식 필참"] * 3 + ["가" * 150], "review_criteria": ["인재상 공감"]}
    out, _ = n3(obj)
    assert out["exclusion_items"] == [{"type": "PRIOR_SAME", "text": "직전학기 본 재단 장학생 제외"}, {"type": "OTHER", "text": "기타 제한"},
                                      {"type": "OTHER", "text": "문자열 제한"}]
    assert len(out["obligations"][3]) == 100 and out["review_criteria"] == ["인재상 공감"]


def test_unmodeled_conditions_dedupe_cut_and_cap():
    items = [{"text": "조건 {}".format(i), "kind": "eligibility"} for i in range(25)]
    items += [{"text": "조건 1"}, {"text": "가" * 300, "section": "신" * 50, "kind": "엉뚱"}, "문자열 조건", {"text": "  "}]
    out, issues = n3({"category": "OUT", "apply_end": "2026-10-08", "unmodeled_conditions": items})
    um = out["unmodeled_conditions"]
    assert len(um) == schema_v3.MAX_UNMODELED and um[0] == {"text": "조건 0", "section": "", "source": "", "kind": "ELIGIBILITY"}
    assert sum(u["text"] == "조건 1" for u in um) == 1                                                   # 같은 문구는 한 번만
    over = [i for i in issues if i["field"] == "unmodeled_conditions"]
    assert over and "20개를 넘어" in over[0]["reason"]                                                   # 넘쳐서 못 담은 것은 검수 사유
    big, _ = n3({"category": "OUT", "apply_end": "2026-10-08", "unmodeled_conditions": [{"text": "가" * 300, "section": "신" * 50}]})
    assert len(big["unmodeled_conditions"][0]["text"]) == 120 and len(big["unmodeled_conditions"][0]["section"]) == 30


# ------------------------------------------------------------------ 유형(variants)
def test_variants_get_v3_extras():
    obj = {"category": "OUT", "apply_end": "2026-10-08", "variants": [
        {"name": "일반", "apply_end": "2026-10-20", "apply_type": "hybrid", "gender": "여", "age": {"max": 29},
         "special_groups": [{"code": "BASIC_LIVELIHOOD", "mode": "PREFERRED"}, {"code": "?"}],
         "gpa": {"min": 3.0, "scale": 4.5, "basis": "LAST_N", "terms": 2}, "income": {"max_bracket": 5, "basis": "BRACKET"},
         "region": {"requirements": [{"type": "RESIDENCE_SELF", "areas": ["수원시"]}]}, "major_groups": ["ENGINEERING", "??"],
         "completed_terms": {"min": 2}, "entry_years": [2026], "transfer": {"mode": "EXCLUDED"}},
        {"name": "저소득", "apply_end": "내일", "apply_type": "UNKNOWN"}]}
    out, issues = n3(obj)
    a, b = out["variants"]
    assert a["apply_end"] == "2026-10-20" and a["apply_type"] == "HYBRID" and a["gender"] == "F" and a["age"] == {"max": 29}
    assert a["special_groups"] == [{"code": "BASIC_LIVELIHOOD", "scope": "SELF", "mode": "PREFERRED"}]
    assert a["gpa"]["terms"] == 2 and a["income"]["basis"] == "BRACKET" and a["region"]["requirements"][0]["areas"] == ["수원시"]
    assert a["major_groups"] == ["ENGINEERING"] and a["completed_terms"] == {"min": 2} and a["entry_years"] == [2026] and a["transfer"] == {"mode": "EXCLUDED"}
    assert any("신분 조건(표준 코드 아님)" in c for c in a["other_conditions"]) and any("전공 계열(표준 값 아님)" in c for c in a["other_conditions"])
    assert "apply_end" not in b and "apply_type" not in b and any(i["field"] == "variants[1].apply_end" for i in issues)


def test_variants_in_v2_mode_have_no_v3_keys():
    obj = {"category": "OUT", "apply_end": "2026-10-08", "variants": [{"name": "A", "apply_end": "2026-10-20", "gender": "F"}, {"name": "B"}]}
    out, _ = normalize(obj, TODAY, "", version=2)
    assert set(out["variants"][0]) == {"name", "amount", "target_levels", "gpa", "credits", "income", "region", "other_conditions"}


# ------------------------------------------------------------------ 출력 형식·경로
def test_template_v3_extends_v2_and_paths_are_consistent():
    for k in schema.TEMPLATE:
        assert k in schema_v3.TEMPLATE_V3
    assert schema_v3.TEMPLATE_V3["region"].keys() >= {"residence", "school_location", "notes", "mode", "requirements"}
    json.dumps(schema_v3.TEMPLATE_V3, ensure_ascii=False)
    paths = schema_v3.V3_PATHS
    for p in ("gpa.min", "gpa.terms", "region.requirements", "region.requirements[].min_years", "special_groups[].code", "completed_terms.min",
              "age.max", "gender", "income.max_median_percent", "unmodeled_conditions[].text", "prerequisites[].type", "transfer.mode"):
        assert p in paths, p
    assert "history.max_prior_terms" not in paths and "gpa.nope" not in paths
    assert schema.TEMPLATE["gpa"].keys() == {"min", "scale", "basis", "alternatives", "notes"}              # v2 출력 형식은 건드리지 않음


def test_non_dict_still_raises_in_v3():
    with pytest.raises(ValueError):
        normalize([1, 2], TODAY, "", version=3)


# ------------------------------------------------------------------ 유형별 마감만 있을 때 최상위 마감 (시험 추출 9211 에서 발견)
def variants_only_deadlines(**extra):
    obj = {"category": "OUT", "summary": "x", "variants": [{"name": "외국인 유학생", "apply_start": "2026-09-01", "apply_end": "2026-09-23"},
                                                           {"name": "성취", "apply_start": "2026-08-20", "apply_end": "2026-10-07"}]}
    obj.update(extra)
    return obj


def test_v3_fills_top_level_deadlines_from_variants():
    out, issues = normalize(variants_only_deadlines(), TODAY, "", version=3)
    assert out["apply_end"] == "2026-10-07" and out["apply_start"] == "2026-08-20"                    # 가장 늦은 마감, 가장 이른 시작
    assert [v["apply_end"] for v in out["variants"]] == ["2026-09-23", "2026-10-07"]                  # 유형별 날짜는 그대로
    assert not any(i["field"] == "apply_end" for i in issues)                                         # 교외 장학금인데 마감이 없다는 거짓 경고가 사라짐


def test_top_level_deadline_is_kept_and_v2_is_unchanged():
    out, _ = normalize(variants_only_deadlines(apply_end="2026-10-30", apply_start="2026-09-01"), TODAY, "", version=3)
    assert out["apply_end"] == "2026-10-30" and out["apply_start"] == "2026-09-01"                    # AI 가 적은 값은 덮어쓰지 않음
    out2, issues2 = normalize(variants_only_deadlines(), TODAY, "", version=2)
    assert out2["apply_end"] is None and any(i["field"] == "apply_end" for i in issues2)               # v2 는 기존 동작 그대로


def test_variant_deadline_edge_cases():
    out, issues = normalize({"category": "OUT", "summary": "x", "variants": [{"name": "A", "apply_end": "내일"}, {"name": "B", "apply_end": "2026-10-01"}]},
                            TODAY, "", version=3)
    assert out["apply_end"] == "2026-10-01" and any(i["field"] == "variants[0].apply_end" for i in issues)   # 읽을 수 있는 날짜만 사용, 잘못된 형식은 이슈
    none, issues2 = normalize({"category": "OUT", "summary": "x", "variants": [{"name": "A"}, {"name": "B"}]}, TODAY, "", version=3)
    assert none["apply_end"] is None and any(i["field"] == "apply_end" for i in issues2)               # 어디에도 마감이 없으면 기존처럼 이슈


# ------------------------------------------------------------------ 시험 추출 5건에서 나온 실제 AI 출력의 문제 (요약 표기, 유형별 국적)
def test_summary_tags_are_filtered_but_real_restrictions_are_kept():
    """AI 가 '※ 중복수혜 : 일부 가능'(드림스폰 요약 표기)을 조건처럼 적은 것을 걸러낸다. 실제 AI 출력 문구 그대로."""
    out, _ = n3({"category": "OUT", "apply_end": "2026-10-08",
                 "exclusion_items": [{"type": "OTHER", "text": "중복수혜 : 일부 가능"},                                   # 9211, 9096
                                     {"type": "CONCURRENT_TUITION", "text": "중복수혜 : 일부 가능 (등록금 범위 내)"},      # 9195: 가짜 제한 조건으로 둔갑한 것
                                     {"type": "CONCURRENT_TUITION", "text": "타 장학금 수령액이 있을 시 차액(실 납부금액)이 100만원 미만인 자 선발 제외 (중복수혜 일부 가능)"},
                                     {"type": "CONCURRENT_ANY", "text": "중복수혜 : 불가"}],
                 "unmodeled_conditions": [{"text": "중복수혜 : 일부 가능", "section": "장학혜택", "source": "본문", "kind": "EXCLUSION"},      # 9098
                                          {"text": "장학금 수혜 등으로 본인 부담 등록금이 1만원 미만인 자 선발 제외", "section": "장학혜택", "source": "본문", "kind": "EXCLUSION"}]})
    texts = [e["text"] for e in out["exclusion_items"]]
    assert len(texts) == 2 and texts[0].startswith("타 장학금 수령액이") and texts[1] == "중복수혜 : 불가"            # 요약 표기 두 개는 사라지고 실제 제한은 남음
    assert len(out["unmodeled_conditions"]) == 1 and out["unmodeled_conditions"][0]["text"].startswith("장학금 수혜 등으로 본인 부담 등록금")
    assert out["exclusions"] == [e["text"] for e in out["exclusion_items"]]                                          # v2 표시용 목록에도 요약 표기가 안 들어감


@pytest.mark.parametrize("text, drop", [("중복수혜 : 일부 가능", True), ("※ 중복수혜 : 가능", True), ("중복수혜: 정보없음", True), ("중복수혜 : 미기재", True),
                                        ("중복수혜 : 일부 가능 (등록금 범위 내)", True), ("중복수혜 : 불가", False),
                                        ("중복수혜 : 일부 가능 - 본인부담 등록금 1만원 미만 제외", False), ("타 장학금 중복 수혜자 제외", False), ("", False)])
def test_summary_tag_detection(text, drop):
    assert schema_v3.is_summary_tag(text) is drop


def test_variant_nationality_for_identity_like_variants():
    """유형 이름이 국적·신분이면 구조로도 담는다 (9211 '외국인 유학생'). 없으면 키가 없다."""
    out, _ = n3({"category": "OUT", "apply_end": "2026-10-08",
                 "variants": [{"name": "외국인 유학생", "nationality": "외국인(유학생)", "apply_type": "SCHOOL"}, {"name": "성취 장학"}]})
    a, b = out["variants"]
    assert a["nationality"] == "외국인(유학생)" and "nationality" not in b
    long, _ = n3({"category": "OUT", "apply_end": "2026-10-08", "variants": [{"name": "A", "nationality": "가" * 80}, {"name": "B"}]})
    assert len(long["variants"][0]["nationality"]) == 40
    out2, _ = normalize({"category": "OUT", "apply_end": "2026-10-08", "variants": [{"name": "A", "nationality": "외국인"}, {"name": "B"}]}, TODAY, "", version=2)
    assert "nationality" not in out2["variants"][0]                                                                  # v2 모드에서는 없음
