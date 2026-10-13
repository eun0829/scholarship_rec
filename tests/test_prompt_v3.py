# -*- coding: utf-8 -*-
"""스키마 v3 프롬프트·파이프라인 연결: 기본값(v2)은 기존 그대로, v3 는 항목 사전에서 프롬프트를 만들고 저장 형식에 사전 정보를 남김."""
import json
import re
from datetime import date

import pytest

from scholarship.common import registry as R
from scholarship.common.ai import schema_v3
from scholarship.common.ai.client import AIResponse
from scholarship.common.ai.prompt import SYSTEM_PROMPT, Section, build_payload, get_system_prompt
from scholarship.common.ai.prompt_v3 import build_system_prompt_v3, system_prompt_v3
from scholarship.common.ai.runner import run_extraction
from scholarship.common.config import Settings
from tests.test_pipeline import GOOD, FakeAdapter, FakeAI, make_pipe, notice


def payload(version=2):
    return build_payload(date(2026, 10, 6), "제목", date(2026, 9, 4), "팀", "본문", [Section("첨부1", "요강", "내용")], [], [], 20000, 60000,
                         schema_version=version)


# ------------------------------------------------------------------ 기본값(v2)은 그대로
def test_default_payload_uses_v2_prompt():
    p = payload()
    assert p.system is SYSTEM_PROMPT and p.schema_version == 2
    assert get_system_prompt() is SYSTEM_PROMPT and get_system_prompt(2) is SYSTEM_PROMPT
    assert "[항목 사전]" not in SYSTEM_PROMPT and "unmodeled_conditions" not in SYSTEM_PROMPT


def test_v3_payload_uses_v3_prompt():
    p = payload(3)
    assert p.schema_version == 3 and p.system == system_prompt_v3() and p.system != SYSTEM_PROMPT
    assert p.user_text == payload(2).user_text                                                 # 사용자 입력(공고 내용)은 같음


# ------------------------------------------------------------------ v3 프롬프트 내용
@pytest.fixture(scope="module")
def v3():
    return system_prompt_v3()


def test_v3_prompt_keeps_v2_rules_and_adds_new_ones(v3):
    for needle in ("공고에 명시된 내용만 추출합니다", "공고에 근거가 없는 추측은 적지 않습니다", "대학생·대학원생 추천용", "빠뜨리지 말고 variants",
                   "마감이 임박했다'는 사실은 검수 사유가 아니므로", "신청 방법과 접수처를 한 줄", "문서 일부만 제공됨"):
        assert needle in v3, needle
    assert "특정 신분에게만 기준이 완화되는 경우" in v3 and "최상위 gpa 는 비웁니다" in v3                    # 완화는 유형으로 나눠 표현하도록 안내
    for needle in ("다만 규칙 25~34 의 항목", "documents(제출서류)가", "유형 이름이 대상의 국적·신분을 뜻하면", "special_groups, nationality, gender, age",
                   "요약 표기만 있는 줄은 조건이 아니므로", "선발 조건은 공고문 확인"):
        assert needle in v3, needle                                                                 # 시험 추출 5건에서 발견한 반복 문제에 대한 안내
    for needle in ("[항목 사전]", "unmodeled_conditions 에 한 건씩", "하나도 빠뜨리지 않습니다", "scale_stated", "LAST_N", "HYBRID",
                   "region.requirements", "ANY", "PREFERRED", "other_conditions 는 비워 둡니다", "4.5 로 가정"):
        assert needle in v3, needle


def test_v3_prompt_registry_section_lists_linked_items(v3):
    reg = R.load_registry()
    section = v3[v3.index("[항목 사전]"):v3.index("[v3 추가 항목")]
    for l in reg.prompt_lines():
        assert l in section, l
    assert section.count("\n- ") == len(reg.prompt_lines())


def test_v3_prompt_output_format_is_the_v3_template(v3):
    start = v3.index('\n{\n "category"')
    assert json.loads(v3[start:]) == schema_v3.TEMPLATE_V3
    assert '"variants": []' in v3 and '"unmodeled_conditions": []' in v3 and '"requirements": []' in v3


