# -*- coding: utf-8 -*-
"""벡터 저장소: Pinecone(REST) 과 시험용 메모리 저장소. 색인(인덱스)은 Pinecone 콘솔에서 미리 만들어 두고 그 주소(host)만 쓴다."""
from __future__ import annotations

import math
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence

import requests

API_VERSION = "2025-01"
UPSERT_BATCH = 100
DELETE_BATCH = 500
RETRY_STATUS = (429, 500, 502, 503, 504)


class StoreError(Exception):
    pass


class MemoryStore:
    """같은 인터페이스의 메모리 구현 (테스트·드라이런)."""

    def __init__(self):
        self.v: Dict[str, Dict[str, Any]] = {}

    def upsert(self, items: Sequence[Dict[str, Any]]) -> None:
        for it in items:
            self.v[it["id"]] = {"values": list(it["values"]), "metadata": dict(it["metadata"])}

    def query(self, vector: Sequence[float], top_k: int, notice_ids: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        def cos(a, b):
            na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
            return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0
        rows = [{"id": i, "score": cos(vector, d["values"]), "metadata": d["metadata"]} for i, d in self.v.items()
                if notice_ids is None or d["metadata"].get("notice_id") in set(notice_ids)]
        return sorted(rows, key=lambda r: (-r["score"], r["id"]))[:top_k]

    def fetch(self, ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        return {i: {"metadata": self.v[i]["metadata"]} for i in ids if i in self.v}

    def list_ids(self, prefix: str = "") -> List[str]:
        return sorted(i for i in self.v if i.startswith(prefix))

    def delete(self, ids: Sequence[str]) -> None:
        for i in ids:
            self.v.pop(i, None)


class PineconeStore:
    def __init__(self, api_key: str, host: str, namespace: str = "scholarship", timeout: float = 30.0, retries: int = 3, sleep=time.sleep):
        if not api_key or not host:
            raise StoreError("PINECONE_API_KEY 와 PINECONE_HOST 가 필요합니다")
        self.key, self.ns, self.timeout, self.retries, self._sleep = api_key, namespace, timeout, retries, sleep
        host = host.strip()
        self.base = host.rstrip("/") if host.startswith("http") else "https://" + host.rstrip("/")

    def _req(self, method: str, path: str, body: Optional[Dict[str, Any]] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        headers = {"Api-Key": self.key, "Content-Type": "application/json", "X-Pinecone-API-Version": API_VERSION}
        last = ""
        for attempt in range(1, self.retries + 1):
            try:
                r = requests.request(method, self.base + path, headers=headers, json=body, params=params, timeout=self.timeout, allow_redirects=False)
            except requests.RequestException as e:
                last = "요청 실패 ({})".format(type(e).__name__)
                self._sleep(min(2 ** attempt, 10))
                continue
            if r.status_code == 200:
                try:
                    return r.json() if r.text.strip() else {}
                except ValueError:
                    raise StoreError("Pinecone 응답을 읽지 못했습니다")
            last = "HTTP {}".format(r.status_code)
            if r.status_code not in RETRY_STATUS:
                raise StoreError("Pinecone 호출 실패: {} ({} {})".format(last, method, path))
            self._sleep(min(2 ** attempt, 10))
        raise StoreError("Pinecone 호출 실패: {}".format(last))

    def upsert(self, items: Sequence[Dict[str, Any]]) -> None:
        for i in range(0, len(items), UPSERT_BATCH):
            self._req("POST", "/vectors/upsert", {"vectors": list(items[i:i + UPSERT_BATCH]), "namespace": self.ns})

    def query(self, vector: Sequence[float], top_k: int, notice_ids: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        body: Dict[str, Any] = {"vector": list(vector), "topK": top_k, "includeMetadata": True, "namespace": self.ns}
        if notice_ids is not None:
            body["filter"] = {"notice_id": {"$in": list(notice_ids)}}
        d = self._req("POST", "/query", body)
        return [{"id": m["id"], "score": m.get("score", 0.0), "metadata": m.get("metadata") or {}} for m in d.get("matches") or []]

    def fetch(self, ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for i in range(0, len(ids), 100):
            d = self._req("GET", "/vectors/fetch", params={"ids": list(ids[i:i + 100]), "namespace": self.ns})
            for k, v in (d.get("vectors") or {}).items():
                out[k] = {"metadata": v.get("metadata") or {}}
        return out

    def list_ids(self, prefix: str = "") -> List[str]:
        out: List[str] = []
        token = None
        while True:
            params: Dict[str, Any] = {"namespace": self.ns, "limit": 100}
            if prefix:
                params["prefix"] = prefix
            if token:
                params["paginationToken"] = token
            d = self._req("GET", "/vectors/list", params=params)
            out += [v["id"] for v in d.get("vectors") or []]
            token = (d.get("pagination") or {}).get("next")
            if not token:
                return out

    def delete(self, ids: Sequence[str]) -> None:
        for i in range(0, len(ids), DELETE_BATCH):
            self._req("POST", "/vectors/delete", {"ids": list(ids[i:i + DELETE_BATCH]), "namespace": self.ns})
