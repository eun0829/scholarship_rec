# -*- coding: utf-8 -*-
"""공고 한 건을 색인할 문서(조각)들로 만든다. AI·외부 호출 없는 순수 함수.
문서: 구조화 요약 1개(#s) + 본문 조각(#b0, #b1 ...) + 첨부 조각(#a1c0 ...). id 는 Pinecone 규칙대로 ASCII 만 쓴다."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..chat import text as T

SUMMARY_MAX = 1800                  # 구조화 요약 최대 글자 수 (질문마다 AI 로 가므로 짧게)
DUP_JACCARD = 0.9                   # 이 이상 겹치는 첨부는 같은 내용으로 보고 하나만 남긴다 (hwp/pdf 중복, 연장 공고문 등)


@dataclass
class Doc:
    id: str              # 'EXT02:9210#s' / '#b0' / '#a1c3'
    notice_id: str       # 'EXT02:9210'
    kind: str            # summary / body / attach
    label: str           # '구조화 요약' / '본문' / '첨부1: 파일명'
    text: str
    file_seq: str = ""   # 첨부 조각의 원본 파일 번호


def _s(v: Any, n: int = 200) -> str:
    return re.sub(r"\s+", " ", str(v)).strip()[:n] if v not in (None, "", [], {}) else ""


def _notes(d: Any) -> List[str]:
    out = []
    if isinstance(d, dict):
        for k in ("notes", "note", "memo"):
            if d.get(k):
                out.append(_s(d[k], 240))
    return out


def summary_text(cand: Dict[str, Any]) -> str:
    """DB 에 이미 있는 구조화 추출 결과로 학생과 무관한 공고 요약을 만든다 (새 AI 호출 없음). 모르는 모양은 건너뛴다."""
    e = cand.get("elig") if isinstance(cand.get("elig"), dict) else {}
    L: List[str] = ["공고: {}".format(cand.get("title") or e.get("scholarship_name") or "")]
    if e.get("foundation"):
        L.append("재단: " + _s(e["foundation"]))
    st, en = cand.get("apply_start") or e.get("apply_start"), cand.get("apply_end") or e.get("apply_end")
    if st or en:
        L.append("모집기간: {} ~ {}".format(st or "?", en or "?"))
    if e.get("apply_how"):
        L.append("신청 방법: " + _s(e["apply_how"], 240))
    if e.get("amount"):
        L.append("지원 내용: " + _s(e["amount"], 240))
    if cand.get("summary") or e.get("summary"):
        L.append("요약: " + _s(cand.get("summary") or e.get("summary"), 300))
    sel = e.get("selection") if isinstance(e.get("selection"), dict) else {}
    if sel.get("method"):
        L.append("선발 방식: " + _s(sel["method"], 240))
    for key, label in (("enrollment", "학적"), ("gpa", "성적"), ("credits", "학점"), ("income", "소득"), ("region", "지역")):
        for n in _notes(e.get(key)):
            L.append("{}: {}".format(label, n))
    others = [_s(x if isinstance(x, str) else (x or {}).get("text") if isinstance(x, dict) else "", 160) for x in e.get("other_conditions") or []]
    others += [_s((x or {}).get("text"), 160) for x in e.get("unmodeled_conditions") or [] if isinstance(x, dict)]
    others = [x for x in others if x]
    if others:
        L.append("기타 조건: " + " / ".join(others[:6]))
    variants = [v for v in e.get("variants") or [] if isinstance(v, dict) and v.get("name")]
    if variants:
        L.append("유형: " + "; ".join("{}{}".format(_s(v["name"], 40), " ({})".format(_s(v.get("amount"), 40)) if v.get("amount") else "") for v in variants[:10]))
    docs = [_s(d if isinstance(d, str) else (d or {}).get("name") if isinstance(d, dict) else "", 60) for d in e.get("documents") or e.get("procedure") or []]
    docs = [x for x in docs if x]
    if docs:
        L.append("제출 서류·절차: " + ", ".join(docs[:12]))
    excl = [_s(x if isinstance(x, str) else (x or {}).get("text") if isinstance(x, dict) else "", 100) for x in e.get("exclusions") or e.get("exclusion_items") or []]
    excl = [x for x in excl if x]
    if excl:
        L.append("제외 대상: " + " / ".join(excl[:6]))
    return "\n".join(L)[:SUMMARY_MAX]


def _shingles(t: str) -> set:
    t = re.sub(r"\s+", "", t or "")
    return {t[i:i + 8] for i in range(0, max(1, len(t) - 7), 4)}


def _dup_of(shingles: set, seen: Sequence[set]) -> bool:
    if not shingles:
        return True
    for o in seen:
        inter = len(shingles & o)
        if inter and inter / max(1, min(len(shingles), len(o))) >= DUP_JACCARD:
            return True
    return False


def notice_docs(nid: str, cand: Dict[str, Any], texts: Optional[Dict[str, Any]], chunk_size: int = 700) -> List[Doc]:
    """요약 + 본문 조각 + (본문·다른 첨부와 거의 같지 않은) 첨부 조각."""
    docs = [Doc(nid + "#s", nid, "summary", "구조화 요약", summary_text(cand))]
    texts = texts or {}
    seen: List[set] = []
    body = texts.get("body") or ""
    if body.strip():
        seen.append(_shingles(body))
        for i, t in enumerate(T.split_text(body, chunk_size)):
            docs.append(Doc("{}#b{}".format(nid, i), nid, "body", "본문", t))
    for n, f in enumerate(texts.get("files") or [], 1):
        ft = f.get("text") or ""
        sh = _shingles(ft)
        if not ft.strip() or _dup_of(sh, seen):
            continue
        seen.append(sh)
        label = "첨부{}: {}".format(n, (f.get("name") or "").rsplit("/", 1)[-1])
        if f.get("role") == "FORM":
            label += " (양식)"
        for i, t in enumerate(T.split_text(ft, chunk_size)):
            docs.append(Doc("{}#a{}c{}".format(nid, n, i), nid, "attach", label, t, str(f.get("seq") or "")))
    return docs


def content_hash(docs: Iterable[Doc], model: str, dims: int) -> str:
    h = hashlib.sha1()
    h.update("{}|{}".format(model, dims).encode("utf-8"))
    for d in docs:
        h.update(d.id.encode("utf-8"))
        h.update(d.text.encode("utf-8"))
        h.update(d.file_seq.encode("utf-8"))
    return h.hexdigest()
