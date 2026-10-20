# -*- coding: utf-8 -*-
import io
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import set_secret
from scholarship.common import secret_store as ss
from scholarship.common.config import Settings, bootstrap

ROOT = Path(__file__).resolve().parent.parent
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX 권한 비트 검사")
SECRET_KEYS = ["DB_PASSWORD", "EXT01_DB_PASSWORD", "AI_API_KEY", "SCHOLARSHIP_SECRET_FILE", "DB_USER", "DB_DSN",
               "EXT01_DB_USER", "EXT01_DB_DSN"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in SECRET_KEYS:
        monkeypatch.delenv(k, raising=False)


def test_roundtrip_with_tricky_characters(tmp_path):
    f = tmp_path / "s" / "secrets.env"
    pw = 'pa#ss=wo rd"\'한글  #끝 '                       # '#', '=', 따옴표, 공백, 한글, 앞뒤 공백
    ss.write(f, {"DB_PASSWORD": pw, "AI_API_KEY": "sk-abc"})
    assert ss.read(f)["DB_PASSWORD"] == pw and ss.read(f)["AI_API_KEY"] == "sk-abc"


def test_merge_keeps_other_keys_and_delete(tmp_path):
    f = tmp_path / "secrets.env"
    ss.write(f, {"DB_PASSWORD": "a"})
    ss.write(f, {"AI_API_KEY": "b"})
    assert ss.read(f) == {"DB_PASSWORD": "a", "AI_API_KEY": "b"}
    ss.write(f, {"DB_PASSWORD": "c"})
    assert ss.read(f)["DB_PASSWORD"] == "c" and "AI_API_KEY" in ss.read(f)
    assert ss.delete(f, "AI_API_KEY") is True and ss.delete(f, "AI_API_KEY") is False
    assert list(ss.read(f)) == ["DB_PASSWORD"]


def test_rejects_bad_keys_and_values(tmp_path):
    f = tmp_path / "secrets.env"
    with pytest.raises(ValueError):
        ss.write(f, {"SOME_OTHER": "x"})
    with pytest.raises(ValueError):
        ss.write(f, {"DB_PASSWORD": "line1\nline2"})
    with pytest.raises(ValueError):
        ss.write(f, {"DB_PASSWORD": ""})
    assert not f.exists()


@posix_only
def test_file_and_dir_permissions_are_private(tmp_path):
    f = tmp_path / "dir" / "secrets.env"
    ss.write(f, {"DB_PASSWORD": "x"})
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert stat.S_IMODE(f.parent.stat().st_mode) == 0o700
    assert not list(f.parent.glob("*.tmp*"))                       # 임시 파일이 남지 않음


@posix_only
def test_loose_permission_is_refused_but_rewrite_fixes_it(tmp_path):
    f = tmp_path / "secrets.env"
    ss.write(f, {"DB_PASSWORD": "x"})
    os.chmod(f, 0o644)
    with pytest.raises(ss.SecretFileError, match="chmod 600"):
        ss.read(f)
    with pytest.raises(ss.SecretFileError):
        ss.load_into_environ(f)
    ss.write(f, {"AI_API_KEY": "k"})                               # 다시 저장하면 600 으로 복구
    assert stat.S_IMODE(f.stat().st_mode) == 0o600 and ss.read(f)["DB_PASSWORD"] == "x"


def test_load_into_environ_does_not_override(tmp_path, monkeypatch):
    f = tmp_path / "secrets.env"
    ss.write(f, {"DB_PASSWORD": "from-file", "AI_API_KEY": "key-file"})
    monkeypatch.setenv("DB_PASSWORD", "from-real-env")
    loaded = ss.load_into_environ(f)
    assert os.environ["DB_PASSWORD"] == "from-real-env" and os.environ["AI_API_KEY"] == "key-file"
    assert loaded == ["AI_API_KEY"]
    monkeypatch.delenv("AI_API_KEY")


def test_bootstrap_priority_env_over_secret_file_over_dotenv(tmp_path, monkeypatch):
    f = tmp_path / "secrets.env"
    ss.write(f, {"DB_PASSWORD": "secret-file", "AI_API_KEY": "secret-key"})
    dotenv = tmp_path / ".env"
    dotenv.write_text("DB_PASSWORD=from-dotenv\nAI_API_KEY=dotenv-key\nDB_USER=scott\n", encoding="utf-8")
    monkeypatch.setenv("AI_API_KEY", "real-env-key")
    bootstrap(str(f), str(dotenv))
    s = Settings.from_env()
    assert s.db_password == "secret-file"                          # 비밀 파일 > .env
    assert s.ai_api_key == "real-env-key"                          # 실제 환경변수 > 비밀 파일
    assert s.db_user == "scott"                                    # 비밀이 아닌 설정은 .env 에서
    for k in ("DB_PASSWORD", "DB_USER"):
        monkeypatch.delenv(k, raising=False)


def test_missing_secret_file_is_fine(tmp_path):
    assert ss.load_into_environ(tmp_path / "none.env") == []


def test_secrets_not_in_repr():
    s = Settings()
    s.db_password, s.ai_api_key = "TOP-SECRET-PW", "sk-TOP-SECRET"
    assert "TOP-SECRET" not in repr(s) and "TOP-SECRET" not in str(s)
    from scholarship.sources.ext01 import Ext01Config
    assert "SECRET" not in repr(Ext01Config(db_password="TOP-SECRET-PW"))


# ------------------------------------------------------------------ set_secret.py
def test_cli_stdin_show_delete(tmp_path, monkeypatch, capsys):
    f = str(tmp_path / "s.env")
    monkeypatch.setattr(sys, "stdin", io.StringIO("my-password\n"))
    assert set_secret.main(["DB_PASSWORD", "--stdin", "--file", f]) == 0
    assert ss.read(f)["DB_PASSWORD"] == "my-password"
    capsys.readouterr()
    assert set_secret.main(["--show", "--file", f]) == 0
    out = capsys.readouterr().out
    assert "저장됨 (11자)" in out and "my-password" not in out        # 값은 절대 출력하지 않음
    assert set_secret.main(["--delete", "DB_PASSWORD", "--file", f]) == 0
    assert ss.read(f) == {}
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    assert set_secret.main(["DB_PASSWORD", "--stdin", "--file", f]) == 2   # 빈 값 거부


def test_cli_stdin_requires_key(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("x\n"))
    assert set_secret.main(["--stdin", "--file", str(tmp_path / "s.env")]) == 2


def test_prompt_value_confirm_and_skip():
    answers = iter(["abc", "abd", "abc", "abc"])                    # 첫 시도 불일치 -> 재입력 -> 일치
    assert set_secret.prompt_value("DB_PASSWORD", ask=lambda _: next(answers)) == "abc"
    assert set_secret.prompt_value("AI_API_KEY", ask=lambda _: "") is None            # Enter = 건너뜀
    assert set_secret.prompt_value("AI_API_KEY", ask=lambda _: "sk-1") == "sk-1"      # API 키는 한 번만
    always_wrong = iter(["a", "b"] * 3)
    assert set_secret.prompt_value("DB_PASSWORD", ask=lambda _: next(always_wrong)) is None


def test_cli_interactive_flow(tmp_path, monkeypatch):
    f = str(tmp_path / "s.env")
    answers = iter(["pw1", "pw1", "", "sk-key", "", "", "", "", ""])      # DB_PASSWORD(2회) / EXT01 건너뜀 / AI 키 / 추천 API 키 건너뜀 / 드림스폰 ID·비밀번호 건너뜀 / OpenAI·Pinecone 키 건너뜀
    monkeypatch.setattr(set_secret.getpass, "getpass", lambda prompt="": next(answers))
    assert set_secret.main(["--file", f]) == 0
    assert ss.read(f) == {"DB_PASSWORD": "pw1", "AI_API_KEY": "sk-key"}


def test_connection_test_masks_password_and_reports(capsys):
    from scholarship.sources.ext01 import Ext01Config
    s = Settings()
    s.db_user, s.db_password, s.db_dsn = "svc", "SAVE-PW", "h:1521/a"
    cfg = Ext01Config(db_user="home_ro", db_password="HOME-PW", db_dsn="h:1521/b")

    def connect(user, pw, dsn, lib):
        class C:
            def close(self): pass
        if user == "home_ro":
            raise RuntimeError("ORA-01017: invalid credentials for HOME-PW")
        return C()

    failed = set_secret.test_connections(s, cfg, connect=connect)
    out = capsys.readouterr().out
    assert failed == ["홈페이지 DB"] and "[성공] 저장 DB" in out and "[실패] 홈페이지 DB" in out
    assert "HOME-PW" not in out and "SAVE-PW" not in out
    assert set_secret.test_connections(Settings(), Ext01Config(), connect=connect) == ["저장 DB", "홈페이지 DB"]


def test_cli_is_runnable_as_script(tmp_path):
    f = tmp_path / "s.env"
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "set_secret.py"), "AI_API_KEY", "--stdin", "--file", str(f)],
                       input="sk-from-pipe\n", capture_output=True, text=True, cwd=str(ROOT), env=env, encoding="utf-8")
    assert r.returncode == 0 and "sk-from-pipe" not in r.stdout + r.stderr and ss.read(f)["AI_API_KEY"] == "sk-from-pipe"


