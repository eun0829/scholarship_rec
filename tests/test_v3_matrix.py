# -*- coding: utf-8 -*-
"""4-B2 일치 시험: 가상 학생 30명 × 공고 20건 × 학교 5개를 엔진으로 판정해 원문에서 만든 정답과 대조한다."""
import json
import re
import shutil
from collections import Counter

import pytest

import run_v3_matrix
from scholarship.recommend import matcher
from scholarship.recommend import matrix as X
from scholarship.recommend.matrix_truth import NO, RULES, UNSURE, YES, Facts, Judge, Truth, judge, tri_and, tri_or
from scholarship.recommend.profile import build_profile
from scholarship.recommend.student_text import parse_student_text
from scholarship.sources.ext02_trial import DEFAULT_SAMPLE
from tests.test_matcher_v3 import prof

MATCH, CONDITIONAL, NO_MATCH = matcher.MATCH, matcher.CONDITIONAL, matcher.NO_MATCH


@pytest.fixture(scope="module")
def notices():
    return X.load_notices()


@pytest.fixture(scope="module")
def students():
    return X.load_students()


@pytest.fixture(scope="module")
def pairs(notices, students):
    return X.run(notices, students)


@pytest.fixture(scope="module")
def by_key(pairs):
    return {p.key: p for p in pairs}


# ================================================================== 자료
def test_pinned_notices_are_the_20_sample_notices_without_contact_info():
    files = sorted((X.default_dir() / "notices").glob("*.json"))
    assert [f.stem for f in files] == sorted(DEFAULT_SAMPLE)
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"[\w.+-]+@(?!example\.com)[\w-]+\.[\w.-]+", text) and not re.search(r"0\d{1,2}[-\s.]\d{3,4}[-\s.]\d{4}", text), f.name
        rec = json.loads(text)
        assert rec["url"] == "https://www.dreamspon.com/scholarship/view.html?idx=" + rec["idx"] and rec["data"] and "evidence" not in rec["data"]


def test_notices_load_as_v3_and_failed_trial_records_are_skipped(tmp_path):
    ns = X.load_notices()
    assert list(ns) == sorted(DEFAULT_SAMPLE) and all(n["elig"]["schema_version"] == 3 and n["title"] for n in ns.values())
    (tmp_path / "1_v3.json").write_text(json.dumps({"idx": "1", "title": "성공", "ok": True, "data": {"category": "OUT"}}), encoding="utf-8")
    (tmp_path / "2_v3.json").write_text(json.dumps({"idx": "2", "title": "실패", "ok": False, "data": None}), encoding="utf-8")
    assert list(X.load_notices(str(tmp_path))) == ["1"]


def test_students_are_30_unique_people_that_parse_without_warnings(students):
    assert [s.id for s in students] == ["s%02d" % i for i in range(1, 31)] and all(s.desc for s in students)
    for s in students:
        assert parse_student_text(s.text).report.warnings == [], s.id
    assert len({s.desc for s in students}) == 30


def test_every_notice_has_exactly_one_truth_rule_and_five_schools(notices):
    assert sorted(RULES) == sorted(notices) and len(X.SCHOOLS) == 5
    assert {s["type"] for s in X.SCHOOLS.values()} == {"GENERAL", "COLLEGE"}


# ================================================================== 정답 규칙의 도구
def test_tri_logic_and_judge_accumulation():
    assert tri_and(True, True) is True and tri_and(True, None) is None and tri_and(None, False) is False and tri_and() is True
    assert tri_or(False, False) is False and tri_or(False, None) is None and tri_or(None, True) is True and tri_or() is None
    j = Judge()
    j.tri(True, "a")
    assert j.result().outcome == YES
    j.tri(None, "b", "ambiguous")
    assert j.result().outcome == UNSURE and j.result().tags == {"ambiguous"}
    j.tri(False, "c", "ai-gap")
    t = j.result()
    assert t.outcome == NO and t.reasons == ["c 미충족"] and t.tags == {"ambiguous", "ai-gap"}                        # 미달이 하나라도 있으면 미달, 이유는 미달 사유만


SCHOOL = {"sido": "경기도", "sigungu": "시흥시", "type": "GENERAL", "programs": []}


def facts(**kw):
    return Facts(prof(**kw), SCHOOL)


