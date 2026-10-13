# -*- coding: utf-8 -*-
"""6-A API 계약: 요청 검증, 프로필 관대한 검증, 필드 계약, OpenAPI 명세, 문서, 연동 시험용 샘플."""
import copy
import json
from dataclasses import fields as dataclass_fields
from pathlib import Path

import pytest

from scholarship.common.config import Settings
from scholarship.common.registry import load_registry
from scholarship.recommend import api_spec as A
from scholarship.recommend import profile_contract as PC
from scholarship.recommend import request as R
from scholarship.recommend import result_format as F
from scholarship.recommend import samples as S
from scholarship.recommend.profile import ProfileError, StudentProfile, build_profile, is_sensitive_key
from scholarship.recommend.service import Recommender
from tests.test_followup_service import BASE, five, school
from tests.test_recommend_service import Stub

ROOT = Path(A.__file__).resolve().parents[2]
REG = load_registry()


def err_fields(body):
    with pytest.raises(R.ApiError) as ei:
        R.parse_request(body)
    return ei.value


# ================================================================== 요청 구조 검증
def test_a_minimal_request_gets_defaults():
    p = R.parse_request({"profile": {}})
    assert (p.profile, p.request_id, p.answers, p.answers_text, p.declined, p.round_no, p.max_questions, p.today, p.warnings) == ({}, None, {}, None, [], 1, 5, None, [])


def test_every_field_is_read_and_limits_are_inclusive():
    p = R.parse_request({"request_id": "a-1_B.2", "profile": {"age": 20}, "answers": {"age": 21}, "answers_text": "x" * 5000, "declined": ["AGE", "AGE", "RESP"], "round": 10,
                         "max_questions": 10, "today": "2026-10-11"})
    assert p.request_id == "a-1_B.2" and p.answers == {"age": 21} and len(p.answers_text) == 5000 and p.declined == ["AGE", "RESP"] and (p.round_no, p.max_questions) == (10, 10)
    assert p.today.isoformat() == "2026-10-11"


@pytest.mark.parametrize("body", [None, [], "profile", 5, [{"profile": {}}]])
def test_a_body_that_is_not_an_object_is_rejected(body):
    e = err_fields(body)
    assert (e.code, e.status, e.fields) == ("INVALID_REQUEST", 400, [{"field": "$", "message": "객체가 아닙니다"}])


@pytest.mark.parametrize("body, field", [
    ({}, "profile"), ({"profile": []}, "profile"), ({"profile": "x"}, "profile"), ({"profile": None}, "profile"), ({"profile": {str(i): i for i in range(101)}}, "profile"),
    ({"profile": {}, "request_id": ""}, "request_id"), ({"profile": {}, "request_id": "a b"}, "request_id"), ({"profile": {}, "request_id": "가나다"}, "request_id"),
    ({"profile": {}, "request_id": "x" * 65}, "request_id"), ({"profile": {}, "request_id": 5}, "request_id"),
    ({"profile": {}, "answers": []}, "answers"), ({"profile": {}, "answers": {str(i): i for i in range(101)}}, "answers"),
    ({"profile": {}, "answers_text": 5}, "answers_text"), ({"profile": {}, "answers_text": "x" * 5001}, "answers_text"),
    ({"profile": {}, "declined": "AGE"}, "declined"), ({"profile": {}, "declined": [1]}, "declined"), ({"profile": {}, "declined": [""]}, "declined"),
    ({"profile": {}, "declined": ["x" * 25]}, "declined"), ({"profile": {}, "declined": ["A"] * 61}, "declined"),
    ({"profile": {}, "round": 0}, "round"), ({"profile": {}, "round": 11}, "round"), ({"profile": {}, "round": True}, "round"), ({"profile": {}, "round": "1"}, "round"), ({"profile": {}, "round": 1.5}, "round"),
    ({"profile": {}, "max_questions": 0}, "max_questions"), ({"profile": {}, "max_questions": 11}, "max_questions"), ({"profile": {}, "max_questions": None}, "max_questions"),
    ({"profile": {}, "today": "10/11/2026"}, "today"), ({"profile": {}, "today": "2026-02-30"}, "today"), ({"profile": {}, "today": 20261011}, "today")])
