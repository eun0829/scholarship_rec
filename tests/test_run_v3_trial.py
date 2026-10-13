# -*- coding: utf-8 -*-
"""run_v3_trial.py: 크레딧이 드는 실행의 안전장치(이미 한 공고는 건너뜀, dry-run 은 AI 를 부르지 않음, 키 없으면 호출 전에 중단)와 결과 파일."""
import json
import re
import shutil
from pathlib import Path

import pytest

import run_v3_trial
from scholarship.common import registry as R
from scholarship.common.ai.client import AIClient, AIResponse
from scholarship.common.ai.prompt_v3 import system_prompt_v3
from tests.test_ext02_trial import FIX, IDEAL

TITLES = {"문주장학재단": "9212", "사랑의 날개": "9098", "거창군": "9096"}


class FakeClient(AIClient):
    """[제목] 을 보고 그 공고의 이상적인 응답을 돌려준다. responses 로 공고별 응답을 바꿀 수 있다."""

    def __init__(self, responses=None, boom=False):
        self.calls, self.systems, self.users, self.responses, self.boom = [], [], [], responses or {}, boom

    def complete(self, system, user_text, images, max_tokens=None):
        if self.boom:
            raise AssertionError("AI 가 호출되면 안 됨")
        title = re.search(r"\[제목\] (.*)", user_text).group(1)
        idx = next(v for k, v in TITLES.items() if k in title)
        self.calls.append(idx)
        self.systems.append(system)
        self.users.append(user_text)
        r = self.responses.get(idx, IDEAL[idx])
        text = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
        return AIResponse(text=text, model="fake-model", input_tokens=9000, output_tokens=4000, stop_reason="end_turn")


@pytest.fixture
def env(tmp_path):
    notices = tmp_path / "ext02_survey_notices.txt"
    shutil.copy(FIX, notices)
    out = tmp_path / "v3_trial"
    base = ["--notices", str(notices), "--out", str(out), "--env", "/nonexistent/.env"]
    return base, out, notices


def run(base, extra=(), client=None):
    return run_v3_trial.main(base + list(extra), client=client)


# ------------------------------------------------------------------ 정상 실행과 결과 파일
def test_run_writes_per_notice_files_and_report(env, capsys):
    base, out, _ = env
    c = FakeClient()
    assert run(base, client=c) == 0
    assert c.calls == ["9096", "9098", "9212"]                                              # 표본 순서(9195, 9211 은 파일에 없어 제외)
    assert all(s == system_prompt_v3() for s in c.systems)                                  # v3 프롬프트를 썼음
    assert all("드림스폰 장학 정보" in u and "신청기간 원문:" not in u and "### [" not in u for u in c.users)      # 본문만 보냄(머리말 줄 제외)
    for idx in ("9096", "9098", "9212"):
        rec = json.loads((out / "{}_v3.json".format(idx)).read_text(encoding="utf-8"))
        assert rec["ok"] and rec["input_tokens"] == 9000 and rec["data"]["gpa"] is not None and (out / "{}_ai_raw.txt".format(idx)).read_text(encoding="utf-8").startswith("{")
    report = (out / "v3_trial_report.txt").read_text(encoding="utf-8")
    assert "(3건 실행, 성공 3건, 실패 0건)" in report and "[1] 항목별 재현율" in report
    results = json.loads((out / "v3_trial_results.json").read_text(encoding="utf-8"))
    assert [r["idx"] for r in results] == ["9212", "9098", "9096"] and all(r["compare"]["lost"] == [] for r in results)
    assert not (out / "credits_log.json").exists()                                           # 가짜 클라이언트라 크레딧 조회 없음
    printed = capsys.readouterr().out
    assert "올려주실 파일" in printed and "v3_trial_report.txt" in printed


def test_first_and_only_select_notices(env):
    base, out, _ = env
    c = FakeClient()
    assert run(base, ["--only", "9098", "9212"], client=c) == 0 and c.calls == ["9098", "9212"]
    c2 = FakeClient()
    assert run(base, ["--first", "1"], client=c2) == 0 and c2.calls == ["9096"]               # 표본 앞 1건(파일에 있는 것 중)
    assert sorted(p.name[:4] for p in out.glob("*_v3.json")) == ["9096", "9098", "9212"]


# ------------------------------------------------------------------ 크레딧 안전장치
def test_already_successful_notices_are_skipped_and_force_redoes(env, capsys):
    base, out, _ = env
    run(base, client=FakeClient())
    c = FakeClient()
    assert run(base, client=c) == 0 and c.calls == []                                       # 다시 실행해도 AI 를 부르지 않음
    assert "처리할 공고가 없습니다" in capsys.readouterr().out
    c2 = FakeClient()
    assert run(base, ["--force", "--only", "9212"], client=c2) == 0 and c2.calls == ["9212"]  # --force 로만 다시