# ------------------------------------------------------------------ run_ext01.py 연동
def test_run_ext01_tells_user_what_is_missing(tmp_path, monkeypatch, capsys):
    import run_ext01
    monkeypatch.setattr(sys, "argv", ["run_ext01.py", "--secret-file", str(tmp_path / "none.env"),
                                      "--env", str(tmp_path / "none.env2"), "--no-ai", "--dry-run"])
    assert run_ext01.main() == 2
    err = capsys.readouterr().err
    assert "필요한 비밀 값이 없습니다" in err and "DB_PASSWORD" in err and "set_secret.py" in err


@posix_only
def test_run_ext01_refuses_loose_secret_file(tmp_path, monkeypatch, capsys):
    import run_ext01
    f = tmp_path / "s.env"
    ss.write(f, {"DB_PASSWORD": "x"})
    os.chmod(f, 0o644)
    monkeypatch.setattr(sys, "argv", ["run_ext01.py", "--secret-file", str(f), "--env", str(tmp_path / "n"), "--no-ai", "--dry-run"])
    assert run_ext01.main() == 2 and "chmod 600" in capsys.readouterr().err


def test_run_ext01_startup_failure_is_friendly_and_masked(tmp_path, monkeypatch, caplog):
    import logging
    import run_ext01
    from scholarship.sources.ext01 import Ext01Adapter
    f = tmp_path / "s.env"
    ss.write(f, {"DB_PASSWORD": "Sup3r#Secret"})

    def boom(self, days):
        raise RuntimeError("ORA-01017: invalid username/password; logon denied (Sup3r#Secret)")

    monkeypatch.setattr(Ext01Adapter, "fetch_notices", boom)
    monkeypatch.setattr(sys, "argv", ["run_ext01.py", "--secret-file", str(f), "--env", str(tmp_path / "n"),
                                      "--no-ai", "--dry-run"])
    with caplog.at_level(logging.ERROR):
        assert run_ext01.main() == 1
    text = caplog.text
    assert "ORA-01017" in text and "set_secret.py DB_PASSWORD" in text
    assert "Sup3r#Secret" not in text and "****" in text


