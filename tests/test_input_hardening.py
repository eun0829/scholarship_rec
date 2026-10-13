# -*- coding: utf-8 -*-
"""입력 보강 P1~P6: 시·도 검증과 대구 결함(P1), 국적 정규화(P2), 키 정규화와 무시된 키 경고(P3), 라벨 동의어(P4), 엄격 모드(P5), 범위·형식 강화(P6)."""
import json
from pathlib import Path

import pytest

from scholarship.recommend import matcher as M
from scholarship.recommend import places, vocab
from scholarship.recommend import profile_contract as PC
from scholarship.recommend import request as R
from scholarship.recommend import result_format as F
from scholarship.recommend.profile import ProfileError, build_profile
from scholarship.recommend.service import Recommender
from scholarship.recommend.student_text import parse_student_text
from tests.test_followup_service import BASE, five, school
from tests.test_matcher_v3 import notice, prof, reg, req
from tests.test_recommend_service import Stub

ROOT = Path(PC.__file__).resolve().parents[2]


def accepts(d, scale=4.5):
    try:
        build_profile(d, scale)
        return True
    except ProfileError:
        return False


def region_status(sido, sigungu, need):
    p = prof(**{k: v for k, v in (("residence_sido", sido), ("residence_sigungu", sigungu)) if v is not None})
    return next(c for c in M.evaluate(notice(**reg("ANY", req("RESIDENCE_SELF", need))), p).common if c.key == "region").status


# ================================================================== P1: 시·도는 17개 공식 이름만, 대구 결함
SIDO_VARIANTS = [(official, alias) for core, official in vocab._SIDO_OFFICIAL.items() for alias in vocab._SIDO[core]]


def test_there_are_seventeen_official_names_and_every_alias_maps_to_one():
    assert len(vocab.SIDO_OFFICIAL_NAMES) == 17 == len(set(vocab.SIDO_OFFICIAL_NAMES)) and len(SIDO_VARIANTS) == 45
    assert vocab.canon_sido("강원도") == "강원특별자치도" and vocab.canon_sido("전라북도") == "전북특별자치도" and vocab.canon_sido("제주도") == "제주특별자치도" and vocab.canon_sido("세종시") == "세종특별자치시"


@pytest.mark.parametrize("official, alias", SIDO_VARIANTS)
def test_every_sido_spelling_is_stored_as_the_official_name(official, alias):
    assert vocab.canon_sido(alias) == official and vocab.canon_sido("  " + alias + " ") == official
    for key in ("residence_sido", "parent_residence_sido", "hs_sido"):
        assert getattr(build_profile({key: alias}, 4.5).profile, key) == official


@pytest.mark.parametrize("bad", ["서울틐별시", "Seoul", "수원시", "서초구", "경기도 수원시", "서울특별시청", "11", "경기두", "대구광역", "광주광역"])
def test_unknown_sido_values_are_rejected_instead_of_silently_failing_the_notice(bad):
    for key in ("residence_sido", "parent_residence_sido", "hs_sido"):
        with pytest.raises(ProfileError, match="알 수 없는 시·도"):
            build_profile({key: bad}, 4.5)


def test_an_unknown_sido_is_a_warning_and_the_region_stays_unconfirmed_not_rejected():
    clean, warnings = R.sanitize_profile({"residence_sido": "서울틐별시", "residence_sigungu": "서초구"}, 4.5)
    assert "residence_sido" not in clean and len(warnings) == 1 and warnings[0].startswith("profile.residence_sido: 알 수 없는 시·도입니다 ('서울틐별시')") and "residence_sido: residence_sido" not in warnings[0]
    assert region_status(None, "서초구", "서울특별시 서초구") == "PASS"                                                  # 시·도 없이 구만 있어도 이름이 하나뿐이면 맞다
    status, body = R.handle_recommend(Recommender(Stub(five()), school()), {"profile": {"residence_sido": "서울틐별시", "residence_sigungu": "서초구", "level": "UNIV"}, "today": "2026-10-06"})
    assert status == 200 and body["profile_warnings"][0].startswith("profile.residence_sido") and F.validate(body) == []


