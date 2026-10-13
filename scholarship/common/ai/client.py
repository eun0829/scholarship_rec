# -*- coding: utf-8 -*-
"""[COMMON] AI 호출 클라이언트. provider 별 구현을 같은 인터페이스로 맞춘다. (requests 만 사용, SDK 불필요)

  AI_PROVIDER=anthropic  : Anthropic Messages API.  AI_BASE_URL 비우면 https://api.anthropic.com (직접 호출)
                           게이트웨이(권장): AI_BASE_URL=https://factchat-cloud.mindlogic.ai/v1/gateway/claude
                           -> POST {AI_BASE_URL}/v1/messages   (x-api-key). 요청이 변환 없이 전달되어 이미지/PDF/캐싱 모두 가능
  AI_PROVIDER=gateway    : OpenAI 호환 Chat Completions (게이트웨이: AI_BASE_URL=https://factchat-cloud.mindlogic.ai/v1/gateway)
                           -> POST {AI_BASE_URL}/chat/completions  (Authorization: Bearer)
  AI_PROVIDER=openai     : OpenAI 직접 호출 (AI_BASE_URL 비우면 https://api.openai.com/v1)

게이트웨이 문서 기준 주의점: 속도 제한(회원당 60초 120건, 429 + Retry-After), 요청 본문 25MB(413), 크레딧 소진은 402,
User-Agent 가 비어 있으면 Cloudflare 가 403(error code: 1010) 으로 막는다.
"""
from __future__ import annotations

import base64
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

import requests

from ..extractors.results import ImagePart

log = logging.getLogger(__name__)

UA = "TUKScholarshipBot/1.0 (internal)"
ANTHROPIC_OFFICIAL = "https://api.anthropic.com"
OPENAI_DEFAULT_BASE = "https://api.openai.com/v1"
COMPAT_PROVIDERS = ("gateway", "openai", "openai_compatible", "openai-compatible", "factchat")
RETRY_STATUS = (429, 500, 502, 503, 504, 529)
SWITCH_URL_STATUS = (301, 302, 307, 308, 404, 405)        # 경로 끝 '/' 유무가 달라서 생기는 오류로 보고 다른 형태를 한 번 시도


class AIError(Exception):
    pass


@dataclass
class AIResponse:
    text: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""          # end_turn / max_tokens / stop / length ...
    truncated: bool = False        # 출력 한도(max_tokens)에서 잘렸는지


class AIClient(ABC):
    max_tokens: int = 4000

    @abstractmethod
    def complete(self, system: str, user_text: str, images: List[ImagePart], max_tokens: Optional[int] = None) -> AIResponse:
        ...


def _wait_seconds(resp: Optional[requests.Response], attempt: int, base_sleep: float) -> float:
    """429 의 Retry-After(초)를 존중한다 (최대 60초). 없으면 점진적 대기."""
    if resp is not None:
        ra = resp.headers.get("Retry-After", "")
        if ra.strip().isdigit():
            return min(max(float(ra), 0.0), 60.0)
    return base_sleep * attempt


