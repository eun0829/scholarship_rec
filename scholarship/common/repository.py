# -*- coding: utf-8 -*-
"""[COMMON] 저장소. 파이프라인은 Repository 인터페이스만 알고, Oracle 은 OracleRepository 가 담당한다.
(테스트/드라이런에서는 메모리 구현을 쓴다.)

rec 는 컬럼명(소문자) -> 값 의 dict. 허용 컬럼 목록에 없는 키는 무시한다 (SQL 인젝션 방지).
"""
from __future__ import annotations

import logging
import json
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger(__name__)

NOTICE_COLS = ["title", "dept_nm", "posted_date", "origin_url", "body_text", "category_cd", "apply_type_cd",
               "apply_start", "apply_end", "org_deadline", "elig_json", "uncertain_json", "summary",
               "content_hash", "status_cd", "extract_model", "extracted_at", "use_yn", "src_fields_json"]
NOTICE_CLOBS = ["body_text", "elig_json", "uncertain_json", "src_fields_json"]
FILE_COLS = ["sort_no", "org_file_nm", "file_ext", "file_size", "nas_path", "role_cd", "extract_method",
             "extract_text", "text_len", "status_cd", "err_msg"]
FILE_CLOBS = ["extract_text"]


def _as_date(v) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    return v if isinstance(v, date) else None


