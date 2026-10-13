# -*- coding: utf-8 -*-
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIX = Path(__file__).parent / "fixtures"
SAMPLE_DIR = Path(os.environ.get("SAMPLE_HWP_DIR", "/mnt/user-data/uploads"))


# 코드가 읽는 환경변수의 접두어. 테스트가 이 PC 의 실제 비밀 값(DB 비밀번호, AI 키, 드림스폰 계정)이나 설정에 영향받지 않게 모두 지운다.
# (tests/test_isolation.py 가 코드에 새 환경변수가 생겼을 때 이 목록이 빠지지 않았는지 확인한다)
ISOLATED_PREFIXES = ("AI_", "DB_", "DREAMSPON_", "EXT01_", "EXT02_", "ITEM_REGISTRY", "KEYWORDS_", "MAX_", "ORACLE_",
                     "RECOMMEND_", "SCHOLARSHIP_", "SKIP_", "UPDATE_")


@pytest.fixture(autouse=True)
def isolate_user_environment(monkeypatch, tmp_path_factory):
    """모든 테스트에 자동 적용: 홈 폴더를 빈 임시 폴더로 바꿔 실제 ~/.scholarship/secrets.env 를 읽지 않게 하고,
    우리 코드가 읽는 환경변수를 지운다. 테스트가 필요한 값은 각 테스트에서 monkeypatch 로 직접 넣는다."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))              # POSIX
    monkeypatch.setenv("USERPROFILE", str(home))       # Windows (Path.home() 이 사용)
    for k in list(os.environ):
        if k.startswith(ISOLATED_PREFIXES):
            monkeypatch.delenv(k, raising=False)


@pytest.fixture
def fixture_dir():
    return FIX


@pytest.fixture
def sample_hwps():
    files = sorted(SAMPLE_DIR.glob("*.hwp")) if SAMPLE_DIR.is_dir() else []
    if not files:
        pytest.skip("샘플 HWP 없음 (SAMPLE_HWP_DIR 지정)")
    return files


@pytest.fixture
def settings(tmp_path):
    from scholarship.common.config import Settings
    s = Settings()
    s.nas_root = tmp_path / "nas"
    s.request_interval = 0
    s.keywords_file = ROOT / "config" / "file_keywords.json"
    return s
