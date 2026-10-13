# -*- coding: utf-8 -*-
"""[RECOMMEND] 학생 정보 텍스트("라벨: 값")를 읽어 프로필 딕셔너리로 만든다. AI·DB 를 쓰지 않는다.

- 줄마다 `라벨: 값` 이다. 순서는 무관하고, `#` 제목 줄은 무시하며, 줄 앞의 `-` 는 있어도 없어도 된다 (전각 콜론·띄어쓰기 변형도 허용).
- 라벨 이름과 동의어, 허용 값, 범위는 항목 사전(config/item_registry.json)에서 가져온다. 사전에 항목을 추가하면 이 파서가 바로 읽는다
  (전용 프로필 필드가 없는 항목은 profile.extra 에 담긴다).
- 읽은 값은 build_profile 이 받는 딕셔너리로 만들어서 JSON 입력과 같은 최종 검증을 거친다 (프로필 JSON 과 같은 길).
- 한 줄이 틀려도 그 항목만 '알 수 없음'이 되고 나머지는 그대로 읽는다. 값을 짐작해서 채우지 않는다.
- 개인정보 라벨(이름, 학번, 주민등록번호, 연락처, 상세 주소 등)과 개인정보 형식의 값은 받지 않는다. 라벨을 먼저 사전과 대조한 뒤에 필터를 적용하므로
  '본인 주민등록' 같은 정상 라벨은 막히지 않는다.
- 학교 고정 항목(학교 소재지·종류·참여 사업)은 서버 설정에서 가져오므로 학생 텍스트에 있어도 무시한다.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..common import registry as R
from ..common.ai.schema_v3 import SPECIAL_CODES
from . import vocab
from .profile import (KNOWN_SCALES, BuiltProfile, ProfileError, StudentProfile, _bool, _int, _num, build_profile, is_sensitive_key,
                      normalize_term)

MAX_BYTES = 20 * 1024                                   # 입력 크기 상한
_DECLINED = {"답하지않음", "미응답", "모름", "답변안함"}
_NONE_WORDS = {"해당없음", "없음", "해당사항없음"}
_SENSITIVE_VALUE = re.compile(r"\d{6}\s*-?\s*[1-8]\d{6}|\b0\d{1,2}[-\s.]?\d{3,4}[-\s.]?\d{4}\b|[\w.+-]+@[\w-]+\.[\w.-]+")
_ENUM_MAPS: Dict[str, Tuple[str, Dict[str, str]]] = {       # 항목 코드 -> (프로필 필드, {사전의 허용 값: 프로필 값})
    "LV": ("status", {"재학": "ENROLLED", "휴학": "LEAVE", "등록휴학": "LEAVE"}),
    "GRAD": ("level", {"학사": "UNIV", "석사": "GRAD", "박사": "GRAD"}),
    "TRF": ("admission_type", {"신입학": "NEW", "편입학": "TRANSFER"}),
    "NAT": ("nationality", {"내국인": "KR", "외국인": "FOREIGN", "재외동포": "OVERSEAS_KR"}),
    "GEN": ("gender", {"남": "M", "여": "F"}),
}
_PLAIN_INT = {"YEAR": ("grade", 1, 6), "SEM": ("semesters_completed", 0, 30), "SLEFT": ("remaining_semesters", 0, 30),
              "ENT": ("entry_year", 1990, 2100), "INC": ("income_bracket", 0, 10), "AGE": ("age", 0, 100)}
_PLAIN_BOOL = {"GRD": "final_semester", "DEG": "degree_held", "NSA": "national_scholarship_applied"}
_PLACE = {"RES": ("residence_sido", "residence_sigungu"), "RESP": ("parent_residence_sido", "parent_residence_sigungu"),
          "HSL": ("hs_sido", "hs_sigungu")}


class _ItemError(Exception):
    """한 항목의 값 오류 (그 항목만 비우고 경고)"""


def _norm(s: str) -> str:
    return R._norm(s)


def _strip_paren(v: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", v).strip()


# ================================================================== 보고서
@dataclass
class ParseReport:
    recognized: List[str] = field(default_factory=list)           # 값을 읽은 항목 코드
    declined: List[str] = field(default_factory=list)             # '답하지 않음'으로 적은 항목 코드
    warnings: List[str] = field(default_factory=list)
    unknown_labels: List[str] = field(default_factory=list)
    dropped_sensitive: List[str] = field(default_factory=list)
    ignored_fixed: List[str] = field(default_factory=list)        # 학교 고정 항목이라 무시한 라벨
    conflicts: List[str] = field(default_factory=list)
    missing: List[Dict[str, Any]] = field(default_factory=list)   # 사전에 있는데 입력에 없는 학생 항목 (등급 순서)

    def as_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in ("recognized", "declined", "warnings", "unknown_labels", "dropped_sensitive", "ignored_fixed",
                                              "conflicts", "missing")}


@dataclass
class ParsedStudent:
    data: Dict[str, Any]
    report: ParseReport


class _Ctx:
    def __init__(self) -> None:
        self.data: Dict[str, Any] = {}
        self.scale: Optional[float] = None
        self.assumed_year: Optional[int] = None

    def set_scale(self, scale: Optional[float]) -> None:
        if scale is None:
            return
        if self.scale is not None and scale != self.scale:
            raise _ItemError("만점({:g})이 앞서 읽은 평점의 만점({:g})과 달라 이 항목을 비움".format(scale, self.scale))
        self.scale = scale
        self.data["gpa_scale"] = scale


# ================================================================== 값 해석
def _number(v: str, item, integer: bool = False, lo: Optional[float] = None, hi: Optional[float] = None) -> float:
    s = _strip_paren(v)
    nums = re.findall(r"-?\d+(?:\.\d+)?", s)
    if len(nums) != 1:
        raise _ItemError("숫자 하나가 필요합니다 ({!r})".format(v))
    x = float(nums[0])
    if integer and x != int(x):
        raise _ItemError("정수가 필요합니다 ({!r})".format(v))
    rg = item.range if item is not None and item.range else (lo, hi)
    if rg and rg[0] is not None and rg[1] is not None and not (rg[0] <= x <= rg[1]):
        raise _ItemError("{:g}~{:g} 범위를 벗어났습니다 ({:g})".format(rg[0], rg[1], x))
    return int(x) if integer else x


def _gpa(v: str) -> Tuple[float, Optional[float]]:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:/\s*(\d+(?:\.\d+)?))?", _strip_paren(v))
    if not m:
        raise _ItemError("'3.79 / 4.5' 형식이어야 합니다 ({!r})".format(v))
    val = float(m.group(1))
    scale = float(m.group(2)) if m.group(2) else None
    if scale is not None and scale not in KNOWN_SCALES:
        raise _ItemError("만점은 {} 중 하나여야 합니다 ({:g})".format("/".join("{:g}".format(s) for s in KNOWN_SCALES), scale))
    if scale is not None and val > scale:
        raise _ItemError("평점({:g})이 만점({:g})보다 큽니다".format(val, scale))
    if val < 0 or val > 5:
        raise _ItemError("평점이 범위(0~5)를 벗어났습니다 ({:g})".format(val))
    return val, scale


def _bool_of(v: str) -> bool:
    try:
        r = _bool(_strip_paren(v), "")
    except ProfileError:
        raise _ItemError("예/아니오로 적어 주세요 ({!r})".format(v))
    if r is None:
        raise _ItemError("값이 비어 있습니다")
    return r


def _enum_word(v: str, item) -> str:
    key = _norm(_strip_paren(v))
    for k in (key, re.sub(r"계열$", "", key)):                            # '공학계열' 처럼 계열을 붙여도 읽는다
        for e in item.enum:
            if _norm(e) == k:
                return e
    raise _ItemError("허용 값이 아닙니다 ({!r}, 허용: {})".format(v, " / ".join(item.enum)))


def _place(v: str, ctx_warn: List[str], label: str) -> Tuple[Optional[str], Optional[str]]:
    toks = _strip_paren(v).split()
    if not toks:
        raise _ItemError("지명이 비어 있습니다")
    if vocab.sido_core(toks[0]):
        sido, rest = toks[0], toks[1:]
    else:
        sido, rest = None, toks
        ctx_warn.append("{}: 시·도가 없어 시·군·구만 저장합니다 (같은 이름이 여러 시·도에 있으면 확인 필요)".format(label))
    return sido, (" ".join(rest) or None)


def bucket_bounds(label: str) -> Tuple[float, Optional[float], bool]:
    """거주 기간 구간 -> (하한, 상한(포함), 열린 구간 여부). '1년 미만' -> (0, 11개월, 아니오), '1~3년' -> (1, 3, 아니오), '3년 이상' -> (3, 없음, 예).
    하한은 '확실히 채웠는지'(PASS), 상한은 '확실히 못 채웠는지'(FAIL)를 가르는 데 쓴다. '미만'은 정확히 그 값은 아니므로 (N-1)년 11개월로 본다."""
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", label)]
    if not nums:
        raise _ItemError("구간 값에서 기간을 읽지 못했습니다 ({!r})".format(label))
    if "미만" in label:
        return 0.0, nums[0] - 1.0 / 12.0, False
    if "이상" in label:
        return nums[0], None, True
    return nums[0], (nums[1] if len(nums) >= 2 else nums[0]), False


def bucket_lower_bound(label: str) -> float:
    """'1년 미만'->0, '1~3년'->1, '3년 이상'->3 (구간의 하한값)"""
    return bucket_bounds(label)[0]


def _history(v: str) -> List[Dict[str, Any]]:
    out = []
    for seg in v.split("|"):
        if not seg.strip():
            continue
        m = re.fullmatch(r"\s*(\S+?)\s+(\d+(?:\.\d+)?)(?:\s+(\d+(?:\.\d+)?))?\s*", _strip_paren(seg))
        term = normalize_term(m.group(1)) if m else None
        if not m or not term:
            raise _ItemError("'2026-1 3.79 15 | 2025-2 3.11 18' 형식이어야 합니다 (읽지 못한 부분: {!r})".format(seg.strip()))
        row = {"term": term, "gpa": float(m.group(2)), "credits": float(m.group(3)) if m.group(3) else None}
        if row["gpa"] > 5:
            raise _ItemError("{} 의 평점({:g})이 범위(0~5)를 벗어났습니다".format(term, row["gpa"]))
        if row["credits"] is not None and row["credits"] > 60:
            raise _ItemError("{} 의 학점({:g})이 범위(0~60)를 벗어났습니다".format(term, row["credits"]))
        if any(r["term"] == term for r in out):
            raise _ItemError("{} 학기가 두 번 들어 있습니다".format(term))
        out.append(row)
    if not out:
        raise _ItemError("학기별 성적이 비어 있습니다")
    return out


CEFR_RANK = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6}
_CEFR_LANGS = ("독일어", "프랑스어", "스페인어", "이탈리아어", "러시아어", "포르투갈어")          # 이 언어는 값 1~6 을 A1~C2 로 보여 준다


def _cefr_letter(key: str, x: float) -> str:
    if key in _CEFR_LANGS and float(x).is_integer() and 1 <= x <= 6:
        return {v: k for k, v in CEFR_RANK.items()}[int(x)]
    return _fmt(x)


def _language(v: str) -> Dict[str, float]:
    if re.fullmatch(r"(해당\s*)?없음|없습니다|없어요", v.strip()):
        return {}                                                      # 어학 성적이 없다고 답함 (답하지 않음과 다르다)
    out: Dict[str, float] = {}
    for part in re.split(r"[,;/]", _strip_paren(v)):
        part = part.strip()
        if not part:
            continue
        c = re.fullmatch(r"([가-힣]+)\s*([ABCabc][12])\s*(?:등급|급)?", part)        # '독일어 B1' = 유럽언어기준(CEFR) 등급 -> 1~6
        if c:
            out[c.group(1)] = float(CEFR_RANK[c.group(2).upper()])
            continue
        m = re.fullmatch(r"([A-Za-z가-힣]+)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:급|점|등급)?", part)
        if not m:
            raise _ItemError("'TOEIC 780, HSK 5급, 독일어 B1' 형식이어야 합니다 (읽지 못한 부분: {!r})".format(part))
        out[m.group(1).upper()] = float(m.group(2))
    if not out:
        raise _ItemError("어학 성적이 비어 있습니다")
    return out


# ================================================================== 항목별 처리
def _handle(item, value: str, ctx: _Ctx, warns: List[str]) -> Dict[str, Any]:
    """한 항목의 값을 프로필 필드 갱신(딕셔너리)으로 바꾼다. 오류는 _ItemError."""
    c = item.code
    if c == "GL":
        val, scale = _gpa(value)
        ctx.set_scale(scale)
        return {"gpa_last": val}
    if c == "GC":
        val, scale = _gpa(value)
        ctx.set_scale(scale)
        return {"gpa_cumulative": val}
    if c == "LTERM":
        t = normalize_term(_strip_paren(value))
        if not t:
            raise _ItemError("'2026-1' 형식이어야 합니다 ({!r})".format(value))
        return {"gpa_last_term": t}
    if c == "CR":
        return {"credits_last": _number(value, item, lo=0, hi=60)}
    if c == "SP":
        return {"gpa_percent": _number(value, item, lo=0, hi=100)}
    if c == "GCP":
        return {"gpa_cumulative_percent": _number(value, item, lo=0, hi=100)}
    if c == "GN":
        return {"term_history": _history(value)}
    if c in _PLAIN_INT:
        f, lo, hi = _PLAIN_INT[c]
        return {f: _number(value, item, integer=True, lo=lo, hi=hi)}
    if c in _PLAIN_BOOL:
        return {_PLAIN_BOOL[c]: _bool_of(value)}
    if item.enum_codes and item.profile_field:                           # 사전이 표시 이름과 표준 코드를 연결한 항목 (예: 학과 계열)
        return {item.profile_field.split("/")[0]: item.code_for(_enum_word(value, item))}
    if c in _ENUM_MAPS and item.enum:
        f, table = _ENUM_MAPS[c]
        word = _enum_word(value, item)
        if word not in table:
            raise _ItemError("'{}' 에 대한 해석 규칙이 없습니다 (student_text._ENUM_MAPS 에 추가 필요)".format(word))
        return {f: table[word]}
    if c in _PLACE:
        sido, sigungu = _place(value, warns, item.label)
        a, b = _PLACE[c]
        return {a: sido, b: sigungu}
    if c == "RESY":
        lo, hi, open_ = bucket_bounds(_enum_word(value, item))
        out: Dict[str, Any] = {"residence_years": lo}
        if open_:
            out["residence_years_open"] = True
        elif hi is not None and hi != lo:
            out["residence_years_upper"] = hi
        return out
    if c == "MAJ":
        return {"major": value.strip()}
    if c == "LANG":
        return {"language_scores": _language(value)}
    return _generic(item, value, warns)


def _generic(item, value: str, warns: List[str]) -> Dict[str, Any]:
    """전용 프로필 필드가 없는 항목(사전에 새로 추가한 항목): 사전의 값 종류(type)대로 읽어서 extra 에 담는다."""
    t = item.type
    if t in ("int", "number"):
        v: Any = _number(value, item, integer=(t == "int"))
    elif t == "bool":
        v = _bool_of(value)
    elif t in ("enum", "bucket"):
        v = _enum_word(value, item)
    elif t == "text":
        v = value.strip()
    elif t == "term":
        v = normalize_term(_strip_paren(value))
        if not v:
            raise _ItemError("'2026-1' 형식이어야 합니다 ({!r})".format(value))
    elif t == "gpa":
        v = _gpa(value)[0]
    elif t == "place":
        sido, sigungu = _place(value, warns, item.label)
        v = {"sido": sido, "sigungu": sigungu}
    elif t == "list":
        v = [s.strip() for s in re.split(r"[,;/]", _strip_paren(value)) if s.strip()]
    else:
        raise _ItemError("값 종류({})에 대한 해석 규칙이 없습니다".format(t))
    return {"extra": {item.code: v}}


# ================================================================== 본문
def parse_student_text(text: str, registry: Optional[R.Registry] = None) -> ParsedStudent:
    """학생 정보 텍스트를 읽는다. 순서와 무관하게 같은 결과를 낸다."""
    if text is None:
        text = ""
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ProfileError("학생 정보 텍스트가 너무 큽니다 ({}KB 초과)".format(MAX_BYTES // 1024))
    reg = registry or R.load_registry()
    idx = reg.label_index()
    rep = ParseReport()
    entries: Dict[str, List[str]] = {}

    # ---- 1) 줄 모으기: 라벨을 먼저 사전과 대조하고, 사전에 없을 때만 개인정보 필터를 적용한다
    for raw in text.splitlines():
        line = unicodedata.normalize("NFKC", raw).strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^[-*•·]\s*", "", line)
        if ":" not in line:
            rep.warnings.append("읽을 수 없는 줄(콜론 없음): {!r}".format(line[:30]))
            continue
        label, _, value = line.partition(":")
        label, value = label.strip(), value.strip()
        item = idx.get(_norm(label))
        if item is None:
            if is_sensitive_key(label):
                rep.dropped_sensitive.append(label)
            else:
                rep.unknown_labels.append(label)
                rep.warnings.append("알 수 없는 라벨: {!r}".format(label[:20]))
            continue
        if _SENSITIVE_VALUE.search(value):
            rep.dropped_sensitive.append("{} (값이 개인정보 형식)".format(label))
            rep.warnings.append("{}: 개인정보 형식의 값이라 받지 않습니다".format(label))
            continue
        if not value:
            continue                                                    # 빈 값은 입력하지 않은 것
        entries.setdefault(item.code, []).append(value)

    # ---- 2) 같은 항목이 여러 번: 같은 값이면 하나로, 다른 값이면 비움(어느 쪽이 맞는지 모름)
    final: Dict[str, str] = {}
    for code, vals in entries.items():
        if len({_norm(v) for v in vals}) > 1:
            lab = reg.get(code).label
            rep.conflicts.append(lab)
            rep.warnings.append("{}: 같은 항목이 서로 다른 값으로 {}번 있어 비웁니다".format(lab, len(vals)))
        else:
            final[code] = vals[0]

    # ---- 3) 사전 순서대로 처리 (줄 순서와 무관하게 결정적: 평점 만점 충돌 때도 앞선 항목이 우선)
    ctx = _Ctx()
    warns: List[str] = []
    special_items = [i for i in reg.enabled() if i.special_code]
    spec_bool: Dict[str, bool] = {}                                      # 신분 코드 -> 개별 답
    agg: Optional[Tuple[str, List[str]]] = None                         # ('none'|'list'|'declined', 항목 코드 목록)
    unknown_special: List[str] = []
    for item in reg.enabled():
        code = item.code
        if item.source == "school_fixed":
            if code in final or code in entries:
                rep.ignored_fixed.append(item.label)
                rep.warnings.append("{}: 학교 고정 항목은 서버 설정에서 가져오므로 무시합니다".format(item.label))
            continue
        if code not in final:
            continue
        value = final[code]
        if _norm(_strip_paren(value)) in _DECLINED:
            rep.declined.append(code)
            if code == "SPECIAL":
                agg = ("declined", [])
                rep.declined.extend(i.code for i in special_items)         # 신분 목록 전체를 답하지 않은 것
            continue
        try:
            if code == "SPECIAL":
                agg, unknown_special = _special_line(value, reg, rep)
                rep.recognized.append(code)
            elif item.special_code:
                spec_bool[item.special_code] = _bool_of(value)
                rep.recognized.append(code)
            else:
                upd = _handle(item, value, ctx, warns)
                for k, v in upd.items():
                    if k == "extra":
                        ctx.data.setdefault("extra", {}).update(v)
                    else:
                        ctx.data[k] = v
                rep.recognized.append(code)
        except _ItemError as e:
            rep.warnings.append("{}: {} -> 비움".format(item.label, e))
    rep.warnings.extend(warns)
    _apply_special(ctx.data, spec_bool, agg, unknown_special, special_items, rep)
    _cross_checks(ctx.data, rep)

    # ---- 4) 입력에 없는 항목 (학생이 답하거나 학교 DB 가 줘야 하는 것. 학교 고정·계산 항목은 제외)
    provided = set(rep.recognized) | set(rep.declined)
    if agg is not None:
        provided |= {i.code for i in special_items} | {"SPECIAL"}
    for item in reg.enabled():
        if item.source in ("student", "school_db") and item.code not in provided:
            rep.missing.append({"code": item.code, "label": item.label, "tier": item.tier, "source": item.source,
                                "sensitive": item.sensitive})
    rep.missing.sort(key=lambda m: m["tier"])
    return ParsedStudent(ctx.data, rep)


def _special_line(value: str, reg: R.Registry, rep: ParseReport) -> Tuple[Tuple[str, List[str]], List[str]]:
    """'해당 항목: 해당 없음 | 다자녀, 한부모'. 목록의 이름은 사전의 신분 항목 라벨·동의어와 대조한다."""
    key = _norm(_strip_paren(value))
    if key in _NONE_WORDS:
        return ("none", []), []
    by_name: Dict[str, str] = {}
    for it in reg.enabled():
        if it.special_code:
            for name in (it.label,) + tuple(it.aliases):
                by_name[_norm(name)] = it.special_code
    codes: List[str] = []
    unknown: List[str] = []
    for tok in re.split(r"[,;/·]", _strip_paren(value)):
        tok = tok.strip()
        if not tok:
            continue
        c = by_name.get(_norm(tok))
        if c:
            if c not in codes:
                codes.append(c)
        else:
            unknown.append(tok)
            rep.warnings.append("해당 항목: 항목 사전에 없는 신분 {!r} (그대로 special_status 에만 담음)".format(tok[:20]))
    return ("list", codes), unknown


def _apply_special(data: Dict[str, Any], spec_bool: Dict[str, bool], agg, unknown: List[str], special_items, rep: ParseReport) -> None:
    """신분 항목의 답을 프로필로: special_codes(해당하는 코드), special_complete(모든 신분 항목에 답했는가).
    목록 줄('해당 항목')이 있으면 그것이 전체 답이고, 개별 줄은 그 항목만 덮어쓴다 (둘이 다르면 개별 줄을 쓰고 경고)."""
    all_codes = [i.special_code for i in special_items]
    answered: Dict[str, bool] = {}
    if agg is not None and agg[0] in ("none", "list"):
        for c in all_codes:
            answered[c] = c in agg[1]
    for c, v in spec_bool.items():
        if c in answered and answered[c] != v:
            rep.warnings.append("{}: '해당 항목' 줄과 개별 항목 줄이 달라 개별 항목 줄을 따릅니다".format(c))
        answered[c] = v
    if not answered and not unknown:
        return
    yes = [c for c in all_codes if answered.get(c)]
    complete = all(c in answered for c in all_codes)
    data["special_codes"] = yes
    data["special_complete"] = complete
    flag = {"BASIC_LIVELIHOOD": "is_basic_livelihood", "NEAR_POOR": "is_near_poor", "SINGLE_PARENT": "is_single_parent"}
    for c, f in flag.items():
        if c in answered:
            data[f] = answered[c]
    if complete:                                                         # v2 판정 코드용 이름 목록 (전부 답했을 때만: [] = 해당 없음)
        name = {i.special_code: (i.aliases[0] if i.aliases else i.label) for i in special_items}
        data["special_status"] = [name[c] for c in yes] + unknown


def _cross_checks(data: Dict[str, Any], rep: ParseReport) -> None:
    """서로 맞지 않는 입력은 고치지 않고 경고만 한다 (어느 쪽이 맞는지 모름)."""
    th = data.get("term_history")
    if th and data.get("gpa_last") is not None and abs(th[0]["gpa"] - data["gpa_last"]) > 0.005:
        rep.warnings.append("직전학기 평점({:g})과 학기별 성적의 가장 최근 값({} {:g})이 다릅니다".format(data["gpa_last"], th[0]["term"], th[0]["gpa"]))
    if th and data.get("gpa_last_term") and th[0]["term"] != data["gpa_last_term"]:
        rep.warnings.append("직전학기({})와 학기별 성적의 가장 최근 학기({})가 다릅니다".format(data["gpa_last_term"], th[0]["term"]))
    scale = data.get("gpa_scale") or 4.5
    for r in th or []:
        if r["gpa"] > scale:
            rep.warnings.append("학기별 성적 {} 의 평점({:g})이 만점({:g})보다 큽니다".format(r["term"], r["gpa"], scale))
            data.pop("term_history")
            break


def format_parse_report(rep: ParseReport, tiers: Tuple[int, ...] = (1, 2)) -> str:
    """사람이 읽는 파싱 보고서. 입력에 없는 항목은 필수(1)·권장(2)만 이름을 보여 주고 선택(3)은 개수만."""
    L = ["[학생 정보 텍스트] 읽은 항목 {}개, 경고 {}개".format(len(rep.recognized), len(rep.warnings))]
    L += ["  경고: " + w for w in rep.warnings]
    if rep.dropped_sensitive:
        L.append("  ※ 개인정보로 보여 받지 않은 항목: " + ", ".join(rep.dropped_sensitive))
    if rep.declined:
        L.append("  답하지 않음으로 표시한 항목: {}개".format(len(rep.declined)))
    shown = [m for m in rep.missing if m["tier"] in tiers]
    rest = len(rep.missing) - len(shown)
    if shown:
        L.append("  입력에 없는 항목: " + ", ".join("{}({})".format(m["label"], {1: "필수", 2: "권장", 3: "선택"}[m["tier"]]) for m in shown)
                 + (" 외 선택 항목 {}개".format(rest) if rest else ""))
    elif rest:
        L.append("  입력에 없는 선택 항목 {}개".format(rest))
    return "\n".join(L)


def profile_from_text(text: str, registry: Optional[R.Registry] = None, default_gpa_scale: Optional[float] = 4.5) -> Tuple[BuiltProfile, ParseReport]:
    """텍스트 -> 최종 프로필. 읽은 값은 JSON 과 같은 검증(build_profile)을 한 번 더 거친다 (그때 나는 오류는 ProfileError)."""
    parsed = parse_student_text(text, registry)
    return build_profile(parsed.data, default_gpa_scale), parsed.report


# ================================================================== 텍스트 만들기 (프로필 -> 텍스트, 빈 양식)
def _fmt(x: float) -> str:
    return "{:g}".format(x)


def profile_to_text(p: StudentProfile, registry: Optional[R.Registry] = None) -> str:
    """프로필을 규격 텍스트로. 읽을 수 있는 값만 적는다 (파서로 다시 읽으면 같은 프로필이 된다)."""
    reg = registry or R.load_registry()
    scale = _fmt(p.gpa_scale or 4.5)
    lines: List[str] = []
    cur_group = None

    def add(item, value: Optional[str]) -> None:
        nonlocal cur_group
        if value is None or value == "":
            return
        if item.group != cur_group:
            lines.append("## " + (item.group or "기타"))
            cur_group = item.group
        lines.append("- {}: {}".format(item.label, value))

    rev = {c: (f, {v: k for k, v in t.items()}) for c, (f, t) in _ENUM_MAPS.items()}
    for it in reg.enabled():
        c = it.code
        if it.source in ("school_fixed",) or it.special_code or c == "SPECIAL":
            continue
        v: Optional[str] = None
        if c == "GL" and p.gpa_last is not None:
            v = "{} / {}".format(_fmt(p.gpa_last), scale)
        elif c == "GC" and p.gpa_cumulative is not None:
            v = "{} / {}".format(_fmt(p.gpa_cumulative), scale)
        elif c == "LTERM":
            v = p.gpa_last_term
        elif c == "CR" and p.credits_last is not None:
            v = _fmt(p.credits_last)
        elif c == "SP" and p.gpa_percent is not None:
            v = _fmt(p.gpa_percent)
        elif c == "GCP" and p.gpa_cumulative_percent is not None:
            v = _fmt(p.gpa_cumulative_percent)
        elif c == "GN" and p.term_history:
            v = " | ".join("{} {}{}".format(r["term"], _fmt(r["gpa"]), " " + _fmt(r["credits"]) if r.get("credits") is not None else "") for r in p.term_history)
        elif c in _PLAIN_INT:
            x = getattr(p, _PLAIN_INT[c][0])
            v = None if x is None else str(x)
        elif c in _PLAIN_BOOL:
            x = getattr(p, _PLAIN_BOOL[c])
            v = None if x is None else ("예" if x else "아니오")
        elif it.enum_codes and it.profile_field:
            code = getattr(p, it.profile_field.split("/")[0], None)
            v = it.word_for(code) if code else None
        elif c in rev and it.enum:
            x = getattr(p, rev[c][0])
            inv = rev[c][1]
            v = next((w for w in it.enum if inv.get(x) == w or _ENUM_MAPS[c][1].get(w) == x), None) if x is not None else None
        elif c in _PLACE:
            a, b = _PLACE[c]
            parts = [s for s in (getattr(p, a), getattr(p, b)) if s]
            v = " ".join(parts) or None
        elif c == "RESY" and p.residence_years is not None and it.enum:
            ok = [e for e in it.enum if bucket_lower_bound(e) <= p.residence_years]
            v = max(ok, key=bucket_lower_bound) if ok else None
        elif c == "MAJ":
            v = p.major
        elif c == "LANG" and p.language_scores is not None:
            v = ", ".join("{} {}".format(k, _cefr_letter(k, x)) for k, x in p.language_scores.items()) or "없음"
        elif not it.profile_field or it.profile_field.split("/")[0] not in StudentProfile.__dataclass_fields__:
            x = (p.extra or {}).get(c)                                  # 전용 필드가 없는 항목
            if isinstance(x, dict):
                v = " ".join(s for s in (x.get("sido"), x.get("sigungu")) if s) or None
            elif isinstance(x, list):
                v = ", ".join(x)
            elif isinstance(x, bool):
                v = "예" if x else "아니오"
            elif x is not None:
                v = str(x)
        add(it, v)
    if p.special_complete:
        grp = next((i.group for i in reg.enabled() if i.code == "SPECIAL"), "소득·신분")
        names = {i.special_code: (i.aliases[0] if i.aliases else i.label) for i in reg.enabled() if i.special_code}
        yes = [names[c] for c in (p.special_codes or []) if c in names]
        label = reg.get("SPECIAL").label if reg.get("SPECIAL") else "해당 항목"
        if grp != cur_group:
            lines.append("## " + grp)
        lines.append("- {}: {}".format(label, ", ".join(yes) if yes else "해당 없음"))
    return "# 학생정보\n" + "\n".join(lines) + "\n"


def _format_hint(it) -> str:
    """값 종류별 입력 예시 (학생이 읽는 안내)."""
    t = it.type
    if it.code == "LANG":
        return "예: TOEIC 780, HSK 5급"
    if it.code == "SPECIAL":
        return "해당 없음 / 답하지 않음 / 해당하는 것을 쉼표로 (예: 다자녀, 한부모)"
    if t == "gpa":
        return "예: 3.79 / 4.5 (만점 표기 권장)"
    if t == "term":
        return "예: 2026-1"
    if t == "history":
        return "예: 2026-1 3.79 15 | 2025-2 3.11 18 (학기 평점 학점, 최근 학기부터)"
    if t in ("enum", "bucket"):
        return "허용 값: " + " / ".join(it.enum)
    if t == "place":
        return "예: 경기도 수원시"
    if t == "bool":
        return "예 / 아니오"
    if t in ("int", "number"):
        return "숫자" + (" ({:g}~{:g})".format(*it.range) if it.range else "")
    return "자유 입력"


def build_template(registry: Optional[R.Registry] = None) -> str:
    """빈 입력 양식: 학생·학교 DB 항목의 라벨과 안내(질문, 입력 예시, 허용 값)를 주석(#)으로 보여 준다. 값을 채워서 쓰면 된다."""
    reg = registry or R.load_registry()
    out = ["# 학생정보 입력 양식 (줄마다 `- 라벨: 값`. # 로 시작하는 줄은 설명이라 무시됩니다. 모르는 항목은 줄을 지우거나 '답하지 않음'이라고 적으세요)"]
    cur = None
    for it in reg.enabled():
        if it.source not in ("student", "school_db") or it.special_code:
            continue
        if it.group != cur:
            out.append("## " + (it.group or "기타"))
            cur = it.group
        out.append("# {} - {}{}".format(it.question or it.label, _format_hint(it), " [민감 정보: 답하지 않아도 됩니다]" if it.sensitive else ""))
        out.append("- {}:".format(it.label))
    return "\n".join(out) + "\n"
