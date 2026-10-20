# -*- coding: utf-8 -*-
"""[API] 6-B 추천 API 서버 (FastAPI). 계약: docs/api_contract_v1.md, docs/openapi_v1.json

  POST /v1/recommend   판정과 후속 질문 (X-API-Key)         GET /v1/health  상태 점검 (키 없음, 학생 정보 없음)
  GET  /v1/meta        버전·한도 (X-API-Key)                GET /v1/openapi.json  계약 파일
  GET  /demo, POST /demo/recommend   시험 화면 (API_ENABLE_DEMO=true 일 때만, 키 없음 -> 개발용)

판정 로직은 recommend.request.handle_recommend 가 그대로 하고, 이 파일은 HTTP 계층만 맡는다:
인증, 본문 크기 제한, JSON 읽기, 오류 본문, 요청 로그(입력값은 남기지 않는다), 공고 목록 캐시.
서버는 학생 정보를 저장하지 않고 로그에도 남기지 않는다. 요청 번호·상태·처리 시간·건수만 기록한다.
"""
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from ..common.config import Settings
from ..recommend import profile_contract as PC
from ..recommend import request as RQ
from ..recommend.api_spec import API_VERSION
from ..recommend.followup import MAX_QUESTIONS, MAX_ROUNDS
from ..recommend.profile import ProfileError
from ..recommend.result_format import RESULT_VERSION
from ..recommend.service import Recommender
from ..chat.service import ChatService
from ..recommend.student_text import build_template, format_parse_report, parse_student_text
from .cache import CachedSource, SourceUnavailable
from .demo_view import demo_payload

log = logging.getLogger("scholarship.api")
OPENAPI_FILE = Path(__file__).resolve().parents[2] / "docs" / "openapi_v1.json"
DEMO_MAX_BYTES = 60000


def _error(code: str, message: str, status: Optional[int] = None, fields=None, request_id: Optional[str] = None) -> JSONResponse:
    e = RQ.ApiError(code, message, fields)
    return JSONResponse(e.body(request_id), status_code=status or e.status)


def _counts(body: Dict[str, Any]) -> str:
    c = body.get("counts") if isinstance(body, dict) else None
    return "match={match} cond={conditional} no_match={no_match}".format(**c) if isinstance(c, dict) and {"match", "conditional", "no_match"} <= set(c) else "-"