@pytest.mark.parametrize("which, need, kw, want", [
    ("residence", "경상남도 거창군", dict(residence_sido="경상남도", residence_sigungu="거창군"), True), ("residence", "경상남도 거창군", dict(residence_sido="경상남도", residence_sigungu="합천군"), False),
    ("residence", "경상남도 거창군", dict(residence_sido="경상남도"), None), ("residence", "경상남도", dict(residence_sido="경상남도"), True), ("residence", "경상남도", dict(residence_sido="경기도"), False),
    ("residence", "경상남도 거창군", {}, None), ("residence", "인천광역시 서구 석남1동", dict(residence_sido="인천광역시", residence_sigungu="서구 석남1동"), True),
    ("residence", "인천광역시 서구 석남1동", dict(residence_sido="인천광역시", residence_sigungu="서구 검단동"), False), ("residence", "인천광역시 서구 석남1동", dict(residence_sido="인천광역시", residence_sigungu="서구"), None),
    ("parent", "서울특별시 서초구", dict(parent_residence_sido="서울특별시", parent_residence_sigungu="서초구"), True), ("hs", "대전광역시", dict(hs_sido="대전광역시"), True)])
def test_truth_place_comparison(which, need, kw, want):
    assert facts(**kw).place(which, need) is want


@pytest.mark.parametrize("kw, need, want", [(dict(residence_years=3.0, residence_years_open=True), 3, True), (dict(residence_years=3.0, residence_years_open=True), 5, None),
                                            (dict(residence_years=1.0, residence_years_upper=3.0), 1, True), (dict(residence_years=1.0, residence_years_upper=3.0), 3, None),
                                            (dict(residence_years=0.0, residence_years_upper=1.0), 3, False), ({}, 1, None)])
def test_truth_residence_years(kw, need, want):
    assert facts(**kw).residence_years_ge(need) is want


def test_truth_grade_helpers():
    f = facts(gpa_last=3.0, gpa_cumulative=3.6, gpa_scale=4.5, credits_last=12, gpa_percent=85)
    assert f.gpa("last", [(3.0, 4.5)]) is True and f.gpa("last", [(3.1, 4.5)]) is False and f.gpa("cum", [(3.6, 4.5)]) is True
    assert f.gpa("last", [(3.0, 4.3)]) is None and f.gpa("last", [(3.1, 4.3), (3.0, 4.5)]) is True                 # 학생의 만점과 같은 기준이 있을 때만 비교
    assert f.credits_ge(12) is True and f.credits_ge(13) is False and f.percent_ge(85) is True and f.percent_ge(86) is False
    assert facts().gpa("last", [(3.0, 4.5)]) is None and facts().credits_ge(12) is None and facts().percent_ge(70) is None
    rows = [{"term": t, "gpa": g, "credits": c} for t, (g, c) in zip(["2026-1", "2025-2", "2025-1", "2024-2"], [(4.0, 18), (3.9, 18), (3.9, 18), (3.8, 17)])]
    h = facts(term_history=rows, gpa_scale=4.5)
    assert h.last_n_average_ge(4, [(3.5, 4.5)]) is True and h.last_n_average_ge(4, [(3.95, 4.5)]) is False and h.last_n_average_ge(5, [(3.5, 4.5)]) is None
    split = facts(term_history=[{"term": "2026-1", "gpa": 4.5, "credits": 1}, {"term": "2025-2", "gpa": 3.0, "credits": 19}], gpa_scale=4.5)
    assert split.last_n_average_ge(2, [(3.5, 4.5)]) is None                                                      # 단순 평균(3.75)과 학점 가중 평균(3.08)이 다르게 판정되면 불확실


@pytest.mark.parametrize("kw, want", [(dict(semesters_completed=0), True), (dict(semesters_completed=1), False), (dict(semesters_completed=0, admission_type="TRANSFER"), False),
                                      (dict(semesters_completed=3), False), ({}, None)])
def test_truth_new_student(kw, want):
    assert facts(**kw).new_student is want


def test_truth_remaining_terms_only_for_four_year_undergraduates():
    assert facts(semesters_completed=6).remaining == 2 and facts().remaining is None
    assert facts(level="GRAD", semesters_completed=1).remaining is None and Facts(prof(semesters_completed=6), dict(SCHOOL, type="COLLEGE")).remaining is None


def test_truth_special_and_income():
    f = facts(special_codes=["DISABLED"], special_complete=True, income_bracket=4)
    assert f.special("DISABLED") is True and f.special("NEAR_POOR") is False and f.special("NEAR_POOR", "DISABLED") is True and facts().special("DISABLED") is None
    assert f.income_le(4) is True and f.income_le(3) is False and facts().income_le(9) is None