def test_failed_notice_is_retried_but_successes_are_kept(env):
    base, out, _ = env
    c = FakeClient(responses={"9098": "JSON 이 아닌 응답"})
    assert run(base, client=c) == 0
    bad = json.loads((out / "9098_v3.json").read_text(encoding="utf-8"))
    assert bad["ok"] is False and "JSON" in bad["error"] and bad["data"] is None
    assert (out / "9098_ai_raw.txt").read_text(encoding="utf-8") == "JSON 이 아닌 응답"           # 실패해도 원문은 보관
    report = (out / "v3_trial_report.txt").read_text(encoding="utf-8")
    assert "(3건 실행, 성공 2건, 실패 1건)" in report and "실패: 9098" in report
    c2 = FakeClient()
    assert run(base, client=c2) == 0 and c2.calls == ["9098"]                                # 성공한 것은 건너뛰고 실패한 것만 재시도


def test_all_failed_returns_1(env):
    base, _, _ = env
    assert run(base, ["--only", "9098"], client=FakeClient(responses={"9098": "x"})) == 1


def test_dry_run_never_calls_ai_and_writes_nothing(env, capsys):
    base, out, _ = env
    assert run(base, ["--dry-run"], client=FakeClient(boom=True)) == 0
    text = capsys.readouterr().out
    assert "dry-run: AI 를 호출하지 않았습니다" in text and "처리할 공고 3건" in text
    lo, hi = [float(x.replace(",", "")) for x in re.search(r"예상 크레딧 약 ([\d,.]+) ~ ([\d,.]+)", text).groups()]
    assert 0 < lo < hi and "추정" in text
    assert re.search(r"시스템 프롬프트 [\d,]+자 vs 공고 본문 평균", text) and "프롬프트 캐시를 요청" in text           # 비용의 대부분이 프롬프트임을 알려 줌
    assert not list(out.glob("*_v3.json"))
    run(base, client=FakeClient())                                                         # 일부 완료 후 dry-run 은 남은 것만 계산
    assert run(base, ["--dry-run", "--force"], client=FakeClient(boom=True)) == 0
    assert "처리할 공고 3건" in capsys.readouterr().out.split("dry-run")[-1]


def test_missing_ai_key_stops_before_any_call(env, capsys):
    base, out, _ = env
    assert run(base) == 2                                                                  # 테스트 환경에는 AI_API_KEY 가 없음 (conftest 격리)
    assert "AI_API_KEY" in capsys.readouterr().err and not list(out.glob("*_v3.json"))


# ------------------------------------------------------------------ 입력·설정 오류
def test_missing_notice_file_and_bad_registry(env, tmp_path, capsys):
    base, out, notices = env
    assert run_v3_trial.main(["--notices", str(tmp_path / "none.txt"), "--out", str(out), "--env", "/nonexistent/.env"], client=FakeClient()) == 2
    assert "python run_ext02_survey.py" in capsys.readouterr().err
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"version": 1, "items": [{"code": "x"}]}), encoding="utf-8")
    c = FakeClient(boom=True)
    assert run(base, ["--registry", str(bad)], client=c) == 2                              # 잘못된 사전이면 AI 를 부르기 전에 중단
    assert not list(out.glob("*_v3.json"))


def test_custom_registry_is_used_for_the_prompt(env, tmp_path):
    base, out, _ = env
    reg = R.load_registry().with_items([{"code": "SCHOLAR_TERMS", "label": "장학 수혜 학기 수", "kind": "student", "source": "student", "type": "int", "tier": 3,
                                         "question": "몇 학기?", "notice_path": "unmodeled_conditions", "op": "custom", "custom": "scholar_terms",
                                         "description": "수혜 학기 수 한도"}])
    f = tmp_path / "reg.json"
    f.write_text(json.dumps({"version": reg.version, "items": [R._to_dict(i) for i in reg.items]}, ensure_ascii=False), encoding="utf-8")
    c = FakeClient()
    assert run(base, ["--registry", str(f), "--only", "9212"], client=c) == 0
    assert "- 장학 수혜 학기 수 (SCHOLAR_TERMS): 수혜 학기 수 한도" in c.systems[0]            # 지정한 사전으로 프롬프트를 만듦


# ------------------------------------------------------------------ 보고서만 다시
def test_report_only_rebuilds_without_ai(env, capsys):
    base, out, _ = env
    assert run(base, ["--report-only"]) == 1 and "저장된 결과가 없습니다" in capsys.readouterr().err
    run(base, client=FakeClient())
    (out / "v3_trial_report.txt").unlink()
    assert run(base, ["--report-only"]) == 0
    assert "[1] 항목별 재현율" in capsys.readouterr().out and (out / "v3_trial_report.txt").is_file()


def test_credit_log_is_summed_in_report(env, monkeypatch, capsys):
    base, out, _ = env
    out.mkdir(parents=True)
    (out / "credits_log.json").write_text(json.dumps([{"when": "t1", "count": 5, "delta": {"total.remaining": -120.5}},
                                                      {"when": "t2", "count": 3, "delta": {"total.remaining": -80.0, "total.used": 80.0}}]), encoding="utf-8")
    run(base, client=FakeClient())
    report = (out / "v3_trial_report.txt").read_text(encoding="utf-8")
    assert "total.remaining: -200.50" in report and "total.used: +80.00" in report         # 여러 번 나눠 실행해도 합산해서 보여줌


