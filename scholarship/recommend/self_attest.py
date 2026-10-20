# -*- coding: utf-8 -*-
"""[RECOMMEND] 본인 확인 항목: 공고 문장 조건 중 학생이 신청할 때 스스로 확인하는 것(징계 이력, 중복 수혜, 서류 제출 가능 등)을 가려낸다.

이런 문장은 코드가 판정할 수 없지만 학생이 알고 있는 내용이라, 충족 판정을 막지 않고 결과에 '본인 확인'으로 표시한다.
규칙은 config/self_attest_rules.json (코드 수정 없이 고침).  시험: python -m scholarship.recommend.self_attest "정학 이상 징계 처분자 제외"
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..common.config import CONFIG_DIR

_cache: Dict[str, Any] = {"mtime": None, "path": None, "rules": None}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _load(path: Optional[str] = None) -> Dict[str, Any]:
    p = Path(path) if path else CONFIG_DIR / "self_attest_rules.json"
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {"exclude": [], "rules": []}
    if _cache["path"] == str(p) and _cache["mtime"] == mtime:
        return _cache["rules"]
    data = json.loads(p.read_text(encoding="utf-8"))
    rules = {"exclude": [_norm(x) for x in data.get("제외_공통", [])],
             "rules": [{"name": r["이름"], "words": [_norm(w) for w in r.get("포함", [])]} for r in data.get("규칙", []) if r.get("사용", True) and r.get("포함")]}
    _cache.update(mtime=mtime, path=str(p), rules=rules)
    return rules


def match(text: str, path: Optional[str] = None) -> Optional[str]:
    """본인 확인 항목이면 해당 규칙 이름, 아니면 None. 파일이 없거나 읽을 수 없으면 None (기존처럼 확인 필요로 남는다)."""
    try:
        cfg = _load(path)
    except (ValueError, KeyError, TypeError):
        return None
    t = _norm(text)
    if not t or any(w in t for w in cfg["exclude"]):
        return None
    for r in cfg["rules"]:
        if any(w in t for w in r["words"]):
            return r["name"]
    return None


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("사용법: python -m scholarship.recommend.self_attest \"조건 문장\"")
        return 2
    for t in args:
        r = match(t)
        print("{}  ->  {}".format(t, "본인 확인 ({})".format(r) if r else "확인 필요 (코드 판정 불가)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