# ================================================================== 분류
@pytest.mark.parametrize("verdict, outcome, tags, want", [
    (NO_MATCH, NO, set(), "OK"), (NO_MATCH, UNSURE, set(), "FALSE_REJECT"), (NO_MATCH, YES, set(), "FALSE_REJECT"),
    (MATCH, NO, set(), "FALSE_ACCEPT_MATCH"), (MATCH, NO, {"detail-place"}, "FALSE_ACCEPT_MATCH"), (CONDITIONAL, NO, set(), "FALSE_ACCEPT_COND"),
    (CONDITIONAL, NO, {"detail-place"}, "KNOWN_LIMIT"), (CONDITIONAL, NO, {"ai-gap"}, "KNOWN_LIMIT"), (CONDITIONAL, NO, {"ambiguous"}, "FALSE_ACCEPT_COND"),
    (MATCH, UNSURE, set(), "OVERCONFIDENT"), (CONDITIONAL, UNSURE, set(), "OK"), (MATCH, YES, set(), "OK"), (CONDITIONAL, YES, set(), "UNDERCONFIDENT")])
def test_classification(verdict, outcome, tags, want):
    assert X.classify(verdict, Truth(outcome, [], tags)) == want


def test_problems_selection():
    ps = [X.Pair("1", "t", None, "시흥", None, Truth(NO), k) for k in X.ORDER]
    assert [p.klass for p in X.problems(ps)] == ["FALSE_REJECT", "FALSE_ACCEPT_MATCH", "OVERCONFIDENT", "FALSE_ACCEPT_COND"]
    assert [p.klass for p in X.problems(ps, include_info=True)] == list(X.ORDER[1:])


# ================================================================== 정답 규칙의 손으로 확인한 기대값 (원문을 읽고 정한 판정)
GOLDEN = [
    # (학생, 공고, 학교, 정답, 이유 일부)
    ("s03", "9053", "시흥", UNSURE, "중위소득"), ("s04", "9053", "시흥", UNSURE, "중위소득"), ("s05", "9053", "시흥", NO, "나이"), ("s01", "9053", "시흥", NO, "노원구 거주"),
    ("s06", "9096", "시흥", YES, ""), ("s07", "9096", "시흥", UNSURE, "거창군 지역"), ("s05", "9096", "시흥", NO, "나이"), ("s01", "9096", "시흥", NO, "거창군"),
    ("s08", "9066", "유성", UNSURE, "내신"), ("s08", "9066", "시흥", NO, "대전 소재 대학"), ("s09", "9066", "유성", NO, "학과 계열"), ("s10", "9066", "유성", NO, "신입생만"),
    ("s11", "9217", "시흥", YES, ""), ("s12", "9217", "시흥", NO, "대상 동"), ("s13", "9217", "시흥", NO, "대상 동"), ("s14", "9217", "시흥", UNSURE, "그리고"),
    ("s15", "9181", "시흥", YES, ""), ("s16", "9181", "시흥", NO, "원덕읍"), ("s17", "9178", "시흥", UNSURE, "유형별"), ("s18", "9178", "시흥", NO, "본인과 부모"),
    ("s20", "9141", "시흥", NO, "중국"), ("s01", "9141", "유성", NO, "중국"),
    ("s21", "9195", "시흥", NO, "휴학"), ("s28", "9195", "시흥", NO, "대학생 대상"), ("s01", "9195", "시흥", YES, ""), ("s08", "9195", "시흥", YES, ""),     # 국가 기준 Ⅰ유형을 확실히 충족하면 대학별 기준 유형은 필요 없다 (신입생은 성적 면제)
    ("s23", "9144", "시흥", UNSURE, "진급"), ("s01", "9144", "시흥", NO, "학년 4"), ("s24", "9144", "시흥", UNSURE, "선발된"), ("s21", "9144", "시흥", NO, "재학생"),
    ("s26", "9120", "시흥", UNSURE, "등록금"), ("s29", "9120", "시흥", NO, "2학기 이상 남음"), ("s01", "9120", "시흥", NO, "2학기 이상 남음"),
    ("s23", "9237", "시흥", YES, ""), ("s24", "9237", "시흥", YES, ""), ("s27", "9237", "시흥", UNSURE, "평균 3.6"), ("s01", "9237", "시흥", NO, "이수 7학기"), ("s29", "9237", "시흥", NO, "이수 8학기"),
    ("s02", "9212", "시흥", UNSURE, "ICT"), ("s01", "9212", "시흥", NO, "서초구"), ("s21", "9212", "시흥", NO, "재학생"),
    ("s25", "9098", "시흥", UNSURE, "문장 조건"), ("s01", "9098", "시흥", NO, "장애인"),
    ("s12", "9058", "성북", YES, ""), ("s06", "9058", "성북", NO, "누적 백분위"), ("s25", "9058", "성북", UNSURE, "누적 백분위"), ("s11", "9058", "성북", NO, "1~4구간"), ("s01", "9058", "시흥", NO, "서울 소재 대학"),
    ("s01", "9088", "시흥", NO, "비수도권"), ("s01", "9088", "해운대", UNSURE, "공고문"),
    ("s08", "9071", "유성", NO, "부모 경기도"), ("s01", "9071", "시흥", UNSURE, "부모 3년"), ("s25", "9071", "시흥", NO, "12학점"),
    ("s13", "9163", "시흥", UNSURE, "중소기업"), ("s01", "9163", "시흥", NO, "부모 인천"),
    ("s19", "9210", "시흥", UNSURE, "유형별"), ("s28", "9210", "시흥", NO, "고등학생·대학생"), ("s28", "9227", "시흥", NO, "고등학생·대학생"), ("s01", "9227", "시흥", UNSURE, "선원"),
    ("s30", "9053", "시흥", UNSURE, "확인 불가"), ("s30", "9058", "시흥", NO, "서울 소재 대학")]


