# -*- coding: utf-8 -*-
"""AI 응답이 'JSON 이 아님'으로 실패하는 원인에 대한 대응 검증: 응답 잘림(max_tokens), 파싱 허용 범위, 실패 시 원문 보관."""
import json
import logging
from datetime import date

import pytest

from scholarship.common.ai.client import AIResponse
from scholarship.common.ai.prompt import Section, build_payload
from scholarship.common.ai.runner import parse_json, run_extraction
from tests.test_pipeline import GOOD, FakeAdapter, FakeAI, make_pipe, notice

GOOD_TEXT = json.dumps(GOOD, ensure_ascii=False)


def payload():
    return build_payload(date(2026, 10, 6), "제목", date(2026, 9, 4), "팀", "본문", [Section("첨부1", "요강", "내용")],
                         [], [], 20000, 60000)


# ------------------------------------------------------------------ parse_json 허용 범위
@pytest.mark.parametrize("text", [
    GOOD_TEXT,
    "```json\n" + GOOD_TEXT + "\n```",
    "다음은 결과입니다.\n" + GOOD_TEXT + "\n이상입니다.",                                   # 앞뒤 설명문
    GOOD_TEXT + "\n\n참고: {예시 아님} 입니다.",                                           # 객체 뒤의 잔여 텍스트/중괄호
    '{"category": "OUT", "summary": "한 줄\n바꿈이 문자열 안에 있음", "apply_end": "2026-10-18",}',   # 문자열 안 줄바꿈 + 끝 쉼표
    '설명 {"x": 1} 그리고 ' + GOOD_TEXT,                                                  # 다른 객체가 먼저 나와도 스키마 객체 선택
])
def test_parse_json_tolerates_common_model_habits(text):
    obj = parse_json(text)
    assert obj is not None and obj.get("category") == "OUT"


@pytest.mark.parametrize("text", ["", "JSON 아님", "[1, 2, 3]", '{"category": "OUT", "gpa": {"min": 3',     # 잘려서 닫히지 않은 JSON
                                  '```json\n{"category": "OUT", "summary": "중간에 끊김'])
def test_parse_json_rejects_truncated_or_non_objects(text):
    assert parse_json(text) is None                                                       # 잘린 JSON 은 복구하지 않음 (일부 값만 있는 결과를 만들지 않기 위해)


# ------------------------------------------------------------------ 응답 잘림: 한도를 올려 재시도
def truncated(text='{"category": "OUT", "summary": "끊'):
    return AIResponse(text=text, model="m", input_tokens=500, output_tokens=4000, stop_reason="max_tokens", truncated=True)


def good():
    return AIResponse(text=GOOD_TEXT, model="m", input_tokens=500, output_tokens=900, stop_reason="end_turn")


def test_truncated_response_retries_with_doubled_limit():
    ai = FakeAI(truncated(), good(), max_tokens=4000)
    res = run_extraction(ai, payload(), date(2026, 9, 4))
    assert res.ok and res.attempts == 2 and ai.max_tokens_seen == [None, 8000]            # 4000 -> 8000
    assert (res.input_tokens, res.output_tokens) == (1000, 4900)                           # 두 호출의 토큰이 합산됨


def test_truncated_repeatedly_stops_at_limit_with_clear_error():
    ai = FakeAI(truncated(), truncated(), truncated(), max_tokens=4000)
    res = run_extraction(ai, payload(), date(2026, 9, 4), tokens_limit=8000)
    assert not res.ok and ai.max_tokens_seen == [None, 8000]                               # 8000 이 상한이면 더 시도하지 않음
    assert "출력 한도" in res.error and "8000" in res.error and res.stop_reason == "max_tokens"
    assert res.raw_text.startswith('{"category"')                                          # 원문 보관


def test_truncated_response_is_never_parsed_as_success():
    cut_but_valid = AIResponse(text=GOOD_TEXT, stop_reason="max_tokens", truncated=True)  # 우연히 유효해 보여도 잘린 응답은 신뢰하지 않음
    ai = FakeAI(cut_but_valid, good(), max_tokens=4000)
    res = run_extraction(ai, payload(), date(2026, 9, 4))
    assert res.ok and res.attempts == 2


