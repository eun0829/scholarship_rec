# -*- coding: utf-8 -*-
"""[CHAT] POST /v1/chat 요청 검증. 틀리면 ApiError(INVALID_REQUEST)로 필드별 사유와 함께 거절한다.

요청: {request_id?, question, notice_ids[1..10], focus?, history[0..6], share_results?, profile?, today?}
- share_results 가 true 일 때만 profile 을 쓴다 (학생이 판정 결과를 AI 에 전달하는 데 동의한 경우). false 이거나 없으면 profile 은 읽지도 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from ..recommend.request import REQUEST_ID, ApiError

MAX_QUESTION = 500
MAX_NOTICES = 10
MAX_HISTORY = 6
MAX_HISTORY_TEXT = 1500
NOTICE_ID = re.compile(r"^[A-Z0-9]{2,10}:[A-Za-z0-9._-]{1,40}$")
KNOWN_KEYS = ("request_id", "question", "notice_ids", "focus", "history", "share_results", "profile", "today")


@dataclass
class ChatRequest:
    question: str
    notice_ids: List[str]
    request_id: Optional[str] = None
    focus: str = ""
    history: List[Dict[str, str]] = field(default_factory=list)
    share_results: bool = False
    profile: Optional[Dict[str, Any]] = None
    today: Optional[date] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def pairs(self):
        return [tuple(i.split(":", 1)) for i in self.notice_ids]


def parse_chat_request(body: Any) -> ChatRequest:
    if not isinstance(body, dict):
        raise ApiError("INVALID_REQUEST", "요청 본문은 JSON 객체여야 합니다", [{"field": "$", "message": "객체가 아닙니다"}])
    errs: List[Dict[str, str]] = []

    def bad(name: str, msg: str) -> None:
        errs.append({"field": name, "message": msg})

    rid = body.get("request_id")
    if rid is not None and not (isinstance(rid, str) and REQUEST_ID.match(rid)):
        bad("request_id", "영문·숫자·`._-` 1~64자여야 합니다")
        rid = None
    q = body.get("question")
    if not (isinstance(q, str) and q.strip()):
        bad("question", "필수 항목이며 비어 있지 않은 문자열이어야 합니다")
        q = ""
    elif len(q) > MAX_QUESTION:
        bad("question", "{}자 이하여야 합니다".format(MAX_QUESTION))
    ids = body.get("notice_ids")
    if not (isinstance(ids, list) and 1 <= len(ids) <= MAX_NOTICES and all(isinstance(i, str) and NOTICE_ID.match(i) for i in ids)):
        bad("notice_ids", "공고 id('EXT01:153381' 꼴) 문자열의 배열이어야 합니다 (1~{}개)".format(MAX_NOTICES))
        ids = []
    ids = list(dict.fromkeys(ids))
    focus = body.get("focus", "")
    if focus in (None, ""):
        focus = ""
    elif not (isinstance(focus, str) and focus in ids):
        bad("focus", "notice_ids 안에 있는 공고 id 여야 합니다")
        focus = ""
    hist = body.get("history", [])
    clean_hist: List[Dict[str, str]] = []
    if hist is None:
        hist = []
    if not (isinstance(hist, list) and len(hist) <= MAX_HISTORY):
        bad("history", "최근 {}개 이하의 배열이어야 합니다".format(MAX_HISTORY))
    else:
        for n, h in enumerate(hist):
            if not (isinstance(h, dict) and h.get("role") in ("user", "assistant") and isinstance(h.get("text"), str) and len(h["text"]) <= MAX_HISTORY_TEXT):
                bad("history[{}]".format(n), "{{role: user|assistant, text: {}자 이하}} 꼴이어야 합니다".format(MAX_HISTORY_TEXT))
            else:
                clean_hist.append({"role": h["role"], "text": h["text"]})
    share = body.get("share_results", False)
    if not isinstance(share, bool):
        bad("share_results", "true 또는 false 여야 합니다")
        share = False
    profile = body.get("profile")
    if profile is not None and not isinstance(profile, dict):
        bad("profile", "JSON 객체여야 합니다")
        profile = None
    if share and profile is None and not errs:
        bad("profile", "share_results 가 true 이면 profile 이 필요합니다")
    today = None
    if body.get("today") is not None:
        try:
            today = date.fromisoformat(body["today"]) if isinstance(body["today"], str) else None
        except ValueError:
            today = None
        if today is None:
            bad("today", "YYYY-MM-DD 형식이어야 합니다")
    if errs:
        raise ApiError("INVALID_REQUEST", "요청 형식이 올바르지 않습니다 ({}개 항목)".format(len(errs)), errs)
    warnings: List[str] = []
    unknown = sorted(k for k in body if k not in KNOWN_KEYS)
    if unknown:
        warnings.append("알 수 없는 요청 항목은 무시합니다: {}".format(", ".join(unknown)))
    if profile is not None and not share:
        warnings.append("share_results 가 true 가 아니어서 profile 은 사용하지 않았습니다")
        profile = None
    return ChatRequest(question=q.strip(), notice_ids=ids, request_id=rid, focus=focus, history=clean_hist,
                       share_results=share, profile=profile if share else None, today=today, warnings=warnings)