def test_connection_test_names_empty_items(capsys):
    from scholarship.sources.ext01 import Ext01Config
    s = Settings()
    s.db_password = "PW-ONLY"                           # 사용자와 DSN 이 비어 있는 상황
    set_secret.test_connections(s, Ext01Config(db_password="PW-ONLY"), connect=lambda *a: None)
    out = capsys.readouterr().out
    assert "저장 DB: 비어 있는 항목 -> DB_USER, DB_DSN" in out
    assert "홈페이지 DB: 비어 있는 항목 -> EXT01_DB_USER 또는 DB_USER, EXT01_DB_DSN 또는 DB_DSN" in out
    assert "PW-ONLY" not in out


def test_cli_test_db_reports_env_and_secret_file_status(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(set_secret, "test_connections", lambda *a, **k: [])
    assert set_secret.main(["--test-db", "--file", str(tmp_path / "none.env"), "--env", str(tmp_path / ".env")]) == 0
    out = capsys.readouterr().out
    assert ".env" in out and "없음" in out and "set_secret.py 로 먼저 저장" in out


class _Resp:
    model, input_tokens, output_tokens = "claude-test-model", 12, 3


def test_test_ai_success_and_masks_key(capsys):
    s = Settings()
    s.ai_api_key = "sk-ant-SECRETKEY"
    seen = {}

    class C:
        def complete(self, system, user, images):
            seen.update(system=system, user=user, images=images)
            return _Resp()
    assert set_secret.test_ai(s, client_factory=lambda st: C()) is True
    out = capsys.readouterr().out
    assert "[성공] AI 호출" in out and "claude-test-model" in out and "SECRETKEY" not in out
    assert seen["images"] == [] and len(seen["user"]) < 10                 # 아주 짧은 호출


@pytest.mark.parametrize("err,hint", [
    ("HTTP 401: authentication_error invalid x-api-key sk-ant-SECRETKEY", "키가 올바르지 않거나 해지"),
    ("HTTP 402: Credit balance exhausted sk-ant-SECRETKEY", "--credits"),
    ("HTTP 403: Model 'x' is not enabled for your tenant. sk-ant-SECRETKEY", "--models"),
    ("HTTP 403: error code: 1010 sk-ant-SECRETKEY", "Cloudflare"),
    ("HTTP 404: Model 'claude-x' not found", "--models"),
    ("HTTP 413: Request body too large", "25MB"),
    ("HTTP 429: rate_limit", "60초에 120건"),
    ("요청 실패: connection refused", "방화벽"),
])
def test_test_ai_failure_hints_and_masking(err, hint, capsys):
    s = Settings()
    s.ai_api_key = "sk-ant-SECRETKEY"

    class C:
        def complete(self, *a):
            raise RuntimeError(err)
    assert set_secret.test_ai(s, client_factory=lambda st: C()) is False
    out = capsys.readouterr().out
    assert "[실패] AI" in out and hint in out and "SECRETKEY" not in out


def test_test_ai_skips_when_key_missing(capsys):
    assert set_secret.test_ai(Settings(), client_factory=lambda st: None) is False
    assert "AI_API_KEY" in capsys.readouterr().out


def test_test_ai_reports_bad_provider_and_missing_base_url(capsys):
    s = Settings()
    s.ai_api_key, s.ai_provider = "k-secret", "nonsense"
    assert set_secret.test_ai(s) is False                                   # 실제 make_client 사용
    out = capsys.readouterr().out
    assert "[실패] AI" in out and "AI_PROVIDER" in out and "k-secret" not in out
    s.ai_provider, s.ai_base_url = "gateway", ""
    assert set_secret.test_ai(s) is False
    assert "AI_BASE_URL" in capsys.readouterr().out


def test_cli_test_ai_wires_up(tmp_path, monkeypatch, capsys):
    f = tmp_path / "s.env"
    ss.write(f, {"AI_API_KEY": "sk-from-file"})
    got = {}
    monkeypatch.setattr(set_secret, "test_ai", lambda st, **k: got.setdefault("key", st.ai_api_key) and True)
    assert set_secret.main(["--test-ai", "--file", str(f), "--env", str(tmp_path / ".env")]) == 0
    assert got["key"] == "sk-from-file"                                     # 비밀 파일의 키가 설정으로 올라감
    assert "비밀 파일" in capsys.readouterr().out
    monkeypatch.delenv("AI_API_KEY", raising=False)
