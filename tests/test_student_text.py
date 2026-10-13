# -*- coding: utf-8 -*-
"""학생 정보 텍스트("라벨: 값") 파서: 순서 무관, 한 줄 오류 격리, 개인정보 처리, 값 해석, 신분 답변, 항목 사전 확장, 되돌려 쓰기."""
import dataclasses
import random

import pytest

from scholarship.common import registry as R
from scholarship.recommend.profile import ProfileError, StudentProfile
from scholarship.recommend.student_text import (MAX_BYTES, build_template, format_parse_report, parse_student_text, profile_from_text,
                                                profile_to_text)

SAMPLE = """# 학생정보
## 학적
- 학적상태: 재학
- 과정: 학사
- 학과: 전자공학부 전자공학전공
- 학과 계열: 공학
- 이수학기: 7
- 입학년도: 2021
- 입학구분: 신입학
- 국적: 내국인
- 성별: 남
## 성적
- 직전학기: 2026-1
- 직전학기 평점: 3.79 / 4.5
- 직전학기 취득학점: 15
- 누적 평점: 3.62 / 4.5
- 학기별 성적: 2026-1 3.79 15 | 2025-2 3.11 18 | 2025-1 3.40 16
## 소득
- 소득구간: 2
## 거주 (학생 선택)
- 본인 주민등록: 경기도 수원시
- 부모 주민등록: 경기도 수원시
- 거주 기간: 3년 이상
- 졸업 고교 소재지: 경기도 안양시
- 만 나이: 24
## 신분 (학생 선택)
- 해당 항목: 해당 없음
"""
SAMPLE_DATA = {
    "gpa_scale": 4.5, "gpa_last": 3.79, "gpa_last_term": "2026-1", "credits_last": 15.0, "gpa_cumulative": 3.62, "semesters_completed": 7,
    "status": "ENROLLED", "level": "UNIV", "entry_year": 2021, "admission_type": "NEW", "major": "전자공학부 전자공학전공", "major_group": "ENGINEERING",
    "residence_sido": "경기도", "residence_sigungu": "수원시", "parent_residence_sido": "경기도", "parent_residence_sigungu": "수원시",
    "residence_years": 3.0, "residence_years_open": True, "hs_sido": "경기도", "hs_sigungu": "안양시", "income_bracket": 2, "age": 24, "gender": "M", "nationality": "KR",
    "special_codes": [], "special_complete": True, "is_basic_livelihood": False, "is_near_poor": False, "is_single_parent": False, "special_status": [],
    "term_history": [{"term": "2026-1", "gpa": 3.79, "credits": 15.0}, {"term": "2025-2", "gpa": 3.11, "credits": 18.0}, {"term": "2025-1", "gpa": 3.4, "credits": 16.0}],
}


@pytest.fixture(scope="module")
def reg():
    return R.load_registry()


def data_of(text, registry=None):
    return parse_student_text(text, registry).data


def parse(text, registry=None):
    return parse_student_text(text, registry)


# ================================================================== 기본: 합의한 샘플
def test_sample_text_is_read_completely_without_warnings():
    ps = parse(SAMPLE)
    assert ps.data == SAMPLE_DATA
    assert ps.report.warnings == [] and ps.report.unknown_labels == [] and ps.report.dropped_sensitive == [] and ps.report.conflicts == []
    assert len(ps.report.recognized) == 21 and "SPECIAL" in ps.report.recognized and "MAJG" in ps.report.recognized
    built, rep = profile_from_text(SAMPLE)                                                            # 최종 검증(build_profile)도 통과
    assert built.assumptions == [] and built.profile.gpa_last == 3.79 and built.profile.special_complete is True


