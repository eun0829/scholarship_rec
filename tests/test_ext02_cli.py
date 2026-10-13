# -*- coding: utf-8 -*-
"""run_ext02.py 와 점검 명령(set_secret --test-dreamspon)을 흉내 서버에 붙여 처음부터 끝까지 확인한다."""
import io
import json
import zipfile
from datetime import date
from pathlib import Path

import pytest

import run_ext02
import set_secret
from scholarship.common.config import Settings, bootstrap
from scholarship.common import secret_store as ss
from scholarship.common.repository import MemoryRepository
from scholarship.common.pipeline import Pipeline
from scholarship.sources.ext02 import Ext02Adapter, Ext02Config
from tests.fake_dreamspon import DEFAULT_SECTIONS, FakeDreamspon, detail_html, list_html
from tests.test_pipeline import FakeAI, GOOD

UID, PW = "student@example.com", "Pa$$w0rd!"
TODAY = date(2026, 10, 7)
AI_OK = dict(GOOD, apply_type="DIRECT", apply_start="2026-10-01", apply_end="2026-10-31", org_deadline=None, summary="요약")


def zip_bytes(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in members.items():
            z.writestr(name, text)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    import scholarship.common.http as http
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    for k in ("DREAMSPON_ID", "DREAMSPON_PASSWORD", "DB_PASSWORD", "AI_API_KEY", "EXT02_BASE_URL", "EXT02_CONFIG_FILE", "EXT02_ATTACH_TO_AI"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SCHOLARSHIP_NAS_ROOT", str(tmp_path / "nas"))


@pytest.fixture
def site(monkeypatch, tmp_path):
    guide_zip = zip_bytes({"2026 장학 선발 공고문.txt": "공고문-자격요건", "지원서 양식.txt": "양식-체크리스트"})
    files = {"/webData/x/1mod공고.zip": guide_zip, "/webData/x/2mod안내.txt": b"txt-attachment"}
    rows = [("11", "장학 11", "재단11", ["장학금", "대학생"], 2, "마감임박"), ("12", "장학 12", "재단12", ["장학금", "대학생"], 5, "모집중"),
            ("13", "고교 장학", "재단13", ["장학금", "고등학생"], 1, "마감임박")]
    details = {"11": detail_html("11", title="장학 11", files=[("공고 자료.zip", "/webData/x/1mod공고.zip")]),
               "12": detail_html("12", title="장학 12", files=[("안내.txt", "/webData/x/2mod안내.txt")]), "13": detail_html("13")}
    srv = FakeDreamspon(UID, PW, {(1, 1): list_html(rows), (2, 1): list_html([]), (3, 1): list_html([])}, details, files)
    cfg = tmp_path / "ext02.json"
    cfg.write_text(json.dumps({"ordby": [1], "delay_sec": 0, "timeout_sec": 5}), encoding="utf-8")
    monkeypatch.setenv("EXT02_BASE_URL", srv.base)
    monkeypatch.setenv("EXT02_CONFIG_FILE", str(cfg))
    yield srv
    srv.close()


def creds(monkeypatch, pw=PW):
    monkeypatch.setenv("DREAMSPON_ID", UID)
    monkeypatch.setenv("DREAMSPON_PASSWORD", pw)


NOENV = ["--env", "/nonexistent/.env"]


# ================================================================== --list-only (계정/DB/AI 불필요)
def test_list_only_needs_no_secrets_and_never_logs_in(site, capsys):
    assert run_ext02.main(["--list-only"] + NOENV) == 0
    out = capsys.readouterr().out
    assert "조회 3건 -> 후보 2건" in out and "대상이 대학생 아님 1건" in out
    assert "idx 11 | D-2" in out and "idx 12 | D-5" in out and "idx 13" not in out
    assert site.login_calls == 0 and all(not r[3] for r in site.requests)


def test_list_only_reports_failure_when_site_returns_nothing(site, capsys):
    site.list_pages = {}
    assert run_ext02.main(["--list-only"] + NOENV) == 1


# ================================================================== 전체 흐름 (dry-run, AI 없음)
def test_full_run_dry_no_ai_collects_details_and_attachments(site, monkeypatch, tmp_path, capsys):
    creds(monkeypatch)
    dump = tmp_path / "dump"
    assert run_ext02.main(["--dry-run", "--no-ai", "--dump-dir", str(dump)] + NOENV) == 0
    err = capsys.readouterr().err
    assert "대상 2건" in err and "수집만 2" in err and "첨부 성공 3 / 실패 0 / 건너뜀 0" in err and "토큰 입력 0" in err
    assert site.login_calls == 1                                                      # 로그인은 한 번
    assert PW not in err and UID not in err
    assert (dump / "ext02_11_body.txt").read_text(encoding="utf-8").count("신청자격") >= 1
    assert "공고문-자격요건" in "".join(p.read_text(encoding="utf-8") for p in dump.glob("ext02_11_att*"))
    assert not list(dump.glob("ext02_13*"))                                           # 고등학생 대상은 수집하지 않음
    nas = Path(tmp_path / "nas" / "ext02")
    assert sorted(p.name for p in nas.glob("11/*.zip")) and sorted(p.name for p in nas.glob("12/*.txt"))
    posts = [r for r in site.requests if r[1] == "/process/fileDown.html"]
    assert {(r[2]["idx"], r[2]["file1"]) for r in posts} == {("11", "/webData/x/1mod공고.zip"), ("12", "/webData/x/2mod안내.txt")}


def test_limit_and_only_options(site, monkeypatch, capsys):
    creds(monkeypatch)
    assert run_ext02.main(["--dry-run", "--no-ai", "--limit", "1"] + NOENV) == 0
    assert "대상 1건" in capsys.readouterr().err
    assert len([r for r in site.requests if r[1] == "/scholarship/view.html"]) == 1
    assert run_ext02.main(["--dry-run", "--no-ai", "--only", "12"] + NOENV) == 0
    assert "대상 1건" in capsys.readouterr().err


# ================================================================== 오류 경로
def test_login_failure_exits_with_guidance_and_never_leaks_password(site, monkeypatch, capsys):
    creds(monkeypatch, pw="Wr0ng-Secret-pw")
    assert run_ext02.main(["--dry-run", "--no-ai"] + NOENV) == 2
    err = capsys.readouterr().err
    assert "드림스폰 로그인 문제" in err and "--test-dreamspon" in err and "Wr0ng-Secret-pw" not in err
    assert site.login_calls == 1 and not [r for r in site.requests if r[1] == "/scholarship/view.html"]


def test_missing_credentials_message_mentions_set_secret(site, capsys):
    assert run_ext02.main(["--dry-run", "--no-ai"] + NOENV) == 2
    assert "DREAMSPON_ID" in capsys.readouterr().err
    assert site.login_calls == 0


def test_missing_db_password_or_ai_key_is_reported_before_any_request(site, capsys):
    assert run_ext02.main(NOENV) == 2
    err = capsys.readouterr().err
    assert "DB_PASSWORD" in err and "AI_API_KEY" in err and site.requests == []


# ================================================================== 증분: 두 번째 실행은 로그인도 하지 않는다
def build(site, repo, **kw):
    cfg = Ext02Config.load()
    st = Settings()
    st.dreamspon_id, st.dreamspon_password = UID, PW
    st.nas_root = Path(st.nas_root)
    return st, Ext02Adapter(st, cfg, known_state=repo.get_notice_state, today=TODAY, sleep=lambda s: None, **kw)


def test_second_run_skips_detail_and_login_then_refresh_updates_period_without_ai(site, tmp_path):
    repo = MemoryRepository()
    ai = FakeAI(AI_OK)
    st, ad = build(site, repo)
    st.nas_root = tmp_path / "nas2"
    summ = Pipeline(st, ad, repo, ai, today=TODAY).run()
    assert summ.extracted == 2 and site.login_calls == 1 and len(ai.calls) == 2
    ad.close()

    views = lambda: len([r for r in site.requests if r[1] == "/scholarship/view.html"])
    before = views()
    st, ad2 = build(site, repo)
    st.nas_root = tmp_path / "nas2"
    summ = Pipeline(st, ad2, repo, ai, today=TODAY).run()
    assert summ.unchanged == 2 and site.login_calls == 1 and views() == before and len(ai.calls) == 2     # 로그인도 상세 조회도 AI 도 없음
    ad2.close()

    site.details["11"] = detail_html("11", title="장학 11", files=[("공고 자료.zip", "/webData/x/1mod공고.zip")],
                                     sections=dict(DEFAULT_SECTIONS, 신청기간=["2026. 10. 01. ~ 2026. 11. 20."]),
                                     info_period="2026. 10. 01. (목) ~ 2026. 11. 20. (금)")        # 마감 연장
    st, ad3 = build(site, repo, refresh=True)
    st.nas_root = tmp_path / "nas2"
    summ = Pipeline(st, ad3, repo, ai, today=TODAY).run()
    assert summ.period_updated == 1 and summ.unchanged == 1 and len(ai.calls) == 2                       # 연장만 반영, AI 호출 없음
    assert repo.notices[("EXT02", "11")]["apply_end"] == date(2026, 11, 20) and site.login_calls == 2
    ad3.close()


def test_full_scope_deactivation_when_item_disappears_from_list(site, tmp_path):
    repo = MemoryRepository()
    ai = FakeAI(AI_OK)
    st, ad = build(site, repo)
    st.nas_root = tmp_path / "nas3"
    Pipeline(st, ad, repo, ai, today=TODAY).run()
    ad.close()
    site.list_pages = {(1, 1): list_html([("11", "장학 11", "재단11", ["장학금", "대학생"], 2, "마감임박")])}      # 12번이 목록에서 사라짐
    st, ad2 = build(site, repo)
    summ = Pipeline(st, ad2, repo, ai, today=TODAY).run()
    assert summ.deactivated == 1 and repo.notices[("EXT02", "12")]["use_yn"] == "N" and repo.notices[("EXT02", "11")]["use_yn"] == "Y"
    ad2.close()
    site.list_failures = 99                                                                              # 목록 조회가 실패하면 정리하지 않는다
    repo2 = MemoryRepository()
    repo2.upsert_notice("EXT02", "99", {"status_cd": "EXTRACTED", "use_yn": "Y"})
    st, ad3 = build(site, repo2)
    summ = Pipeline(st, ad3, repo2, ai, today=TODAY).run()
    assert summ.deactivated == 0 and repo2.notices[("EXT02", "99")]["use_yn"] == "Y"
    ad3.close()


# ================================================================== set_secret --test-dreamspon / 비밀 항목
def adapter_for(site, uid=UID, pw=PW):
    def factory(st):
        cfg = Ext02Config.load()
        return Ext02Adapter(st, cfg, today=TODAY, sleep=lambda s: None)
    st = Settings()
    st.dreamspon_id, st.dreamspon_password = uid, pw
    return st, factory


def test_set_secret_test_dreamspon_success_and_failures(site, capsys):
    st, factory = adapter_for(site)
    assert set_secret.test_dreamspon(st, factory) is True
    out = capsys.readouterr().out
    assert "[성공] 목록 조회 (로그인 불필요): 후보 2건" in out and "[성공] 로그인 (계정 st***)" in out
    assert "[성공] 상세 본문 읽기 (idx=11, 고정 항목 7개, 첨부 1개)" in out and PW not in out and UID not in out
    st, factory = adapter_for(site, pw="nope-nope")
    assert set_secret.test_dreamspon(st, factory) is False
    out = capsys.readouterr().out
    assert "[실패] 로그인" in out and "nope-nope" not in out
    st = Settings()
    assert set_secret.test_dreamspon(st, factory) is False and "비어 있는 항목" in capsys.readouterr().out
    site.list_pages = {(1, 1): list_html([])}
    st, factory = adapter_for(site)
    assert set_secret.test_dreamspon(st, factory) is True and "후보가 없어" in capsys.readouterr().out      # 후보가 없어도 로그인은 확인


def test_dreamspon_secrets_are_stored_in_secret_file_and_loaded(tmp_path, monkeypatch):
    assert "DREAMSPON_ID" in ss.ALLOWED_KEYS and "DREAMSPON_PASSWORD" in ss.ALLOWED_KEYS
    f = tmp_path / "secrets.env"
    ss.write(str(f), {"DREAMSPON_ID": UID, "DREAMSPON_PASSWORD": PW})
    assert ss.read(str(f)) == {"DREAMSPON_ID": UID, "DREAMSPON_PASSWORD": PW}
    bootstrap(str(f), str(tmp_path / "none.env"))
    st = Settings.from_env()
    assert (st.dreamspon_id, st.dreamspon_password) == (UID, PW)
    assert PW not in repr(st) and UID not in repr(st)                                                       # repr 에 노출되지 않음
    assert set_secret.LABELS["DREAMSPON_PASSWORD"] and set_secret.LABELS["DREAMSPON_ID"]


def test_attach_mode_env_setting(monkeypatch):
    assert Settings.from_env().ext02_attach_to_ai == "guide_only"
    for raw, expected in (("guide_and_form", "guide_and_form"), (" NONE ", "none"), ("엉뚱한값", "guide_only")):
        monkeypatch.setenv("EXT02_ATTACH_TO_AI", raw)
        assert Settings.from_env().ext02_attach_to_ai == expected