# ------------------------------------------------------------------ JSON 이 아닌 응답: 기존처럼 총 2회
def test_non_json_retries_once_then_gives_up_and_keeps_raw():
    ai = FakeAI("죄송하지만 이 공고는 분석할 수 없습니다.")
    res = run_extraction(ai, payload(), date(2026, 9, 4))
    assert not res.ok and len(ai.calls) == 2 and "JSON 이 아님" in res.error
    assert res.raw_text.startswith("죄송") and "JSON 객체 하나만" in ai.calls[1][0]


def test_non_json_then_success_on_reminder():
    ai = FakeAI("설명만 있음", GOOD_TEXT)
    res = run_extraction(ai, payload(), date(2026, 9, 4))
    assert res.ok and len(ai.calls) == 2


# ------------------------------------------------------------------ 파이프라인: 실패 원인 분석 자료 보관
def test_pipeline_keeps_raw_response_info_when_extraction_fails(settings, fixture_dir, tmp_path, caplog):
    raw = '{"category": "OUT", ' + '"summary": "' + "가" * 900 + '끝에서 끊김'
    ai = FakeAI(truncated(raw), truncated(raw), max_tokens=4000)
    n = notice(fixture_dir, "text")
    pipe, repo = make_pipe(settings, FakeAdapter([n]), ai, dump_dir=tmp_path / "dump")
    settings.ai_max_tokens_limit = 8000
    with caplog.at_level(logging.WARNING):
        s = pipe.run()
    rec = repo.notices[("EXT01", "1001")]
    info = json.loads(rec["uncertain_json"])
    assert rec["status_cd"] == "FAILED" and s.failed == 1
    assert "출력 한도" in info["error"] and info["stop_reason"] == "max_tokens" and info["attempts"] == 2
    assert info["raw_length"] == len(raw) and info["raw_head"] == raw[:600] and info["raw_tail"] == raw[-400:]
    dumped = (tmp_path / "dump" / "ext01_1001_ai_raw.txt").read_text(encoding="utf-8")
    assert "중단 사유: max_tokens" in dumped and dumped.endswith("끝에서 끊김")           # 전체 원문 파일
    assert "응답 잘림" in caplog.text


# ------------------------------------------------------------------ 설정
def test_ai_max_tokens_from_env(monkeypatch):
    from scholarship.common.config import Settings
    s0 = Settings()
    assert (s0.ai_max_tokens, s0.ai_max_tokens_limit, s0.ai_timeout) == (16000, 24000, 300)
    monkeypatch.setenv("AI_MAX_TOKENS", "12000")
    s = Settings.from_env()
    assert s.ai_max_tokens == 12000 and s.ai_max_tokens_limit == 24000
    monkeypatch.setenv("AI_MAX_TOKENS", "30000")
    s = Settings.from_env()
    assert s.ai_max_tokens_limit >= s.ai_max_tokens                                       # 상한이 기본값보다 작아지지 않게


def test_raw_response_is_dumped_on_success_too(settings, fixture_dir, tmp_path):
    """dump-dir 사용 시 성공한 호출의 AI 원문(코드가 빈 항목을 채우기 전)도 보관한다 - 출력 크기/형식 점검용."""
    ai = FakeAI(AIResponse(text=GOOD_TEXT, model="m", input_tokens=10, output_tokens=777, stop_reason="end_turn"))
    pipe, repo = make_pipe(settings, FakeAdapter([notice(fixture_dir, "text")]), ai, dump_dir=tmp_path / "dump")
    pipe.run()
    raw = (tmp_path / "dump" / "ext01_1001_ai_raw.txt").read_text(encoding="utf-8")
    assert raw.startswith("# 성공 | 중단 사유: end_turn") and "출력 토큰(누적) 777" in raw and GOOD_TEXT in raw
    assert (tmp_path / "dump" / "ext01_1001_ai_result.json").exists()