@pytest.mark.parametrize("sid, idx, school, outcome, why", GOLDEN)
def test_golden_truth(by_key, sid, idx, school, outcome, why):
    t = by_key["{}:{}:{}".format(sid, idx, school)].truth
    assert t.outcome == outcome and any(why in r for r in t.reasons) if why else t.outcome == outcome, (t.outcome, t.reasons)


# ================================================================== 일치 시험에서 찾은 엔진 오류가 고쳐진 상태 (엔진의 기대 판정)
@pytest.mark.parametrize("key, verdict", [
    ("s06:9058:성북", NO_MATCH),             # 누적 백분위(80)가 기준(85) 미만: 직전학기 백분위(70)가 아니라 누적 백분위로 판정
    ("s12:9058:성북", MATCH),                # 누적 백분위 86 + 기초수급자 + 소득 1구간 + 서울 소재 대학: 충족
    ("s25:9058:성북", CONDITIONAL),          # 누적 백분위를 입력하지 않으면 확인 필요 (직전학기 백분위 62 로 탈락시키지 않는다)
    ("s10:9066:유성", NO_MATCH), ("s30:9066:유성", NO_MATCH),    # '신입생만 대상' (AI 가 구조화했지만 엔진이 읽지 않던 누락)
    ("s14:9217:시흥", CONDITIONAL),          # 신분은 충족인데 소득 6구간 초과: '그리고/또는' 모호 -> 거짓 탈락시키지 않음
    ("s20:9217:시흥", CONDITIONAL),          # 신분 미해당이지만 소득을 모르는 학생
    ("s07:9096:시흥", CONDITIONAL),          # 거창 고교 졸업('3년 재학'은 알 수 없음)을 충족으로 단정하던 오류
    ("s06:9096:시흥", MATCH),                # 모든 조건 충족은 충족 그대로
    ("s08:9066:유성", CONDITIONAL), ("s30:9053:시흥", CONDITIONAL)])
def test_engine_verdicts_after_the_fixes(by_key, key, verdict):
    assert by_key[key].verdict == verdict


def test_exact_detail_place_names_are_now_confirmed(by_key):
    for key in ("s15:9181:시흥", "s11:9217:시흥"):
        region = [c for c in by_key[key].evaluation.common if c.key == "region"][0]
        assert region.status == matcher.PASS and "세부" not in region.message, key
    for key in ("s16:9181:시흥", "s12:9217:시흥"):                                                                   # 이름이 다르면 미달로 단정하지 않고 확인 필요
        region = [c for c in by_key[key].evaluation.common if c.key == "region"][0]
        assert region.status == matcher.UNKNOWN, key


