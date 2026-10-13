# -*- coding: utf-8 -*-
"""쿼리 파일(config/ext01_query01.txt, ext01_query02.txt) 로더와 외부01 어댑터의 쿼리 사용 방식 검증."""
import os
from pathlib import Path

import pytest

from scholarship.common import sql_file
from scholarship.common.config import CONFIG_DIR, Settings
from scholarship.sources import ext01
from scholarship.sources.ext01 import Ext01Adapter, Ext01Config


# ------------------------------------------------------------------ sql_file 로더
def test_clean_removes_terminator_comments_and_blank_lines():
    sql = sql_file.clean("-- 설명\nSELECT 1 FROM dual -- 끝 주석\n;\n-- 뒤의 주석\n")
    assert sql == "SELECT 1 FROM dual"
    assert sql_file.clean("SELECT 1 FROM dual\n/\n") == "SELECT 1 FROM dual"


def test_semicolon_inside_string_is_not_terminator():
    assert sql_file.clean("SELECT ';' x, 'it''s -- not comment' y FROM dual;") == \
        "SELECT ';' x, 'it''s -- not comment' y FROM dual"


def test_hints_and_block_comments():
    sql = sql_file.clean("SELECT /*+ INDEX(a ix1) */ /* 설명 */ a.x FROM t a")
    assert "/*+ INDEX(a ix1) */" in sql and "설명" not in sql


def test_multiple_statements_and_empty_rejected():
    with pytest.raises(sql_file.SqlFileError, match="하나만"):
        sql_file.clean("SELECT 1 FROM dual; SELECT 2 FROM dual;")
    with pytest.raises(sql_file.SqlFileError, match="주석만"):
        sql_file.clean("-- 주석만 있음\n/* 이것도 */")


def test_used_binds_ignores_comments_and_strings():
    sql = "SELECT '12:30' t, ':fake' f FROM x WHERE a = :setup -- :days 는 주석\n AND b = :DAYS"
    assert sql_file.used_binds(sql) == {"setup", "days"}
    assert sql_file.used_binds("SELECT 1 FROM dual -- :days") == set()


def test_render_tokens_and_validation(tmp_path):
    f = tmp_path / "q.txt"
    f.write_text("-- {DATE_COND} 는 주석이라 치환 대상이 아님\nSELECT 1 FROM t a WHERE a.s = :setup AND {DATE_COND};", encoding="utf-8")
    out = sql_file.render(f, {"DATE_COND": "a.d >= SYSDATE - :days"}, ["setup", "days"])
    assert out == "SELECT 1 FROM t a WHERE a.s = :setup AND a.d >= SYSDATE - :days"
    f.write_text("SELECT 1 FROM t WHERE x = {UNKNOWN_TOKEN}", encoding="utf-8")
    with pytest.raises(sql_file.SqlFileError, match="알 수 없는 치환 토큰"):
        sql_file.render(f, {"DATE_COND": "x"}, ["setup"])
    f.write_text("SELECT 1 FROM t WHERE x = :foo", encoding="utf-8")
    with pytest.raises(sql_file.SqlFileError, match=":foo"):
        sql_file.render(f, {}, ["setup", "days"])


def test_read_text_encodings_and_missing(tmp_path):
    f = tmp_path / "ansi.txt"
    f.write_bytes("-- 한글 주석\nSELECT '가' FROM dual".encode("cp949"))            # 메모장 ANSI 저장
    assert sql_file.clean(sql_file.read_text(f)) == "SELECT '가' FROM dual"
    g = tmp_path / "bom.txt"
    g.write_bytes(b"\xef\xbb\xbf" + "SELECT 1 FROM dual".encode("utf-8"))             # UTF-8 BOM
    assert sql_file.read_text(g).startswith("SELECT")
    with pytest.raises(sql_file.SqlFileError, match="없습니다"):
        sql_file.read_text(tmp_path / "nope.txt")


