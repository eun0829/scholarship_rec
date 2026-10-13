# -*- coding: utf-8 -*-
"""OpenAI 호환 게이트웨이 클라이언트 검증 (로컬 가짜 HTTP 서버로 요청/응답 형식 확인).
※ 실제 게이트웨이(factchat-cloud.mindlogic.ai)와의 호환은 이 테스트로 보장되지 않는다 -> python set_secret.py --test-ai 로 확인."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scholarship.common.ai.client import AIError, OpenAICompatClient, make_client
from scholarship.common.ai.prompt import Section, build_payload
from scholarship.common.ai.runner import run_extraction
from scholarship.common.config import Settings
from scholarship.common.extractors.results import ImagePart
from datetime import date


class FakeGateway:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []
        outer = self

        class H(BaseHTTPRequestHandler):
            def _reply(self, body_json):
                outer.requests.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                       "headers": {k.lower(): v for k, v in self.headers.items()}, "json": body_json,
                                       "method": self.command})
                resp = outer.responses.pop(0) if len(outer.responses) > 1 else outer.responses[0]
                status, payload = resp[0], resp[1]
                extra = resp[2] if len(resp) > 2 else {}
                data = (payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in extra.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self._reply(json.loads(body.decode("utf-8")))

            def do_GET(self):
                self._reply(None)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = "http://127.0.0.1:{}/v1/gateway".format(self.server.server_port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def ok(content, **extra):
    return 200, dict({"model": "claude-sonnet-5", "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": content}}],
                     "usage": {"prompt_tokens": 123, "completion_tokens": 45}}, **extra)


@pytest.fixture
def gateway():
    made = []

    def make(*responses):
        g = FakeGateway(*responses)
        made.append(g)
        return g
    yield make
    for g in made:
        g.close()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """재시도 대기를 기록만 하고 실제로는 기다리지 않는다."""
    import scholarship.common.ai.client as c
    slept = []
    monkeypatch.setattr(c.time, "sleep", lambda s: slept.append(s))
    return slept


def client(g, **kw):
    return OpenAICompatClient("sk-TESTKEY", "claude-sonnet-5", g.base, timeout=10, retry_sleep=0, **kw)


def test_request_format_text_only(gateway):
    g = gateway(ok("pong"))
    r = client(g).complete("SYSTEM PROMPT", "사용자 입력", [])
    req = g.requests[0]
    assert req["path"] == "/v1/gateway/chat/completions"                   # base_url + /chat/completions (OpenAI SDK 와 동일)
    assert req["auth"] == "Bearer sk-TESTKEY"
    body = req["json"]
    assert body["model"] == "claude-sonnet-5" and body["max_tokens"] == 4000 and body["temperature"] == 0
    assert body["messages"] == [{"role": "system", "content": "SYSTEM PROMPT"}, {"role": "user", "content": "사용자 입력"}]
    assert "response_format" not in body                                    # JSON 모드는 쓰지 않음 (호환성)
    assert (r.text, r.model, r.input_tokens, r.output_tokens) == ("pong", "claude-sonnet-5", 123, 45)


def test_request_format_with_images(gateway):
    g = gateway(ok("x"))
    img = ImagePart("image/png", b"\x89PNG-bytes", "포스터 (분할 1/2)")
    client(g).complete("S", "본문", [img])
    content = g.requests[0]["json"]["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "[이미지: 포스터 (분할 1/2)]"}
    assert content[1]["type"] == "image_url" and content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[-1] == {"type": "text", "text": "본문"}


def test_response_content_as_parts_and_error_body(gateway):
    g = gateway(ok([{"type": "text", "text": "{\"a\":"}, {"type": "text", "text": " 1}"}]))
    assert client(g).complete("S", "U", []).text == '{"a": 1}'
    g2 = gateway((200, {"error": {"message": "model not found"}}))
    with pytest.raises(AIError, match="choices"):
        client(g2).complete("S", "U", [])


def test_retry_on_server_errors_then_success(gateway):
    g = gateway((503, {"error": "busy"}), (429, {"error": "slow down"}), ok("done"))
    assert client(g).complete("S", "U", []).text == "done" and len(g.requests) == 3


def test_gives_up_with_status_in_message(gateway):
    g = gateway((401, {"error": {"message": "invalid api key"}}))
    with pytest.raises(AIError, match="HTTP 401"):
        client(g).complete("S", "U", [])
    assert len(g.requests) == 1                                             # 401 은 재시도하지 않음
    g2 = gateway((500, {"error": "x"}))
    with pytest.raises(AIError, match="HTTP 500"):
        client(g2).complete("S", "U", [])
    assert len(g2.requests) == 4                                            # 5xx 는 4회 시도


def test_temperature_rejected_is_retried_without_it(gateway):
    g = gateway((400, {"error": {"message": "temperature is not supported for this model"}}), ok("ok"))
    assert client(g).complete("S", "U", []).text == "ok"
    assert "temperature" in g.requests[0]["json"] and "temperature" not in g.requests[1]["json"]


def test_connection_error_message():
    c = OpenAICompatClient("k", "m", "http://127.0.0.1:9/v1", timeout=2, retry_sleep=0)
    with pytest.raises(AIError, match="요청 실패"):
        c.complete("S", "U", [])


def test_make_client_providers():
    s = Settings()
    s.ai_api_key, s.ai_model = "k", "m"
    s.ai_provider, s.ai_base_url = "gateway", "https://gw.example/v1/gateway/"
    c = make_client(s)
    assert isinstance(c, OpenAICompatClient) and c.url == "https://gw.example/v1/gateway/chat/completions"
    s.ai_provider, s.ai_base_url = "openai", ""
    assert make_client(s).url == "https://api.openai.com/v1/chat/completions"           # 직접 OpenAI 는 기본 주소
    s.ai_provider = "gateway"
    with pytest.raises(AIError, match="AI_BASE_URL"):
        make_client(s)
    s.ai_provider = "anthropic"
    assert type(make_client(s)).__name__ == "AnthropicClient"
    s.ai_provider = "nonsense"
    with pytest.raises(ValueError, match="AI_PROVIDER"):
        make_client(s)
    s.ai_provider, s.ai_api_key = "gateway", ""
    with pytest.raises(AIError, match="AI_API_KEY"):
        make_client(s)


def test_end_to_end_extraction_through_gateway(gateway):
    good = {"category": "OUT", "apply_type": "SCHOOL", "apply_end": "2026-10-25", "target_levels": ["UNIV"],
            "gpa": {"min": 80, "scale": 100, "basis": "EITHER"}, "summary": "요약"}
    g = gateway(ok("```json\n" + json.dumps(good, ensure_ascii=False) + "\n```"))
    payload = build_payload(date(2026, 10, 6), "제목", date(2026, 9, 4), "팀", "본문", [Section("첨부1", "요강", "내용")],
                            [], [], 20000, 60000)
    res = run_extraction(client(g), payload, date(2026, 9, 4))
    assert res.ok and res.data["apply_end"] == "2026-10-25" and res.data["gpa"]["scale"] == 100.0
    assert (res.input_tokens, res.output_tokens) == (123, 45)
    sent = g.requests[0]["json"]["messages"]
    assert sent[0]["role"] == "system" and "JSON" in sent[0]["content"] and "[본문]" in sent[1]["content"]


# ================================================================== Anthropic 네이티브 (게이트웨이 /claude)
def anth_ok(text="pong"):
    return 200, {"id": "msg_1", "type": "message", "model": "claude-sonnet-5", "role": "assistant",
                 "content": [{"type": "text", "text": text}], "usage": {"input_tokens": 77, "output_tokens": 9}}


def anth(g, **kw):
    from scholarship.common.ai.client import AnthropicClient
    return AnthropicClient("sk-GWKEY", "claude-sonnet-5", timeout=10, max_tokens=4000, base_url=g.base + "/claude",
                           retry_sleep=0, **kw)


def test_anthropic_gateway_request_format(gateway):
    g = gateway(anth_ok("{}"))
    img = ImagePart("image/jpeg", b"\xff\xd8jpeg", "포스터")
    r = anth(g).complete("SYSTEM", "본문", [img])
    req = g.requests[0]
    assert req["path"] == "/v1/gateway/claude/v1/messages"                       # SDK 와 같은 경로 (끝 '/' 없음)
    h = req["headers"]
    assert h["x-api-key"] == "sk-GWKEY" and h["anthropic-version"] == "2023-06-01"
    assert h["user-agent"].startswith("TUKScholarshipBot") and "authorization" not in h   # 비어 있는 UA 는 Cloudflare 가 403(1010)
    b = req["json"]
    assert b["model"] == "claude-sonnet-5" and b["max_tokens"] == 4000 and b["temperature"] == 0
    assert b["system"] == [{"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}]
    c = b["messages"][0]["content"]
    assert c[0] == {"type": "text", "text": "[이미지: 포스터]"}
    assert c[1]["type"] == "image" and c[1]["source"]["media_type"] == "image/jpeg" and c[1]["source"]["type"] == "base64"
    assert c[-1] == {"type": "text", "text": "본문"}
    assert (r.text, r.model, r.input_tokens, r.output_tokens) == ("{}", "claude-sonnet-5", 77, 9)


def test_anthropic_beta_header_only_when_configured(gateway):
    g = gateway(anth_ok())
    anth(g).complete("S", "U", [])
    assert "anthropic-beta" not in g.requests[0]["headers"]
    g2 = gateway(anth_ok())
    anth(g2, beta="prompt-caching-2024-07-31").complete("S", "U", [])
    assert g2.requests[0]["headers"]["anthropic-beta"] == "prompt-caching-2024-07-31"


def test_anthropic_falls_back_to_trailing_slash_path(gateway):
    g = gateway((404, {"detail": "Not found"}), anth_ok("ok"))
    assert anth(g).complete("S", "U", []).text == "ok"
    assert [r["path"] for r in g.requests] == ["/v1/gateway/claude/v1/messages", "/v1/gateway/claude/v1/messages/"]


def test_anthropic_402_not_retried_and_message_kept(gateway):
    g = gateway((402, {"error": {"message": "Credit balance exhausted"}}))
    with pytest.raises(AIError, match="HTTP 402.*Credit balance exhausted"):
        anth(g).complete("S", "U", [])
    assert len(g.requests) == 1                                   # 402 는 재시도하지 않음


def test_anthropic_429_honors_retry_after(gateway, no_sleep):
    g = gateway((429, {"error": "slow"}, {"Retry-After": "7"}), (429, {"error": "slow"}, {"Retry-After": "999"}), anth_ok("ok"))
    assert anth(g).complete("S", "U", []).text == "ok"
    assert no_sleep == [7.0, 60.0]                                # Retry-After 존중, 최대 60초로 제한


def test_anthropic_cache_control_and_temperature_fallbacks(gateway):
    g = gateway((400, {"error": {"message": "system.0.cache_control: Extra inputs are not permitted"}}),
                (400, {"error": {"message": "temperature is deprecated for this model"}}), anth_ok("ok"))
    assert anth(g).complete("S", "U", []).text == "ok"
    b0, b1, b2 = (r["json"] for r in g.requests)
    assert "cache_control" in b0["system"][0] and "cache_control" not in b1["system"][0]
    assert "temperature" in b1 and "temperature" not in b2


def test_anthropic_official_base_url_has_no_slash_fallback():
    from scholarship.common.ai.client import AnthropicClient
    c = AnthropicClient("k", "m")
    assert c.urls == ["https://api.anthropic.com/v1/messages"]
    c2 = AnthropicClient("k", "m", base_url="https://gw.example/v1/gateway/claude/")
    assert c2.urls == ["https://gw.example/v1/gateway/claude/v1/messages", "https://gw.example/v1/gateway/claude/v1/messages/"]


def test_openai_compat_falls_back_to_trailing_slash_and_sends_ua(gateway):
    g = gateway((404, {"detail": "x"}), ok("ok"))
    assert client(g).complete("S", "U", []).text == "ok"
    assert [r["path"] for r in g.requests] == ["/v1/gateway/chat/completions", "/v1/gateway/chat/completions/"]
    assert g.requests[0]["headers"]["user-agent"].startswith("TUKScholarshipBot")


def test_base_url_mistakes_are_caught_early():
    from scholarship.common.ai.client import validate_base_url
    with pytest.raises(AIError, match="/claude"):
        validate_base_url("anthropic", "https://factchat-cloud.mindlogic.ai/v1/gateway")
    with pytest.raises(AIError, match="AI_PROVIDER=anthropic"):
        validate_base_url("gateway", "https://factchat-cloud.mindlogic.ai/v1/gateway/claude")
    validate_base_url("anthropic", "https://factchat-cloud.mindlogic.ai/v1/gateway/claude")
    validate_base_url("gateway", "https://factchat-cloud.mindlogic.ai/v1/gateway")
    validate_base_url("anthropic", "")


def test_make_client_anthropic_with_gateway_base():
    s = Settings()
    s.ai_api_key, s.ai_model, s.ai_provider = "k", "claude-sonnet-5", "anthropic"
    s.ai_base_url = "https://factchat-cloud.mindlogic.ai/v1/gateway/claude"
    s.ai_anthropic_beta = "prompt-caching-2024-07-31"
    c = make_client(s)
    assert type(c).__name__ == "AnthropicClient" and c.urls[0].endswith("/v1/gateway/claude/v1/messages") and c.beta
    s.ai_base_url = "https://factchat-cloud.mindlogic.ai/v1/gateway"
    with pytest.raises(AIError, match="/claude"):
        make_client(s)


# ================================================================== 크레딧 / 모델 목록 조회
def test_gateway_helpers(gateway):
    from scholarship.common.ai import gateway as gw
    assert gw.gateway_root("https://x/v1/gateway/claude/") == "https://x/v1/gateway"
    assert gw.gateway_root("https://x/v1/gateway") == "https://x/v1/gateway"
    assert gw.is_gateway("https://x/v1/gateway/claude") and not gw.is_gateway("") and not gw.is_gateway("https://api.anthropic.com")
    credits = {"object": "credit_balance", "monthly_allocated": {"quota": 1000.0, "used": 120.5, "remaining": 879.5,
               "renewal_date": "2026-11-01T00:00:00Z"}, "purchased": {"total": 500.0, "used": 0.0, "remaining": 500.0},
               "total": {"quota": 1500.0, "used": 120.5, "remaining": 1379.5}}
    g = gateway((200, credits))
    got = gw.fetch_credits(g.base + "/claude", "sk-K")
    assert got["total"]["remaining"] == 1379.5
    assert g.requests[0]["path"] == "/v1/gateway/credits/" and g.requests[0]["method"] == "GET"
    assert g.requests[0]["auth"] == "Bearer sk-K" and g.requests[0]["headers"]["x-api-key"] == "sk-K"
    lines = gw.format_credits(got)
    assert any("월 할당" in l and "remaining=879.5" in l for l in lines) and any("합계" in l and "1379.5" in l for l in lines)
    assert "알 수 없는" in gw.format_credits({"weird": 1})[0]
    g2 = gateway((200, {"object": "list", "data": [{"id": "claude-sonnet-5", "type": "llm"}, {"id": "claude-opus-5"}]}))
    models = gw.fetch_models(g2.base, "k")
    assert [m["id"] for m in models] == ["claude-sonnet-5", "claude-opus-5"] and "type=llm" in g2.requests[0]["path"]
    g3 = gateway((401, {"error": "Invalid API key"}))
    with pytest.raises(RuntimeError, match="HTTP 401"):
        gw.fetch_credits(g3.base, "bad")


def test_set_secret_credits_and_models_output(capsys):
    import set_secret
    s = Settings()
    s.ai_api_key, s.ai_base_url, s.ai_model = "sk-SEC", "https://x/v1/gateway/claude", "claude-sonnet-5"
    assert set_secret.show_credits(s, fetch=lambda b, k: {"total": {"used": 1, "remaining": 99}}) is True
    out = capsys.readouterr().out
    assert "[크레딧]" in out and "remaining=99" in out
    assert set_secret.show_models(s, fetch=lambda b, k: [{"id": "claude-sonnet-5"}, {"id": "claude-opus-5"}]) is True
    out = capsys.readouterr().out
    assert "2개" in out and "claude-sonnet-5  <- 현재 AI_MODEL" in out and "목록에 없습니다" not in out
    set_secret.show_models(s, fetch=lambda b, k: [{"id": "other"}])
    assert "목록에 없습니다" in capsys.readouterr().out

    def boom(b, k):
        raise RuntimeError("HTTP 401: bad key sk-SEC")
    assert set_secret.show_credits(s, fetch=boom) is False
    assert "sk-SEC" not in capsys.readouterr().out
    s.ai_base_url = ""                                                      # 직접 호출이면 해당 없음
    assert set_secret.show_credits(s) is False and "게이트웨이" in capsys.readouterr().out


# ================================================================== 응답 잘림 감지 / max_tokens 재지정
def test_anthropic_detects_max_tokens_stop_and_accepts_override(gateway):
    g = gateway((200, {"model": "claude-sonnet-5", "content": [{"type": "text", "text": '{"a": '}],
                       "stop_reason": "max_tokens", "usage": {"input_tokens": 10, "output_tokens": 4000}}),
                anth_ok("{}"))
    c = anth(g)
    r1 = c.complete("S", "U", [])
    assert r1.truncated is True and r1.stop_reason == "max_tokens" and g.requests[0]["json"]["max_tokens"] == 4000
    r2 = c.complete("S", "U", [], max_tokens=9000)
    assert g.requests[1]["json"]["max_tokens"] == 9000 and r2.truncated is False


def test_openai_compat_detects_length_finish_and_accepts_override(gateway):
    cut = (200, {"model": "m", "choices": [{"finish_reason": "length", "message": {"content": '{"a": '}}],
                 "usage": {"prompt_tokens": 5, "completion_tokens": 4000}})
    g = gateway(cut, ok("{}"))
    c = client(g)
    r1 = c.complete("S", "U", [])
    assert r1.truncated is True and r1.stop_reason == "length"
    r2 = c.complete("S", "U", [], max_tokens=7000)
    assert g.requests[1]["json"]["max_tokens"] == 7000 and r2.truncated is False and r2.stop_reason == "stop"