def test_missing_items_are_reported_by_tier_and_exclude_fixed_and_derived(reg):
    miss = parse(SAMPLE).report.missing
    assert [(m["code"], m["tier"]) for m in miss] == [("YEAR", 1), ("SP", 2), ("GCP", 2), ("DEG", 3), ("LANG", 3), ("NSA", 3)]
    assert all(m["source"] in ("student", "school_db") for m in miss) and miss[0]["label"] == "학년"
    everything = {i.code for i in reg.enabled() if i.source in ("student", "school_db")}
    empty = parse("").report.missing
    assert {m["code"] for m in empty} == everything and [m["tier"] for m in empty] == sorted(m["tier"] for m in empty)
    assert not {"SLOC", "SCH_TYPE", "SPART", "SLEFT", "GRD"} & everything                                  # 학교 고정·계산 항목은 학생에게 묻지 않음


# ================================================================== 순서·표기 무관
def test_line_order_never_changes_the_result():
    lines = SAMPLE.strip().splitlines()
    base = parse(SAMPLE)
    for seed in range(8):
        shuffled = lines[:]
        random.Random(seed).shuffle(shuffled)
        got = parse("\n".join(shuffled))
        assert got.data == base.data and sorted(got.report.recognized) == sorted(base.report.recognized) and got.report.warnings == []
    assert parse("\n".join(reversed(lines))).data == base.data


def test_markers_colons_spacing_and_aliases_are_tolerated():
    variants = [
        "\n".join(l for l in SAMPLE.splitlines() if not l.startswith("#")),                              # '#' 줄 없음
        "\n".join(l[2:] if l.startswith("- ") else l for l in SAMPLE.splitlines()),                      # '-' 없음
        SAMPLE.replace(": ", " ： "),                                                                    # 전각 콜론
        SAMPLE.replace("직전학기 평점", "직전 학기 평점").replace("누적 평점", "누적평점").replace("본인 주민등록", "본인주민등록"),   # 라벨 띄어쓰기
        SAMPLE.replace("3.79 / 4.5", "3.79/4.5").replace("3.62 / 4.5", "3.62 /4.5"),                    # 값 띄어쓰기
        SAMPLE.replace("소득구간: 2", "소득분위: ２"),                                                      # 동의어 라벨 + 전각 숫자
        SAMPLE.replace("본인 주민등록", "주민등록 기준지"),
    ]
    for v in variants:
        assert parse(v).data == SAMPLE_DATA, v[:60]


def test_parenthetical_annotations_are_ignored():
    d = data_of("- 이수학기: 7 (졸업학기 충족)\n- 소득구간: 2구간 (한국장학재단)\n- 직전학기 평점: 3.79 / 4.5 (성적상승)\n- 학년: 4학년 (졸업반)")
    assert d["semesters_completed"] == 7 and d["income_bracket"] == 2 and d["gpa_last"] == 3.79 and d["grade"] == 4


def test_comment_only_blank_and_colonless_lines():
    ps = parse("# 주석만\n\n   \n- 이수학기: 7\n그냥 문장입니다\n- 성별:\n")
    assert ps.data == {"semesters_completed": 7}
    assert ps.report.warnings == ["읽을 수 없는 줄(콜론 없음): '그냥 문장입니다'"]                          # 빈 값(성별)은 입력하지 않은 것: 경고 없이 '입력에 없는 항목'
    assert "GEN" in [m["code"] for m in ps.report.missing]


# ================================================================== 한 줄 오류는 그 항목만
@pytest.mark.parametrize("line, field", [
    ("소득구간: 둘째", "income_bracket"), ("소득구간: 15", "income_bracket"), ("소득구간: 2~3", "income_bracket"), ("직전학기 평점: 매우 좋음", "gpa_last"),
    ("직전학기 평점: 3.79 / 4.2", "gpa_last"), ("직전학기 평점: 4.8 / 4.5", "gpa_last"), ("만 나이: 스물넷", "age"), ("성별: 기타", "gender"),
    ("학적상태: 졸업", "status"), ("직전학기: 2026-3", "gpa_last_term"), ("거주 기간: 2년", "residence_years"), ("이수학기: 7.5", "semesters_completed"),
    ("국가장학금 신청 여부: 글쎄", "national_scholarship_applied"), ("학기별 성적: 2026-1", "term_history"), ("학기별 성적: 2026-1 3.8 15 | 2026.1 3.0 12", "term_history"),
    ("학기별 성적: 2026-1 5.8 15", "term_history"), ("학기별 성적: 2026-1 3.8 99", "term_history"), ("어학 성적: 높음", "language_scores"),
])
def test_a_bad_line_only_drops_that_item(line, field):
    base = "- 직전학기 취득학점: 15\n"                                                                      # 오류 사례와 겹치지 않는 항목
    ps = parse(base + "- " + line)
    assert field not in ps.data or ps.data[field] is None
    assert ps.data["credits_last"] == 15.0                                                                # 나머지 항목은 그대로
    assert any("-> 비움" in w for w in ps.report.warnings), ps.report.warnings
    profile_from_text(base + "- " + line)                                                                 # 최종 검증(build_profile)에서 ProfileError 가 나지 않음


