# -*- coding: utf-8 -*-
"""5-C 이어 답하기(recommend_step): 1차 판정 + 후속 질문 -> 답 제출 -> 2차 판정 + 변화. 서버는 상태를 저장하지 않는다."""
import copy
import json

import pytest

import run_recommend
from scholarship.common.config import Settings
from scholarship.recommend import matcher as M
from scholarship.recommend import result_format as F
from scholarship.recommend.file_source import FileSource
from scholarship.recommend.profile import ProfileError
from scholarship.recommend.service import Recommender, diff_evaluations
from tests.test_matcher_v3 import TODAY, notice, prof, reg, req, v3cand
from tests.test_recommend_service import Stub

MATCH, CONDITIONAL, NO_MATCH = M.MATCH, M.CONDITIONAL, M.NO_MATCH
TEXT_COND = [{"text": "봉사 활동 증빙", "section": "신청자격", "source": "본문", "kind": "ELIGIBILITY"}]


def school():
    s = Settings()
    s.recommend_school_sido, s.recommend_school_sigungu, s.recommend_school_type = "경기도", "시흥시", "GENERAL"
    return s


def five():
    """부모 거주(서초구) / 나이(29세 이하) / 신분(장애) / 문장 조건 / 학교 소재지(서울: 시흥은 미달) 다섯 공고"""
    return [v3cand(1, **reg("ANY", req("RESIDENCE_PARENT", "서울특별시 서초구"))), v3cand(2, age={"max": 29}),
            v3cand(3, special_groups=[{"code": "DISABLED", "mode": "REQUIRED"}]), v3cand(4, unmodeled_conditions=TEXT_COND),
            v3cand(5, **reg("ANY", req("SCHOOL_LOCATION", "서울특별시")))]


BASE = {"level": "UNIV", "status": "ENROLLED", "grade": 4, "semesters_completed": 7, "residence_sido": "경기도", "residence_sigungu": "수원시"}
PARENT_SEOCHO = {"parent_residence_sido": "서울특별시", "parent_residence_sigungu": "서초구"}


def rec():
    return Recommender(Stub(five()), school())


def step(**kw):
    kw.setdefault("today", TODAY)
    return rec().recommend_step(kw.pop("profile", BASE), **kw)


def ids(items):
    return [x["src_id"] for x in items]


def qcodes(r):
    return [q["code"] for q in r["follow_up"]["questions"]]


# ================================================================== 1차 호출
def test_the_first_call_returns_verdicts_questions_data_gaps_and_the_text_condition_list():
    r = step()
    assert r["result_version"] == 1 and r["round"] == 1 and r["counts"]["conditional"] == 4 and r["counts"]["no_match"] == 1
    assert sorted(ids(r["conditional"])) == ["1", "2", "3", "4"] and ids(r["excluded"]) == ["5"]
    assert qcodes(r) == ["RESP", "AGE", "SPECIAL"]                                                                  # 신분은 민감해서 마지막
    assert [q["resolves"] for q in r["follow_up"]["questions"]] == [["EXT01:1"], ["EXT01:2"], ["EXT01:3"]]
    assert r["follow_up"]["data_gap"] == [] and [d["id"] for d in r["follow_up"]["document_only"]] == ["EXT01:4"]
    assert r["answers"] == {"applied": [], "warnings": [], "declined": []} and r["changes"] == []
    assert r["change_summary"] == {"changed": 0, "to_match": 0, "to_conditional": 0, "to_no_match": 0, "unchanged": 5, "reduced_confirms": 0}
    assert F.validate(r) == []


def test_recommend_itself_is_unchanged_except_for_the_version_and_ids():
    r = rec().recommend(BASE, today=TODAY)
    assert "follow_up" not in r and "changes" not in r and r["result_version"] == 1 and r["conditional"][0]["id"].startswith("EXT01:")
    assert F.validate(r, step=False) == []


