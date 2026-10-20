# -*- coding: utf-8 -*-
"""--results-dir (파일 공급원)에서도 원문 주소가 나온다: 글번호에서 만든 기본 주소."""
import json
import shutil
from datetime import date

from scholarship.recommend.file_source import FileSource, origin_url

FIX = "tests/fixtures/ext01_v3"


def test_origin_url_rules(monkeypatch):
    monkeypatch.delenv("EXT01_DOWN_BASE", raising=False)
    monkeypatch.delenv("EXT01_BBS_SETUP_SEQ", raising=False)
    assert origin_url("EXT01", "153808") == "https://www.tukorea.ac.kr/bbs/tukorea/374/153808/artclView.do"
    assert origin_url("ext02", "9235") == "https://www.dreamspon.com/scholarship/view.html?idx=9235"
    monkeypatch.setenv("EXT01_DOWN_BASE", "https://school.example/")
    monkeypatch.setenv("EXT01_BBS_SETUP_SEQ", "777")
    assert origin_url("EXT01", "1") == "https://school.example/bbs/tukorea/777/1/artclView.do"


def test_file_source_candidates_carry_the_url(tmp_path, monkeypatch):
    monkeypatch.delenv("EXT01_DOWN_BASE", raising=False)
    monkeypatch.delenv("EXT01_BBS_SETUP_SEQ", raising=False)
    shutil.copytree(FIX, tmp_path / "r")
    for f in (tmp_path / "r").glob("ext01_*_ai_result.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        d["elig"]["apply_end"] = "2099-01-01"
        f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    cands = FileSource(tmp_path / "r").list_candidates(date(2026, 10, 13))
    assert cands and all(c["origin_url"] == "https://www.tukorea.ac.kr/bbs/tukorea/374/{}/artclView.do".format(c["src_id"]) for c in cands)