PAIRS = [("서울특별시", "서초구"), ("부산광역시", "해운대구"), ("대구광역시", "수성구"), ("인천광역시", "서구"), ("광주광역시", "북구"), ("대전광역시", "유성구"), ("울산광역시", "남구"), ("경기도", "수원시"),
         ("강원특별자치도", "춘천시"), ("충청북도", "청주시"), ("충청남도", "천안시"), ("전북특별자치도", "전주시"), ("전라남도", "순천시"), ("경상북도", "포항시"), ("경상남도", "창원시"),
         ("제주특별자치도", "제주시"), ("대구광역시", "중구"), ("대구광역시", "달서구"), ("대구광역시", "동구")]


@pytest.mark.parametrize("sido, sigungu", PAIRS)
def test_a_student_in_the_required_place_passes_however_the_sido_is_written(sido, sigungu):
    """공식 이름·약칭·옛 이름 어느 쪽으로 적어도 같은 지역 요건을 충족한다 (대구는 약칭 '대구', '대구시' 에서 미달이 되던 결함)"""
    core = next(c for c, o in vocab._SIDO_OFFICIAL.items() if o == sido)
    for alias in vocab._SIDO[core]:
        assert region_status(alias, sigungu, "{} {}".format(sido, sigungu)) == "PASS", (alias, sigungu)
    assert region_status(sido, sigungu, "{} {}".format(sido, sigungu)) == "PASS"


def test_the_daegu_tokenizer_defect_is_fixed():
    assert places._tokens("대구광역시 수성구") == ["대구광역시", "수성구"] and places._tokens("대구광역시수성구") == ["대구광역시", "수성구"]
    assert places._tokens("경기도수원시영통구") == ["경기도", "수원시", "영통구"] and places._tokens("부산광역시 해운대구") == ["부산광역시", "해운대구"]
    p = places.parse_place("대구광역시 수성구")
    assert (p.sido, p.city) == ("대구", "수성") and places.compare(p, places.student_place("대구", "수성구"))[0] == "PASS"


AMBIGUOUS = [("중구", ["서울특별시 중구", "부산광역시 중구", "대구광역시 중구", "인천광역시 중구", "대전광역시 중구", "울산광역시 중구"]), ("서구", ["인천광역시 서구", "부산광역시 서구", "대구광역시 서구", "광주광역시 서구"]),
             ("동구", ["광주광역시 동구", "부산광역시 동구", "대구광역시 동구"]), ("고성군", ["강원특별자치도 고성군", "경상남도 고성군"]), ("광주", ["광주광역시", "경기도 광주시"])]


@pytest.mark.parametrize("sigungu, needs", AMBIGUOUS)
def test_same_named_places_without_a_sido_are_never_decided_either_way(sigungu, needs):
    """시·도 없이 이름만으로는 어느 지역인지 알 수 없으므로 충족도 미달도 단정하지 않는다 (확인 필요)"""
    assert [region_status(None, sigungu, n) for n in needs] == ["UNKNOWN"] * len(needs)


# ================================================================== P2: 국적
@pytest.mark.parametrize("value, want", [(v, "KR") for v in ("KR", "kr", "KOR", "Korea", "Korean", "South Korea", "대한민국", "한국", "한국인", "내국인", " 내 국 인 ")] +
                         [(v, "FOREIGN") for v in ("FOREIGN", "foreigner", "외국인", "외국", "비내국인", "외국인(유학생)")] + [(v, "OVERSEAS_KR") for v in ("OVERSEAS_KR", "재외동포", "재외국민")])
def test_nationality_spellings_are_normalized(value, want):
    assert build_profile({"nationality": value}, 4.5).profile.nationality == want


