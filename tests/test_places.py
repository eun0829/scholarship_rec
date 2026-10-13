# -*- coding: utf-8 -*-
"""지명 비교(places): '시·도 + 시·군·구' 분해, 구 단위, 같은 이름 시·군·구의 모호성."""
import pytest

from scholarship.recommend import matcher, places
from scholarship.recommend.places import FAIL, PASS, UNKNOWN, Place, combine_any, compare, parse_place, student_place


@pytest.mark.parametrize("raw, sido, city, gu, other, ambiguous", [
    ("서울특별시 서초구", "서울", "서초", None, False, False),
    ("경기도 수원시", "경기", "수원", None, False, False),
    ("경기도 수원시 영통구", "경기", "수원", "영통", False, False),
    ("수원시 영통구", None, "수원", "영통", False, False),                      # 시·도 없이도 읽음
    ("영통구", "경기", "수원", "영통", False, False),                           # 구만 적어도 일반구 표로 수원시 영통구
    ("경기도수원시영통구", "경기", "수원", "영통", False, False),               # 띄어쓰기 없는 표기
    ("서초구", None, "서초", None, False, False),                              # 자치구
    ("중구", None, "중구", None, False, True),                                # 같은 이름이 여러 시·도에 있음
    ("서울특별시 중구", "서울", "중구", None, False, False),
    ("경기도 광주시", "경기", "광주", None, False, False),
    ("광주광역시 동구", "광주", "동구", None, False, False),
    ("광주 동구", "광주", "동구", None, False, False),                          # 뒤에 구가 오면 광주광역시
    ("광주", None, "광주", None, False, True),                                # 광역시인지 경기 광주시인지 알 수 없음
    ("경기도 수원시 광교동", "경기", "수원", None, True, False),                # 읍·면·동·리는 판정하지 않음
    ("광교동", None, None, None, True, False),
    ("강원특별자치도 춘천시", "강원", "춘천", None, False, False),
    ("세종특별자치시", "세종", None, None, False, False),
    ("인천", "인천", None, None, False, False),
    ("포항시 남구", None, "포항", "남구", False, False),                        # 시 아래의 남구(자치구의 남구와 구분)
    ("", None, None, None, False, False), (None, None, None, None, False, False),
])
def test_parse_place(raw, sido, city, gu, other, ambiguous):
    p = parse_place(raw)
    assert (p.sido, p.city, p.gu, p.other, p.ambiguous) == (sido, city, gu, other, ambiguous), p
    assert p.empty == (not (sido or city or gu))


@pytest.mark.parametrize("req, sido, sigungu, want", [
    ("서울특별시 서초구", "서울특별시", "서초구", PASS),                         # v2 엔진은 이 사례에서 FAIL 이었다
    ("경기도 수원시", "경기도", "수원시", PASS),
    ("경기도 수원시", "경기도", "수원시 영통구", PASS),                          # 시 단위 요건은 구까지 적은 학생도 충족
    ("경기도 수원시 영통구", "경기도", "수원시", UNKNOWN),                       # 구 단위 요건인데 학생은 시까지만: 확인 필요
    ("경기도 수원시 영통구", "경기도", "수원시 영통구", PASS),
    ("경기도 수원시 영통구", "경기도", "수원시 팔달구", FAIL),
    ("경기도 수원시 영통구", "경기도", "성남시 분당구", FAIL),
    ("영통구", "경기도", "수원시", UNKNOWN),
    ("분당구", "경기도", "성남시 수정구", FAIL),
    ("경기도", "경기도", "시흥시", PASS), ("경기도", "서울특별시", "서초구", FAIL), ("경기도", None, "시흥시", UNKNOWN),
    ("서울특별시 중구", "서울특별시", "중구", PASS), ("서울특별시 중구", "대구광역시", "중구", FAIL), ("부산광역시 중구", "서울특별시", "중구", FAIL),
    ("중구", "서울특별시", "중구", UNKNOWN),                                  # 시·도 없는 '중구'는 어느 곳인지 알 수 없음
    ("경기도 광주시", "광주광역시", "동구", FAIL), ("광주광역시", "광주광역시", "동구", PASS), ("경기도 광주시", "경기도", "광주시", PASS),
    ("경기도 광주시", None, "광주시", UNKNOWN),
    ("광주", "광주광역시", "동구", UNKNOWN), ("광주", "경기도", "광주시", UNKNOWN), ("광주", "서울특별시", "서초구", FAIL),
    ("경기도수원시영통구", "경기도", "수원시 영통구", PASS),
    ("경기도 수원시 광교동", "경기도", "수원시 영통구", UNKNOWN), ("광교동", "경기도", "수원시", UNKNOWN),
    ("경상남도 거창군", "경남", "거창군", PASS), ("경상남도 거창군", None, "거창군", PASS),            # 시·도를 모르는 학생도 고유한 이름이면 확인
    ("경상남도 거창군", "경상남도", None, UNKNOWN), ("경상남도 거창군", None, None, UNKNOWN),
    ("인천광역시", "인천", None, PASS), ("강원특별자치도 춘천시", "강원도", "춘천시", PASS), ("춘천시", "강원특별자치도", "춘천시", PASS),
    ("세종특별자치시", "세종시", None, PASS), ("서울특별시", "경기도", "수원시", FAIL),
])
def test_compare(req, sido, sigungu, want):
    got, why = compare(parse_place(req), student_place(sido, sigungu))
    assert got == want, (req, sido, sigungu, got, why)
    assert why                                                                      # 이유 문구가 항상 있다


