# -*- coding: utf-8 -*-
"""5-D 결과 JSON 형식 v1: 스키마·검증기·문서의 일치, 학생이 질문에 답하며 이어가는 전 과정 시뮬레이션."""
import json
import re
from datetime import date
from pathlib import Path

import pytest

from scholarship.common.config import Settings
from scholarship.common.registry import load_registry
from scholarship.recommend import matrix as X
from scholarship.recommend import result_format as F
from scholarship.recommend import service
from scholarship.recommend.followup import MAX_QUESTIONS, MAX_ROUNDS
from scholarship.recommend.service import Recommender
from scholarship.recommend.student_text import parse_student_text

ROOT = Path(F.__file__).resolve().parents[2]
REG = load_registry()


# ================================================================== 작은 검증기
def errs(value, schema):
    out = []
    F._check(value, schema, {"$defs": F.DEFS}, "$", out)
    return out


def test_validator_types_enums_constants_and_requirements():
    assert errs("a", {"type": "string"}) == [] and errs(1, {"type": "string"}) == ["$: string 형식이 아닙니다 (실제: int)"]
    assert errs(True, {"type": "integer"}) != [] and errs(1, {"type": "integer"}) == [] and errs(1.5, {"type": "number"}) == [] and errs(True, {"type": "number"}) != []   # bool 은 정수가 아니다
    assert errs(None, {"type": ["string", "null"]}) == [] and errs(None, {"type": "string"}) != []
    assert errs("x", {"enum": ["a", "b"]}) == ["$: 'x' 는 허용 값이 아닙니다 ('a', 'b')"] and errs(None, {"enum": ["a", None]}) == []
    assert errs(2, {"const": 1}) == ["$: 2 (필요: 1)"] and errs(1, {"const": 1}) == []
    obj = {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}, "b": {"type": "array", "items": {"type": "string"}}}}
    assert errs({"a": 1}, obj) == [] and errs({}, obj) == ["$.a: 필수 항목이 없습니다"] and errs({"a": 1, "extra": 5}, obj) == []                       # 모르는 필드는 허용
    assert errs({"a": 1, "b": ["x", 2]}, obj) == ["$.b[1]: string 형식이 아닙니다 (실제: int)"]
    assert errs([1], {"type": "object"}) == ["$: object 형식이 아닙니다 (실제: list)"]


def test_validator_follows_references():
    assert errs({"name": "a", "reason": "b"}, {"$ref": "#/$defs/unmatched_variant"}) == [] and errs({"name": "a"}, {"$ref": "#/$defs/unmatched_variant"}) == ["$.reason: 필수 항목이 없습니다"]


# ================================================================== 스키마 파일과 문서
def test_schema_file_is_in_sync_with_the_code_and_every_reference_resolves():
    on_disk = (ROOT / "docs" / "result_v1.schema.json").read_text(encoding="utf-8")
    assert on_disk == F.schema_json(), "docs/result_v1.schema.json 이 코드와 다르다: python -m scholarship.recommend.result_format --write"
    doc = json.loads(on_disk)
    assert doc["$schema"].endswith("draft-07/schema#") and doc["properties"]["result_version"] == {"const": 1} and service.RESULT_VERSION == F.RESULT_VERSION == 1
    refs = set(re.findall(r'"\$ref": "#/\$defs/([a-z_]+)"', on_disk))
    assert refs and refs <= set(doc["$defs"]) and set(doc["$defs"]) - refs <= {"item", "excluded", "follow_up", "change", "change_summary", "answers"}      # 정의가 쓰이거나 최상위에서 참조된다
    base = F.schema(step=False)
    assert "follow_up" not in base["properties"] and set(F.schema(step=True)["required"]) - set(base["required"]) == {"round", "answers", "follow_up", "changes", "change_summary"}


def doc_text():
    return (ROOT / "docs" / "result_format_v1.md").read_text(encoding="utf-8")


