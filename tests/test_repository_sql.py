# -*- coding: utf-8 -*-
"""OracleRepository 가 만드는 SQL/바인드 점검 (실제 DB 없이 커서 호출만 기록).
※ 실제 Oracle 11g 에서의 실행은 별도 확인 필요. 실제 실행에서 ORA-22284(중복 LONG 바인드)가 나서 MERGE -> UPDATE/INSERT 로 바꿨고,
   그 회귀를 막기 위해 '각 문장에서 같은 바인드 이름이 두 번 나오지 않는다'를 검사한다."""
import re
from datetime import date

import pytest

from scholarship.common.repository import OracleRepository


class FakeCursor:
    def __init__(self, conn):
        self.conn, self.rowcount = conn, 0

    def setinputsizes(self, **kw):
        self.conn.log.append(("sizes", kw))

    def execute(self, sql, binds=None, **kw):
        self.conn.log.append(("exec", sql, binds if binds is not None else kw))
        if sql.startswith("UPDATE SCHOLARSHIP_NOTICE") and " SET " in sql and "USE_YN = :y" not in sql:
            self.rowcount = self.conn.update_rowcount
        else:
            self.rowcount = 1

    def fetchone(self):
        return ("abc", "EXTRACTED", "Y", None, None)

    def fetchall(self):
        return [("1",), ("2",)]

    def close(self):
        pass


class FakeConn:
    def __init__(self, update_rowcount=0):
        self.log, self.commits, self.update_rowcount = [], 0, update_rowcount

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1


def execs(conn):
    return [(e[1], e[2]) for e in conn.log if e[0] == "exec"]


def assert_no_duplicate_binds(sql):
    names = re.findall(r":(\w+)", sql)
    assert len(names) == len(set(names)), "같은 바인드 이름이 두 번 이상 나옴 (ORA-22284 위험): {}".format(sql)


def test_new_row_runs_update_then_insert():
    conn = FakeConn(update_rowcount=0)
    OracleRepository(conn).upsert_notice("EXT01", "153666", {
        "title": "제목", "body_text": "본" * 5000, "apply_end": date(2026, 10, 25), "summary": "",
        "status_cd": "EXTRACTED", "evil; DROP TABLE x": "1"})
    (upd, ub), (ins, ib) = execs(conn)
    assert upd.startswith("UPDATE SCHOLARSHIP_NOTICE SET ") and "UPDATED_AT = SYSDATE" in upd
    assert "WHERE SRC_CD = :k_SRC_CD AND SRC_ID = :k_SRC_ID" in upd
    assert ins.startswith("INSERT INTO SCHOLARSHIP_NOTICE (SRC_CD, SRC_ID,") and ":k_SRC_CD" in ins
    for sql, binds in ((upd, ub), (ins, ib)):
        assert_no_duplicate_binds(sql)                                      # 긴 값(CLOB) 중복 바인드 금지
        assert set(re.findall(r":(\w+)", sql)) == set(binds)                # SQL 의 모든 바인드가 값을 가짐
        assert "DROP" not in sql and "evil" not in str(binds)               # 허용 컬럼 외 키는 무시
        assert binds["summary"] is None and binds["k_SRC_ID"] == "153666"   # 빈 문자열 -> NULL
    clob = __import__("oracledb").DB_TYPE_CLOB
    assert [e[1] for e in conn.log if e[0] == "sizes"] == [{"body_text": clob}, {"body_text": clob}]   # 실행마다 지정
    assert conn.commits == 1


def test_existing_row_runs_update_only():
    conn = FakeConn(update_rowcount=1)
    OracleRepository(conn).upsert_notice("EXT01", "1", {"status_cd": "FAILED"})
    assert len(execs(conn)) == 1 and execs(conn)[0][0].startswith("UPDATE") and conn.commits == 1


def test_file_upsert_composite_key_and_long_text():
    conn = FakeConn(update_rowcount=0)
    OracleRepository(conn).upsert_file("EXT01", "1", "77", {"org_file_nm": "a.hwp", "extract_text": "가" * 20000,
                                                           "status_cd": "EXTRACTED", "text_len": 20000})
    (upd, ub), (ins, ib) = execs(conn)
    assert "SCHOLARSHIP_NOTICE_FILE" in upd and "FILE_SEQ = :k_FILE_SEQ" in upd and ub["k_FILE_SEQ"] == "77"
    assert_no_duplicate_binds(upd)
    assert_no_duplicate_binds(ins)
    assert ib["extract_text"] == "가" * 20000


def test_empty_update_still_touches_timestamp():
    conn = FakeConn(update_rowcount=1)
    OracleRepository(conn).upsert_notice("EXT01", "1", {"unknown_col": "x"})      # 허용 컬럼이 하나도 없을 때
    assert execs(conn)[0][0].startswith("UPDATE SCHOLARSHIP_NOTICE SET UPDATED_AT = SYSDATE WHERE")


def test_state_and_activation_queries():
    conn = FakeConn()
    repo = OracleRepository(conn)
    assert repo.get_notice_state("EXT01", "1") == {"content_hash": "abc", "status_cd": "EXTRACTED", "use_yn": "Y",
                                                   "src_fields_json": None, "elig_json": None}
    assert repo.list_active_ids_since("EXT01", date(2026, 9, 6)) == ["1", "2"]
    repo.set_use_yn("EXT01", ["1", "2"], "N")
    assert sum(1 for s, _ in execs(conn) if "UPDATE SCHOLARSHIP_NOTICE SET USE_YN" in s) == 2