# ================================================================== 전체 일치 시험 (회귀 방지)
ACCEPTED = {
    # 알려진 불일치만 허용한다. 새 불일치가 생기면 이 시험이 실패한다. 이유를 바꿀 때는 이 표와 README 를 함께 고친다.
    # 9144 '3학년 진급 재학생' 거짓 탈락은 해소됨: 공고에 '진급' 표현이 있으면 다음 학년 대상도 확인 필요로 남긴다 (matcher_v3.check_grades_v3)
    "KNOWN_LIMIT": {("s16", "9181"), ("s12", "9217")},   # 읍·면·동 이름이 다른 학생: 법정동·행정동 표기 차이 때문에 미달로 단정하지 않는 설계
    "UNDERCONFIDENT": {("s15", "9181")},  # 다른 대상(원덕고 재학생)의 문장 조건이 공고 공통 자격에 담겨 조건부로 남음 (9217 의 고등학생 문장은 미달 유형 접두 문장 정리로 해소)
}


def test_the_full_matrix_has_only_the_accepted_mismatches(pairs):
    assert len(pairs) == 3000
    seen = {}
    for p in pairs:
        if p.klass != "OK":
            seen.setdefault(p.klass, set()).add((p.student.id, p.idx))
    assert seen == ACCEPTED, {k: seen.get(k, set()) ^ ACCEPTED.get(k, set()) for k in set(seen) | set(ACCEPTED)}
    c = X.counts(pairs)
    assert c["FALSE_ACCEPT_MATCH"] == 0 and c["OVERCONFIDENT"] == 0 and c["FALSE_ACCEPT_COND"] == 0                  # 거짓 통과·과신·조건부로 통과는 하나도 없다
    assert c["OK"] == 3000 - 10 - 5


def test_the_matrix_exercises_every_notice_with_different_truths(pairs):
    cov = X.coverage(pairs)
    assert sorted(cov) == sorted(DEFAULT_SAMPLE)
    for idx, c in cov.items():
        assert c[NO] > 0, idx
        if idx != "9141":                                                                                          # 9141 은 중국 소재 대학 한정이라 국내 학교의 모든 학생이 미달
            assert c[UNSURE] + c[YES] > 0, idx
    assert {idx for idx, c in cov.items() if c[YES] > 0} == {"9058", "9096", "9181", "9195", "9217", "9237"}              # 충족이 나오는 공고


def test_engine_never_rejects_a_student_the_truth_cannot_rule_out_except_the_accepted_case(pairs):
    rejects = {(p.student.id, p.idx) for p in pairs if p.verdict == NO_MATCH and p.truth.outcome != NO}
    assert rejects == set()
    unknown_student = [p for p in pairs if p.student.id == "s30" and p.verdict == NO_MATCH]                          # 정보가 거의 없는 학생은 확실한 사유(학교·학과 계열 등)로만 탈락
    assert all(p.truth.outcome == NO for p in unknown_student)