# ------------------------------------------------------------------ 기본 쿼리 파일
def test_default_files_render_for_both_column_types(settings):
    ad = Ext01Adapter(settings, Ext01Config())
    for typ, expect_to_char in (("DATE", False), ("TIMESTAMP(6)", False), ("VARCHAR2", True), ("CHAR", True)):
        q1, q2 = ad.render_queries(typ)
        assert ("TO_CHAR" in q1) == expect_to_char and ("TO_CHAR" in q2) == expect_to_char
        for q in (q1, q2):
            assert "{" not in q and "--" not in q and ";" not in q
            assert sql_file.used_binds(q) == {"setup", "days"}
        assert "TRASH_YN = 'N'" in q1 and "HOME.APP_BBS_ATCHMNFL" in q2


def test_default_paths_do_not_depend_on_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Ext01Config.from_env(Settings())
    assert cfg.query01_file.is_absolute() and cfg.query01_file.is_file() and cfg.query02_file.is_file()
    assert Settings().keywords_file.is_file() and CONFIG_DIR.is_dir()


def test_query_file_edits_apply_on_next_call(settings, tmp_path):
    q1, q2 = tmp_path / "q1.txt", tmp_path / "q2.txt"
    q1.write_text(Path(CONFIG_DIR / "ext01_query01.txt").read_text(encoding="utf-8"), encoding="utf-8")
    q2.write_text(Path(CONFIG_DIR / "ext01_query02.txt").read_text(encoding="utf-8"), encoding="utf-8")
    ad = Ext01Adapter(settings, Ext01Config(query01_file=q1, query02_file=q2))
    assert "OPEN_YN" not in ad.render_queries()[0]
    q1.write_text(q1.read_text(encoding="utf-8").replace("AND       a.TRASH_YN = 'N'",
                  "AND       a.TRASH_YN = 'N'\nAND       a.OPEN_YN = 'Y'"), encoding="utf-8")
    assert "a.OPEN_YN = 'Y'" in ad.render_queries()[0]                # 재시작 없이 바로 반영


def test_env_overrides_query_file_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("EXT01_QUERY01_FILE", str(tmp_path / "my1.txt"))
    assert Ext01Config.from_env(Settings()).query01_file == tmp_path / "my1.txt"


# ------------------------------------------------------------------ 어댑터가 쿼리를 쓰는 방식 (가짜 DB)
class FakeCursor:
    def __init__(self, conn):
        self.conn, self.description, self._rows = conn, None, []

    def execute(self, sql, binds=None):
        self.conn.executed.append((sql, dict(binds or {})))
        if "ALL_TAB_COLUMNS" in sql:
            self.description, self._rows = [("DATA_TYPE",)], [("DATE",)]
        elif "APP_BBS_ATCHMNFL" in sql:
            if self.conn.fail_on == "q2":
                raise RuntimeError("ORA-00942: table or view does not exist")
            self.description = [(c,) for c in self.conn.q2_cols]
            self._rows = self.conn.q2_rows
        else:
            self.description = [(c,) for c in self.conn.q1_cols]
            self._rows = self.conn.q1_rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeConn:
    def __init__(self):
        self.executed, self.fail_on = [], None
        self.q1_cols = ["BBS_ARTCL_SEQ", "SJ", "CN", "RGSDE", "ANNYMTY_WRITER"]
        self.q1_rows = [(153666, "제목", "<p>본문</p>", "20261001", "학생지원팀")]
        self.q2_cols = ["BBS_ATCHMNFL_SEQ", "BBS_ARTCL_SEQ", "ORGINL_NM", "SORT_NO"]
        self.q2_rows = [(9001, 153666, "공고문.hwp", 1)]

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        pass


def adapter_with(settings, conn, **cfg):
    ad = Ext01Adapter(settings, Ext01Config(**cfg))
    ad._conn = conn
    return ad


def test_fetch_with_default_files(settings):
    conn = FakeConn()
    notices = adapter_with(settings, conn, setup_seq="374").fetch_notices(30)
    assert notices[0].src_id == "153666" and notices[0].attachments[0].org_file_nm == "공고문.hwp"
    sqls = [e for e in conn.executed if "ALL_TAB_COLUMNS" not in e[0]]
    assert all(b == {"setup": "374", "days": 30} for _, b in sqls)