def test_unknown_labels_are_warned_not_guessed():
    ps = parse("- 직전학기 평정: 3.7 / 4.5\n- 평점: 3.7\n- 성적: 좋음\n- 주민등록: 경기도 수원시\n- 이수학기: 7")
    assert ps.data == {"semesters_completed": 7}                                                          # 오타·모호한 라벨을 짐작해서 채우지 않음
    assert ps.report.unknown_labels == ["직전학기 평정", "평점", "성적"] and ps.report.dropped_sensitive == ["주민등록"]     # 모호한 '주민등록'은 사전에 없으니 개인정보 필터로
    assert len(ps.report.warnings) == 3


# ================================================================== 같은 항목이 여러 번
def test_duplicates_same_value_ok_different_value_conflicts_in_any_order():
    ok = parse("- 이수학기: 7\n- 이수 학기: 7\n- 만 나이: 24")
    assert ok.data == {"semesters_completed": 7, "age": 24} and ok.report.warnings == [] and ok.report.conflicts == []
    a = parse("- 이수학기: 7\n- 이수 학기: 8\n- 만 나이: 24")
    b = parse("- 이수 학기: 8\n- 만 나이: 24\n- 이수학기: 7")
    assert a.data == b.data == {"age": 24} and a.report.conflicts == ["이수학기"] and any("서로 다른 값" in w for w in a.report.warnings)


def test_gpa_scale_conflict_is_resolved_by_item_priority_not_line_order():
    first = parse("- 직전학기 평점: 3.5 / 4.5\n- 누적 평점: 3.4 / 4.3")
    second = parse("- 누적 평점: 3.4 / 4.3\n- 직전학기 평점: 3.5 / 4.5")
    assert first.data == second.data == {"gpa_scale": 4.5, "gpa_last": 3.5}                                # 직전학기 평점이 우선, 누적은 비움
    assert any("만점" in w and "누적 평점" in w for w in first.report.warnings)
    built, _ = profile_from_text("- 직전학기 평점: 3.5")
    assert built.profile.gpa_scale == 4.5 and any("4.5 으로 가정" in a for a in built.assumptions)         # 만점이 없으면 4.5 가정(결정)


# ================================================================== 개인정보
def test_sensitive_labels_are_dropped_but_normal_labels_with_similar_words_pass():
    ps = parse("""- 이름: 홍길동
- 학번: 20260001
- 주민등록번호: 900101-1234567
- 부모 주민등록번호: 6501011234567
- 연락처: 010-1234-5678
- 이메일: a@b.com
- 상세 주소: 서울시 성북구 길음동 123
- 생년월일: 2000-01-01
- 본인 주민등록: 경기도 수원시
- 부모 주민등록: 경기도 안양시""")
    assert sorted(ps.report.dropped_sensitive) == sorted(["이름", "학번", "주민등록번호", "부모 주민등록번호", "연락처", "이메일", "상세 주소", "생년월일"])
    assert ps.data == {"residence_sido": "경기도", "residence_sigungu": "수원시", "parent_residence_sido": "경기도", "parent_residence_sigungu": "안양시"}