def test_prompt_and_normalizer_use_the_same_standard_values(v3):
    """프롬프트가 알려 주는 표준 값과 정규화 코드가 받아들이는 값이 어긋나면 AI 가 쓴 값이 조용히 버려진다."""
    groups = [schema_v3.REGION_TYPES, schema_v3.SPECIAL_CODES, schema_v3.MAJOR_GROUPS, schema_v3.SCHOOL_TYPES, schema_v3.SCHOOL_EXCLUDED,
              schema_v3.SCHOOL_PROGRAMS, schema_v3.PREREQ_TYPES, schema_v3.RECOMMENDERS, schema_v3.EXCLUSION_TYPES, schema_v3.UNMODELED_KINDS,
              schema_v3.TRANSFER_MODES, schema_v3.INCOME_BASIS, schema_v3.BRACKET_TERMS, schema_v3.DEADLINE_KINDS, schema_v3.SPECIAL_SCOPES,
              schema_v3.SPECIAL_MODES, schema_v3.WAIVER_STUDENTS, schema_v3.WAIVER_SKIP, ("ANY", "ALL")]
    for g in groups:
        for code in g:
            if code in ("UNKNOWN", "UNSPECIFIED"):                                             # '모름' 값은 AI 가 쓰라고 안내하지 않음
                continue
            assert code in v3, code
    for basis in ("LAST_SEMESTER", "CUMULATIVE", "EITHER", "LAST_N", "ENTRANCE"):
        assert basis in v3


def test_prompt_size_guard(v3):
    assert 8000 < len(v3) < 22000                                                              # 규칙·항목이 불어나 프롬프트가 지나치게 커지는 것을 막음


def test_prompt_follows_registry_changes():
    reg = R.load_registry()
    new = {"code": "SCHOLAR_TERMS", "label": "장학 수혜 학기 수", "kind": "student", "source": "student", "type": "int", "tier": 3,
           "question": "지금까지 장학금을 받은 학기는?", "notice_path": "unmodeled_conditions", "op": "custom", "custom": "scholar_terms",
           "description": "수혜 학기 수 한도"}
    p2 = build_system_prompt_v3(reg.with_items([new]))
    assert "- 장학 수혜 학기 수 (SCHOLAR_TERMS): 수혜 학기 수 한도" in p2 and "SCHOLAR_TERMS" not in system_prompt_v3()   # 항목 추가 -> 프롬프트 자동 확장


# ------------------------------------------------------------------ 러너: 요청의 버전으로 응답 정리
V3_JSON = dict(GOOD, region={"mode": "ANY", "requirements": [{"type": "RESIDENCE_SELF", "areas": ["춘천시"], "min_years": 1},
                                                            {"type": "RESIDENCE_PARENT", "areas": ["춘천시"], "min_years": 1}]},
               special_groups=[{"code": "BASIC_LIVELIHOOD", "mode": "PREFERRED"}], gender="F", apply_type="HYBRID",
               unmodeled_conditions=[{"text": "시장이 정한 재능나눔 활동 가능자", "section": "신청자격", "source": "본문", "kind": "ELIGIBILITY"}])


def ai_text(obj):
    return AIResponse(text=json.dumps(obj, ensure_ascii=False), model="m", input_tokens=10, output_tokens=10, stop_reason="end_turn")


def test_runner_normalizes_by_payload_version():
    r3 = run_extraction(FakeAI(ai_text(V3_JSON)), payload(3), date(2026, 9, 4))
    assert r3.ok and r3.data["region"]["mode"] == "ANY" and r3.data["gender"] == "F" and r3.data["apply_type"] == "HYBRID"
    assert r3.data["other_conditions"] == ["시장이 정한 재능나눔 활동 가능자"]
    r2 = run_extraction(FakeAI(ai_text(V3_JSON)), payload(2), date(2026, 9, 4))                 # 같은 응답이라도 v2 요청이면 v2 로 정리
    assert r2.ok and "gender" not in r2.data and r2.data["apply_type"] == "UNKNOWN" and r2.data["region"]["residence"] == []