# ================================================================== 2차 호출: 답을 JSON 으로
def test_json_answers_are_merged_and_the_changes_are_reported():
    r = step(answers=dict(PARENT_SEOCHO, age=31), round_no=2)
    assert sorted(r["answers"]["applied"]) == ["age", "parent_residence_sido", "parent_residence_sigungu"] and r["answers"]["warnings"] == []
    assert ids(r["recommended"]) == ["1"] and sorted(ids(r["conditional"])) == ["3", "4"] and sorted(ids(r["excluded"])) == ["2", "5"]
    by = {c["src_id"]: c for c in r["changes"]}
    assert (by["1"]["from"], by["1"]["to"], by["1"]["reason"]) == ("CONDITIONAL", "MATCH", "남은 확인 항목이 모두 풀렸습니다")
    assert (by["2"]["from_label"], by["2"]["to_label"], by["2"]["reason"]) == ("조건부", "미달", "만 31세 (만 29세 이하 미달)")
    assert [c["to"] for c in r["changes"]] == ["MATCH", "NO_MATCH"]                                                  # 충족이 먼저
    assert r["change_summary"] == {"changed": 2, "to_match": 1, "to_conditional": 0, "to_no_match": 1, "unchanged": 3, "reduced_confirms": 0}
    assert qcodes(r) == ["SPECIAL"] and r["round"] == 2 and F.validate(r) == []                                     # 답한 질문은 다시 나오지 않는다


def test_the_answers_replace_earlier_values_and_a_wrong_parent_residence_rejects_the_notice():
    r = step(profile=dict(BASE, **PARENT_SEOCHO), answers={"parent_residence_sido": "경기도", "parent_residence_sigungu": "수원시"}, round_no=2)
    assert [c["src_id"] for c in r["changes"]] == ["1"] and r["changes"][0]["to"] == "NO_MATCH" and "부모 주민등록" in r["changes"][0]["reason"]


def test_a_wrong_answer_is_reported_and_the_other_answers_still_apply():
    r = step(answers=dict(PARENT_SEOCHO, age="abc"), round_no=2)
    assert sorted(r["answers"]["applied"]) == ["parent_residence_sido", "parent_residence_sigungu"]
    assert len(r["answers"]["warnings"]) == 1 and r["answers"]["warnings"][0].startswith("age:")
    assert ids(r["recommended"]) == ["1"] and "2" in ids(r["conditional"])                                          # 틀린 나이는 적용되지 않아 2번은 그대로 조건부


def test_unknown_and_personal_keys_are_not_accepted():
    r = step(answers={"새로운항목": 1, "이름": "홍길동"}, round_no=2)
    assert r["answers"]["applied"] == [] and sorted(w.split(":")[0] for w in r["answers"]["warnings"]) == ["새로운항목", "이름"]
    assert all("받지 않는 항목입니다" in w for w in r["answers"]["warnings"]) and r["changes"] == []


def test_the_callers_inputs_are_never_modified():
    profile, answers, declined = copy.deepcopy(BASE), dict(PARENT_SEOCHO, age=31), ["AGE"]
    before = (copy.deepcopy(profile), copy.deepcopy(answers), list(declined))
    rec().recommend_step(profile, answers=answers, declined=declined, round_no=2, today=TODAY)
    assert (profile, answers, declined) == before


def test_an_invalid_earlier_input_is_an_error_not_a_warning():
    with pytest.raises(ProfileError, match="age"):
        step(profile=dict(BASE, age="abc"), answers={"grade": 3})


# ================================================================== 2차 호출: 답을 텍스트로, 건너뛰기
def test_text_answers_use_the_same_parser_and_skipped_items_are_not_asked_again():
    r = step(answers_text="- 만 나이: 31\n- 해당 항목: 해당 없음\n", round_no=2)
    assert {"age", "special_codes", "special_complete"} <= set(r["answers"]["applied"])
    assert sorted(c["src_id"] for c in r["changes"]) == ["2", "3"] and {c["to"] for c in r["changes"]} == {"NO_MATCH"}
    skipped = step(answers_text="- 해당 항목: 답하지 않음\n", round_no=2)
    assert "SPECIAL" in skipped["answers"]["declined"] and "SPECIAL" not in qcodes(skipped) and skipped["changes"] == []
    assert qcodes(skipped) == ["RESP", "AGE"]


def test_declined_codes_from_the_caller_are_respected():
    r = step(declined=["AGE", "SPECIAL"], round_no=2)
    assert qcodes(r) == ["RESP"] and r["answers"]["declined"] == ["AGE", "SPECIAL"]
    done = step(declined=["RESP", "AGE", "SPECIAL"], round_no=2)
    assert done["follow_up"]["done"] is True and done["follow_up"]["done_reason"] == "ALL_DECLINED"


