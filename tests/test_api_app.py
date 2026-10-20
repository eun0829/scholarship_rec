# -*- coding: utf-8 -*-
"""6-B HTTP 계층: 인증, 본문 크기, 오류 본문, 캐시·stale, health/meta, 시험 화면, 로그에 입력값이 남지 않음."""
import json
import logging
from datetime import date

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from scholarship.api.app import create_app  # noqa: E402
from scholarship.api.cache import CachedSource  # noqa: E402
from scholarship.common.config import Settings  # noqa: E402
from scholarship.recommend.file_source import FileSource  # noqa: E402

FIX = "tests/fixtures/ext01_v3"
TODAY = "2026-10-13"
PROFILE = {"level": "UNIV", "status": "ENROLLED", "grade": 4, "semesters_completed": 7, "gpa_last": 3.8, "gpa_scale": 4.5, "credits_last": 15,
           "income_bracket": 2, "nationality": "KR", "residence_sido": "경기도", "residence_sigungu": "수원시"}


class Flaky:
    """list_candidates 가 실패하도록 켜고 끌 수 있는 공급원."""
    def __init__(self):
        self.inner, self.fail, self.calls, self.closed = FileSource(FIX), False, 0, 0

    def list_candidates(self, today):
        self.calls += 1
        if self.fail:
            raise ConnectionError("ORA-12541 secret-dsn-host")
        return self.inner.list_candidates(today)

    def close(self):
        self.closed += 1


def make(key="k1", require=True, demo=False, source=None, **kw):
    s = Settings()
    s.api_key, s.api_require_key, s.api_enable_demo = key, require, demo
    for k, v in kw.items():
        setattr(s, k, v)
    src = source or Flaky()
    app = create_app(s, lambda: src)
    return TestClient(app), app, src


def post(c, body=None, key="k1", raw=None, headers=None):
    h = dict(headers or {})
    if key is not None:
        h["X-API-Key"] = key
    if raw is not None:
        return c.post("/v1/recommend", content=raw, headers=h)
    return c.post("/v1/recommend", json=body, headers=h)


def test_recommend_ok_echoes_request_id_and_counts():
    c, _, _ = make()
    r = post(c, {"request_id": "abc-1", "profile": PROFILE, "today": TODAY})
    assert r.status_code == 200 and r.json()["request_id"] == "abc-1"
    assert set(r.json()["counts"]) >= {"match", "conditional", "no_match"}


def test_api_key_required_and_compared():
    c, _, _ = make()
    body = {"profile": PROFILE, "today": TODAY}
    for key in (None, "", "wrong", "k1 "):
        r = post(c, body, key=key)
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"
    assert post(c, body, key="k1").status_code == 200
    assert c.get("/v1/meta").status_code == 401 and c.get("/v1/meta", headers={"X-API-Key": "k1"}).status_code == 200


def test_missing_server_key_fails_closed_but_dev_mode_opens():
    c, _, _ = make(key="")
    r = post(c, {"profile": PROFILE, "today": TODAY}, key="anything")
    assert r.status_code == 500 and r.json()["error"]["code"] == "SERVER_CONFIG"
    c2, _, _ = make(key="", require=False)
    assert post(c2, {"profile": PROFILE, "today": TODAY}, key=None).status_code == 200


