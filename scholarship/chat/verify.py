# -*- coding: utf-8 -*-
"""[CHAT] AI 응답 검증. 모델이 쓴 글을 그대로 믿지 않고, 원문에 실제로 있는 인용으로 뒷받침되는 경우에만 답을 내보낸다.

- 인용(quote)은 해당 자료 안에 글자 그대로(공백 무시) 있어야 한다. 없는 인용은 버린다.
- 유효한 인용이 하나도 없으면 모델의 문장은 쓰지 않고 정해 둔 문구로 바꾼다 (not_found / out_of_scope / ungrounded).
- 답변 속 숫자가 자료에 없으면 경고를 붙인다 (계산·환산으로 만든 숫자일 수 있음).
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .text import squash

NOT_FOUND = "질문하신 내용은 제가 가진 공고 원문(본문·첨부)에서 확인되지 않았습니다. 공고 원문 링크나 담당 기관에 확인해 주세요."
OUT_OF_SCOPE = "장학금 공고에 관한 질문에만 답할 수 있습니다. 궁금한 공고의 조건, 서류, 일정, 금액 등을 물어보세요."
UNGROUNDED = "답변의 근거가 되는 원문을 확인하지 못해 답변을 드리지 않습니다. 질문을 조금 더 구체적으로 적어 주시거나 공고 원문을 확인해 주세요."
MIN_QUOTE = 6
MAX_QUOTE = 300
_NUM = re.compile(r"\d[\d,]*\d|\d")
_MARK = re.compile(r"\[(\d{1,2})\]")


def parse_model_json(text: str) -> Optional[Dict[str, Any]]:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    for cand in (t, t[t.find("{"): t.rfind("}") + 1] if "{" in t and "}" in t else ""):
        if not cand:
            continue
        try:
            d = json.loads(cand)
        except ValueError:
            continue
        if isinstance(d, dict):
            return d
    return None


def repair_truncated(text: str) -> Optional[Dict[str, Any]]:
    """출력 한도에 걸려 JSON 이 중간에 잘렸을 때, 끝까지 쓴 인용까지만 살려 닫는다. answer 가 온전하지 않으면 None."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t).strip()
    start = t.find("{")
    if start < 0 or '"answer"' not in t:
        return None
    t = t[start:]
    cuts = [m.end() for m in re.finditer(r"\}", t)][-30:]
    for end in reversed(cuts):
        for tail in ("]}", "}"):
            try:
                d = json.loads(t[:end] + tail)
            except ValueError:
                continue
            if isinstance(d, dict) and isinstance(d.get("answer"), str) and d["answer"].strip():
                d.setdefault("followups", [])
                return d
    return None


def _quote_ok(quote: str, hay: str) -> bool:
    parts = [p for p in re.split(r"…|\.\.\.", quote) if squash(p)]
    if not parts:
        return False
    pos = 0
    for p in parts:
        sp = squash(p)
        if len(sp) < MIN_QUOTE and len(parts) == 1:
            return False
        if len(sp) < 3:
            return False
        at = hay.find(sp, pos)
        if at < 0:
            return False
        pos = at + len(sp)
    return True


def _digits(text: str) -> set:
    return {m.replace(",", "") for m in _NUM.findall(text or "")}


def verify(model_text: str, sources: List[Dict[str, str]], extra_text: str = "") -> Dict[str, Any]:
    """sources: [{"id", "notice", "label", "text"}]. extra_text: 질문·시스템 판정처럼 숫자의 출처가 될 수 있는 글.
    반환: {"outcome": answered|not_found|out_of_scope|ungrounded, "answer", "citations": [{"n","ref","notice_id","source","quote"}], "followups", "warnings"}"""
    d = parse_model_json(model_text) or repair_truncated(model_text)
    if d is None:
        return {"outcome": "ungrounded", "answer": UNGROUNDED, "citations": [], "followups": [], "warnings": [], "why": "AI 응답을 JSON 으로 읽지 못함 (응답이 중간에 잘렸을 수 있음)"}
    if d.get("out_of_scope") is True:
        return {"outcome": "out_of_scope", "answer": OUT_OF_SCOPE, "citations": [], "followups": [], "warnings": []}
    by_id = {s["id"]: s for s in sources}
    hay = {s["id"]: squash(s["text"]) for s in sources}
    raw_cites = d.get("citations") if isinstance(d.get("citations"), list) else []
    valid: Dict[int, Dict[str, Any]] = {}
    for i, c in enumerate(raw_cites, 1):
        if not isinstance(c, dict):
            continue
        ref, q = c.get("ref"), c.get("quote")
        if not (isinstance(ref, str) and ref in by_id and isinstance(q, str) and MIN_QUOTE <= len(q.strip()) <= MAX_QUOTE):
            continue
        if _quote_ok(q, hay[ref]):
            valid[i] = {"ref": ref, "notice_id": by_id[ref]["notice"], "source": by_id[ref]["label"], "quote": q.strip()}
            if by_id[ref].get("file_seq"):
                valid[i]["file_seq"] = by_id[ref]["file_seq"]
    answer = d.get("answer") if isinstance(d.get("answer"), str) else ""
    if d.get("not_found") is True or not answer.strip():
        return {"outcome": "not_found", "answer": NOT_FOUND, "citations": [], "followups": [], "warnings": []}
    if not valid:
        return {"outcome": "ungrounded", "answer": UNGROUNDED, "citations": [], "followups": [], "warnings": [], "why": "인용 {}개 중 원문과 맞는 것 0개".format(len(raw_cites))}
    order = {old: n for n, old in enumerate(sorted(valid), 1)}              # 살아남은 인용만 1부터 다시 번호를 붙인다
    answer = _MARK.sub(lambda m: "[{}]".format(order[int(m.group(1))]) if int(m.group(1)) in order else "", answer).strip()
    cites = []
    for old in sorted(valid):
        cites.append({"n": order[old], **valid[old]})
    known = set()
    for s in sources:
        known |= _digits(s["text"])
    known |= _digits(extra_text)
    body = _MARK.sub("", answer)
    odd = sorted({x for x in _digits(body) if len(x) >= 2 and x not in known})
    warnings = []
    if odd:
        warnings.append("답변의 숫자({})가 공고 원문에서 그대로 확인되지 않았습니다. 원문을 확인해 주세요.".format(", ".join(odd[:5])))
        answer += "\n\n※ " + warnings[0]
    fu = [f.strip() for f in (d.get("followups") if isinstance(d.get("followups"), list) else []) if isinstance(f, str) and 0 < len(f.strip()) <= 60 and "http" not in f][:3]
    return {"outcome": "answered", "answer": answer, "citations": cites, "followups": fu, "warnings": warnings}
