# -*- coding: utf-8 -*-
"""스키마 v2: 유형별 조건(variants), 신청 방법(apply_how), 구버전 결과와의 호환, 프롬프트 규칙."""
import json
from datetime import date

import pytest

from scholarship.common.ai import schema
from scholarship.common.ai.prompt import SYSTEM_PROMPT


def variant(name, **kw):
    v = {"name": name}
    v.update(kw)
    return v


# 봄내장학생 같은 다유형 공고 (공통: 춘천 거주·재학, 유형별: 성적/학점/소득/기타)
BOMNAE = {
    "category": "OUT", "apply_type": "DIRECT", "apply_end": "2026-10-07", "target_levels": ["MIDDLE", "HIGH", "UNIV"],
    "apply_how": "구글폼 + 서류 우편·방문 접수, 총장(학장) 추천서 필요",
    "gpa": {"min": None, "scale": None}, "credits": {"min_last_semester": None},
    "region": {"residence": ["춘천시"]},
    "variants": [
        variant("성적우수", amount="최대 100만원", gpa={"min": 4.0, "scale": 4.5, "basis": "LAST_SEMESTER",
                                                       "alternatives": [{"min": 3.7, "scale": 4.3}]},
                credits={"min_last_semester": 12}),
        variant("일반(통리반장 자녀·주소이전)", gpa={"min": 3.0, "scale": 4.5, "basis": "LAST_SEMESTER"}, credits={"min_last_semester": 12}),
        variant("애향-저소득", income={"eligible_types": ["기초생활수급자", "차상위계층", "한부모가족"]}),
        variant("특기", other_conditions=["1년 이내 광역 대회 1위 또는 전국 대회 3위 이상"]),
        variant("자원봉사우수", other_conditions=["직전학기 봉사 50시간 이상"]),
    ],
}


def test_template_has_new_fields_and_version():
    assert "apply_how" in schema.TEMPLATE and schema.TEMPLATE["variants"] == [] and schema.SCHEMA_VERSION == 2


def test_multi_type_notice_keeps_per_type_conditions():
    d, issues = schema.normalize(BOMNAE, date(2026, 9, 17))
    assert issues == []
    assert d["gpa"]["min"] is None and d["credits"]["min_last_semester"] is None        # 공통 값은 비움
    assert d["target_levels"] == ["MIDDLE", "HIGH", "UNIV"]                                 # 공고의 학교급 전체
    assert d["apply_how"].startswith("구글폼")
    names = [v["name"] for v in d["variants"]]
    assert names == ["성적우수", "일반(통리반장 자녀·주소이전)", "애향-저소득", "특기", "자원봉사우수"]
    top, normal, lowincome = d["variants"][0], d["variants"][1], d["variants"][2]
    assert top["gpa"]["min"] == 4.0 and top["gpa"]["alternatives"] == [{"min": 3.7, "scale": 4.3}]
    assert normal["gpa"]["min"] == 3.0 and top["credits"]["min_last_semester"] == 12.0
    assert lowincome["gpa"]["min"] is None and lowincome["income"]["eligible_types"][0] == "기초생활수급자"   # 성적 기준 없는 유형
    assert d["variants"][3]["other_conditions"] and set(d["variants"][0]) == {"name", "amount", "target_levels", "gpa", "credits", "income", "region", "other_conditions"}


def test_notice_without_variants_defaults_cleanly():
    d, issues = schema.normalize({"category": "OUT", "apply_end": "2026-10-18"}, date(2026, 10, 1))
    assert d["variants"] == [] and d["apply_how"] is None and issues == []


