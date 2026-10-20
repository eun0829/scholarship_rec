# -*- coding: utf-8 -*-
"""정확도 자동 점검(audit, population, run_accuracy): 학생 생성, 불변 규칙, 추출 품질 검사, 분포, 기준선 비교, 보고서."""
import json
import pathlib

import pytest

import run_accuracy
from scholarship.recommend import audit as A
from scholarship.recommend import matcher as M
from scholarship.recommend import population as P

FIX = pathlib.Path(__file__).parent / "fixtures" / "elig_incheon_152953.json"


def elig():
    return json.loads(FIX.read_text(encoding="utf-8"))


def notices():
    e = elig()
    return {"EXT01:152953": {"src_cd": "EXT01", "src_id": "152953", "title": "인천 장학", "apply_end": "2026-10-08", "summary": e["summary"], "elig": e}}


def test_generate_is_deterministic_and_sized():
    a, b = P.generate(60, seed=1), P.generate(60, seed=1)
    assert len(a) == 60 and [s.desc for s in a] == [s.desc for s in b]
    assert [s.desc for s in a] != [s.desc for s in P.generate(60, seed=2)]
    assert len({s.id for s in a}) == 60


def test_personas_load():
    ps = P.load_personas()
    assert len(ps) >= 14 and ps[0].id.startswith("P")


def test_region_probes_cover_required_areas():
    probes = P.region_probes(notices())
    assert any("인천광역시" in s.desc for s in probes)
    assert any(s.profile.residence_sido == "인천광역시" for s in probes)


def test_lint_finds_known_problems():
    codes = {i["code"] for i in A.lint_notice(notices()["EXT01:152953"])}
    assert "PREFIX_IN_COMMON" in codes and "REGION_NO_SIDO" in codes


def test_lint_flags_missing_scale_and_amount():
    e = {"schema_version": 3, "gpa": {"min": 3.0, "scale": None}, "summary": "1인당 100만원 지급", "variants": []}
    codes = {i["code"] for i in A.lint_notice({"elig": e, "apply_end": "2026-12-01", "summary": e["summary"]})}
    assert {"GPA_NO_SCALE", "AMOUNT_MISSING"} <= codes


def test_report_runs_and_has_all_parts():
    rep = A.build_report(notices(), P.generate(20, seed=3), "시흥")
    assert rep["pairs"] > 0 and not rep["crashes"] and not rep["invariants"]
    assert rep["unknown_ranking"] and rep["lint_summary"] and rep["per_notice"]
    assert 0 <= rep["decided_rate"] <= 1


def test_invariant_violation_is_detected(monkeypatch):
    real = A.evaluate_one

    def bad(el, prof):
        ev = real(el, prof)
        if prof.gpa_last is not None:                                          # 성적이 높을수록 일부러 미달로 만든다
            ev.verdict = M.NO_MATCH if prof.gpa_last >= 4.0 else M.MATCH
        return ev
    monkeypatch.setattr(A, "evaluate_one", bad)
    st = [s for s in P.generate(60, seed=5) if s.profile.gpa_last is not None and 3.5 <= s.profile.gpa_last < 4.0][:5]
    assert st
    v = A.check_invariants(notices(), st, "시흥")
    assert any(x["rule"] == "gpa-up" for x in v)


def test_crash_is_reported_not_raised(monkeypatch):
    def boom(el, prof):
        raise ValueError("x")
    monkeypatch.setattr(A, "evaluate_one", boom)
    rows, crashes = A.run_grid(notices(), P.generate(2, seed=1), "시흥")
    assert not rows and len(crashes) == 2 and "ValueError" in crashes[0]["error"]


def test_diff_baseline_reports_new_and_fixed():
    base = {"decided_rate": 0.5, "invariants": [], "lint": {"a": [{"code": "X", "detail": ""}]}, "per_notice": []}
    rep = {"decided_rate": 0.6, "invariants": [], "lint": {"b": [{"code": "Y", "detail": ""}]}, "per_notice": []}
    d = A.diff_baseline(rep, base)
    assert d["new_lint"] == [("b", "Y")] and d["fixed_lint"] == [("a", "X")] and d["decided_rate"] == [0.5, 0.6]


def test_cli_writes_reports_and_baseline(tmp_path, capsys):
    nd = tmp_path / "n"
    nd.mkdir()
    (nd / "EXT01_152953.json").write_text(json.dumps(notices()["EXT01:152953"], ensure_ascii=False), encoding="utf-8")
    out, base = tmp_path / "o", tmp_path / "b.json"
    args = ["--notices", str(nd), "--students", "15", "--out", str(out), "--baseline", str(base), "--school", "시흥"]
    assert run_accuracy.main(args + ["--update-baseline", "--strict"]) == 0
    assert (out / "accuracy.md").exists() and (out / "accuracy.xlsx").exists() and base.exists()
    assert run_accuracy.main(args) == 0
    assert "기준선과 비교" in (out / "accuracy.md").read_text(encoding="utf-8")


def test_cli_without_notices_fails_cleanly(tmp_path):
    assert run_accuracy.main(["--notices", str(tmp_path / "none")]) == 2


def test_coverage_counts_structured_and_sentences():
    cv = A.coverage(notices())
    assert cv["structured"] > 0 and 0 < cv["overall"] <= 1
    assert all("고등학생" not in x["text"] for x in cv["sentence_list"])       # 고등학생 전용 문장은 세지 않는다
    assert cv["per_notice"][0]["notice"] == "EXT01:152953"
