# -*- coding: utf-8 -*-
"""[RECOMMEND] 규칙으로 정하지 못한 확인 조건의 '일반적 충족 가능성'을 AI 로 묻는다 (보조 라벨 전용).

- AI 에는 공고 조건 문장만 보낸다 (학생 프로필·식별 정보 없음). 판정에는 쓰이지 않는다.
- 같은 문장은 메모리 캐시로 다시 묻지 않는다. 한 요청당 호출은 최대 1번 (여러 문장을 묶어서).
- 키 없음·시간 초과·응답 오류는 모두 조용히 넘어가 규칙값(보통)으로 응답한다 (fail-open).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any, Dict, List, Optional, Tuple

from .feasibility import HARD, NORMAL, VERY_HARD

log = logging.getLogger(__name__)

SYSTEM = (
    "당신은 한국 대학 장학금 공고의 신청 조건을 평가합니다. 이 대학은 {school} 중심 대학입니다. 각 조건이 '이 대학 재학생 일반 기준으로, 아직 충족하지 못한 학생이 "
    "노력하면 충족할 수 있는지'를 판단하세요. NORMAL=흔하거나 준비하면 가능, HARD=대부분 학생이 충족하기 어렵거나 바꿀 수 없음(희소 자격·신분 등), "
    "VERY_HARD=이 대학의 계열과 맞지 않아 사실상 충족 불가(예: 다른 계열 전공자 대상). 반드시 JSON 배열만 출력: "
    "[{{\"n\":번호,\"level\":\"NORMAL|HARD|VERY_HARD\",\"reason\":\"20자 안팎 한 줄\"}}]"
)
_JSON = re.compile(r"\[.*\]", re.S)


class FeasibilityAI:
    def __init__(self, client, timeout: float = 8.0, max_items: int = 20, ttl: int = 7 * 86400, max_cache: int = 2000, school: str = "대학", salt: str = "", brief: str = ""):
        self.client = client
        self.school = school                                              # 학교 계열 설명 (학생 정보 아님)
        self.brief = brief                                                # 규칙 파일 요약 (AI 가 같은 기준으로 판단하도록)
        self.salt = salt                                                  # 규칙 파일 버전: 규칙을 고치면 캐시도 새로 만든다
        self.timeout = timeout
        self.max_items = max_items
        self.ttl = ttl
        self.max_cache = max_cache
        self._cache: Dict[str, Tuple[float, Optional[Tuple[str, str]]]] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1)

    def _key(self, text: str) -> str:
        return hashlib.sha1((self.school + "|" + self.salt + "|" + re.sub(r"\s+", "", text)).encode("utf-8")).hexdigest()

    def lookup(self, texts: List[str]) -> Dict[str, Tuple[str, str]]:
        """문장 -> (등급, 이유). 알 수 없는 문장은 결과에 없다."""
        now = time.time()
        out: Dict[str, Tuple[str, str]] = {}
        todo: List[str] = []
        with self._lock:
            for t in texts[: self.max_items]:
                hit = self._cache.get(self._key(t))
                if hit and now - hit[0] < self.ttl:
                    if hit[1]:
                        out[t] = hit[1]
                else:
                    todo.append(t)
        if not todo or self.client is None:
            return out
        try:
            got = self._pool.submit(self._ask, todo).result(timeout=self.timeout)
        except FutureTimeout:
            log.warning("feasibility AI timeout")
            return out
        except Exception as e:                                             # 라벨은 보조 정보이므로 실패해도 응답은 계속
            log.warning("feasibility AI failed: %s", type(e).__name__)
            return out
        with self._lock:
            if len(self._cache) > self.max_cache:
                self._cache.clear()
            for i, t in enumerate(todo, 1):
                self._cache[self._key(t)] = (now, got.get(i))
        for i, t in enumerate(todo, 1):
            if got.get(i):
                out[t] = got[i]
        return out

    def _ask(self, todo: List[str]) -> Dict[int, Tuple[str, str]]:
        user = "\n".join("{}. {}".format(i, t) for i, t in enumerate(todo, 1))
        resp = self.client.complete(SYSTEM.format(school=self.school) + ("\n\n" + self.brief if self.brief else ""), user, [], max_tokens=800)
        log.info("feasibility AI tokens in=%s out=%s", getattr(resp, "input_tokens", 0), getattr(resp, "output_tokens", 0))
        m = _JSON.search(getattr(resp, "text", "") or "")
        rows = json.loads(m.group(0)) if m else []
        res: Dict[int, Tuple[str, str]] = {}
        for r in rows:
            if isinstance(r, dict) and r.get("level") in (NORMAL, HARD, VERY_HARD) and isinstance(r.get("n"), int):
                res[r["n"]] = (r["level"], str(r.get("reason") or "")[:60])
        return res


def build(settings, client: Any = None, rules: Any = None) -> Optional[FeasibilityAI]:
    """설정으로 만든다. 꺼져 있거나 키가 없으면 None."""
    if not getattr(settings, "feasibility_ai", False):
        return None
    if client is None:
        if not getattr(settings, "ai_api_key", ""):
            return None
        try:
            from ..common.ai.client import make_client
            client = make_client(settings, max_tokens=800, timeout=max(2, int(settings.feasibility_timeout)))
        except Exception as e:
            log.warning("feasibility AI disabled: %s", type(e).__name__)
            return None
    school, salt, brief = "대학", "", ""
    if rules is not None:
        school = "·".join(rules.major_label(m) for m in rules.school_majors) or "대학"
        salt = rules.version
        from .feasibility_rules import brief_for_ai
        brief = brief_for_ai(rules)
    return FeasibilityAI(client, timeout=float(settings.feasibility_timeout), max_items=int(settings.feasibility_max_items), school=school, salt=salt, brief=brief)
