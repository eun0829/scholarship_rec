# -*- coding: utf-8 -*-
"""[RECOMMEND] 6-A OpenAPI 3.1 명세 (docs/openapi_v1.json). 코드가 원본이고, 파일이 코드와 다르면 테스트가 실패한다.
다시 쓰기: python -m scholarship.recommend.api_spec --write

응답 스키마는 결과 형식 v1(result_format.py)을, 요청의 프로필 필드는 필드 계약(profile_contract.py)을 그대로 쓴다.
/v1/health, /v1/meta 는 계약만 먼저 정하고 6-B 에서 구현한다.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict

from . import profile_contract as PC
from . import result_format as F
from .followup import MAX_QUESTIONS, MAX_ROUNDS
from .request import ERROR_CODES, MAX_DECLINED, MAX_PROFILE_KEYS, MAX_QUESTIONS_LIMIT, MAX_ROUND, MAX_TEXT

API_VERSION = "1.0.0"


def _rewrite(o: Any) -> Any:
    """JSON Schema 의 #/$defs/x 참조를 OpenAPI 의 #/components/schemas/x 로"""
    if isinstance(o, dict):
        return {k: ("#/components/schemas/" + v.rsplit("/", 1)[-1] if k == "$ref" else _rewrite(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [_rewrite(x) for x in o]
    return o


def _profile_properties() -> Dict[str, Any]:
    props: Dict[str, Any] = {}
    for f in PC.fields():
        p: Dict[str, Any] = {"type": f.type, "description": f.desc, "example": f.example}
        if f.enum:
            p["enum"] = list(f.enum)
        if f.key in PC.BOUNDS:
            lo, hi = PC.BOUNDS[f.key]
            p["minimum"] = lo
            if f.key in PC.GPA_KEYS:
                p["maximum"] = 5.0                                               # 실제 최댓값은 gpa_scale (4.0 / 4.3 / 4.5 / 5.0)
            elif hi is not None:
                p["maximum"] = hi
        if f.key in PC.LENGTHS:
            p["minLength"], p["maxLength"] = 1, PC.LENGTHS[f.key]
        if f.key == "gpa_last_term":
            p["pattern"] = "^(199[0-9]|20[0-9]{2})-[12]$"
        if f.key == "special_codes":
            p["items"], p["uniqueItems"] = {"type": "string", "enum": list(f.enum)}, True
            p.pop("enum")
        if f.key == "term_history":
            p["minItems"], p["maxItems"] = 1, PC.MAX_ITEMS["term_history"]
            p["items"] = {"type": "object", "required": ["term", "gpa", "credits"], "properties": {
                "term": {"type": "string", "pattern": "^(199[0-9]|20[0-9]{2})-[12]$"}, "gpa": {"type": "number", "minimum": 0, "maximum": 5.0},
                "credits": {"type": "number", "minimum": 0, "maximum": PC.CREDITS_MAX}}}
        if f.key == "language_scores":
            p["additionalProperties"] = {"type": "number", "minimum": 0, "maximum": PC.LANGUAGE_MAX}
        if f.source:
            p["x-source"] = f.source
        if f.tier:
            p["x-tier"] = f.tier
        if f.sensitive:
            p["x-sensitive"] = True
        props[f.key] = p
    return props


def openapi() -> Dict[str, Any]:
    schemas: Dict[str, Any] = _rewrite(copy.deepcopy(F.DEFS))
    schemas["RecommendResponse"] = _rewrite({"type": "object", "required": F.STEP_REQUIRED, "properties": F.STEP_PROPS})
    profile = {"type": "object", "description": "학생 프로필. 알 수 없는 키는 무시하고 `profile_notes.ignored_keys` 로 알린다. 이름·학번·연락처 같은 개인정보 키는 받지 않고 `profile_notes.dropped_sensitive_keys` 로 알린다.",
               "properties": _profile_properties(), "additionalProperties": True}
    schemas["Profile"] = profile
    schemas["RecommendRequest"] = {
        "type": "object", "required": ["profile"],
        "properties": {
            "request_id": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,64}$", "description": "호출하는 쪽이 정하는 요청 번호 (응답과 오류에 그대로 돌려준다). 학번 등 학생을 알 수 있는 값을 쓰지 않는다"},
            "profile": {"$ref": "#/components/schemas/Profile"},
            "answers": {"$ref": "#/components/schemas/Profile", "description": "이번 회차에 학생이 새로 답한 값 (후속 질문의 answer.keys). profile 의 같은 키를 덮어쓴다"},
            "answers_text": {"type": "string", "maxLength": MAX_TEXT, "description": "답을 `- 라벨: 값` 줄로 보낼 때 (개발·시험용)"},
            "declined": {"type": "array", "maxItems": MAX_DECLINED, "items": {"type": "string", "maxLength": 24}, "description": "학생이 건너뛴 항목 코드. 다시 묻지 않는다"},
            "round": {"type": "integer", "minimum": 1, "maximum": MAX_ROUND, "default": 1, "description": "몇 번째 질문 회차인지. 질문은 최대 {}회".format(MAX_ROUNDS)},
            "max_questions": {"type": "integer", "minimum": 1, "maximum": MAX_QUESTIONS_LIMIT, "default": MAX_QUESTIONS, "description": "한 번에 묻는 최대 질문 수"},
            "today": {"type": "string", "format": "date", "description": "판정 기준일 (시험용. 보내지 않으면 서버의 오늘)"},
            "strict": {"type": "boolean", "default": False, "description": "true 면 응답에 `contract_violations`(계약과 한 글자라도 다른 입력의 목록)를 담는다. 판정은 평소처럼 이루어진다. 포털의 변환 코드를 시험할 때 쓴다"}},
        "description": "profile 항목 수는 최대 {}개. 알 수 없는 최상위 키는 무시하고 `profile_warnings` 로 알린다.".format(MAX_PROFILE_KEYS)}
    schemas["ChatRequest"] = {
        "type": "object", "required": ["question", "notice_ids"],
        "properties": {
            "request_id": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,64}$"},
            "question": {"type": "string", "minLength": 1, "maxLength": 500, "description": "학생의 질문. 이름·학번·연락처 같은 개인정보는 적지 않게 화면에서 안내한다 (서버는 주민번호·전화·이메일·학번 꼴을 가려서 AI 에 보낸다)"},
            "notice_ids": {"type": "array", "minItems": 1, "maxItems": 10, "items": {"type": "string", "pattern": "^[A-Z0-9]{2,10}:[A-Za-z0-9._-]{1,40}$"},
                           "description": "물어볼 공고 id (추천 결과 항목의 `id`, 예: `EXT01:153381`). 열려 있는 공고만 받는다"},
            "focus": {"type": "string", "description": "학생이 보고 있는 공고 id (notice_ids 안의 하나). 질문과 가장 관련 있는 공고로 먼저 본다"},
            "history": {"type": "array", "maxItems": 6, "items": {"type": "object", "required": ["role", "text"], "properties": {
                "role": {"enum": ["user", "assistant"]}, "text": {"type": "string", "maxLength": 1500}}},
                "description": "직전 대화(최근 6개 이하). 서버는 대화를 저장하지 않으므로 포털이 보관했다가 보낸다"},
            "share_results": {"type": "boolean", "default": False, "description": "학생이 '내 판정 결과를 AI에 전달'하는 데 동의했는가. true 일 때만 profile 을 쓰고, 서버가 판정을 다시 계산해 요약만 AI 에 보낸다 (profile 원본은 보내지 않는다). false 면 profile 은 읽지도 않는다"},
            "profile": {"$ref": "#/components/schemas/Profile", "description": "share_results 가 true 일 때 필요"},
            "today": {"type": "string", "format": "date", "description": "기준일 (시험용)"}}}
    schemas["ChatResponse"] = {
        "type": "object", "required": ["mode", "outcome", "grounded", "answer", "citations", "followups", "notices", "notices_missing", "warnings", "cached", "disclaimer"],
        "properties": {
            "request_id": {"type": ["string", "null"]},
            "mode": {"enum": ["A", "B"], "description": "A=질문과 원문만 AI 에 보냄, B=판정 결과 요약도 보냄 (학생 동의 필요)"},
            "outcome": {"enum": ["answered", "not_found", "out_of_scope", "ungrounded"], "description": "answered=원문 인용으로 뒷받침된 답, 나머지는 서버가 정한 고정 문구"},
            "grounded": {"type": "boolean", "description": "outcome 이 answered 인가"},
            "answer": {"type": "string", "description": "답변. 문장 끝의 [1] 은 citations 의 n 이다"},
            "citations": {"type": "array", "items": {"type": "object", "required": ["n", "notice_id", "source", "quote"], "properties": {
                "n": {"type": "integer"}, "notice_id": {"type": "string"}, "source": {"type": "string", "description": "본문 / 첨부1: 파일명 / 시스템 판정"},
                "quote": {"type": "string", "description": "원문에서 그대로 가져온 문장 (서버가 원문에 있는지 확인함)"},
                "file": {"$ref": "#/components/schemas/ChatFile", "description": "(선택) 인용이 첨부파일에서 나왔고 서버에 원본이 있을 때"}}}},
            "source_files": {"type": "array", "description": "(선택) 인용에 쓰인 원본 첨부파일 목록. `공고명 > 파일명` 으로 보여 주고 `GET /v1/notices/{notice_id}/files/{file.seq}` 로 내려받는다",
                             "items": {"type": "object", "required": ["notice_id", "title", "file"], "properties": {
                                 "notice_id": {"type": "string"}, "title": {"type": "string"}, "file": {"$ref": "#/components/schemas/ChatFile"}}}},
            "followups": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
            "notices": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "title": {"type": "string"}, "url": {"type": ["string", "null"]}}}, "description": "답변에 쓴 공고"},
            "notices_missing": {"type": "array", "items": {"type": "string"}, "description": "목록에 없거나 마감된 공고 id"},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "cached": {"type": "boolean"}, "disclaimer": {"type": "string"}}}
    schemas["ChatFile"] = {"type": "object", "required": ["seq", "name", "ext", "size"], "properties": {
        "seq": {"type": "string", "description": "파일 번호 (다운로드 주소에 쓴다)"}, "name": {"type": "string"}, "ext": {"type": "string"}, "size": {"type": "integer", "description": "바이트"}}}
    schemas["Error"] = {"type": "object", "required": ["error"], "properties": {"error": {"type": "object", "required": ["code", "message", "fields", "request_id"], "properties": {
        "code": {"enum": sorted(ERROR_CODES)}, "message": {"type": "string"}, "request_id": {"type": ["string", "null"]},
        "fields": {"type": "array", "items": {"type": "object", "required": ["field", "message"], "properties": {"field": {"type": "string"}, "message": {"type": "string"}}}}}}}}
    schemas["Health"] = {"type": "object", "required": ["status", "api_version"], "properties": {
        "status": {"enum": ["ok", "degraded"]}, "api_version": {"type": "string"}, "notices_as_of": {"type": ["string", "null"], "description": "공고 목록을 마지막으로 읽은 시각 (ISO 8601)"}}}
    schemas["Meta"] = {"type": "object", "required": ["api_version", "result_version", "max_questions", "max_rounds"], "properties": {
        "api_version": {"type": "string"}, "result_version": {"type": "integer"}, "notices_as_of": {"type": ["string", "null"]},
        "max_questions": {"type": "integer"}, "max_rounds": {"type": "integer"}}}

    def ref(n: str) -> Dict[str, Any]:
        return {"$ref": "#/components/schemas/" + n}

    def err(desc: str) -> Dict[str, Any]:
        return {"description": desc, "content": {"application/json": {"schema": ref("Error")}}}

    return {
        "openapi": "3.1.0",
        "info": {"title": "장학금 추천 API", "version": API_VERSION,
                 "description": "학생 프로필을 받아 장학금 공고와 비교한 판정(충족·조건부·미달)과 후속 질문을 돌려준다. 서버는 학생 정보를 저장하지 않는다. 설명: docs/api_contract_v1.md, docs/result_format_v1.md"},
        "security": [{"ApiKey": []}],
        "paths": {
            "/v1/recommend": {"post": {"operationId": "recommend", "summary": "판정과 후속 질문 (1차와 이어 답하는 호출 모두)",
                "requestBody": {"required": True, "content": {"application/json": {"schema": ref("RecommendRequest")}}},
                "responses": {"200": {"description": "판정 결과", "content": {"application/json": {"schema": ref("RecommendResponse")}}},
                              "400": err("요청 형식 오류(INVALID_REQUEST) 또는 프로필 항목 간 모순(INVALID_PROFILE)"), "401": err("API 키 없음 또는 불일치(UNAUTHORIZED)"),
                              "413": err("요청이 너무 큼(PAYLOAD_TOO_LARGE)"), "500": err("서버 오류(INTERNAL, SERVER_CONFIG)"), "503": err("공고 DB를 읽을 수 없음(UNAVAILABLE)")}}},
            "/v1/chat": {"post": {"operationId": "chat", "summary": "공고 질문 챗봇 (CHAT_ENABLED=true 일 때만). 판정은 바꾸지 않고 공고 원문을 근거로 설명한다",
                "requestBody": {"required": True, "content": {"application/json": {"schema": ref("ChatRequest")}}},
                "responses": {"200": {"description": "답변", "content": {"application/json": {"schema": ref("ChatResponse")}}},
                              "400": err("요청 형식 오류(INVALID_REQUEST) 또는 프로필 항목 간 모순(INVALID_PROFILE)"), "401": err("API 키 없음 또는 불일치(UNAUTHORIZED)"),
                              "404": err("챗봇 기능이 꺼져 있음(CHAT_DISABLED)"), "413": err("요청이 너무 큼(PAYLOAD_TOO_LARGE)"),
                              "429": err("동시에 처리하는 질문이 많음(BUSY)"), "500": err("서버 오류(INTERNAL)"),
                              "503": err("공고 DB를 읽을 수 없음(UNAVAILABLE) 또는 AI 응답 실패(CHAT_UNAVAILABLE). 추천 API 는 영향 없음")}}},
            "/v1/notices/{notice_id}/files/{seq}": {"get": {"operationId": "noticeFile", "summary": "챗봇 출처의 원본 첨부파일 내려받기. 포털 서버가 키로 대신 불러 사용자에게 전달한다 (키를 브라우저에 두지 않는다)",
                "parameters": [{"name": "notice_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "공고 id (예: EXT01:153381)"},
                               {"name": "seq", "in": "path", "required": True, "schema": {"type": "string"}, "description": "응답 `file.seq`"}],
                "responses": {"200": {"description": "파일 (attachment)", "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}},
                              "401": err("API 키 없음 또는 불일치(UNAUTHORIZED)"), "404": err("열려 있는 공고의 연결된 파일이 아님, 또는 서버에 파일이 없음(NOT_FOUND)"),
                              "500": err("서버 오류(INTERNAL)"), "503": err("공고 DB를 읽을 수 없음(UNAVAILABLE)")}}},
            "/v1/health": {"get": {"operationId": "health", "summary": "상태 점검 (6-B 에서 구현)", "responses": {"200": {"description": "상태", "content": {"application/json": {"schema": ref("Health")}}}}}},
            "/v1/meta": {"get": {"operationId": "meta", "summary": "버전과 한도 (6-B 에서 구현)", "responses": {"200": {"description": "정보", "content": {"application/json": {"schema": ref("Meta")}}}}}}},
        "components": {"securitySchemes": {"ApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}, "schemas": schemas}}


def openapi_json() -> str:
    return json.dumps(openapi(), ensure_ascii=False, indent=1) + "\n"


if __name__ == "__main__":
    if "--write" in sys.argv:
        (Path(__file__).resolve().parents[2] / "docs" / "openapi_v1.json").write_text(openapi_json(), encoding="utf-8")
        print("docs/openapi_v1.json 갱신")
    else:
        print(openapi_json(), end="")
