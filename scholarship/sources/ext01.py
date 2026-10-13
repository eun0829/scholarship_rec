# -*- coding: utf-8 -*-
"""[EXT01] 학교 홈페이지 장학공지 게시판(게시판 374) 어댑터.

- 본문/첨부 목록: 홈페이지 DB 직접 조회. SQL 은 config/ext01_query01.txt (본문), ext01_query02.txt (첨부) 에서 읽는다
                  -> 코드를 고치지 않고 파일만 수정하면 다음 실행부터 적용된다 (python run_ext01.py --show-sql 로 확인)
- 본문 이미지 : https://tukorea.ac.kr + DB 의 img src   (NAS/서버 경로는 사용하지 않음)
- 첨부 다운로드: https://www.tukorea.ac.kr/bbs/tukorea/{게시판}/{첨부번호}/download.do
- 연결 키: BBS_ARTCL_SEQ
"""
from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urljoin, urlparse

from ..common import db, sql_file
from ..common.config import CONFIG_DIR, Settings, env_str
from ..common.http import download_file, new_session
from ..common.models import SourceAttachment, SourceNotice
from ..common.storage import file_ext
from .base import SourceAdapter

log = logging.getLogger(__name__)

ALLOWED_HOSTS = {"tukorea.ac.kr", "www.tukorea.ac.kr"}
SQL_BINDS = ("setup", "days")
NOTICE_REQUIRED = ("BBS_ARTCL_SEQ", "SJ", "CN", "RGSDE")          # ANNYMTY_WRITER 는 선택
ATTACH_REQUIRED = ("BBS_ATCHMNFL_SEQ", "BBS_ARTCL_SEQ", "ORGINL_NM")   # SORT_NO 는 선택


@dataclass
class Ext01Config:
    db_user: str = ""
    db_password: str = field(default="", repr=False)
    db_dsn: str = ""
    setup_seq: str = "374"
    site_base: str = "https://tukorea.ac.kr"
    down_base: str = "https://www.tukorea.ac.kr"
    query01_file: Path = CONFIG_DIR / "ext01_query01.txt"
    query02_file: Path = CONFIG_DIR / "ext01_query02.txt"

    @classmethod
    def from_env(cls, s: Settings) -> "Ext01Config":
        return cls(
            db_user=env_str("EXT01_DB_USER") or s.db_user,
            db_password=env_str("EXT01_DB_PASSWORD") or s.db_password,
            db_dsn=env_str("EXT01_DB_DSN") or s.db_dsn,
            setup_seq=env_str("EXT01_BBS_SETUP_SEQ", "374"),
            site_base=env_str("EXT01_SITE_BASE", "https://tukorea.ac.kr").rstrip("/"),
            down_base=env_str("EXT01_DOWN_BASE", "https://www.tukorea.ac.kr").rstrip("/"),
            query01_file=Path(env_str("EXT01_QUERY01_FILE") or (CONFIG_DIR / "ext01_query01.txt")),
            query02_file=Path(env_str("EXT01_QUERY02_FILE") or (CONFIG_DIR / "ext01_query02.txt")),
        )


# ---------------------------------------------------------------- 날짜 조건 / 값 해석
def date_condition(rgsde_type: str) -> str:
    """쿼리 파일의 {DATE_COND} 로 들어가는 조건. RGSDE 컬럼 타입에 맞는 '최근 :days 일'.
    (옛 쿼리의 to_char(sysdate-30) 는 날짜 형식이 지정되지 않아 세션 설정에 따라 결과가 달라진다)"""
    if (rgsde_type or "").upper().startswith(("VARCHAR", "CHAR", "NVARCHAR")):
        return "a.RGSDE >= TO_CHAR(TRUNC(SYSDATE) - :days, 'YYYYMMDD')"
    return "a.RGSDE >= TRUNC(SYSDATE) - :days"


