# -*- coding: utf-8 -*-
"""스키마 v3 시험 추출의 대조 로직: 공고 파일 읽기, 기대 데이터, 조건 항목 감지, 사라짐/원문만 구분, 크레딧 측정, 보고서."""
import copy
from pathlib import Path

import pytest

from scholarship.common import registry as R
from scholarship.common.ai.schema import normalize
from scholarship.sources import ext02_trial as T

FIX = Path(__file__).parent / "fixtures" / "ext02_survey_sample.txt"

# AI 가 이렇게 써 주기를 기대하는 출력 (위 발췌 공고 3건의 본문을 읽고 쓴 것)
IDEAL = {
    "9212": {"category": "OUT", "apply_type": "DIRECT", "apply_start": "2026-09-21", "apply_end": "2026-10-08", "target_levels": ["UNIV"], "grades": [2, 3, 4],
             "gpa": {"min": 3.0, "scale": 4.5, "basis": "LAST_SEMESTER", "scale_stated": True}, "credits": {"min_last_semester": 12},
             "region": {"mode": "ANY", "requirements": [{"type": "RESIDENCE_SELF", "areas": ["서울특별시 서초구"], "min_years": 1},
                                                         {"type": "RESIDENCE_PARENT", "areas": ["서울특별시 서초구"], "min_years": 1}]},
             "major_groups": ["ENGINEERING"], "majors": ["AI, 빅데이터 등 ICT 관련 전공자"],
             "exclusion_items": [{"type": "CONCURRENT_TUITION", "text": "타 장학금 수령 후 실 납부금액 100만원 미만인 자 선발 제외"}],
             "obligations": ["장학증서 수여식 필참"], "summary": "서초구 거주 ICT 전공 대학생 등록금 지원"},
    "9098": {"category": "OUT", "apply_type": "DIRECT", "apply_start": "2026-06-01", "apply_end": "2026-07-01", "target_levels": ["UNIV"],
             "school": {"excluded_types": ["CYBER", "OPEN_UNIV", "COMPANY_UNIV"]}, "income": {"basis": "NARRATIVE"},
             "special_groups": [{"code": "DISABLED", "mode": "REQUIRED"}, {"code": "BASIC_LIVELIHOOD", "mode": "PREFERRED"}, {"code": "NEAR_POOR", "mode": "PREFERRED"}],
             "recommendation": {"required": True, "from": ["PRINCIPAL", "OTHER"]},
             "exclusion_items": [{"type": "PRIOR_SAME", "text": "본 법인의 장학금 기수혜자 선발 제외"}],
             "obligations": ["오프라인 장학금 전달식 참여 가능한 자"],
             "review_criteria": ["학업 우수·봉사정신", "자기소개서에 나눔 실천 의지를 구체적으로 표현"], "summary": "장애인 대학생 장학"},
    "9096": {"category": "OUT", "apply_type": "DIRECT", "apply_start": "2026-05-15", "apply_end": "2026-06-15", "target_levels": ["UNIV"],
             "gpa": {"min": 2.5, "scale": 4.5, "basis": "LAST_SEMESTER", "scale_stated": True}, "credits": {"min_last_semester": 12},
             "region": {"mode": "ANY", "requirements": [{"type": "RESIDENCE_SELF", "areas": ["경상남도 거창군"], "min_years": 3},
                                                         {"type": "GRADUATED_SCHOOL", "areas": ["경상남도 거창군"]},
                                                         {"type": "RESIDENCE_PARENT", "areas": ["경상남도 거창군"], "min_years": 3}]},
             "age": {"max": 29}, "school": {"programs": ["NATIONAL_SCHOLARSHIP"]},
             "prerequisites": [{"type": "NATIONAL_SCHOLARSHIP_APPLIED", "text": "해당 학기 신청자"}],
             "exclusion_items": [{"type": "CONCURRENT_TUITION", "text": "본인 부담 등록금이 1만원 미만인 자 선발 제외"}], "summary": "거창군 출신 대학생 등록금 지원"},
}


def norm(idx, **patch):
    obj = copy.deepcopy(IDEAL[idx])
    obj.update(patch)
    out, issues = normalize(obj, None, "", version=3)
    return out


