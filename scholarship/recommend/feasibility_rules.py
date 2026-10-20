# -*- coding: utf-8 -*-
"""[RECOMMEND] 실현 가능성 규칙 파일(config/feasibility_rules.json) 로드와 검증.

규칙은 코드가 아니라 파일에 있다. 잘못된 파일은 로드할 때 오류를 한꺼번에 알려서(RulesError) 판정 오류로 번지지 않게 한다.
파일이 없거나 깨졌을 때 조용히 내장값으로 바꾸지 않는다.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..common.ai.schema_v3 import MAJOR_GROUPS

LEVELS = ("NORMAL", "HARD", "VERY_HARD")
DEFAULT_FILE = Path(__file__).resolve().parents[2] / "config" / "feasibility_rules.json"
_TOP = {"설명", "등급_이름", "학교_계열", "학교_계열_설명", "기본_등급", "기본_등급_설명", "계열_불일치_등급", "대상_표현", "대상_표현_설명", "계열_키워드", "규칙",
        "서류_제외", "서류_제외_설명", "특이_서류_포함", "특이_서류_포함_설명", "AI_지침", "AI_지침_설명", "요약_지침", "요약_지침_설명"}
_RULE = {"이름", "등급", "이유", "포함", "제외", "설명"}
_FAMILY = {"라벨", "키워드", "설명"}


class RulesError(ValueError):
    pass


@dataclass
class Rule:
    name: str
    level: str
    reason: str
    include: Tuple[str, ...]
    exclude: Tuple[str, ...]


@dataclass
class Rules:
    level_names: Dict[str, str]
    school_majors: Tuple[str, ...]
    default_level: str
    mismatch_level: str
    target_words: Tuple[str, ...]
    families: Dict[str, Dict[str, Any]]
    rules: List[Rule]
    doc_skip: Tuple[str, ...]
    doc_keep: Tuple[str, ...]
    ai_notes: Tuple[str, ...] = ()
    summary_notes: Tuple[str, ...] = ()
    version: str = ""
    path: str = ""

    def label(self, level: str) -> str:
        return self.level_names.get(level, level)

    def major_label(self, code: str) -> str:
        return (self.families.get(code) or {}).get("label") or {"ENGINEERING": "공학계", "NATURAL": "자연계"}.get(code, code)


def _strs(v: Any, where: str, errs: List[str], allow_empty: bool = True) -> Tuple[str, ...]:
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        errs.append("{}: 비어 있지 않은 문자열 목록이어야 합니다".format(where))
        return ()
    if not v and not allow_empty:
        errs.append("{}: 한 개 이상 필요합니다".format(where))
    return tuple(x.strip() for x in v)


def parse_rules(data: Any, path: str = "", raw: str = "") -> Rules:
    errs: List[str] = []
    if not isinstance(data, dict):
        raise RulesError("규칙 파일은 JSON 객체여야 합니다")
    for k in data:
        if k not in _TOP:
            errs.append("알 수 없는 키: {!r} (오타인지 확인하세요)".format(k))
    names = data.get("등급_이름")
    if not isinstance(names, dict) or set(names) != set(LEVELS) or not all(isinstance(x, str) and x for x in names.values()):
        errs.append("등급_이름: NORMAL, HARD, VERY_HARD 세 가지의 표시 문구가 모두 있어야 합니다")
        names = {"NORMAL": "보통", "HARD": "어려움", "VERY_HARD": "매우 어려움"}
    majors = data.get("학교_계열", [])
    if not isinstance(majors, list) or any(m not in MAJOR_GROUPS for m in majors):
        errs.append("학교_계열: {} 중에서 고르세요".format(", ".join(MAJOR_GROUPS)))
        majors = []
    default_level = data.get("기본_등급", "NORMAL")
    mismatch = data.get("계열_불일치_등급", "VERY_HARD")
    for key, val in (("기본_등급", default_level), ("계열_불일치_등급", mismatch)):
        if val not in LEVELS:
            errs.append("{}: {} 중 하나여야 합니다 (현재 {!r})".format(key, ", ".join(LEVELS), val))
    target = _strs(data.get("대상_표현", []), "대상_표현", errs)
    fams: Dict[str, Dict[str, Any]] = {}
    fam_raw = data.get("계열_키워드", {})
    if not isinstance(fam_raw, dict):
        errs.append("계열_키워드: 객체여야 합니다")
        fam_raw = {}
    for code, spec in fam_raw.items():
        if code not in MAJOR_GROUPS:
            errs.append("계열_키워드.{}: 알 수 없는 계열 코드입니다 ({})".format(code, ", ".join(MAJOR_GROUPS)))
            continue
        if not isinstance(spec, dict):
            errs.append("계열_키워드.{}: 객체여야 합니다".format(code))
            continue
        for k in spec:
            if k not in _FAMILY:
                errs.append("계열_키워드.{}: 알 수 없는 키 {!r}".format(code, k))
        kws = _strs(spec.get("키워드", []), "계열_키워드.{}.키워드".format(code), errs, allow_empty=False)
        fams[code] = {"label": str(spec.get("라벨") or code), "keywords": kws}
    rules: List[Rule] = []
    seen = set()
    rr = data.get("규칙", [])
    if not isinstance(rr, list):
        errs.append("규칙: 목록이어야 합니다")
        rr = []
    for i, r in enumerate(rr, 1):
        where = "규칙 #{}".format(i)
        if not isinstance(r, dict):
            errs.append("{}: 객체여야 합니다".format(where))
            continue
        name = r.get("이름")
        where = "규칙 #{} ({})".format(i, name or "이름 없음")
        for k in r:
            if k not in _RULE:
                errs.append("{}: 알 수 없는 키 {!r}".format(where, k))
        if not isinstance(name, str) or not name.strip():
            errs.append("{}: 이름이 필요합니다".format(where))
        elif name in seen:
            errs.append("{}: 이름이 중복됩니다".format(where))
        seen.add(name)
        if r.get("등급") not in LEVELS:
            errs.append("{}: 등급은 {} 중 하나여야 합니다 (현재 {!r})".format(where, ", ".join(LEVELS), r.get("등급")))
        inc = _strs(r.get("포함", []), where + ".포함", errs, allow_empty=False)
        exc = _strs(r.get("제외", []), where + ".제외", errs)
        rules.append(Rule(str(name or ""), str(r.get("등급") or "NORMAL"), str(r.get("이유") or name or ""), inc, exc))
    skip = _strs(data.get("서류_제외", []), "서류_제외", errs)
    keep = _strs(data.get("특이_서류_포함", []), "특이_서류_포함", errs)
    notes = _strs(data.get("AI_지침", []), "AI_지침", errs)
    snotes = _strs(data.get("요약_지침", []), "요약_지침", errs)
    if errs:
        raise RulesError("실현 가능성 규칙 파일 오류 ({}):\n  - ".format(path or "?") + "\n  - ".join(errs))
    version = hashlib.sha1((raw or json.dumps(data, ensure_ascii=False, sort_keys=True)).encode("utf-8")).hexdigest()[:10]
    return Rules(dict(names), tuple(majors), default_level, mismatch, target, fams, rules, skip, keep, notes, snotes, version, path)


def load_rules(path: Optional[str] = None) -> Rules:
    p = Path(path) if path else DEFAULT_FILE
    if not p.is_absolute() and not p.exists():
        p = DEFAULT_FILE.parents[1] / p
    try:
        raw = p.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise RulesError("실현 가능성 규칙 파일을 읽을 수 없습니다: {} ({})".format(p, e))
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise RulesError("실현 가능성 규칙 파일이 올바른 JSON 이 아닙니다: {} ({})".format(p, e))
    return parse_rules(data, str(p), raw)


_CURRENT: Optional[Rules] = None


def use(rules: Rules) -> None:
    """화면 표시(확인사항 간소화 등)가 참조할 규칙을 지정한다. 추천 서비스가 시작할 때 부른다."""
    global _CURRENT
    _CURRENT = rules


def current() -> Rules:
    global _CURRENT
    if _CURRENT is None:
        _CURRENT = load_rules()
    return _CURRENT


def brief_for_ai(rules: Rules) -> str:
    """AI 에 함께 보여 줄 규칙 요약: 등급 정의, 이 학교 계열, 규칙 목록(이름·등급·이유·대표 키워드), 계열 키워드, 사용자가 적은 AI 지침.
    AI 는 규칙에 걸리지 않은 조건만 판단하지만, 같은 기준으로 판단하도록 규칙 전체를 참고하게 한다."""
    L = ["[등급] " + ", ".join("{}={}".format(k, rules.level_names[k]) for k in LEVELS)]
    if rules.school_majors:
        L.append("[이 학교 계열] " + "·".join(rules.major_label(m) for m in rules.school_majors) + " (다른 계열 학생이 대상인 조건은 {})".format(rules.mismatch_level))
    L.append("[이미 정해진 규칙 - 아래에 걸리는 조건은 이 등급을 따름]")
    for r in rules.rules:
        L.append("- {}: {} ({}) 예: {}".format(r.name, r.level, r.reason, ", ".join(r.include[:6])))
    for code, f in rules.families.items():
        L.append("- {} 대상 공고: {} (키워드 예: {})".format(f["label"], rules.mismatch_level, ", ".join(f["keywords"][:6])))
    L.append("[기본 등급] 판단이 어렵거나 근거가 부족하면 {}".format(rules.default_level))
    if rules.ai_notes:
        L.append("[추가 지침]")
        L += ["- " + n for n in rules.ai_notes]
    return "\n".join(L)
