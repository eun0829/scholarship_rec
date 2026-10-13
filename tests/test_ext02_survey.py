# -*- coding: utf-8 -*-
"""외부02 학생 정보 조건 조사 도구 (최근 N건 선별, 상세 수집, 1차 통계, 이어받기, 공고문 첨부)."""
import io
import json
import zipfile
from datetime import date
from pathlib import Path

import pytest

import run_ext02_survey
from scholarship.sources import ext02_parse as P
from scholarship.sources import ext02_survey as S
from scholarship.sources.ext02 import Ext02Adapter, Ext02Config
from scholarship.common.config import Settings
from tests.fake_dreamspon import DEFAULT_SECTIONS, FakeDreamspon, detail_html, list_html

UID, PW = "student@example.com", "Pa$$w0rd!"
TODAY = date(2026, 10, 7)


def make_rows(n=40, start=100):
    out = []
    for k in range(n):
        if k % 5 == 4:
            tags = ["대출지원", "대학생"]
        elif k % 4 == 3:
            tags = ["장학금", "고등학생"]
        else:
            tags = ["장학금", "대학생"]
        out.append((str(start - k), "장학 {}".format(start - k), "재단{}".format(start - k), tags, k % 7, "모집중" if k < 10 else "모집완료"))
    return out


def zip_bytes(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in members.items():
            z.writestr(name, text)
    return buf.getvalue()


def sections_for(idx):
    i = int(idx)
    base = dict(DEFAULT_SECTIONS)
    if i % 3 == 0:
        base["신청자격"] = ["[성적기준] 직전학기 평점 3.0 이상", "- 직전학기 12학점 이상 이수", "[지역기준] 거주지", "- 경기도 거주 3학년 이상 재학생"]
    elif i % 3 == 1:
        base["신청자격"] = ["[소득기준] 소득", "- 소득 3구간 이하", "[기타조건]", "- 기초생활수급자 또는 차상위 계층, 한부모 가정"]
    else:
        base["신청자격"] = ["[어학능력] 영어", "- TOEIC 700점 이상", "[추천사항] 지도교수", "- 지도교수 추천서 제출"]
    return base


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    import scholarship.common.http as http
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    for k in ("DREAMSPON_ID", "DREAMSPON_PASSWORD", "EXT02_BASE_URL", "EXT02_CONFIG_FILE"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def site(monkeypatch, tmp_path):
    rows = make_rows()
    pages = {(5, 1): list_html(rows[:15], max_page=3), (5, 2): list_html(rows[15:30], max_page=3), (5, 3): list_html(rows[30:], max_page=3),
             (4, 1): list_html([(str(60 - k), "과거 장학 {}".format(60 - k), "재단", ["장학금", "대학생"], 0, "모집완료") for k in range(10)])}
    details = {r[0]: detail_html(r[0], title=r[1], org=r[2], sections=sections_for(r[0])) for r in rows}
    for k in range(10):
        details[str(60 - k)] = detail_html(str(60 - k), title="과거 장학 {}".format(60 - k), sections=sections_for(60 - k))
    srv = FakeDreamspon(UID, PW, pages, details, {})
    cfg = tmp_path / "ext02.json"
    cfg.write_text(json.dumps({"delay_sec": 0, "timeout_sec": 5}), encoding="utf-8")
    monkeypatch.setenv("EXT02_BASE_URL", srv.base)
    monkeypatch.setenv("EXT02_CONFIG_FILE", str(cfg))
    srv.rows = rows
    yield srv
    srv.close()


def creds(monkeypatch, pw=PW):
    monkeypatch.setenv("DREAMSPON_ID", UID)
    monkeypatch.setenv("DREAMSPON_PASSWORD", pw)


def adapter(site):
    st = Settings()
    st.dreamspon_id, st.dreamspon_password = UID, PW
    cfg = Ext02Config.load()
    return Ext02Adapter(st, cfg, today=TODAY, sleep=lambda s: None)


NOENV = ["--env", "/nonexistent/.env"]
views = lambda srv: [r for r in srv.requests if r[1] == "/scholarship/view.html"]


# ================================================================== 선별
def test_select_recent_uses_kind_audience_and_includes_closed():
    rows = [P.ListRow(idx=str(r[0]), title=r[1], org=r[2], tags=r[3], dday=r[4], state=r[5]) for r in make_rows()]
    sel = S.select_recent(rows, 5, ["장학금"], ["대학생"])
    pop = sorted((int(r.idx) for r in rows if r.kind == "장학금" and "대학생" in r.audiences), reverse=True)
    assert [int(r.idx) for r in sel] == pop[:5] == [100, 99, 98, 95, 94]
    assert all(r.kind == "장학금" and "대학생" in r.audiences for r in sel)
    far = S.select_recent(rows, 100, ["장학금"], ["대학생"])
    assert len(far) == 24 and "모집완료" in {r.state for r in far}                      # 마감된 공고도 포함, 대상이 부족하면 있는 만큼
    assert len(S.select_recent(rows, 100, [], [])) == 40                                  # 종류/대상 조건을 비우면 전부


def test_collect_candidates_stops_when_enough_and_continues_when_not(site):
    ad = adapter(site)
    rows, notes = S.collect_candidates(ad, [5, 4, 0], 10, ["장학금"], ["대학생"], 30)
    assert len(notes) == 1 and "ordby=5" in notes[0] and "최근순 정렬 맞음" in notes[0] and len(rows) == 40
    assert not [r for r in site.requests if r[2].get("ordby") == "4"]                    # 충분히 모였으니 다음 정렬 방식은 훑지 않음
    site.requests.clear()
    ad2 = adapter(site)
    rows, notes = S.collect_candidates(ad2, [5, 4, 0], 30, ["장학금"], ["대학생"], 30)      # 5번에서 24건뿐 -> 4번까지 가면 34건이라 거기서 멈춤
    assert len(notes) == 2 and "ordby=4" in notes[1] and len(rows) == 50
    assert any(r[2].get("ordby") == "4" for r in site.requests) and not any(r[2].get("ordby") == "0" for r in site.requests)
    ad3 = adapter(site)
    rows, notes = S.collect_candidates(ad3, [5, 4, 0], 40, ["장학금"], ["대학생"], 30)      # 34건으로도 부족 -> 끝까지(0번도) 훑음
    assert len(notes) == 3 and "ordby=0" in notes[2] and "조사 대상 0건" in notes[2]
    ad3.close()
    ad.close(); ad2.close()


def test_collect_reports_when_sort_is_not_recent_first(site):
    site.list_pages[(7, 1)] = list_html([("5", "a", "o", ["장학금", "대학생"], 0, "모집중"), ("9", "b", "o", ["장학금", "대학생"], 0, "모집중")])
    ad = adapter(site)
    _, notes = S.collect_candidates(ad, [7], 5, ["장학금"], ["대학생"], 30)
    assert "최근순 정렬 아님" in notes[0]
    ad.close()


# ================================================================== 개념 키워드 / 통계
def test_concept_hits_find_student_info_needs():
    h = S.concept_hits("직전학기 평점 3.0 이상, 소득 3구간 이하, 기초생활수급자, 경기도 거주 3학년, TOEIC 700, 지도교수 추천서, 한부모 가정, 선원의 자녀")
    for k in ("last_term", "gpa", "income", "basic", "residence", "grade", "language", "recommend", "single_parent", "parent_job"):
        assert k in h, k
    assert "소득" in h["income"] and "3학년" in h["grade"]
    assert S.concept_hits("") == {} and "gender" not in S.concept_hits("직전학기 평점 3.0 이상")
    keys = [k for k, _, _ in S.CONCEPTS]
    assert len(keys) == len(set(keys))


def test_summarize_counts_and_examples():
    def rec(idx, state, concepts, levels=("UNIV",), apply="DIRECT", tags=None, att=()):
        return {"idx": idx, "title": "t" + idx, "org": "o", "state": state, "period": {"kind": "RANGE"}, "target": {"levels": list(levels)},
                "method": {"apply_type_hint": apply, "headline": "온라인 접수"}, "eligibility": {k: {"tag": k, "lines": ["서울 거주"], "label": ""} for k in (tags or [])},
                "concepts": concepts, "attachments": list(att)}
    recs = [rec("1", "모집중", {"gpa": "평점 3.0", "residence": "서울 거주"}, tags=["gpa", "region"], att=["공고문.zip"]),
            rec("2", "모집완료", {"gpa": "평점 3.5"}, levels=("UNIV", "GRAD"), apply="SCHOOL", tags=["gpa"]), rec("3", "모집완료", {}, tags=[])]
    txt = S.summarize(recs, failures=["9"])
    assert "최근 공고 3건" in txt and "상세를 읽지 못한 공고 1건: 9" in txt
    assert "모집완료 2" in txt and "UNIV: 2 (67%)" in txt and "UNIV,GRAD: 1 (33%)" in txt
    assert "평점·성적 (gpa): 2 (67%)" in txt and "[1] …평점 3.0…" in txt
    assert "SCHOOL: 1 (33%)" in txt and "신청자격 항목이 비어 있는 공고: 1 (33%)" in txt and "첨부가 있는 공고 1 (33%)" in txt
    assert S.summarize([]).startswith("=== 드림스폰 최근 공고 0건")                       # 빈 입력에서도 오류 없음


# ================================================================== 실행 (CLI)
def test_list_only_needs_no_login(site, capsys):
    assert run_ext02_survey.main(["--list-only", "--count", "8"] + NOENV) == 0
    out = capsys.readouterr().out
    assert "조사 대상 8건" in out and "idx 100" in out and "idx 100 | 모집중" in out
    assert site.login_calls == 0 and all(not r[3] for r in site.requests)


def test_full_survey_creates_files_and_includes_closed(site, monkeypatch, tmp_path, capsys):
    creds(monkeypatch)
    out = tmp_path / "survey"
    assert run_ext02_survey.main(["--count", "15", "--out", str(out)] + NOENV) == 0
    printed = capsys.readouterr().out
    assert "상세 15건 수집 (실패 0건)" in printed and site.login_calls == 1
    recs = S.load_records(out / "ext02_survey_notices.jsonl")
    assert len(recs) == 15 and recs[0]["idx"] == "100" and "모집완료" in {r["state"] for r in recs}      # 마감 포함, 최근순
    txt = (out / "ext02_survey_notices.txt").read_text(encoding="utf-8")
    assert txt.count("### [") == 15 and "### [1] idx=100 | 모집중 | 장학 100" in txt and "신청자격" in txt and "직전학기 평점 3.0 이상" in txt
    summary = (out / "ext02_survey_summary.txt").read_text(encoding="utf-8")
    assert "최근 공고 15건" in summary and "소득구간·분위" in summary and "어학 성적·능력" in summary and "지도교수" in summary
    assert PW not in txt + summary and UID not in txt + summary and "user@example.com" not in txt                    # 계정·이메일이 결과에 없음
    assert "list_tags" in recs[0] and recs[0]["list_tags"] == ["장학금", "대학생"]                                   # 목록 태그는 참고용으로만 보관


def test_resume_only_fetches_missing_details(site, monkeypatch, tmp_path):
    creds(monkeypatch)
    out = tmp_path / "survey"
    assert run_ext02_survey.main(["--count", "5", "--out", str(out)] + NOENV) == 0
    assert len(views(site)) == 5
    site.requests.clear()
    assert run_ext02_survey.main(["--count", "8", "--out", str(out), "--resume"] + NOENV) == 0
    assert len(views(site)) == 3 and len(S.load_records(out / "ext02_survey_notices.jsonl")) == 8                    # 이미 받은 5건은 건너뜀
    site.requests.clear()
    assert run_ext02_survey.main(["--count", "8", "--out", str(out)] + NOENV) == 0                                  # --resume 없이 다시 -> 처음부터
    assert len(views(site)) == 8 and len(S.load_records(out / "ext02_survey_notices.jsonl")) == 8


def test_detail_failure_is_isolated(site, monkeypatch, tmp_path, capsys):
    creds(monkeypatch)
    del site.details["99"]                                                                                          # 99번은 본문이 안 읽힘
    out = tmp_path / "survey"
    assert run_ext02_survey.main(["--count", "4", "--out", str(out)] + NOENV) == 0
    recs = S.load_records(out / "ext02_survey_notices.jsonl")
    assert [r["idx"] for r in recs] == ["100", "98", "95"]
    assert "상세를 읽지 못한 공고 1건: 99" in (out / "ext02_survey_summary.txt").read_text(encoding="utf-8")


def test_all_kinds_option_and_too_few_targets(site, monkeypatch, tmp_path, capsys):
    creds(monkeypatch)
    out = tmp_path / "survey"
    assert run_ext02_survey.main(["--count", "40", "--kinds", "", "--audience", "", "--out", str(out), "--list-only"] + NOENV) == 0
    assert "조사 대상 40건" in capsys.readouterr().out
    assert run_ext02_survey.main(["--count", "100", "--out", str(out), "--list-only"] + NOENV) == 0                  # 대상이 부족해도 있는 만큼
    assert "조사 대상 34건" in capsys.readouterr().out                                                                # 5번 24건 + 4번 10건


def test_login_failure_exit_code_and_no_password_leak(site, monkeypatch, tmp_path, capsys):
    creds(monkeypatch, pw="Wr0ng-Secret")
    assert run_ext02_survey.main(["--count", "3", "--out", str(tmp_path / "s")] + NOENV) == 2
    err = capsys.readouterr().err
    assert "드림스폰 로그인 문제" in err and "Wr0ng-Secret" not in err and site.login_calls == 1
    assert not (tmp_path / "s" / "ext02_survey_notices.txt").exists()


def test_survey_never_writes_to_db_or_requires_ai_secrets(site, monkeypatch, tmp_path):
    creds(monkeypatch)                                                                                              # DB_PASSWORD, AI_API_KEY 없이도 실행됨
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    monkeypatch.delenv("AI_API_KEY", raising=False)
    assert run_ext02_survey.main(["--count", "2", "--out", str(tmp_path / "s")] + NOENV) == 0


# ================================================================== (선택) 공고문 첨부
def test_attachments_option_collects_guides_only(site, monkeypatch, tmp_path):
    creds(monkeypatch)
    z = zip_bytes({"2026 장학 선발 공고문.txt": "공고문-추가조건: 1년 이상 거주", "지원서 양식.txt": "양식-체크리스트", "합격자 명단.txt": "명단"})
    site.files = {"/webData/x/1.zip": z, "/webData/x/2.txt": "요강-단독파일".encode("utf-8"), "/webData/x/3.txt": "양식-단독".encode("utf-8")}
    site.details["100"] = detail_html("100", title="장학 100", files=[("자료 모음.zip", "/webData/x/1.zip"), ("2026 선발 요강.txt", "/webData/x/2.txt"),
                                                                    ("신청서 양식.txt", "/webData/x/3.txt"), ("없는 파일.txt", "/webData/none.txt")])
    out = tmp_path / "survey"
    assert run_ext02_survey.main(["--count", "2", "--out", str(out), "--attachments"] + NOENV) == 0
    g = (out / "ext02_survey_guides.txt").read_text(encoding="utf-8")
    assert "공고문-추가조건: 1년 이상 거주" in g and "요강-단독파일" in g
    assert "양식-체크리스트" not in g and "양식-단독" not in g and "명단" not in g                                      # 양식·제외 파일은 받지 않음/뺌
    assert "### idx=100" in g and "idx=99" not in g                                                                  # 공고문이 없는 글은 파일에 나오지 않음
    recs = S.load_records(out / "ext02_survey_notices.jsonl")
    assert {x["role"] for x in recs[0]["guide_texts"]} <= {"GUIDE", "UNKNOWN"} and "guide_texts" in recs[1]
    posts = [r[2]["file1"] for r in site.requests if r[1] == "/process/fileDown.html"]
    assert "/webData/x/3.txt" not in posts                                                                           # 양식 파일은 내려받지도 않음


def test_without_attachments_option_nothing_is_downloaded(site, monkeypatch, tmp_path):
    creds(monkeypatch)
    site.details["100"] = detail_html("100", title="장학 100", files=[("공고문.txt", "/webData/x/9.txt")])
    site.files = {"/webData/x/9.txt": b"x"}
    assert run_ext02_survey.main(["--count", "2", "--out", str(tmp_path / "s")] + NOENV) == 0
    assert not [r for r in site.requests if r[1] == "/process/fileDown.html"]
    assert not (tmp_path / "s" / "ext02_survey_guides.txt").exists()
