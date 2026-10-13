# -*- coding: utf-8 -*-
"""학생 프로필 v3: 새 필드 검증, 기존 JSON 입력 호환, 개인정보 필터가 새 필드를 막지 않는지."""
import dataclasses
import json
from pathlib import Path

import pytest

from scholarship.common import registry as R
from scholarship.recommend.profile import ProfileError, StudentProfile, build_profile, is_sensitive_key, normalize_term

FIX = Path(__file__).parent / "fixtures" / "profiles"
V3_FIELDS = ("gpa_last_term", "term_history", "entry_year", "admission_type", "gender", "age", "parent_residence_sido", "parent_residence_sigungu",
             "hs_sido", "hs_sigungu", "degree_held", "national_scholarship_applied", "special_codes", "special_complete", "school_programs", "extra",
             "residence_years_upper", "residence_years_open", "major_group", "gpa_cumulative_percent")


def test_v3_fields_default_to_unknown():
    p = build_profile({"level": "UNIV"}).profile
    assert all(getattr(p, f) is None for f in V3_FIELDS)


def test_v3_fields_from_json_are_normalized():
    p = build_profile({
        "gpa_last_term": "2026년 1학기", "gpa_scale": 4.5,
        "term_history": [{"term": "2025-2", "gpa": 3.11, "credits": 18}, {"term": "2026-1", "gpa": 3.79, "credits": 15}, {"term": "2025.1", "gpa": 3.4}],
        "entry_year": "2021", "admission_type": "편입학", "gender": "여학생", "age": 24,
        "parent_residence_sido": " 경기도 ", "parent_residence_sigungu": "수원시", "hs_sido": "경기도", "hs_sigungu": "안양시",
        "degree_held": "아니오", "national_scholarship_applied": "예", "special_codes": ["multi_child", "SINGLE_PARENT", "multi_child"],
        "special_complete": True, "school_programs": ["국가장학금", "local_talent"], "extra": {"scholar_terms": 6, "tags": ["a", "b"], "home": {"sido": "경기도", "sigungu": None}},
        "nationality": "재외동포"}).profile
    assert p.gpa_last_term == "2026-1" and p.entry_year == 2021 and p.admission_type == "TRANSFER" and p.gender == "F" and p.age == 24
    assert [r["term"] for r in p.term_history] == ["2026-1", "2025-2", "2025-1"]                         # 최근 학기부터
    assert p.term_history[2] == {"term": "2025-1", "gpa": 3.4, "credits": None}
    assert (p.parent_residence_sido, p.parent_residence_sigungu, p.hs_sido, p.hs_sigungu) == ("경기도", "수원시", "경기도", "안양시")
    assert p.degree_held is False and p.national_scholarship_applied is True
    assert p.special_codes == ["MULTI_CHILD", "SINGLE_PARENT"] and p.special_complete is True                  # 중복 제거, 대문자
    assert p.school_programs == ["NATIONAL_SCHOLARSHIP", "LOCAL_TALENT"] and p.nationality == "OVERSEAS_KR"
    assert p.extra == {"SCHOLAR_TERMS": 6, "TAGS": ["a", "b"], "HOME": {"sido": "경기도", "sigungu": None}}


@pytest.mark.parametrize("v, want", [("2026-1", "2026-1"), ("2026-2", "2026-2"), ("2026.1", "2026-1"), ("2026/2", "2026-2"), ("2026년 1학기", "2026-1"),
                                     ("2026-1학기", "2026-1"), (" 2026 - 1 ", "2026-1"), ("2026-3", None), ("26-1", None), ("2026", None), ("", None), (None, None)])
def test_normalize_term(v, want):
    assert normalize_term(v) == want