def test_variant_problems_are_reported_not_crashing():
    raw = {"category": "OUT", "apply_end": "2026-10-18", "gpa": {"min": 3.5, "scale": 4.5}, "variants": [
        variant("A", gpa={"min": 3.0, "scale": 4.5}),                           # 공통(3.5)보다 낮은 유형 기준 -> 모순
        variant("a "),                                                          # 이름 중복(공백/대소문자 무시)
        {"amount": "이름 없음"},                                                  # 이름 없음
        "문자열 항목",                                                            # 객체가 아님 -> 무시
        variant("B", gpa={"min": 80, "scale": 4.5}),                            # 만점보다 큼
        variant("C", gpa={"min": 3.0}),                                         # 만점 단위 없음
    ]}
    d, issues = schema.normalize(raw, date(2026, 10, 1))
    fields = [i["field"] for i in issues]
    assert [v["name"] for v in d["variants"]] == ["A", "B", "C"]
    assert "variants[0].gpa" in fields                                           # 공통 vs 유형 모순
    assert "variants[1]" in fields and any(f.startswith("variants[") and "gpa.min" in f for f in fields)
    assert "variants[2].gpa.scale" in fields


def test_single_variant_and_too_many_variants():
    d, issues = schema.normalize({"category": "OUT", "apply_end": "2026-10-18", "variants": [variant("혼자")]}, None)
    assert any(i["field"] == "variants" and "1개뿐" in i["reason"] for i in issues)
    many = {"category": "OUT", "apply_end": "2026-10-18", "variants": [variant("유형{}".format(i)) for i in range(20)]}
    d2, issues2 = schema.normalize(many, None)
    assert len(d2["variants"]) == schema.MAX_VARIANTS == 12
    assert any("앞 12개만" in i["reason"] for i in issues2)


def test_non_dict_sections_do_not_crash():
    d, _ = schema.normalize({"category": "OUT", "apply_end": "2026-10-18", "gpa": "3.0 이상", "income": [1], "region": None,
                             "selection": "서류", "enrollment": 5, "variants": "없음"}, None)
    assert d["gpa"]["min"] is None and d["variants"] == [] and d["selection"]["method"] is None


def test_old_v1_result_still_normalizes():
    """실제 결과(153381, 구형식)를 그대로 넣어도 깨지지 않고 새 항목은 빈 값이어야 한다."""
    v1 = json.load(open(__file__.replace("test_schema_variants.py", "fixtures/ai_result_153381_v1.json"), encoding="utf-8"))
    d, issues = schema.normalize(v1["elig"], date(2026, 9, 17))
    assert issues == [] and d["variants"] == [] and d["apply_how"] is None
    assert d["gpa"]["min"] == 3.0 and d["region"]["residence"] == ["춘천시"] and d["apply_end"] == "2026-10-07"
    assert set(d) == set(schema.TEMPLATE)


def test_prompt_contains_v2_rules():
    for needle in ("variants", "공통", "추가로", "apply_how", "60자 이내", "최대 12개", "공고가 대상으로 하는 학교급을 모두",
                   "빼지 않습니다", "등급(장려/우수/최우수", "신청 방법·접수처·제출 방식은 자격 조건이 아니므로", "uncertain 은 최대 5개"):
        assert needle in SYSTEM_PROMPT, needle
    assert "대학생·대학원생 추천용" in SYSTEM_PROMPT and "다른 학교급 전용 조건의 불일치" in SYSTEM_PROMPT      # 대학생과 무관한 불일치로 검수 대기가 쌓이지 않게
    assert "공고에 근거가 없는 추측은 적지 않습니다" in SYSTEM_PROMPT and "마감이 임박했다'는 사실은 검수 사유가 아니므로" in SYSTEM_PROMPT   # 근거 없는 추측과 '마감 임박'은 검수 사유가 아님
    assert "빠뜨리지 말고 variants" in SYSTEM_PROMPT                                                       # 단, 조건 추출 자체는 유지
    assert '"variants": []' in SYSTEM_PROMPT and '"apply_how": null' in SYSTEM_PROMPT        # 출력 형식에도 포함
    assert "형식 예" in SYSTEM_PROMPT and '"name": "성적우수"' in SYSTEM_PROMPT