@pytest.fixture(scope="module")
def reg():
    return R.load_registry()


@pytest.fixture(scope="module")
def expected():
    return T.load_expected()


# ------------------------------------------------------------------ 파일 읽기와 기대 데이터
def test_parse_notice_file_splits_header_and_body():
    ns = T.parse_notice_file(FIX.read_text(encoding="utf-8"))
    assert [n.idx for n in ns] == ["9212", "9098", "9096"]
    n = ns[0]
    assert n.state == "마감임박" and n.title.startswith("문주장학재단 AI·ICT") and n.org == "서초구"
    assert n.body.startswith("드림스폰 장학 정보") and "### [" not in n.body and "신청기간 원문:" not in n.body and "AI, 빅데이터 등 ICT 관련 전공자" in n.body
    assert T.parse_notice_file("") == [] and T.parse_notice_file("아무 내용\n### 잘못된 머리말") == []


def test_expected_data_and_sample_are_consistent(expected):
    notices, names = expected
    assert len(notices) == 61 and len(T.DEFAULT_SAMPLE) == 20 == len(set(T.DEFAULT_SAMPLE))
    assert set(T.DEFAULT_SAMPLE) <= set(notices)
    assert all(codes <= set(names) for codes in notices.values())                                     # 모르는 코드가 없음
    assert notices["9212"] == {"RES", "RESP", "RESY", "YEAR", "MAJ", "GL", "CR", "OBL", "CONC"}
    assert notices["9096"] == {"AGE", "SPART", "RES", "RESP", "RESY", "HSL", "CR", "GL", "NSA", "CONC", "TUITION"}
    assert notices["9098"] == {"SCH_TYPE", "DIS", "REC", "INCT", "BASIC", "ESSAY", "PRIOR", "OBL"}
    assert T.DEFAULT_SAMPLE[:5] == ["9195", "9211", "9096", "9098", "9212"]                            # 1차 5건은 길고 까다로운 것


# ------------------------------------------------------------------ 이상적인 출력은 만점
@pytest.mark.parametrize("idx", ["9212", "9098", "9096"])
def test_ideal_outputs_reach_full_recall_without_extras(idx, expected, reg):
    exp = expected[0][idx]
    elig = norm(idx)
    c = T.compare_one(exp, elig, reg)
    assert c["lost"] == [] and c["preserved"] == [] and c["extra"] == [], c
    assert set(c["hit"]) == {x for x in exp if x not in T.UNDETECTABLE}
    assert c["undetectable"] == (["TUITION"] if idx == "9096" else [])                                   # 구조 항목이 없는 조건은 따로 표시


# ------------------------------------------------------------------ 사라짐 / 원문만 / 추가
def test_missing_condition_is_preserved_lost_or_extra(expected, reg):
    exp = expected[0]["9096"]
    base = norm("9096")
    no_prereq = copy.deepcopy(base)
    no_prereq.pop("prerequisites")
    c = T.compare_one(exp, no_prereq, reg)
    assert c["lost"] == ["NSA"] and c["preserved"] == []                                                # 구조에도 원문에도 없음 -> 정보 손실 의심

    kept = copy.deepcopy(no_prereq)
    kept["unmodeled_conditions"] = [{"text": "한국장학재단 국가장학금 해당 학기 신청자", "section": "신청자격", "source": "본문", "kind": "ELIGIBILITY"}]
    c = T.compare_one(exp, kept, reg)
    assert c["preserved"] == ["NSA"] and c["lost"] == []                                                # 원문이 기타 목록에 남음 (실제 공고 문장)

    extra = copy.deepcopy(base)
    extra["gender"] = "F"
    c = T.compare_one(exp, extra, reg)
    assert c["extra"] == ["GEN"] and c["lost"] == []                                                    # 기대에 없는데 AI 가 담음 -> 사람이 확인

    notes_only = copy.deepcopy(no_prereq)
    notes_only["credits"]["notes"] = "국가장학금 신청 필수"                                               # 메모에 남은 것도 원문 보존으로 인정
    assert T.compare_one(exp, notes_only, reg)["preserved"] == ["NSA"]