def parse_rgsde(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    digits = re.sub(r"\D", "", str(v))
    if len(digits) >= 8:
        try:
            return datetime.strptime(digits[:8], "%Y%m%d").date()
        except ValueError:
            return None
    return None


def resolve_image_url(site_base: str, src: str) -> str:
    """본문 img src -> 호출 URL. 허용 호스트(학교 도메인)만 통과. 한글 파일명은 URL 인코딩."""
    full = urljoin(site_base + "/", src.strip())
    u = urlparse(full)
    if u.scheme not in ("http", "https") or (u.hostname or "").lower() not in ALLOWED_HOSTS:
        raise ValueError("허용되지 않은 이미지 주소: {}".format(src[:80]))
    return "{}://{}{}".format(u.scheme, u.netloc, quote(u.path, safe="/%"))


def require_columns(names: Tuple[str, ...], query_file: Path, cursor_cols: List[str]) -> None:
    missing = [n for n in names if n not in cursor_cols]
    if missing:
        raise sql_file.SqlFileError(
            "{}: SELECT 목록에 필수 컬럼이 없습니다: {} (현재 컬럼: {}). 컬럼 이름 또는 별칭(AS)을 맞춰주세요.".format(
                Path(query_file).name, ", ".join(missing), ", ".join(cursor_cols)))


class Ext01Adapter(SourceAdapter):
    src_cd = "EXT01"

    def __init__(self, settings: Settings, cfg: Optional[Ext01Config] = None):
        self.s = settings
        self.cfg = cfg or Ext01Config.from_env(settings)
        self.session = new_session()
        self._conn = None

    # ------------------------------------------------------------------ SQL 파일
    def render_queries(self, rgsde_type: str = "DATE") -> Tuple[str, str]:
        """쿼리 파일을 읽어 {DATE_COND} 를 채운 최종 SQL (매 호출마다 파일을 다시 읽는다)."""
        tokens = {"DATE_COND": date_condition(rgsde_type)}
        return (sql_file.render(self.cfg.query01_file, tokens, SQL_BINDS),
                sql_file.render(self.cfg.query02_file, tokens, SQL_BINDS))

    def _run(self, conn, sql: str, query_file: Path, required: Tuple[str, ...], days: int) -> List[Dict[str, Any]]:
        values = {"setup": self.cfg.setup_seq, "days": days}
        binds = {k: values[k] for k in sql_file.used_binds(sql)}      # 파일에서 실제로 쓴 바인드만 전달
        cur = conn.cursor()
        try:
            try:
                cur.execute(sql, binds)
            except Exception as e:
                raise RuntimeError("쿼리 실행 실패 ({}): {}".format(Path(query_file).name, e))
            cols = [d[0].upper() for d in cur.description]
            require_columns(required, query_file, cols)
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            cur.close()

    # ------------------------------------------------------------------ DB
    def _connection(self):
        if self._conn is None:
            c = self.cfg
            self._conn = db.connect(c.db_user, c.db_password, c.db_dsn, self.s.oracle_client_lib)
        return self._conn

    def _rgsde_type(self, conn) -> str:
        cur = conn.cursor()
        try:
            cur.execute("SELECT DATA_TYPE FROM ALL_TAB_COLUMNS WHERE OWNER = 'HOME' "
                        "AND TABLE_NAME = 'APP_BBS_ARTCL' AND COLUMN_NAME = 'RGSDE'")
            row = cur.fetchone()
            t = row[0] if row else "DATE"
        except Exception as e:                       # 권한이 없으면 DATE 로 가정
            log.warning("RGSDE 타입 조회 실패, DATE 로 가정: %s", e)
            t = "DATE"
        finally:
            cur.close()
        log.info("RGSDE 컬럼 타입: %s", t)
        return t

    def fetch_notices(self, days: int) -> List[SourceNotice]:
        conn = self._connection()
        sql1, sql2 = self.render_queries(self._rgsde_type(conn))
        notices = self._run(conn, sql1, self.cfg.query01_file, NOTICE_REQUIRED, days)
        atts = self._run(conn, sql2, self.cfg.query02_file, ATTACH_REQUIRED, days)
        return self.assemble(notices, atts)

    def assemble(self, notice_rows: List[Dict[str, Any]], attach_rows: List[Dict[str, Any]]) -> List[SourceNotice]:
        """DB 행(dict) -> SourceNotice (DB 없이 테스트 가능하도록 분리)."""
        c = self.cfg
        by_notice: Dict[str, List[SourceAttachment]] = {}
        for r in attach_rows:
            fs = str(r["BBS_ATCHMNFL_SEQ"])
            by_notice.setdefault(str(r["BBS_ARTCL_SEQ"]), []).append(SourceAttachment(
                file_seq=fs, sort_no=int(r.get("SORT_NO") or 0), org_file_nm=r.get("ORGINL_NM") or "",
                download_url="{}/bbs/tukorea/{}/{}/download.do".format(c.down_base, c.setup_seq, fs)))
        out = []
        for r in notice_rows:
            sid = str(r["BBS_ARTCL_SEQ"])
            cn = r.get("CN")
            out.append(SourceNotice(
                src_cd=self.src_cd, src_id=sid,
                title=html.unescape(r.get("SJ") or "").strip(),
                dept_nm=(r.get("ANNYMTY_WRITER") or "").strip(),
                posted_date=parse_rgsde(r.get("RGSDE")),
                origin_url="{}/bbs/tukorea/{}/{}/artclView.do".format(c.down_base, c.setup_seq, sid),
                body_html=cn if isinstance(cn, str) else (cn.read() if cn is not None else ""),
                attachments=sorted(by_notice.get(sid, []), key=lambda a: a.sort_no)))
        return out

    # ------------------------------------------------------------------ 다운로드
    def download_attachment(self, att: SourceAttachment, dest: Path, max_bytes: int) -> int:
        time.sleep(self.s.request_interval)
        return download_file(self.session, att.download_url, dest, max_bytes,
                             timeout=self.s.http_timeout, retries=self.s.http_retries,
                             ext=file_ext(att.org_file_nm))

    def download_body_image(self, src: str, dest: Path, max_bytes: int) -> int:
        url = resolve_image_url(self.cfg.site_base, src)
        time.sleep(self.s.request_interval)
        return download_file(self.session, url, dest, max_bytes,
                             timeout=self.s.http_timeout, retries=self.s.http_retries, ext=file_ext(url))

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