def build_candidate(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """DB 행(대문자 컬럼명 dict) -> 추천 단계가 쓰는 후보 dict. ELIG_JSON 을 해석할 수 없으면 None."""
    try:
        elig = json.loads(r["ELIG_JSON"]) if r.get("ELIG_JSON") else None
    except (TypeError, ValueError):
        elig = None
    if not isinstance(elig, dict):
        return None
    try:
        unc = json.loads(r["UNCERTAIN_JSON"]) if r.get("UNCERTAIN_JSON") else {}
    except (TypeError, ValueError):
        unc = {}
    return {"src_cd": r.get("SRC_CD"), "src_id": str(r.get("SRC_ID")), "title": r.get("TITLE") or "",
            "origin_url": r.get("ORIGIN_URL"), "posted_date": _as_date(r.get("POSTED_DATE")),
            "category": r.get("CATEGORY_CD"), "apply_type": r.get("APPLY_TYPE_CD"),
            "apply_start": _as_date(r.get("APPLY_START")), "apply_end": _as_date(r.get("APPLY_END")),
            "org_deadline": _as_date(r.get("ORG_DEADLINE")), "summary": r.get("SUMMARY"),
            "status": r.get("STATUS_CD"), "elig": elig, "uncertain": unc if isinstance(unc, dict) else {}}


class Repository(ABC):
    @abstractmethod
    def get_notice_state(self, src_cd: str, src_id: str) -> Optional[Dict[str, Any]]:
        """없으면 None. 있으면 {'content_hash','status_cd','use_yn','src_fields_json','elig_json'}"""

    @abstractmethod
    def upsert_notice(self, src_cd: str, src_id: str, rec: Dict[str, Any]) -> None: ...

    @abstractmethod
    def upsert_file(self, src_cd: str, src_id: str, file_seq: str, rec: Dict[str, Any]) -> None: ...

    @abstractmethod
    def list_active_ids_since(self, src_cd: str, since: Optional[date]) -> List[str]:
        """since 가 None 이면 게시일과 무관하게 활성(USE_YN=Y) 글 전체."""

    @abstractmethod
    def set_use_yn(self, src_cd: str, ids: Iterable[str], yn: str) -> None: ...

    @abstractmethod
    def find_ids_by_title(self, src_cd: str, title_norm: str, exclude_id: str) -> List[str]:
        """같은 제목(공백 정리 후)을 가진 '활성(USE_YN=Y)' 다른 글의 글번호 목록."""

    @abstractmethod
    def list_candidates(self, today: date) -> List[Dict[str, Any]]:
        """추천 후보: 사용 중, 교외, 추출 완료(EXTRACTED/NEED_REVIEW), 마감일이 오늘 이후인 공고 (build_candidate 형식)."""

    def get_notice_texts(self, pairs) -> Dict[tuple, Dict[str, Any]]:
        """챗봇이 근거로 쓰는 원문. {(src_cd, src_id): {"body": 본문, "files": [{"seq", "name", "role", "text"}]}}.
        첨부는 텍스트를 뽑은 것만, 제외(EXCLUDED)로 분류된 것은 뺀다. 읽기 전용."""
        return {}

    def close(self) -> None:
        pass


class MemoryRepository(Repository):
    """테스트/드라이런용."""

    def __init__(self):
        self.notices: Dict[tuple, Dict[str, Any]] = {}
        self.files: Dict[tuple, Dict[str, Any]] = {}

    def get_notice_state(self, src_cd, src_id):
        r = self.notices.get((src_cd, src_id))
        return None if r is None else {"content_hash": r.get("content_hash"), "status_cd": r.get("status_cd"),
                                       "use_yn": r.get("use_yn", "Y"), "src_fields_json": r.get("src_fields_json"),
                                       "elig_json": r.get("elig_json")}

    def upsert_notice(self, src_cd, src_id, rec):
        self.notices.setdefault((src_cd, src_id), {}).update({k: v for k, v in rec.items() if k in NOTICE_COLS})

    def upsert_file(self, src_cd, src_id, file_seq, rec):
        self.files.setdefault((src_cd, src_id, file_seq), {}).update({k: v for k, v in rec.items() if k in FILE_COLS})

    def list_active_ids_since(self, src_cd, since):
        return [i for (c, i), r in self.notices.items()
                if c == src_cd and r.get("use_yn", "Y") == "Y" and (since is None or (r.get("posted_date") or since) >= since)]

    def set_use_yn(self, src_cd, ids, yn):
        for i in ids:
            if (src_cd, i) in self.notices:
                self.notices[(src_cd, i)]["use_yn"] = yn

    def list_candidates(self, today):
        out = []
        for (c, i), r in self.notices.items():
            ae = _as_date(r.get("apply_end"))
            if (r.get("use_yn", "Y") != "Y" or r.get("category_cd") not in ("OUT", "IN", "NATIONAL")
                    or r.get("status_cd") not in ("EXTRACTED", "NEED_REVIEW") or ae is None or ae < today):
                continue
            cand = build_candidate({
                "SRC_CD": c, "SRC_ID": i, "TITLE": r.get("title"), "ORIGIN_URL": r.get("origin_url"),
                "POSTED_DATE": r.get("posted_date"), "CATEGORY_CD": r.get("category_cd"),
                "APPLY_TYPE_CD": r.get("apply_type_cd"), "APPLY_START": r.get("apply_start"), "APPLY_END": r.get("apply_end"),
                "ORG_DEADLINE": r.get("org_deadline"), "SUMMARY": r.get("summary"), "STATUS_CD": r.get("status_cd"),
                "ELIG_JSON": r.get("elig_json"), "UNCERTAIN_JSON": r.get("uncertain_json")})
            if cand:
                out.append(cand)
        return sorted(out, key=lambda x: x["apply_end"])

    def get_notice_texts(self, pairs):
        out = {}
        for key in pairs:
            r = self.notices.get(tuple(key))
            if r is None or r.get("use_yn", "Y") != "Y":
                continue
            files = [{"seq": k[2], "name": f.get("org_file_nm") or "첨부", "role": f.get("role_cd") or "UNKNOWN", "text": f.get("extract_text") or "",
                      "ext": f.get("file_ext") or "", "size": f.get("file_size") or 0, "nas_path": f.get("nas_path") or ""}
                     for k, f in sorted(self.files.items(), key=lambda kv: kv[0][2]) if k[:2] == tuple(key) and f.get("extract_text")
                     and (f.get("role_cd") or "UNKNOWN") != "EXCLUDED"]
            out[tuple(key)] = {"body": r.get("body_text") or "", "files": files}
        return out

    def find_ids_by_title(self, src_cd, title_norm, exclude_id):
        from .text_clean import normalize_title
        return sorted(i for (c, i), r in self.notices.items()
                      if c == src_cd and i != exclude_id and r.get("use_yn", "Y") == "Y"
                      and normalize_title(r.get("title") or "") == title_norm)


class OracleRepository(Repository):
    """SCHOLARSHIP_NOTICE / SCHOLARSHIP_NOTICE_FILE (schema_ext01.sql). Oracle 11g 호환 (UPDATE -> 없으면 INSERT)."""

    def __init__(self, conn, candidate_sql_file=None):
        self.conn = conn
        self.candidate_sql_file = candidate_sql_file          # 추천 후보 조회 SQL (config/recommend_query.txt)

    # ------------------------------------------------------------------ 내부
    def _upsert(self, table: str, keys: Dict[str, str], rec: Dict[str, Any], cols: List[str], clobs: List[str]) -> None:
        """UPDATE 후 갱신된 행이 없으면 INSERT.
        (MERGE 를 쓰지 않는 이유: 같은 바인드를 UPDATE/INSERT 양쪽에 쓰면 긴 값(CLOB)에서 ORA-22284
         '중복 LONG 바인드는 지원되지 않습니다' 가 난다. 이 방식은 각 문장에서 바인드가 한 번씩만 나온다.)"""
        import oracledb
        vals = {c: rec[c] for c in cols if c in rec}                       # 허용 컬럼만 (SQL 인젝션 방지)
        vals = {c: (None if (isinstance(v, str) and v == "") else v) for c, v in vals.items()}   # '' 는 Oracle 에서 NULL
        key_cols = list(keys.keys())
        key_binds = {"k_" + k: v for k, v in keys.items()}
        where = " AND ".join("{0} = :k_{0}".format(k) for k in key_cols)
        set_sql = ", ".join("{0} = :{0}".format(c) for c in vals)
        set_sql = (set_sql + ", " if set_sql else "") + "UPDATED_AT = SYSDATE"
        upd = "UPDATE {} SET {} WHERE {}".format(table, set_sql, where)
        ins = "INSERT INTO {} ({}) VALUES ({})".format(
            table, ", ".join(key_cols + list(vals)),
            ", ".join([":k_{}".format(k) for k in key_cols] + [":{}".format(c) for c in vals]))
        binds = dict(vals)
        binds.update(key_binds)
        clob_sizes = {c: oracledb.DB_TYPE_CLOB for c in clobs if vals.get(c) is not None}

        cur = self.conn.cursor()
        try:
            for sql in (upd, ins):
                if clob_sizes:
                    cur.setinputsizes(**clob_sizes)                       # 실행마다 지정
                cur.execute(sql, binds)
                if sql is upd and cur.rowcount > 0:
                    break                                                  # 이미 있던 행을 갱신했으면 끝
        finally:
            cur.close()
        self.conn.commit()

    # ------------------------------------------------------------------ 인터페이스
    _has_src_fields = True            # SRC_FIELDS_JSON 컬럼이 없는 환경(ALTER 전)이면 False 로 바뀌어 구버전 쿼리를 쓴다

    def get_notice_state(self, src_cd, src_id):
        cur = self.conn.cursor()
        try:
            if self._has_src_fields:
                try:
                    cur.execute("SELECT CONTENT_HASH, STATUS_CD, USE_YN, SRC_FIELDS_JSON, ELIG_JSON FROM SCHOLARSHIP_NOTICE "
                                "WHERE SRC_CD = :c AND SRC_ID = :i", c=src_cd, i=src_id)
                    row = cur.fetchone()
                    return None if row is None else {"content_hash": row[0], "status_cd": row[1], "use_yn": row[2],
                                                     "src_fields_json": row[3], "elig_json": row[4]}
                except Exception as e:
                    if "ORA-00904" not in str(e):
                        raise
                    self._has_src_fields = False            # 컬럼 없음 -> 구버전 쿼리
                    log.warning("SRC_FIELDS_JSON 컬럼이 없습니다 (schema_ext02.sql 의 ALTER TABLE 필요). 외부02 항목 저장은 건너뜁니다.")
            cur.execute("SELECT CONTENT_HASH, STATUS_CD, USE_YN, ELIG_JSON FROM SCHOLARSHIP_NOTICE "
                        "WHERE SRC_CD = :c AND SRC_ID = :i", c=src_cd, i=src_id)
            row = cur.fetchone()
        finally:
            cur.close()
        return None if row is None else {"content_hash": row[0], "status_cd": row[1], "use_yn": row[2], "src_fields_json": None,
                                         "elig_json": row[3]}

    def upsert_notice(self, src_cd, src_id, rec):
        cols = NOTICE_COLS if self._has_src_fields else [c for c in NOTICE_COLS if c != "src_fields_json"]
        self._upsert("SCHOLARSHIP_NOTICE", {"SRC_CD": src_cd, "SRC_ID": src_id}, rec, cols, NOTICE_CLOBS)

    def upsert_file(self, src_cd, src_id, file_seq, rec):
        self._upsert("SCHOLARSHIP_NOTICE_FILE", {"SRC_CD": src_cd, "SRC_ID": src_id, "FILE_SEQ": file_seq},
                    rec, FILE_COLS, FILE_CLOBS)

    def list_active_ids_since(self, src_cd, since):
        cur = self.conn.cursor()
        try:
            if since is None:
                cur.execute("SELECT SRC_ID FROM SCHOLARSHIP_NOTICE WHERE SRC_CD = :c AND USE_YN = 'Y'", c=src_cd)
            else:
                cur.execute("SELECT SRC_ID FROM SCHOLARSHIP_NOTICE WHERE SRC_CD = :c AND USE_YN = 'Y' "
                            "AND POSTED_DATE >= :d", c=src_cd, d=since)
            return [r[0] for r in cur.fetchall()]
        finally:
            cur.close()

    def set_use_yn(self, src_cd, ids, yn):
        ids = list(ids)
        if not ids:
            return
        cur = self.conn.cursor()
        try:
            for i in ids:
                cur.execute("UPDATE SCHOLARSHIP_NOTICE SET USE_YN = :y, UPDATED_AT = SYSDATE "
                            "WHERE SRC_CD = :c AND SRC_ID = :i", y=yn, c=src_cd, i=i)
        finally:
            cur.close()
        self.conn.commit()

    def list_candidates(self, today):
        from pathlib import Path
        from . import sql_file
        from .config import CONFIG_DIR
        path = Path(self.candidate_sql_file or (CONFIG_DIR / "recommend_query.txt"))
        sql = sql_file.render(path, {}, ["today"])
        binds = {"today": today} if "today" in sql_file.used_binds(sql) else {}
        cur = self.conn.cursor()
        try:
            cur.execute(sql, binds)
            cols = [d[0].upper() for d in cur.description]
            need = [c for c in ("SRC_CD", "SRC_ID", "TITLE", "APPLY_END", "ELIG_JSON") if c not in cols]
            if need:
                raise sql_file.SqlFileError("{}: SELECT 목록에 필수 컬럼이 없습니다: {}".format(path.name, ", ".join(need)))
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            cur.close()
        out = [c for c in (build_candidate(r) for r in rows) if c]
        return sorted(out, key=lambda x: x["apply_end"] or date.max)

    @staticmethod
    def _lob(v) -> str:
        if v is None:
            return ""
        return v.read() if hasattr(v, "read") else str(v)

    def get_notice_texts(self, pairs):
        """읽기 전용. 공고 본문(BODY_TEXT)과 텍스트를 뽑은 첨부(EXTRACT_TEXT)를 공고마다 한 번씩 읽는다 (바인드 변수만 사용)."""
        out = {}
        cur = self.conn.cursor()
        try:
            for src_cd, src_id in pairs:
                cur.execute("SELECT BODY_TEXT FROM SCHOLARSHIP_NOTICE WHERE SRC_CD = :c AND SRC_ID = :i AND USE_YN = 'Y'", c=src_cd, i=src_id)
                row = cur.fetchone()
                if row is None:
                    continue
                body = self._lob(row[0])
                cur.execute("SELECT FILE_SEQ, ORG_FILE_NM, ROLE_CD, EXTRACT_TEXT, FILE_EXT, FILE_SIZE, NAS_PATH FROM SCHOLARSHIP_NOTICE_FILE "
                            "WHERE SRC_CD = :c AND SRC_ID = :i AND EXTRACT_TEXT IS NOT NULL ORDER BY SORT_NO, FILE_SEQ", c=src_cd, i=src_id)
                files = []
                for seq, name, role, text, ext, size, nas in cur.fetchall():
                    t = self._lob(text)
                    if t.strip() and (role or "UNKNOWN") != "EXCLUDED":
                        files.append({"seq": seq, "name": name or "첨부", "role": role or "UNKNOWN", "text": t,
                                      "ext": ext or "", "size": size or 0, "nas_path": nas or ""})        # nas_path 는 서버 안에서만 쓰고 응답에 싣지 않는다
                out[(src_cd, src_id)] = {"body": body, "files": files}
        finally:
            cur.close()
        return out

    def find_ids_by_title(self, src_cd, title_norm, exclude_id):
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT SRC_ID FROM SCHOLARSHIP_NOTICE WHERE SRC_CD = :c AND SRC_ID <> :i AND USE_YN = 'Y' "
                        "AND REGEXP_REPLACE(TRIM(TITLE), '[[:space:]]+', ' ') = :t", c=src_cd, i=exclude_id, t=title_norm)
            return [r[0] for r in cur.fetchall()]
        finally:
            cur.close()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