def create_app(settings: Settings, source_factory: Callable[[], Any], clock: Callable[[], float] = time.monotonic) -> FastAPI:
    """source_factory: list_candidates(today) 와 close() 를 가진 후보 공급원을 만든다 (DB 연결 또는 파일 폴더)."""
    cache = CachedSource(source_factory, ttl=settings.api_cache_seconds, stale_ttl=settings.api_stale_seconds)
    rec = Recommender(cache, settings)
    chat = ChatService(settings, cache, rec)                       # 기본은 꺼짐 (CHAT_ENABLED). 켜기 전에는 AI 를 부르지 않는다

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        cache.close()                                              # 서버를 끌 때 DB 연결을 닫는다

    app = FastAPI(title="장학금 추천 API", version=API_VERSION, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.cache, app.state.recommender, app.state.settings, app.state.chat = cache, rec, settings, chat

    # ------------------------------------------------------------------ 인증
    def check_key(request: Request) -> Optional[JSONResponse]:
        if not settings.api_require_key:
            return None
        if not settings.api_key:                                   # 키를 정하지 않은 채 열어 두지 않는다
            log.error("API_KEY 가 설정되지 않았습니다 (python set_secret.py API_KEY)")
            return _error("SERVER_CONFIG", "서버 설정에 문제가 있습니다. 관리자에게 문의하세요")
        given = request.headers.get("x-api-key", "")
        if not hmac.compare_digest(given.encode("utf-8"), settings.api_key.encode("utf-8")):
            return _error("UNAUTHORIZED", "API 키가 없거나 올바르지 않습니다")
        return None

    async def read_json(request: Request) -> Tuple[Any, Optional[JSONResponse]]:
        limit = settings.api_max_body_bytes
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            return None, _error("PAYLOAD_TOO_LARGE", "요청이 너무 큽니다 (최대 {:,} 바이트)".format(limit))
        raw = bytearray()
        async for chunk in request.stream():                      # Content-Length 가 없거나 거짓이어도 읽는 도중에 끊는다
            raw += chunk
            if len(raw) > limit:
                return None, _error("PAYLOAD_TOO_LARGE", "요청이 너무 큽니다 (최대 {:,} 바이트)".format(limit))
        try:
            return json.loads(bytes(raw).decode("utf-8")), None
        except (UnicodeDecodeError, ValueError):
            return None, _error("INVALID_REQUEST", "요청 형식이 올바르지 않습니다 (1개 항목)", fields=[{"field": "$", "message": "UTF-8 JSON 이 아닙니다"}])

    def process(body: Any) -> Tuple[int, Dict[str, Any]]:
        rid = body.get("request_id") if isinstance(body, dict) and isinstance(body.get("request_id"), str) and RQ.REQUEST_ID.match(body["request_id"]) else None
        try:
            return RQ.handle_recommend(rec, body)
        except SourceUnavailable:
            return 503, RQ.ApiError("UNAVAILABLE", "공고 목록을 읽을 수 없습니다. 잠시 후 다시 시도하세요").body(rid)
        except Exception as e:                                     # 메시지에 입력값이 섞일 수 있어 예외 이름만 남긴다
            log.error("INTERNAL rid=%s type=%s", rid, type(e).__name__)
            return 500, RQ.ApiError("INTERNAL", "서버 내부 오류입니다. 잠시 후 다시 시도하세요").body(rid)

    # ------------------------------------------------------------------ 엔드포인트
    @app.post("/v1/recommend")
    async def recommend(request: Request) -> Response:
        t0 = clock()
        denied = check_key(request)
        if denied:
            log.warning("recommend status=%s ms=%d", denied.status_code, (clock() - t0) * 1000)
            return denied
        body, bad = await read_json(request)
        if bad:
            log.warning("recommend status=%s ms=%d", bad.status_code, (clock() - t0) * 1000)
            return bad
        status, out = await run_in_threadpool(process, body)
        rid = out.get("request_id") if status == 200 else (out.get("error") or {}).get("request_id")
        log.info("recommend rid=%s status=%s ms=%d %s", rid, status, (clock() - t0) * 1000, _counts(out))
        return JSONResponse(out, status_code=status)

    def process_chat(body: Any) -> Tuple[int, Dict[str, Any]]:
        rid = body.get("request_id") if isinstance(body, dict) and isinstance(body.get("request_id"), str) and RQ.REQUEST_ID.match(body["request_id"]) else None
        try:
            return chat.handle(body)
        except SourceUnavailable:
            return 503, RQ.ApiError("UNAVAILABLE", "공고 목록을 읽을 수 없습니다. 잠시 후 다시 시도하세요").body(rid)
        except Exception as e:                                     # 질문·답변이 메시지에 섞일 수 있어 예외 이름만 남긴다
            log.error("INTERNAL chat rid=%s type=%s", rid, type(e).__name__)
            return 500, RQ.ApiError("INTERNAL", "서버 내부 오류입니다. 잠시 후 다시 시도하세요").body(rid)

    @app.post("/v1/chat")
    async def chat_endpoint(request: Request) -> Response:
        """공고 질문 챗봇. 판정은 바꾸지 않고 공고 원문을 근거로 설명한다 (CHAT_ENABLED=true 일 때만). 질문·답변은 저장·로그하지 않는다."""
        t0 = clock()
        denied = check_key(request)
        if denied:
            log.warning("chat status=%s ms=%d", denied.status_code, (clock() - t0) * 1000)
            return denied
        body, bad = await read_json(request)
        if bad:
            log.warning("chat status=%s ms=%d", bad.status_code, (clock() - t0) * 1000)
            return bad
        status, out = await run_in_threadpool(process_chat, body)
        return JSONResponse(out, status_code=status)

    def serve_file(notice_id: str, seq: str) -> Response:
        try:
            path, name = chat.file_for_download(notice_id, seq)
        except RQ.ApiError as e:
            return JSONResponse(e.body(None), status_code=e.status)
        except SourceUnavailable:
            return _error("UNAVAILABLE", "공고 목록을 읽을 수 없습니다. 잠시 후 다시 시도하세요")
        except Exception as e:                                     # 경로·파일명이 메시지에 섞일 수 있어 예외 이름만 남긴다
            log.error("INTERNAL file type=%s", type(e).__name__)
            return _error("INTERNAL", "서버 내부 오류입니다. 잠시 후 다시 시도하세요")
        log.info("file notice=%s seq=%s", notice_id, seq)          # 파일 내용·파일명은 남기지 않는다
        return FileResponse(str(path), filename=name, media_type="application/octet-stream", content_disposition_type="attachment",
                            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})

    @app.get("/v1/notices/{notice_id}/files/{seq}")
    async def notice_file(notice_id: str, seq: str, request: Request) -> Response:
        """챗봇 출처의 원본 첨부파일 내려받기 (API 키 필요). 포털 서버가 키로 대신 불러 사용자에게 전달한다."""
        denied = check_key(request)
        if denied:
            return denied
        return await run_in_threadpool(serve_file, notice_id, seq)

    @app.get("/v1/health")
    async def health() -> Response:
        """키 없이 부른다 (감시용). 공고 목록을 확인하고 시각·건수만 알린다 (학생 정보 없음)."""
        def probe() -> Tuple[int, Dict[str, Any]]:
            today = date.today()
            try:
                cache.list_candidates(today)                       # 캐시가 만료됐으면 여기서 다시 읽는다
            except SourceUnavailable:
                return 503, {"status": "down", "api_version": API_VERSION, "time": datetime.now().isoformat(timespec="seconds"), "notices": cache.status(today)}
            st = cache.status(today)
            return 200, {"status": "degraded" if st["stale"] else "ok", "api_version": API_VERSION,
                         "time": datetime.now().isoformat(timespec="seconds"), "notices": st}
        status, body = await run_in_threadpool(probe)
        return JSONResponse(body, status_code=status)

    @app.get("/v1/meta")
    async def meta(request: Request) -> Response:
        denied = check_key(request)
        if denied:
            return denied
        return JSONResponse({
            "api_version": API_VERSION, "result_version": RESULT_VERSION,
            "limits": {"max_profile_keys": RQ.MAX_PROFILE_KEYS, "max_declined": RQ.MAX_DECLINED, "max_round": RQ.MAX_ROUND,
                       "max_questions": RQ.MAX_QUESTIONS_LIMIT, "default_questions": MAX_QUESTIONS, "question_rounds": MAX_ROUNDS,
                       "max_body_bytes": settings.api_max_body_bytes, "max_answers_text": RQ.MAX_TEXT},
            "recommend_categories": list(settings.recommend_categories),
            "profile_fields": len(PC.fields()),
        })

    @app.get("/v1/openapi.json")
    async def openapi_file() -> Response:
        try:
            return Response(OPENAPI_FILE.read_text(encoding="utf-8"), media_type="application/json")
        except OSError:
            return _error("INTERNAL", "계약 파일을 찾을 수 없습니다")

    # ------------------------------------------------------------------ 시험 화면 (개발용)
    if settings.api_enable_demo:
        @app.get("/demo", response_class=HTMLResponse)
        async def demo_page() -> str:
            return DEMO_HTML.replace("__TEMPLATE__", json.dumps(build_template(), ensure_ascii=False))

        @app.post("/demo/recommend")
        async def demo_recommend(request: Request) -> Response:
            raw = b""
            async for chunk in request.stream():
                raw += chunk
                if len(raw) > DEMO_MAX_BYTES:
                    return _error("PAYLOAD_TOO_LARGE", "입력이 너무 큽니다")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                return _error("INVALID_REQUEST", "UTF-8 텍스트가 아닙니다")

            def run() -> Dict[str, Any]:
                try:
                    parsed = parse_student_text(text)
                    result = rec.recommend(parsed.data)
                except ProfileError as e:
                    return {"ok": False, "message": "입력 오류: {}".format(e)}
                except SourceUnavailable:
                    return {"ok": False, "message": "공고 목록을 읽을 수 없습니다"}
                return demo_payload(format_parse_report(parsed.report), result)
            return JSONResponse(await run_in_threadpool(run))

        @app.get("/demo/files/{notice_id}/{seq}")
        async def demo_file(notice_id: str, seq: str) -> Response:
            """시험 화면의 첨부 내려받기 (인증 없음, 개발용)."""
            return await run_in_threadpool(serve_file, notice_id, seq)

        @app.post("/demo/chat")
        async def demo_chat(request: Request) -> Response:
            """시험 화면의 챗봇: 학생 텍스트를 프로필로 바꿔 /v1/chat 과 같은 처리를 한다 (인증 없음, 개발용)."""
            body, bad = await read_json(request)
            if bad:
                return bad
            if not isinstance(body, dict):
                return _error("INVALID_REQUEST", "JSON 객체여야 합니다")
            if isinstance(body.get("student_text"), str):
                try:
                    body["profile"] = parse_student_text(body.pop("student_text")).data
                except ProfileError as e:
                    return JSONResponse({"error": {"code": "INVALID_PROFILE", "message": "입력 오류: {}".format(e), "fields": [], "request_id": None}}, status_code=400)
            status, out = await run_in_threadpool(process_chat, body)
            return JSONResponse(out, status_code=status)

    return app