def test_json_and_text_answers_can_be_combined_with_text_winning():
    r = step(answers={"age": 20}, answers_text="- 만 나이: 31", round_no=2)
    assert "2" in ids(r["excluded"]) and r["answers"]["applied"].count("age") == 1


def test_question_rounds_and_limits():
    assert step(round_no=3)["follow_up"]["done_reason"] == "MAX_ROUNDS" and step(round_no=3)["follow_up"]["questions"] == []
    assert qcodes(step(max_questions=2)) == ["RESP", "AGE"] and step(max_questions=2)["follow_up"]["remaining"] == 1


# ================================================================== 변화 비교
def test_confirmations_that_shrink_without_changing_the_verdict_are_counted_separately():
    both = Recommender(Stub([v3cand(1, age={"max": 29}, **reg("ANY", req("RESIDENCE_PARENT", "서울특별시 서초구")))]), school())
    r = both.recommend_step(BASE, answers={"age": 22}, round_no=2, today=TODAY)
    assert r["changes"] == [] and r["change_summary"]["reduced_confirms"] == 1 and r["change_summary"]["unchanged"] == 1 and ids(r["conditional"]) == ["1"]


def test_diff_orders_changes_with_matches_first_and_ignores_unmatched_notices():
    s = school()
    r1 = Recommender(Stub(five()), s)
    before = r1._evaluate_all(BASE, TODAY)[-1]
    after = r1._evaluate_all(dict(BASE, **PARENT_SEOCHO, age=31), TODAY)[-1]
    changes, summary = diff_evaluations(before, after)
    assert [c["src_id"] for c in changes] == ["1", "2"] and summary["changed"] == 2
    assert diff_evaluations(before, before) == ([], {"changed": 0, "to_match": 0, "to_conditional": 0, "to_no_match": 0, "unchanged": 5, "reduced_confirms": 0})
    only_first = [x for x in after if x[0]["src_id"] == "1"]
    assert len(only_first) == 1 and diff_evaluations(before, only_first)[1] == {"changed": 1, "to_match": 1, "to_conditional": 0, "to_no_match": 0, "unchanged": 0, "reduced_confirms": 0}   # 비교 대상에 없는 공고는 세지 않는다
    assert diff_evaluations([], after) == ([], {"changed": 0, "to_match": 0, "to_conditional": 0, "to_no_match": 0, "unchanged": 0, "reduced_confirms": 0})


# ================================================================== 개발용 결과 폴더(FileSource)가 v3 조건을 잃지 않는다
def test_file_source_keeps_v3_conditions(tmp_path):
    for sid, kw in (("9001", {"new_student_only": True}), ("9002", {})):
        e = notice(**kw)
        e["scholarship_name"], e["apply_end"] = "장학 " + sid, "2026-12-31"
        (tmp_path / "ext02_{}_ai_result.json".format(sid)).write_text(json.dumps({"elig": e, "review": {}}, ensure_ascii=False), encoding="utf-8")
    r = Recommender(FileSource(str(tmp_path)), school()).recommend(dict(BASE), today=TODAY)
    assert ids(r["excluded"]) == ["9001"] and "신입생만 대상" in r["excluded"][0]["reasons"][0] and ids(r["recommended"]) == ["9002"]       # v2 로 정리했다면 신입생 조건이 사라져 둘 다 충족이었을 것


# ================================================================== 실행 도구
@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    for c in five():
        e = dict(c["elig"], apply_end="2026-12-31", scholarship_name="장학 {}".format(c["src_id"]))
        (d / "ext02_{}_ai_result.json".format(c["src_id"])).write_text(json.dumps({"elig": e, "review": {}}, ensure_ascii=False), encoding="utf-8")
    first = tmp_path / "first.txt"
    first.write_text("- 학적상태: 재학\n- 학년: 4\n- 과정: 학사\n- 이수학기: 7\n- 본인 주민등록: 경기도 수원시\n", encoding="utf-8")
    answers = tmp_path / "answers.txt"
    answers.write_text("- 부모 주민등록: 서울특별시 서초구\n- 만 나이: 31\n- 해당 항목: 답하지 않음\n", encoding="utf-8")
    return d, first, answers