@pytest.mark.parametrize("data, msg", [
    ({"gpa_last_term": "2026-3"}, "gpa_last_term"),
    ({"term_history": "많음"}, "term_history: 목록"),
    ({"term_history": ["2026-1"]}, r"term_history\[0\]"),
    ({"term_history": [{"term": "2026-1"}]}, "gpa"),
    ({"term_history": [{"term": "2026-1", "gpa": 3.0}, {"term": "2026.1", "gpa": 3.1}]}, "두 번"),
    ({"term_history": [{"term": "2026-1", "gpa": 4.8}], "gpa_scale": 4.5}, "범위"),
    ({"term_history": [{"term": "2026-1", "gpa": 3.0, "credits": 99}]}, "credits"),
    ({"entry_year": 1800}, "entry_year"), ({"age": 200}, "age"), ({"admission_type": "복학"}, "admission_type"), ({"gender": "기타"}, "gender"),
    ({"special_codes": ["외계인"]}, "special_codes"), ({"school_programs": ["없는사업"]}, "school_programs"),
    ({"degree_held": "글쎄"}, "degree_held"), ({"extra": [1]}, "extra"), ({"extra": {"x": {"a": 1}}}, "extra.x"), ({"extra": {"x": [1, 2]}}, "extra.x"),
])
def test_invalid_v3_values_raise_profile_error(data, msg):
    with pytest.raises(ProfileError, match=msg):
        build_profile(data)


def test_v2_json_inputs_are_unchanged():
    for f in sorted(FIX.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        b = build_profile(d)
        assert all(getattr(b.profile, k) is None for k in V3_FIELDS), f.name                           # v2 입력에는 v3 값이 생기지 않음
    low = build_profile(json.loads((FIX / "chuncheon_low_gpa_lowincome.json").read_text(encoding="utf-8"))).profile
    assert low.gpa_last == 2.0 and low.is_basic_livelihood is True and low.residence_sigungu == "춘천시"


def test_every_profile_field_is_known_and_none_is_flagged_sensitive():
    names = [f.name for f in dataclasses.fields(StudentProfile)]
    assert len(names) == 30 + len(V3_FIELDS)
    assert not [n for n in names if is_sensitive_key(n)]                                                  # 새 필드 이름이 개인정보로 오인되어 버려지지 않음
    p = build_profile({n: None for n in names}).profile
    assert p == StudentProfile()


def test_unknown_keys_are_ignored_and_sensitive_keys_dropped_with_v3_input():
    b = build_profile({"age": 24, "parent_residence_sido": "경기도", "이름": "홍길동", "생년월일": "2000-01-01", "새로운항목": 1})
    assert b.profile.age == 24 and b.profile.parent_residence_sido == "경기도"
    assert b.dropped_sensitive == ["생년월일", "이름"] and b.ignored == ["새로운항목"]


def test_registry_profile_fields_all_exist_in_the_profile():
    """항목 사전의 profile_field 가 실제 프로필 필드와 어긋나면 파서와 판정이 서로 다른 이름을 쓰게 된다."""
    fields = {f.name for f in dataclasses.fields(StudentProfile)}
    bad = []
    for it in R.load_registry().items:
        for tok in (it.profile_field or "").split("/"):
            if tok and tok not in fields:
                bad.append((it.code, tok))
    assert bad == []


# ------------------------------------------------------------------ 4-A: 거주 기간 구간, 학기별 성적만 있을 때의 만점 가정
def test_residence_years_bounds_validation():
    p = build_profile({"residence_years": 1, "residence_years_upper": 3}).profile
    assert (p.residence_years, p.residence_years_upper, p.residence_years_open) == (1.0, 3.0, None)
    assert build_profile({"residence_years": 3, "residence_years_open": "예"}).profile.residence_years_open is True
    with pytest.raises(ProfileError, match="보다 작을 수 없습니다"):
        build_profile({"residence_years": 3, "residence_years_upper": 1})
    with pytest.raises(ProfileError, match="함께 쓸 수 없습니다"):
        build_profile({"residence_years": 3, "residence_years_upper": 5, "residence_years_open": True})


def test_term_history_alone_gets_the_default_gpa_scale_with_an_assumption():
    b = build_profile({"term_history": [{"term": "2026-1", "gpa": 3.8}]})
    assert b.profile.gpa_scale == 4.5 and any("4.5 으로 가정" in a for a in b.assumptions)
    given = build_profile({"term_history": [{"term": "2026-1", "gpa": 4.1}], "gpa_scale": 4.3})
    assert given.profile.gpa_scale == 4.3 and given.assumptions == []
    both = build_profile({"gpa_last": 3.5, "term_history": [{"term": "2026-1", "gpa": 3.5}]})
    assert len([a for a in both.assumptions if "가정" in a]) == 1                                     # 가정 문구가 중복되지 않음
    with pytest.raises(ProfileError, match="학기별 성적을 입력할 때는 만점"):
        build_profile({"term_history": [{"term": "2026-1", "gpa": 3.8}]}, default_gpa_scale=None)