def test_each_invalid_field_is_named(body, field):
    e = err_fields(body)
    assert e.code == "INVALID_REQUEST" and [f["field"] for f in e.fields] == [field] and e.fields[0]["message"] and e.message == "요청 형식이 올바르지 않습니다 (1개 항목)"


def test_all_problems_are_reported_at_once():
    e = err_fields({"profile": 5, "round": 0, "today": "x", "declined": 3})
    assert [f["field"] for f in e.fields] == ["profile", "declined", "round", "today"] and e.message == "요청 형식이 올바르지 않습니다 (4개 항목)"


def test_unknown_top_level_keys_are_ignored_with_a_warning():
    p = R.parse_request({"profile": {}, "zeta": 1, "alpha": 2})
    assert p.warnings == ["알 수 없는 요청 항목은 무시합니다: alpha, zeta"]


def test_the_error_body_shape_and_status_codes():
    e = R.ApiError("INVALID_REQUEST", "m", [{"field": "round", "message": "x"}])
    assert e.body("r1") == {"error": {"code": "INVALID_REQUEST", "message": "m", "fields": [{"field": "round", "message": "x"}], "request_id": "r1"}} and e.body()["error"]["request_id"] is None
    assert R.ERROR_CODES == {"INVALID_REQUEST": 400, "INVALID_PROFILE": 400, "UNAUTHORIZED": 401, "PAYLOAD_TOO_LARGE": 413, "SERVER_CONFIG": 500, "INTERNAL": 500, "UNAVAILABLE": 503}


# ================================================================== 프로필: 틀린 키만 빼고 판정
def test_a_valid_profile_passes_unchanged():
    clean, warnings = R.sanitize_profile(dict(BASE, age=24), 4.5)
    assert clean == dict(BASE, age=24) and warnings == [] and clean is not BASE


def test_only_the_wrong_keys_are_dropped_and_reported_in_order():
    clean, warnings = R.sanitize_profile({"age": "abc", "grade": 4, "income_bracket": 99, "level": "UNIV"}, 4.5)
    assert clean == {"grade": 4, "level": "UNIV"} and [w.split(":")[0] for w in warnings] == ["profile.age", "profile.income_bracket"] and all(len(w) > 15 for w in warnings)


def test_keys_that_are_each_valid_but_contradict_each_other_are_rejected():
    with pytest.raises(R.ApiError) as ei:
        R.sanitize_profile({"gpa_last": 4.4, "gpa_scale": 4.0}, 4.5)
    assert ei.value.code == "INVALID_PROFILE" and ei.value.fields == [{"field": "profile", "message": "gpa_last: 0~4.0 범위를 벗어남 (4.4)"}]
    assert R.sanitize_profile({"gpa_last": 4.4, "gpa_scale": 4.5}, 4.5)[1] == []


def test_unknown_keys_are_warned_but_personal_keys_are_left_to_profile_notes():
    clean, warnings = R.sanitize_profile({"grade": 4, "새로운항목": 1, "이름": "홍길동"}, 4.5)
    assert warnings == ["profile.새로운항목: 알 수 없는 항목이라 무시했습니다"] and "이름" in clean                      # 개인정보 키는 build_profile 이 받지 않고 결과의 profile_notes 로 알린다


# ================================================================== 처리 함수
def rec():
    return Recommender(Stub(five()), school())


def call(body, recommender=None):
    return R.handle_recommend(recommender or rec(), body)


def test_a_valid_request_returns_200_with_the_request_id_and_a_valid_result():
    status, body = call({"request_id": "r-1", "profile": BASE, "today": "2026-10-06"})
    assert status == 200 and body["request_id"] == "r-1" and body["profile_warnings"] == [] and body["follow_up"]["questions"][0]["code"] == "RESP" and F.validate(body) == []


def test_a_wrong_profile_value_still_gets_a_verdict_with_a_warning():
    status, body = call({"profile": dict(BASE, age="abc"), "today": "2026-10-06"})
    assert status == 200 and [w.split(":")[0] for w in body["profile_warnings"]] == ["profile.age"] and "AGE" in [q["code"] for q in body["follow_up"]["questions"]]
    assert body["request_id"] is None and F.validate(body) == []