def cli(capsys, folder, *extra, monkeypatch=None):
    d, first, _ = folder
    assert run_recommend.main(["--profile-text", str(first), "--results-dir", str(d), "--today", "2026-10-06", "--env", "/nonexistent/.env"] + list(extra)) == 0
    return capsys.readouterr()


@pytest.fixture(autouse=True)
def school_env(monkeypatch):
    monkeypatch.setenv("RECOMMEND_SCHOOL_SIDO", "경기도")
    monkeypatch.setenv("RECOMMEND_SCHOOL_SIGUNGU", "시흥시")
    monkeypatch.setenv("RECOMMEND_SCHOOL_TYPE", "GENERAL")


def test_cli_follow_up_prints_the_questions_and_the_text_condition_list(capsys, folder):
    out = cli(capsys, folder, "--follow-up", "--width", "130").out
    assert "[후속 질문] 1회차 / 최대 3회" in out and "| 부모 주민등록 |" in out and "| 만 나이" in out and "민감 (건너뛰어도 됩니다)" in out
    assert out.index("| 부모 주민등록 |") < out.index("| 만 나이") < out.index("| 해당 항목")                                 # 순서: 일반 먼저, 민감 마지막
    assert "- 부모 주민등록: 경기도 수원시" in out and "[공고문에서 직접 확인할 것]" in out and "봉사 활동 증빙" in out and "[변화]" not in out


def test_cli_answers_print_the_changes_and_the_end_of_questions(capsys, folder):
    out = cli(capsys, folder, "--answers", str(folder[2]), "--round", "2", "--width", "130").out
    flat = " ".join(out.split())
    assert "[변화] 답을 넣기 전과 비교: 바뀐 공고 2 (충족 1 / 조건부 0 / 미달 1)" in flat
    assert "| 1 | 장학 1 | 조건부 | 충족 | 남은 확인 항목이 모두 풀렸습니다 |" in flat and "| 2 | 장학 2 | 조건부 | 미달 | 만 31세 (만 29세 이하 미달) |" in flat
    assert "[후속 질문] 2회차 / 최대 3회" in out and "후속 질문 종료: 건너뛴 항목만 남았습니다" in out                         # 부모·나이는 답했고 신분은 건너뜀: 남은 질문 없음


def test_cli_declined_round_and_json(capsys, folder):
    out = cli(capsys, folder, "--follow-up", "--declined", "RESP,SPECIAL", "--width", "130").out
    assert "| 만 나이" in out and "| 부모 주민등록 |" not in out and "| 해당 항목" not in out
    last = cli(capsys, folder, "--round", "3", "--width", "130").out
    assert "후속 질문 종료: 후속 질문은 최대 3회까지입니다" in last
    js = json.loads(cli(capsys, folder, "--follow-up", "--json").out)
    assert js["result_version"] == 1 and js["follow_up"]["questions"][0]["code"] == "RESP" and F.validate(js) == []


def test_cli_without_follow_up_options_is_the_plain_recommendation(capsys, folder):
    out = cli(capsys, folder, "--width", "130").out
    assert "[후속 질문]" not in out and "[변화]" not in out
    js = json.loads(cli(capsys, folder, "--json").out)
    assert "follow_up" not in js and F.validate(js, step=False) == []


def test_cli_list_and_markdown_formats_include_the_follow_up_blocks(capsys, folder):
    assert "[후속 질문] 1회차 / 최대 3회" in cli(capsys, folder, "--follow-up", "--format", "list").out
    md = cli(capsys, folder, "--follow-up", "--format", "md").out
    assert "[후속 질문] 1회차 / 최대 3회" in md and "| # | 항목 | 질문 |" in md


# ================================================================== run_v3_match.py: v3_trial 20건으로 같은 5단계 기능
import run_v3_match
from scholarship.recommend.table import display_width
from tests.test_run_v3_match import NOENV, write_trial


