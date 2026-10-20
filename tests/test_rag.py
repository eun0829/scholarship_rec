# -*- coding: utf-8 -*-
"""챗봇 색인(RAG): 문서 만들기, OpenAI·Pinecone 호출 형식, 하루 한 번 색인, 검색, 챗봇 연결(실패하면 예전 방식). 외부 호출은 모두 가짜."""
import hashlib
import json
from datetime import date

import pytest

from scholarship.common.config import Settings
from scholarship.rag import docs as D
from scholarship.rag.embed import EmbedError, OpenAIEmbedder
from scholarship.rag.indexer import build_index
from scholarship.rag.retrieve import Retriever
from scholarship.rag.store import MemoryStore, PineconeStore, StoreError

TODAY = date(2026, 10, 20)


class FakeEmbedder:
    model, dims = "fake", 64

    def __init__(self):
        self.calls, self.texts = 0, 0
        self.fail = False

    def _vec(self, t):
        v = [0.0] * self.dims
        t = "".join(t.split())
        for i in range(len(t) - 1):
            v[int(hashlib.md5(t[i:i + 2].encode("utf-8")).hexdigest(), 16) % self.dims] += 1.0
        return v

    def embed(self, texts):
        if self.fail:
            raise EmbedError("x")
        self.calls += 1
        self.texts += len(texts)
        return [self._vec(t) for t in texts]

    def embed_one(self, t):
        return self.embed([t])[0]


def cand(sid="9210", title="춘천시 봄내장학", end=date(2026, 11, 20), summary="춘천시민 대학생 장학금"):
    return {"src_cd": "EXT02", "src_id": sid, "title": title, "apply_start": date(2026, 10, 1), "apply_end": end, "summary": summary,
            "elig": {"foundation": "춘천인재육성장학재단", "amount": "최대 100만원", "apply_how": "구글폼 접수", "selection": {"method": "성적과 생활수준 평가"},
                     "gpa": {"notes": "직전학기 B학점 이상"}, "variants": [{"name": "일반", "amount": "100만원"}, {"name": "저소득"}],
                     "documents": [{"name": "신청서"}, {"name": "재학증명서"}], "exclusions": [{"text": "중복 수혜 불가"}]}}


class Src:
    def __init__(self, cands, texts):
        self.cands, self.texts = cands, texts

    def list_candidates(self, today):
        return [c for c in self.cands if c["apply_end"] >= today]

    def get_notice_texts(self, pairs):
        return {tuple(p): self.texts[tuple(p)] for p in pairs if tuple(p) in self.texts}


BODY = "봄내장학 모집 안내\n\n신청 자격: 춘천시에 1년 이상 거주한 대학생\n\n제출 서류: 신청서, 재학증명서, 성적증명서"
ATT = "선발 기준은 성적 50%, 생활수준 30%, 봉사 20%이며 유형별로 다르게 평가한다.\n\n" * 3


def texts(att=ATT):
    return {("EXT02", "9210"): {"body": BODY, "files": [{"seq": 1, "name": "a/공고.hwp", "role": "GUIDE", "text": att}, {"seq": 2, "name": "a/공고.pdf", "role": "GUIDE", "text": att}]}}


# ================================================================== 문서
def test_summary_text_uses_structured_fields_and_is_short():
    t = D.summary_text(cand())
    for needle in ("공고: 춘천시 봄내장학", "모집기간: 2026-10-01 ~ 2026-11-20", "신청 방법: 구글폼 접수", "성적: 직전학기 B학점 이상", "유형: 일반 (100만원); 저소득", "제출 서류·절차: 신청서, 재학증명서", "제외 대상: 중복 수혜 불가"):
        assert needle in t, needle
    assert len(t) <= D.SUMMARY_MAX and D.summary_text({"title": "x", "elig": None}) == "공고: x"          # 모르는 모양은 건너뛴다


def test_notice_docs_drop_duplicate_attachments_and_use_ascii_ids():
    ds = D.notice_docs("EXT02:9210", cand(), texts()["EXT02", "9210"])
    assert [d.kind for d in ds].count("summary") == 1
    assert len([d for d in ds if d.kind == "attach"]) >= 1 and not [d for d in ds if d.label.startswith("첨부2")]       # hwp 와 같은 pdf 는 하나만
    assert all(d.id.isascii() for d in ds) and len({d.id for d in ds}) == len(ds)


# ================================================================== OpenAI / Pinecone 호출 형식
class Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._d = status, data or {}
        self.text = json.dumps(self._d)

    def json(self):
        return self._d