def test_summary_text_is_not_counted_as_preserved(expected, reg):
    exp = expected[0]["9096"]
    elig = norm("9096")
    elig.pop("prerequisites")
    elig["summary"] = "국가장학금 신청자 대상 거창군 장학"
    assert T.compare_one(exp, elig, reg)["lost"] == ["NSA"]                                             # 요약문은 원문 보존으로 치지 않음


# ------------------------------------------------------------------ 감지 규칙
def test_detect_codes_covers_gpa_income_special_and_prereq_mappings():
    e = norm("9212")
    e.update(gpa={"min": 3.0, "scale": 4.5, "basis": "EITHER"}, enrollment={"required_status": ["재학"], "excluded_status": ["휴학생", "졸업예정자"], "min_remaining_semesters": 2})
    f = T.detect_codes(e)
    assert {"GL", "GC", "LV", "GRD", "SLEFT"} <= set(f)
    e2, _ = normalize({"category": "OUT", "apply_end": "2026-10-08", "gpa": {"min": 85, "scale": 100, "basis": "LAST_N", "terms": 2},
                       "income": {"max_bracket": 5, "basis": "MEDIAN_PERCENT", "max_median_percent": 130, "max_asset_krw": 100000000},
                       "special_groups": [{"code": "NEAR_POOR"}, {"code": "WORKER_CHILD"}, {"code": "MULTI_CHILD"}],
                       "prerequisites": [{"type": "ADMISSION_OFFER"}, {"type": "DEGREE_HELD"}, {"type": "BRACKET_CALCULATED"}],
                       "region": {"requirements": [{"type": "PARENT_WORKPLACE", "areas": ["인천"]}, {"type": "ACTUAL_RESIDENCE", "areas": ["화성"]}]},
                       "transfer": {"mode": "EXCLUDED"}, "entry_years": [2025]}, None, "", version=3)
    f2 = T.detect_codes(e2)
    assert {"GN", "SP", "INC", "INCM", "AST", "BASIC", "WCH", "MCH", "OVS", "DEG", "NSA", "PWORK", "REALRES", "TRF", "ENT"} <= set(f2)
    assert "GL" not in f2 and "RES" not in f2


def test_detect_codes_reads_variants_and_v2_style_fields():
    e, _ = normalize({"category": "OUT", "apply_end": "2026-10-08", "apply_type": "HYBRID", "gpa": {"min": 3.6, "basis": "CUMULATIVE"},
                      "region": {"residence": ["춘천시"], "school_location": ["강원"]},
                      "variants": [{"name": "A", "special_groups": [{"code": "DISABLED"}], "gpa": {"min": 3.0, "scale": 4.5, "basis": "ENTRANCE"}},
                                   {"name": "B", "apply_type": "SCHOOL", "gender": "여", "age": {"max": 29}}]}, None, "", version=3)
    f = T.detect_codes(e)
    assert {"GC", "SCALE_UNSPEC", "RES", "SLOC", "DIS", "GR", "GEN", "AGE", "ROUTE"} <= set(f)           # 최상위와 유형별 모두, v2 식 지역 표기도 인정
    assert f["DIS"].startswith("variants[0]") and T.detect_codes({}) == {}


# ------------------------------------------------------------------ 크레딧 측정
def test_credit_helpers(monkeypatch):
    assert T._flatten_numbers({"a": {"b": 1, "c": {"d": 2.5}}, "e": "x", "f": True, "g": 3}) == {"a.b": 1.0, "a.c.d": 2.5, "g": 3.0}
    before = {"m.used": 100.0, "m.left": 900.0, "t": 5.0}
    after = {"m.used": 164.25, "m.left": 835.75, "t": 5.0}
    assert T.credit_diff(before, after) == ["m.left: 900.0 -> 835.75 (-64.25)", "m.used: 100.0 -> 164.25 (+64.25)"]
    assert T.credit_diff(None, after) == [] and T.credit_diff(before, None) == []

    from scholarship.common.ai import gateway
    from scholarship.common.config import Settings
    s = Settings()
    assert T.credit_snapshot(s) is None                                                                  # 키·주소가 없으면 조회하지 않음
    s.ai_base_url, s.ai_api_key = "https://factchat-cloud.mindlogic.ai/v1/gateway/claude", "k"
    monkeypatch.setattr(gateway, "fetch_credits", lambda base, key: {"total": {"remaining": 10, "label": "x"}})
    assert T.credit_snapshot(s) == {"total.remaining": 10.0}

    def boom(base, key):
        raise RuntimeError("네트워크 오류")
    monkeypatch.setattr(gateway, "fetch_credits", boom)
    assert T.credit_snapshot(s) is None                                                                  # 조회 실패는 시험 실행을 막지 않음