def test_the_document_mentions_every_field_of_the_format():
    text = doc_text()
    missing = []
    groups = {"최상위": list(F.STEP_PROPS), "공고 항목": list(F.DEFS["item"]["properties"]), "유형": list(F.DEFS["variant"]["properties"]), "질문": list(F.DEFS["question"]["properties"]),
              "답변 형식": list(F.DEFS["question"]["properties"]["answer"]["properties"]), "후속 질문": list(F.DEFS["follow_up"]["properties"]), "변화": list(F.DEFS["change"]["properties"]),
              "변화 요약": list(F.DEFS["change_summary"]["properties"]), "답변 결과": list(F.DEFS["answers"]["properties"]), "미달": list(F.DEFS["excluded"]["properties"]),
              "선행 조건": list(F.DEFS["prerequisite"]["properties"]), "학교 사업": list(F.DEFS["school_program"]["properties"])}
    for name, keys in groups.items():
        for k in keys:
            if "`{}`".format(k) not in text:
                missing.append("{}: {}".format(name, k))
    assert missing == [], "문서에 설명이 없는 필드: {}".format(missing)
    for word in ("MATCH", "CONDITIONAL", "NO_MATCH", "NO_QUESTIONS", "ALL_DECLINED", "MAX_ROUNDS", "gpa_scale", "PARTICIPATES", "TODO", "UNMET"):
        assert word in text, word


def test_every_enum_value_in_the_schema_is_explained_in_the_document():
    text = doc_text()
    for enum in (F.VERDICTS, F.VERDICT_LABELS, F.DONE_REASONS, F.DEFS["prerequisite"]["properties"]["status"]["enum"], F.DEFS["school_program"]["properties"]["school"]["enum"]):
        for v in enum:
            assert v in text, v


# ================================================================== 입력 계약: 항목 사전에 없는 동반 키
COMPANION_KEYS = {"gpa_scale": "성적 값과 함께 보내는 만점 (없으면 4.5로 가정하고 안내)", "residence_years_upper": "거주 기간 구간의 상한", "residence_years_open": "거주 기간이 'N년 이상'인지",
                  "special_complete": "신분 항목에 모두 답했는지", "special_status": "신분 항목 중 일부 응답 상태(내부)"}


def test_every_profile_key_a_student_can_send_is_declared_by_the_registry_or_listed_as_a_companion_key():
    """포털이 보낼 키는 항목 사전의 profile_field 로 알려진다. 사전에 없는 동반 키(gpa_scale 등)는 계약서(6단계)에 따로 적어야 하므로 여기에 이유와 함께 등록한다."""
    covered = set()
    for it in REG.enabled():
        if it.kind == "student":
            covered |= {k for k in (it.profile_field or "").split("/") if k}
    seen = set()
    for st in X.load_students():
        seen |= set(parse_student_text(st.text).data)
    assert seen - covered <= set(COMPANION_KEYS), sorted(seen - covered - set(COMPANION_KEYS))
    assert set(COMPANION_KEYS) - seen - {"special_status"} == set()                                                  # 등록한 동반 키는 실제로 쓰여야 한다


# ================================================================== 시뮬레이션: 질문에 답하며 이어가는 전 과정
class NoticeSource:
    """고정한 공고 20건 (모두 마감 전)"""
    def __init__(self, notices):
        self.n = notices

    def list_candidates(self, today):
        return [{"src_cd": "EXT02", "src_id": i, "title": v["title"], "origin_url": v.get("url"), "category": "OUT", "elig": v["elig"], "status": None,
                 "apply_end": date(2026, 12, 31), "apply_start": None, "org_deadline": None, "summary": None} for i, v in self.n.items()]


def keys_of(item):
    return [k for k in (item.profile_field or "").split("/") if k]


# 첫 호출에 포털이 보내는 값: 학교 DB 항목(성적 만점 포함) + 본인 주민등록
FIRST_KEYS = {"gpa_scale"}
for _it in REG.enabled():
    if _it.kind == "student" and (_it.source == "school_db" or _it.code == "RES"):
        FIRST_KEYS |= set(keys_of(_it))


def answer_keys(code):
    if code == "SPECIAL":
        return ["special_codes", "special_complete", "is_basic_livelihood", "is_near_poor", "is_single_parent"]
    if code == "RESY":
        return ["residence_years", "residence_years_upper", "residence_years_open"]
    return keys_of(REG.get(code))


def settings_for(name):
    sc = X.SCHOOLS[name]
    s = Settings()
    s.recommend_school_sido, s.recommend_school_sigungu, s.recommend_school_type, s.recommend_school_programs = sc["sido"], sc["sigungu"], sc["type"], tuple(sc["programs"])
    return s


def verdicts(r):
    out = {x["id"]: x["verdict"] for x in r["recommended"] + r["conditional"]}
    out.update({x["id"]: "NO_MATCH" for x in r["excluded"]})
    return out