@pytest.mark.parametrize("line", ["본인 주민등록: 900101-1234567", "만 나이: 010-1234-5678", "학과: 홍길동 hong@example.com", "학과: 9001011234567"])
def test_sensitive_looking_values_are_dropped_even_under_normal_labels(line):
    ps = parse("- 이수학기: 7\n- " + line)
    assert ps.data == {"semesters_completed": 7} and any("값이 개인정보 형식" in d for d in ps.report.dropped_sensitive)


def test_school_fixed_items_are_ignored_even_if_present():
    ps = parse("- 학교 소재지: 서울특별시\n- 학교 종류: 일반대학\n- 학교 참여 사업: 국가장학금\n- 이수학기: 7")
    assert ps.data == {"semesters_completed": 7} and sorted(ps.report.ignored_fixed) == ["학교 소재지", "학교 종류", "학교 참여 사업"]


def test_oversized_input_is_rejected():
    with pytest.raises(ProfileError, match="너무 큽니다"):
        parse("- 이수학기: 7\n" + "# 가" * (MAX_BYTES // 3))
    assert parse("") .data == {} and parse(None).data == {}


# ================================================================== 값 해석
@pytest.mark.parametrize("value, years, upper, open_", [("1년 미만", 0.0, 11 / 12, None), ("1~3년", 1.0, 3.0, None), ("3년 이상", 3.0, None, True), (" 3년  이상 ", 3.0, None, True)])
def test_residence_period_keeps_lower_and_upper_bounds(value, years, upper, open_):
    """하한은 '확실히 채웠는지'(PASS), 상한은 '확실히 못 채웠는지'(FAIL)를 가르는 데 쓴다. '3년 이상'은 상한이 없는 열린 구간."""
    d = data_of("- 거주 기간: " + value)
    assert d["residence_years"] == years and d.get("residence_years_open") is open_
    assert (d.get("residence_years_upper") is None) if upper is None else d["residence_years_upper"] == pytest.approx(upper)


@pytest.mark.parametrize("value, sido, sigungu, warns", [
    ("경기도 수원시", "경기도", "수원시", 0), ("서울시 성북구", "서울시", "성북구", 0), ("경기도 수원시 영통구", "경기도", "수원시 영통구", 0),
    ("경기도", "경기도", None, 0), ("수원시 영통구", None, "수원시 영통구", 1), ("중구", None, "중구", 1)])
def test_place_values(value, sido, sigungu, warns):
    ps = parse("- 본인 주민등록: " + value)
    assert (ps.data["residence_sido"], ps.data["residence_sigungu"]) == (sido, sigungu) and len(ps.report.warnings) == warns
    if warns:
        assert "시·도가 없어" in ps.report.warnings[0]


@pytest.mark.parametrize("value", ["2026-1", "2026.1", "2026년 1학기", "2026-1학기"])
def test_term_formats(value):
    assert data_of("- 직전학기: " + value) == {"gpa_last_term": "2026-1"}


def test_term_history_is_sorted_and_credits_optional():
    d = data_of("- 학기별 성적: 2025-1 3.4 | 2026-1 3.79 15 | 2025-2 3.11 18")
    assert [r["term"] for r in d["term_history"]] == ["2025-1", "2026-1", "2025-2"]                        # 파서는 적힌 순서 그대로, 정렬은 프로필 검증에서
    built, _ = profile_from_text("- 학기별 성적: 2025-1 3.4 | 2026-1 3.79 15 | 2025-2 3.11 18")
    assert [r["term"] for r in built.profile.term_history] == ["2026-1", "2025-2", "2025-1"] and built.profile.term_history[2]["credits"] is None


def test_history_vs_last_semester_consistency_warnings_do_not_change_values():
    ps = parse("- 직전학기 평점: 3.5 / 4.5\n- 직전학기: 2025-2\n- 학기별 성적: 2026-1 3.79 15 | 2025-2 3.11 18")
    assert ps.data["gpa_last"] == 3.5 and len(ps.data["term_history"]) == 2
    assert any("가장 최근 값" in w for w in ps.report.warnings) and any("가장 최근 학기" in w for w in ps.report.warnings)


def test_language_scores():
    assert data_of("- 어학 성적: TOEIC 780, HSK 5급; toefl 95.5")["language_scores"] == {"TOEIC": 780.0, "HSK": 5.0, "TOEFL": 95.5}


@pytest.mark.parametrize("label, value, field, want", [
    ("학적상태", "휴학", "status", "LEAVE"), ("학적상태", "등록휴학", "status", "LEAVE"), ("과정", "석사", "level", "GRAD"), ("입학구분", "편입학", "admission_type", "TRANSFER"),
    ("국적", "외국인", "nationality", "FOREIGN"), ("국적", "재외동포", "nationality", "OVERSEAS_KR"), ("성별", "여", "gender", "F"),
    ("졸업학기 여부", "예", "final_semester", True), ("학위 소지 여부", "아니오", "degree_held", False), ("국가장학금 신청 여부", "해당", "national_scholarship_applied", True),
    ("직전학기 백분위", "87.5", "gpa_percent", 87.5), ("남은 학기", "2", "remaining_semesters", 2), ("학년", "3", "grade", 3)])
def test_enum_bool_and_number_values(label, value, field, want):
    assert data_of("- {}: {}".format(label, value))[field] == want


def test_declined_is_recorded_and_not_reported_missing():
    ps = parse("- 성별: 답하지 않음\n- 소득구간: 모름\n- 이수학기: 7")
    assert ps.data == {"semesters_completed": 7} and sorted(ps.report.declined) == ["GEN", "INC"]
    assert "GEN" not in [m["code"] for m in ps.report.missing] and "INC" not in [m["code"] for m in ps.report.missing]


# ================================================================== 신분 항목: 해당 없음 / 답하지 않음 / 목록 / 개별
def test_special_none_list_declined_and_unknown_tokens():
    none = data_of("- 해당 항목: 해당 없음")
    assert none["special_codes"] == [] and none["special_complete"] is True and none["special_status"] == [] and none["is_single_parent"] is False
    lst = parse("- 해당 항목: 다자녀, 한부모, 가족돌봄")
    assert lst.data["special_codes"] == ["SINGLE_PARENT", "MULTI_CHILD"] and lst.data["special_complete"] is True
    assert lst.data["special_status"] == ["한부모", "다자녀", "가족돌봄"] and lst.data["is_single_parent"] is True and lst.data["is_basic_livelihood"] is False
    assert any("가족돌봄" in w for w in lst.report.warnings)
    dec = parse("- 해당 항목: 답하지 않음\n- 이수학기: 7")
    assert dec.data == {"semesters_completed": 7} and "SPECIAL" in dec.report.declined and len(dec.report.declined) == 9   # 신분 항목 8개 + 목록
    assert not [m for m in dec.report.missing if m["code"] in ("SPECIAL", "BASIC", "DIS", "MCH")]


def test_special_individual_lines_and_partial_answers():
    part = data_of("- 기초생활수급자: 해당\n- 다자녀 가정: 해당")
    assert part["special_codes"] == ["BASIC_LIVELIHOOD", "MULTI_CHILD"] and part["special_complete"] is False
    assert "special_status" not in part and part["is_basic_livelihood"] is True                             # 일부만 답했으면 '해당 없음'을 단정하지 않음
    only_no = data_of("- 한부모 가정: 아니오")
    assert only_no["special_codes"] == [] and only_no["special_complete"] is False and only_no["is_single_parent"] is False
    all_in = data_of("\n".join("- {}: {}".format(l, "해당" if l == "장애(본인 또는 가족)" else "아니오") for l in
                               ["기초생활수급자", "차상위계층", "장애(본인 또는 가족)", "한부모 가정", "북한이탈주민", "다문화 가정", "국가유공자(본인·자녀)", "다자녀 가정"]))
    assert all_in["special_codes"] == ["DISABLED"] and all_in["special_complete"] is True and all_in["special_status"] == ["장애"]


def test_special_individual_overrides_the_list_and_warns():
    ps = parse("- 해당 항목: 다자녀\n- 다자녀 가정: 아니오")
    assert ps.data["special_codes"] == [] and ps.data["special_complete"] is True and any("MULTI_CHILD" in w and "개별 항목 줄" in w for w in ps.report.warnings)


# ================================================================== 항목 사전에서 확장 / 사전이 정한 것
EXTRA = [
    {"code": "SCHOLAR_TERMS", "label": "장학 수혜 학기 수", "aliases": ["수혜 학기수", "장학 받은 학기 수"], "kind": "student", "source": "student", "type": "int", "tier": 3,
     "range": [0, 16], "question": "지금까지 장학금을 받은 학기는?"},
    {"code": "HAS_CAR", "label": "차량 보유", "kind": "student", "source": "student", "type": "bool", "tier": 3, "question": "차량이 있나요?"},
    {"code": "SIZE", "label": "체급", "kind": "student", "source": "student", "type": "enum", "enum": ["소형", "대형"], "tier": 3, "question": "체급은?"},
    {"code": "HOME_TOWN", "label": "고향", "kind": "student", "source": "student", "type": "place", "tier": 3, "question": "고향은?"},
    {"code": "HOBBIES", "label": "취미", "kind": "student", "source": "student", "type": "list", "tier": 3, "question": "취미는?"},
    {"code": "NOTE", "label": "메모", "kind": "student", "source": "student", "type": "text", "tier": 3, "question": "메모"},
]


def test_new_registry_items_are_read_without_code_changes(reg):
    reg2 = reg.with_items(EXTRA)
    text = "- 수혜 학기수: 6\n- 차량 보유: 예\n- 체급: 대형\n- 고향: 경기도 수원시\n- 취미: 독서, 등산\n- 메모: 자유 입력\n- 이수학기: 7"
    ps = parse(text, reg2)
    assert ps.data["extra"] == {"SCHOLAR_TERMS": 6, "HAS_CAR": True, "SIZE": "대형", "HOME_TOWN": {"sido": "경기도", "sigungu": "수원시"},
                                "HOBBIES": ["독서", "등산"], "NOTE": "자유 입력"} and ps.data["semesters_completed"] == 7 and ps.report.warnings == []
    built, _ = profile_from_text(text, reg2)
    assert built.profile.extra["SCHOLAR_TERMS"] == 6 and built.profile.semesters_completed == 7
    assert parse(text).report.unknown_labels == ["수혜 학기수", "차량 보유", "체급", "고향", "취미", "메모"]       # 기본 사전에서는 모르는 라벨
    bad = parse("- 장학 받은 학기 수: 20\n- 체급: 중형", reg2)
    assert "extra" not in bad.data and len(bad.report.warnings) == 2                                       # 범위·허용 값 검증도 사전이 정한 대로


def test_disabled_items_are_not_recognized_and_changed_enum_needs_a_rule(reg):
    items = [dict(R._to_dict(i)) for i in reg.items]
    for it in items:
        if it["code"] == "GEN":
            it["enabled"] = False
        if it["code"] == "LV":
            it["enum"] = ["재학", "휴학", "등록휴학", "휴학예정"]
    reg2 = R.from_data({"version": reg.version, "items": items})
    ps = parse("- 성별: 남\n- 학적상태: 휴학예정\n- 이수학기: 7", reg2)
    assert ps.report.unknown_labels == ["성별"] and "status" not in ps.data and any("해석 규칙이 없습니다" in w for w in ps.report.warnings)


def test_every_registry_item_is_readable(reg):
    """사전의 모든 학생·학교DB 항목이 실제로 읽힌다 (사전에 항목이 늘었는데 파서가 못 읽는 일을 막는 시험)."""
    samples = {"GL": "3.5 / 4.5", "LTERM": "2026-1", "CR": "15", "GC": "3.4 / 4.5", "GN": "2026-1 3.5 15", "SP": "88", "YEAR": "3", "SEM": "5", "SLEFT": "3",
               "GRD": "아니오", "LV": "재학", "GRAD": "학사", "ENT": "2022", "TRF": "신입학", "DEG": "아니오", "MAJ": "컴퓨터공학", "RES": "경기도 시흥시",
               "RESP": "경기도 시흥시", "RESY": "1~3년", "HSL": "경기도 시흥시", "INC": "3", "AGE": "22", "GEN": "여", "NAT": "내국인", "LANG": "TOEIC 800",
               "NSA": "예", "SPECIAL": "해당 없음", "MAJG": "공학", "GCP": "85"}
    lines, expect = [], set()
    for it in reg.enabled():
        if it.source == "school_fixed" or it.source not in ("student", "school_db", "derived"):
            continue
        if it.special_code:
            lines.append("- {}: 아니오".format(it.label))
            expect.add(it.code)
            continue
        assert it.code in samples, "시험용 예시 값이 없는 항목: " + it.code
        lines.append("- {}: {}".format(it.label, samples[it.code]))
        expect.add(it.code)
    ps = parse("\n".join(lines))
    assert ps.report.warnings == [] and set(ps.report.recognized) >= expect - {i.code for i in reg.enabled() if i.special_code}
    assert ps.report.unknown_labels == [] and not [m for m in ps.report.missing if m["code"] not in {"SPECIAL"} and m["code"] in expect and not
                                                    reg.get(m["code"]).special_code]
    profile_from_text("\n".join(lines))                                                                    # 모든 항목을 넣은 텍스트가 최종 검증도 통과


# ================================================================== 되돌려 쓰기
def as_dict(p):
    return dataclasses.asdict(p)


def test_round_trip_sample_and_rich_profile():
    p1, _ = profile_from_text(SAMPLE)
    p2, rep2 = profile_from_text(profile_to_text(p1.profile))
    assert as_dict(p2.profile) == as_dict(p1.profile) and rep2.warnings == []
    rich = """- 학적상태: 휴학
- 과정: 석사
- 국적: 재외동포
- 성별: 여
- 직전학기 평점: 4.1 / 4.3
- 거주 기간: 1~3년
- 본인 주민등록: 서울시 성북구
- 학년: 2
- 어학 성적: TOEIC 780, HSK 5급
- 해당 항목: 한부모, 장애
- 국가장학금 신청 여부: 예
- 졸업학기 여부: 아니오"""
    q1, _ = profile_from_text(rich)
    q2, rep = profile_from_text(profile_to_text(q1.profile))
    assert as_dict(q2.profile) == as_dict(q1.profile) and rep.warnings == [] and q1.profile.special_codes == ["DISABLED", "SINGLE_PARENT"]


def test_round_trip_keeps_extra_items(reg):
    reg2 = reg.with_items(EXTRA)
    p1, _ = profile_from_text("- 장학 수혜 학기 수: 6\n- 차량 보유: 예\n- 고향: 경기도 수원시\n- 취미: 독서, 등산\n- 체급: 소형", reg2)
    text = profile_to_text(p1.profile, reg2)
    assert "- 장학 수혜 학기 수: 6" in text and "- 차량 보유: 예" in text and "- 취미: 독서, 등산" in text
    p2, rep = profile_from_text(text, reg2)
    assert as_dict(p2.profile) == as_dict(p1.profile) and rep.warnings == []


# ================================================================== 보고서·양식
def test_format_parse_report_lists_warnings_sensitive_and_missing_tiers():
    ps = parse("- 이름: 홍길동\n- 소득구간: 둘째\n- 성별: 답하지 않음\n- 이수학기: 7")
    text = format_parse_report(ps.report)
    assert text.startswith("[학생 정보 텍스트] 읽은 항목 1개, 경고 1개") and "경고: 소득구간: 숫자 하나가 필요합니다" in text
    assert "개인정보로 보여 받지 않은 항목: 이름" in text and "답하지 않음으로 표시한 항목: 1개" in text
    assert "입력에 없는 항목: " in text and "학년(필수)" in text and "외 선택 항목" in text
    clean = format_parse_report(parse(SAMPLE).report)
    assert "경고:" not in clean and "입력에 없는 항목: 학년(필수), 직전학기 백분위(권장), 누적 백분위(권장) 외 선택 항목 3개" in clean


def test_template_lists_every_student_label_and_reads_as_blank(reg):
    t = build_template(reg)
    expected_labels = [i.label for i in reg.enabled() if i.source in ("student", "school_db") and not i.special_code]
    assert all("- {}:".format(l) in t for l in expected_labels) and "학교 소재지" not in t and "다자녀 가정" not in t
    assert "예: 3.79 / 4.5" in t and "허용 값: 재학 / 휴학 / 등록휴학" in t and "민감 정보" in t
    ps = parse(t, reg)
    assert ps.data == {} and ps.report.warnings == [] and ps.report.recognized == [] and ps.report.unknown_labels == []
    assert len(ps.report.missing) == len([i for i in reg.enabled() if i.source in ("student", "school_db")])


# ================================================================== 4-B2: 어학 성적 '없음'과 누적 백분위 입력
@pytest.mark.parametrize("value", ["없음", "해당 없음", "없습니다"])
def test_no_language_score_is_an_answer_and_differs_from_not_answering(value):
    ps = parse_student_text("- 어학 성적: " + value)
    assert ps.data == {"language_scores": {}} and ps.report.warnings == [] and "LANG" in ps.report.recognized      # 성적이 없다고 답한 것
    built, _ = profile_from_text("- 어학 성적: " + value)
    assert built.profile.language_scores == {} and "- 어학 성적: 없음" in profile_to_text(built.profile)           # 되돌려 써도 유지
    assert profile_from_text("- 학년: 3")[0].profile.language_scores is None                                       # 답하지 않음은 None
    assert parse_student_text("- 어학 성적: 답하지 않음").report.declined == ["LANG"]


def test_language_scores_still_parse_and_round_trip():
    ps = parse_student_text("- 어학 성적: TOEIC 780, HSK 5급")
    assert ps.data == {"language_scores": {"TOEIC": 780.0, "HSK": 5.0}}
    built, _ = profile_from_text("- 어학 성적: TOEIC 780, HSK 5급")
    assert "- 어학 성적: TOEIC 780, HSK 5" in profile_to_text(built.profile)


@pytest.mark.parametrize("label", ["누적 백분위", "전체 백분위", "누적 100점 환산"])
def test_cumulative_percentile_input(label):
    ps = parse_student_text("- {}: 86.5".format(label))
    assert ps.data == {"gpa_cumulative_percent": 86.5} and ps.report.warnings == [] and "GCP" in ps.report.recognized
    assert parse_student_text("- {}: 86\n- 직전학기 백분위: 70".format(label)).data == {"gpa_cumulative_percent": 86.0, "gpa_percent": 70.0}   # 직전학기 백분위와 별개


def test_cumulative_percentile_errors_and_round_trip():
    bad = parse_student_text("- 누적 백분위: 101\n- 이수학기: 7")
    assert bad.data == {"semesters_completed": 7} and "누적 백분위" in bad.report.warnings[0]
    built, _ = profile_from_text("- 누적 백분위: 85")
    assert built.profile.gpa_cumulative_percent == 85.0 and "- 누적 백분위: 85" in profile_to_text(built.profile)


@pytest.mark.parametrize("label", ["직전학기 백분율", "직전학기 백분율 점수"])
def test_percentage_labels_are_aliases_of_last_term_percentile(label):
    r = parse_student_text("- 학년: 2\n- {}: 84.22".format(label))
    assert r.data["gpa_percent"] == 84.22 and not r.report.unknown_labels


def test_language_cefr_letters_in_text():
    from scholarship.recommend import student_text as ST
    assert ST._language("TOEIC 780, 독일어 B1") == {"TOEIC": 780.0, "독일어": 3.0}
    assert ST._language("프랑스어 c2급") == {"프랑스어": 6.0}
    assert ST._language("HSK 5급") == {"HSK": 5.0}
    assert ST._cefr_letter("독일어", 3.0) == "B1" and ST._cefr_letter("HSK", 3.0) == "3"