def test_bad_json_and_bad_structure_use_the_error_body():
    c, _, _ = make()
    r = post(c, raw=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_REQUEST" and r.json()["error"]["fields"][0]["field"] == "$"
    r = post(c, raw="한".encode("cp949"), headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    r = post(c, {"request_id": "r9", "profile": "x"})
    assert r.status_code == 400 and r.json()["error"]["request_id"] == "r9" and r.json()["error"]["fields"]


def test_payload_limit_by_header_and_by_stream():
    c, _, _ = make(api_max_body_bytes=200)
    big = json.dumps({"profile": {"major": "가" * 300}}).encode("utf-8")
    r = post(c, raw=big, headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"

    def gen():                                              # Content-Length 없이 조금씩 보낸다
        for _ in range(10):
            yield b" " * 50
    r = c.post("/v1/recommend", content=gen(), headers={"X-API-Key": "k1", "Content-Type": "application/json"})
    assert r.status_code == 413


def test_unauthorized_request_does_not_read_or_judge_the_body():
    c, _, src = make()
    assert post(c, {"profile": PROFILE}, key="nope").status_code == 401 and src.calls == 0


def test_cache_reuses_the_notice_list_until_ttl():
    c, app, src = make()
    t = [0.0]
    app.state.cache._clock = lambda: t[0]
    body = {"profile": PROFILE, "today": TODAY}
    post(c, body); post(c, body)
    assert src.calls == 1
    t[0] = 299; post(c, body); assert src.calls == 1
    t[0] = 301; post(c, body); assert src.calls == 2


def test_source_failure_serves_stale_then_503_when_nothing_cached():
    c, app, src = make()                                    # health 가 실제 오늘 날짜로 캐시를 보므로 요청도 오늘 날짜로 한다 (날짜가 바뀌면 깨지던 시험)
    t = [0.0]
    app.state.cache._clock = lambda: t[0]
    body = {"request_id": "s1", "profile": PROFILE, "today": date.today().isoformat()}
    assert post(c, body).status_code == 200
    src.fail, t[0] = True, 400                              # 만료 + DB 장애 -> 이전 목록으로 응답
    r = post(c, body)
    assert r.status_code == 200 and app.state.cache.status(date.today())["stale"] is True and src.closed == 1   # 끊긴 연결은 닫는다
    h = c.get("/v1/health")
    assert h.status_code == 200 and h.json()["status"] == "degraded"
    t[0] = 400 + 21601                                      # 이전 목록도 너무 오래됨
    r = post(c, body)
    assert r.status_code == 503 and r.json()["error"]["code"] == "UNAVAILABLE" and r.json()["error"]["request_id"] == "s1"
    assert "secret-dsn-host" not in r.text and c.get("/v1/health").status_code == 503
    src.fail = False                                        # 복구되면 다시 정상
    assert post(c, body).status_code == 200 and c.get("/v1/health").json()["status"] == "ok"


def test_unexpected_error_is_internal_without_details(monkeypatch):
    c, app, _ = make()
    monkeypatch.setattr(app.state.recommender, "recommend_step", lambda *a, **k: (_ for _ in ()).throw(ValueError("학생 3.9 수원")))
    r = post(c, {"request_id": "e1", "profile": PROFILE, "today": TODAY})
    assert r.status_code == 500 and r.json()["error"]["code"] == "INTERNAL" and "수원" not in r.text and r.json()["error"]["request_id"] == "e1"


def test_logs_never_contain_student_input(caplog):
    c, _, src = make()
    caplog.set_level(logging.DEBUG, logger="scholarship")
    secret = {"level": "UNIV", "major": "비밀전공XYZ", "residence_sido": "경기도", "residence_sigungu": "수원시", "gpa_last": 3.777}
    post(c, {"request_id": "log1", "profile": secret, "today": TODAY})
    src.fail = True
    app_cache = c.app.state.cache
    app_cache.invalidate()
    post(c, {"request_id": "log2", "profile": secret, "today": TODAY})
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "log1" in text and "status=200" in text
    for leak in ("비밀전공XYZ", "3.777", "수원시", "secret-dsn-host"):
        assert leak not in text


def test_health_needs_no_key_and_shows_no_student_data():
    c, _, _ = make()
    r = c.get("/v1/health")
    j = r.json()
    assert r.status_code == 200 and j["status"] == "ok" and j["notices"]["loaded"] >= 1 and j["api_version"]


def test_meta_and_openapi_file():
    c, _, _ = make()
    j = c.get("/v1/meta", headers={"X-API-Key": "k1"}).json()
    assert j["limits"]["max_body_bytes"] == 262144 and j["limits"]["question_rounds"] == 3 and j["recommend_categories"] == ["OUT"]
    o = c.get("/v1/openapi.json")
    assert o.status_code == 200 and json.loads(o.text)["openapi"].startswith("3.1")
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404      # 자동 문서는 열지 않는다


def test_demo_is_off_by_default_and_works_when_on():
    c, _, _ = make()
    assert c.get("/demo").status_code == 404 and c.post("/demo/recommend", content=b"x").status_code == 404
    c, _, _ = make(demo=True)
    page = c.get("/demo")
    assert page.status_code == 200 and "학생 정보" in page.text and "__TEMPLATE__" not in page.text
    r = c.post("/demo/recommend", content=open("sample_student.txt", "rb").read()).json()
    assert r["ok"] is True and "장학금 추천 결과" in r["text"]
    cd = r["cards"]
    assert cd["symbols"]["LINK"] == "▶" and cd["verdict_title"]["cond"] == "조건부" and "충족" in cd["summary"]
    assert [i["verdict"] for i in cd["items"]] == sorted((i["verdict"] for i in cd["items"]), key={"match": 0, "cond": 1, "no": 2}.get)     # 충족 -> 조건부 -> 미달 순
    assert all(i["index"] >= 1 and i["total"] >= i["index"] and "title" in i and "url" in i for i in cd["items"])
    assert "[한눈에 보기]" in r["text"]
    assert c.post("/demo/recommend", content=b"x" * 70000).status_code == 413


def test_cached_source_deepcopies_and_keeps_few_dates():
    src = Flaky()
    cs = CachedSource(lambda: src, ttl=60)
    a = cs.list_candidates(date(2026, 10, 13))
    a[0]["title"] = "바꿈"
    assert cs.list_candidates(date(2026, 10, 13))[0]["title"] != "바꿈"       # 호출한 쪽이 고쳐도 캐시는 그대로
    for d in range(14, 20):
        cs.list_candidates(date(2026, 10, d))
    assert len(cs._entries) == 3


def test_demo_chat_screen_has_bubbles_focus_and_lists_excluded_notices():
    from scholarship.api import app as A
    from scholarship.api.demo_view import demo_payload
    for needle in ('class="cb"', 'id="focus"', "function addMsg", "대화 지우기", "typing", 'id="split"', "askbtn", "grid-template-columns", "preventScroll", "scrollIntoView", "function diagText", "원본 첨부파일", "/demo/files/", "이 공고에 대해서만 답변합니다"):
        assert needle in A.DEMO_HTML
    res = {"result_version": 1, "today": "2026-10-20", "profile_notes": {"dropped_sensitive_keys": [], "ignored_keys": [], "assumptions": []},
           "counts": {"candidates": 1, "after_filter": 1, "after_dedupe": 1, "match": 0, "conditional": 0, "no_match": 1},
           "recommended": [], "conditional": [], "excluded": [{"id": "EXT02:9211", "src_id": "9211", "title": "대전시 인재육성장학", "reasons": ["지역 조건 미달"]}]}
    out = demo_payload("ok", res)
    assert out["notice_ids"] == ["EXT02:9211"] and out["notices"][0]["verdict"] == "미달"