# ------------------------------------------------------------------ 보고서
def results_for(expected, reg, bad=False):
    rs = []
    for idx in ("9212", "9098", "9096"):
        elig = norm(idx)
        if idx == "9096":
            elig.pop("prerequisites")
            elig["unmodeled_conditions"] = [{"text": "국가장학금 해당 학기 신청자", "section": "신청자격", "source": "본문", "kind": "ELIGIBILITY"}]
            elig["gender"] = "F"
        rs.append({"idx": idx, "title": "공고 " + idx, "ok": True, "input_tokens": 9000, "output_tokens": 4000, "attempts": 1, "elapsed_sec": 60.0,
                   "issues": [{"field": "region.mode", "reason": "x"}] if idx == "9212" else [], "uncertain_verify": 0,
                   "unmodeled": len(elig.get("unmodeled_conditions") or []), "region_mode_unknown": False,
                   "compare": T.compare_one(expected[0][idx], elig, reg)})
    if bad:
        rs.append({"idx": "9195", "title": "실패 공고", "ok": False, "error": "응답이 JSON 이 아님"})
    return rs


def test_summary_report_sections(expected, reg):
    text = T.summarize(results_for(expected, reg, bad=True), expected[1], reg, ["total.remaining: -64.25"])
    for needle in ("(4건 실행, 성공 3건, 실패 1건)", "실패: 9195", "크레딧 변화", "total.remaining: -64.25", "토큰: 입력 평균 9,000", "[1] 항목별 재현율",
                   "필수 합계", "권장 합계", "[2] 공고별", "원문만: NSA", "추가로 찾음(확인 필요): GEN", "[3] 품질 신호", "검수 대기(NEED_REVIEW)가 될 공고: 1건 / 3건",
                   "region.mode 1건", "[4] 판정 가이드", "필수·권장 항목 재현율"):
        assert needle in text, needle


def test_summary_guidance_flags_low_recall_and_losses(expected, reg):
    rs = results_for(expected, reg)
    for r in rs:                                                                                           # 모든 조건을 놓친 결과를 흉내
        r["compare"] = T.compare_one(expected[0][r["idx"]], {}, reg)
    text = T.summarize(rs, expected[1], reg)
    assert "미달: 프롬프트 개선 필요" in text and "초과: 기타 목록 규칙 점검" in text
    ok_text = T.summarize(results_for(expected, reg)[:2], expected[1], reg)                               # 9212, 9098 은 이상적이라 놓친 것이 없음
    assert "충족" in ok_text and "(모든 공고가 기대와 일치)" in ok_text


# ------------------------------------------------------------------ 시험 추출 5건 결과에서 발견한 측정 빈틈 (국가장학금 9195)
NATIONAL_LIKE = {
    "category": "OUT", "apply_type": "DIRECT", "target_levels": ["UNIV"], "income": {"max_bracket": 9, "basis": "BRACKET"},
    "enrollment": {"required_status": ["재학"], "notes": "재입학생, 복학생도 신청 가능"},      # 실제 AI 출력처럼: 허용 문구는 메모에만
    "school": {"types": ["UNIV4", "COLLEGE"], "programs": ["NATIONAL_SCHOLARSHIP"]},
    "waivers": [{"when": {"student": ["NEW_STUDENT", "TRANSFER", "REENTRY"], "special": ["DISABLED"]}, "skip": ["gpa", "credits"]}],
    "variants": [{"name": "국가장학금Ⅰ·다자녀유형", "gpa": {"min": 80, "scale": 100, "basis": "LAST_SEMESTER"}, "credits": {"min_last_semester": 12}},
                 {"name": "수급자·차상위", "special_groups": [{"code": "BASIC_LIVELIHOOD"}, {"code": "NEAR_POOR"}],
                  "gpa": {"min": 70, "scale": 100, "basis": "LAST_SEMESTER"}}],
    "unmodeled_conditions": [{"text": "국가장학금Ⅱ유형 : 대학별 선발 기준에 따름", "section": "신청자격", "source": "본문", "kind": "ELIGIBILITY"}],
}


