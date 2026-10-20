# -*- coding: utf-8 -*-
"""[RECOMMEND] 5-D 결과 JSON 형식 v1.

포털(교내 통합정보시스템)이 받는 판정 결과의 모양을 한 곳에 정의한다. JSON Schema(draft-07) 형식이라 포털 쪽에서 어떤 검증기로도 쓸 수 있고
(docs/result_v1.schema.json), 이 모듈의 validate() 로 외부 패키지 없이 같은 검사를 할 수 있다.

호환 규칙: 필드를 더하는 변경은 같은 버전(result_version 1)에서 하고, 필드를 없애거나 뜻·형식을 바꾸면 버전을 올린다.
모든 객체는 알려지지 않은 필드를 허용한다 (포털은 모르는 필드를 무시해야 한다).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

RESULT_VERSION = 1
S, NS, I, B = {"type": "string"}, {"type": ["string", "null"]}, {"type": "integer"}, {"type": "boolean"}
SA = {"type": "array", "items": S}
VERDICTS = ["MATCH", "CONDITIONAL", "NO_MATCH"]
VERDICT_LABELS = ["충족", "조건부", "미달"]
DONE_REASONS = ["NO_QUESTIONS", "ALL_DECLINED", "MAX_ROUNDS"]


def _obj(required: List[str], props: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "object", "required": required, "properties": props}


def _ref(name: str) -> Dict[str, str]:
    return {"$ref": "#/$defs/" + name}


DEFS: Dict[str, Any] = {
    "variant": _obj(["name", "verdict", "verdict_label", "to_confirm"], {
        "name": S, "verdict": {"enum": VERDICTS}, "verdict_label": {"enum": VERDICT_LABELS}, "amount": NS, "apply_start": NS, "apply_end": NS, "expired": B,
        "preferences": {"type": "array", "items": _ref("preference")}, "to_confirm": SA, "documents": SA,
        "evaluation": {"type": "array", "items": _ref("evaluation_part")}, "recommender": NS, "criteria": SA}),
    "evaluation_part": _obj(["item", "percent"], {"item": S, "percent": {"type": "number"}}),
    "procedure_step": _obj(["name"], {"name": S, "when": NS}),
    "selection_info": _obj([], {"evaluation": {"type": "array", "items": _ref("evaluation_part")}, "recommender": NS,
                                "procedure": {"type": "array", "items": _ref("procedure_step")}, "criteria": SA}),
    "unmatched_variant": _obj(["name", "reason"], {"name": S, "reason": S, "amount": NS, "reasons": SA, "evaluation": {"type": "array", "items": _ref("evaluation_part")}, "criteria": SA}),
    "preference": _obj(["code", "label", "applies"], {"code": S, "label": S, "applies": {"type": ["boolean", "null"]}}),
    "prerequisite": _obj(["type", "label", "status"], {"type": S, "label": S, "text": NS, "status": {"enum": ["MET", "TODO", "UNMET", "UNKNOWN"]}}),
    "school_program": _obj(["program", "label", "school"], {"program": S, "label": S, "school": {"enum": ["PARTICIPATES", "NOT", "UNKNOWN"]}}),
    "note": _obj(["kind", "label", "text"], {"kind": S, "label": S, "text": S}),
    "warning": _obj(["code", "message"], {"code": S, "message": S}),
    "item": _obj(["id", "src_cd", "src_id", "title", "verdict", "verdict_label", "days_left", "to_confirm", "matched_variants", "unmatched_variants", "passed", "fail_reasons",
                  "warnings", "assumptions", "prerequisites", "preferences", "school_programs", "notes", "schema_version"], {
        "id": S, "src_cd": S, "src_id": S, "title": S, "scholarship_name": NS, "foundation": NS, "url": NS, "summary": NS, "apply_start": NS, "apply_end": NS, "days_left": I,
        "org_deadline": NS, "apply_how": NS, "amount": NS, "status": NS, "schema_version": I,
        "verdict": {"enum": VERDICTS}, "verdict_label": {"enum": VERDICT_LABELS},
        "best_variant": {"type": ["object", "null"]}, "tier": {"enum": ["HIGH", "PARTIAL", "CHECK", "NONE"]}, "tier_label": S,
        "to_confirm": SA, "passed": SA, "self_confirm": SA, "fail_reasons": SA, "assumptions": SA, "merged_from": {"type": "array"},
        "matched_variants": {"type": "array", "items": _ref("variant")}, "unmatched_variants": {"type": "array", "items": _ref("unmatched_variant")},
        "warnings": {"type": "array", "items": _ref("warning")}, "prerequisites": {"type": "array", "items": _ref("prerequisite")},
        "preferences": {"type": "array", "items": _ref("preference")}, "school_programs": {"type": "array", "items": _ref("school_program")},
        "notes": {"type": "array", "items": _ref("note")},
        "selection_info": _ref("selection_info"),
        "condition_labels": {"type": "array", "items": _obj(["text", "label"], {"text": S, "label": S})},
        "feasibility": _obj(["level", "label", "reason", "source"], {"level": {"enum": ["NORMAL", "HARD", "VERY_HARD"]}, "label": S, "reason": S, "source": {"enum": ["rule", "ai", "default"]}}),
        "condition_summary": _obj(["source", "groups"], {"source": {"enum": ["ai", "rule"]}, "groups": {"type": "array", "items": _obj(["title", "summary", "quote", "level", "kind", "rows"], {
            "title": S, "summary": S, "quote": S, "level": {"enum": ["NORMAL", "HARD", "VERY_HARD"]}, "kind": {"enum": ["confirm", "exclude"]}, "rows": {"type": "array", "items": I}})}}),
        "benefit": _obj(["amount", "variants", "limits"], {"amount": NS, "variants": {"type": "array", "items": _obj(["type", "amount"], {"type": S, "amount": S})}, "limits": SA})}),
    "excluded": _obj(["id", "src_id", "title", "reasons"], {
        "id": S, "src_cd": NS, "src_id": S, "title": NS, "url": NS, "reasons": SA,
        # 선택 필드 (RECOMMEND_EXCLUDED_DETAIL): 미달 카드에 요약·혜택·조건을 보여 주기 위한 재료. 뜻은 item 의 같은 이름 필드와 같다
        "summary": NS, "apply_start": NS, "apply_end": NS, "days_left": I, "benefit": {"type": "object"}, "passed": SA, "self_confirm": SA, "fail_reasons": SA, "to_confirm": SA,
        "matched_variants": {"type": "array", "items": _ref("variant")}, "unmatched_variants": {"type": "array", "items": _ref("unmatched_variant")},
        "prerequisites": {"type": "array", "items": _ref("prerequisite")}, "preferences": {"type": "array", "items": _ref("preference")},
        "school_programs": {"type": "array", "items": _ref("school_program")}, "notes": {"type": "array", "items": _ref("note")},
        "warnings": {"type": "array", "items": _ref("warning")}, "assumptions": SA}),
    "question": _obj(["code", "label", "question", "type", "sensitive", "skippable", "resolves", "resolves_count", "answer"], {
        "code": S, "label": S, "question": S, "type": S, "sensitive": B, "skippable": B, "resolves": SA, "resolves_count": I,
        "answer": _obj(["keys", "text"], {"keys": SA, "text": S, "format": S})}),
    "data_gap": _obj(["code", "label", "resolves_count", "resolves"], {"code": S, "label": S, "field": NS, "resolves_count": I, "resolves": SA}),
    "document_only": _obj(["id", "title", "count", "items"], {"id": S, "title": S, "count": I, "items": SA}),
    "follow_up": _obj(["round", "max_rounds", "max_questions", "done", "done_reason", "questions", "remaining", "data_gap", "document_only"], {
        "round": I, "max_rounds": I, "max_questions": I, "done": B, "done_reason": {"enum": DONE_REASONS + [None]}, "remaining": I,
        "questions": {"type": "array", "items": _ref("question")}, "data_gap": {"type": "array", "items": _ref("data_gap")},
        "document_only": {"type": "array", "items": _ref("document_only")}}),
    "change": _obj(["id", "from", "to", "from_label", "to_label", "reason"], {
        "id": S, "src_cd": NS, "src_id": NS, "title": NS, "url": NS, "from": {"enum": VERDICTS}, "to": {"enum": VERDICTS},
        "from_label": {"enum": VERDICT_LABELS}, "to_label": {"enum": VERDICT_LABELS}, "reason": S}),
    "change_summary": _obj(["changed", "to_match", "to_conditional", "to_no_match", "unchanged", "reduced_confirms"],
                           {k: I for k in ("changed", "to_match", "to_conditional", "to_no_match", "unchanged", "reduced_confirms")}),
    "answers": _obj(["applied", "warnings", "declined"], {"applied": SA, "warnings": SA, "declined": SA}),
}

BASE_REQUIRED = ["result_version", "today", "profile_notes", "counts", "recommended", "conditional", "excluded"]
BASE_PROPS: Dict[str, Any] = {
    "result_version": {"const": RESULT_VERSION}, "today": S,
    "profile_notes": _obj(["dropped_sensitive_keys", "ignored_keys", "assumptions"], {"dropped_sensitive_keys": SA, "ignored_keys": SA, "assumptions": SA}),
    "counts": _obj(["candidates", "after_filter", "after_dedupe", "match", "conditional", "no_match"], {k: I for k in ("candidates", "after_filter", "after_dedupe", "match", "conditional", "no_match")}),
    "recommended": {"type": "array", "items": _ref("item")}, "conditional": {"type": "array", "items": _ref("item")}, "excluded": {"type": "array", "items": _ref("excluded")},
}
STEP_REQUIRED = BASE_REQUIRED + ["round", "answers", "follow_up", "changes", "change_summary"]
STEP_PROPS: Dict[str, Any] = dict(BASE_PROPS, round=I, answers=_ref("answers"), follow_up=_ref("follow_up"), changes={"type": "array", "items": _ref("change")},
                                  change_summary=_ref("change_summary"), request_id=NS, profile_warnings=SA, contract_violations=SA)       # request_id, profile_warnings, contract_violations(엄격 모드) 는 API 가 붙인다(선택)


def schema(step: bool = True) -> Dict[str, Any]:
    """step=True: 후속 질문·변화가 포함된 결과(recommend_step). False: 기본 결과(recommend)."""
    return {"$schema": "http://json-schema.org/draft-07/schema#", "title": "scholarship recommend result v{}".format(RESULT_VERSION), "type": "object",
            "required": STEP_REQUIRED if step else BASE_REQUIRED, "properties": STEP_PROPS if step else BASE_PROPS, "$defs": DEFS}


def schema_json(step: bool = True) -> str:
    return json.dumps(schema(step), ensure_ascii=False, indent=1, sort_keys=False) + "\n"


# ------------------------------------------------------------------ 외부 패키지 없이 쓰는 작은 검증기 (JSON Schema 의 일부: type / enum / const / required / properties / items / $ref)
_TYPES = {"string": str, "boolean": bool, "array": list, "object": dict, "null": type(None)}


def _is(value: Any, t: str) -> bool:
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[t])


def _check(value: Any, sch: Dict[str, Any], root: Dict[str, Any], path: str, out: List[str]) -> None:
    if "$ref" in sch:
        sch = root["$defs"][sch["$ref"].rsplit("/", 1)[-1]]
    if "const" in sch and value != sch["const"]:
        out.append("{}: {!r} (필요: {!r})".format(path, value, sch["const"]))
        return
    if "enum" in sch and value not in sch["enum"]:
        out.append("{}: {!r} 는 허용 값이 아닙니다 ({})".format(path, value, ", ".join(repr(x) for x in sch["enum"])))
        return
    if "type" in sch:
        types = sch["type"] if isinstance(sch["type"], list) else [sch["type"]]
        if not any(_is(value, t) for t in types):
            out.append("{}: {} 형식이 아닙니다 (실제: {})".format(path, "/".join(types), type(value).__name__))
            return
    if isinstance(value, dict):
        for k in sch.get("required", []):
            if k not in value:
                out.append("{}.{}: 필수 항목이 없습니다".format(path, k))
        for k, sub in sch.get("properties", {}).items():
            if k in value:
                _check(value[k], sub, root, "{}.{}".format(path, k), out)
    elif isinstance(value, list) and "items" in sch:
        for i, v in enumerate(value):
            _check(v, sch["items"], root, "{}[{}]".format(path, i), out)


def validate(result: Dict[str, Any], step: bool = True) -> List[str]:
    """결과가 형식 v1 에 맞는지 검사한다. 맞으면 빈 목록, 아니면 어느 위치가 왜 틀렸는지 문장 목록."""
    root = schema(step)
    out: List[str] = []
    _check(result, root, root, "$", out)
    return out


if __name__ == "__main__":                                   # python -m scholarship.recommend.result_format [--write]  (--write: docs/result_v1.schema.json 을 UTF-8 로 직접 쓴다)
    if "--write" in sys.argv:
        (Path(__file__).resolve().parents[2] / "docs" / "result_v1.schema.json").write_text(schema_json(), encoding="utf-8")
        print("docs/result_v1.schema.json 갱신")
    else:
        print(schema_json(), end="")
