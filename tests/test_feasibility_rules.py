# -*- coding: utf-8 -*-
"""실현 가능성 규칙 파일: 검증, 순서, 사용자 정의 규칙 반영, 환경변수 경로, 확인 도구."""
import copy
import json

import pytest

import check_feasibility_rules as CLI
from scholarship.common.config import Settings
from scholarship.recommend import feasibility as F
from scholarship.recommend import feasibility_rules as R
from scholarship.recommend.service import Recommender
from tests.test_recommend_service import Stub

BASE = json.loads(R.DEFAULT_FILE.read_text(encoding="utf-8"))


def write(tmp_path, data):
    p = tmp_path / "rules.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_default_file_is_valid():
    r = R.load_rules()
    assert r.school_majors == ("ENGINEERING",) and r.label("VERY_HARD") == "매우 어려움" and len(r.rules) >= 5


def test_all_errors_reported_together(tmp_path):
    bad = copy.deepcopy(BASE)
    bad["오타키"] = 1
    bad["학교_계열"] = ["ENGINEER"]
    bad["계열_키워드"]["AGRICULTURE"]["키워드"] = []
    bad["계열_키워드"]["FARM"] = {"키워드": ["a"]}
    bad["규칙"].append({"이름": "희소 외국어", "등급": "EASY", "포함": []})
    with pytest.raises(R.RulesError) as e:
        R.load_rules(write(tmp_path, bad))
    m = str(e.value)
    for needle in ("오타키", "학교_계열", "AGRICULTURE", "FARM", "이름이 중복", "등급은", "포함"):
        assert needle in m, needle


def test_missing_or_broken_file_is_an_error_not_a_silent_default(tmp_path):
    with pytest.raises(R.RulesError):
        R.load_rules(str(tmp_path / "none.json"))
    p = tmp_path / "x.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(R.RulesError):
        R.load_rules(str(p))


def test_first_matching_rule_wins_and_exclude_words_apply(tmp_path):
    d = copy.deepcopy(BASE)
    d["규칙"].insert(0, {"이름": "내 규칙", "등급": "HARD", "이유": "내가 정함", "포함": ["봉사"], "제외": ["교내"]})
    r = R.load_rules(write(tmp_path, d))
    assert F.classify_text("확인 필요: 봉사 활동 100시간", r, ()) == ("HARD", "내가 정함")
    assert F.classify_text("확인 필요: 교내 봉사 활동", r, ()) is None


def test_user_added_family_keyword_takes_effect(tmp_path):
    d = copy.deepcopy(BASE)
    d["계열_키워드"]["AGRICULTURE"]["키워드"].append("원예")
    r = R.load_rules(write(tmp_path, d))
    assert F.classify_text("확인 필요: 원예 분야 종사 학생", r, ("ENGINEERING",))[0] == "VERY_HARD"
    assert F.classify_text("확인 필요: 원예 분야 종사 학생", R.load_rules(), ("ENGINEERING",)) is None


def test_rules_file_path_setting_and_label_rename(tmp_path):
    d = copy.deepcopy(BASE)
    d["등급_이름"]["VERY_HARD"] = "사실상 불가"
    s = Settings()
    s.feasibility_rules_file = write(tmp_path, d)
    rec = Recommender(Stub([]), s)
    assert rec._rules.label("VERY_HARD") == "사실상 불가"
    assert rec._rules.version != R.load_rules().version                            # 규칙이 다르면 AI 캐시도 따로


def test_cli_validates_and_tests_a_sentence(capsys):
    assert CLI.main([]) == 0
    assert "규칙 파일 정상" in capsys.readouterr().out
    assert CLI.main(["졸업 후 농업계에 종사할 학생", "해당 지역 출신"]) == 0
    out = capsys.readouterr().out
    assert "[매우 어려움]" in out and "AI 보조 대상" in out


def test_cli_reports_bad_file(tmp_path, capsys):
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"등급_이름": {}}), encoding="utf-8")
    assert CLI.main(["--file", str(p)]) == 1
    assert "오류" in capsys.readouterr().out