def test_waivers_count_as_structured_hits(expected, reg):
    """'신입·편입·재입학생과 장애인은 성적 조건 미적용'을 waivers 로 담았다면 ENT/TRF/DIS 를 구조로 담은 것이다."""
    elig, _ = normalize(dict(NATIONAL_LIKE, summary="x", apply_end="2026-10-08"), None, "", version=3)
    f = T.detect_codes(elig)
    assert f["ENT"] == "waivers" and f["TRF"] == "waivers" and f["DIS"] == "waivers"
    no_waiver = dict(NATIONAL_LIKE, summary="x", apply_end="2026-10-08")
    no_waiver.pop("waivers")
    f2 = T.detect_codes(normalize(no_waiver, None, "", version=3)[0])
    assert "DIS" not in f2 and "TRF" not in f2


def test_variant_name_is_preserved_text_not_structured(expected, reg):
    """유형 이름('다자녀유형')은 원문으로는 남지만, 신분(special_groups)으로 담긴 것은 아니다 -> 원문만."""
    elig, _ = normalize(dict(NATIONAL_LIKE, summary="x", apply_end="2026-10-08"), None, "", version=3)
    c = T.compare_one(expected[0]["9195"], elig, reg)
    assert set(c["hit"]) >= {"ENT", "TRF", "DIS", "BASIC", "GL", "SP", "CR", "INC", "SPART"} and "LV" not in expected[0]["9195"]   # LV 는 '복학생도 신청 가능'(허용 문구)이라 기대에서 뺌
    assert c["preserved"] == ["MCH"] and "다자녀" in c["preserved_evidence"]["MCH"]
    assert c["lost"] == ["GR"]                                                                              # 지역인재 신입 성적은 실제로 빠짐
    assert c["extra"] == ["SCH_TYPE"] and c["context"]["variants"] == ["국가장학금Ⅰ·다자녀유형", "수급자·차상위"] and c["context"]["waivers"] == 1


def test_report_shows_evidence_context_verify_reasons_and_prompt_versions(expected, reg):
    elig, _ = normalize(dict(NATIONAL_LIKE, summary="x", apply_end="2026-10-08"), None, "", version=3)
    r = {"idx": "9195", "title": "국가장학금", "ok": True, "input_tokens": 1000, "output_tokens": 3000, "attempts": 1, "elapsed_sec": 20.0, "issues": [],
         "uncertain_verify": 1, "uncertain_items": [{"field": "gpa", "reason": "기준표 일부가 불명확", "level": "verify"}], "unmodeled": 1,
         "region_mode_unknown": False, "compare": T.compare_one(expected[0]["9195"], elig, reg), "prompt_sha": "abcd1234"}
    text = T.summarize([r], expected[1], reg)
    assert '원문만: MCH(다자녀 가정) <- "국가장학금Ⅰ·다자녀유형"' in text
    assert "사라짐: GR(" in text and "AI 가 쓴 유형 이름: 국가장학금Ⅰ·다자녀유형, 수급자·차상위 | 면제 1개 | 기타 목록:" in text
    assert "9195 AI 검수 표시 [gpa] 기준표 일부가 불명확" in text and "프롬프트 버전: abcd1234 1건" in text


# ------------------------------------------------------------------ 시험 추출 5건 결과 반영
def test_variant_nationality_is_detected_as_nat():
    elig, _ = normalize({"category": "OUT", "apply_end": "2026-10-08", "variants": [{"name": "외국인 유학생", "nationality": "외국인"}, {"name": "과학 장학"}]},
                        None, "", version=3)
    assert T.detect_codes(elig)["NAT"] == "variants[0].nationality"


