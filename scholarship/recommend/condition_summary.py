# -*- coding: utf-8 -*-
"""[RECOMMEND] 조건이 많은 조건부 공고의 '확인할 조건'을 3~5줄로 요약한다 (AI + 코드 대체).

- 입력은 공고의 조건 문장(조건 칸)뿐이다. 학생 상태·프로필은 AI 에 보내지 않는다 -> 공고 단위로 캐시한다.
- AI 응답은 검증한다: 모든 조건 행이 한 그룹에 정확히 한 번 들어가고, 원문 발췌는 그 행 문장의 일부여야 한다. 어긋나면 그 부분만 코드 값으로 바꾼다.
- AI 가 없거나(키 없음·꺼짐) 실패·시간 초과이면 코드 규칙으로 묶어서 요약한다 (유형별 요건 접기, 제외 조건 묶기).
- 요약은 보조 정보다. 판정·조건 행 계산에는 관여하지 않고, 전체 원문 조건은 항상 남는다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import feasibility as F
from .feasibility_rules import Rules, brief_for_ai

log = logging.getLogger(__name__)

PROMPT_VERSION = "1"
MAX_GROUPS = 5
TITLE_MAX, SUMMARY_MAX, QUOTE_MAX = 12, 80, 60
LEVELS = (F.NORMAL, F.HARD, F.VERY_HARD)

SYSTEM = (
    "당신은 한국 대학 장학금 공고의 신청 조건을 학생이 읽기 쉽게 정리합니다. 이 대학은 {school} 중심 대학입니다. "
    "각 공고는 번호가 붙은 조건 행 목록입니다(행 번호는 0부터). 같은 주제의 행을 그룹으로 묶어 요약하세요. 모든 행은 정확히 한 그룹에 들어가야 합니다. "
    "반드시 JSON 배열만 출력합니다: [{{\"n\":공고번호,\"groups\":[{{\"title\":\"12자 이내\",\"summary\":\"80자 이내 핵심\",\"quote\":\"해당 행 문장에서 그대로 가져온 60자 이내 발췌\","
    "\"level\":\"NORMAL|HARD|VERY_HARD\",\"rows\":[행번호,...]}}]}}]. 공고에 없는 내용은 만들지 않습니다."
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _cut(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def row_text(row: Dict[str, Any]) -> str:
    return (row.get("text") or row.get("cond") or "").strip()


_TOPICS = (("제외", ("제외", "불가", "중복", "수혜")), ("지역", ("거주", "주민등록", "소재", "시도", "지역", "시 ", "군 ")), ("성적", ("평점", "성적", "학점", "백분위")), ("졸업", ("졸업",)),
           ("소득", ("소득", "수급", "차상위", "분위")), ("추천", ("추천",)), ("서류", ("서류", "제출", "증명")), ("신분", ("신분", "자녀", "본인 또는")),
           ("경력", ("경력", "근무", "승무", "경험")), ("가족·부양", ("가족", "부양", "보호자", "부모")), ("나이", ("만 ", "세 이하", "세 미만", "연령")), ("학적", ("재학", "휴학", "학기", "학년")))


def _topic(text: str) -> str:
    for name, words in _TOPICS:
        if any(w in text for w in words):
            return name
    return "조건"


def code_summary(rows: Sequence[Dict[str, Any]], max_groups: int = MAX_GROUPS) -> List[Dict[str, Any]]:
    """AI 없이 묶는다: 유형별 요건은 한 그룹, 제외 조건은 한 그룹, 나머지는 행마다(주제 이름 붙임). 그룹이 많으면 남는 행을 '기타'로 묶는다."""
    groups: List[Dict[str, Any]] = []
    variant_idx = [i for i, r in enumerate(rows) if r.get("variant")]
    excl_idx = [i for i, r in enumerate(rows) if not r.get("variant") and r.get("result") == "exclude"]
    single = [i for i in range(len(rows)) if i not in set(variant_idx) | set(excl_idx)]
    for i in single:
        t = row_text(rows[i])
        groups.append({"title": _topic(t), "summary": _cut(t, 60), "quote": "" if len(t) <= 60 else _cut(t, QUOTE_MAX), "kind": "confirm", "rows": [i]})
    if excl_idx:
        texts = [_cut(row_text(rows[i]), 28) for i in excl_idx]
        groups.append({"title": "제외 조건 ({}개)".format(len(excl_idx)), "summary": " / ".join(texts[:2]) + (" 외 {}개".format(len(texts) - 2) if len(texts) > 2 else ""),
                       "quote": "", "kind": "exclude", "rows": excl_idx})
    if variant_idx:
        names: List[str] = []
        for i in variant_idx:
            if rows[i]["variant"] not in names:
                names.append(rows[i]["variant"])
        groups.append({"title": "유형별 요건 ({}개)".format(len(variant_idx)), "summary": "유형: " + " · ".join(names[:4]) + (" 외 {}개".format(len(names) - 4) if len(names) > 4 else ""),
                       "quote": _cut(row_text(rows[variant_idx[0]]), QUOTE_MAX), "kind": "confirm", "rows": variant_idx})
    if len(groups) > max_groups:                                    # 마지막 두 그룹(제외·유형별)은 유지하고 앞쪽 낱개를 '기타 조건'으로 합친다
        keep_tail = [g for g in groups if g["title"].startswith(("제외 조건", "유형별 요건"))]
        rest = [g for g in groups if g not in keep_tail]
        room = max(1, max_groups - len(keep_tail))
        head, over = rest[: room - 1], rest[room - 1:]
        if over:
            idx = sorted(i for g in over for i in g["rows"])
            head.append({"title": "기타 조건 ({}개)".format(len(idx)), "summary": " / ".join(_cut(row_text(rows[i]), 24) for i in idx[:2]) + (" 외 {}개".format(len(idx) - 2) if len(idx) > 2 else ""),
                         "quote": "", "kind": "confirm", "rows": idx})
        groups = head + keep_tail
    return groups


def _finish(groups: List[Dict[str, Any]], rows: Sequence[Dict[str, Any]], rules: Rules) -> List[Dict[str, Any]]:
    """그룹마다 kind(제외 조건이 하나라도 있으면 exclude)와 level(없으면 기본 등급)을 채운다."""
    out = []
    for g in groups:
        g = dict(g)
        g["kind"] = "exclude" if any(rows[i].get("result") == "exclude" for i in g["rows"]) else "confirm"
        if g.get("level") not in LEVELS:
            g["level"] = rules.default_level
        out.append(g)
    return out


def validate_ai(raw: Any, rows: Sequence[Dict[str, Any]], rules: Rules) -> Optional[List[Dict[str, Any]]]:
    """AI 가 돌려준 그룹 목록을 검증·보정한다. 쓸 수 없으면 None (코드 대체)."""
    if not isinstance(raw, list) or not raw:
        return None
    n = len(rows)
    seen: set = set()
    groups: List[Dict[str, Any]] = []
    for g in raw:
        if not isinstance(g, dict):
            continue
        idx: List[int] = []
        for i in g.get("rows") or []:
            if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < n and i not in seen and i not in idx:
                idx.append(i)
        if not idx:
            continue
        seen.update(idx)
        title = _cut(str(g.get("title") or ""), TITLE_MAX) or _topic(row_text(rows[idx[0]]))
        summary = _cut(str(g.get("summary") or ""), SUMMARY_MAX) or _cut(row_text(rows[idx[0]]), 60)
        quote = str(g.get("quote") or "").strip()
        if quote and not any(_norm(quote) in _norm(row_text(rows[i])) for i in idx):
            quote = ""                                              # 원문에 없는 문장은 발췌로 쓰지 않는다
        if not quote:
            quote = _cut(row_text(rows[idx[0]]), QUOTE_MAX)
        groups.append({"title": title, "summary": summary, "quote": _cut(quote, QUOTE_MAX), "level": g.get("level"), "rows": idx})
    missing = [i for i in range(n) if i not in seen]
    if not groups:
        return None
    if missing:
        groups.append({"title": "기타 조건 ({}개)".format(len(missing)), "summary": " / ".join(_cut(row_text(rows[i]), 24) for i in missing[:2]) + (" 외 {}개".format(len(missing) - 2) if len(missing) > 2 else ""),
                       "quote": "", "level": rules.default_level, "rows": missing})
    return _finish(groups[: MAX_GROUPS + 1], rows, rules)


def apply_levels(groups: List[Dict[str, Any]], rows: Sequence[Dict[str, Any]], rules: Rules, majors: Sequence[str]) -> List[Dict[str, Any]]:
    """규칙 파일이 정하는 등급이 AI 값보다 우선한다 (계열 불일치 등). 학생 계열에 따라 달라질 수 있어 캐시 밖에서 적용한다."""
    out = []
    for g in groups:
        g = dict(g)
        hits = [F.classify_text("확인 필요: " + row_text(rows[i]), rules, majors) for i in g["rows"]]
        hits = [h for h in hits if h]
        if hits:
            g["level"] = max((h[0] for h in hits), key=lambda x: F.LEVEL_RANK[x])
        out.append(g)
    return out


class ConditionSummarizer:
    def __init__(self, client, rules: Rules, timeout: float = 15.0, max_items: int = 8, ttl: int = 7 * 86400, max_cache: int = 1000):
        self.client = client
        self.rules = rules
        self.timeout = timeout
        self.max_items = max_items
        self.ttl = ttl
        self.max_cache = max_cache
        self._cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1)
        self.school = "·".join(rules.major_label(m) for m in rules.school_majors) or "대학"

    def _key(self, rows: Sequence[Dict[str, Any]]) -> str:
        body = "\n".join("{}|{}".format(r.get("variant") or "", row_text(r)) for r in rows)
        return hashlib.sha1("|".join((self.school, self.rules.version, PROMPT_VERSION, body)).encode("utf-8")).hexdigest()

    def summarize_many(self, jobs: Sequence[Tuple[str, Sequence[Dict[str, Any]]]], majors: Sequence[str] = ()) -> Dict[str, Dict[str, Any]]:
        """jobs: (공고 id, 열린 조건 행 목록). 반환: 공고 id -> {"source": "ai"|"rule", "groups": [...]}. 항상 모든 공고에 값을 돌려준다."""
        out: Dict[str, Dict[str, Any]] = {}
        todo: List[Tuple[str, Sequence[Dict[str, Any]]]] = []
        now = time.time()
        with self._lock:
            for nid, rows in jobs[: self.max_items]:
                hit = self._cache.get(self._key(rows))
                if hit and now - hit[0] < self.ttl:
                    out[nid] = {"source": "ai", "groups": apply_levels(hit[1], rows, self.rules, majors)}
                else:
                    todo.append((nid, rows))
        if todo and self.client is not None:
            got = self._ask(todo)
            with self._lock:
                if len(self._cache) > self.max_cache:
                    self._cache.clear()
                for i, (nid, rows) in enumerate(todo, 1):
                    gs = got.get(i)
                    if gs:
                        self._cache[self._key(rows)] = (now, gs)
                        out[nid] = {"source": "ai", "groups": apply_levels(gs, rows, self.rules, majors)}
        for nid, rows in jobs:                                         # AI 를 못 쓴 공고(또는 상한을 넘은 공고)는 코드 대체
            if nid not in out:
                out[nid] = {"source": "rule", "groups": apply_levels(_finish(code_summary(rows), rows, self.rules), rows, self.rules, majors)}
        return out

    def _ask(self, todo: Sequence[Tuple[str, Sequence[Dict[str, Any]]]]) -> Dict[int, List[Dict[str, Any]]]:
        try:
            return self._pool.submit(self._call, todo).result(timeout=self.timeout)
        except FutureTimeout:
            log.warning("condition summary AI timeout")
        except Exception as e:                                          # 요약은 보조 정보이므로 실패해도 응답은 계속
            log.warning("condition summary AI failed: %s", type(e).__name__)
        return {}

    def _call(self, todo) -> Dict[int, List[Dict[str, Any]]]:
        parts = []
        for i, (_, rows) in enumerate(todo, 1):
            parts.append("공고 {}\n".format(i) + "\n".join("{}. {}{}".format(k, "[{}] ".format(r["variant"]) if r.get("variant") else "", row_text(r)) for k, r in enumerate(rows)))
        system = SYSTEM.format(school=self.school) + "\n\n" + brief_for_ai(self.rules)
        if self.rules.summary_notes:
            system += "\n[요약 지침]\n" + "\n".join("- " + n for n in self.rules.summary_notes)
        resp = self.client.complete(system, "\n\n".join(parts), [], max_tokens=3000)
        log.info("condition summary AI tokens in=%s out=%s", getattr(resp, "input_tokens", 0), getattr(resp, "output_tokens", 0))
        m = re.search(r"\[.*\]", getattr(resp, "text", "") or "", re.S)
        data = json.loads(m.group(0)) if m else []
        res: Dict[int, List[Dict[str, Any]]] = {}
        for d in data if isinstance(data, list) else []:
            if isinstance(d, dict) and isinstance(d.get("n"), int) and 1 <= d["n"] <= len(todo):
                g = validate_ai(d.get("groups"), todo[d["n"] - 1][1], self.rules)
                if g:
                    res[d["n"]] = g
        return res


def build(settings, rules: Rules, client: Any = None) -> ConditionSummarizer:
    """설정으로 만든다. AI 가 꺼져 있거나 키가 없으면 client=None (코드 대체만 한다)."""
    if client is None and getattr(settings, "condition_summary_ai", False) and getattr(settings, "ai_api_key", ""):
        try:
            from ..common.ai.client import make_client
            client = make_client(settings, max_tokens=3000, timeout=max(2, int(settings.condition_summary_timeout)))
        except Exception as e:
            log.warning("condition summary AI disabled: %s", type(e).__name__)
            client = None
    return ConditionSummarizer(client, rules, timeout=float(settings.condition_summary_timeout), max_items=int(settings.condition_summary_max_items))