# ------------------------------------------------------------------ 파이프라인 전체 흐름
class RecordingAI(FakeAI):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.systems = []

    def complete(self, system, user_text, images, max_tokens=None):
        self.systems.append(system)
        return super().complete(system, user_text, images, max_tokens)


def run_one(settings, fixture_dir, response):
    ai = RecordingAI(response)
    pipe, repo = make_pipe(settings, FakeAdapter([notice(fixture_dir, "text")]), ai)
    pipe.run(days=30)
    return ai, repo.notices[("EXT01", "1001")]


def test_pipeline_default_is_v2_and_unchanged(settings, fixture_dir):
    assert settings.ai_schema_version == 2
    ai, rec = run_one(settings, fixture_dir, GOOD)
    assert ai.systems == [SYSTEM_PROMPT] and rec["status_cd"] == "EXTRACTED"
    elig = json.loads(rec["elig_json"])
    assert elig["schema_version"] == 2 and "registry_version" not in elig and "processed_items" not in elig


def test_pipeline_v3_uses_v3_prompt_and_records_registry_info(settings, fixture_dir):
    settings.ai_schema_version = 3
    ai, rec = run_one(settings, fixture_dir, V3_JSON)
    assert ai.systems == [system_prompt_v3()] and rec["status_cd"] == "EXTRACTED" and rec["apply_type_cd"] == "HYBRID"
    elig = json.loads(rec["elig_json"])
    reg = R.load_registry()
    assert elig["schema_version"] == 3 and elig["registry_version"] == reg.version and elig["processed_items"] == reg.codes()
    assert "GL" in elig["processed_items"] and "RESP" in elig["processed_items"]
    assert elig["region"]["requirements"][1]["type"] == "RESIDENCE_PARENT" and elig["special_groups"][0]["mode"] == "PREFERRED"
    assert "evidence" not in elig and "uncertain" not in elig


def test_pipeline_v3_with_bad_registry_stops_before_any_ai_call(settings, fixture_dir, tmp_path):
    bad = tmp_path / "reg.json"
    bad.write_text(json.dumps({"version": 1, "items": [{"code": "x"}]}), encoding="utf-8")
    settings.ai_schema_version, settings.item_registry_file = 3, str(bad)
    ai = RecordingAI(V3_JSON)
    pipe, repo = make_pipe(settings, FakeAdapter([notice(fixture_dir, "text")]), ai)
    with pytest.raises(R.RegistryError, match="항목 사전 오류"):
        pipe.run(days=30)
    assert ai.systems == [] and repo.notices == {}                                             # AI 를 부르지 않았고(크레딧 낭비 없음) 저장도 안 함


def test_registry_is_not_needed_without_ai_or_in_v2(settings, fixture_dir, tmp_path):
    bad = tmp_path / "reg.json"
    bad.write_text("깨진 파일", encoding="utf-8")
    settings.item_registry_file = str(bad)
    pipe, repo = make_pipe(settings, FakeAdapter([notice(fixture_dir, "text")]), RecordingAI(GOOD))       # v2: 사전을 읽지 않음
    pipe.run(days=30)
    assert repo.notices[("EXT01", "1001")]["status_cd"] == "EXTRACTED"
    settings.ai_schema_version = 3
    pipe3, repo3 = make_pipe(settings, FakeAdapter([notice(fixture_dir, "text")]), None)                  # v3 라도 AI 없이 수집만 하면 읽지 않음
    pipe3.run(days=30)
    assert ("EXT01", "1001") in repo3.notices


def test_v3_scale_not_stated_is_not_a_review_reason(settings, fixture_dir):
    settings.ai_schema_version = 3
    obj = dict(GOOD, gpa={"min": 3.6, "basis": "CUMULATIVE"})
    _, rec = run_one(settings, fixture_dir, obj)
    assert rec["status_cd"] == "EXTRACTED"                                                     # 만점 미기재는 4.5 가정이라 검수 대기가 아님
    assert json.loads(rec["elig_json"])["gpa"]["scale_stated"] is False
    settings.ai_schema_version = 2
    _, rec2 = run_one(settings, fixture_dir, obj)
    assert rec2["status_cd"] == "NEED_REVIEW"                                                  # v2 는 기존대로