def test_unknown_request_keys_are_warned_in_the_response():
    status, body = call({"profile": BASE, "today": "2026-10-06", "zeta": 1})
    assert status == 200 and body["profile_warnings"] == ["알 수 없는 요청 항목은 무시합니다: zeta"]


def test_an_invalid_request_gets_400_and_echoes_a_valid_request_id():
    status, body = call({"request_id": "r-9", "profile": BASE, "round": 0})
    assert status == 400 and body["error"]["code"] == "INVALID_REQUEST" and body["error"]["fields"][0]["field"] == "round" and body["error"]["request_id"] == "r-9"
    assert call({"profile": BASE, "round": 0})[1]["error"]["request_id"] is None
    assert call({"request_id": "bad id", "profile": BASE})[1]["error"]["request_id"] is None                        # 형식이 틀린 요청 번호는 돌려주지 않는다
    assert call("not an object")[0] == 400


def test_a_contradictory_profile_gets_400_invalid_profile():
    status, body = call({"request_id": "r-2", "profile": {"gpa_last": 4.4, "gpa_scale": 4.0}})
    assert status == 400 and body["error"]["code"] == "INVALID_PROFILE" and body["error"]["request_id"] == "r-2"


def test_a_server_configuration_error_is_a_500_without_details():
    bad = Settings()
    bad.recommend_school_type = "없는종류"
    status, body = call({"request_id": "r-3", "profile": BASE}, Recommender(Stub(five()), bad))
    assert status == 500 and body["error"]["code"] == "SERVER_CONFIG" and "없는종류" not in json.dumps(body, ensure_ascii=False) and body["error"]["request_id"] == "r-3"


def test_personal_keys_are_reported_but_never_echoed_back():
    status, body = call({"profile": dict(BASE, **{"이름": "홍길동", "학번": "2021123456"}), "today": "2026-10-06"})
    assert status == 200 and body["profile_notes"]["dropped_sensitive_keys"] == ["이름", "학번"]
    text = json.dumps(body, ensure_ascii=False)
    assert "홍길동" not in text and "2021123456" not in text


def test_the_request_body_is_never_modified():
    body = {"request_id": "r", "profile": dict(BASE, age="abc"), "answers": {"age": 20}, "declined": ["SPECIAL"], "round": 2, "today": "2026-10-06"}
    before = copy.deepcopy(body)
    call(body)
    assert body == before


def test_answers_with_a_wrong_value_are_reported_in_the_answer_warnings_not_as_an_error():
    status, body = call({"profile": BASE, "answers": {"age": "abc", "parent_residence_sido": "서울특별시", "parent_residence_sigungu": "서초구"}, "round": 2, "today": "2026-10-06"})
    assert status == 200 and len(body["answers"]["warnings"]) == 1 and body["answers"]["warnings"][0].startswith("age:") and body["profile_warnings"] == []


# ================================================================== 필드 계약
def contract():
    return {f.key: f for f in PC.fields()}


def registry_keys():
    return {k for it in REG.enabled() if it.kind == "student" for k in (it.profile_field or "").split("/") if k}


def test_the_contract_covers_every_registry_key_plus_the_documented_companions():
    keys = set(contract())
    assert registry_keys() <= keys and keys - registry_keys() == set(PC.COMPANIONS)
    assert keys == set(PC.FORMAT) == set(PC.ORDER) and len(PC.ORDER) == len(set(PC.ORDER))
    assert keys <= {f.name for f in dataclass_fields(StudentProfile)}


def test_registry_derived_rows_carry_source_tier_and_sensitivity():
    c = contract()
    assert (c["gpa_last"].source, c["gpa_last"].tier, c["gpa_last"].code) == ("school_db", "필수", "GL") and (c["residence_sido"].source, c["residence_sido"].tier) == ("student", "필수")
    assert (c["income_bracket"].source, c["income_bracket"].sensitive) == ("school_db", True) and (c["special_codes"].code, c["special_codes"].sensitive) == ("SPECIAL", True)
    assert (c["parent_residence_sido"].code, c["parent_residence_sido"].tier) == ("RESP", "권장") and c["language_scores"].tier == "선택"
    assert all(c[k].source == "" and c[k].tier == "" for k in PC.COMPANIONS)
    assert {k for k, f in c.items() if f.sensitive} == {"income_bracket", "special_codes", "is_basic_livelihood", "is_near_poor", "is_single_parent"}