def test_noise_summary_tag_no_longer_creates_a_fake_restriction(expected, reg):
    """9195: AI 가 '중복수혜 : 일부 가능 (등록금 범위 내)'를 CONCURRENT_TUITION 으로 적은 것은 정규화에서 걸러져 '추가로 찾음'이 되지 않는다."""
    elig, _ = normalize(dict(NATIONAL_LIKE, summary="x", apply_end="2026-10-08",
                             exclusion_items=[{"type": "CONCURRENT_TUITION", "text": "중복수혜 : 일부 가능 (등록금 범위 내)"}]), None, "", version=3)
    assert "CONC" not in T.detect_codes(elig) and "CONC" not in T.compare_one(expected[0]["9195"], elig, reg)["extra"]


def test_parse_notice_file_reads_period_start_as_reference_date():
    ns = {n.idx: n for n in T.parse_notice_file(FIX.read_text(encoding="utf-8"))}
    assert (ns["9212"].period_start, ns["9098"].period_start, ns["9096"].period_start) == ("2026-09-21", "2026-06-01", "2026-05-15")
    assert T.parse_notice_file("### [1] idx=1 | 모집중 | 제목 | 기관\n신청기간 원문: 미정 | 목록 태그(참고): - | 첨부: 없음\n본문")[0].period_start is None


# ------------------------------------------------------------------ 20건 시험 추출 결과 반영: 키워드 힌트와 기대 데이터 정정
@pytest.mark.parametrize("code, text", [
    ("CONC", "장학금 수혜 등으로 본인 부담 등록금이 1만원 미만인 자 선발 제외"),                       # 9096 (exclusion_items OTHER 로 적힘)
    ("CONC", "삼척향토장학재단, 삼척시도계장학재단의 2026년도 장학금 기수령자 선발 제외"),               # 9181
    ("PRIOR", "전년도, 동일연도 인천인재평생교육진흥원 해외 프로그램(연수, 배낭연수, 봉사) 장학생 선발 제외"),    # 9217
    ("PRIOR", "신규장학생으로 선발된 3학년 진급 재학생"),                                              # 9144
    ("INCT", "애향: 선발 조건은 공고문 확인 (저소득자녀/통리반장자녀/시정유공본인·자녀)"),               # 9210
    ("SEM", "대학생 : 2개 학기 이상 성적증명서 제출이 가능한 자"),                                       # 9120
])
def test_hints_recognize_real_sentences_the_ai_kept_as_text(code, text, reg):
    """원문이 exclusion_items(OTHER)나 기타 목록에 남아 있는데 키워드가 좁아서 '사라짐'으로 센 경우 (실제 AI 출력 문장)."""
    elig = {"unmodeled_conditions": [{"text": text, "kind": "ELIGIBILITY"}]}
    c = T.compare_one({code}, elig, reg)
    assert c["preserved"] == [code] and c["lost"] == [], c


def test_hints_do_not_accept_unrelated_text(reg):
    c = T.compare_one({"CONC", "PRIOR", "SEM"}, {"unmodeled_conditions": [{"text": "봉사활동 시간을 증빙할 수 있는 자", "kind": "ELIGIBILITY"}]}, reg)
    assert sorted(c["lost"]) == ["CONC", "PRIOR", "SEM"]                                                  # 관련 없는 문장은 원문 보존으로 치지 않음


def test_expected_corrections_are_documented_and_applied(expected):
    import json
    d = json.loads((Path(__file__).resolve().parent.parent / "data" / "ext02_survey_expected.json").read_text(encoding="utf-8"))
    notices = expected[0]
    assert len(d["corrections"]) == 10 and all(c["evidence"] and c["reason"] for c in d["corrections"])    # 모든 정정에 근거 문장이 있음
    for c in d["corrections"]:
        if c["change"] == "기대에서 제외":
            assert c["code"] not in notices[c["idx"]], c
        else:
            old, new = [x.strip() for x in c["code"].split("->")]
            assert old not in notices[c["idx"]] and new in notices[c["idx"]], c
    for idx in ("9163", "9154", "9071"):                                                                  # 부모만 거주 조건: 본인(RES)은 없고 부모(RESP)만
        assert "RES" not in notices[idx] and "RESP" in notices[idx]
    assert {"ENT", "TRF", "LV", "BASIC"}.isdisjoint(notices["9088"]) and "SLOC" in notices["9141"] and "OVS" not in notices["9141"]