@pytest.mark.parametrize("bad", ["OTHER", "XYZ", "미국", "USA", "한국계", "1", "korean-american"])
def test_unknown_nationality_is_rejected_not_stored_as_is(bad):
    with pytest.raises(ProfileError, match="nationality: 알 수 없는 값"):
        build_profile({"nationality": bad}, 4.5)


def test_a_korean_student_written_as_korean_is_no_longer_rejected_from_domestic_only_notices():
    n = notice(nationality="내국인", target_levels=["UNIV"])
    for v in ("Korean", "한국인", "KR"):
        c = next(c for c in M.evaluate(n, prof(nationality=v)).common if c.key == "nationality")
        assert c.status == "PASS", v
    assert next(c for c in M.evaluate(n, prof(nationality="외국인")).common if c.key == "nationality").status == "FAIL"


# ================================================================== P6: 범위·길이·개수 (표의 값과 코드가 정확히 일치)
@pytest.mark.parametrize("key", sorted(PC.BOUNDS))
def test_the_documented_numeric_range_is_exactly_what_the_code_accepts(key):
    lo, hi = PC.BOUNDS[key]
    extra = {"gpa_scale": 4.5} if key in PC.GPA_KEYS else {}
    hi = 4.5 if key in PC.GPA_KEYS else (2100 if key == "entry_year" else hi)
    step = 1 if PC.FORMAT[key][0] == "integer" else 0.1
    assert accepts({key: lo, **extra}) and accepts({key: hi, **extra}), key
    assert not accepts({key: lo - step, **extra}) and not accepts({key: hi + step, **extra}), key


@pytest.mark.parametrize("key, limit", sorted(PC.LENGTHS.items()))
def test_string_length_limits(key, limit):
    assert accepts({key: "가" * limit}) and not accepts({key: "가" * (limit + 1)})