def converse(rec, full, today):
    """학생처럼 행동한다: 첫 호출 -> 질문마다 자기 정보로 답(정보가 없으면 건너뜀) -> 이어서 호출. 포털처럼 답을 자기 입력에 합쳐 둔다."""
    profile = {k: v for k, v in full.items() if k in FIRST_KEYS}
    declined, history = [], []
    r = rec.recommend_step(profile, round_no=1, today=today)
    history.append(r)
    for n in range(2, MAX_ROUNDS + 1):
        if r["follow_up"]["done"]:
            break
        answers = {}
        for q in r["follow_up"]["questions"]:
            have = {k: full[k] for k in answer_keys(q["code"]) if k in full}
            if have:
                answers.update(have)
            else:
                declined.append(q["code"])
        r = rec.recommend_step(profile, answers=answers, declined=declined, round_no=n, today=today)
        profile = {**profile, **answers}
        history.append(r)
    return history


@pytest.fixture(scope="module")
def simulation():
    notices, students = X.load_notices(), X.load_students()
    today, out = date(2026, 10, 11), []
    for sname in ("시흥", "성북", "유성"):
        rec = Recommender(NoticeSource(notices), settings_for(sname))
        for st in students:
            full = parse_student_text(st.text).data
            out.append((sname, st.id, converse(rec, full, today), verdicts(rec.recommend(full, today=today))))
    return out


def test_the_conversation_ends_with_the_same_verdicts_as_giving_everything_at_once(simulation):
    bad = [(s, sid, {k: (h[-1] and verdicts(h[-1])[k], full[k]) for k in full if verdicts(h[-1]).get(k) != full[k]}) for s, sid, h, full in simulation if verdicts(h[-1]) != full]
    assert bad == [], bad[:3]
    assert len(simulation) == 90


def test_answered_verdicts_never_come_back_to_life(simulation):
    for s, sid, history, _ in simulation:
        nos = [{k for k, v in verdicts(h).items() if v == "NO_MATCH"} for h in history]
        assert all(a <= b for a, b in zip(nos, nos[1:])), (s, sid)                                                    # 미달은 정보를 더 줘도 미달이다


def test_questions_respect_the_limits_order_and_never_repeat(simulation):
    for s, sid, history, _ in simulation:
        seen = []
        for h in history:
            qs = h["follow_up"]["questions"]
            assert len(qs) <= MAX_QUESTIONS and [q["sensitive"] for q in qs] == sorted(q["sensitive"] for q in qs), (s, sid)       # 민감 항목은 마지막
            assert all(REG.get(q["code"]).source == "student" for q in qs)                                             # 학교 DB 항목은 묻지 않는다
            seen += [q["code"] for q in qs]
        assert len(seen) == len(set(seen)) and len(history) <= MAX_ROUNDS, (s, sid)


def test_every_response_in_the_conversation_matches_the_format(simulation):
    bad = [(s, sid, n, F.validate(h)[:2]) for s, sid, history, _ in simulation for n, h in enumerate(history, 1) if F.validate(h)]
    assert bad == [], bad[:3]


def test_the_conversation_is_short_and_resolves_most_of_what_can_be_resolved(simulation):
    asked = [sum(len(h["follow_up"]["questions"]) for h in history) for _, _, history, _ in simulation]
    assert max(asked) <= MAX_QUESTIONS * MAX_ROUNDS and sum(asked) / len(asked) < 5                                   # 학생당 평균 5개 미만 (시뮬레이션: 4.1)
    first_conditional = sum(len(history[0]["conditional"]) for _, _, history, _ in simulation)
    last_conditional = sum(len(history[-1]["conditional"]) for _, _, history, _ in simulation)
    assert last_conditional < first_conditional * 0.8                                                                 # 조건부가 크게 줄어든다
    assert all(history[-1]["follow_up"]["done"] or len(history) == MAX_ROUNDS for _, _, history, _ in simulation)


def test_without_the_grade_scale_the_server_says_it_assumed_4_5():
    notices = X.load_notices()
    rec = Recommender(NoticeSource(notices), settings_for("시흥"))
    full = parse_student_text({s.id: s for s in X.load_students()}["s27"].text).data                                  # 4.3 만점 학교 학생
    without = {k: v for k, v in full.items() if k in FIRST_KEYS and k != "gpa_scale"}
    r = rec.recommend_step(without, today=date(2026, 10, 11))
    assert any("4.5 으로 가정" in a for a in r["profile_notes"]["assumptions"])                                       # 조용히 틀리지 않고 가정을 알린다
    with_scale = rec.recommend_step({k: v for k, v in full.items() if k in FIRST_KEYS}, today=date(2026, 10, 11))
    assert not any("4.5 으로 가정" in a for a in with_scale["profile_notes"]["assumptions"])                           # 만점을 보내면 가정하지 않는다