def test_openai_embedder_request_shape_retry_and_no_key_in_errors(monkeypatch):
    seen = []

    def post(url, headers=None, json=None, timeout=None, allow_redirects=None):
        seen.append((url, headers, json))
        if len(seen) == 1:
            return Resp(429)
        return Resp(200, {"data": [{"index": i, "embedding": [0.1, float(i)]} for i in reversed(range(len(json["input"])))], "usage": {"total_tokens": 7}})
    monkeypatch.setattr("scholarship.rag.embed.requests.post", post)
    e = OpenAIEmbedder("sk-SECRET", "text-embedding-3-large", 1024, sleep=lambda s: None)
    out = e.embed(["가", "나"])
    assert out == [[0.1, 0.0], [0.1, 1.0]] and e.tokens_used == 7                       # 순서는 index 로 맞춘다
    url, headers, body = seen[-1]
    assert url == "https://api.openai.com/v1/embeddings" and headers["Authorization"] == "Bearer sk-SECRET" and body["dimensions"] == 1024 and body["model"] == "text-embedding-3-large"
    monkeypatch.setattr("scholarship.rag.embed.requests.post", lambda *a, **k: Resp(401))
    with pytest.raises(EmbedError) as ei:
        e.embed(["가"])
    assert "sk-SECRET" not in str(ei.value) and "401" in str(ei.value)
    with pytest.raises(EmbedError):
        OpenAIEmbedder("")


def test_pinecone_store_request_shapes(monkeypatch):
    calls = []

    def req(method, url, headers=None, json=None, params=None, timeout=None, allow_redirects=None):
        calls.append((method, url, headers, json, params))
        if url.endswith("/vectors/list"):
            return Resp(200, {"vectors": [{"id": "EXT02:1#s"}], "pagination": {"next": "t2"}} if not (params or {}).get("paginationToken") else {"vectors": [{"id": "EXT02:1#b0"}]})
        if url.endswith("/query"):
            return Resp(200, {"matches": [{"id": "a", "score": 0.9, "metadata": {"text": "t"}}]})
        if url.endswith("/vectors/fetch"):
            return Resp(200, {"vectors": {"EXT02:1#s": {"metadata": {"chash": "h"}}}})
        return Resp(200, {})
    monkeypatch.setattr("scholarship.rag.store.requests.request", req)
    s = PineconeStore("pc-SECRET", "idx-abc.svc.pinecone.io", "ns1", sleep=lambda x: None)
    s.upsert([{"id": str(i), "values": [0.1], "metadata": {}} for i in range(250)])
    ups = [c for c in calls if c[1].endswith("/vectors/upsert")]
    assert len(ups) == 3 and ups[0][1] == "https://idx-abc.svc.pinecone.io/vectors/upsert" and ups[0][2]["Api-Key"] == "pc-SECRET" and ups[0][3]["namespace"] == "ns1"
    assert s.query([0.1], 5, ["EXT02:1"]) == [{"id": "a", "score": 0.9, "metadata": {"text": "t"}}]
    q = [c for c in calls if c[1].endswith("/query")][0][3]
    assert q["filter"] == {"notice_id": {"$in": ["EXT02:1"]}} and q["topK"] == 5 and q["includeMetadata"] is True
    assert s.list_ids("EXT02:1#") == ["EXT02:1#s", "EXT02:1#b0"]                         # 쪽 넘김
    assert s.fetch(["EXT02:1#s"])["EXT02:1#s"]["metadata"]["chash"] == "h"
    s.delete(["a"])
    assert calls[-1][1].endswith("/vectors/delete") and calls[-1][3]["ids"] == ["a"]
    monkeypatch.setattr("scholarship.rag.store.requests.request", lambda *a, **k: Resp(403))
    with pytest.raises(StoreError) as ei:
        s.query([0.1], 1)
    assert "pc-SECRET" not in str(ei.value)
    with pytest.raises(StoreError):
        PineconeStore("", "")