def terms(n):
    return [{"term": "{}-{}".format(2026 - i // 2, 2 - i % 2), "gpa": 3.0, "credits": 15} for i in range(n)]


def test_term_history_is_limited_to_twenty_terms_and_ignores_the_order():
    assert accepts({"term_history": terms(20)}) and not accepts({"term_history": terms(21)}) and PC.MAX_ITEMS["term_history"] == 20
    for rows in (terms(6), list(reversed(terms(6)))):
        assert [r["term"] for r in build_profile({"term_history": rows}, 4.5).profile.term_history] == ["2026-2", "2026-1", "2025-2", "2025-1", "2024-2", "2024-1"]
    with pytest.raises(ProfileError, match="두 번 들어 있습니다"):
        build_profile({"term_history": terms(2) + terms(1)}, 4.5)


def test_language_score_and_residence_limits():
    assert accepts({"language_scores": {"TOEIC": 1000}}) and not accepts({"language_scores": {"TOEIC": 1001}}) and PC.LANGUAGE_MAX == 1000
    assert accepts({"residence_years": 100}) and not accepts({"residence_years": 101})
    assert not accepts({"residence_years": 3, "residence_years_upper": 1}) and not accepts({"residence_years": 3, "residence_years_open": True, "residence_years_upper": 5})


def test_the_documented_table_shows_every_range():
    table = PC.markdown_table()
    assert "| 허용 범위·값 |" in table
    row = {f.key: next(l for l in table.splitlines() if l.startswith("| `{}` |".format(f.key))) for f in PC.fields()}
    for key, (lo, hi) in PC.BOUNDS.items():
        if key in PC.GPA_KEYS or key == "entry_year" or key == "residence_years_upper":
            continue
        assert "{:g} ~ {:g}".format(lo, hi) in row[key], key
    assert "0 ~ `gpa_scale`" in row["gpa_last"] and "1990 ~ (올해+1)" in row["entry_year"] and "1 ~ 20개" in row["term_history"] and "1 ~ 40자" in row["residence_sigungu"] and "1 ~ 100자" in row["major"]
    assert "`KR` / `FOREIGN` / `OVERSEAS_KR`" in row["nationality"] and "`true` / `false`" in row["degree_held"] and "17개 시·도 공식 이름" in row["hs_sido"]


def test_the_openapi_profile_carries_the_same_limits():
    props = json.loads((ROOT / "docs" / "openapi_v1.json").read_text(encoding="utf-8"))["components"]["schemas"]["Profile"]["properties"]
    for key, (lo, hi) in PC.BOUNDS.items():
        assert props[key]["minimum"] == lo, key
        if key not in PC.GPA_KEYS and hi is not None:
            assert props[key]["maximum"] == hi, key
    assert props["gpa_last"]["maximum"] == 5.0 and props["major"]["maxLength"] == 100 and props["hs_sigungu"]["maxLength"] == 40 and props["term_history"]["maxItems"] == 20
    assert props["language_scores"]["additionalProperties"]["maximum"] == 1000 and props["special_codes"]["uniqueItems"] is True and len(props["special_codes"]["items"]["enum"]) == 8
    assert props["residence_sido"]["enum"] == list(vocab.SIDO_OFFICIAL_NAMES) and props["nationality"]["enum"] == ["KR", "FOREIGN", "OVERSEAS_KR"] and "pattern" in props["gpa_last_term"]
    assert json.loads((ROOT / "docs" / "openapi_v1.json").read_text(encoding="utf-8"))["components"]["schemas"]["RecommendRequest"]["properties"]["strict"]["type"] == "boolean"


@pytest.mark.parametrize("key", [f.key for f in PC.fields() if f.type == "boolean"])
def test_booleans_must_be_json_booleans(key):
    clean, warnings = R.check_types({key: "true", "grade": 4})
    assert clean == {"grade": 4} and warnings == ["profile.{}: true/false 형식이어야 합니다 (받은 값: str)".format(key)]
    assert R.check_types({key: 1})[0] == {} and R.check_types({key: True})[1] == [] and R.check_types({key: False})[0] == {key: False}


def test_arrays_and_objects_must_be_json_arrays_and_objects_of_the_right_content():
    assert R.check_types({"special_codes": "DISABLED"})[0] == {} and R.check_types({"special_codes": [1]})[0] == {} and R.check_types({"special_codes": ["DISABLED"]})[0] == {"special_codes": ["DISABLED"]}
    assert R.check_types({"special_codes": []})[0] == {"special_codes": []} and R.check_types({"term_history": "x"})[0] == {} and R.check_types({"language_scores": []})[0] == {}
    c, w = R.check_types({"language_scores": {"TOEIC": "780"}}, "answers")
    assert c == {} and w == ["answers.language_scores: 값은 숫자여야 합니다 ('780' 같은 문자열은 받지 않습니다)"]
    assert R.check_types({"language_scores": {"TOEIC": True}})[0] == {} and R.check_types({"language_scores": {}})[0] == {"language_scores": {}}


def test_a_current_grade_that_disagrees_with_the_term_history_is_warned_not_dropped():
    rows = [{"term": "2026-1", "gpa": 3.79, "credits": 15}, {"term": "2025-2", "gpa": 3.11, "credits": 18}]
    clean, w = R.sanitize_profile({"gpa_scale": 4.5, "gpa_last": 3.0, "term_history": rows}, 4.5)
    assert len(w) == 1 and w[0].startswith("profile.gpa_last: 3 이(가) term_history 의 2026-1 학기 평점 3.79") and clean["gpa_last"] == 3.0
    assert R.sanitize_profile({"gpa_scale": 4.5, "gpa_last": 3.79, "term_history": rows}, 4.5)[1] == []
    assert R.sanitize_profile({"gpa_scale": 4.5, "gpa_last": 3.11, "gpa_last_term": "2025-2", "term_history": rows}, 4.5)[1] == []                 # 직전학기 이름으로 맞는 학기를 찾는다
    assert R.sanitize_profile({"gpa_scale": 4.5, "gpa_last": 3.5}, 4.5)[1] == []


# ================================================================== P3: 키 정규화
@pytest.mark.parametrize("sent", ["GPA_LAST", " gpa_last", "gpa_last ", "Gpa_Last", "gpa-last", "Gpa Last", "gpa  last"])
def test_key_spelling_variants_are_read_as_the_contract_key(sent):
    clean, w = R.normalize_keys({sent: 3.5, "grade": 4})
    assert clean == {"gpa_last": 3.5, "grade": 4} and w == []


def test_a_duplicate_after_normalization_keeps_the_exact_key_and_says_so():
    clean, w = R.normalize_keys({"grade": 4, "GRADE": 3})
    assert clean == {"grade": 4} and w == ["profile.GRADE: `grade` 와 같은 항목이 이미 있어 무시했습니다"]
    assert R.normalize_keys({"GRADE": 3, "grade ": 2})[0] == {"grade": 3} and len(R.normalize_keys({"GRADE": 3, "grade ": 2})[1]) == 1


def test_misspelled_keys_are_ignored_with_a_warning_and_the_wrong_value_is_not_used():
    clean, w = R.sanitize_profile({"grade": 4, "gpa_lat": 3.5, "학년": 4, "이름": "홍길동"}, 4.5)
    assert w == ["profile.gpa_lat: 알 수 없는 항목이라 무시했습니다", "profile.학년: 알 수 없는 항목이라 무시했습니다"]
    status, body = R.handle_recommend(Recommender(Stub(five()), school()), {"profile": dict(BASE, **{"GPA_LAST": 3.5, "gpa_lat": 1}), "today": "2026-10-06"})
    assert status == 200 and body["profile_warnings"] == ["profile.gpa_lat: 알 수 없는 항목이라 무시했습니다"] and body["profile_notes"]["ignored_keys"] == ["gpa_lat"]
    assert "profile.GPA_LAST" not in " ".join(body["profile_warnings"])                                                                         # 대소문자 차이는 받아들인다


def test_answers_keys_and_types_are_checked_the_same_way():
    rec = Recommender(Stub(five()), school())
    status, body = R.handle_recommend(rec, {"profile": BASE, "answers": {"AGE": 31, "degree_held": "true", "PARENT_RESIDENCE_SIDO": "서울특별시", "parent_residence_sigungu": "서초구"}, "round": 2, "today": "2026-10-06"})
    assert status == 200 and body["answers"]["warnings"] == ["answers.degree_held: true/false 형식이어야 합니다 (받은 값: str)"]
    assert sorted(body["answers"]["applied"]) == ["age", "parent_residence_sido", "parent_residence_sigungu"] and {c["src_id"] for c in body["changes"]} == {"1", "2"}


# ================================================================== P4: 라벨 동의어 (뜻이 갈리는 말은 일부러 넣지 않는다)
@pytest.mark.parametrize("label, value, key, want", [
    ("현재 학년", "4", "grade", 4), ("몇 학년", "4", "grade", 4), ("재학 학년", "4", "grade", 4), ("지난 학기 평점", "3.79 / 4.5", "gpa_last", 3.79), ("지난학기 평점", "3.79 / 4.5", "gpa_last", 3.79),
    ("연령", "24", "age", 24), ("소속 학과", "전자공학부", "major", "전자공학부"), ("등록 학기 수", "7", "semesters_completed", 7),
    ("부모님 주소", "서울 서초구", "parent_residence_sido", "서울특별시"), ("부모 거주지", "서울 서초구", "parent_residence_sido", "서울특별시"), ("부모님 거주지", "서울 서초구", "parent_residence_sido", "서울특별시")])
def test_safe_label_synonyms_are_recognized(label, value, key, want):
    r = parse_student_text("- {}: {}".format(label, value))
    assert not r.report.warnings and key in r.data, (label, r.data)
    assert getattr(build_profile(r.data, 4.5).profile, key) == want                                                  # 시·도 약칭은 프로필 단계에서 공식 이름이 된다


@pytest.mark.parametrize("label", ["학점", "거주지", "현주소", "GPA", "토익", "사는 곳", "분위"])
def test_ambiguous_labels_are_deliberately_not_guessed(label):
    r = parse_student_text("- {}: 3.5".format(label))
    assert r.data == {} and len(r.report.warnings) == 1                                                       # 본인/부모? 평점/취득학점? 알 수 없으니 경고로 되묻는다


def test_the_bare_address_label_is_treated_as_personal_information():
    r = parse_student_text("- 주소: 서울특별시 서초구 반포대로 1")
    assert r.data == {} and r.report.dropped_sensitive == ["주소"] and r.report.warnings == []                       # 본인/부모/학교 주소를 구분할 수 없고 상세 주소는 받지 않는다


# ================================================================== P5: 엄격 모드
SAMPLES = json.loads((ROOT / "docs" / "samples" / "api_samples.json").read_text(encoding="utf-8"))["students"]


def viol(data, **kw):
    return R.contract_violations(data, **kw)


def test_the_sample_requests_contain_no_contract_violations():
    """연동 시험용 샘플은 계약을 한 글자도 어기지 않는다 (포털 변환 코드가 맞춰야 할 기준)"""
    assert [(s["name"], viol(s["first_request"]["profile"])) for s in SAMPLES if viol(s["first_request"]["profile"])] == []


@pytest.mark.parametrize("data, needle", [
    ({"GPA_LAST": 3.5, "gpa_scale": 4.5}, "profile.GPA_LAST: 키는 `gpa_last` 로 정확히 보내 주세요"), ({"gpa_lat": 3.5}, "profile.gpa_lat: 계약에 없는 항목입니다"),
    ({"이름": "홍길동"}, "profile.이름: 학생을 식별하는 개인정보는 보내지 않습니다"), ({"age": None}, "profile.age: null 대신 키를 생략해 주세요"),
    ({"grade": "4"}, "profile.grade: 정수 형식이어야 합니다 (받은 값: str)"), ({"grade": 4.0}, "profile.grade: 정수 형식이어야 합니다 (받은 값: float)"), ({"grade": True}, "정수 형식"),
    ({"gpa_last": "3.5", "gpa_scale": 4.5}, "profile.gpa_last: 숫자 형식이어야 합니다"), ({"degree_held": "true"}, "profile.degree_held: true/false 형식"), ({"degree_held": 1}, "true/false 형식"),
    ({"level": "대학생"}, "profile.level: 허용 값이 아닙니다 (UNIV / GRAD)"), ({"level": "HIGH"}, "허용 값이 아닙니다"), ({"nationality": "Korean"}, "profile.nationality: 허용 값이 아닙니다 (KR / FOREIGN / OVERSEAS_KR)"),
    ({"residence_sido": "경기"}, "profile.residence_sido: 공식 이름이어야 합니다 (예: 경기도). 받은 값: '경기'"), ({"hs_sido": "서울틐별시"}, "profile.hs_sido: 공식 이름이어야 합니다"),
    ({"major": ""}, "profile.major: 빈 문자열은 보내지 말고 키를 생략해 주세요"), ({"major": "가" * 101}, "profile.major: 100자를 넘을 수 없습니다"), ({"hs_sigungu": "가" * 41}, "40자를 넘을 수 없습니다"),
    ({"grade": 7}, "profile.grade: 1 ~ 6 범위여야 합니다 (받은 값: 7)"), ({"age": 101}, "0 ~ 100 범위"), ({"income_bracket": 11}, "0 ~ 10 범위"), ({"gpa_last": 4.6, "gpa_scale": 4.5}, "0 ~ 4.5 범위"),
    ({"gpa_last": 4.2, "gpa_scale": 4.0}, "0 ~ 4 범위"), ({"gpa_scale": 4.2}, "4.0 / 4.3 / 4.5 / 5.0 중 하나"), ({"gpa_last_term": "2026.1"}, "profile.gpa_last_term: `YYYY-1` 또는 `YYYY-2` 형식"),
    ({"gpa_last": 3.5}, "profile.gpa_scale: 성적 값과 함께 보내야 합니다"), ({"special_codes": "DISABLED"}, "profile.special_codes: 배열 형식"), ({"special_codes": ["XYZ"]}, "허용되지 않는 신분 코드: XYZ"),
    ({"special_codes": ["DISABLED", "DISABLED"]}, "중복"), ({"special_codes": ["OTHER"]}, "허용되지 않는 신분 코드: OTHER"), ({"language_scores": {"TOEIC": "780"}}, "TOEIC: 숫자여야 합니다"),
    ({"language_scores": {"TOEIC": 1001}}, "0 ~ 1000 범위"), ({"term_history": terms(21), "gpa_scale": 4.5}, "1 ~ 20개여야 합니다 (21개)"), ({"term_history": [{"term": "2026-3", "gpa": 3, "credits": 3}], "gpa_scale": 4.5}, "[0].term"),
    ({"term_history": [{"term": "2026-1", "gpa": 3, "credits": 3}] * 2, "gpa_scale": 4.5}, "학기가 중복"), ({"term_history": [{"term": "2026-1", "gpa": 3}], "gpa_scale": 4.5}, "{term, gpa, credits} 객체"),
    ({"residence_years": 3, "residence_years_upper": 1}, "`residence_years` 보다 작을 수 없습니다"), ({"residence_years_open": True, "residence_years_upper": 5}, "함께 보낼 수 없습니다")])
def test_each_deviation_from_the_contract_is_reported(data, needle):
    assert any(needle in m for m in viol(data)), (data, viol(data))


def test_a_fully_conforming_profile_has_no_violations_and_entry_year_depends_on_the_date():
    from datetime import date
    full = {"level": "UNIV", "status": "ENROLLED", "grade": 4, "nationality": "KR", "gpa_scale": 4.5, "gpa_last": 3.79, "residence_sido": "경기도", "residence_sigungu": "수원시",
            "special_codes": [], "language_scores": {}, "degree_held": False, "term_history": terms(3)}
    assert viol(full) == []
    assert viol({"entry_year": 2027}, today=date(2026, 10, 11)) == [] and viol({"entry_year": 2028}, today=date(2026, 10, 11))[0].startswith("profile.entry_year: 1990 ~ 2027 범위")
    assert viol({"entry_year": 1989})[0].startswith("profile.entry_year: 1990 ~")


def test_strict_mode_adds_the_report_without_changing_the_verdicts():
    rec = Recommender(Stub(five()), school())
    body = {"profile": dict(BASE, **{"GRADE": 4, "nationality": "Korean"}), "answers": {"age": "31", "degree_held": "true"}, "round": 2, "today": "2026-10-06"}
    s1, plain = R.handle_recommend(rec, body)
    s2, strict = R.handle_recommend(rec, dict(body, strict=True))
    assert s1 == s2 == 200 and "contract_violations" not in plain
    assert strict["contract_violations"] == ["profile.GRADE: 키는 `grade` 로 정확히 보내 주세요", "profile.nationality: 허용 값이 아닙니다 (KR / FOREIGN / OVERSEAS_KR). 받은 값: 'Korean'",
                                             "answers.age: 정수 형식이어야 합니다 (받은 값: str)", "answers.degree_held: true/false 형식이어야 합니다 (받은 값: str)"]
    assert {k: v for k, v in strict.items() if k != "contract_violations"} == plain and F.validate(strict) == []                                 # 판정·질문·변화는 그대로


def test_the_strict_flag_must_be_a_boolean():
    for bad in ("true", 1, None, []):
        e = R.ApiError
        with pytest.raises(e) as ei:
            R.parse_request({"profile": {}, "strict": bad})
        assert [f["field"] for f in ei.value.fields] == ["strict"]
    assert R.parse_request({"profile": {}, "strict": True}).strict is True and R.parse_request({"profile": {}}).strict is False
