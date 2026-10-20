# -*- coding: utf-8 -*-
"""질문 때 쓰는 검색: 질문(개인정보 가린 뒤)을 임베딩해 요청한 공고들 안에서 관련 조각을 꺼낸다. 질문·결과는 저장하지 않는다."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


class Retriever:
    def __init__(self, embedder, store, top_k: int = 6, summary_notices: int = 3):
        self.embedder, self.store, self.top_k, self.summary_notices = embedder, store, top_k, summary_notices

    def sources_for(self, question: str, notice_ids: Sequence[str], titles: Dict[str, str], focus: str = "") -> Dict[str, Any]:
        """반환: {"sources": [{"id","notice","title","label","text"}], "chosen": [공고 id...], "matches": N}. 결과가 없으면 sources 가 빈 목록."""
        ids = list(notice_ids)
        vec = self.embedder.embed_one(question)
        matches = [m for m in self.store.query(vec, self.top_k, ids) if (m.get("metadata") or {}).get("text")]
        if focus and focus in ids and not any(m["metadata"].get("notice_id") == focus for m in matches):         # 고른 공고는 검색 결과에 없어도 요약을 함께 보낸다
            matches.append({"id": focus + "#s", "score": 0.0, "metadata": {}})
        order: List[str] = []
        for m in matches:
            nid = (m.get("metadata") or {}).get("notice_id") or m["id"].split("#", 1)[0]
            if nid not in order:
                order.append(nid)
        if focus in order:
            order.remove(focus)
            order.insert(0, focus)
        order = order[: self.summary_notices]
        sums = self.store.fetch([n + "#s" for n in order]) if order else {}
        sources: List[Dict[str, str]] = []

        def add(nid: str, label: str, text: str, file_seq: str = "") -> None:
            src = {"id": "S{}".format(len(sources) + 1), "notice": nid, "title": titles.get(nid, ""), "label": label, "text": text}
            if file_seq:
                src["file_seq"] = file_seq
            sources.append(src)
        for nid in order:
            s = (sums.get(nid + "#s") or {}).get("metadata") or {}
            if s.get("text"):
                add(nid, "구조화 요약", s["text"])
        seen = {s["text"] for s in sources}
        by: Dict[str, List[Dict[str, Any]]] = {}
        for m in matches:
            md = m.get("metadata") or {}
            if md.get("kind") == "summary" or not md.get("text") or md["text"] in seen or md.get("notice_id") not in order:
                continue
            by.setdefault(md["notice_id"], []).append(md)
        for nid in order:
            for md in by.get(nid, []):
                add(nid, md.get("label") or "본문", md["text"], md.get("file_seq") or "")
        return {"sources": sources, "chosen": order, "matches": len(matches)}
