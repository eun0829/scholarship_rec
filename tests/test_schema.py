# -*- coding: utf-8 -*-
from datetime import date

import pytest

from scholarship.common.ai import schema
from scholarship.common.ai.runner import parse_json


def test_weekday_flags_source_typo():
    # 울산연구원 공고의 실제 오기: 9/23 은 수요일인데 (금)
    txt = "신청기간 : 2026. 9. 16.(수) 10:00 ~ 9. 23.(금) 18:00까지 / 공고기간 2026. 8. 26.(수) ~ 9. 23.(수)"
    issues = schema.weekday_issues(txt, date(2026, 8, 26))
    assert len(issues) == 1 and "9. 23.(금)" in issues[0]["reason"] and "수요일" in issues[0]["reason"]


def test_weekday_ok_and_no_year():
    assert schema.weekday_issues("2026. 10. 25.(일) 23:59", None) == []
    assert schema.weekday_issues("10. 8.(목)", None) == []          # 연도 모르면 건너뜀
    assert schema.weekday_issues("10. 8.(목)", date(2026, 9, 3)) == []


def test_normalize_happy_path():
    raw = {"category": "OUT", "apply_type": "SCHOOL", "apply_start": "2026-09-04", "apply_end": "2026-10-25",
           "org_deadline": "2026-11-06", "target_levels": ["UNIV"], "grades": [2, 3],
           "gpa": {"min": 80, "scale": 100, "basis": "EITHER"},
           "credits": {"min_last_semester": 12}, "income": {"max_bracket": 8},
           "summary": "x", "evidence": {"apply_end": "본문"}, "uncertain": ["단체 수상 기준 불명"]}
    d, issues = schema.normalize(raw, date(2026, 9, 4))
    assert issues == []
    assert d["gpa"]["scale"] == 100.0 and d["credits"]["min_last_semester"] == 12.0
    assert d["uncertain"] == [{"field": "", "reason": "단체 수상 기준 불명", "level": "verify"}]
    assert set(d) == set(schema.TEMPLATE)                    # 키 구조 보장


def test_normalize_catches_problems():
    raw = {"category": "WHAT", "apply_start": "2026-10-25", "apply_end": "2026/09/01",
           "target_levels": ["UNIV", "ALIEN"], "gpa": {"min": 3.0}}
    d, issues = schema.normalize(raw, date(2026, 9, 4))
    fields = {i["field"] for i in issues}
    assert {"category", "apply_end", "target_levels", "gpa.scale"} <= fields
    assert d["category"] == "ETC" and d["apply_end"] is None and d["target_levels"] == ["UNIV"]


def test_normalize_out_without_deadline_and_gpa_over_scale():
    d, issues = schema.normalize({"category": "OUT", "gpa": {"min": 80, "scale": 4.5}}, None)
    assert {"apply_end", "gpa.min"} <= {i["field"] for i in issues}


def test_normalize_rejects_non_object():
    with pytest.raises(ValueError):
        schema.normalize(["x"])


def test_parse_json_variants():
    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('설명입니다 {"a": {"b": 2}} 끝') == {"a": {"b": 2}}
    assert parse_json("JSON 아님") is None
    assert parse_json("[1,2]") is None


def test_uncertain_level_and_other_conditions():
    raw = {"category": "IN", "other_conditions": ["TOEIC 850 이상", " "], "uncertain": [
        {"field": "gpa", "reason": "표가 깨짐", "level": "verify"},
        {"field": "apply_type", "reason": "직접 신청으로 분류", "level": "note"},
        {"field": "income", "reason": "표시 없음"},                       # level 없음 -> verify
        {"field": "x", "reason": "이상한 값", "level": "urgent"}]}        # 모르는 level -> verify
    d, _ = schema.normalize(raw, None)
    assert d["other_conditions"] == ["TOEIC 850 이상"]
    assert [u["level"] for u in d["uncertain"]] == ["verify", "note", "verify", "verify"]
    assert "other_conditions" in schema.TEMPLATE


def test_prompt_mentions_new_rules():
    from scholarship.common.ai.prompt import SYSTEM_PROMPT
    for needle in ("other_conditions", '"note"', '"verify"', "또는' 관계", "앞부분만 전달되었다는 사실은 적지 않습니다"):
        assert needle in SYSTEM_PROMPT
