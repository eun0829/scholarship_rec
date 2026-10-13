# -*- coding: utf-8 -*-
"""[EXT02] 드림스폰(dreamspon.com) 장학 어댑터.

  1) 목록(/scholarship/list.html?ordby=&page=) : 로그인 없이 읽는다 -> 종류/대상 태그/상태로 후보를 거른다
  2) 이미 저장된 글(완료 상태)은 상세를 조회하지 않는다 (stub). 새 글/미완료 글이 있을 때만 로그인한다 (지연 로그인, 1회)
  3) 상세(/scholarship/view.html?idx=) : 로그인 필요. 고정 7개 항목을 해석해 SourceNotice 로 만든다 (ext02_parse.compose)
  4) 첨부 : POST /process/fileDown.html (file1=목록의 data-filepath, idx) - 로그인 세션 쿠키로 받는다

로그인: POST /process/checkuser.html  mode=login, userid, userpw, idsaveCheck=Y, autoLogin=N, pageReferer=''
        응답 JSON 이 result=true 이고 checkyn='Y' 이면 성공. 실패하면 자동 재시도 없이 중단한다 (계정 잠금 방지).
        비밀번호는 로그/오류 메시지에 남기지 않는다. 쿠키는 메모리 세션에만 둔다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests

from ..common.config import CONFIG_DIR, Settings, env_str
from ..common.http import HtmlResponseError, download_file, new_session
from ..common.models import SourceAttachment, SourceNotice
from ..common.storage import file_ext
from . import ext02_parse as P
from .base import SourceAdapter

log = logging.getLogger(__name__)

SOURCE_NOTE = ("본문은 드림스폰이 공고문을 요약한 정보라서 누락이나 오류가 있을 수 있습니다. '요강' 첨부(공고문)가 있고 내용이 충돌하면 "
               "첨부를 우선하세요. 신청기간은 코드가 별도로 확인하므로, 본문과 다른 날짜가 첨부에 보이면 uncertain(level=verify)에 적으세요. "
               "다음은 코드가 따로 기록하므로 uncertain 에 적지 마세요: 본문이 요약본이라는 사실, 요강 첨부가 없다는 사실, "
               "제출서류나 상세 내용이 '공고문 안내 확인'으로만 적혀 있는 것, 선발인원이나 금액이 미정/미기재인 것. "
               "그 외에 자격 판정에 쓰이는 값이 실제로 모호하거나 서로 충돌할 때만 verify 로 적으세요.")


MAX_RELOGINS = 2                                  # 세션이 계속 풀리는 서버에서 요청마다 로그인하지 않도록 상한을 둔다


class LoginError(Exception):
    pass


class DetailError(Exception):
    pass


@dataclass
class Ext02Config:
    base_url: str = "https://www.dreamspon.com"
    list_path: str = "/scholarship/list.html"
    view_path: str = "/scholarship/view.html"
    login_path: str = "/process/checkuser.html"
    download_path: str = "/process/fileDown.html"
    ordby: List[int] = field(default_factory=lambda: [1, 2, 3])
    max_pages: int = 15
    delay_sec: float = 2.0
    timeout_sec: int = 30
    include_kinds: List[str] = field(default_factory=lambda: ["장학금"])
    include_tags: List[str] = field(default_factory=lambda: ["대학생"])
    exclude_states: List[str] = field(default_factory=lambda: ["모집완료"])
    max_attachments: int = 10
    use_summary_images: bool = False
    apply_type_keywords: Dict[str, List[str]] = field(default_factory=lambda: dict(P.DEFAULT_APPLY_KW))
    target_keywords: Dict[str, List[str]] = field(default_factory=lambda: dict(P.DEFAULT_TARGET_KW))

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Ext02Config":
        path = Path(env_str("EXT02_CONFIG_FILE") or path or (CONFIG_DIR / "ext02.json"))
        cfg = cls()
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            for k, v in data.items():
                if not k.startswith("_") and hasattr(cfg, k):
                    setattr(cfg, k, v)
        base = env_str("EXT02_BASE_URL")
        if base:
            cfg.base_url = base.rstrip("/")
        return cfg


class Ext02Adapter(SourceAdapter):
    src_cd = "EXT02"
    full_scope = True

    def __init__(self, settings: Settings, cfg: Optional[Ext02Config] = None,
                 known_state: Optional[Callable[[str, str], Optional[Dict[str, Any]]]] = None,
                 refresh: bool = False, force: bool = False, limit: Optional[int] = None, only: Optional[List[str]] = None,
                 today: Optional[date] = None, sleep: Callable[[float], None] = time.sleep):
        self.s = settings
        self.cfg = cfg or Ext02Config.load()
        self.known_state, self.refresh, self.force = known_state, refresh, force
        self.limit, self.only = limit, set(only or [])
        self.today = today or date.today()
        self._sleep = sleep
        self.session = new_session()
        self._last_request = 0.0
        self._logged_in = False
        self._had_content = False                 # 로그인 후 상세 본문을 한 번이라도 읽었는가 (세션 만료 구분용)
        self._login_error: Optional[LoginError] = None
        self._relogins = 0                        # 세션 만료로 다시 로그인한 횟수 (실행당 최대 MAX_RELOGINS)
        self.deactivation_safe = True
        self.failed_ids: List[str] = []

    # ------------------------------------------------------------------ HTTP
    def _url(self, path: str) -> str:
        return self.cfg.base_url.rstrip("/") + path

    def _pause(self) -> None:
        """요청 사이 간격을 지킨다 (서버 부담 최소화)."""
        wait = self.cfg.delay_sec - (time.monotonic() - self._last_request)
        if self._last_request and wait > 0:
            self._sleep(wait)
        self._last_request = time.monotonic()

    def _get(self, url: str, params: Optional[dict] = None) -> requests.Response:
        last: Optional[Exception] = None
        for attempt in range(1, 4):
            self._pause()
            try:
                r = self.session.get(url, params=params, timeout=self.cfg.timeout_sec)
                if r.status_code >= 500:
                    last = DetailError("HTTP {}".format(r.status_code))
                    self._sleep(2.0 * attempt)
                    continue
                if r.status_code != 200:
                    raise DetailError("HTTP {}".format(r.status_code))
                if not r.encoding or r.encoding.lower() == "iso-8859-1":
                    r.encoding = "utf-8"
                return r
            except requests.RequestException as e:
                last = e
                self._sleep(2.0 * attempt)
        raise DetailError("요청 실패: {}".format(last))

    # ------------------------------------------------------------------ 로그인 (지연, 1회 시도)
    def login(self) -> None:
        if self._login_error is not None:
            raise self._login_error                      # 한 번 실패했으면 다시 시도하지 않는다
        uid, pw = self.s.dreamspon_id, self.s.dreamspon_password
        if not uid or not pw:
            self._login_error = LoginError("DREAMSPON_ID / DREAMSPON_PASSWORD 가 설정되지 않았습니다 "
                                           "(python set_secret.py DREAMSPON_ID, python set_secret.py DREAMSPON_PASSWORD)")
            raise self._login_error
        self._pause()
        try:
            r = self.session.post(self._url(self.cfg.login_path), timeout=self.cfg.timeout_sec,
                                  data={"mode": "login", "userid": uid, "idsaveCheck": "Y", "autoLogin": "N",
                                        "pageReferer": "", "userpw": pw},
                                  headers={"X-Requested-With": "XMLHttpRequest", "Referer": self._url("/member/login.html")})
        except requests.RequestException as e:
            self._login_error = LoginError("로그인 요청 실패: {}".format(str(e).replace(pw, "****").replace(uid, "****")))
            raise self._login_error
        if r.status_code != 200:
            self._login_error = LoginError("로그인 응답 HTTP {}".format(r.status_code))
            raise self._login_error
        try:
            data = r.json()
        except ValueError:
            self._login_error = LoginError("로그인 응답이 JSON 이 아닙니다 (사이트 화면이 바뀌었을 수 있음)")
            raise self._login_error
        ok = data.get("result") in (True, 1, "true") and str(data.get("checkyn", "")).upper() == "Y"
        if not ok:
            self._login_error = LoginError("로그인 실패: 아이디/비밀번호가 맞지 않거나 로그인이 제한되었습니다. 자동으로 다시 시도하지 않습니다 "
                                           "(소셜 로그인으로 가입한 계정이면 아이디/비밀번호 로그인이 안 될 수 있습니다)")
            raise self._login_error
        self._logged_in = True
        log.info("[EXT02] 로그인 성공")

    def _can_relogin(self) -> bool:
        return self._had_content and self._relogins < MAX_RELOGINS

    def _relogin(self) -> None:
        """세션 만료 시 다시 로그인한다 (실행당 최대 MAX_RELOGINS 회)."""
        self._relogins += 1
        self._logged_in = False
        self.session.cookies.clear()
        self.login()

    # ------------------------------------------------------------------ 목록 (로그인 불필요)
    def crawl_list(self) -> List[P.ListRow]:
        seen: Dict[str, P.ListRow] = {}
        ok = True
        for ob in self.cfg.ordby:
            page, last = 1, 1
            while page <= self.cfg.max_pages:
                params: Dict[str, Any] = {}
                if ob:
                    params["ordby"] = ob
                if page > 1:
                    params["page"] = page
                try:
                    r = self._get(self._url(self.cfg.list_path), params)
                except DetailError as e:
                    log.warning("[EXT02] 목록 조회 실패 (ordby=%s, page=%d): %s", ob, page, e)
                    ok = False
                    break
                rows = P.parse_list(r.text)
                if page == 1:
                    last = P.parse_max_page(r.text)
                if not rows:
                    break
                for row in rows:
                    seen.setdefault(row.idx, row)
                if page >= last:
                    break
                page += 1
        self.deactivation_safe = ok and bool(seen)         # 목록을 못 읽었거나 0건이면 '사라진 글' 정리를 하지 않는다
        if not self.deactivation_safe:
            log.warning("[EXT02] 목록이 비정상이라 사라진 글 비활성화를 건너뜁니다 (조회 %d건)", len(seen))
        return list(seen.values())

    def select(self, rows: List[P.ListRow]) -> List[P.ListRow]:
        c = self.cfg
        out = []
        for r in rows:
            if c.include_kinds and r.kind not in c.include_kinds:
                continue
            if c.include_tags and not any(t in r.audiences for t in c.include_tags):
                continue
            if r.state in c.exclude_states or (r.dday is not None and r.dday < 0):
                continue
            out.append(r)
        return sorted(out, key=lambda r: (r.dday if r.dday is not None else 10 ** 6, -int(r.idx)))

    # ------------------------------------------------------------------ 상세 (로그인 필요)
    def fetch_detail(self, idx: str) -> P.Detail:
        if not self._logged_in:
            self.login()
        for attempt in (1, 2):
            r = self._get(self._url(self.cfg.view_path), {"idx": idx})
            d = P.parse_detail(r.text)
            if d.has_content and "/member/login" not in r.url:
                self._had_content = True
                return d
            if attempt == 1 and self._can_relogin():        # 이전에는 읽혔는데 이번에 안 읽힘 -> 세션 만료로 보고 재로그인
                log.warning("[EXT02] 세션이 만료된 것으로 보여 다시 로그인합니다")
                self._relogin()
                continue
            raise DetailError("상세 내용을 읽지 못했습니다 (idx={}). 로그인 후에도 본문이 없으면 계정 권한이나 화면 형식을 확인하세요".format(idx))
        raise DetailError("상세 조회 실패 (idx={})".format(idx))

    # ------------------------------------------------------------------ SourceAdapter
    def _is_known_completed(self, idx: str) -> bool:
        if self.known_state is None or self.force or self.refresh:
            return False
        st = self.known_state(self.src_cd, idx)
        return bool(st) and st.get("status_cd") in ("EXTRACTED", "NEED_REVIEW")

    def fetch_notices(self, days: int) -> List[SourceNotice]:
        rows = self.select(self.crawl_list())
        log.info("[EXT02] 목록 후보 %d건 (종류 %s, 대상 %s)", len(rows), "/".join(self.cfg.include_kinds) or "전체",
                 "/".join(self.cfg.include_tags) or "전체")
        if self.only:
            rows = [r for r in rows if r.idx in self.only]
        if self.limit:
            rows = rows[:self.limit]
        out: List[SourceNotice] = []
        for row in rows:
            if self._is_known_completed(row.idx):
                out.append(SourceNotice(src_cd=self.src_cd, src_id=row.idx, title=row.title, dept_nm=row.org, stub=True))
                continue
            try:
                out.append(self._build_notice(row, self.fetch_detail(row.idx)))
            except LoginError:
                raise                                       # 로그인 문제는 전체 중단
            except DetailError as e:
                log.warning("[EXT02/%s] 상세 조회 실패 - 건너뜀: %s", row.idx, e)
                self.failed_ids.append(row.idx)
                out.append(SourceNotice(src_cd=self.src_cd, src_id=row.idx, title=row.title, dept_nm=row.org, stub=True))
        return out

    def _build_notice(self, row: P.ListRow, d: P.Detail) -> SourceNotice:
        fields, hints = P.compose(d, row, self.today, self.cfg.target_keywords, self.cfg.apply_type_keywords)
        start = hints.get("apply_start")
        posted = self.today
        if start:
            y, m, dd = (int(x) for x in start.split("-"))
            posted = min(date(y, m, dd), self.today)
        atts = []
        for n, a in enumerate(d.attachments[:self.cfg.max_attachments], 1):
            tag = hashlib.sha1(a.filepath.encode("utf-8")).hexdigest()[:6]          # 같은 이름으로 다시 올리면 번호가 바뀌어 새로 받는다
            atts.append(SourceAttachment(file_seq="{}-{}".format(n, tag), sort_no=n, org_file_nm=a.name,
                                         download_url=self._url(self.cfg.download_path),
                                         extra={"file1": a.filepath, "idx": row.idx}))
        basis = json.dumps({"title": d.title, "info": {k: v for k, v in d.info.items() if k != "신청기간"},
                            "sections": {k: v for k, v in d.sections.items() if k != "period"}, "extra": d.extra_sections,
                            "org": d.org, "original_url": d.original_url,
                            "files": sorted((a.name, a.filepath) for a in d.attachments)}, ensure_ascii=False, sort_keys=True)
        return SourceNotice(
            src_cd=self.src_cd, src_id=row.idx, title=d.title or row.title, dept_nm=d.org_name or row.org, posted_date=posted,
            origin_url=d.original_url or "{}?idx={}".format(self._url(self.cfg.view_path), row.idx),
            body_html=P.build_body_html(d, self.cfg.use_summary_images), attachments=atts,
            source_fields=fields, hints=hints, hash_basis=basis, source_note=SOURCE_NOTE)

    def download_attachment(self, att: SourceAttachment, dest: Path, max_bytes: int) -> int:
        if not self._logged_in:
            self.login()
        data = {"file1": att.extra["file1"], "idx": att.extra["idx"]}
        ext = file_ext(att.org_file_nm)
        for attempt in (1, 2):
            self._pause()
            try:
                return download_file(self.session, self._url(self.cfg.download_path), dest, max_bytes,
                                     timeout=self.cfg.timeout_sec, ext=ext, method="POST", data=data,
                                     headers={"Referer": "{}?idx={}".format(self._url(self.cfg.view_path), att.extra["idx"])})
            except HtmlResponseError:
                if attempt == 1 and self._can_relogin():    # 로그인이 풀린 경우 다시 로그인해서 재시도
                    self._relogin()
                    continue
                raise

    def download_body_image(self, src: str, dest: Path, max_bytes: int) -> int:
        url = urljoin(self._url("/"), src)
        if urlparse(url).netloc != urlparse(self.cfg.base_url).netloc:
            raise ValueError("허용되지 않은 이미지 주소: {}".format(url[:80]))
        self._pause()
        return download_file(self.session, url, dest, max_bytes, timeout=self.cfg.timeout_sec, ext=file_ext(src.split("?")[0]))

    def close(self) -> None:
        self.session.close()
