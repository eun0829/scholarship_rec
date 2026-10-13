# -*- coding: utf-8 -*-
"""[COMMON] AI 호출 -> JSON 파싱 -> 스키마 검증. 파싱 실패 시 1회 재시도."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from .client import AIClient, AIError
from .prompt import Payload
from .schema import normalize

log = logging.getLogger(__name__)


@dataclass
class AIResult:
    ok: bool
    data: Optional[Dict[str, Any]] = None
    issues: List[Dict[str, str]] = field(default_factory=list)
    model: str = ""
    raw_text: str = ""
    error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""
    attempts: int = 0


_FENCE = re.compile(r"```(?:json|JSON)?")
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def parse_json(text: str) -> Optional[dict]:
    """응답에서 JSON 객체 하나를 뽑는다. 앞뒤 설명문, 코드블록, 객체 뒤의 잔여 텍스트, 문자열 안 줄바꿈,
    끝의 쉼표를 허용한다. (잘려서 닫히지 않은 JSON 은 복구하지 않는다 -> None)"""
    t = _FENCE.sub("", text or "").strip()
    dec = json.JSONDecoder(strict=False)                   # strict=False: 문자열 안의 줄바꿈/제어문자 허용
    found = []
    for variant in (t, _TRAILING_COMMA.sub(r"\1", t)):    # 원문, 끝의 쉼표를 정리한 문장 둘 다 시도
        pos = variant.find("{")
        tries = 0
        while pos >= 0 and tries < 8:
            try:
                obj, _end = dec.raw_decode(variant[pos:])   # 첫 객체가 끝나는 곳에서 멈춤 (뒤의 텍스트 무시)
                if isinstance(obj, dict):
                    found.append(obj)
            except json.JSONDecodeError:
                pass
            pos = variant.find("{", pos + 1)
            tries += 1
    if not found:
        return None
    for obj in found:                                      # 스키마 키가 있는 객체를 우선
        if "category" in obj or "apply_end" in obj:
            return obj
    return max(found, key=lambda o: len(json.dumps(o, ensure_ascii=False)))


def run_extraction(client: AIClient, payload: Payload, posted: Optional[date], max_attempts: int = 3,
                   tokens_limit: int = 16000) -> AIResult:
    """AI 호출 -> JSON 파싱 -> 검증.
    - 응답이 max_tokens 에서 잘리면 한도를 2배(최대 tokens_limit)로 올려 다시 호출
    - JSON 이 아니면 '설명 없이 JSON 만' 경고를 붙여 다시 호출
    실패해도 마지막 응답 원문(raw_text)과 중단 사유(stop_reason)를 돌려준다."""
    tin = tout = 0
    raw, model, error, stop = "", "", "", ""
    user_text = payload.user_text
    cur_tokens = getattr(client, "max_tokens", 4000)
    override: Optional[int] = None
    attempts = 0
    bad_responses = 0                                      # JSON 이 아니거나 스키마가 틀린 응답 횟수 (2회면 포기)
    for attempt in range(1, max_attempts + 1):
        attempts = attempt
        try:
            resp = (client.complete(payload.system, user_text, payload.images, max_tokens=override)
                    if override else client.complete(payload.system, user_text, payload.images))
        except AIError as e:
            return AIResult(ok=False, error="AI 호출 실패: {}".format(e), raw_text=raw, input_tokens=tin,
                            output_tokens=tout, stop_reason=stop, attempts=attempts)
        raw, model, stop = resp.text, resp.model, resp.stop_reason
        tin += resp.input_tokens
        tout += resp.output_tokens

        if resp.truncated:
            used = override or cur_tokens
            nxt = min(used * 2, tokens_limit)
            error = "응답이 출력 한도(max_tokens {})에서 잘림 (출력 {}토큰)".format(used, resp.output_tokens)
            if nxt <= used:
                log.warning("AI 응답 잘림: 더 올릴 수 없음 (상한 %d)", tokens_limit)
                break                                      # 이미 상한 - 더 시도해도 같은 결과
            log.warning("AI 응답 잘림 (max_tokens %d): 한도를 %d 로 올려 재시도", used, nxt)
            override = nxt
            continue
        obj = parse_json(raw)
        if obj is not None:
            try:
                data, issues = normalize(obj, posted, payload.source_text, version=payload.schema_version)
            except ValueError as e:
                error = "스키마 오류: {}".format(e)
            else:
                return AIResult(ok=True, data=data, issues=issues, model=model, raw_text=raw, input_tokens=tin,
                                output_tokens=tout, stop_reason=stop, attempts=attempts)
        else:
            error = "응답이 JSON 이 아님 (중단 사유: {}, 길이 {}자)".format(stop or "알 수 없음", len(raw))
        bad_responses += 1
        user_text = payload.user_text + "\n\n[주의] 직전 응답이 올바른 JSON 객체가 아니었습니다. 설명 없이 JSON 객체 하나만 출력하세요."
        log.warning("AI 응답 처리 실패 (시도 %d): %s", attempt, error)
        if bad_responses >= 2:
            break
    return AIResult(ok=False, error=error, model=model, raw_text=raw, input_tokens=tin, output_tokens=tout,
                    stop_reason=stop, attempts=attempts)