def test_reasons_are_specific():
    assert compare(parse_place("경기도 수원시 영통구"), student_place("경기도", "수원시"))[1] == "구 단위까지 확인 필요 (학생이 시·군까지만 입력)"
    assert "시·도 확인 필요" in compare(parse_place("중구"), student_place("서울특별시", "중구"))[1]
    assert compare(parse_place("경기도"), student_place("서울특별시", "서초구"))[1] == "시·도가 다름"
    assert compare(parse_place("경기도 수원시"), student_place("경기도", "성남시"))[1] == "시·군·구가 다름"
    assert "광주광역시인지 경기 광주시인지" in compare(parse_place("광주"), student_place("광주광역시", "동구"))[1]


def test_student_place_joins_sido_and_sigungu():
    assert student_place("경기도", "수원시 영통구") == Place(sido="경기", city="수원", gu="영통", raw="경기도 수원시 영통구")
    assert student_place(None, None).empty and student_place("경기도", None).sido == "경기"


def test_combine_any():
    assert combine_any([FAIL, PASS]) == PASS and combine_any([FAIL, FAIL]) == FAIL and combine_any([FAIL, UNKNOWN]) == UNKNOWN
    assert combine_any([]) == PASS and combine_any([UNKNOWN, UNKNOWN]) == UNKNOWN


def test_the_old_v2_comparison_fails_on_combined_strings():
    """v2 엔진(matcher._place_status)은 '시·도 + 시·군·구'가 한 문자열로 적힌 공고를 같은 지역 학생에게도 미달로 판정했다. v3 경로는 places 를 쓴다."""
    assert matcher._place_status(["서울특별시 서초구"], "서울특별시", "서초구") == matcher.FAIL
    assert compare(parse_place("서울특별시 서초구"), student_place("서울특별시", "서초구"))[0] == PASS


# ------------------------------------------------------------------ 20건 실제 판정 결과 반영: 권역, 해외, 국내 표기 (9088 비수도권, 9141 중국)
@pytest.mark.parametrize("raw, group_name, size", [("비수도권", "비수도권", 14), ("수도권", "수도권", 3), ("충청권", "충청권", 4), ("호남권", "호남권", 3), ("영남권", "영남권", 5),
                                                  ("비수도권 소재", "비수도권", 14), ("수도권지역", "수도권", 3), (" 비 수도권 ", "비수도권", 14)])
def test_region_groups_are_parsed(raw, group_name, size):
    p = parse_place(raw)
    assert p.group_name == group_name and len(p.group) == size and not p.empty
    assert places_all_sido_is_partitioned()


def places_all_sido_is_partitioned():
    g = places.REGION_GROUPS
    return g["수도권"] | g["비수도권"] == places._ALL_SIDO and not (g["수도권"] & g["비수도권"])           # 수도권과 비수도권은 전체를 겹침 없이 나눔