@pytest.mark.parametrize("key", list(PC.ORDER))
def test_every_documented_example_is_accepted_and_actually_used(key):
    f = contract()[key]
    built = build_profile({key: f.example}, 4.5)                                                                    # 서버는 항상 기본 만점(4.5)을 넘긴다
    assert key not in built.ignored and key not in built.dropped_sensitive
    assert getattr(built.profile, key) is not None


def test_every_documented_enum_value_is_accepted_as_written():
    for f in PC.fields():
        for v in f.enum:
            given = [v] if f.type == "array" else v                                                                  # special_codes 는 코드 목록
            assert getattr(build_profile({f.key: given}, 4.5).profile, f.key) == given, (f.key, v)
    for bad in ({"status": "XYZ"}, {"level": "UNIVERSITY!"}, {"gender": "?"}, {"admission_type": "OLD"}, {"major_group": "ROBOTICS"}, {"gpa_scale": 7}):
        with pytest.raises(ProfileError):
            build_profile(bad, None)


def contract_doc():
    return (ROOT / "docs" / "api_contract_v1.md").read_text(encoding="utf-8")


def test_the_contract_document_field_table_is_generated_from_the_code():
    assert PC.sync_doc(ROOT / "docs" / "api_contract_v1.md") is True, "python -m scholarship.recommend.profile_contract --write-docs"
    table = PC.markdown_table().splitlines()
    assert len(table) == len(PC.fields()) + 2 and all("`{}`".format(f.key) in contract_doc() for f in PC.fields())
    assert contract_doc().count(PC.BEGIN) == 1 and contract_doc().count(PC.END) == 1


def test_the_contract_document_mentions_every_request_field_error_code_and_rule():
    text = contract_doc()
    for k in R.KNOWN_KEYS:
        assert "`{}`".format(k) in text, k
    for code in R.ERROR_CODES:
        assert "`{}`".format(code) in text, code
    for word in ("X-API-Key", "profile_warnings", "gpa_scale", "dropped_sensitive_keys", "저장하지 않는다", "/v1/recommend", "/v1/health", "/v1/meta", "api_samples.json"):
        assert word in text, word


# ================================================================== OpenAPI
def spec():
    return A.openapi()


def test_the_openapi_file_is_in_sync_with_the_code():
    assert (ROOT / "docs" / "openapi_v1.json").read_text(encoding="utf-8") == A.openapi_json(), "python -m scholarship.recommend.api_spec --write"


def collect_refs(o, out):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "$ref":
                out.add(v)
            else:
                collect_refs(v, out)
    elif isinstance(o, list):
        for x in o:
            collect_refs(x, out)
    return out


def test_openapi_structure_security_and_references():
    s = spec()
    assert s["openapi"] == "3.1.0" and s["components"]["securitySchemes"]["ApiKey"] == {"type": "apiKey", "in": "header", "name": "X-API-Key"} and s["security"] == [{"ApiKey": []}]
    assert set(s["paths"]) == {"/v1/recommend", "/v1/health", "/v1/meta"}
    assert set(s["paths"]["/v1/recommend"]["post"]["responses"]) == {"200", "400", "401", "413", "500", "503"}
    ops = [op["operationId"] for p in s["paths"].values() for op in p.values()]
    assert sorted(ops) == ["health", "meta", "recommend"]
    refs = collect_refs(s, set())
    assert refs and all(r.startswith("#/components/schemas/") and r.rsplit("/", 1)[-1] in s["components"]["schemas"] for r in refs), sorted(refs)
    assert "$defs" not in json.dumps(s)


