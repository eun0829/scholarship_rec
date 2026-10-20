# -*- coding: utf-8 -*-
"""OpenAI 임베딩 호출 (REST, 추가 패키지 없음). 키는 로그·오류 메시지에 남기지 않는다."""
from __future__ import annotations

import time
from typing import List, Optional, Sequence

import requests

MAX_INPUT_CHARS = 8000
BATCH = 64
RETRY_STATUS = (429, 500, 502, 503, 504)
SWITCH_URL_STATUS = (301, 302, 307, 308, 404, 405)


class EmbedError(Exception):
    pass


class OpenAIEmbedder:
    def __init__(self, api_key: str, model: str = "text-embedding-3-large", dims: int = 1024, base_url: str = "https://api.openai.com/v1",
                 timeout: float = 30.0, retries: int = 3, sleep=time.sleep):
        if not api_key:
            raise EmbedError("OPENAI_API_KEY 가 없습니다 (python set_secret.py OPENAI_API_KEY)")
        self.key, self.model, self.dims, self.timeout, self.retries, self._sleep = api_key, model, dims, timeout, retries, sleep
        self.url = base_url.rstrip("/") + "/embeddings"
        self.urls = [self.url, self.url + "/"]                       # 게이트웨이 문서의 경로는 '/embeddings/' (끝의 '/' 유무가 다르면 다른 형태를 한 번 시도)
        self.tokens_used = 0

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        out: List[List[float]] = []
        for i in range(0, len(texts), BATCH):
            out += self._call([t[:MAX_INPUT_CHARS] or " " for t in texts[i:i + BATCH]])
        return out

    def embed_one(self, text: str) -> List[float]:
        return self.embed([text])[0]

    def _call(self, batch: List[str]) -> List[List[float]]:
        body = {"model": self.model, "input": batch, "encoding_format": "float"}
        if self.dims:
            body["dimensions"] = self.dims
        last = ""
        url_i = 0
        attempt = 0
        while attempt < self.retries + 1:
            attempt += 1
            try:
                r = requests.post(self.urls[url_i], headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"}, json=body,
                                  timeout=self.timeout, allow_redirects=False)
            except requests.RequestException as e:
                last = "요청 실패 ({})".format(type(e).__name__)
                self._sleep(min(2 ** attempt, 10))
                continue
            if r.status_code == 200:
                try:
                    data = r.json()
                    rows = sorted(data["data"], key=lambda d: d["index"])
                    self.tokens_used += int((data.get("usage") or {}).get("total_tokens") or 0)
                    return [list(map(float, d["embedding"])) for d in rows]
                except (ValueError, KeyError, TypeError):
                    raise EmbedError("임베딩 응답을 읽지 못했습니다")
            last = "HTTP {}".format(r.status_code)
            if r.status_code in SWITCH_URL_STATUS and url_i + 1 < len(self.urls):
                url_i += 1
                attempt -= 1
                continue
            if r.status_code not in RETRY_STATUS:
                raise EmbedError("임베딩 호출 실패: {} (키·모델·차원·주소 설정을 확인하세요)".format(last))
            self._sleep(min(2 ** attempt, 10))
        raise EmbedError("임베딩 호출 실패: {}".format(last))
