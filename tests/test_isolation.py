# -*- coding: utf-8 -*-
"""테스트 격리: 이 PC 의 실제 비밀 파일·환경변수가 테스트에 섞이지 않는지 (실제 PC 에서 겪은 문제의 재발 방지)."""
import glob
import os
import re
import subprocess
import sys
from pathlib import Path

from scholarship.common import secret_store
from tests.conftest import ISOLATED_PREFIXES, ROOT


def test_home_is_an_empty_temp_folder_and_secret_file_is_not_read():
    home = Path.home()
    assert "home" in home.name and not (home / ".scholarship").exists()
    assert secret_store.default_path() == home / ".scholarship" / "secrets.env" and not secret_store.default_path().exists()


def test_every_env_name_the_code_reads_is_covered_by_isolation():
    """코드에 새 환경변수가 생겼는데 격리 목록에 접두어가 없으면 PC 의 실제 값이 테스트에 섞일 수 있다."""
    names = set()
    for f in glob.glob(str(ROOT / "scholarship" / "**" / "*.py"), recursive=True) + glob.glob(str(ROOT / "*.py")):
        s = open(f, encoding="utf-8").read()
        names |= set(re.findall(r'env_(?:str|int|bool|float)\(\s*"([A-Z][A-Z0-9_]+)"', s))
        names |= set(re.findall(r'os\.environ(?:\.get|\.setdefault)?\(\s*"([A-Z][A-Z0-9_]+)"', s))
        names |= set(re.findall(r'os\.getenv\(\s*"([A-Z][A-Z0-9_]+)"', s))
    names.discard("USERNAME")                                  # Windows 기본 변수 (우리 설정이 아님)
    uncovered = sorted(n for n in names if not n.startswith(ISOLATED_PREFIXES))
    assert len(names) > 30 and uncovered == [], uncovered


def test_suite_passes_even_when_pc_has_real_secrets_and_env(tmp_path):
    """실제 PC 처럼 홈에 비밀 파일이 있고 OS 환경변수가 설정되어 있어도, 비밀이 없다고 가정하는 테스트가 통과해야 한다."""
    home = tmp_path / "realhome"
    (home / ".scholarship").mkdir(parents=True)
    (home / ".scholarship" / "secrets.env").write_text(
        "DB_PASSWORD=real-db-password\nAI_API_KEY=real-ai-key\nDREAMSPON_ID=real@example.com\nDREAMSPON_PASSWORD=real-pw\n", encoding="utf-8")
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), DB_PASSWORD="env-db-password", AI_API_KEY="env-ai-key",
               ORACLE_CLIENT_LIB="/nonexistent/oci", DREAMSPON_ID="env@example.com", DREAMSPON_PASSWORD="env-pw", AI_SCHEMA_VERSION="3")
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_ext02_cli.py", "tests/test_secrets.py", "tests/test_config.py", "-q", "-x",
                        "-p", "no:cacheprovider"], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=240)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-500:]
