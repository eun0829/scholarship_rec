# -*- coding: utf-8 -*-
import json
import shutil
from pathlib import Path

import run_v3_repro as R

FIX = Path(__file__).parent / "fixtures" / "ext01_v3"
STUDENT = Path(__file__).parent.parent / "sample_student.txt"


def make_runs(tmp_path, tweak=None):
    for n in (1, 2, 3):
        d = tmp_path / "run{}".format(n)
        shutil.copytree(FIX, d)
    if tweak:
        f = tmp_path / "run2" / "ext01_153930_ai_result.json"
        data = json.loads(f.read_text(encoding="utf-8"))
        tweak(data["elig"])
        f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_identical_runs_report_no_wobble(tmp_path, capsys):
    out = make_runs(tmp_path)
    assert R.main(["--compare-only", "--out-dir", str(out), "--ids", "153930", "153808"]) == 0
    text = capsys.readouterr().out
    assert "3회 비교" in text and "회차마다 같음" in text and "흔들림 있음 0건" in text


def test_changed_kind_and_category_are_reported(tmp_path, capsys):
    def tweak(e):
        e["unmodeled_conditions"][-1]["kind"] = "REVIEW"
        e["category"] = "OUT"
    out = make_runs(tmp_path, tweak)
    assert R.main(["--compare-only", "--out-dir", str(out), "--ids", "153930"]) == 0
    text = capsys.readouterr().out
    assert "흔들림 있음" in text and "unmodeled.kinds" in text and "category: NATIONAL | OUT | NATIONAL" in text and "흔들림 있음 1건" in text


def test_dry_plan_does_not_call_anything_and_shows_the_cost(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(R.subprocess, "call", lambda *a, **k: (_ for _ in ()).throw(AssertionError("AI 를 부르면 안 됨")))
    assert R.main(["--ids", "1", "2", "--runs", "3", "--dry-plan", "--out-dir", str(tmp_path)]) == 0
    assert "AI 호출 6건" in capsys.readouterr().out


def test_input_errors(tmp_path, capsys):
    assert R.main(["--out-dir", str(tmp_path)]) == 2
    assert R.main(["--ids", "1", "--runs", "1", "--out-dir", str(tmp_path)]) == 2
    assert R.main(["--compare-only", "--out-dir", str(tmp_path / "none"), "--ids", "1"]) == 2


def test_verdict_comparison_with_a_student(tmp_path, capsys):
    out = make_runs(tmp_path, lambda e: e.update(category="OUT", income={"max_bracket": 1.0, "eligible_types": [], "notes": None, "basis": "BRACKET", "bracket_term": "CURRENT_TERM"}))
    assert R.main(["--compare-only", "--out-dir", str(out), "--ids", "153930", "--student", str(Path(__file__).parent.parent / "sample_student.txt"), "--env", "/nonexistent"]) == 0
    text = capsys.readouterr().out
    assert "판정: " in text and "None" not in text