# ------------------------------------------------------------------ 설정
def test_schema_version_setting_from_env(monkeypatch):
    monkeypatch.delenv("AI_SCHEMA_VERSION", raising=False)
    monkeypatch.delenv("ITEM_REGISTRY_FILE", raising=False)
    s = Settings.from_env()
    assert s.ai_schema_version == 2 and s.item_registry_file == ""
    monkeypatch.setenv("AI_SCHEMA_VERSION", "3")
    monkeypatch.setenv("ITEM_REGISTRY_FILE", "x/reg.json")
    s = Settings.from_env()
    assert s.ai_schema_version == 3 and s.item_registry_file == "x/reg.json"
    monkeypatch.setenv("AI_SCHEMA_VERSION", "4")
    with pytest.raises(ValueError, match="2 또는 3"):
        Settings.from_env()


# ------------------------------------------------------------------ 프롬프트와 저장 정보는 같은 항목 사전 파일을 쓴다
def custom_registry_file(tmp_path):
    reg = R.load_registry().with_items([{"code": "SCHOLAR_TERMS", "label": "장학 수혜 학기 수", "kind": "student", "source": "student", "type": "int", "tier": 3,
                                         "question": "몇 학기?", "notice_path": "unmodeled_conditions", "op": "custom", "custom": "scholar_terms",
                                         "description": "수혜 학기 수 한도"}])
    f = tmp_path / "custom_registry.json"
    f.write_text(json.dumps({"version": 7, "items": [R._to_dict(i) for i in reg.items]}, ensure_ascii=False), encoding="utf-8")
    return str(f)


def test_prompt_uses_the_given_registry_file(tmp_path):
    f = custom_registry_file(tmp_path)
    custom, default = system_prompt_v3(f), system_prompt_v3()
    assert "SCHOLAR_TERMS" in custom and "SCHOLAR_TERMS" not in default and system_prompt_v3(f) is custom      # 파일마다 한 번만 만듦
    p = build_payload(date(2026, 10, 6), "제목", date(2026, 9, 4), "팀", "본문", [Section("첨부1", "요강", "내용")], [], [], 20000, 60000,
                      schema_version=3, registry_file=f)
    assert p.system == custom and p.schema_version == 3


def test_pipeline_prompt_and_saved_registry_info_come_from_the_same_file(settings, fixture_dir, tmp_path):
    settings.ai_schema_version, settings.item_registry_file = 3, custom_registry_file(tmp_path)
    ai, rec = run_one(settings, fixture_dir, V3_JSON)
    elig = json.loads(rec["elig_json"])
    assert "SCHOLAR_TERMS" in ai.systems[0]                                                    # 프롬프트에 지정한 사전의 항목이 들어감
    assert elig["registry_version"] == 7 and "SCHOLAR_TERMS" in elig["processed_items"]       # 저장된 사전 정보도 같은 사전


def test_v3_prompt_reads_promotion_grades_as_a_range(v3):
    """4-B2 에서 찾은 문제: '3학년 진급 재학생'을 학년 [3]으로만 읽어 현재 2학년을 탈락시켰다 -> [N-1, N] 으로 읽도록 안내."""
    rule = [l for l in v3.splitlines() if l.startswith("35. ")]
    assert len(rule) == 1
    for needle in ("진급", "grades 에 [N-1, N] 을 모두 적습니다", "'3학년 진급 재학생' -> grades [2, 3]", "범위를 좁히지 말고 넓게 적습니다"):
        assert needle in rule[0], needle
    nums = [int(l.split(".")[0]) for l in v3.splitlines() if re.match(r"^\d+\. ", l)]
    assert nums == list(range(1, 36))                                                                       # 규칙 번호가 1~35 로 빠짐없이 이어진다


def test_prompt_rule_numbers_referenced_in_the_text_still_exist(v3):
    assert "다만 규칙 25~34 의 항목" in v3 and [l for l in v3.splitlines() if l.startswith("34. ")] and [l for l in v3.splitlines() if l.startswith("25. ")]
