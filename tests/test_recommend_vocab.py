# -*- coding: utf-8 -*-
import pytest

from scholarship.recommend import vocab as v


@pytest.mark.parametrize("text,expected", [
    ("재학생(5학기 이상 등록)", {v.ENROLLED}),
    ("휴학생", {v.LEAVE}),
    ("졸업생", {v.GRADUATED}), ("수료생", {v.GRADUATED}), ("졸업", {v.GRADUATED}),
    ("졸업학기", {v.GRADUATING}), ("졸업예정자", {v.GRADUATING}),
    ("초과학기생", {v.EXTENDED}), ("정규학기 초과등록 학생", {v.EXTENDED}),
    ("졸업연기자(0학점 재학생)", {v.DEFERRED, v.ENROLLED}), ("학사학위 취득유예생", {v.DEFERRED}),
    ("대학원생", {v.GRAD_STUDENT}), ("사이버대학", {v.REMOTE}), ("학점은행제", {v.REMOTE}), ("방송통신대", {v.REMOTE}),
    ("신청 당시 등록금 미납 또는 분납 상태인 재학생", {v.TUITION_UNPAID, v.ENROLLED}),
    ("신입생", {v.NEW_STUDENT}), ("편입생", {v.TRANSFER}),
    ("졸업어학자격 미합격자", set()),                                    # '졸업'이 들어가지만 졸업생 조건이 아님
])
def test_status_tags(text, expected):
    assert v.status_tags(text) == expected


@pytest.mark.parametrize("a,b,same", [
    ("경기도", "경기", True), ("서울특별시", "서울시", True), ("강원특별자치도", "강원도", True), ("전라북도", "전북특별자치도", True),
    ("충북", "충청북도", True), ("경기도", "서울", False),
])
def test_sido_core(a, b, same):
    assert (v.sido_core(a) == v.sido_core(b)) is same and v.sido_core(a) is not None


def test_place_core_and_classification():
    assert v.place_core("춘천시") == v.place_core("춘천") == "춘천"
    assert v.place_core("시흥시") == "시흥" and v.place_core("강남구") == "강남" and v.place_core("서울특별시") == "서울"
    assert v.classify_place("춘천시") == ("SIGUNGU", "춘천")
    assert v.classify_place("강원특별자치도") == ("SIDO", "강원")
    assert v.classify_place("혈동2리") == ("OTHER", None) and v.classify_place("신북읍") == ("OTHER", None)
    assert v.classify_place("") == ("OTHER", None)


def test_income_kinds_and_brackets():
    assert v.income_kind("국민기초생활수급자") == v.BASIC and v.income_kind("기초생활수급자") == v.BASIC
    assert v.income_kind("차상위계층") == v.NEAR_POOR and v.income_kind("한부모가족") == v.SINGLE_PARENT
    assert v.income_kind("한국장학재단 학자금 지원구간 2구간 이내") == v.BRACKET and v.income_kind("소득분위 3분위 이하") == v.BRACKET
    assert v.income_kind("농어촌 거주") == v.OTHER
    assert v.parse_bracket("학자금 지원구간 2구간 이내") == 2 and v.parse_bracket("소득 8분위") == 8 and v.parse_bracket("기초") is None


def test_nationality_and_durations():
    assert v.nationality_kind("대한민국 국적자") == "KR" and v.nationality_kind("외국인 유학생") == "FOREIGN"
    assert v.nationality_kind(None) is None and v.nationality_kind("재외동포") is None
    assert v.durations("기본 1년 이상 거주, 주소이전(3개월~1년 미만)") == [12, 3, 12]
    assert v.durations("거주 제한 없음") == []
