# -*- coding: utf-8 -*-
"""[COMMON] API 게이트웨이 부가 조회 (크레딧 잔액, 모델 목록). 게이트웨이를 쓸 때만 의미가 있다.

  GET {게이트웨이 루트}/credits/  : 월 할당/충전/합계 크레딧
  GET {게이트웨이 루트}/models/   : 이 키로 쓸 수 있는 모델 (?type=llm)
게이트웨이 루트 = AI_BASE_URL 에서 끝의 '/claude' 를 뺀 주소 (예: https://factchat-cloud.mindlogic.ai/v1/gateway)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from .client import ANTHROPIC_OFFICIAL, OPENAI_DEFAULT_BASE, UA


def is_gateway(base_url: str) -> bool:
    b = (base_url or "").rstrip("/")
    return bool(b) and b not in (ANTHROPIC_OFFICIAL, OPENAI_DEFAULT_BASE)


def gateway_root(base_url: str) -> str:
    b = (base_url or "").rstrip("/")
    for suffix in ("/claude", "/gemini"):
        if b.endswith(suffix):
            b = b[: -len(suffix)]
    return b


def _get(root: str, path: str, key: str, timeout: int = 20, params: Optional[dict] = None):
    headers = {"Authorization": "Bearer {}".format(key), "x-api-key": key, "User-Agent": UA}
    last = None
    for p in (path + "/", path):                           # 문서의 경로는 끝 '/' 포함
        r = requests.get(root + p, headers=headers, timeout=timeout, params=params, allow_redirects=False)
        if r.status_code == 200:
            return r.json()
        last = r
        if r.status_code not in (301, 302, 307, 308, 404, 405):
            break
    raise RuntimeError("HTTP {}: {}".format(last.status_code, last.text[:200]))


def fetch_credits(base_url: str, key: str) -> Dict[str, Any]:
    return _get(gateway_root(base_url), "/credits", key)


def fetch_models(base_url: str, key: str, model_type: str = "llm") -> List[Dict[str, Any]]:
    data = _get(gateway_root(base_url), "/models", key, params={"type": model_type} if model_type else None)
    items = data.get("data") if isinstance(data, dict) else data
    return [m for m in (items or []) if isinstance(m, dict)]


def format_credits(c: Dict[str, Any]) -> List[str]:
    """응답에서 사람이 볼 줄을 만든다 (하위 필드 이름이 문서에 일부 가려져 있어 숫자/날짜 필드를 있는 대로 표시)."""
    labels = (("monthly_allocated", "월 할당"), ("purchased", "충전"), ("total", "합계"))
    lines = []
    for key, label in labels:
        part = c.get(key)
        if not isinstance(part, dict):
            continue
        fields = ", ".join("{}={}".format(k, round(v, 2) if isinstance(v, float) else v)
                           for k, v in part.items() if isinstance(v, (int, float, str)) and v != "")
        lines.append("  {}: {}".format(label, fields))
    return lines or ["  (알 수 없는 응답 형식) {}".format(str(c)[:200])]