def test_openapi_request_schema_matches_the_code_limits_and_the_field_contract():
    s = spec()["components"]["schemas"]
    req = s["RecommendRequest"]
    assert req["required"] == ["profile"] and set(req["properties"]) == set(R.KNOWN_KEYS)
    assert req["properties"]["answers_text"]["maxLength"] == R.MAX_TEXT and req["properties"]["declined"]["maxItems"] == R.MAX_DECLINED and req["properties"]["round"]["maximum"] == R.MAX_ROUND
    assert req["properties"]["max_questions"]["maximum"] == R.MAX_QUESTIONS_LIMIT and req["properties"]["max_questions"]["default"] == 5
    assert req["properties"]["request_id"]["pattern"] == R.REQUEST_ID.pattern
    prof = s["Profile"]
    assert set(prof["properties"]) == set(PC.ORDER) and prof["additionalProperties"] is True
    assert prof["properties"]["gpa_scale"]["enum"] == [4.5, 4.3, 4.0, 5.0] and prof["properties"]["income_bracket"]["x-sensitive"] is True and prof["properties"]["gpa_last"]["x-source"] == "school_db"
    assert all("description" in p and "example" in p and p["type"] in {"string", "integer", "number", "boolean", "array", "object"} for p in prof["properties"].values())
    assert s["Error"]["properties"]["error"]["properties"]["code"]["enum"] == sorted(R.ERROR_CODES)


def test_openapi_response_schema_is_the_result_format():
    s = spec()["components"]["schemas"]
    assert set(F.DEFS) <= set(s) and s["RecommendResponse"]["required"] == F.STEP_REQUIRED
    assert {"request_id", "profile_warnings", "follow_up", "changes"} <= set(s["RecommendResponse"]["properties"])
    assert s["RecommendResponse"]["properties"]["result_version"] == {"const": 1}
    _, body = call({"profile": BASE, "today": "2026-10-06"})
    assert F.validate(body) == [] and {"request_id", "profile_warnings"} <= set(body)


# ================================================================== 연동 시험용 샘플
def samples_file():
    return json.loads((ROOT / "docs" / "samples" / "api_samples.json").read_text(encoding="utf-8"))


def test_the_sample_file_is_in_sync_with_the_code():
    assert samples_file() == json.loads(S.samples_json()), "python -m scholarship.recommend.samples --write"


def test_sample_requests_follow_the_contract_and_never_carry_personal_or_student_answered_values():
    d = samples_file()
    assert d["meta"]["notices"] == 20 and len(d["students"]) == 30 and [s["name"] for s in d["students"]] == ["s%02d" % i for i in range(1, 31)]
    answered_later = {"parent_residence_sido", "parent_residence_sigungu", "hs_sido", "hs_sigungu", "age", "special_codes", "special_complete", "residence_years", "residence_years_upper",
                      "residence_years_open", "language_scores", "degree_held", "national_scholarship_applied", "is_basic_livelihood", "is_near_poor", "is_single_parent"}
    for s in d["students"]:
        req = R.parse_request(s["first_request"])
        assert req.warnings == [] and req.request_id == s["name"] and set(req.profile) <= set(PC.ORDER) and not (set(req.profile) & answered_later), s["name"]
        assert not [k for k in req.profile if is_sensitive_key(k)], s["name"]                                      # 이름·학번 같은 개인정보 키는 없다
        assert build_profile(s["full_profile"], None).profile is not None
        assert R.sanitize_profile(req.profile, 4.5)[1] == [], s["name"]


def test_replaying_the_sample_requests_reproduces_the_expected_results():
    d = samples_file()
    r = Recommender(S.NoticeSource(S.X.load_notices()), S.settings())
    for s in d["students"]:
        status, body = R.handle_recommend(r, s["first_request"])
        assert status == 200 and F.validate(body) == [], s["name"]
        assert {k: body["counts"][k] for k in ("match", "conditional", "no_match")} == s["expected_first"]["counts"], s["name"]
        assert [q["code"] for q in body["follow_up"]["questions"]] == s["expected_first"]["question_codes"], s["name"]
        fin = s["expected_final"]
        assert sum(fin["counts"].values()) == 20 and 1 <= fin["rounds"] <= 3 and fin["counts"]["conditional"] <= s["expected_first"]["counts"]["conditional"], s["name"]