# ------------------------------------------------------------------ 실제 결과(153381 v2)에서 발견된 결함의 회귀 방지
def test_document_objects_become_readable_strings():
    d, issues = schema.normalize({"category": "OUT", "apply_end": "2026-10-18", "documents": [
        {"name": "지원대상자 추천서", "notes": "학교 총장(학장) 직인"},
        {"name": "재학증명서", "notes": None},
        {"title": "통장 사본"},
        "{'name': '성적증명서', 'notes': '직전학기'}",             # 과거에 dict repr 문자열로 저장된 형태도 복구
        "그냥 문자열", {"x": 1, "y": None}, None, ""]}, None)
    assert d["documents"] == ["지원대상자 추천서 (학교 총장(학장) 직인)", "재학증명서", "통장 사본", "성적증명서 (직전학기)",
                              "그냥 문자열", "1"]
    assert issues == []


def test_real_153381_v2_result_documents_are_repaired():
    v2 = json.load(open(__file__.replace("test_schema_variants.py", "fixtures/ai_result_153381_v2.json"), encoding="utf-8"))
    assert v2["elig"]["documents"][0].startswith("{'name'")                      # 저장된 결과에는 결함이 그대로 남아 있음
    d, issues = schema.normalize(v2["elig"], date(2026, 9, 17))
    assert d["documents"][0] == "지원신청서 및 서약서"
    assert d["documents"][3] == "지원대상자 추천서 (학교 총장(학장) 또는 읍면동장 직인)"
    assert not any(x.startswith("{") for x in d["documents"]) and len(d["documents"]) == 12
    assert len(d["variants"]) == 11 and d["gpa"]["min"] is None and d["target_levels"] == ["MIDDLE", "HIGH", "UNIV"]
    assert issues == []


def test_compact_variants_with_omitted_keys_are_filled_with_defaults():
    raw = {"category": "OUT", "apply_end": "2026-10-18", "target_levels": ["HIGH", "UNIV"], "variants": [
        {"name": "성적우수", "gpa": {"min": 4.0, "scale": 4.5}, "credits": {"min_last_semester": 12}},
        {"name": "성적향상", "target_levels": ["HIGH"], "other_conditions": ["학교장 추천"]}]}
    d, issues = schema.normalize(raw, date(2026, 10, 1))
    assert issues == []
    a, b = d["variants"]
    assert a["income"] == {"max_bracket": None, "eligible_types": [], "notes": None} and a["region"]["residence"] == []
    assert a["gpa"]["basis"] == "UNKNOWN" and a["target_levels"] == []                   # 비우면 최상위와 같음
    assert b["target_levels"] == ["HIGH"] and b["gpa"]["min"] is None and b["other_conditions"] == ["학교장 추천"]


def test_variant_target_levels_must_fit_inside_top_level():
    raw = {"category": "OUT", "apply_end": "2026-10-18", "target_levels": ["UNIV"], "variants": [
        {"name": "A", "target_levels": ["UNIV"]}, {"name": "B", "target_levels": ["HIGH", "ALIEN"]}]}
    d, issues = schema.normalize(raw, date(2026, 10, 1))
    reasons = " | ".join(i["reason"] for i in issues if i["field"] == "variants[1].target_levels")
    assert "알 수 없는 학교급" in reasons and "최상위 target_levels 에 없음" in reasons
    assert d["variants"][1]["target_levels"] == ["HIGH"] and not any(i["field"] == "variants[0].target_levels" for i in issues)


def test_prompt_has_compact_variant_and_string_list_rules():
    for needle in ("값이 없는 키(null, 빈 목록, 빈 객체)를 적지 않습니다", "그 유형의 target_levels 에 적습니다",
                   "모든 학교급을 합친 값" if False else "모든 유형의 학교급을 합친 값", "값이 있는 키만 적음",
                   "항목은 문자열입니다 (객체나"):
        assert needle in SYSTEM_PROMPT, needle
    assert '"name": "성적향상", "target_levels": ["HIGH"]' in SYSTEM_PROMPT
