# -*- coding: utf-8 -*-
"""항목 사전 점검 명령(check_registry.py)."""
import json

import check_registry
from scholarship.common import registry as R


def test_check_default_registry_passes(capsys):
    assert check_registry.main([]) == 0
    out = capsys.readouterr().out
    assert "항목 사전 점검 통과" in out and "등급: 필수 14개, 권장 16개, 선택 10개" in out
    assert "범용 비교" in out and "코드로 만든 비교 함수" in out and "gpa" in out and "special 8" in out         # 함수별 항목 수도 보여줌


def test_check_shows_prompt_section(capsys):
    assert check_registry.main(["--show-prompt"]) == 0
    out = capsys.readouterr().out
    assert "[v3 프롬프트] 전체" in out and "직전학기 평점 (GL)" in out


def test_check_reports_errors_and_fails(tmp_path, capsys):
    data = json.loads(R.default_path().read_text(encoding="utf-8"))
    data["items"][0]["quesion"] = "오타"
    data["items"][1]["code"] = data["items"][2]["code"]
    f = tmp_path / "r.json"
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert check_registry.main(["--file", str(f)]) == 1
    err = capsys.readouterr().err
    assert "점검 실패" in err and "알 수 없는 키" in err and "중복됨" in err                                     # 오류를 한꺼번에 알려 줌
    assert check_registry.main(["--file", str(tmp_path / "none.json")]) == 1
