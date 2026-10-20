# -*- coding: utf-8 -*-
"""문장으로만 남은 조건을 항목 사전 힌트로 찾는 도구 (재추출 대상 선별)."""
import json

import pytest

import run_hint_scan
from scholarship.common.registry import load_registry
from scholarship.recommend import hint_scan as H

REG = load_registry()


def codes(elig, only=None):
    return {h["code"] for h in H.scan_notice(elig, REG, only)}


@pytest.mark.parametrize("text, code", [
    ("장학금 수혜 시점에 학사 2년 이상을 마쳐야 하며", "SEM"),
    ("학사 2년 이상 이수한 자", "SEM"),
    ("3학년을 수료한 자", "SEM"),
    ("「고등교육법」 제2조 중 국내의 대학의 신입생에 한함", "ENT"),
    ("신입생만 지원 가능", "ENT"),
    ("최종 학년 재학생(휴학생을 포함)", "YEAR"),
])
def test_known_sentences_are_found(text, code):
    assert code in codes({"other_conditions": [text]}, [code])


def test_sentence_without_hint_is_not_reported():
    assert codes({"other_conditions": ["봉사활동 경험이 있는 자"]}, ["SEM", "ENT", "YEAR"]) == set()


def test_item_already_structured_is_skipped():
    e = {"other_conditions": ["학사 2년 이상 이수한 자"], "completed_terms": {"min": 4, "max": None}}
    assert "SEM" not in codes(e)


def test_variant_sentences_are_scanned_with_their_scope():
    e = {"variants": [{"name": "A유형", "other_conditions": ["신입생에 한함"]}], "other_conditions": []}
    hits = H.scan_notice(e, REG, ["ENT"])
    assert hits and hits[0]["scope"] == "A유형"


def test_duplicate_texts_are_reported_once():
    e = {"other_conditions": ["신입생에 한함"], "unmodeled_conditions": [{"text": "신입생에 한함", "kind": "ELIGIBILITY"}]}
    assert len(H.scan_notice(e, REG, ["ENT"])) == 1


def test_only_eligibility_sentences_are_scanned():
    e = {"unmodeled_conditions": [{"text": "신입생 환영회 참석", "kind": "OBLIGATION"}]}
    assert codes(e, ["ENT"]) == set()


def test_reextract_commands_group_by_source():
    cmds = H.reextract_commands(["EXT01:153587", "EXT02:9227", "EXT01:152513"])
    assert cmds == ["python run_ext01.py --days 400 --only 152513 153587 --force", "python run_ext02.py --include-closed --only 9227 --force"]


def test_cli_prints_notices_and_commands(tmp_path, capsys):
    rec = {"src_cd": "EXT01", "src_id": "1", "title": "시험 공고", "elig": {"other_conditions": ["신입생에 한함"], "schema_version": 3}}
    (tmp_path / "EXT01_1.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    assert run_hint_scan.main(["--dir", str(tmp_path), "--codes", "ENT"]) == 0
    out = capsys.readouterr().out
    assert "EXT01:1" in out and "시험 공고" in out and "python run_ext01.py --days 400 --only 1 --force" in out


def test_cli_without_notices_fails_politely(tmp_path):
    assert run_hint_scan.main(["--dir", str(tmp_path / "none")]) == 2