@pytest.fixture
def trial_env(tmp_path):
    d = tmp_path / "v3_trial"
    write_trial(d, {"9001": ("부모 거주 장학", dict(reg("ANY", req("RESIDENCE_PARENT", "서울특별시 서초구")), apply_end="2026-09-01")), "9002": ("나이 장학", dict(age={"max": 29}, apply_end="2027-01-31")),
                    "9003": ("문장 조건 장학", dict(unmodeled_conditions=TEXT_COND))})
    first = tmp_path / "first.txt"
    first.write_text("- 학적상태: 재학\n- 학년: 4\n- 과정: 학사\n- 이수학기: 7\n- 본인 주민등록: 경기도 수원시\n", encoding="utf-8")
    answers = tmp_path / "answers.txt"
    answers.write_text("- 부모 주민등록: 서울특별시 서초구\n- 만 나이: 31\n", encoding="utf-8")
    return d, first, answers


def trial_cli(capsys, env, *extra):
    d, first, _ = env
    assert run_v3_match.main(["--profile-text", str(first), "--trial-dir", str(d), "--today", "2026-10-12"] + list(extra) + NOENV) == 0
    return capsys.readouterr()


def test_run_v3_match_follow_up_works_on_the_trial_notices_without_a_deadline_filter(capsys, trial_env):
    out = trial_cli(capsys, trial_env, "--follow-up", "--width", "130").out
    assert "부모 거주 장학" in out and "(마감 지남)" in out and "D--" not in out                                    # 2026-09-01 마감은 기준일(10-12)보다 이전이지만 걸러지지 않고 '마감 지남'으로 표시
    assert "[후속 질문] 1회차 / 최대 3회" in out and "| 부모 주민등록 |" in out and "| 만 나이" in out and "[공고문에서 직접 확인할 것]" in out and "봉사 활동 증빙" in out


def test_run_v3_match_answers_show_the_changes_and_json_matches_the_format(capsys, trial_env):
    out = trial_cli(capsys, trial_env, "--answers", str(trial_env[2]), "--round", "2", "--width", "130").out
    flat = " ".join(out.split())
    assert "[변화] 답을 넣기 전과 비교: 바뀐 공고 2 (충족 1 / 조건부 0 / 미달 1)" in flat and "| 9001 | 부모 거주 장학 | 조건부 | 충족 |" in flat and "| 9002 | 나이 장학 | 조건부 | 미달 |" in flat
    js = json.loads(trial_cli(capsys, trial_env, "--follow-up", "--json").out)
    assert F.validate(js) == [] and js["follow_up"]["questions"][0]["code"] == "RESP" and [x["src_id"] for x in js["conditional"]] == ["9001", "9002", "9003"]


def test_run_v3_match_follow_up_options_and_errors(capsys, trial_env):
    declined = trial_cli(capsys, trial_env, "--declined", "RESP", "--width", "130").out
    assert "| 부모 주민등록 |" not in declined and "| 만 나이" in declined
    assert "후속 질문은 최대 3회까지입니다" in trial_cli(capsys, trial_env, "--round", "3", "--width", "130").out
    d, first, _ = trial_env
    assert run_v3_match.main(["--profile-text", str(first), "--trial-dir", str(d), "--follow-up", "--today", "내일"] + NOENV) == 2
    assert "YYYY-MM-DD" in capsys.readouterr().err
    assert run_v3_match.main(["--profile-text", str(first), "--trial-dir", str(d), "--answers", "/nonexistent/a.txt"] + NOENV) == 2
    assert "답변 파일을 읽을 수 없습니다" in capsys.readouterr().err
    plain = trial_cli(capsys, trial_env, "--width", "130").out
    assert "[후속 질문]" not in plain and "=== v3 공고 3건 판정" in plain                                           # 옵션이 없으면 기존 동작 그대로


def test_the_deadline_cell_marks_passed_deadlines():
    from scholarship.recommend.service import _deadline_cell
    assert _deadline_cell({"apply_end": "2026-10-18", "days_left": 12, "warnings": []}) == "10-18 (D-12)"
    assert _deadline_cell({"apply_end": "2026-10-01", "days_left": -5, "warnings": []}) == "10-01 (마감 지남)"
    assert _deadline_cell({"apply_end": "2026-10-06", "days_left": 0, "warnings": []}) == "10-06 (D-0)"
    assert _deadline_cell({"apply_end": None, "days_left": None, "warnings": []}) == "-" and _deadline_cell({"apply_end": "2026-10-18", "warnings": []}) == "10-18"
