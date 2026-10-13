# -*- coding: utf-8 -*-
import pytest

from scholarship.recommend.profile import ProfileError, build_profile


def test_korean_synonyms_and_basic_fields():
    b = build_profile({"level": "대학생", "grade": 3, "status": "휴학", "school_type": "사이버대학", "nationality": "대한민국",
                       "residence_sido": " 경기도 ", "is_basic_livelihood": "예", "tuition_paid": "N"})
    p = b.profile
    assert (p.level, p.grade, p.status, p.school_type, p.nationality) == ("UNIV", 3, "LEAVE", "REMOTE", "KR")
    assert p.residence_sido == "경기도" and p.is_basic_livelihood is True and p.tuition_paid is False
    assert p.gpa_last is None and p.special_status is None            # 입력 없음 = None (판정에서 '알 수 없음')


def test_sensitive_keys_are_dropped_not_stored():
    b = build_profile({"level": "UNIV", "name": "홍길동", "Student_ID": "2026001", "학번": "1", "E-mail": "x", "email": "a@b.c",
                       "Phone": "010", "address": "시흥시 ...", "주민등록번호": "000000-0000000", "foo": 1})
    assert b.dropped_sensitive == sorted(["name", "Student_ID", "학번", "E-mail", "email", "Phone", "address", "주민등록번호"])
    assert b.ignored == ["foo"]                                         # 모르는 항목은 무시
    dumped = repr(b.profile)
    assert "홍길동" not in dumped and "2026001" not in dumped and "a@b.c" not in dumped and "시흥시 ..." not in dumped


def test_gpa_scale_assumed_or_required():
    b = build_profile({"gpa_last": 3.5})
    assert b.profile.gpa_scale == 4.5 and "4.5 으로 가정" in b.assumptions[0]
    with pytest.raises(ProfileError, match="gpa_scale"):
        build_profile({"gpa_last": 3.5}, default_gpa_scale=None)
    assert build_profile({"gpa_last": 3.5, "gpa_scale": 4.3}).assumptions == []
    assert build_profile({"level": "UNIV"}).assumptions == []           # 평점이 없으면 가정할 것이 없음


@pytest.mark.parametrize("data,msg", [
    ({"level": "외계인"}, "level"),
    ({"status": "도망"}, "status"),
    ({"grade": 9}, "grade"),
    ({"income_bracket": 11}, "income_bracket"),
    ({"gpa_last": 4.8, "gpa_scale": 4.5}, "gpa_last"),               # 만점보다 큼
    ({"gpa_scale": 3.3}, "gpa_scale"),
    ({"is_near_poor": "maybe"}, "is_near_poor"),
    ({"credits_last": "많이"}, "credits_last"),
    ({"special_status": 3}, "special_status"),
    ({"language_scores": ["TOEIC"]}, "language_scores"),
    ("문자열", "JSON 객체"),
])
def test_invalid_values_raise_profile_error(data, msg):
    with pytest.raises(ProfileError, match=msg):
        build_profile(data)


def test_lists_and_scores():
    p = build_profile({"special_status": [], "current_scholarships": [" 국가장학금 ", ""], "nationality": "외국인",
                       "language_scores": {"toeic": 800, "OPIc ": "7"}}).profile
    assert p.special_status == [] and p.current_scholarships == ["국가장학금"] and p.nationality == "FOREIGN"
    assert p.language_scores == {"TOEIC": 800.0, "OPIC": 7.0}


def test_json_fixture_files_load():
    import json, os
    d = os.path.join(os.path.dirname(__file__), "fixtures", "profiles")
    for f in os.listdir(d):
        build_profile(json.load(open(os.path.join(d, f), encoding="utf-8")))


@pytest.mark.parametrize("key,sensitive", [
    ("home_address", True), ("E-Mail", True), ("phone_number", True), ("Student ID", True), ("student-name", True),
    ("birth_date", True), ("학생이름", True), ("보호자연락처", True), ("계좌번호", True), ("rrn", True),
    ("residence_sido", False), ("school_sigungu", False), ("gpa_last", False), ("income_bracket", False), ("major", False),
    ("residence_years", False),
])
def test_sensitive_key_detection_is_robust_but_not_overbroad(key, sensitive):
    from scholarship.recommend.profile import is_sensitive_key
    assert is_sensitive_key(key) is sensitive