# ================================================================== 색인
def test_index_is_incremental_and_cleans_up_closed_notices():
    emb, store = FakeEmbedder(), MemoryStore()
    closed = cand("1", "지난 공고", end=date(2026, 10, 1))
    src = Src([cand(), closed], {**texts(att=ATT * 12), ("EXT02", "1"): {"body": "지난 공고 본문입니다 충분히 긴 글", "files": []}})
    st = build_index(src, emb, store, TODAY)
    assert st["notices"] == 1 and st["indexed"] == 1 and st["skipped"] == 0 and store.list_ids("EXT02:9210#")
    n = emb.texts
    st2 = build_index(src, emb, store, TODAY)                                             # 그대로면 임베딩하지 않는다
    assert st2["skipped"] == 1 and st2["indexed"] == 0 and emb.texts == n
    src.texts = texts(att="완전히 다른 첨부 내용입니다. 신청은 구글폼으로만 받습니다.")           # 내용이 바뀌면 다시 올리고 줄어든 조각은 지운다
    before = set(store.list_ids("EXT02:9210#"))
    st3 = build_index(src, emb, store, TODAY)
    assert st3["indexed"] == 1 and emb.texts > n and len(before) > len(store.list_ids("EXT02:9210#")) > 1
    store.upsert([{"id": "EXT02:77#s", "values": [1.0] * 64, "metadata": {"notice_id": "EXT02:77"}}])      # 후보에서 빠진 공고(마감·삭제)는 지운다
    st4 = build_index(src, emb, store, TODAY)
    assert st4["removed"] == 1 and not store.list_ids("EXT02:77")
    store.upsert([{"id": "EXT02:78#s", "values": [1.0] * 64, "metadata": {"notice_id": "EXT02:78"}}])
    build_index(src, emb, store, TODAY, limit=1)                                           # 일부만 본 실행은 정리하지 않는다
    assert store.list_ids("EXT02:78")


def test_index_plan_only_makes_no_calls_and_failures_are_isolated():
    emb, store = FakeEmbedder(), MemoryStore()
    st = build_index(Src([cand()], texts()), emb, store, TODAY, plan_only=True)
    assert st["docs"] > 1 and st["chars"] > 100 and emb.calls == 0 and not store.v
    emb.fail = True
    st = build_index(Src([cand(), cand("2", "다른 공고")], {**texts(), ("EXT02", "2"): {"body": "다른 공고 본문 내용이 충분히 깁니다", "files": []}}), emb, store, TODAY)
    assert st["failed"] == 2 and st["indexed"] == 0                                         # 실패해도 멈추지 않고 센다


# ================================================================== 검색
def indexed():
    emb, store = FakeEmbedder(), MemoryStore()
    src = Src([cand(), cand("9211", "대전시 인재육성장학", summary="대전시 거주 대학생")], {**texts(), ("EXT02", "9211"): {"body": "대전시 인재육성장학 안내\n\n대전에 주민등록을 둔 대학생 대상", "files": []}})
    build_index(src, emb, store, TODAY)
    return emb, store


def test_retriever_returns_summary_first_and_only_requested_notices():
    emb, store = indexed()
    r = Retriever(emb, store, top_k=4)
    got = r.sources_for("선발 기준 성적 비율", ["EXT02:9210"], {"EXT02:9210": "춘천시 봄내장학"}, "EXT02:9210")
    assert got["chosen"] == ["EXT02:9210"] and got["sources"][0]["label"] == "구조화 요약" and all(s["notice"] == "EXT02:9210" for s in got["sources"])
    assert any("성적 50%" in s["text"] for s in got["sources"])
    assert [s["id"] for s in got["sources"]] == ["S{}".format(i) for i in range(1, len(got["sources"]) + 1)]
    both = r.sources_for("대전 주민등록", ["EXT02:9210", "EXT02:9211"], {}, "")
    assert "EXT02:9211" in both["chosen"]
    assert r.sources_for("아무거나", ["EXT02:404"], {}, "")["sources"] == []                    # 색인에 없는 공고는 빈 결과


# ================================================================== 챗봇 연결
def _chat(retriever):
    pytest.importorskip("fastapi")
    from tests.test_chat import FakeAI, Src as ChatSrc, good, make, ask, ID, FILE
    return FakeAI, ChatSrc, good, make, ask, ID


def test_chat_uses_rag_sources_without_reading_notice_text():
    FakeAI, ChatSrc, good, make, ask, ID = _chat(None)
    emb, store = FakeEmbedder(), MemoryStore()
    store.upsert([{"id": ID + "#s", "values": emb._vec("요약"), "metadata": {"notice_id": ID, "kind": "summary", "label": "구조화 요약", "text": "공고: 대산장학생 모집기간: 2026-10-01 ~ 2026-11-11"}},
                  {"id": ID + "#b0", "values": emb._vec("제출서류 지원서 성적증명서 자기소개서"), "metadata": {"notice_id": ID, "kind": "body", "label": "본문", "text": "제출서류: 지원서 1부, 성적증명서 1부, 자기소개서 1부"}}])
    ai = FakeAI(good(quote="제출서류: 지원서 1부, 성적증명서 1부, 자기소개서 1부").replace("S1", "S2"))
    c, app, src = make(ai, api_enable_demo=True)
    app.state.chat.retriever = Retriever(emb, store, top_k=3)
    j = ask(c).json()
    assert j["outcome"] == "answered" and j["diag"]["rag"] is True and src.text_calls == 0
    assert "구조화 요약" in ai.last_user and "제출서류: 지원서 1부" in ai.last_user