class AnthropicClient(AIClient):
    VERSION = "2023-06-01"

    def __init__(self, api_key: str, model: str, timeout: int = 180, max_tokens: int = 4000, temperature=0,
                 base_url: str = "", retry_sleep: float = 3.0, beta: str = ""):
        if not api_key:
            raise AIError("AI_API_KEY 가 설정되지 않음")
        self.api_key, self.model = api_key, model
        self.timeout, self.max_tokens, self.temperature, self.retry_sleep = timeout, max_tokens, temperature, retry_sleep
        self.base_url = (base_url or ANTHROPIC_OFFICIAL).rstrip("/")
        self.official = self.base_url == ANTHROPIC_OFFICIAL
        self.beta = beta

    @property
    def urls(self) -> List[str]:
        u = self.base_url + "/v1/messages"
        return [u] if self.official else [u, u + "/"]       # 게이트웨이 문서의 경로는 '/v1/messages/' (SDK 는 끝 '/' 없이 호출)

    def _body(self, system, user_text, images, with_temperature=True, with_cache=True, max_tokens=None):
        content = []
        for im in images:                                  # 이미지는 텍스트보다 앞에 둔다
            content.append({"type": "text", "text": "[이미지: {}]".format(im.label)})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": im.media_type,
                "data": base64.b64encode(im.data).decode("ascii")}})
        content.append({"type": "text", "text": user_text})
        sys_block = {"type": "text", "text": system}
        if with_cache:
            sys_block["cache_control"] = {"type": "ephemeral"}
        body = {"model": self.model, "max_tokens": max_tokens or self.max_tokens, "system": [sys_block],
                "messages": [{"role": "user", "content": content}]}
        if with_temperature and self.temperature is not None:
            body["temperature"] = self.temperature
        return body

    def complete(self, system: str, user_text: str, images: List[ImagePart], max_tokens: Optional[int] = None) -> AIResponse:
        headers = {"x-api-key": self.api_key, "anthropic-version": self.VERSION,
                   "content-type": "application/json", "User-Agent": UA}
        if self.beta:
            headers["anthropic-beta"] = self.beta
        with_temp, with_cache, url_i = True, True, 0
        last = ""
        attempt = 0
        while attempt < 4:
            attempt += 1
            resp = None
            try:
                resp = requests.post(self.urls[url_i], headers=headers, timeout=self.timeout, allow_redirects=False,
                                     json=self._body(system, user_text, images, with_temp, with_cache, max_tokens))
            except requests.RequestException as e:
                last = "요청 실패: {}".format(e)
                time.sleep(_wait_seconds(None, attempt, self.retry_sleep))
                continue
            if resp.status_code == 200:
                data = resp.json()
                text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
                u = data.get("usage", {}) or {}
                stop = data.get("stop_reason") or ""
                return AIResponse(text=text, model=data.get("model", self.model),
                                  input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0),
                                  stop_reason=stop, truncated=(stop == "max_tokens"))
            last = "HTTP {}: {}".format(resp.status_code, resp.text[:300])
            low = resp.text.lower()
            if resp.status_code in SWITCH_URL_STATUS and url_i + 1 < len(self.urls):
                url_i += 1
                attempt -= 1                                # 경로 형태만 바꾸는 시도는 재시도 횟수에 넣지 않음
                continue
            if resp.status_code == 400 and with_temp and "temperature" in low:
                with_temp = False
                continue
            if resp.status_code == 400 and with_cache and "cache_control" in low:
                with_cache = False
                continue
            if resp.status_code in RETRY_STATUS:
                time.sleep(_wait_seconds(resp, attempt, self.retry_sleep))
                continue
            break
        raise AIError(last or "AI 호출 실패")


