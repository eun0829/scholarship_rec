# -*- coding: utf-8 -*-
import os

import pytest
from pathlib import Path

from scholarship.common.config import Settings, load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def test_env_example_loads_cleanly(monkeypatch):
    for k in list(os.environ):
        if k.startswith(("DB_", "EXT01_", "AI_", "ORACLE_", "SCHOLARSHIP_")):
            monkeypatch.delenv(k, raising=False)
    load_dotenv(str(ROOT / ".env.example"))
    s = Settings.from_env()
    assert s.oracle_client_lib is None                      # 주석만 있는 값이 경로로 읽히면 안 됨
    assert os.environ["EXT01_SITE_BASE"] == "https://tukorea.ac.kr"      # 줄 끝 주석 제거
    assert os.environ["EXT01_BBS_SETUP_SEQ"] == "374"
    assert s.ai_provider == "anthropic" and s.nas_root == Path("/info/ai_bundle/scholarship")
    assert s.ai_model == "claude-sonnet-5-5"
    assert s.ai_base_url == "https://factchat-cloud.mindlogic.ai/v1/gateway/claude" and s.ai_vision is True
    for k in [k for k in os.environ if k.startswith(("DB_", "EXT01_", "AI_", "ORACLE_", "SCHOLARSHIP_"))]:
        monkeypatch.delenv(k, raising=False)


def test_env_quotes_and_existing_values(tmp_path, monkeypatch):
    p = tmp_path / ".env"
    p.write_text('A_TEST="x y"\n# 주석\nB_TEST=1  # 설명\nC_TEST=\n', encoding="utf-8")
    monkeypatch.setenv("B_TEST", "keep")
    for k in ("A_TEST", "C_TEST"):
        monkeypatch.delenv(k, raising=False)
    load_dotenv(str(p))
    assert os.environ["A_TEST"] == "x y" and os.environ["B_TEST"] == "keep" and os.environ["C_TEST"] == ""
    monkeypatch.delenv("A_TEST"); monkeypatch.delenv("C_TEST")


def test_default_model_is_sonnet_5_5():
    from scholarship.common.config import Settings
    assert Settings().ai_model == "claude-sonnet-5-5"


def test_recommend_categories_default_and_parse(monkeypatch):
    from scholarship.common.config import Settings, parse_categories
    monkeypatch.delenv("RECOMMEND_CATEGORIES", raising=False)
    assert Settings.from_env().recommend_categories == ("OUT",)
    assert parse_categories("OUT, national") == ("OUT", "NATIONAL")
    monkeypatch.setenv("RECOMMEND_CATEGORIES", "OUT,NATIONAL")
    assert Settings.from_env().recommend_categories == ("OUT", "NATIONAL")
    for bad in ("INFO", "OUT,LOAN", "ETC"):
        with pytest.raises(ValueError):
            parse_categories(bad)
