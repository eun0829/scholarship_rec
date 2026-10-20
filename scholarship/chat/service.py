# -*- coding: utf-8 -*-
"""[CHAT] 공고 질문 답변. 판정은 recommend 가 코드로 하고, 여기서는 공고 원문(본문·첨부)을 근거로 설명만 한다.

단계
  A  share_results=false : 원문과 질문만 AI 에 보낸다 (학생 정보 없음)
  B  share_results=true  : 서버가 profile 로 판정을 다시 계산해 '시스템 판정' 요약을 AI 에 함께 보낸다. profile 원본은 AI 에 보내지 않는다.
어느 단계에서도 AI 는 판정을 바꾸지 못한다. 답은 원문 인용으로 뒷받침될 때만 내보낸다 (verify.py). AI 가 실패해도 추천 API 는 영향이 없다 (503 CHAT_UNAVAILABLE).
서버는 질문·답변·학생 정보를 저장하지 않고 로그에도 남기지 않는다 (요청 번호·상태·시간·건수·토큰만).
"""
from __future__ import annotations

import copy
from datetime import date
import dataclasses
import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from ..common.ai.client import AIError, make_client
from ..recommend import request as RQ
from ..recommend.request import ApiError
from . import prompt as P
from . import text as T
from . import verify as V
from . import files as FL
from .request import ChatRequest, parse_chat_request

log = logging.getLogger("scholarship.chat")
MAX_CONTEXT_NOTICES = 3
CACHE_MAX = 300
DISCLAIMER = "AI가 공고 원문을 바탕으로 안내한 내용입니다. 자격과 선발 여부는 공고 원문과 재단·학교의 확인이 우선합니다."


def verdict_text(nid: str, v: Dict[str, Any]) -> str:
    """시스템 판정 요약 (AI 에 보내는 글). 값은 이미 코드가 계산한 것이다."""
    lines = ["공고 {} '{}' 의 시스템 판정: {}".format(nid, v.get("title") or "", v.get("verdict_label") or "")]
    best = v.get("best_variant")
    if best:
        lines.append("가장 가까운 유형: {} ({})".format(best.get("name"), best.get("verdict_label")))

    def add(label: str, items, n: int = 8) -> None:
        items = [str(x) for x in (items or []) if x]
        if items:
            lines.append("{}: {}".format(label, " / ".join(items[:n])))
    add("충족한 조건", v.get("passed"))
    add("확인이 필요한 조건", v.get("to_confirm"))
    add("미달 사유", v.get("fail_reasons"), 3)
    mv = ["{}={}".format(x.get("name"), x.get("verdict_label")) for x in v.get("matched_variants") or []]
    add("해당 가능한 유형", mv, 10)
    um = ["{}({})".format(x.get("name"), x.get("reason")) for x in v.get("unmatched_variants") or []]
    add("해당하지 않는 유형", um, 10)
    return "\n".join(lines)


