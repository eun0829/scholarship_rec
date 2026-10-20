# -*- coding: utf-8 -*-
"""[API] 공고 목록 캐시. 요청마다 DB 를 읽지 않고, 일정 시간(ttl) 동안은 마지막으로 읽은 목록을 쓴다.

- DB 연결은 하나만 쓰므로 읽기는 잠금으로 한 번에 하나만 한다 (요청은 여러 개가 동시에 와도 된다).
- 읽기에 실패하면 연결을 버리고 다음 요청에서 다시 연결한다. 이미 읽어 둔 목록이 stale_ttl 안이면 그 목록으로 계속 응답한다 (stale).
- 공고 목록은 학생 정보가 아니다. 학생 입력은 여기에 들어오지 않는다.
"""
import copy
import logging
import threading
import time
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("scholarship.api")
MAX_DATES = 3                                         # 기준일별로 보관하는 목록 수 (시험용 today 가 여러 개 와도 메모리가 늘지 않게)


class SourceUnavailable(Exception):
    """공고 목록을 읽을 수 없고, 쓸 만한 이전 목록도 없다."""


class CachedSource:
    def __init__(self, factory: Callable[[], Any], ttl: int = 300, stale_ttl: int = 21600,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], datetime] = datetime.now):
        self._factory, self.ttl, self.stale_ttl, self._clock, self._wall = factory, ttl, stale_ttl, clock, wall
        self._src: Any = None
        self._lock = threading.Lock()
        self._entries: Dict[date, Tuple[float, datetime, List[Dict[str, Any]]]] = {}
        self.last_error: Optional[str] = None             # 마지막 읽기 실패의 예외 이름 (메시지는 비밀번호·주소가 섞일 수 있어 남기지 않는다)
        self.last_served_stale = False

    # ------------------------------------------------------------------ 후보 공급 (Recommender 가 부른다)
    def list_candidates(self, today: date) -> List[Dict[str, Any]]:
        with self._lock:
            now = self._clock()
            ent = self._entries.get(today)
            if ent and now - ent[0] < self.ttl:
                self.last_served_stale = False
                return copy.deepcopy(ent[2])
            try:
                if self._src is None:
                    self._src = self._factory()
                data = self._src.list_candidates(today)
            except Exception as e:                        # DB 끊김·SQL 오류 등
                self.last_error = type(e).__name__
                log.error("공고 목록 읽기 실패 (%s)", self.last_error)
                self._drop_source()
                if ent and now - ent[0] < self.stale_ttl:
                    self.last_served_stale = True
                    return copy.deepcopy(ent[2])
                raise SourceUnavailable(self.last_error)
            self.last_error = None
            self.last_served_stale = False
            self._entries[today] = (now, self._wall(), data)
            while len(self._entries) > MAX_DATES:
                self._entries.pop(min(self._entries, key=lambda k: self._entries[k][0]))
            return copy.deepcopy(data)

    # ------------------------------------------------------------------ 공고 원문 (챗봇이 부른다)
    def notice_texts(self, pairs, ttl: int = 600, max_entries: int = 200) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """공고 본문·첨부 텍스트. 공고 목록과 따로 짧게 캐시한다 (원문은 학생 정보가 아니다). 읽을 수 없으면 SourceUnavailable."""
        pairs = [tuple(p) for p in pairs]
        with self._lock:
            now = self._clock()
            cache = self.__dict__.setdefault("_texts", {})
            out: Dict[Tuple[str, str], Dict[str, Any]] = {}
            need = []
            for k in pairs:
                ent = cache.get(k)
                if ent and now - ent[0] < ttl:
                    out[k] = ent[1]
                else:
                    need.append(k)
            if need:
                try:
                    if self._src is None:
                        self._src = self._factory()
                    getter = getattr(self._src, "get_notice_texts", None)
                    got = getter(need) if getter else {}
                except Exception as e:
                    self.last_error = type(e).__name__
                    log.error("공고 원문 읽기 실패 (%s)", self.last_error)
                    self._drop_source()
                    stale = {k: cache[k][1] for k in need if k in cache}
                    if len(stale) == len(need):
                        out.update(stale)
                        return out
                    raise SourceUnavailable(self.last_error)
                for k, v in got.items():
                    cache[k] = (now, v)
                    out[k] = v
                while len(cache) > max_entries:
                    cache.pop(min(cache, key=lambda x: cache[x][0]))
            return out

    # ------------------------------------------------------------------ 상태 (health 가 쓴다)
    def status(self, today: Optional[date] = None) -> Dict[str, Any]:
        with self._lock:
            ent = self._entries.get(today or date.today())
            return {"loaded": len(ent[2]) if ent else None,
                    "refreshed_at": ent[1].isoformat(timespec="seconds") if ent else None,
                    "age_seconds": int(self._clock() - ent[0]) if ent else None,
                    "stale": self.last_served_stale, "last_error": self.last_error}

    def invalidate(self) -> None:
        with self._lock:
            self._entries.clear()
            self.__dict__.pop("_texts", None)

    def close(self) -> None:
        with self._lock:
            self._drop_source()

    def _drop_source(self) -> None:
        src, self._src = self._src, None
        close = getattr(src, "close", None)
        if close:
            try:
                close()
            except Exception:
                pass
