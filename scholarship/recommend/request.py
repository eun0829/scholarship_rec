# -*- coding: utf-8 -*-
"""[RECOMMEND] 6-A API 요청 처리 (HTTP 프레임워크와 무관한 순수 함수).

- parse_request: 요청 본문의 구조를 검증한다. 틀리면 ApiError(400, INVALID_REQUEST)로 **필드별 사유**와 함께 거절한다.
- sanitize_profile: 프로필의 값 하나가 틀렸다고 전체를 거절하지 않는다. 틀린 키만 빼고 경고(profile_warnings)로 알리고 나머지로 판정한다.
  남은 키끼리 모순이 있을 때만 거절한다 (INVALID_PROFILE).
- handle_recommend: (HTTP 상태, 본문)을 돌려준다. 6-B 의 HTTP 계층이 이것을 그대로 감싼다.
오류 본문: {"error": {"code", "message", "fields": [{"field", "message"}], "request_id"}}
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from . import profile_contract as PC
from .followup import MAX_QUESTIONS
from .profile import ProfileError, build_profile, is_sensitive_key, reason
from .service import Recommender

MAX_PROFILE_KEYS = 100
MAX_DECLINED = 60
MAX_TEXT = 5000
MAX_ROUND = 10
MAX_QUESTIONS_LIMIT = 10
REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
KNOWN_KEYS = ("request_id", "profile", "answers", "answers_text", "declined", "round", "max_questions", "today", "strict")
ERROR_CODES = {"INVALID_REQUEST": 400, "INVALID_PROFILE": 400, "UNAUTHORIZED": 401, "PAYLOAD_TOO_LARGE": 413, "SERVER_CONFIG": 500, "INTERNAL": 500, "UNAVAILABLE": 503,
               "CHAT_DISABLED": 404, "NOT_FOUND": 404, "BUSY": 429, "CHAT_UNAVAILABLE": 503}


class ApiError(Exception):
    def __init__(self, code: str, message: str, fields: Optional[List[Dict[str, str]]] = None):
        super().__init__(message)
        self.code, self.message, self.fields, self.status = code, message, fields or [], ERROR_CODES[code]

    def body(self, request_id: Optional[str] = None) -> Dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "fields": self.fields, "request_id": request_id}}


@dataclass
class ParsedRequest:
    profile: Dict[str, Any]
    request_id: Optional[str] = None
    answers: Dict[str, Any] = field(default_factory=dict)
    answers_text: Optional[str] = None
    declined: List[str] = field(default_factory=list)
    round_no: int = 1
    max_questions: int = MAX_QUESTIONS
    today: Optional[date] = None
    strict: bool = False
    warnings: List[str] = field(default_factory=list)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse_request(body: Any) -> ParsedRequest:
    if not isinstance(body, dict):
        raise ApiError("INVALID_REQUEST", "요청 본문은 JSON 객체여야 합니다", [{"field": "$", "message": "객체가 아닙니다"}])
    errs: List[Dict[str, str]] = []

    def bad(name: str, msg: str) -> None:
        errs.append({"field": name, "message": msg})

    rid = body.get("request_id")
    if rid is not None and not (isinstance(rid, str) and REQUEST_ID.match(rid)):
        bad("request_id", "영문·숫자·`._-` 1~64자여야 합니다")
        rid = None
    profile = body.get("profile")
    if not isinstance(profile, dict):
        bad("profile", "필수 항목이며 JSON 객체여야 합니다")
        profile = {}
    elif len(profile) > MAX_PROFILE_KEYS:
        bad("profile", "항목이 너무 많습니다 (최대 {}개)".format(MAX_PROFILE_KEYS))
    answers = body.get("answers")
    if answers is not None and not isinstance(answers, dict):
        bad("answers", "JSON 객체여야 합니다")
    elif isinstance(answers, dict) and len(answers) > MAX_PROFILE_KEYS:
        bad("answers", "항목이 너무 많습니다 (최대 {}개)".format(MAX_PROFILE_KEYS))
    text = body.get("answers_text")
    if text is not None and not (isinstance(text, str) and len(text) <= MAX_TEXT):
        bad("answers_text", "문자열이어야 하며 {}자 이하여야 합니다".format(MAX_TEXT))
    declined = body.get("declined")
    if declined is not None and not (isinstance(declined, list) and len(declined) <= MAX_DECLINED and all(isinstance(x, str) and 0 < len(x) <= 24 for x in declined)):
        bad("declined", "항목 코드 문자열의 배열이어야 합니다 (최대 {}개, 각 24자 이하)".format(MAX_DECLINED))
    rnd = body.get("round", 1)
    if not (_is_int(rnd) and 1 <= rnd <= MAX_ROUND):
        bad("round", "1~{} 사이의 정수여야 합니다".format(MAX_ROUND))
    mq = body.get("max_questions", MAX_QUESTIONS)
    if not (_is_int(mq) and 1 <= mq <= MAX_QUESTIONS_LIMIT):
        bad("max_questions", "1~{} 사이의 정수여야 합니다".format(MAX_QUESTIONS_LIMIT))
    today = None
    if body.get("today") is not None:
        try:
            today = date.fromisoformat(body["today"]) if isinstance(body["today"], str) else None
        except ValueError:
            today = None
        if today is None:
            bad("today", "YYYY-MM-DD 형식이어야 합니다")
    strict = body.get("strict", False)
    if not isinstance(strict, bool):
        bad("strict", "true 또는 false 여야 합니다")
        strict = False
    if errs:
        raise ApiError("INVALID_REQUEST", "요청 형식이 올바르지 않습니다 ({}개 항목)".format(len(errs)), errs)
    unknown = sorted(k for k in body if k not in KNOWN_KEYS)
    return ParsedRequest(profile=profile, request_id=rid, answers=dict(answers or {}), answers_text=text, declined=list(dict.fromkeys(declined or [])), round_no=rnd, max_questions=mq,
                         today=today, strict=strict, warnings=["알 수 없는 요청 항목은 무시합니다: {}".format(", ".join(unknown))] if unknown else [])


CONTRACT = {f.key: f for f in PC.fields()}
_KO_TYPE = {"integer": "정수", "number": "숫자", "string": "문자열", "boolean": "true/false", "array": "배열", "object": "객체"}
_TERM = re.compile(r"^(199\d|20\d\d)-[12]$")


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _type_ok(typ: str, v: Any) -> bool:
    if typ == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if typ == "number":
        return _is_num(v)
    return isinstance(v, {"string": str, "boolean": bool, "array": list, "object": dict}[typ])


def normalize_keys(data: Dict[str, Any], prefix: str = "profile") -> Tuple[Dict[str, Any], List[str]]:
    """키의 앞뒤 공백·대소문자·하이픈 차이를 없앤다 (`GPA_LAST`, ` grade`, `gpa-last` -> 계약의 키). 같은 항목이 두 번 들어 있으면 정확한 키를 쓰고 나머지는 무시한다."""
    out: Dict[str, Any] = {}
    warnings: List[str] = []
    for k, v in data.items():
        if not isinstance(k, str) or k in CONTRACT:
            out[k] = v
            continue
        nk = re.sub(r"[\s\-]+", "_", k.strip()).lower()
        if nk in CONTRACT:
            if nk in data or nk in out:
                warnings.append("{}.{}: `{}` 와 같은 항목이 이미 있어 무시했습니다".format(prefix, k, nk))
            else:
                out[nk] = v
        else:
            out[k] = v
    return out, warnings


def check_types(data: Dict[str, Any], prefix: str = "profile") -> Tuple[Dict[str, Any], List[str]]:
    """불리언은 JSON true/false, 배열·객체는 JSON 배열·객체여야 한다 (문자열 'true', 숫자 1, 단일 문자열 신분은 받지 않는다). 어긋난 키만 빼고 경고한다."""
    out: Dict[str, Any] = {}
    warnings: List[str] = []
    for k, v in data.items():
        f = CONTRACT.get(k)
        msg = None
        if f is not None and v is not None:
            if f.type in ("boolean", "array", "object") and not _type_ok(f.type, v):
                msg = "{} 형식이어야 합니다 (받은 값: {})".format(_KO_TYPE[f.type], type(v).__name__)
            elif k == "special_codes" and not all(isinstance(x, str) for x in v):
                msg = "신분 코드 문자열의 배열이어야 합니다"
            elif k == "language_scores" and not all(_is_num(x) for x in v.values()):
                msg = "값은 숫자여야 합니다 ('780' 같은 문자열은 받지 않습니다)"
        if msg:
            warnings.append("{}.{}: {}".format(prefix, k, msg))
        else:
            out[k] = v
    return out, warnings


def _consistency(p: Any) -> List[str]:
    """직전학기 평점(gpa_last)이 학기별 성적의 해당 학기 평점과 다르면 알린다 (둘 중 하나가 틀렸을 수 있다. 판정에서 빼지는 않는다)."""
    if p.gpa_last is None or not p.term_history:
        return []
    row = next((r for r in p.term_history if p.gpa_last_term and r["term"] == p.gpa_last_term), p.term_history[0])
    if abs(row["gpa"] - p.gpa_last) > 0.005:
        return ["profile.gpa_last: {:g} 이(가) term_history 의 {} 학기 평점 {:g} 과(와) 다릅니다 (둘 중 하나를 확인해 주세요)".format(p.gpa_last, row["term"], row["gpa"])]
    return []


def sanitize_profile(profile: Dict[str, Any], default_scale: Optional[float] = None, *, today: Optional[date] = None) -> Tuple[Dict[str, Any], List[str]]:
    """프로필 -> (판정에 쓸 프로필, 경고). 틀린 키만 빼고 나머지로 판정한다."""
    profile, warnings = normalize_keys(profile)
    profile, w = check_types(profile)
    warnings += w
    try:
        built = build_profile(profile, default_scale)
        clean = dict(profile)
    except ProfileError:
        clean = {}
        for k, v in profile.items():
            try:
                build_profile({k: v}, default_scale)
                clean[k] = v
            except ProfileError as e:
                warnings.append("profile.{}: {}".format(k, reason(k, e)))
        try:
            built = build_profile(clean, default_scale)
        except ProfileError as e:                       # 각각은 맞는데 서로 모순인 경우
            raise ApiError("INVALID_PROFILE", "입력 항목 사이의 관계가 올바르지 않습니다", [{"field": "profile", "message": str(e)}])
    warnings += ["profile.{}: 알 수 없는 항목이라 무시했습니다".format(k) for k in built.ignored if not is_sensitive_key(k)]
    return clean, warnings + _consistency(built.profile)


def _value_problems(f: "PC.Field", v: Any, scale: float, year: int) -> List[str]:
    if not _type_ok(f.type, v):
        return ["{} 형식이어야 합니다 (받은 값: {})".format(_KO_TYPE[f.type], type(v).__name__)]
    out: List[str] = []
    k = f.key
    if f.type == "string":
        if not v.strip():
            return ["빈 문자열은 보내지 말고 키를 생략해 주세요"]
        if k in PC.LENGTHS and len(v) > PC.LENGTHS[k]:
            out.append("{}자를 넘을 수 없습니다 ({}자)".format(PC.LENGTHS[k], len(v)))
        if k == "gpa_last_term":
            if not _TERM.match(v):
                out.append("`YYYY-1` 또는 `YYYY-2` 형식이어야 합니다 (연도 1990~2099)")
        elif f.enum and v not in f.enum:
            out.append("공식 이름이어야 합니다 (예: 경기도). 받은 값: {!r}".format(v) if k in PC.SIDO_KEYS else "허용 값이 아닙니다 ({}). 받은 값: {!r}".format(" / ".join(f.enum), v))
    elif f.type in ("integer", "number"):
        if k == "gpa_scale":
            if v not in f.enum:
                out.append("4.0 / 4.3 / 4.5 / 5.0 중 하나여야 합니다 (받은 값: {})".format(v))
        elif k in PC.BOUNDS:
            lo, hi = PC.BOUNDS[k]
            hi = scale if k in PC.GPA_KEYS else (year + 1 if k == "entry_year" else hi)
            if (lo is not None and v < lo) or (hi is not None and v > hi):
                out.append("{:g} ~ {:g} 범위여야 합니다 (받은 값: {})".format(lo, hi, v))
    elif f.type == "array":
        if k == "special_codes":
            bad = [x for x in v if x not in f.enum]
            if bad:
                out.append("허용되지 않는 신분 코드: {}".format(", ".join(map(str, bad))))
            if len(set(map(str, v))) != len(v):
                out.append("같은 신분 코드가 중복되어 있습니다")
        elif k == "term_history":
            if not 1 <= len(v) <= PC.MAX_ITEMS[k]:
                out.append("1 ~ {}개여야 합니다 ({}개)".format(PC.MAX_ITEMS[k], len(v)))
            seen = set()
            for i, r in enumerate(v):
                if not isinstance(r, dict) or set(r) - {"term", "gpa", "credits"} or not {"term", "gpa", "credits"} <= set(r):
                    out.append("[{}]: {{term, gpa, credits}} 객체여야 합니다".format(i))
                    continue
                if not (isinstance(r["term"], str) and _TERM.match(r["term"])):
                    out.append("[{}].term: `YYYY-1` 또는 `YYYY-2` 형식이어야 합니다".format(i))
                elif r["term"] in seen:
                    out.append("[{}].term: {} 학기가 중복되어 있습니다".format(i, r["term"]))
                else:
                    seen.add(r["term"])
                if not (_is_num(r["gpa"]) and 0 <= r["gpa"] <= scale):
                    out.append("[{}].gpa: 0 ~ {:g} 범위의 숫자여야 합니다".format(i, scale))
                if not (_is_num(r["credits"]) and 0 <= r["credits"] <= PC.CREDITS_MAX):
                    out.append("[{}].credits: 0 ~ {} 범위의 숫자여야 합니다".format(i, PC.CREDITS_MAX))
    elif f.type == "object" and k == "language_scores":
        for ek, ev in v.items():
            if not (isinstance(ek, str) and ek.strip()):
                out.append("시험 이름이 비어 있습니다")
            elif not _is_num(ev):
                out.append("{}: 숫자여야 합니다 (받은 값: {})".format(ek, type(ev).__name__))
            elif not 0 <= ev <= PC.LANGUAGE_MAX:
                out.append("{}: 0 ~ {} 범위여야 합니다 (받은 값: {})".format(ek, PC.LANGUAGE_MAX, ev))
    return out


def contract_violations(data: Dict[str, Any], today: Optional[date] = None, prefix: str = "profile") -> List[str]:
    """엄격 모드(연동 시험용): 계약과 한 글자라도 다른 입력을 모두 찾는다. 운영에서는 이런 입력도 가능한 한 받아들이지만(관대한 검증), 포털의 변환 코드는 이 목록이 비어 있도록 고친다."""
    year = (today or date.today()).year
    scale = data.get("gpa_scale") if _is_num(data.get("gpa_scale")) else 4.5
    out: List[str] = []
    for k, v in data.items():
        key = k
        if k not in CONTRACT:
            nk = re.sub(r"[\s\-]+", "_", k.strip()).lower() if isinstance(k, str) else None
            if nk in CONTRACT:
                out.append("{}.{}: 키는 `{}` 로 정확히 보내 주세요".format(prefix, k, nk))
                key = nk
            elif isinstance(k, str) and is_sensitive_key(k):
                out.append("{}.{}: 학생을 식별하는 개인정보는 보내지 않습니다".format(prefix, k))
                continue
            else:
                out.append("{}.{}: 계약에 없는 항목입니다".format(prefix, k))
                continue
        if v is None:
            out.append("{}.{}: null 대신 키를 생략해 주세요".format(prefix, key))
            continue
        out += ["{}.{}: {}".format(prefix, key, m) for m in _value_problems(CONTRACT[key], v, scale, year)]
    if any(k in data for k in ("gpa_last", "gpa_cumulative", "term_history")) and "gpa_scale" not in data:
        out.append("{}.gpa_scale: 성적 값과 함께 보내야 합니다 (없으면 4.5로 가정합니다)".format(prefix))
    lo, up = data.get("residence_years"), data.get("residence_years_upper")
    if _is_num(lo) and _is_num(up) and up < lo:
        out.append("{}.residence_years_upper: `residence_years` 보다 작을 수 없습니다".format(prefix))
    if data.get("residence_years_open") is True and up is not None:
        out.append("{}.residence_years_open: `residence_years_upper` 와 함께 보낼 수 없습니다".format(prefix))
    return out


def handle_recommend(rec: Recommender, body: Any) -> Tuple[int, Dict[str, Any]]:
    """POST /v1/recommend 의 처리. (HTTP 상태, 응답 본문)"""
    rid = body.get("request_id") if isinstance(body, dict) and isinstance(body.get("request_id"), str) and REQUEST_ID.match(body["request_id"]) else None
    try:
        req = parse_request(body)
        clean, warnings = sanitize_profile(req.profile, rec.s.default_gpa_scale, today=req.today)
        answers, aw = normalize_keys(req.answers, "answers")
        answers, aw2 = check_types(answers, "answers")
        result = rec.recommend_step(clean, answers=answers, answers_text=req.answers_text, declined=req.declined, round_no=req.round_no,
                                    today=req.today, max_questions=req.max_questions)
        result["answers"]["warnings"] = aw + aw2 + result["answers"]["warnings"]
    except ApiError as e:
        return e.status, e.body(rid)
    except ProfileError:                                 # 서버 환경설정 오류(.env 의 학교 설정 등): 학생 입력은 이미 걸러졌다
        return 500, ApiError("SERVER_CONFIG", "서버 설정에 문제가 있습니다. 관리자에게 문의하세요").body(rid)
    result["request_id"] = req.request_id
    result["profile_warnings"] = warnings + req.warnings
    if req.strict:
        result["contract_violations"] = contract_violations(req.profile, req.today) + contract_violations(req.answers, req.today, "answers")
    return 200, result
