"""'유형이름: 조건' 꼴 문장을 그 유형의 조건으로 옮기는 공용 규칙 (저장할 때와 판정할 때 같은 함수를 쓴다).

AI 가 유형별 조건을 공통 other_conditions 에 '나비: 자격증 3개 이상' 처럼 적어 두면, 그 유형이 아닌 학생에게도
'확인 필요'가 걸려 어떤 학생도 충족에 이르지 못한다. 문장 앞의 이름이 유형 이름과 맞으면 해당 유형의 조건으로 옮긴다.

안전 규칙 (잘못 옮겨 자격 없는 유형이 통과하는 것이 가장 위험하므로 보수적으로):
- 앞 이름이 일반 낱말('대상', '참고' 등)이거나 어느 유형과도 맞지 않으면 공통에 그대로 둔다.
- 모든 유형에 맞으면 공통 문장과 다르지 않으므로 그대로 둔다.
- 옮긴 문장은 맞은 유형 모두에 넣는다 (같은 문장이 이미 있으면 중복하지 않는다).
- 같은 입력에 몇 번 적용해도 결과가 같다 (멱등).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

VARIANT_PREFIX = re.compile(r"^\s*([^:：]{1,30}?)\s*[:：]\s*\S")

# 유형 이름이 아니라 문장의 머리말로 쓰이는 낱말 (이 낱말로 시작하는 앞 이름은 옮기지 않는다)
GENERIC_PREFIXES = frozenset({
    "대상", "자격", "참고", "비고", "공통", "기타", "유의", "유의사항", "주의", "주의사항", "지원자격", "신청자격", "지원대상", "신청대상",
    "선발기준", "선발대상", "제출서류", "신청방법", "지원방법", "선발", "공고", "안내", "문의", "참조", "필수", "우대", "제외", "단",
})


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def prefix_matches(prefix: str, names) -> bool:
    """문장 앞의 유형 표시가 유형 이름 중 하나와 같은가. '인천인희망드림 고등학생' 처럼 이름 뒤에 말이 붙거나,
    'iHUG' 처럼 이름의 앞부분만 적어도 같은 유형으로 본다 (2글자 미만은 우연히 겹치기 쉬워 제외).
    '애향-통리반장 자녀' 유형의 문장이 '통리반장 자녀: ...' 로 시작하는 경우처럼, 이름이 '묶음-세부' 꼴이면 세부 이름(3글자 이상)으로도 비교한다."""
    toks = prefix.split()
    cands = {re.sub(r"\s+", "", prefix)} | ({toks[0]} if toks else set())            # 'iHUG 고등학생' 은 첫 낱말 'iHUG' 로도 비교
    cands = {c for c in cands if len(c) >= 2}

    def forms(n: str):
        return {n} | {x for x in re.split(r"[-‐–·/]", n) if len(x) >= 3}                # '애향-시정유공' -> '시정유공' ('애향' 같은 묶음 이름은 짧아 제외)

    return any(c == f or (len(f) >= 2 and (c.startswith(f) or f.startswith(c))) for c in cands for n in names for f in forms(n))


def _strip_prefix(text: str) -> str:
    m = re.match(r"^\s*[^:：]{1,30}?\s*[:：]\s*", text)
    rest = text[m.end():].strip() if m else text
    return rest or text


def attach_prefixed_conditions(elig: Dict[str, Any]) -> Dict[str, Any]:
    """공통 other_conditions 중 '유형이름: …' 문장을 맞는 유형의 other_conditions 로 옮긴 사본을 돌려준다 (옮길 것이 없으면 같은 객체)."""
    variants = elig.get("variants") or []
    common = elig.get("other_conditions") or []
    if len(variants) < 2 or not common:
        return elig
    vnames = [_squash(v.get("name") or "") if isinstance(v, dict) else "" for v in variants]
    keep: List[str] = []
    adds: Dict[int, List[str]] = {}
    for text in common:
        m = VARIANT_PREFIX.match(text) if isinstance(text, str) else None
        prefix = m.group(1).strip() if m else ""
        hit = [i for i, n in enumerate(vnames) if n and prefix and _squash(prefix) not in GENERIC_PREFIXES and prefix_matches(prefix, {n})]
        if not hit or len(hit) == len(variants):
            keep.append(text)
            continue
        body = _strip_prefix(text)
        for i in hit:
            adds.setdefault(i, []).append(body)
    if not adds:
        return elig
    new_variants: List[Any] = []
    for i, v in enumerate(variants):
        if i in adds and isinstance(v, dict):
            have = list(v.get("other_conditions") or [])
            have_keys = {_squash(_strip_prefix(x)) for x in have if isinstance(x, str)}
            for b in adds[i]:
                if _squash(b) not in have_keys:
                    have.append(b)
                    have_keys.add(_squash(b))
            v = dict(v, other_conditions=have)
        new_variants.append(v)
    return dict(elig, other_conditions=keep, variants=new_variants)