def test_find_ids_by_title_sql():
    conn = FakeConn()
    ids = OracleRepository(conn).find_ids_by_title("EXT01", "2026년 하나장학생 선발 안내", "2")
    sql, binds = execs(conn)[0]
    assert ids == ["1", "2"] or ids  # FakeCursor.fetchall 결과 사용
    assert "USE_YN = 'Y'" in sql and "SRC_ID <> :i" in sql and "REGEXP_REPLACE(TRIM(TITLE)" in sql
    assert binds == {"c": "EXT01", "i": "2", "t": "2026년 하나장학생 선발 안내"}
    assert_no_duplicate_binds(sql)


# ================================================================== 외부02: SRC_FIELDS_JSON, 전체 활성 글 조회, 컬럼 없는 환경
def test_upsert_notice_with_src_fields_json_uses_clob_without_duplicate_binds():
    conn = FakeConn(update_rowcount=0)
    OracleRepository(conn).upsert_notice("EXT02", "9218", {"title": "t", "src_fields_json": '{"period": {"end": "2026-10-08"}}' * 300,
                                                          "elig_json": "{}", "status_cd": "COLLECTED"})
    (upd, ub), (ins, ib) = execs(conn)
    clob = __import__("oracledb").DB_TYPE_CLOB
    assert "src_fields_json = :src_fields_json" in upd and "src_fields_json" in ins and ib["src_fields_json"].startswith('{"period"')
    sizes = [e[1] for e in conn.log if e[0] == "sizes"]
    assert all(s.get("src_fields_json") == clob and s.get("elig_json") == clob for s in sizes)
    assert_no_duplicate_binds(upd)
    assert_no_duplicate_binds(ins)


def test_list_active_ids_with_and_without_posted_date_window():
    conn = FakeConn()
    repo = OracleRepository(conn)
    repo.list_active_ids_since("EXT02", None)
    repo.list_active_ids_since("EXT01", date(2026, 9, 1))
    (all_sql, all_binds), (win_sql, win_binds) = execs(conn)
    assert "POSTED_DATE" not in all_sql and all_binds == {"c": "EXT02"}                                      # 전체 활성 글
    assert "POSTED_DATE >= :d" in win_sql and win_binds == {"c": "EXT01", "d": date(2026, 9, 1)}


def test_memory_repository_active_ids_and_state_include_new_fields():
    from scholarship.common.repository import MemoryRepository
    repo = MemoryRepository()
    repo.upsert_notice("EXT02", "1", {"status_cd": "EXTRACTED", "posted_date": None, "src_fields_json": "{}", "elig_json": "{x}", "content_hash": "h"})
    repo.upsert_notice("EXT02", "2", {"status_cd": "EXTRACTED", "posted_date": date(2020, 1, 1)})
    repo.set_use_yn("EXT02", ["2"], "N")
    assert sorted(repo.list_active_ids_since("EXT02", None)) == ["1"]
    assert repo.get_notice_state("EXT02", "1") == {"content_hash": "h", "status_cd": "EXTRACTED", "use_yn": "Y",
                                                   "src_fields_json": "{}", "elig_json": "{x}"}


class _NoColumnCursor:
    """SRC_FIELDS_JSON 컬럼이 아직 없는 DB (ALTER 적용 전)를 흉내낸다."""
    rowcount = 1

    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, binds=None, **kw):
        self.conn.sqls.append(sql)
        if "SRC_FIELDS_JSON" in sql.upper():                                    # 컬럼명 대소문자와 무관하게 존재하지 않는 컬럼으로 취급
            raise RuntimeError('ORA-00904: "SRC_FIELDS_JSON": invalid identifier')

    def fetchone(self):
        return ("hash1", "EXTRACTED", "Y", '{"apply_end": "2026-10-08"}')

    def close(self):
        pass


class _NoColumnConn:
    def __init__(self):
        self.sqls, self.commits = [], 0

    def cursor(self):
        return _NoColumnCursor(self)

    def commit(self):
        self.commits += 1


def test_missing_src_fields_column_falls_back_and_keeps_working(caplog):
    conn = _NoColumnConn()
    repo = OracleRepository(conn)
    with caplog.at_level("WARNING"):
        st = repo.get_notice_state("EXT01", "1")
    assert st == {"content_hash": "hash1", "status_cd": "EXTRACTED", "use_yn": "Y", "src_fields_json": None,
                  "elig_json": '{"apply_end": "2026-10-08"}'}
    assert repo._has_src_fields is False and "SRC_FIELDS_JSON 컬럼이 없습니다" in caplog.text
    n_before = len(conn.sqls)
    repo.upsert_notice("EXT01", "1", {"title": "t", "src_fields_json": "{}", "status_cd": "COLLECTED"})        # 컬럼이 없으면 이 값은 건너뜀
    assert all("SRC_FIELDS_JSON" not in s.upper() for s in conn.sqls[n_before:]) and len(conn.sqls) > n_before
    repo.get_notice_state("EXT01", "1")
    assert all("SRC_FIELDS_JSON" not in s.upper() for s in conn.sqls[-1:])                                   # 다음부터는 처음부터 구버전 쿼리


def test_other_database_errors_are_not_swallowed():
    class Boom(_NoColumnCursor):
        def execute(self, sql, binds=None, **kw):
            raise RuntimeError("ORA-01017: invalid username/password")

    class C(_NoColumnConn):
        def cursor(self):
            return Boom(self)

    with pytest.raises(RuntimeError, match="ORA-01017"):
        OracleRepository(C()).get_notice_state("EXT02", "1")