class OpenAICompatClient(AIClient):
    """OpenAI 호환 Chat Completions (게이트웨이 포함). 이미지는 image_url(data URL), JSON 모드는 쓰지 않는다."""

    def __init__(self, api_key: str, model: str, base_url: str, timeout: int = 180, max_tokens: int = 4000,
                 temperature=0, retry_sleep: float = 3.0):
        if not api_key:
            raise AIError("AI_API_KEY 가 설정되지 않음")
        if not base_url:
            raise AIError("AI_BASE_URL 이 설정되지 않음")
        self.api_key, self.model = api_key, model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.urls = [self.url, self.url + "/"]               # 게이트웨이 문서의 경로는 '/chat/completions/'
        self.timeout, self.max_tokens, self.temperature, self.retry_sleep = timeout, max_tokens, temperature, retry_sleep

    def _body(self, system, user_text, images, with_temperature=True, max_tokens=None):
        if images:
            content = []
            for im in images:
                content.append({"type": "text", "text": "[이미지: {}]".format(im.label)})
                content.append({"type": "image_url", "image_url": {
                    "url": "data:{};base64,{}".format(im.media_type, base64.b64encode(im.data).decode("ascii"))}})
            content.append({"type": "text", "text": user_text})
        else:
            content = user_text
        body = {"model": self.model, "max_tokens": max_tokens or self.max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
        if with_temperature and self.temperature is not None:
            body["temperature"] = self.temperature
        return body

    @staticmethod
    def _parse(data):
        """(본문 텍스트, finish_reason)"""
        if not isinstance(data, dict) or not data.get("choices"):
            err = data.get("error") if isinstance(data, dict) else None
            raise AIError("응답에 choices 가 없음: {}".format(str(err or data)[:300]))
        choice = data["choices"][0]
        content = (choice.get("message") or {}).get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return content or "", choice.get("finish_reason") or ""

    def complete(self, system: str, user_text: str, images: List[ImagePart], max_tokens: Optional[int] = None) -> AIResponse:
        headers = {"Authorization": "Bearer {}".format(self.api_key), "Content-Type": "application/json", "User-Agent": UA}
        with_temp, url_i, attempt = True, 0, 0
        last = ""
        while attempt < 4:
            attempt += 1
            resp = None
            try:
                resp = requests.post(self.urls[url_i], headers=headers, timeout=self.timeout, allow_redirects=False,
                                     json=self._body(system, user_text, images, with_temp, max_tokens))
            except requests.RequestException as e:
                last = "요청 실패: {}".format(e)
                time.sleep(_wait_seconds(None, attempt, self.retry_sleep))
                continue
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError:
                    raise AIError("응답이 JSON 이 아님: {}".format(resp.text[:200]))
                text, finish = self._parse(data)
                u = data.get("usage") or {}
                return AIResponse(text=text, model=data.get("model") or self.model,
                                  input_tokens=u.get("prompt_tokens", 0), output_tokens=u.get("completion_tokens", 0),
                                  stop_reason=finish, truncated=(finish == "length"))
            last = "HTTP {}: {}".format(resp.status_code, resp.text[:300])
            if resp.status_code in SWITCH_URL_STATUS and url_i + 1 < len(self.urls):
                url_i += 1
                attempt -= 1
                continue
            if resp.status_code == 400 and with_temp and "temperature" in resp.text.lower():
                with_temp = False
                continue
            if resp.status_code in RETRY_STATUS:
                time.sleep(_wait_seconds(resp, attempt, self.retry_sleep))
                continue
            break
        raise AIError(last or "AI 호출 실패")


def validate_base_url(provider: str, base_url: str) -> None:
    """게이트웨이에서 흔한 설정 실수를 호출 전에 알려준다."""
    b = (base_url or "").rstrip("/")
    if provider == "anthropic" and b.endswith("/v1/gateway"):
        raise AIError("AI_PROVIDER=anthropic 의 게이트웨이 주소는 '/claude' 로 끝나야 합니다: {}/claude".format(b))
    if provider in COMPAT_PROVIDERS and b.endswith("/claude"):
        raise AIError("AI_PROVIDER={} 는 OpenAI 호환 주소(.../v1/gateway)를 씁니다. '/claude' 주소는 AI_PROVIDER=anthropic 용입니다".format(provider))


def make_client(settings, max_tokens=None, timeout=None) -> AIClient:
    prov = (settings.ai_provider or "").lower()
    mt = max_tokens or settings.ai_max_tokens
    to = timeout or settings.ai_timeout
    if prov == "anthropic":
        validate_base_url(prov, settings.ai_base_url)
        return AnthropicClient(settings.ai_api_key, settings.ai_model, to, mt, base_url=settings.ai_base_url,
                               beta=getattr(settings, "ai_anthropic_beta", ""))
    if prov in COMPAT_PROVIDERS:
        validate_base_url(prov, settings.ai_base_url)
        base = settings.ai_base_url or (OPENAI_DEFAULT_BASE if prov == "openai" else "")
        return OpenAICompatClient(settings.ai_api_key, settings.ai_model, base, to, mt)
    raise ValueError("알 수 없는 AI_PROVIDER: {!r} (사용 가능: anthropic, gateway, openai)".format(settings.ai_provider))