# ================================================================== 실행 스크립트
def test_cli_prints_the_report_and_pair_details(capsys):
    assert run_v3_matrix.main(["--width", "130"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("=== 일치 시험: 공고 20 × 학생 30 × 학교 5 = 3000쌍 ===") and "2985" in out and "[공고별]" in out and "[불일치] 0쌍" in out
    assert run_v3_matrix.main(["--show", "all", "--width", "130"]) == 0
    assert "[불일치] 15쌍" in capsys.readouterr().out
    assert run_v3_matrix.main(["--pair", "s14:9217"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("=== s14 × 9217 (인천시 SK행복 장학) | 학교 시흥 ===") and "분류: 일치 | 엔진 조건부 | 정답 불확실" in out and "'그리고'인지 '또는'인지 모호" in out and "원문: https://www.dreamspon.com/scholarship/view.html?idx=9217" in out
    assert "(다른 학교의 결과: --pair s14:9217:학교이름)" in out
    assert run_v3_matrix.main(["--pair", "s14:9217:성북"]) == 0 and "학교 성북" in capsys.readouterr().out


def test_cli_options_and_errors(capsys):
    assert run_v3_matrix.main(["--schools", "시흥,성북", "--width", "130"]) == 0
    assert "공고 20 × 학생 30 × 학교 2 = 1200쌍" in capsys.readouterr().out
    assert run_v3_matrix.main(["--schools", "없는학교"]) == 2 and "알 수 없는 학교 없는학교" in capsys.readouterr().err
    assert run_v3_matrix.main(["--pair", "s99:9217"]) == 2 and "해당하는 쌍이 없습니다" in capsys.readouterr().err
    assert run_v3_matrix.main(["--notices", "/nonexistent/dir"]) == 2 and "공고가 없습니다" in capsys.readouterr().err
    assert run_v3_matrix.main(["--format", "md", "--schools", "시흥"]) == 0
    assert "| 분류 | 쌍 | 비율 |" in capsys.readouterr().out


def test_strict_exit_code_reports_a_new_severe_mismatch(tmp_path, capsys):
    folder = tmp_path / "notices"
    shutil.copytree(X.default_dir() / "notices", folder)
    f = folder / "9096.json"
    rec = json.loads(f.read_text(encoding="utf-8"))
    rec["data"]["credits"]["min_last_semester"] = 40                                                               # 일부러 틀린 구조화: 정답은 12학점인데 엔진은 40학점을 요구
    f.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    assert run_v3_matrix.main(["--notices", str(folder), "--width", "130"]) == 0
    out = capsys.readouterr().out
    assert "거짓 탈락" in out and "s06" in out and "9096" in out
    assert run_v3_matrix.main(["--notices", str(folder), "--strict"]) == 1
    capsys.readouterr()
    assert run_v3_matrix.main(["--strict"]) == 0                                                                   # 지금 상태는 거짓 탈락·거짓 통과가 없으므로 엄격 모드도 통과한다


# ================================================================== 스키마 가드: AI 가 구조화한 조건을 엔진이 조용히 무시하지 않는다 (4-B2 감사에서 new_student_only·language_requirements 누락 발견)
ENGINE_FILES = ("matcher.py", "matcher_v3.py", "conditions_v3.py", "places.py")
# 판정에 쓰지 않고 표시·저장만 하는 필드 (이유를 적어 둔다). 여기에 없는 필드는 엔진 코드가 읽어야 한다.
NOT_JUDGED = {"scholarship_name": "이름", "foundation": "재단", "summary": "요약", "apply_start": "신청 기간(서비스가 처리)", "apply_end": "신청 기간(서비스가 처리)", "org_deadline": "재단 마감(서비스가 처리)",
              "apply_how": "신청 방법", "apply_type": "신청 유형", "amount": "금액", "recruit_count": "선발 인원", "documents": "제출서류(확인 사항)", "uncertain": "AI 의 불확실 표시(서비스 경고)",
              "category": "공고 분류(서비스 필터)", "selection": "선발 방법", "procedure": "선발 진행 절차(표시용)", "evaluation": "선발 평가 비율(표시용)", "criteria": "선발 기준(표시용)", "recommender": "추천자(표시용)", "evidence": "AI 가 인용한 근거 문구(검수용)", "deadline_kind": "추천 마감 종류(안내용)"}
SUBFIELDS_NOT_JUDGED = {("gpa", "scale_stated"): "만점 표기 여부(가정 안내용)", ("income", "bracket_term"): "구간 산정 학기"}


def engine_source():
    from pathlib import Path
    root = Path(matcher.__file__).parent
    return "".join((root / f).read_text(encoding="utf-8") for f in ENGINE_FILES)


def test_every_schema_condition_field_is_read_by_the_engine():
    from scholarship.common.ai.schema_v3 import TEMPLATE_V3
    src = engine_source()
    reads = lambda key: bool(re.search(r"""["']%s["']""" % re.escape(key), src))
    unread = [k for k in TEMPLATE_V3 if k not in NOT_JUDGED and not reads(k)]
    assert unread == [], "엔진이 읽지 않는 스키마 필드: {} (판정에 쓰거나 NOT_JUDGED 에 이유와 함께 적는다)".format(unread)
    sub = [(k, s) for k, v in TEMPLATE_V3.items() if isinstance(v, dict) and k not in NOT_JUDGED for s in v if not reads(s) and (k, s) not in SUBFIELDS_NOT_JUDGED]
    assert sub == [], "엔진이 읽지 않는 하위 필드: {}".format(sub)


def test_the_not_judged_lists_only_name_real_schema_fields():
    from scholarship.common.ai.schema_v3 import TEMPLATE_V3
    assert [k for k in NOT_JUDGED if k not in TEMPLATE_V3] == [] and [k for k, _ in SUBFIELDS_NOT_JUDGED if k not in TEMPLATE_V3] == []
    assert all(s in TEMPLATE_V3[k] for k, s in SUBFIELDS_NOT_JUDGED)                                                  # 오래된 항목이 남지 않게 한다
