# -*- coding: utf-8 -*-
"""run_recommend --profile-text 와 make_student_text: 텍스트 입력이 JSON 입력과 같은 추천 결과를 내는지, 오류 처리, 개인정보."""
import json
import shutil
from pathlib import Path

import pytest

import make_student_text
import run_recommend
from tests.test_recommend_service import _results_dir

FIX = Path(__file__).parent / "fixtures" / "profiles"
NOENV = ["--env", "/nonexistent/.env", "--today", "2026-10-06"]
# chuncheon_low_gpa_lowincome.json 과 같은 학생을 텍스트로
SAME_STUDENT = """# 학생정보
- 과정: 학사
- 학적상태: 재학
- 직전학기 평점: 2.0 / 4.5
- 직전학기 취득학점: 12
- 본인 주민등록: 강원특별자치도 춘천시
- 거주 기간: 3년 이상
- 기초생활수급자: 해당
"""


@pytest.fixture
def results(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    _results_dir(d)
    return str(d)


def run_json(args, capsys):
    assert run_recommend.main(args + ["--json"] + NOENV) == 0
    cap = capsys.readouterr()
    return json.loads(cap.out), cap.err


def test_text_and_json_profiles_give_the_same_recommendations(tmp_path, results, capsys):
    prof = tmp_path / "p.json"
    shutil.copy(FIX / "chuncheon_low_gpa_lowincome.json", prof)
    txt = tmp_path / "s.txt"
    txt.write_text(SAME_STUDENT, encoding="utf-8")
    a, _ = run_json(["--profile", str(prof), "--results-dir", results], capsys)
    b, err = run_json(["--profile-text", str(txt), "--results-dir", results], capsys)
    for key in ("counts", "recommended", "conditional", "excluded"):
        assert a[key] == b[key], key                                                                       # 같은 학생이면 같은 판정
    assert b["counts"]["conditional"] == 1 and b["conditional"][0]["matched_variants"][0]["name"] == "애향-저소득"
    assert "[학생 정보 텍스트] 읽은 항목 7개, 경고 0개" in err and "profile_parse" not in a
    assert {"GL", "CR", "RES", "RESY", "BASIC", "LV", "GRAD"} <= set(b["profile_parse"]["recognized"])
    assert b["profile_parse"]["warnings"] == [] and b["profile_notes"]["dropped_sensitive_keys"] == []


def test_text_mode_prints_the_parse_report_to_stderr_and_results_to_stdout(tmp_path, results, capsys):
    txt = tmp_path / "s.txt"
    txt.write_text(SAME_STUDENT + "- 소득구간: 둘째\n- 이름: 홍길동\n", encoding="utf-8")
    assert run_recommend.main(["--profile-text", str(txt), "--results-dir", results] + NOENV) == 0
    cap = capsys.readouterr()
    assert "장학금 추천 결과 (기준일 2026-10-06)" in cap.out and "[학생 정보 텍스트]" not in cap.out                # 결과와 보고서가 섞이지 않음
    assert "경고: 소득구간: 숫자 하나가 필요합니다" in cap.err and "받지 않은 항목: 이름" in cap.err
    assert "홍길동" not in cap.out + cap.err                                                                # 개인정보 값은 어디에도 출력되지 않음


def test_profile_options_are_exclusive_and_one_is_required(tmp_path, results):
    txt = tmp_path / "s.txt"
    txt.write_text(SAME_STUDENT, encoding="utf-8")
    with pytest.raises(SystemExit) as e1:
        run_recommend.main(["--profile", str(FIX / "siheung_mid_gpa.json"), "--profile-text", str(txt), "--results-dir", results] + NOENV)
    with pytest.raises(SystemExit) as e2:
        run_recommend.main(["--results-dir", results] + NOENV)
    assert e1.value.code == 2 and e2.value.code == 2


def test_text_input_errors_return_2(tmp_path, results, capsys):
    big = tmp_path / "big.txt"
    big.write_text("# 가" * 20000, encoding="utf-8")
    assert run_recommend.main(["--profile-text", str(big), "--results-dir", results] + NOENV) == 2
    err = capsys.readouterr().err
    assert "프로필 오류" in err and "너무 큽니다" in err
    assert run_recommend.main(["--profile-text", str(tmp_path / "none.txt"), "--results-dir", results] + NOENV) == 2
    assert "입력을 읽을 수 없습니다" in capsys.readouterr().err


def test_empty_text_still_runs_and_everything_is_unknown(tmp_path, results, capsys):
    txt = tmp_path / "e.txt"
    txt.write_text("# 아무것도 안 적음\n", encoding="utf-8")
    data, err = run_json(["--profile-text", str(txt), "--results-dir", results], capsys)
    assert data["profile_parse"]["recognized"] == [] and len(data["profile_parse"]["missing"]) >= 30 and data["counts"]["no_match"] == 0
    assert "읽은 항목 0개" in err


# ================================================================== make_student_text
def test_make_student_text_template_and_out(tmp_path, capsys):
    assert make_student_text.main(["--template"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# 학생정보 입력 양식") and "- 직전학기 평점:" in out and "- 해당 항목:" in out and "- 학교 소재지:" not in out
    f = tmp_path / "양식.txt"
    assert make_student_text.main(["--template", "--out", str(f)]) == 0
    assert f.read_text(encoding="utf-8") == out and "저장:" in capsys.readouterr().out


def test_make_student_text_from_json_drops_school_items_and_reads_back(tmp_path, capsys):
    assert make_student_text.main(["--from-json", str(FIX / "siheung_mid_gpa.json")]) == 0
    text = capsys.readouterr().out
    assert "- 직전학기 평점: 3.2 / 4.5" in text and "- 학년: 2" in text and "- 본인 주민등록: 경기도 시흥시" in text
    assert "학교" not in text.replace("직전학기", "")                                                       # 학교 고정 항목(소재지·종류)은 학생 텍스트에 넣지 않음
    f = tmp_path / "t.txt"
    f.write_text(text, encoding="utf-8")
    assert make_student_text.main(["--check", str(f)]) == 0
    assert "경고 0개" in capsys.readouterr().out


def test_make_student_text_check_reports_and_errors(tmp_path, capsys):
    f = tmp_path / "t.txt"
    f.write_text("- 소득구간: 둘째\n- 이수학기: 7\n- 연락처: 010-1234-5678\n", encoding="utf-8")
    assert make_student_text.main(["--check", str(f)]) == 0
    out = capsys.readouterr().out
    assert "읽은 항목 1개, 경고 1개" in out and "받지 않은 항목: 연락처" in out and "010-1234-5678" not in out
    assert make_student_text.main(["--check", str(tmp_path / "none.txt")]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"gpa_last": 9, "gpa_scale": 4.5}), encoding="utf-8")
    assert make_student_text.main(["--from-json", str(bad)]) == 2
    assert make_student_text.main(["--template", "--registry", str(tmp_path / "no.json")]) == 2
    err = capsys.readouterr().err
    assert "범위를 벗어남" in err and "찾을 수 없음" in err
    with pytest.raises(SystemExit):
        make_student_text.main([])