@pytest.mark.parametrize("req, sido, sigungu, want", [
    ("비수도권", "경기도", "시흥시", FAIL), ("비수도권", "서울특별시", "성북구", FAIL), ("비수도권", "인천광역시", "서구", FAIL),
    ("비수도권", "부산광역시", "해운대구", PASS), ("비수도권", "세종특별자치시", None, PASS), ("비수도권", "강원특별자치도", "춘천시", PASS),
    ("비수도권", None, "시흥시", UNKNOWN), ("비수도권", None, None, UNKNOWN),
    ("수도권", "경기도", "시흥시", PASS), ("수도권", "부산광역시", "중구", FAIL),
    ("충청권", "대전광역시", "유성구", PASS), ("충청권", "경기도", "수원시", FAIL), ("호남권", "광주광역시", "동구", PASS), ("영남권", "경상북도", "포항시", PASS),
    ("중국", "경기도", "시흥시", FAIL), ("해외", "경기도", "시흥시", FAIL), ("중국 상하이", "서울특별시", None, FAIL), ("미국", None, "수원시", FAIL), ("중국", None, None, UNKNOWN),
    ("대한민국", "경기도", "시흥시", PASS), ("국내", None, None, UNKNOWN),
])
def test_compare_groups_foreign_and_domestic(req, sido, sigungu, want):
    got, why = compare(parse_place(req), student_place(sido, sigungu))
    assert got == want, (req, sido, sigungu, got, why)


def test_group_and_foreign_reasons_read_naturally():
    assert compare(parse_place("비수도권"), student_place("경기도", "시흥시"))[1] == "비수도권이 아님"
    assert compare(parse_place("수도권"), student_place("부산광역시", None))[1] == "수도권이 아님"
    assert compare(parse_place("중국"), student_place("경기도", "시흥시"))[1] == "국내 지역이라 해외 조건에 해당하지 않음"
    assert "비수도권 여부를 알 수 없음" in compare(parse_place("비수도권"), student_place(None, None))[1]


def test_real_place_names_that_resemble_the_new_words_are_not_misread():
    for name, city in (("인천광역시 서구", "서"), ("경기도 시흥시", "시흥"), ("강원특별자치도 인제군", "인제"), ("충청북도 증평군", "증평")):
        p = parse_place(name)
        assert p.group is None and not p.foreign and not p.domestic and p.sido is not None, name


# ================================================================== 세부 지역(읍·면·동·리): 이름이 정확히 같을 때만 충족 (4-B2)
@pytest.mark.parametrize("need, have, want", [
    ("인천광역시 서구 석남1동", "인천광역시 서구 석남1동", PASS), ("인천광역시 서구 석남1동", "인천광역시 서구 검단동", UNKNOWN), ("인천광역시 서구 석남1동", "인천광역시 서구", UNKNOWN),
    ("강원특별자치도 삼척시 원덕읍", "강원특별자치도 삼척시 원덕읍", PASS), ("강원특별자치도 삼척시 원덕읍", "강원특별자치도 삼척시 근덕면", UNKNOWN),
    ("강원특별자치도 삼척시 원덕읍 노곡리", "강원특별자치도 삼척시 원덕읍", UNKNOWN), ("강원특별자치도 삼척시 원덕읍 노곡리", "강원특별자치도 삼척시 원덕읍 노곡리", PASS),
    ("서울특별시 서초구", "서울특별시 서초구 반포동", PASS),                                                  # 공고에 세부 지역이 없으면 학생의 세부 지역은 무관
    ("인천광역시 서구 석남1동", "인천광역시 부평구 석남1동", FAIL), ("인천광역시 서구 석남1동", "서울특별시 서초구 석남1동", FAIL)])
def test_detail_place_matches_only_on_the_exact_name(need, have, want):
    assert compare(parse_place(need), parse_place(have))[0] == want


def test_detail_names_are_recorded_and_a_different_dong_is_never_a_fail():
    assert parse_place("인천광역시 서구 석남1동").detail == ("석남1동",) and parse_place("서울특별시 서초구").detail == ()
    assert parse_place("강원특별자치도 삼척시 원덕읍 노곡리").detail == ("원덕읍", "노곡리")
    assert compare(parse_place("인천광역시 서구 석남1동"), parse_place("인천광역시 서구 검단동")) == (UNKNOWN, "세부 지역(읍·면·동·리) 확인 필요")    # 법정동·행정동 표기 차이로 미달로 단정하지 않음
    assert compare(parse_place("인천광역시 서구 석남1동"), parse_place("인천광역시 서구 석남1동")) == (PASS, "해당 (세부 지역 이름 일치)")