def test_only_binds_used_in_file_are_passed(settings, tmp_path):
    q1, q2 = tmp_path / "q1.txt", tmp_path / "q2.txt"
    q1.write_text("SELECT BBS_ARTCL_SEQ, SJ, CN, RGSDE FROM HOME.APP_BBS_ARTCL WHERE BBS_SETUP_SEQ = '374' AND RGSDE >= SYSDATE - 30;", encoding="utf-8")
    q2.write_text("SELECT * FROM HOME.APP_BBS_ATCHMNFL", encoding="utf-8")
    conn = FakeConn()
    conn.q1_cols = ["BBS_ARTCL_SEQ", "SJ", "CN", "RGSDE"]                      # ANNYMTY_WRITER 없이도 동작
    conn.q2_cols = ["BBS_ATCHMNFL_SEQ", "BBS_ARTCL_SEQ", "ORGINL_NM"]          # SORT_NO 없이도 동작
    conn.q2_rows = [(9001, 153666, "a.hwp")]
    out = adapter_with(settings, conn, query01_file=q1, query02_file=q2).fetch_notices(30)
    assert out[0].dept_nm == "" and out[0].attachments[0].sort_no == 0
    assert [b for s, b in conn.executed if "ALL_TAB" not in s] == [{}, {}]     # 바인드를 쓰지 않으면 넘기지 않음


def test_columns_are_matched_by_name_not_position(settings, tmp_path):
    q1 = tmp_path / "q1.txt"
    q1.write_text("SELECT a.RGSDE, a.CN, a.SJ AS SJ, a.BBS_ARTCL_SEQ AS BBS_ARTCL_SEQ FROM HOME.APP_BBS_ARTCL a WHERE a.BBS_SETUP_SEQ = :setup AND {DATE_COND}", encoding="utf-8")
    conn = FakeConn()
    conn.q1_cols = ["RGSDE", "CN", "SJ", "BBS_ARTCL_SEQ"]
    conn.q1_rows = [("20261001", "<p>x</p>", "순서가 바뀐 제목", 777)]
    conn.q2_rows = []
    out = adapter_with(settings, conn, query01_file=q1).fetch_notices(30)
    assert out[0].src_id == "777" and out[0].title == "순서가 바뀐 제목" and out[0].posted_date.day == 1


def test_missing_required_columns_message(settings, tmp_path):
    q1 = tmp_path / "ext01_query01.txt"
    q1.write_text("SELECT a.BBS_ARTCL_SEQ, a.SJ FROM HOME.APP_BBS_ARTCL a WHERE a.BBS_SETUP_SEQ = :setup", encoding="utf-8")
    conn = FakeConn()
    conn.q1_cols = ["BBS_ARTCL_SEQ", "SJ"]
    with pytest.raises(sql_file.SqlFileError) as ei:
        adapter_with(settings, conn, query01_file=q1).fetch_notices(30)
    msg = str(ei.value)
    assert "ext01_query01.txt" in msg and "CN" in msg and "RGSDE" in msg and "AS" in msg


def test_execution_error_names_the_file(settings):
    conn = FakeConn()
    conn.fail_on = "q2"
    with pytest.raises(RuntimeError, match=r"ext01_query02\.txt.*ORA-00942"):
        adapter_with(settings, conn).fetch_notices(30)


def test_bad_query_file_fails_before_touching_db(settings, tmp_path):
    q1 = tmp_path / "q1.txt"
    q1.write_text("SELECT 1 FROM dual WHERE x = :nope", encoding="utf-8")
    conn = FakeConn()
    ad = adapter_with(settings, conn, query01_file=q1)
    with pytest.raises(sql_file.SqlFileError, match=":nope"):
        ad.fetch_notices(30)
    assert all("ALL_TAB_COLUMNS" in s for s, _ in conn.executed)               # 본 쿼리는 실행되지 않음