# ------------------------------------------------------------------ 저장된 AI 원문을 현재 코드로 다시 정리 (크레딧 없이)
def test_renormalize_rebuilds_from_raw_without_ai_calls(env, capsys):
    base, out, _ = env
    response = dict(IDEAL["9212"], apply_end=None, apply_start=None,
                    variants=[{"name": "A", "apply_start": "2026-09-01", "apply_end": "2026-09-23"}, {"name": "B", "apply_end": "2026-10-07"}])
    assert run(base, ["--only", "9212"], client=FakeClient(responses={"9212": response})) == 0
    f = out / "9212_v3.json"
    rec = json.loads(f.read_text(encoding="utf-8"))
    assert rec["data"]["apply_end"] == "2026-10-07"                                              # 지금 코드는 유형 마감에서 채움
    rec["data"]["apply_end"], rec["issues"] = None, [{"field": "apply_end", "reason": "교외 장학금인데 마감일을 찾지 못함"}]   # 예전 코드로 정리된 결과를 흉내
    f.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    capsys.readouterr()
    assert run(base, ["--renormalize"], client=FakeClient(boom=True)) == 0                       # AI 를 부르지 않음
    fixed = json.loads(f.read_text(encoding="utf-8"))
    assert fixed["data"]["apply_end"] == "2026-10-07" and fixed["issues"] == [] and fixed["renormalized"] is True
    assert fixed["input_tokens"] == rec["input_tokens"] and fixed["prompt_sha"] == rec["prompt_sha"]    # 다른 기록은 그대로
    printed = capsys.readouterr().out
    assert "1건을 저장된 AI 원문에서 다시 정리했습니다" in printed and "[1] 항목별 재현율" in printed


def test_renormalize_skips_failed_and_missing_raw(env, capsys):
    base, out, _ = env
    assert run(base, client=FakeClient(responses={"9098": "JSON 아님"})) == 0
    (out / "9096_ai_raw.txt").unlink()                                                            # 원문이 없는 것은 건너뜀
    assert run(base, ["--renormalize"], client=FakeClient(boom=True)) == 0
    assert "1건을 저장된 AI 원문에서 다시 정리했습니다" in capsys.readouterr().out               # 9212 만
    empty = out.parent / "empty_out"
    assert run_v3_trial.main(["--notices", base[1], "--out", str(empty), "--env", "/nonexistent/.env", "--renormalize"], client=FakeClient(boom=True)) == 1


def test_results_json_keeps_prompt_version_and_ai_summary_for_diagnosis(env):
    base, out, _ = env
    run(base, client=FakeClient())
    results = json.loads((out / "v3_trial_results.json").read_text(encoding="utf-8"))
    by = {r["idx"]: r for r in results}
    assert len({r["prompt_sha"] for r in results}) == 1 and len(results[0]["prompt_sha"]) == 8
    ai = by["9096"]["ai"]                                                                         # 다음 분석에서 원인을 바로 볼 수 있게 AI 가 쓴 핵심 항목을 함께 저장
    assert {"variants", "waivers", "special_groups", "exclusion_items", "prerequisites", "enrollment", "apply", "region", "uncertain"} <= set(ai)
    assert ai["prerequisites"][0]["type"] == "NATIONAL_SCHOLARSHIP_APPLIED" and ai["region"]["mode"] == "ANY" and ai["apply"]["apply_type"] == "DIRECT"
    report = (out / "v3_trial_report.txt").read_text(encoding="utf-8")
    assert "프롬프트 버전: " + results[0]["prompt_sha"] + " 3건" in report


def test_reference_date_is_the_notice_start_date_not_today(env):
    """지난 공고(예: 2026-06 마감)를 오늘 기준으로 보내면 AI 가 '마감이 지났다'고 검수 표시를 단다 -> 신청 시작일을 [기준일]로 보낸다."""
    base, out, _ = env
    c = FakeClient()
    assert run(base, client=c) == 0
    by = {re.search(r"\[제목\] (.*)", u).group(1)[:4]: u for u in c.users}
    assert "[기준일] 2026-09-21" in by["문주장학"] and "[기준일] 2026-06-01" in by["사랑의 "] and "[기준일] 2026-05-15" in by["거창군 "]


def test_saved_records_carry_the_original_url(env, monkeypatch):
    base, out, _ = env
    assert run(base, ["--only", "9212"], client=FakeClient()) == 0
    rec = json.loads((out / "9212_v3.json").read_text(encoding="utf-8"))
    assert rec["url"] == "https://www.dreamspon.com/scholarship/view.html?idx=9212"
    monkeypatch.setenv("EXT02_BASE_URL", "http://127.0.0.1:9000")
    assert run(base, ["--only", "9212", "--force"], client=FakeClient()) == 0
    assert json.loads((out / "9212_v3.json").read_text(encoding="utf-8"))["url"] == "http://127.0.0.1:9000/scholarship/view.html?idx=9212"