DEMO_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>장학금 추천 시험 화면</title>
<style>
:root{--bg:#fff;--fg:#1d2430;--mut:#5b6678;--line:#d9dee7;--acc:#1f5fbf;--warn:#fff4d6;--head:#f1f4f9;--card:#fff;
--ok:#1b7f4b;--okbg:#e3f5ea;--cond:#a85d00;--condbg:#fff0d9;--no:#a33a3a;--nobg:#fbe6e6}
@media (prefers-color-scheme:dark){:root{--bg:#14181f;--fg:#e6e9ef;--mut:#9aa4b5;--line:#2b3340;--acc:#7fb0ff;--warn:#3a3217;--head:#1c222c;--card:#181d26;
--ok:#5fcf8f;--okbg:#17301f;--cond:#f0b45a;--condbg:#38290f;--no:#f08a8a;--nobg:#3a1a1a}}
*{box-sizing:border-box}main.wide{max-width:1560px}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 system-ui,"Malgun Gothic",sans-serif}
main{max-width:1100px;transition:max-width .2s;margin:0 auto;padding:16px}h1{font-size:20px;margin:0 0 4px}.sub{color:var(--mut);margin:0 0 10px}
.warn{background:var(--warn);border:1px solid var(--line);padding:8px 12px;border-radius:6px;margin:0 0 12px}
textarea{width:100%;height:210px;border:1px solid var(--line);border-radius:6px;padding:10px;background:transparent;color:var(--fg);font:13px/1.5 ui-monospace,Consolas,monospace}
button{background:var(--acc);color:#fff;border:0;border-radius:6px;padding:8px 16px;font-size:15px;cursor:pointer;margin:8px 8px 0 0}
button.sec{background:transparent;color:var(--acc);border:1px solid var(--acc)}
#summary{margin:18px 0 4px;font-weight:600}.note{color:var(--mut);margin:0 0 4px}
.card{background:var(--card);border:1px solid var(--line);border-left-width:8px;border-radius:8px;margin:22px 0;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.card.match{border-left-color:var(--ok)}.card.cond{border-left-color:var(--cond)}.card.no{border-left-color:var(--no)}
.hd{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center}
.card.match .hd{background:var(--okbg)}.card.cond .hd{background:var(--condbg)}.card.no .hd{background:var(--nobg)}
.badge{font-weight:700;border-radius:999px;padding:2px 12px;color:#fff;font-size:13px}
.match .badge{background:var(--ok)}.cond .badge{background:var(--cond)}.no .badge{background:var(--no)}
.dark .badge{color:#000}.ttl{font-size:17px;font-weight:700;flex:1 1 320px}.meta{color:var(--mut);font-size:13px}
table{border-collapse:collapse;width:100%}th,td{padding:8px 16px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}
tr:last-child th,tr:last-child td{border-bottom:0}th{width:110px;color:var(--mut);font-weight:600;white-space:nowrap;background:var(--head)}
ul{list-style:none;margin:0;padding:0}li{margin:3px 0}li.sub2{margin-left:18px}.gh{font-weight:700;margin:8px 0 2px}.gh:first-child{margin-top:0}
.chip{display:inline-block;border-radius:4px;padding:0 7px;margin-right:6px;font-size:12px;font-weight:600;border:1px solid currentColor;white-space:nowrap}
.chip.CONFIRM{color:var(--cond)}.chip.EXCLUDE{color:var(--no)}.chip.WARN{color:var(--cond)}.chip.NOTE{color:var(--mut)}.chip.BENEFIT{color:var(--ok)}.chip.FNORMAL{color:var(--ok)}.chip.FHARD{color:var(--cond)}.chip.FVERY_HARD{color:var(--no);background:var(--nobg)}
table.sub{margin:4px 0;width:100%}table.sub th,table.sub td{border:1px solid var(--bd,#8884);padding:3px 8px;text-align:left;font-weight:400}table.sub th{font-weight:600}
ul.sg{margin:2px 0;padding-left:18px}ul.sg li{margin:6px 0}.q{color:var(--mut);font-size:12px;margin:2px 0 0 4px}.chip.Rmet{color:var(--ok)}.chip.Rconfirm{color:var(--cond)}.chip.Rexclude{color:var(--no)}.chip.Rfail{color:var(--no)}details summary{cursor:pointer;color:var(--mut);margin-top:4px}
.cb{border:1px solid var(--line);border-radius:12px;background:var(--card);overflow:hidden}
#split.two{display:grid;grid-template-columns:minmax(0,1fr) minmax(360px,480px);gap:20px;align-items:start}
#chat{display:flex;flex-direction:column}#chat[hidden]{display:none}#chat .cb{display:flex;flex-direction:column;flex:1;min-height:0}#chat details.note{margin:0 0 6px}#chat details.note summary{margin:0}
#split.two #chat{position:sticky;top:8px;height:calc(100vh - 16px);overscroll-behavior:contain}#split.two #chat h2{margin:0 0 4px!important}
#split.two #log{flex:1;min-height:0;height:auto}
@media (max-width:1000px){#split.two{display:block}#split.two #chat{position:static;height:auto}#split.two #log{height:min(60vh,520px);flex:none}}
.card.sel{box-shadow:0 0 0 3px var(--acc)}.askbtn{margin:0;padding:2px 12px;font-size:13px;border-radius:999px}
.cbh{padding:10px 14px;background:var(--head);border-bottom:1px solid var(--line);display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.cbh{flex:none}.cbh label{font-size:12px;color:var(--mut);white-space:nowrap}.cbh select{flex:1 1 200px;min-width:0;max-width:100%;padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg);font-size:13px}
#log{height:min(60vh,520px);overflow-y:auto;overscroll-behavior:contain;padding:14px;display:flex;flex-direction:column;gap:10px;background:var(--bg)}
.msg{display:flex;gap:8px;max-width:88%}.msg.me{align-self:flex-end;flex-direction:row-reverse}
.av{flex:none;width:30px;height:30px;border-radius:50%;background:var(--acc);color:#fff;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700}
.msg.me .av{display:none}
.bub{padding:9px 13px;border-radius:14px;white-space:pre-wrap;word-break:break-word;background:var(--head);border:1px solid var(--line);border-top-left-radius:4px}
.msg.me .bub{background:var(--acc);color:#fff;border-color:var(--acc);border-top-left-radius:14px;border-top-right-radius:4px}
.msg.sys .bub{background:var(--warn);font-size:13px}
.cite{margin:8px 0 0;padding:6px 0 0;border-top:1px dashed var(--line);font-size:12px;color:var(--mut);white-space:normal}.cite div{margin:3px 0}
.fus{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.fus button{margin:0;padding:4px 10px;font-size:13px;border-radius:999px}
.files{margin-top:8px;padding-top:6px;border-top:1px dashed var(--line);font-size:12px}.files .fh{font-weight:600;margin-bottom:3px}.files .fl{margin:2px 0}.files .ft{color:var(--mut)}.files a.dl{color:var(--acc);font-weight:600}.files .fz{color:var(--mut)}
.diag{font-size:11px;color:var(--mut);margin-top:6px;white-space:normal}.typing{display:inline-flex;gap:4px;padding:4px 2px}.typing i{width:7px;height:7px;border-radius:50%;background:var(--mut);animation:bl 1.2s infinite}.typing i:nth-child(2){animation-delay:.2s}.typing i:nth-child(3){animation-delay:.4s}
@keyframes bl{0%,80%,100%{opacity:.25}40%{opacity:1}}
.cbi{flex:none;display:flex;gap:8px;padding:10px;border-top:1px solid var(--line);background:var(--card)}
.cbi input{flex:1;padding:9px 12px;border:1px solid var(--line);border-radius:999px;background:transparent;color:var(--fg);font-size:14px}.cbi button{margin:0;border-radius:999px;padding:8px 18px}
a.url{color:var(--acc);word-break:break-all;font-family:ui-monospace,Consolas,monospace;font-size:13px}.empty{color:var(--mut)}
pre{white-space:pre-wrap;border:1px solid var(--line);border-radius:6px;padding:10px;font:12px/1.5 ui-monospace,Consolas,monospace}
</style></head><body><main>
<h1>장학금 추천 시험 화면</h1><p class="sub">학생 정보를 `- 라벨: 값` 줄로 적고 [추천 받기]를 누르세요. 입력은 서버에 저장되지 않습니다.</p>
<p class="warn">개발·시험용 화면입니다. 인증이 없으므로 운영 서버에서는 켜지 마세요 (API_ENABLE_DEMO).</p>
<textarea id="in" spellcheck="false"></textarea>
<button id="go">추천 받기</button><button class="sec" id="tpl">빈 양식 넣기</button><button class="sec" id="txt">텍스트로 보기</button>
<div id="split"><div id="left"><div id="parse" class="note"></div><div id="summary"></div><div id="notes" class="note"></div><div id="res"></div><pre id="raw" hidden></pre></div>
<section id="chat" hidden><h2 style="font-size:16px">공고 질문 도우미</h2>
<details class="note"><summary>이용 안내 (펼치기)</summary>충족·조건부·미달 공고에 대해 물어보세요. 공고 원문(본문·첨부)을 근거로 답하고, 판정은 바꾸지 않습니다. 이름·학번·연락처 같은 개인정보는 적지 마세요. 대화는 이 화면에만 있고 서버에 저장되지 않습니다.</details>
<div class="cb"><div class="cbh"><select id="focus" title="질문할 공고"></select><button class="sec" id="clr" title="대화 지우기" style="margin:0;padding:4px 10px;font-size:13px">지우기</button>
<label><input type="checkbox" id="share"> 내 판정 결과도 AI에 전달 (동의)</label></div>
<div id="log"></div><div class="cbi"><input id="q" maxlength="500" placeholder="예: 제출 서류가 뭐예요?" autocomplete="off"><button id="ask">보내기</button></div></div></section></div>
<script>
const TEMPLATE=__TEMPLATE__;const $=id=>document.getElementById(id);let last=null;
$("in").value=TEMPLATE;$("tpl").onclick=()=>{$("in").value=TEMPLATE};
$("txt").onclick=()=>{const r=$("raw");r.hidden=!r.hidden;if(last)r.textContent=last.text};
function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e}
function row(tb,label,node){const tr=el("tr");tr.append(el("th",null,label));const td=el("td");td.append(node);tr.append(td);tb.append(tr)}
function textNode(t){return document.createTextNode(t)}
function chip(c,kind,label){return el("span","chip "+kind,c.symbols[kind]+" "+label)}
function list(items){const ul=el("ul");items.forEach(f=>{const li=el("li");f(li);ul.append(li)});return ul}
const RES={fail:"미달",met:"충족",confirm:"확인 필요",exclude:"제외 조건","":"-"};
const MK={fail:"✕",met:"✓",confirm:"△",exclude:"⊘","":"·"};   // 조건 줄 앞 기호
function condTable(rows){const hv=rows.some(r=>r.variant);if(hv)rows=rows.filter(r=>r.variant||r.cond!=="해당 유형");const t=el("table","sub"),h=el("tr");(hv?["유형"]:[]).concat(["조건","결과","내 상태"]).forEach(x=>h.append(el("th",null,x)));t.append(h);
 rows.forEach(r=>{const tr=el("tr");let cond=r.cond;
  if(hv){const pre="["+r.variant+"] ";if(r.variant&&cond.startsWith(pre))cond=cond.slice(pre.length);tr.append(el("td",null,r.variant||"공통"))}
  const td=el("td");if(r.result){td.append(el("span","chip R"+r.result,RES[r.result]))}else td.append(textNode("-"));
  tr.append(el("td",null,(MK[r.result]||MK[""])+" "+cond),td,el("td",null,r.state));t.append(tr)});return t}
function variantTable(rows,slim){const am=rows.every(r=>r.amount),ev=rows.some(r=>r.evaluation),cr=rows.some(r=>r.criteria),dc=rows.some(r=>r.docs);   // 금액 열은 모든 유형에 금액이 있을 때만 (없으면 혜택 행에서 본다). slim: 아래 조건 표가 유형별 결과·사유를 보여 주므로 결과·사유 열은 뺀다
 const t=el("table","sub vt"),h=el("tr");["유형"].concat(slim?[]:["결과"],am?["금액"]:[],ev?["평가 기준"]:[],cr?["선발 기준"]:[],dc?["추가 서류"]:[],slim?[]:["확인·사유"]).forEach(x=>h.append(el("th",null,x)));t.append(h);
 rows.forEach(r=>{const tr=el("tr");tr.append(el("td",null,r.name));
  if(!slim){const td=el("td");td.append(el("span","chip R"+r.result,RES[r.result]));tr.append(td)}
  if(am)tr.append(el("td",null,r.amount));
  if(ev)tr.append(el("td",null,r.evaluation||"-"));if(cr)tr.append(el("td",null,r.criteria||"-"));if(dc)tr.append(el("td",null,r.docs||"-"));if(!slim)tr.append(el("td",null,r.note||"-"));t.append(tr)});return t}
function condBox(c,cd,groups){const b=el("div");const open=cd.open.concat(cd.info);
 if(groups&&groups.length){const gt=el("table","sub"),gh=el("tr");["조건","결과","내용","원문"].forEach(x=>gh.append(el("th",null,x)));gt.append(gh);
   groups.forEach(g=>{const tr=el("tr"),rc=el("td");rc.append(el("span","chip R"+g.result,RES[g.result]));
    tr.append(el("td",null,(MK[g.result]||MK[""])+" "+g.title),rc,el("td",null,g.summary||"-"),el("td","q",g.quote&&g.quote!==g.summary?"“"+g.quote+"”":"-"));gt.append(tr)});b.append(gt);
   if(open.length){const d=el("details"),sm=el("summary",null,"원문 조건 "+cd.open.length+"개 펼치기");d.append(sm,condTable(open));b.append(d)}}
 else if(open.length)b.append(condTable(open));
 if(cd.met.length){const d=el("details"),sm=el("summary",null,"충족 "+cd.met.length+"개 펼치기 ("+cd.met.map(m=>m.cond).join(" · ")+")");d.append(sm,condTable(cd.met));b.append(d)}
 if(!open.length&&!cd.met.length)b.append(el("span","empty","확인할 조건이 없음"));return b}
function render(c){const root=$("res");root.textContent="";$("summary").textContent=c.summary;$("notes").textContent=c.notes.join(" / ");
 for(const x of c.items){const card=el("article","card "+x.verdict),hd=el("div","hd");
  hd.append(el("span","badge",c.symbols[x.verdict]+" "+c.verdict_title[x.verdict]+" "+x.index+"/"+x.total),el("span","ttl",x.title),el("span","meta","글번호 "+x.src_id+(x.deadline?"  ·  마감 "+x.deadline:"")));
  const nid=noticeId(x.src_id);if(nid){const ab=el("button","sec askbtn","이 공고에 질문");ab.onclick=()=>pick(nid,card);hd.append(ab);card.dataset.nid=nid}
  card.append(hd);const tb=el("table");
  const cd=x.conditions,hasCond=cd.open.length||cd.met.length||cd.info.length;
  if(x.verdict==="no"&&!hasCond)row(tb,"미달 사유",list(x.reasons.map(r=>li=>{li.append(chip(c,"EXCLUDE","미달"),textNode(r))})));
  if(x.period)row(tb,"모집기간",textNode(x.period));
  if(x.summary)row(tb,"요약",textNode(x.summary));
  if(x.benefit&&x.benefit.length)row(tb,"혜택",list(x.benefit.map(b=>li=>{li.append(chip(c,"BENEFIT","혜택"),textNode(b))})));
  if(x.variant_rows&&x.variant_rows.length)row(tb,"유형 ("+x.variant_rows.length+"개)",variantTable(x.variant_rows,!!(cd.open.length||cd.met.length||cd.info.length)&&!x.variant_rows.some(r=>r.result==="fail")));
  if(x.selection&&x.selection.method&&x.selection.method.length)row(tb,"선발 방식",list(x.selection.method.map(t=>li=>{li.append(textNode(t))})));
  if(x.selection&&x.selection.criteria)row(tb,"선발 기준",textNode(x.selection.criteria));
  if(x.review&&x.review.length)row(tb,"심사 기준",list(x.review.map(d=>li=>{li.append(textNode(d))})));
  if(x.verdict!=="no"||hasCond)row(tb,"조건",condBox(c,cd,x.condition_groups));
  if(x.selection&&x.selection.procedure)row(tb,"진행 절차",textNode(x.selection.procedure));
  if(x.documents&&x.documents.length)row(tb,"제출 서류",list(x.documents.map(d=>li=>{li.append(textNode("- "+d))})));
  const cg=x.checks_groups||(x.checks.length?[{title:"확인사항",items:x.checks}]:[]);
  for(const g of cg)row(tb,g.title,list(g.items.map(t=>li=>{li.append(textNode("- "+t))})));
  if(x.verdict!=="no"&&!cg.length)row(tb,"확인사항",el("span","empty","특이 사항 없음"));
  if(x.assumptions.length)row(tb,"가정",list(x.assumptions.map(a=>li=>{li.append(chip(c,"NOTE","가정"),textNode(a))})));
  const lk=el("span");if(x.url){const a=el("a","url",x.url);a.href=x.url;a.target="_blank";a.rel="noopener noreferrer";lk.append(textNode(c.symbols.LINK+" "),a)}else lk.append(el("span","empty","(주소 없음)"));
  row(tb,"원문",lk);card.append(tb);root.append(card)}}
let NIDS=[],HIST=[],NT=[],BUSY=false;
function diagText(d){return d?("점검: 질문 대상 "+d.found+"/"+d.asked+" · 원문 있음 "+d.usable+" · 선택된 공고 "+d.chosen+(d.focus_in?" (고른 공고 포함)":"")+" · 보낸 조각 "+d.chunks+"개("+d.chars+"자, 첨부 "+d.attach_used+"개)"+" · "+(d.rag?"색인 검색":"원문 직접 선택")+" · AI "+(d.ai?"호출함":"호출 안 함")+(d.ai?" · 출력 "+d.out_tokens+"/"+d.max_tokens+" 토큰":"")+(d.why?" · 사유: "+d.why:"")+(d.cited_files?" · 인용한 첨부 "+d.cited_files+"개 중 다운로드 가능 "+d.linked_files+"개"+(d.file_why?" ("+d.file_why+")":""):"")):""}
function addMsg(role,text,cites,fu,diag,files){const m=el("div","msg "+role);if(role==="bot")m.append(el("div","av","도"));const b=el("div","bub");b.append(textNode(text));
 if(files&&files.length){const w=el("div","files");w.append(el("div","fh","원본 첨부파일"));files.forEach(x=>{const d=el("div","fl"),a=el("a","dl",x.file.name+(x.file.ext&&!x.file.name.toLowerCase().endsWith("."+x.file.ext)?"."+x.file.ext:""));a.href="/demo/files/"+encodeURIComponent(x.notice_id)+"/"+encodeURIComponent(x.file.seq);
   d.append(el("span","ft","- "+(x.title||x.notice_id)+" > "),a,el("span","fz"," ("+(x.file.size>1048576?(x.file.size/1048576).toFixed(1)+"MB":Math.max(1,Math.round(x.file.size/1024))+"KB")+") 다운로드"));w.append(d)});b.append(w)}
 if(fu&&fu.length){const w=el("div","fus");fu.forEach(f=>{const k=el("button","sec",f);k.onclick=()=>{$("q").value=f;$("ask").click()};w.append(k)});b.append(w)}
 if(diag)b.append(el("div","diag",diagText(diag)));
 m.append(b);$("log").append(m);$("log").scrollTop=$("log").scrollHeight;return m}
function typing(){const m=el("div","msg bot");m.append(el("div","av","도"));const b=el("div","bub"),t=el("span","typing");t.append(el("i"),el("i"),el("i"));b.append(t);m.append(b);$("log").append(m);$("log").scrollTop=$("log").scrollHeight;return m}
function noticeId(src){const n=NT.find(t=>t.id.split(":")[1]===String(src));return n?n.id:""}
function pick(nid,card){$("focus").value=nid;const n=NT.find(t=>t.id===nid);if(n)addMsg("sys","선택한 공고: "+n.title+" - 이 공고에 대해서만 답변합니다");goCard(nid);qf()}
function qf(){$("q").focus({preventScroll:true})}
function goCard(v){let t=null;document.querySelectorAll(".card").forEach(c=>{const on=!!v&&c.dataset.nid===v;c.classList.toggle("sel",on);if(on)t=c});if(t)t.scrollIntoView({behavior:"smooth",block:"start"})}
$("focus").onchange=()=>{goCard($("focus").value);qf()};
function fillFocus(){const s=$("focus");s.textContent="";s.append(new Option("공고를 고르지 않으면: 위 공고들에서 찾기 (질문과 가까운 공고 자동 선택)",""));NT.forEach(n=>s.append(new Option("["+n.verdict+"] "+n.title.slice(0,40),n.id)))}
function resetChat(){HIST=[];$("log").textContent="";addMsg("bot","안녕하세요. 위 공고에 대해 궁금한 점을 물어보세요. 공고를 고르면 그 공고를 먼저 찾아봅니다.")}
$("clr").onclick=resetChat;$("q").addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.isComposing){e.preventDefault();$("ask").click()}});
$("ask").onclick=async()=>{const q=$("q").value.trim();if(!q||BUSY)return;BUSY=true;$("ask").disabled=true;$("q").value="";addMsg("me",q);const tp=typing();
 const f=$("focus").value;let ids=NIDS.slice();if(f){ids=[f].concat(ids.filter(x=>x!==f))}
 const body={question:q,notice_ids:ids.slice(0,10),history:HIST.slice(-6),share_results:$("share").checked};if(f)body.focus=f;if(body.share_results)body.student_text=$("in").value;
 try{const r=await fetch("/demo/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});const j=await r.json();tp.remove();
  if(!r.ok){addMsg("bot",(j.error&&j.error.message)||"오류");return}
  addMsg("bot",j.answer.replace(/ ?\[\d{1,2}\]/g,""),j.citations,j.followups,j.diag,j.source_files);HIST.push({role:"user",text:q},{role:"assistant",text:j.answer});if(j.warnings&&j.warnings.length)addMsg("sys",j.warnings.join(" / "))}
 catch(e){tp.remove();addMsg("bot","오류: "+e)}finally{BUSY=false;$("ask").disabled=false;qf()}};
$("go").onclick=async()=>{$("summary").textContent="판정 중...";$("res").textContent="";$("parse").textContent="";
 try{const r=await fetch("/demo/recommend",{method:"POST",headers:{"Content-Type":"text/plain; charset=utf-8"},body:$("in").value});
  const j=await r.json();if(!j.ok){$("summary").textContent=j.message||(j.error&&j.error.message)||"오류";return}
  last=j;$("parse").textContent=j.parse;$("raw").textContent=j.text;NIDS=j.notice_ids||[];NT=j.notices||[];fillFocus();resetChat();$("chat").hidden=!NIDS.length;$("split").classList.toggle("two",!!NIDS.length);document.querySelector("main").classList.toggle("wide",!!NIDS.length);render(j.cards)}
 catch(e){$("summary").textContent="화면 표시 중 오류: "+e}};
</script></main></body></html>
"""
