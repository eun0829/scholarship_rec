# -*- coding: utf-8 -*-
"""[COMMON] 항목 사전(item registry): 학생 항목과 공고 조건 항목을 한 곳(config/item_registry.json)에서 정의한다.

사전에 항목 하나를 적으면 아래가 같은 정의에서 나온다 (코드를 고치지 않고 항목을 늘리기 위한 장치).
  - 학생 입력 라벨(동의어 포함)과 값 형식 검증      -> label_index(), 값 종류(type/enum/range)
  - 후속 질문 문구                                   -> question
  - 공고 구조화(AI) 프롬프트의 '항목 사전' 부분      -> prompt_lines()
  - 공고 조건 필드와 비교 방식의 연결                -> notice_path, op, custom
  - 사전에 없는 조건을 나중에 찾아내는 키워드 힌트   -> detect_hints (증분 보강에서 사용)

잘못된 사전은 로드할 때 한꺼번에 오류로 알려 준다 (오타·중복 라벨·없는 공고 필드 경로가 판정 오류로 번지지 않게).
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from .ai.schema_v3 import SPECIAL_CODES, V3_PATHS

KINDS = ("student", "school", "derived")                    # 학생 정보 / 학교 고정 속성 / 다른 값에서 계산하는 값
SOURCES = ("school_db", "student", "school_fixed", "derived")
TYPES = ("int", "number", "gpa", "enum", "place", "bucket", "list", "text", "bool", "history", "term")
OPS = ("gte", "lte", "in", "place_in", "truthy", "custom")
TIERS = (1, 2, 3)                                          # 1 필수 / 2 권장 / 3 선택(희귀)
ALLOWED_KEYS = {"code", "label", "aliases", "group", "tier", "kind", "source", "type", "enum", "enum_codes", "range", "sensitive", "question",
                "profile_field", "notice_path", "op", "custom", "special_code", "detect_hints", "description", "enabled"}
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,23}$")
_CUSTOM = re.compile(r"^[a-z][a-z0-9_]{0,29}$")
DEFAULT_FILE = "config/item_registry.json"


class RegistryError(ValueError):
    """항목 사전이 올바르지 않음 (오류 목록을 줄바꿈으로 묶어 메시지에 담는다)."""


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "")).lower()


@dataclass(frozen=True)
class Item:
    code: str
    label: str
    kind: str
    source: str
    type: str
    tier: int
    aliases: tuple = ()
    group: str = ""
    enum: tuple = ()
    enum_codes: tuple = ()                      # enum 의 표시 이름과 같은 순서의 표준 코드 (학생 입력의 표시 이름 <-> 공고의 표준 코드)
    range: Optional[tuple] = None
    sensitive: bool = False
    question: str = ""
    profile_field: str = ""
    notice_path: str = ""
    op: str = ""
    custom: str = ""
    special_code: str = ""
    detect_hints: tuple = ()
    description: str = ""
    enabled: bool = True

    def hint_patterns(self) -> List["re.Pattern"]:
        return [re.compile(h, re.I) for h in self.detect_hints]

    def code_for(self, word: str) -> Optional[str]:
        """enum 표시 이름 -> 표준 코드 (enum_codes 가 없으면 None)"""
        return self.enum_codes[self.enum.index(word)] if self.enum_codes and word in self.enum else None

    def word_for(self, code: str) -> Optional[str]:
        """표준 코드 -> enum 표시 이름"""
        return self.enum[self.enum_codes.index(code)] if self.enum_codes and code in self.enum_codes else None


def validate(data: Any, valid_paths: Optional[Set[str]] = None) -> List[str]:
    """사전 데이터의 오류 목록. 비어 있으면 올바른 사전이다."""
    paths = V3_PATHS if valid_paths is None else valid_paths
    errs: List[str] = []
    if not isinstance(data, dict):
        return ["최상위가 객체가 아님"]
    if not isinstance(data.get("version"), int) or data["version"] < 1:
        errs.append("version: 1 이상의 정수가 필요함")
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return errs + ["items: 항목 목록이 비어 있음"]
    codes: Dict[str, int] = {}
    labels: Dict[str, str] = {}
    for i, it in enumerate(items):
        where = "items[{}]".format(i)
        if not isinstance(it, dict):
            errs.append("{}: 객체가 아님".format(where))
            continue
        code = it.get("code")
        if isinstance(code, str):
            where = "{} ({})".format(where, code)
        if not isinstance(code, str) or not _CODE.match(code):
            errs.append("{}: code 는 영문 대문자·숫자·밑줄(최대 24자)이어야 함: {!r}".format(where, code))
        elif code in codes:
            errs.append("{}: code 가 items[{}] 와 중복됨".format(where, codes[code]))
        else:
            codes[code] = i
        unknown = [k for k in it if k not in ALLOWED_KEYS and not str(k).startswith("_")]
        if unknown:
            errs.append("{}: 알 수 없는 키 {} (오타가 아닌지 확인)".format(where, unknown))
        label = it.get("label")
        if not isinstance(label, str) or not label.strip():
            errs.append("{}: label 이 비어 있음".format(where))
        else:
            aliases = it.get("aliases", [])
            if not isinstance(aliases, list) or not all(isinstance(a, str) and a.strip() for a in aliases):
                errs.append("{}: aliases 는 문자열 목록이어야 함".format(where))
                aliases = []
            for name in [label] + list(aliases):
                key = _norm(name)
                if key in labels and labels[key] != code:
                    errs.append("{}: 라벨/동의어 {!r} 가 {} 와 겹침 (같은 라벨이 둘이면 학생 입력을 구분할 수 없음)".format(where, name, labels[key]))
                else:
                    labels[key] = code
        for k, allowed in (("kind", KINDS), ("source", SOURCES), ("type", TYPES)):
            if it.get(k) not in allowed:
                errs.append("{}: {} 는 {} 중 하나여야 함: {!r}".format(where, k, "/".join(allowed), it.get(k)))
        if it.get("tier") not in TIERS:
            errs.append("{}: tier 는 1(필수)/2(권장)/3(선택) 중 하나여야 함: {!r}".format(where, it.get("tier")))
        typ = it.get("type")
        en = it.get("enum")
        if typ in ("enum", "bucket"):
            if not isinstance(en, list) or not en or not all(isinstance(x, str) and x for x in en):
                errs.append("{}: type={} 는 enum(허용 값 목록)이 필요함".format(where, typ))
            elif len(set(en)) != len(en):
                errs.append("{}: enum 에 중복 값이 있음".format(where))
        elif en is not None:
            errs.append("{}: enum 은 type 이 enum/bucket 일 때만 쓸 수 있음".format(where))
        ec = it.get("enum_codes")
        if ec is not None:
            if typ not in ("enum", "bucket") or not isinstance(en, list):
                errs.append("{}: enum_codes 는 enum 이 있는 항목에서만 쓸 수 있음".format(where))
            elif not isinstance(ec, list) or len(ec) != len(en) or not all(isinstance(x, str) and _CODE.match(x) for x in ec) or len(set(ec)) != len(ec):
                errs.append("{}: enum_codes 는 enum 과 같은 개수의 서로 다른 코드(영문 대문자·숫자·밑줄)여야 함".format(where))
        rg = it.get("range")
        if rg is not None:
            if (not isinstance(rg, list) or len(rg) != 2 or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in rg)
                    or rg[0] > rg[1]):
                errs.append("{}: range 는 [최소, 최대] 숫자 두 개여야 함".format(where))
            elif typ not in ("int", "number", "gpa"):
                errs.append("{}: range 는 type 이 int/number/gpa 일 때만 쓸 수 있음".format(where))
        if "sensitive" in it and not isinstance(it["sensitive"], bool):
            errs.append("{}: sensitive 는 true/false 여야 함".format(where))
        if "enabled" in it and not isinstance(it["enabled"], bool):
            errs.append("{}: enabled 는 true/false 여야 함".format(where))
        if it.get("source") == "student" and not (isinstance(it.get("question"), str) and it["question"].strip()):
            errs.append("{}: 학생이 직접 고르는 항목(source=student)은 후속 질문 문구(question)가 필요함".format(where))
        path, op, custom = it.get("notice_path"), it.get("op"), it.get("custom")
        if path is not None:
            if path not in paths:
                errs.append("{}: notice_path {!r} 는 공고 스키마(v3)에 없는 경로임".format(where, path))
            if op not in OPS:
                errs.append("{}: notice_path 가 있으면 op 는 {} 중 하나여야 함: {!r}".format(where, "/".join(OPS), op))
        elif op is not None or custom is not None:
            errs.append("{}: op/custom 은 notice_path 가 있을 때만 쓸 수 있음".format(where))
        if op == "custom":
            if not (isinstance(custom, str) and _CUSTOM.match(custom)):
                errs.append("{}: op=custom 이면 custom(비교 함수 이름, 소문자·밑줄)이 필요함".format(where))
        elif custom is not None:
            errs.append("{}: custom 은 op=custom 일 때만 쓸 수 있음".format(where))
        sc = it.get("special_code")
        if sc is not None and (typ != "bool" or sc not in SPECIAL_CODES):
            errs.append("{}: special_code 는 type=bool 항목에서 {} 중 하나만 쓸 수 있음: {!r}".format(where, "/".join(SPECIAL_CODES), sc))
        hints = it.get("detect_hints", [])
        if not isinstance(hints, list):
            errs.append("{}: detect_hints 는 정규식 문자열 목록이어야 함".format(where))
        else:
            for h in hints:
                try:
                    if not isinstance(h, str) or not h or len(h) > 200:
                        raise re.error("빈 값이거나 200자 초과")
                    re.compile(h)
                except re.error as e:
                    errs.append("{}: detect_hints 정규식 오류 {!r}: {}".format(where, h, e))
    return errs


@dataclass
class Registry:
    version: int
    items: List[Item] = field(default_factory=list)

    def __post_init__(self):
        self.by_code: Dict[str, Item] = {it.code: it for it in self.items}

    def get(self, code: str) -> Optional[Item]:
        return self.by_code.get(code)

    def enabled(self) -> List[Item]:
        return [it for it in self.items if it.enabled]

    def codes(self) -> List[str]:
        return [it.code for it in self.enabled()]

    def label_index(self) -> Dict[str, Item]:
        """정규화한 라벨/동의어(공백 제거) -> 항목. 학생 입력 텍스트를 읽을 때 쓴다."""
        idx: Dict[str, Item] = {}
        for it in self.enabled():
            for name in (it.label,) + tuple(it.aliases):
                idx[_norm(name)] = it
        return idx

    def paths(self) -> Set[str]:
        return {it.notice_path for it in self.enabled() if it.notice_path}

    def prompt_lines(self) -> List[str]:
        """공고 구조화 프롬프트의 '항목 사전' 부분. 공고 필드에 연결된 항목만, 등급 순서로."""
        rows = sorted((it for it in self.enabled() if it.notice_path), key=lambda it: (it.tier, self.items.index(it)))
        return ["- {} ({}): {}".format(it.label, it.code, it.description or it.notice_path) for it in rows]

    def with_items(self, extra: Sequence[Dict[str, Any]]) -> "Registry":
        """항목을 더한 새 사전 (검증 포함). 시험이나 미리 보기에서 쓴다."""
        data = {"version": self.version, "items": [_to_dict(it) for it in self.items] + list(extra)}
        return from_data(data)


def _to_dict(it: Item) -> Dict[str, Any]:
    d: Dict[str, Any] = {"code": it.code, "label": it.label, "kind": it.kind, "source": it.source, "type": it.type, "tier": it.tier}
    for k in ("aliases", "enum", "enum_codes", "detect_hints"):
        if getattr(it, k):
            d[k] = list(getattr(it, k))
    for k in ("group", "question", "profile_field", "notice_path", "op", "custom", "special_code", "description"):
        if getattr(it, k):
            d[k] = getattr(it, k)
    if it.range is not None:
        d["range"] = list(it.range)
    if it.sensitive:
        d["sensitive"] = True
    if not it.enabled:
        d["enabled"] = False
    return d


def from_data(data: Any, valid_paths: Optional[Set[str]] = None) -> Registry:
    errs = validate(data, valid_paths)
    if errs:
        raise RegistryError("항목 사전 오류 {}건:\n  - ".format(len(errs)) + "\n  - ".join(errs))
    items = []
    for it in data["items"]:
        items.append(Item(
            code=it["code"], label=it["label"].strip(), kind=it["kind"], source=it["source"], type=it["type"], tier=it["tier"],
            aliases=tuple(it.get("aliases", [])), group=it.get("group", ""), enum=tuple(it.get("enum", [])), enum_codes=tuple(it.get("enum_codes", [])),
            range=tuple(it["range"]) if it.get("range") is not None else None, sensitive=bool(it.get("sensitive", False)),
            question=it.get("question", ""), profile_field=it.get("profile_field", ""), notice_path=it.get("notice_path", ""),
            op=it.get("op", ""), custom=it.get("custom", ""), special_code=it.get("special_code", ""),
            detect_hints=tuple(it.get("detect_hints", [])), description=it.get("description", ""), enabled=bool(it.get("enabled", True))))
    return Registry(version=data["version"], items=items)


def default_path(configured: str = "") -> Path:
    """사전 파일 위치: 설정(ITEM_REGISTRY_FILE) > 현재 폴더의 config/ > 프로젝트 폴더의 config/."""
    if configured:
        return Path(configured)
    env = os.environ.get("ITEM_REGISTRY_FILE", "").strip()
    if env:
        return Path(env)
    here = Path(DEFAULT_FILE)
    if here.is_file():
        return here
    return Path(__file__).resolve().parents[2] / DEFAULT_FILE


def load_registry(path: Optional[str] = None) -> Registry:
    p = default_path(path or "")
    try:
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise RegistryError("항목 사전 파일을 찾을 수 없음: {}".format(p))
    except ValueError as e:
        raise RegistryError("항목 사전 파일이 올바른 JSON 이 아님 ({}): {}".format(p, e))
    return from_data(raw)