def test_chat_falls_back_to_direct_selection_when_rag_fails_or_is_empty():
    FakeAI, ChatSrc, good, make, ask, ID = _chat(None)
    for retr in (Retriever(FakeEmbedder(), MemoryStore()), None):                            # 색인이 비어 있음 / 아예 없음
        ai = FakeAI(good())
        c, app, src = make(ai, api_enable_demo=True)
        app.state.chat.retriever = retr
        j = ask(c).json()
        assert j["outcome"] == "answered" and j["diag"]["rag"] is False and src.text_calls == 1
    bad = FakeEmbedder()
    bad.fail = True
    ai = FakeAI(good())
    c, app, src = make(ai, api_enable_demo=True)
    app.state.chat.retriever = Retriever(bad, MemoryStore())                                  # 임베딩 호출이 실패해도 예전 방식으로 답한다
    j = ask(c).json()
    assert j["outcome"] == "answered" and j["diag"]["rag"] is False


def test_rag_is_off_by_default_and_needs_every_key():
    from scholarship.chat.service import ChatService
    assert ChatService._make_retriever(Settings()) is None
    s = Settings()
    s.rag_enabled = True
    assert ChatService._make_retriever(s) is None                                             # 키·주소가 없으면 켜지지 않는다
    s.openai_api_key, s.pinecone_api_key, s.pinecone_host = "a", "b", "h.svc.pinecone.io"
    assert isinstance(ChatService._make_retriever(s), Retriever)


def test_embedding_endpoint_prefers_openai_key_then_the_gateway_with_the_ai_key():
    s = Settings()
    s.ai_api_key, s.ai_base_url = "gw-key", "https://factchat-cloud.mindlogic.ai/v1/gateway/claude"
    assert s.embedding_endpoint() == ("gw-key", "https://factchat-cloud.mindlogic.ai/v1/gateway")             # Claude 방식 주소는 OpenAI 호환 주소로 바꾼다
    s.ai_base_url = "https://factchat-cloud.mindlogic.ai/v1/gateway"
    assert s.embedding_endpoint() == ("gw-key", "https://factchat-cloud.mindlogic.ai/v1/gateway")
    s.openai_api_key = "sk-own"
    assert s.embedding_endpoint() == ("sk-own", "https://api.openai.com/v1")
    s.rag_base_url = "https://x.example/v1/"
    assert s.embedding_endpoint() == ("sk-own", "https://x.example/v1")
    assert Settings().embedding_endpoint() == ("", "")


def test_embedder_retries_with_trailing_slash_like_the_gateway_docs(monkeypatch):
    urls = []

    def post(url, headers=None, json=None, timeout=None, allow_redirects=None):
        urls.append(url)
        return Resp(308) if not url.endswith("/") else Resp(200, {"data": [{"index": 0, "embedding": [1.0]}]})
    monkeypatch.setattr("scholarship.rag.embed.requests.post", post)
    e = OpenAIEmbedder("k", base_url="https://gw.example/v1/gateway", sleep=lambda s: None)
    assert e.embed(["가"]) == [[1.0]] and urls == ["https://gw.example/v1/gateway/embeddings", "https://gw.example/v1/gateway/embeddings/"]


def test_new_secret_keys_are_allowed():
    from scholarship.common import secret_store as ss
    assert "OPENAI_API_KEY" in ss.ALLOWED_KEYS and "PINECONE_API_KEY" in ss.ALLOWED_KEYS


def test_retriever_passes_the_attachment_file_seq_to_the_sources():
    emb, store = indexed()
    r = Retriever(emb, store, top_k=6)
    got = r.sources_for("신청 서류 첨부 안내", ["EXT02:9210"], {}, "EXT02:9210")
    att = [s for s in got["sources"] if s["label"].startswith("첨부")]
    assert att and all(s.get("file_seq") == "1" for s in att)
    assert all("file_seq" not in s for s in got["sources"] if not s["label"].startswith("첨부"))