class ChatService:
    def __init__(self, settings, source, recommender, client=None, retriever=None):
        self.s, self.source, self.rec, self._client = settings, source, recommender, client
        self.retriever = retriever if retriever is not None else self._make_retriever(settings)
        self._sem = threading.BoundedSemaphore(max(1, settings.chat_max_concurrent))
        self._cache: "OrderedDict[str, Tuple[float, Dict[str, Any]]]" = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _make_retriever(settings):
        """RAG_ENABLED 이고 키·주소가 모두 있을 때만 만든다. 하나라도 없으면 None (예전 방식으로 답한다)."""
        if not getattr(settings, "rag_enabled", False):
            return None
        try:
            from ..rag.embed import OpenAIEmbedder
            from ..rag.retrieve import Retriever
            from ..rag.store import PineconeStore
            key, base = settings.embedding_endpoint()
            if not key or not base:
                raise ValueError("임베딩 키·주소 없음")
            emb = OpenAIEmbedder(key, settings.rag_embed_model, settings.rag_embed_dims, base, timeout=settings.rag_timeout, retries=2)
            store = PineconeStore(settings.pinecone_api_key, settings.pinecone_host, settings.pinecone_namespace, timeout=settings.rag_timeout, retries=2)
            return Retriever(emb, store, settings.rag_top_k)
        except Exception as e:
            log.warning("RAG 를 켤 수 없어 예전 방식으로 답합니다: %s", type(e).__name__)
            return None

    # ------------------------------------------------------------------ 공개
    def handle(self, body: Any) -> Tuple[int, Dict[str, Any]]:
        rid = body.get("request_id") if isinstance(body, dict) and isinstance(body.get("request_id"), str) and RQ.REQUEST_ID.match(body["request_id"]) else None
        if not self.s.chat_enabled:
            return 404, ApiError("CHAT_DISABLED", "챗봇 기능이 꺼져 있습니다").body(rid)
        try:
            req = parse_chat_request(body)
            if not self._sem.acquire(blocking=False):
                return 429, ApiError("BUSY", "질문이 많아 잠시 후 다시 시도해 주세요").body(rid)
            try:
                return 200, self._answer(req)
            finally:
                self._sem.release()
        except ApiError as e:
            return e.status, e.body(rid)

    # ------------------------------------------------------------------ 처리
    def _answer(self, req: ChatRequest) -> Dict[str, Any]:
        t0 = time.monotonic()
        today = req.today
        today = today or date.today()
        cands = {"{}:{}".format(c.get("src_cd"), c.get("src_id")): c for c in self.source.list_candidates(date(2000, 1, 1) if self.rec.s.recommend_include_closed else today)}      # 시험용 전체 조회일 때는 추천과 같은 범위의 공고를 물을 수 있다
        found = [i for i in req.notice_ids if i in cands]
        missing = [i for i in req.notice_ids if i not in cands]
        asked_n = len(req.notice_ids)
        if req.focus and req.focus in found:                                     # 공고를 골랐으면 그 공고에 대해서만 답한다 (다른 공고의 원문은 가져오지도 않는다)
            found = [req.focus]
        if not found:
            raise ApiError("INVALID_REQUEST", "물어볼 수 있는 공고가 없습니다 (마감되었거나 목록에 없음)", [{"field": "notice_ids", "message": "열려 있는 공고가 아닙니다"}])

        verdicts: Dict[str, Dict[str, Any]] = {}
        warnings = list(req.warnings)
        if req.share_results:
            clean, pw = RQ.sanitize_profile(req.profile or {}, self.rec.s.default_gpa_scale, today=today)
            warnings += pw
            verdicts = self.rec.evaluate_selected(clean, found, today)
        mode = "B" if req.share_results else "A"

        titles = {i: cands[i].get("title") or "" for i in found}
        question = T.mask_pii(req.question)
        history = [{"role": h["role"], "text": T.mask_pii(h["text"])} for h in req.history]
        prev_q = next((h["text"] for h in reversed(history) if h["role"] == "user"), "")
        sources: List[Dict[str, str]] = []
        chosen: List[str] = []
        rag_used = False
        if self.retriever is not None:                                       # 색인 검색: 관련 조각만 꺼낸다 (실패·빈 결과면 아래 예전 방식)
            try:
                got = self.retriever.sources_for((question + " " + prev_q).strip(), found, titles, req.focus)
                if got["sources"]:
                    sources, chosen, rag_used = got["sources"], got["chosen"], True
            except Exception as e:
                log.warning("chat rid=%s RAG 실패 -> 예전 방식 (%s)", req.request_id, type(e).__name__)
        usable: List[str] = list(chosen)
        if not rag_used:
            texts = self.source.notice_texts([tuple(i.split(":", 1)) for i in found])
            chunks_by: Dict[str, List[T.Chunk]] = {}
            for i in found:
                tx = texts.get(tuple(i.split(":", 1)))
                if tx:
                    chunks_by[i] = T.chunks_of(i, tx)
            usable = [i for i in found if chunks_by.get(i)]
            no_text = [i for i in found if i not in usable]
            if no_text:
                warnings.append("원문이 저장되지 않은 공고가 있어 답변에 쓰지 못했습니다: {}".format(", ".join(no_text)))
            order = T.rank_by_chunks(question + " " + " ".join(h["text"] for h in history if h["role"] == "user"), {i: titles[i] for i in usable}, chunks_by, req.focus)
            chosen = order[:MAX_CONTEXT_NOTICES]
            all_chunks = [c for i in chosen for c in chunks_by[i]]
            picked = T.select_chunks(question + " " + prev_q, all_chunks, self.s.chat_context_chars)               # 짧은 후속 질문('그럼 유형별로는?')은 직전 질문도 함께 보고 조각을 고른다
            sources = self._sources(picked, titles)
        n_ctx, ctx_chars, ctx_attach = len(sources), sum(len(x["text"]) for x in sources), len({x["label"] for x in sources if x["label"].startswith("첨부")})     # 원문 조각만 센다 (시스템 판정 요약은 제외)
        extra = ""
        for n, i in enumerate([i for i in chosen if i in verdicts], 1):
            vt = verdict_text(i, verdicts[i])
            sources.append({"id": "J{}".format(n), "notice": i, "title": titles[i], "label": "시스템 판정", "text": vt})
            extra += vt + "\n"
        used = [{"id": i, "title": titles[i], "url": cands[i].get("origin_url")} for i in chosen]

        diag = {"asked": asked_n, "found": len(found), "usable": len(usable), "chosen": len(chosen), "focus_in": bool(req.focus and req.focus in chosen),
                "chunks": n_ctx, "chars": ctx_chars, "attach_used": ctx_attach,
                "rag": rag_used, "ai": False}     # 개수만. 질문·원문 내용은 담지 않는다 (시험 화면에서만 응답에 붙는다)
        log.info("chat rid=%s context rag=%s found=%d usable=%d chosen=%d sources=%d chars=%d", req.request_id, rag_used, len(found), len(usable), len(chosen), n_ctx, ctx_chars)   # 질문 내용은 남기지 않는다
        if not n_ctx:
            return self._finish(req, mode, {"outcome": "not_found", "answer": V.NOT_FOUND, "citations": [], "followups": [], "warnings": []},
                                used, missing, warnings, False, t0, 0, 0, diag)

        key = None
        if not history:
            digest = hashlib.sha256(json.dumps([question, sorted(chosen), mode, [(s["id"], hashlib.sha256(s["text"].encode("utf-8")).hexdigest()[:16]) for s in sources]],
                                               ensure_ascii=False).encode("utf-8")).hexdigest()
            key = digest
            hit = self._cache_get(key)
            if hit is not None:
                return self._finish(req, mode, hit, used, missing, warnings, True, t0, 0, 0, diag)

        user_msg = P.build_user_message(question, history, sources)
        try:
            resp = self._ai().complete(P.SYSTEM, user_msg, [], max_tokens=self.s.chat_max_tokens)
        except (AIError, ValueError) as e:
            log.error("chat rid=%s AI 실패 %s", req.request_id, type(e).__name__)
            raise ApiError("CHAT_UNAVAILABLE", "지금은 답변을 만들 수 없습니다. 잠시 후 다시 시도해 주세요")
        diag["ai"] = True
        res = V.verify(resp.text, sources, extra + " " + question)
        diag.update({"out_tokens": resp.output_tokens, "max_tokens": self.s.chat_max_tokens, "why": res.get("why") or ""})
        if key and res["outcome"] == "answered":
            self._cache_put(key, res)
        return self._finish(req, mode, res, used, missing, warnings, False, t0, resp.input_tokens, resp.output_tokens, diag)

    # ------------------------------------------------------------------ 보조
    def _ai(self):
        if self._client is not None:
            return self._client
        s = dataclasses.replace(self.s, ai_model=self.s.chat_model or self.s.ai_model)
        return make_client(s, max_tokens=self.s.chat_max_tokens, timeout=self.s.chat_timeout)

    @staticmethod
    def _sources(picked: List[T.Chunk], titles: Dict[str, str]) -> List[Dict[str, str]]:
        groups: "OrderedDict[Tuple[str, str], List[T.Chunk]]" = OrderedDict()
        for c in picked:
            groups.setdefault((c.notice_id, c.source), []).append(c)
        out = []
        for n, ((nid, label), cs) in enumerate(groups.items(), 1):
            parts: List[str] = []
            prev = None
            for c in cs:
                if prev is not None and c.index != prev + 1:
                    parts.append("…")
                parts.append(c.text)
                prev = c.index
            src = {"id": "S{}".format(n), "notice": nid, "title": titles.get(nid, ""), "label": label, "text": "\n".join(parts)}
            if cs[0].file_seq:
                src["file_seq"] = cs[0].file_seq
            out.append(src)
        return out

    def _with_files(self, citations: List[Dict[str, Any]], used: List[Dict[str, Any]]):
        """인용이 첨부파일에서 나왔고 서버에 원본이 있으면 file 정보를 붙인다. 반환: (인용 목록, [{notice_id, title, file}] 공고별 첨부 목록)."""
        out = [{k: v for k, v in c.items() if k != "file_seq"} for c in citations]
        want = {(c["notice_id"], str(c["file_seq"])) for c in citations if c.get("file_seq")}
        why = ""
        if not want:
            return out, [], ""
        titles = {u["id"]: u["title"] for u in used}
        try:
            texts = self.source.notice_texts(sorted({tuple(n.split(":", 1)) for n, _ in want}))
        except Exception as e:                                                   # 파일 연결은 덤이다. 읽지 못해도 답변은 그대로 낸다
            log.warning("chat 첨부 정보 읽기 실패 (%s)", type(e).__name__)
            return out, [], "첨부 정보를 읽지 못함"
        pub: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for nid, seq in want:
            for f in (texts.get(tuple(nid.split(":", 1))) or {}).get("files") or []:
                if str(f.get("seq")) == seq:
                    info = FL.public_file(self.s.nas_root, f)
                    if info:
                        pub[(nid, seq)] = info
                    else:
                        why = FL.why_not(self.s.nas_root, f.get("nas_path"))
        files: List[Dict[str, Any]] = []
        seen = set()
        for c, o in zip(citations, out):
            info = pub.get((c["notice_id"], str(c.get("file_seq") or "")))
            if info:
                o["file"] = info
                if (c["notice_id"], info["seq"]) not in seen:
                    seen.add((c["notice_id"], info["seq"]))
                    files.append({"notice_id": c["notice_id"], "title": titles.get(c["notice_id"], ""), "file": info})
        return out, files, why

    def file_for_download(self, notice_id: str, seq: str, today: Optional[date] = None):
        """다운로드 허용 확인. 반환: (경로, 파일명). 허용되지 않으면 ApiError(404) — 이유는 구분해 알려주지 않는다."""
        nf = ApiError("NOT_FOUND", "파일을 찾을 수 없습니다")
        if not (FL.NOTICE_ID.match(notice_id or "") and FL.FILE_SEQ.match(seq or "")):
            raise nf
        today = today or date.today()
        cands = {"{}:{}".format(c.get("src_cd"), c.get("src_id")) for c in self.source.list_candidates(date(2000, 1, 1) if self.rec.s.recommend_include_closed else today)}
        if notice_id not in cands:
            raise nf
        texts = self.source.notice_texts([tuple(notice_id.split(":", 1))])
        for f in (texts.get(tuple(notice_id.split(":", 1))) or {}).get("files") or []:
            if str(f.get("seq")) == seq:
                info = FL.public_file(self.s.nas_root, f)
                if info:
                    return FL.safe_path(self.s.nas_root, f.get("nas_path")), info["name"]
        raise nf

    def _cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            ent = self._cache.get(key)
            if ent and time.monotonic() - ent[0] < self.s.chat_cache_seconds:
                self._cache.move_to_end(key)
                return copy.deepcopy(ent[1])
            self._cache.pop(key, None)
            return None

    def _cache_put(self, key: str, res: Dict[str, Any]) -> None:
        if self.s.chat_cache_seconds <= 0:
            return
        with self._lock:
            self._cache[key] = (time.monotonic(), copy.deepcopy(res))
            while len(self._cache) > CACHE_MAX:
                self._cache.popitem(last=False)

    def _finish(self, req: ChatRequest, mode: str, res: Dict[str, Any], used, missing, warnings, cached: bool, t0: float, tin: int, tout: int, diag: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        log.info("chat rid=%s mode=%s outcome=%s cached=%s notices=%d ms=%d in=%d out=%d", req.request_id, mode, res["outcome"], cached, len(used),
                 (time.monotonic() - t0) * 1000, tin, tout)
        citations, source_files, file_why = self._with_files(res["citations"], used)
        if diag is not None:
            n_att = len({(c["notice_id"], c["file_seq"]) for c in res["citations"] if c.get("file_seq")})
            diag["cited_files"], diag["linked_files"] = n_att, len(source_files)
            if n_att and not source_files:
                diag["file_why"] = file_why
        extra_out = {"diag": diag} if diag is not None and getattr(self.s, "api_enable_demo", False) else {}       # 시험 화면(/demo)에서만 붙는 점검 값 (개수만)
        return {**extra_out, "request_id": req.request_id, "mode": mode, "outcome": res["outcome"], "grounded": res["outcome"] == "answered",
                "answer": res["answer"], "citations": citations, "source_files": source_files, "followups": res["followups"],
                "notices": used, "notices_missing": missing, "warnings": warnings + res["warnings"], "cached": cached, "disclaimer": DISCLAIMER}
