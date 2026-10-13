# -*- coding: utf-8 -*-
"""[RECOMMEND] 6-A 입력 계약: 포털이 `profile` / `answers` 에 담아 보내는 JSON 키의 목록과 형식.

- 키·출처(학교 DB / 학생)·등급·민감 여부는 항목 사전(config/item_registry.json)에서 가져오고, 형식·허용 값·예시만 여기서 정한다.
- 항목 사전에 없는 동반 키(gpa_scale 등)는 COMPANIONS 에 이유와 함께 적는다.
- docs/api_contract_v1.md 의 필드 표는 markdown_table() 이 만든다. 문서가 코드와 다르면 테스트가 실패한다
  (다시 쓰기: python -m scholarship.recommend.profile_contract --write-docs).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..common.registry import load_registry
from ..common.ai.schema_v3 import MAJOR_GROUPS
from .vocab import SIDO_OFFICIAL_NAMES

BEGIN, END = "<!-- FIELDS:BEGIN -->", "<!-- FIELDS:END -->"
TIER = {1: "필수", 2: "권장", 3: "선택"}
LEVELS = ("UNIV", "GRAD")
STATUSES = ("ENROLLED", "LEAVE", "GRADUATED")
CONTRACT_SPECIAL_CODES = ("BASIC_LIVELIHOOD", "NEAR_POOR", "DISABLED", "SINGLE_PARENT", "DEFECTOR", "MULTICULTURAL", "VETERAN", "MULTI_CHILD")   # 학생에게 묻는 8개 신분
# 허용 범위 (코드가 실제로 받아들이는 범위와 같다. 시험으로 확인): 정수·실수 (최솟값, 최댓값). 평점의 최댓값은 gpa_scale, 입학년도의 최댓값은 올해+1(엄격 모드에서만 검사)
BOUNDS: Dict[str, Tuple[Optional[float], Optional[float]]] = {
    "grade": (1, 6), "semesters_completed": (0, 30), "entry_year": (1990, None), "gpa_last": (0, None), "gpa_cumulative": (0, None), "credits_last": (0, 60),
    "gpa_percent": (0, 100), "gpa_cumulative_percent": (0, 100), "income_bracket": (0, 10), "residence_years": (0, 100), "residence_years_upper": (0, 100), "age": (0, 100)}
GPA_KEYS = ("gpa_last", "gpa_cumulative")
LENGTHS: Dict[str, int] = {"major": 100, "residence_sigungu": 40, "parent_residence_sigungu": 40, "hs_sigungu": 40}      # 문자열 최대 길이
MAX_ITEMS: Dict[str, int] = {"term_history": 20}
LANGUAGE_MAX = 1000
CREDITS_MAX = 60
SIDO_KEYS = ("residence_sido", "parent_residence_sido", "hs_sido")
RANGE_TEXT: Dict[str, str] = {
    "gpa_last": "0 ~ `gpa_scale`", "gpa_cumulative": "0 ~ `gpa_scale`", "entry_year": "1990 ~ (올해+1)", "residence_years_upper": "`residence_years` ~ 100",
    "gpa_last_term": "`YYYY-1` 또는 `YYYY-2` (연도 1990~2099)", "term_history": "1 ~ 20개. 같은 학기 중복 불가, 순서는 무관", "language_scores": "값 0 ~ 1000 (성적이 없으면 `{}`)",
    "special_codes": "아래 8개 코드, 중복 없이 (없으면 `[]`)", "residence_sido": "17개 시·도 공식 이름", "parent_residence_sido": "17개 시·도 공식 이름", "hs_sido": "17개 시·도 공식 이름",
    "residence_sigungu": "1 ~ 40자", "parent_residence_sigungu": "1 ~ 40자", "hs_sigungu": "1 ~ 40자", "major": "1 ~ 100자", "gpa_scale": "4.0 / 4.3 / 4.5 / 5.0"}


@dataclass
class Field:
    key: str
    type: str                       # string / integer / number / boolean / array / object
    desc: str
    example: Any
    enum: Tuple[Any, ...] = ()
    code: str = ""                  # 항목 사전 코드 (동반 키는 비어 있음)
    source: str = ""                # school_db / student
    tier: str = ""                  # 필수 / 권장 / 선택
    sensitive: bool = False


# 키 -> (형식, 설명, 허용 값, 예시). 항목 사전이 알려 주지 않는 형식 정보만 여기에 있다.
FORMAT: Dict[str, Tuple[str, str, Tuple[Any, ...], Any]] = {
    "level": ("string", "과정", LEVELS, "UNIV"),
    "status": ("string", "학적 상태", STATUSES, "ENROLLED"),
    "grade": ("integer", "학년", (), 4),
    "semesters_completed": ("integer", "등록(이수)한 학기 수. 신입생은 0", (), 7),
    "entry_year": ("integer", "입학년도", (), 2021),
    "admission_type": ("string", "입학 구분 (신입학 / 편입학)", ("NEW", "TRANSFER"), "NEW"),
    "major": ("string", "학과 이름", (), "전자공학부"),
    "major_group": ("string", "학과가 속한 계열 (학교 시스템의 분류를 표준 코드로 전달)", tuple(MAJOR_GROUPS), "ENGINEERING"),
    "nationality": ("string", "국적 (내국인 / 외국인 / 재외동포). 다른 값은 받지 않는다", ("KR", "FOREIGN", "OVERSEAS_KR"), "KR"),
    "gender": ("string", "성별", ("M", "F"), "M"),
    "gpa_last": ("number", "직전학기 평점. 만점은 gpa_scale 로 함께 보낸다", (), 3.79),
    "gpa_cumulative": ("number", "누적(전체) 평점. 만점은 gpa_scale 로 함께 보낸다", (), 3.62),
    "gpa_scale": ("number", "평점 만점. **성적 값(gpa_last, gpa_cumulative)과 함께 보낸다.** 보내지 않으면 4.5로 가정하고 결과의 profile_notes.assumptions 에 알린다", (4.5, 4.3, 4.0, 5.0), 4.5),
    "gpa_last_term": ("string", "직전학기 `YYYY-N`", (), "2026-1"),
    "credits_last": ("number", "직전학기 취득학점", (), 15),
    "term_history": ("array", "학기별 성적, 최근 학기부터: `[{term, gpa, credits}]`", (), [{"term": "2026-1", "gpa": 3.79, "credits": 15}, {"term": "2025-2", "gpa": 3.11, "credits": 18}]),
    "gpa_percent": ("number", "직전학기 100점 환산 점수 (공고가 100점제일 때만 쓰임). 0~100", (), 88),
    "gpa_cumulative_percent": ("number", "누적(전체) 100점 환산 점수 (공고의 전체 평점 기준이 100점제일 때만 쓰임). 0~100", (), 85),
    "income_bracket": ("integer", "학자금 지원구간(소득분위) 0~10. **민감: 학생 동의 후에만 보낸다**", (), 2),
    "residence_sido": ("string", "본인 주민등록 시·도. 공식 이름: " + ", ".join("`{}`".format(n) for n in SIDO_OFFICIAL_NAMES) + ". 약칭·옛 이름(`경기`, `강원도`)은 공식 이름으로 바꿔 받지만 오타는 거절한다", SIDO_OFFICIAL_NAMES, "경기도"),
    "residence_sigungu": ("string", "본인 주민등록 시·군·구 (읍·면·동까지 적어도 된다: `수원시 장안구 연무동`)", (), "수원시"),
    "residence_years": ("number", "거주 기간(년). 구간으로 받았으면 하한 (`3년 이상` -> 3). 소수 가능(0.5)", (), 3),
    "residence_years_upper": ("number", "거주 기간 구간의 상한(포함). `1~3년` -> 3. 정확한 값이면 보내지 않는다", (), 3),
    "residence_years_open": ("boolean", "`N년 이상`처럼 상한이 없으면 true", (), True),
    "parent_residence_sido": ("string", "부모 주민등록 시·도 (`residence_sido` 와 같은 공식 이름)", SIDO_OFFICIAL_NAMES, "경기도"),
    "parent_residence_sigungu": ("string", "부모 주민등록 시·군·구", (), "수원시"),
    "hs_sido": ("string", "졸업한 고등학교가 있는 시·도 (`residence_sido` 와 같은 공식 이름)", SIDO_OFFICIAL_NAMES, "경기도"),
    "hs_sigungu": ("string", "졸업한 고등학교가 있는 시·군·구", (), "안양시"),
    "age": ("integer", "만 나이", (), 24),
    "special_codes": ("array", "해당하는 신분 코드 목록. **민감: 학생 동의 후에만 보낸다**", CONTRACT_SPECIAL_CODES, ["DISABLED"]),
    "special_complete": ("boolean", "신분 항목에 모두 답했으면 true (목록에 없는 신분은 해당 없음으로 본다). 일부만 답했으면 false 또는 보내지 않는다", (), True),
    "is_basic_livelihood": ("boolean", "기초생활수급자 여부 (special_codes 와 같은 뜻의 개별 답. 둘 중 하나만 써도 된다)", (), False),
    "is_near_poor": ("boolean", "차상위계층 여부 (개별 답)", (), False),
    "is_single_parent": ("boolean", "한부모가정 여부 (개별 답)", (), False),
    "degree_held": ("boolean", "학위 소지 여부", (), False),
    "national_scholarship_applied": ("boolean", "이번 학기 국가장학금 신청 여부", (), True),
    "language_scores": ("object", "어학 성적 `{시험 이름: 점수 또는 등급 숫자}` (값은 숫자). 독일어 같은 유럽언어기준(CEFR) 등급은 언어 이름을 키로, A1=1 A2=2 B1=3 B2=4 C1=5 C2=6 으로 보낸다. 성적이 없으면 빈 객체 `{}` (보내지 않으면 '답하지 않음')", (), {"TOEIC": 780, "HSK": 5, "독일어": 3}),
}
# 항목 사전의 profile_field 에 없는 동반 키 (이유)
COMPANIONS: Dict[str, str] = {"gpa_scale": "성적 값과 함께", "residence_years_upper": "거주 기간 구간과 함께", "residence_years_open": "거주 기간 구간과 함께", "special_complete": "신분 응답과 함께"}
ORDER = ["level", "status", "grade", "semesters_completed", "entry_year", "admission_type", "major", "major_group", "nationality", "gender", "gpa_last", "gpa_scale", "gpa_last_term", "credits_last",
         "gpa_cumulative", "term_history", "gpa_percent", "gpa_cumulative_percent", "income_bracket", "residence_sido", "residence_sigungu", "residence_years", "residence_years_upper",
         "residence_years_open", "parent_residence_sido", "parent_residence_sigungu", "hs_sido", "hs_sigungu", "age", "special_codes", "special_complete", "is_basic_livelihood",
         "is_near_poor", "is_single_parent", "degree_held", "national_scholarship_applied", "language_scores"]


def fields() -> List[Field]:
    reg = load_registry()
    owner: Dict[str, Any] = {}
    for it in reg.enabled():
        if it.kind != "student":
            continue
        for k in (it.profile_field or "").split("/"):
            if k and (k not in owner or it.code == "SPECIAL"):                  # special_codes 는 여러 신분 항목이 가리킨다: 묶음 항목(SPECIAL)을 대표로
                owner[k] = it
    out: List[Field] = []
    for k in ORDER:
        typ, desc, enum, example = FORMAT[k]
        it = owner.get(k)
        out.append(Field(k, typ, desc, example, tuple(enum), it.code if it else "", it.source if it else "", TIER[it.tier] if it else "", bool(it and it.sensitive)))
    return out


def range_text(f: Field) -> str:
    """표의 '허용 범위·값' 칸"""
    if f.key in RANGE_TEXT:
        return RANGE_TEXT[f.key]
    if f.enum and len(f.enum) <= 10:
        return " / ".join("`{}`".format(e) for e in f.enum)
    if f.key in BOUNDS:
        lo, hi = BOUNDS[f.key]
        return "{:g} ~ {:g}".format(lo, hi) if hi is not None else "{:g} 이상".format(lo)
    if f.type == "boolean":
        return "`true` / `false` (JSON 불리언만)"
    return ""


def _cell(f: Field) -> str:
    ex = f.example if isinstance(f.example, str) else __import__("json").dumps(f.example, ensure_ascii=False)
    return "{}. 예: `{}`".format(f.desc, ex).replace("|", "\\|")


def markdown_table() -> str:
    rows = ["| JSON 키 | 출처 | 등급 | 형식 | 허용 범위·값 | 설명 |", "|---|---|---|---|---|---|"]
    for f in fields():
        src = {"school_db": "학교 DB", "student": "학생"}.get(f.source, "동반 키")
        tier = f.tier or "(동반)"
        rows.append("| `{}` | {} | {}{} | {} | {} | {} |".format(f.key, src, tier, " (민감)" if f.sensitive else "", f.type, range_text(f).replace("|", "\\|"), _cell(f)))
    return "\n".join(rows)


def doc_block() -> str:
    return "{}\n{}\n{}".format(BEGIN, markdown_table(), END)


def sync_doc(path: Path, write: bool = False) -> bool:
    """문서의 필드 표 블록이 코드가 만든 것과 같은가. write=True 면 다시 쓴다."""
    text = path.read_text(encoding="utf-8")
    i, j = text.index(BEGIN), text.index(END) + len(END)
    same = text[i:j] == doc_block()
    if write and not same:
        path.write_text(text[:i] + doc_block() + text[j:], encoding="utf-8")
    return same


if __name__ == "__main__":
    doc = Path(__file__).resolve().parents[2] / "docs" / "api_contract_v1.md"
    if "--write-docs" in sys.argv:
        sync_doc(doc, write=True)
        print("{} 갱신".format(doc))
    else:
        print(markdown_table())
