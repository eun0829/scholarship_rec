# -*- coding: utf-8 -*-
"""[RECOMMEND] 저장된 공고의 문장 조건 중 항목 사전의 detect_hints 에 걸리는데 구조로는 담기지 않은 것을 찾는다 (AI 호출 없음).

프롬프트나 항목 사전을 고친 뒤 '어떤 공고를 다시 추출하면 좋아지는지'를 고르는 용도다.
  - 문장: 공통·유형의 other_conditions 와 unmodeled_conditions 의 text
  - 이미 그 항목이 구조로 담긴 공고(ext02_trial.detect_codes)는 건너뛴다 (문장이 보충 설명일 수 있음)
결과는 참고용이다. 문장을 자동으로 조건으로 바꾸지 않는다 (유형마다 대상이 다른 공고에서 공통 문장이 잘못 승격되는 것을 막기 위해).
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..sources import ext02_trial as T

Hit = Dict[str, str]


def _sentences(elig: Dict[str, Any]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for t in elig.get("other_conditions") or []:
        if isinstance(t, str):
            out.append(("공통", t))
    for u in elig.get("unmodeled_conditions") or []:
        if isinstance(u, dict) and isinstance(u.get("text"), str) and u.get("kind", "ELIGIBILITY") == "ELIGIBILITY":
            out.append(("공통", u["text"]))
    for v in elig.get("variants") or []:
        if isinstance(v, dict):
            name = v.get("name") or "유형"
            out += [(name, t) for t in v.get("other_conditions") or [] if isinstance(t, str)]
    return out


def scan_notice(elig: Dict[str, Any], registry, codes: Optional[Iterable[str]] = None) -> List[Hit]:
    """공고 한 건: [{code, label, scope, text}]. codes 를 주면 그 항목만 본다."""
    found = T.detect_codes(elig)
    want = set(codes) if codes else None
    items = [it for it in registry.enabled() if it.detect_hints and (want is None or it.code in want)]
    out: List[Hit] = []
    seen = set()
    for scope, text in _sentences(elig):
        for it in items:
            if it.code in found:
                continue
            if any(re.search(h, text, re.I) for h in it.detect_hints):
                key = (it.code, scope, text)
                if key not in seen:
                    seen.add(key)
                    out.append({"code": it.code, "label": it.label, "scope": scope, "text": text})
    return out


def scan_all(notices: Dict[str, Dict[str, Any]], registry, codes: Optional[Iterable[str]] = None) -> Dict[str, List[Hit]]:
    res: Dict[str, List[Hit]] = {}
    for key, rec in notices.items():
        hits = scan_notice(rec.get("elig") or {}, registry, codes)
        if hits:
            res[key] = hits
    return res


def reextract_commands(keys: Iterable[str]) -> List[str]:
    """'EXT01:153587' 형태 키 -> 재추출 명령 (출처별로 묶음)."""
    ext01 = sorted(k.split(":", 1)[1] for k in keys if k.startswith("EXT01:"))
    ext02 = sorted(k.split(":", 1)[1] for k in keys if k.startswith("EXT02:"))
    cmds: List[str] = []
    if ext01:
        cmds.append("python run_ext01.py --days 400 --only {} --force".format(" ".join(ext01)))
    if ext02:
        cmds.append("python run_ext02.py --include-closed --only {} --force".format(" ".join(ext02)))
    return cmds
