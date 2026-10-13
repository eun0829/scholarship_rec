# -*- coding: utf-8 -*-
"""외부02 어댑터: 로컬 흉내 서버로 목록(무로그인), 지연 로그인, 세션 만료, 상세, POST 다운로드를 검증한다."""
import io
import json
import logging
import re
import zipfile
from datetime import date
from pathlib import Path

import pytest

from scholarship.common.config import Settings
from scholarship.common.http import DownloadError
from scholarship.sources import ext02_parse as P
from scholarship.sources.ext02 import (DetailError, Ext02Adapter, Ext02Config, LoginError, MAX_RELOGINS)
from tests.fake_dreamspon import FakeDreamspon, detail_html, list_html

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 7)
UID, PW = "student@example.com", "Pa$$w0rd!"


def zip_bytes(**members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in members.items():
            z.writestr(name, text)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    import scholarship.common.http as http
    monkeypatch.setattr(http.time, "sleep", lambda s: None)       # 다운로드 재시도 대기 제거


@pytest.fixture
def make(tmp_path):
    made = []

    def build(rows=None, details=None, files=None, list_pages=None, cfg_over=None, known=None, **adapter_kw):
        pages = list_pages if list_pages is not None else {(1, 1): list_html(rows or [])}
        srv = FakeDreamspon(UID, PW, pages, details or {}, files or {})
        made.append(srv)
        cfg = Ext02Config()
        cfg.base_url, cfg.delay_sec, cfg.timeout_sec = srv.base, 0.0, 5
        cfg.ordby = [1]
        for k, v in (cfg_over or {}).items():
            setattr(cfg, k, v)
        st = Settings()
        st.dreamspon_id, st.dreamspon_password = UID, PW
        sleeps = []
        adapter = Ext02Adapter(st, cfg, known_state=known, today=TODAY, sleep=sleeps.append, **adapter_kw)
        adapter.sleeps = sleeps
        made.append(adapter)
        return srv, adapter

    yield build
    for m in made:
        m.close()


ROW = lambda idx, title="장학 {}", tags=("장학금", "대학생"), dday=3, state="모집중": (idx, title.format(idx), "재단" + idx, list(tags), dday, state)


# ================================================================== 목록 (로그인 불필요)
def test_list_is_fetched_without_login_and_filtered(make):
    rows = [ROW("1"), ROW("2", tags=("장학금", "고등학생")), ROW("3", tags=("대출지원", "대학생")), ROW("4", state="모집완료"),
            ROW("5", dday=0, state="마감임박"), ROW("6", dday=9)]
    srv, ad = make(rows=rows)
    sel = ad.select(ad.crawl_list())
    assert [r.idx for r in sel] == ["5", "1", "6"]                      # 마감 빠른 순. 고등학생/대출/모집완료는 제외
    assert srv.login_calls == 0 and all(not r[3] for r in srv.requests)  # 로그인 없이 읽음
    assert ad.deactivation_safe is True


def test_list_paging_ordby_merge_and_dedupe(make):
    pages = {(1, 1): list_html([ROW("1"), ROW("2")], max_page=3), (1, 2): list_html([ROW("3")], max_page=3),
             (1, 3): list_html([ROW("4")], max_page=3), (2, 1): list_html([ROW("2"), ROW("9")], max_page=1)}
    srv, ad = make(list_pages=pages, cfg_over={"ordby": [1, 2]})
    assert sorted(r.idx for r in ad.crawl_list()) == ["1", "2", "3", "4", "9"]       # ordby 1 의 3쪽 + ordby 2 의 1쪽, idx 중복 제거
    gets = [(r[2].get("ordby"), r[2].get("page")) for r in srv.requests if r[1] == "/scholarship/list.html"]
    assert gets == [("1", None), ("1", "2"), ("1", "3"), ("2", None)]


def test_list_max_pages_limit_and_default_ordby_has_no_param(make):
    pages = {(0, p): list_html([ROW(str(p))], max_page=50) for p in range(1, 51)}
    srv, ad = make(list_pages=pages, cfg_over={"ordby": [0], "max_pages": 4})
    assert len(ad.crawl_list()) == 4
    assert all("ordby" not in r[2] for r in srv.requests)


def test_list_server_errors_are_retried_then_reported_unsafe(make):
    srv, ad = make(rows=[ROW("1")])
    srv.list_failures = 2                                              # 500 두 번 후 정상
    assert [r.idx for r in ad.crawl_list()] == ["1"] and ad.deactivation_safe is True and len(ad.sleeps) >= 2
    srv.list_failures = 99                                             # 계속 실패
    srv2, ad2 = make(rows=[ROW("1")])
    srv2.list_failures = 99
    assert ad2.crawl_list() == [] and ad2.deactivation_safe is False   # 사라진 글 정리를 하면 안 되는 상태


def test_empty_list_is_not_safe_for_deactivation(make):
    srv, ad = make(rows=[])
    assert ad.crawl_list() == [] and ad.deactivation_safe is False


def test_select_with_real_sample_rows():
    rows = P.parse_list((FIX / "dreamspon_list.html").read_text(encoding="utf-8"))
    ad = Ext02Adapter(Settings(), Ext02Config(), today=TODAY)
    assert [r.idx for r in ad.select(rows)] == ["9211", "9210", "9218", "9217", "9213", "9212"]          # D-day 오름차순, 같으면 idx 큰 순
    ad.cfg.include_tags = []
    assert len(ad.select(rows)) == 12 and all(r.kind == "장학금" for r in ad.select(rows))
    ad.cfg.include_kinds = []
    assert len(ad.select(rows)) == 15


# ================================================================== 로그인
def test_login_request_matches_site_script(make):
    srv, ad = make(rows=[ROW("1")])
    ad.login()
    form = srv.last_login_form
    js = (FIX / "dreamspon_login.html").read_text(encoding="utf-8")
    block = re.search(r"data:\s*\{(.*?)\}", js, re.S).group(1)
    site_keys = set(re.findall(r"(\w+)\s*:", block))                  # 사이트 JS(dlogin)가 보내는 항목과 정확히 같아야 한다
    assert set(form) == site_keys == {"mode", "userid", "idsaveCheck", "autoLogin", "pageReferer", "userpw"}
    assert re.search(r'url\s*=\s*"/process/checkuser\.html"', js)
    assert form["mode"] == "login" and form["autoLogin"] == "N" and form["idsaveCheck"] == "Y" and form["pageReferer"] == ""
    assert form["userid"] == UID and form["userpw"] == PW and ad._logged_in


def test_login_failure_stops_without_retry_and_hides_password(make, caplog):
    srv, ad = make(rows=[ROW("1")])
    ad.s.dreamspon_password = "wrong-" + PW
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(LoginError) as e1:
            ad.login()
        with pytest.raises(LoginError) as e2:
            ad.login()                                                 # 한 번 실패하면 다시 시도하지 않는다
    assert srv.login_calls == 1 and e1.value is e2.value
    assert "wrong-" not in str(e1.value) and PW not in caplog.text and "wrong-" not in caplog.text
    assert "다시 시도하지 않습니다" in str(e1.value)


def test_missing_credentials_never_touch_the_network(make):
    srv, ad = make(rows=[ROW("1")])
    ad.s.dreamspon_id = ""
    with pytest.raises(LoginError, match="DREAMSPON_ID"):
        ad.fetch_detail("1")
    assert srv.requests == []


def test_login_unexpected_responses(make):
    srv, ad = make(rows=[ROW("1")])
    srv.pw = "other"                                                   # 서버가 거부 -> checkyn N
    with pytest.raises(LoginError, match="로그인 실패"):
        ad.login()
    bad = Ext02Adapter(ad.s, ad.cfg, today=TODAY)
    bad.cfg.login_path = "/process/nonjson.html"                        # 200 이지만 JSON 이 아닌 응답
    with pytest.raises(LoginError, match="JSON"):
        bad.login()
    bad2 = Ext02Adapter(ad.s, ad.cfg, today=TODAY)
    bad2.cfg.login_path = "/nope"
    with pytest.raises(LoginError, match="HTTP 404"):
        bad2.login()
    unreachable = Ext02Adapter(ad.s, Ext02Config(base_url="http://127.0.0.1:9", timeout_sec=2), today=TODAY)
    with pytest.raises(LoginError) as e:
        unreachable.login()
    assert PW not in str(e.value) and UID not in str(e.value)


# ================================================================== 상세 / 세션
def test_detail_requires_login_and_login_is_lazy(make):
    srv, ad = make(rows=[ROW("1")], details={"1": detail_html("1")})
    assert srv.login_calls == 0
    d = ad.fetch_detail("1")
    assert srv.login_calls == 1 and d.has_content and d.idx == "1" and d.org_name == "테스트재단"
    view = [r for r in srv.requests if r[1] == "/scholarship/view.html"]
    assert view[0][3] is True                                          # 로그인한 세션 쿠키로 요청
    ad.fetch_detail("1")
    assert srv.login_calls == 1                                        # 한 번만 로그인


def test_unreadable_detail_after_fresh_login_is_an_error_not_a_relogin_loop(make):
    srv, ad = make(rows=[ROW("1")], details={})                         # 로그인은 되는데 본문이 안 보이는 계정/화면
    with pytest.raises(DetailError, match="상세 내용을 읽지 못했습니다"):
        ad.fetch_detail("1")
    assert srv.login_calls == 1


def test_expired_session_relogs_in_once_and_continues(make):
    details = {str(i): detail_html(str(i)) for i in range(1, 5)}
    srv, ad = make(rows=[ROW("1")], details=details)
    srv.expire_after_details = 2                                        # 두 번째 상세 이후 세션 만료
    assert [ad.fetch_detail(str(i)).idx for i in (1, 2, 3, 4)] == ["1", "2", "3", "4"]
    assert srv.login_calls == 2 and ad._relogins == 1


def test_relogin_is_capped(make):
    srv, ad = make(rows=[ROW("1")], details={"1": detail_html("1")})
    ad.fetch_detail("1")
    for _ in range(MAX_RELOGINS):                                       # 세션이 계속 풀리는 서버
        srv.sessions.clear()
        ad.fetch_detail("1")
    srv.sessions.clear()
    with pytest.raises(DetailError):
        ad.fetch_detail("1")
    assert srv.login_calls == 1 + MAX_RELOGINS                          # 요청마다 로그인하지 않는다


# ================================================================== fetch_notices
def test_fetch_notices_builds_source_notice(make):
    rows = [ROW("7", dday=3)]
    files = [("2026 장학 공고문.hwp", "/webData/x/111mod공고문.hwp"), ("신청서.zip", "/webData/x/222mod신청서.zip")]
    srv, ad = make(rows=rows, details={"7": detail_html("7", title="테스트 장학", files=files, original="https://example.org/n/7")})
    [n] = ad.fetch_notices(30)
    assert (n.src_cd, n.src_id, n.title, n.dept_nm, n.stub) == ("EXT02", "7", "테스트 장학", "테스트재단", False)
    assert n.origin_url == "https://example.org/n/7"
    assert n.posted_date == date(2026, 10, 1)                           # 신청 시작일 (오늘보다 미래면 오늘)
    assert [a.org_file_nm for a in n.attachments] == ["2026 장학 공고문.hwp", "신청서.zip"]
    a0 = n.attachments[0]
    assert re.fullmatch(r"1-[0-9a-f]{6}", a0.file_seq) and a0.extra == {"file1": "/webData/x/111mod공고문.hwp", "idx": "7"}
    assert n.attachments[1].file_seq != a0.file_seq and a0.download_url == srv.base + "/process/fileDown.html"
    assert n.hints["apply_end"] == "2026-10-31" and n.hints["recruit_count"] == "5명" and n.hints["apply_type"] == "DIRECT"
    assert n.source_fields["period"]["source"] == "DETAIL" and n.source_fields["eligibility"]["gpa"]["lines"] == ["직전학기 평점 3.0/4.5 이상"]
    assert "신청자격" in n.body_html and "user@example.com" not in n.body_html + json.dumps(n.source_fields, ensure_ascii=False)
    assert "드림스폰이 공고문을 요약" in n.source_note and n.hash_basis


def test_future_start_date_is_not_used_as_posted_date(make):
    sec = {"신청기간": ["2026. 11. 01. ~ 2026. 11. 30."]}
    srv, ad = make(rows=[ROW("1")], details={"1": detail_html("1", sections=sec)})
    assert ad.fetch_notices(30)[0].posted_date == TODAY


def test_hash_basis_ignores_period_and_view_count_but_not_content(make):
    def basis(period="2026. 10. 01. ~ 2026. 10. 31.", extra=None, hit="7"):
        sec = dict({"신청기간": [period], "선발인원": ["총 5명"], "지원대상": ["대학생"]}, **(extra or {}))
        srv, ad = make(rows=[ROW("1")], details={"1": detail_html("1", sections=sec).replace("조회 : 7", "조회 : " + hit)})
        return ad.fetch_notices(30)[0].hash_basis
    base = basis()
    assert basis(period="2026. 10. 01. ~ 2026. 12. 31.") == base and basis(hit="9999") == base        # 기간/조회수 변화는 무시
    assert basis(extra={"신청자격": ["[기타조건]", "- 새 조건"]}) != base                              # 내용이 바뀌면 달라짐


def test_known_completed_items_are_stubs_and_never_trigger_login(make):
    srv, ad = make(rows=[ROW("1"), ROW("2")], details={"1": detail_html("1"), "2": detail_html("2")},
                   known=lambda cd, i: {"status_cd": "EXTRACTED", "use_yn": "Y"})
    out = ad.fetch_notices(30)
    assert [(n.src_id, n.stub) for n in out] == [("2", True), ("1", True)] and srv.login_calls == 0      # 같은 D-day 면 idx 큰 글 먼저
    assert not [r for r in srv.requests if r[1] == "/scholarship/view.html"]


def test_known_incomplete_forced_or_refreshed_items_are_fetched(make):
    states = {"1": {"status_cd": "EXTRACTED"}, "2": {"status_cd": "FAILED"}, "3": {"status_cd": "COLLECTED"}}
    details = {i: detail_html(i) for i in "123"}
    srv, ad = make(rows=[ROW("1"), ROW("2"), ROW("3")], details=details, known=lambda cd, i: states.get(i))
    assert {n.src_id: n.stub for n in ad.fetch_notices(30)} == {"1": True, "2": False, "3": False}      # 미완료는 이어서 처리
    srv, ad = make(rows=[ROW("1")], details={"1": detail_html("1")}, known=lambda cd, i: states["1"], refresh=True)
    assert ad.fetch_notices(30)[0].stub is False                                                      # --refresh
    srv, ad = make(rows=[ROW("1")], details={"1": detail_html("1")}, known=lambda cd, i: states["1"], force=True)
    assert ad.fetch_notices(30)[0].stub is False                                                      # --force


def test_limit_and_only_restrict_detail_requests(make):
    rows = [ROW(str(i), dday=i) for i in range(1, 6)]
    details = {str(i): detail_html(str(i)) for i in range(1, 6)}
    srv, ad = make(rows=rows, details=details, limit=2)
    assert [n.src_id for n in ad.fetch_notices(30)] == ["1", "2"]                                     # 마감 임박순 앞 2건만 상세 조회
    assert len([r for r in srv.requests if r[1] == "/scholarship/view.html"]) == 2
    srv, ad = make(rows=rows, details=details, only=["4"])
    assert [n.src_id for n in ad.fetch_notices(30)] == ["4"]


def test_detail_failure_is_isolated_and_item_stays_seen(make):
    srv, ad = make(rows=[ROW("1", dday=1), ROW("2", dday=2)], details={"2": detail_html("2")})        # 1번은 본문이 안 읽힘
    out = ad.fetch_notices(30)
    assert [(n.src_id, n.stub) for n in out] == [("1", True), ("2", False)] and ad.failed_ids == ["1"]


def test_login_failure_aborts_the_whole_run(make):
    srv, ad = make(rows=[ROW("1")], details={"1": detail_html("1")})
    ad.s.dreamspon_password = "bad"
    with pytest.raises(LoginError):
        ad.fetch_notices(30)


# ================================================================== 첨부 다운로드 (POST)
def test_download_attachment_posts_form_with_session(make, tmp_path):
    fp = "/webData/x/111mod공고문.txt"
    srv, ad = make(rows=[ROW("1")], details={"1": detail_html("1", files=[("공고문.txt", fp)])}, files={fp: b"hello attachment"})
    [n] = ad.fetch_notices(30)
    dest = tmp_path / "out" / "a.txt"
    assert ad.download_attachment(n.attachments[0], dest, 10_000) == 16 and dest.read_bytes() == b"hello attachment"
    post = [r for r in srv.requests if r[1] == "/process/fileDown.html"][-1]
    assert post[0] == "POST" and post[2] == {"file1": fp, "idx": "1"} and post[3] is True


def test_download_logs_in_lazily_and_relogs_in_when_session_expires(make, tmp_path):
    fp = "/webData/x/1mod자료.zip"
    payload = zip_bytes(**{"a.txt": "x"})
    srv, ad = make(rows=[ROW("1")], details={"1": detail_html("1", files=[("자료.zip", fp)])}, files={fp: payload})
    [n] = ad.fetch_notices(30)
    srv.sessions.clear()                                                # 로그인이 풀린 뒤 다운로드 -> HTML 응답 -> 재로그인
    assert ad.download_attachment(n.attachments[0], tmp_path / "z.zip", 10_000_000) == len(payload)
    assert srv.login_calls == 2
    fresh = Ext02Adapter(ad.s, ad.cfg, today=TODAY)                     # 아직 로그인하지 않은 어댑터는 다운로드 때 로그인
    n.attachments[0].extra["idx"] = "1"
    assert fresh.download_attachment(n.attachments[0], tmp_path / "z2.zip", 10_000_000) == len(payload) and srv.login_calls == 3
    fresh.close()


def test_download_rejects_wrong_type_missing_and_oversized(make, tmp_path):
    fp = "/webData/x/1mod가짜.pdf"
    srv, ad = make(rows=[ROW("1")], details={"1": detail_html("1", files=[("가짜.pdf", fp), ("없음.pdf", "/webData/none.pdf")])},
                   files={fp: b"<html>not a pdf</html>"})
    [n] = ad.fetch_notices(30)
    with pytest.raises(DownloadError):                                  # HTML 응답 (로그인 페이지가 아닌 가짜 PDF 내용도 형식 검사에서 거부)
        ad.download_attachment(n.attachments[0], tmp_path / "a.pdf", 10_000)
    with pytest.raises(DownloadError, match="HTTP 404"):
        ad.download_attachment(n.attachments[1], tmp_path / "b.pdf", 10_000)
    srv.files[fp] = b"%PDF-1.4" + b"x" * 5000
    with pytest.raises(DownloadError, match="크기"):
        ad.download_attachment(n.attachments[0], tmp_path / "c.pdf", 1000)
    assert not list((tmp_path).glob("*.part"))


def test_body_image_download_is_restricted_to_the_site(make, tmp_path):
    srv, ad = make(rows=[ROW("1")])
    assert ad.download_body_image("/data/scholarship/202609/a.png", tmp_path / "img.png", 10_000) > 0
    with pytest.raises(ValueError):
        ad.download_body_image("https://evil.example.com/a.png", tmp_path / "x.png", 10_000)


# ================================================================== 요청 간격
def test_requests_are_spaced_by_delay(make):
    srv, ad = make(rows=[ROW("1")], details={"1": detail_html("1")}, cfg_over={"delay_sec": 1.5})
    ad.crawl_list()
    ad.fetch_detail("1")                                                # 목록 1 + 로그인 1 + 상세 1 = 3번 요청
    assert len(ad.sleeps) == 2 and all(1.0 < s <= 1.5 for s in ad.sleeps)       # 첫 요청 뒤부터 간격을 둠


def test_config_loading_and_env_override(monkeypatch, tmp_path):
    cfg = Ext02Config.load()
    assert cfg.ordby == [1, 2, 3] and cfg.include_tags == ["대학생"] and cfg.include_kinds == ["장학금"] and cfg.use_summary_images is False
    assert cfg.apply_type_keywords["SCHOOL"][0] == "학교경유" and cfg.target_keywords["GRAD"] == ["대학원"]
    f = tmp_path / "e.json"
    f.write_text(json.dumps({"_메모": "무시", "ordby": [2], "max_pages": 3, "모르는키": 1, "include_tags": ["대학생", "전문대"]}), encoding="utf-8")
    monkeypatch.setenv("EXT02_BASE_URL", "http://localhost:9999/")
    c2 = Ext02Config.load(f)
    assert (c2.ordby, c2.max_pages, c2.include_tags, c2.base_url) == ([2], 3, ["대학생", "전문대"], "http://localhost:9999")
