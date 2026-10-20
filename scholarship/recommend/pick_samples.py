# -*- coding: utf-8 -*-
"""[RECOMMEND] 카드 시험용 공고 고르기: 유형이 하나뿐인 공고 N건 + 유형이 여러 개인 공고 N건 (AI 호출 없음).

저장된 공고(export_notices 결과)를 유형 수로 나눠 고른다. 같은 입력이면 항상 같은 공고가 골라진다.
- 유형 1개 이하(variants 0~1개): 글번호 순으로 고르게 퍼뜨려 N건
- 유형 2개 이상: 유형 수가 많은 공고부터가 아니라 '적음·중간·많음'이 섞이도록 N건 (카드가 짧은 경우와 긴 경우를 모두 본다)
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

Notices = Dict[str, Dict[str, Any]]


def variant_count(rec: Dict[str, Any]) -> int:
    v = (rec.get("elig") or {}).get("variants")
    return len(v) if isinstance(v, list) else 0


def _spread(keys: List[str], n: int) -> List[str]:
    """정렬된 목록에서 n개를 고르게 퍼뜨려 뽑는다 (처음·중간·끝이 섞이도록)."""
    if n <= 0 or not keys:
        return []
    if len(keys) <= n:
        return list(keys)
    if n == 1:
        return [keys[len(keys) // 2]]
    idx = sorted({round(i * (len(keys) - 1) / (n - 1)) for i in range(n)})
    return [keys[i] for i in idx]


def pick(notices: Notices, single: int = 3, multi: int = 3) -> Tuple[List[str], List[str]]:
    """(유형 1개 이하 공고 키, 유형 여러 개 공고 키). 모자라면 있는 만큼만 돌려준다."""
    ones = sorted((k for k, r in notices.items() if variant_count(r) < 2), key=lambda k: (notices[k].get("src_id") or "", k))
    many = sorted((k for k, r in notices.items() if variant_count(r) >= 2), key=lambda k: (variant_count(notices[k]), k))
    return _spread(ones, single), _spread(many, multi)
