# -*- coding: utf-8 -*-
"""수집 뒤 하루 한 번 도는 색인 단계. 마감 안 된 공고만 색인하고, 바뀐 공고만 다시 임베딩하며, 더 이상 후보가 아닌 공고는 지운다."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from . import docs as D

log = logging.getLogger("scholarship.rag")
MIN_CHARS = 10


def notice_key(c: Dict[str, Any]) -> str:
    return "{}:{}".format(c.get("src_cd"), c.get("src_id"))


def _meta(d: D.Doc, cand: Dict[str, Any], chash: str) -> Dict[str, Any]:
    m = {"notice_id": d.notice_id, "kind": d.kind, "label": d.label[:120], "title": (cand.get("title") or "")[:100], "text": d.text}
    if d.file_seq:
        m["file_seq"] = d.file_seq
    if d.kind == "summary":
        m["chash"] = chash
        m["apply_end"] = cand["apply_end"].isoformat() if cand.get("apply_end") else ""
    return m


def build_index(source, embedder, store, today: date, include_closed: bool = False, force: bool = False, limit: Optional[int] = None,
                plan_only: bool = False) -> Dict[str, Any]:
    """source: list_candidates(today), get_notice_texts(pairs) 를 가진 공급원(OracleRepository 등).
    plan_only 이면 임베딩·저장 호출 없이 개수와 글자 수만 센다."""
    cands = source.list_candidates(date(2000, 1, 1) if include_closed else today)
    if limit:
        cands = cands[:limit]
    stats = {"notices": len(cands), "indexed": 0, "skipped": 0, "removed": 0, "docs": 0, "chars": 0, "failed": 0}
    keep = {notice_key(c) for c in cands}
    texts_by = source.get_notice_texts([(c.get("src_cd"), c.get("src_id")) for c in cands]) if cands else {}
    for c in cands:
        nid = notice_key(c)
        docs = [d for d in D.notice_docs(nid, c, texts_by.get((c.get("src_cd"), c.get("src_id")))) if len(d.text.strip()) >= MIN_CHARS]
        chash = D.content_hash(docs, getattr(embedder, "model", ""), getattr(embedder, "dims", 0))
        stats["docs"] += len(docs)
        stats["chars"] += sum(len(d.text) for d in docs)
        if plan_only:
            continue
        try:
            if not force:
                got = store.fetch([nid + "#s"]).get(nid + "#s")
                if got and (got.get("metadata") or {}).get("chash") == chash:
                    stats["skipped"] += 1
                    continue
            old = set(store.list_ids(nid + "#"))
            vecs = embedder.embed([d.text for d in docs])
            store.upsert([{"id": d.id, "values": v, "metadata": _meta(d, c, chash)} for d, v in zip(docs, vecs)])
            stale = sorted(old - {d.id for d in docs})                       # 줄어든 조각(첨부가 빠졌거나 본문이 짧아짐)
            if stale:
                store.delete(stale)
            stats["indexed"] += 1
        except Exception as e:                                               # 한 공고가 실패해도 나머지는 계속한다
            stats["failed"] += 1
            log.warning("[RAG] %s 색인 실패: %s", nid, type(e).__name__)
    if not plan_only and not limit:                                          # 마감·삭제로 후보에서 빠진 공고 정리 (일부만 본 실행에서는 하지 않는다)
        try:
            gone = sorted({i for i in store.list_ids("") if i.split("#", 1)[0] not in keep})
            if gone:
                store.delete(gone)
                stats["removed"] = len({i.split("#", 1)[0] for i in gone})
        except Exception as e:
            log.warning("[RAG] 지난 공고 정리 실패: %s", type(e).__name__)
    return stats
