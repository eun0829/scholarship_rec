# -*- coding: utf-8 -*-
"""중복 묶기, 서비스(필터·경고·정렬), 후보 조회(Memory/Oracle/File), CLI."""
import json
import os
import shutil
from datetime import date, datetime

import pytest

from scholarship.common.ai import schema
from scholarship.common.config import Settings
from scholarship.common.repository import MemoryRepository, OracleRepository, build_candidate
from scholarship.recommend.dedupe import group_key, merge_candidates
from scholarship.recommend.file_source import FileSource
from scholarship.recommend.profile import ProfileError
from scholarship.recommend.service import Recommender, format_text

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
TODAY = date(2026, 10, 6)


def elig(**kw):
    base = {"category": "OUT", "apply_end": "2026-10-18", "target_levels": ["UNIV"], "scholarship_name": "기본 장학금"}
    base.update(kw)
    d, _ = schema.normalize(base, None)
    d["schema_version"] = 2
    return d


def cand(src_id, e=None, **kw):
    e = e or elig()
    c = {"src_cd": "EXT01", "src_id": str(src_id), "title": e.get("scholarship_name") or "제목", "origin_url": "https://x/{}".format(src_id),
         "posted_date": date(2026, 9, 20), "category": "OUT", "apply_type": "DIRECT", "apply_start": None,
         "apply_end": date(2026, 10, 18), "org_deadline": None, "summary": e.get("summary"), "status": "EXTRACTED",
         "elig": e, "uncertain": {}}
    c.update(kw)
    return c


class Stub:
    def __init__(self, cands):
        self.cands = cands

    def list_candidates(self, today):
        return list(self.cands)


def run(cands, profile=None, settings=None, today=TODAY):
    profile = profile if profile is not None else {"level": "UNIV", "status": "ENROLLED"}
    return Recommender(Stub(cands), settings or Settings()).recommend(profile, today=today)


# ================================================================== 중복 묶기
def test_group_key_ignores_boilerplate_but_keeps_year():
    a = cand(1, elig(scholarship_name="2026년 하반기 봄내장학생", foundation="(재)춘천인재육성장학재단"))
    b = cand(2, elig(scholarship_name="2026년 하반기 봄내장학생 선발 기간연장 안내", foundation="춘천인재육성장학재단"))
    c = cand(3, elig(scholarship_name="2027년 하반기 봄내장학생", foundation="(재)춘천인재육성장학재단"))
    d = cand(4, elig(scholarship_name="2026년 하반기 봄내장학생", foundation="다른재단"))
    assert group_key(a) == group_key(b) != group_key(c) and group_key(a) != group_key(d)


def test_merge_keeps_latest_notice_and_reports_merged_from():
    old = cand(100, elig(scholarship_name="2026년 성취장학생", foundation="대전청년내일재단"), posted_date=date(2026, 9, 8),
               apply_end=date(2026, 10, 7))
    new = cand(200, elig(scholarship_name="2026년 성취장학생 선발계획 안내(기간연장)", foundation="대전청년내일재단"),
               posted_date=date(2026, 9, 28), apply_end=date(2026, 10, 14))
    other = cand(300, elig(scholarship_name="다른 장학", foundation="다른재단"), posted_date=date(2026, 9, 1))
    out = merge_candidates([old, other, new])
    assert [c["src_id"] for c in out] == ["200", "300"]                                      # 최신순, 대표는 연장 공고
    assert out[0]["merged_from"] == [{"src_id": "100", "title": "2026년 성취장학생", "apply_end": "2026-10-07"}]
    assert out[1]["merged_from"] == []
    assert old.get("merged_from") is None                                                    # 입력은 변경하지 않음


def test_merge_tie_break_by_src_id_and_missing_names():
    a, b = cand(5, elig(scholarship_name="X 장학", foundation="F"), posted_date=None), cand(9, elig(scholarship_name="X 장학", foundation="F"), posted_date=None)
    assert merge_candidates([a, b])[0]["src_id"] == "9"
    no_name = cand(7, elig(scholarship_name=None), title="이름 없는 공고")
    assert group_key(no_name)[1] == "이름없는"                                                        # 장학명이 없으면 제목에서 '공고' 같은 군더더기를 뺀다
    only_boilerplate = cand(8, elig(scholarship_name="장학금 신청 안내"))
    assert group_key(only_boilerplate)[1] != ""                                                     # 군더더기만 남으면 제목 전체로 대체


# ================================================================== 서비스: 필터 / 경고 / 정렬
def test_counts_filters_and_sorting():
    easy = elig(scholarship_name="쉬운 장학", gpa={"min": 3.0, "scale": 4.5, "basis": "LAST_SEMESTER"})
    r = run([
        cand(1, elig(scholarship_name="A 장학", other_conditions=["TOEIC 750"]), apply_end=date(2026, 10, 20)),     # 조건부(확인 1)
        cand(2, easy, apply_end=date(2026, 10, 30)),                                                              # 충족
        cand(3, elig(scholarship_name="C 장학"), apply_end=date(2026, 10, 12)),                                   # 충족, 더 임박
        cand(4, elig(scholarship_name="D 장학", target_levels=["HIGH"]), apply_end=date(2026, 10, 25)),            # 미달
        cand(5, elig(scholarship_name="E 장학"), apply_end=date(2026, 10, 1)),                                    # 이미 마감 -> 제외
        cand(6, elig(scholarship_name="F 장학", category="IN"), category="IN"),                                    # 교내 -> 제외
    ], profile={"level": "UNIV", "status": "ENROLLED", "gpa_last": 3.5})
    assert r["counts"] == {"candidates": 6, "after_filter": 4, "after_dedupe": 4, "match": 2, "conditional": 1, "no_match": 1}
    assert [i["src_id"] for i in r["recommended"]] == ["3", "2"]                                       # 마감 빠른 순
    assert [i["src_id"] for i in r["conditional"]] == ["1"] and r["excluded"][0]["src_id"] == "4"
    assert "대상 학교급이 아님" in r["excluded"][0]["reasons"][0]
    assert r["recommended"][0]["days_left"] == 6 and r["recommended"][0]["verdict_label"] == "충족"


def test_conditional_sorted_by_fewer_confirmations_first():
    many = elig(scholarship_name="M", other_conditions=["a", "b", "c"])
    few = elig(scholarship_name="F", other_conditions=["a"])
    r = run([cand(1, many, apply_end=date(2026, 10, 10)), cand(2, few, apply_end=date(2026, 10, 30))])
    assert [i["src_id"] for i in r["conditional"]] == ["2", "1"]


def test_max_results_limit():
    cands = [cand(i, elig(scholarship_name="N{}".format(i)), apply_end=date(2026, 10, 7 + i)) for i in range(1, 6)]
    s = Settings()
    s.recommend_max_results = 3
    r = run(cands, settings=s)
    assert len(r["recommended"]) == 3 and r["counts"]["match"] == 5                                    # 개수 제한은 목록에만 적용


def test_deadline_rules_and_warnings():
    s = Settings()
    r = run([cand(1, elig(scholarship_name="오늘"), apply_end=date(2026, 10, 6)),
             cand(2, elig(scholarship_name="내일"), apply_end=date(2026, 10, 8)),
             cand(3, elig(scholarship_name="여유"), apply_end=date(2026, 10, 9), apply_start=date(2026, 10, 7))], settings=s)
    w = {i["src_id"]: [x["code"] for x in i["warnings"]] for i in r["recommended"]}
    assert w["1"] == ["LAST_DAY"] and w["2"] == ["URGENT"] and w["3"] == ["NOT_STARTED"]               # 임박 기준 2일: 3번은 3일 남음
    s.recommend_min_days_left = 1                                                                      # 오늘 마감 제외
    r = run([cand(1, elig(scholarship_name="오늘"), apply_end=date(2026, 10, 6))], settings=s)
    assert r["counts"]["after_filter"] == 0
    s2 = Settings()
    s2.recommend_urgent_days = 5
    r = run([cand(3, elig(scholarship_name="여유"), apply_end=date(2026, 10, 9))], settings=s2)
    assert [x["code"] for x in r["recommended"][0]["warnings"]] == ["URGENT"]


def test_warnings_letter_review_verify_legacy_orgdeadline():
    e = elig(apply_how="구글폼 제출, 총장 추천서 필요")
    unc = {"uncertain": [{"field": "gpa", "reason": "표와 FAQ가 다름", "level": "verify"},
                         {"field": "amount", "reason": "참고", "level": "note"}]}
    r = run([cand(1, e, status="NEED_REVIEW", uncertain=unc, org_deadline=date(2026, 10, 25)),
             cand(2, {k: v for k, v in elig(scholarship_name="구형").items() if k != "schema_version"})])
    items = {i["src_id"]: i for i in r["recommended"]}
    codes = [w["code"] for w in items["1"]["warnings"]]
    assert codes == ["LETTER", "ORG_DEADLINE", "NEED_REVIEW", "VERIFY"]
    assert "표와 FAQ가 다름" in items["1"]["warnings"][3]["message"] and "참고" not in json.dumps(items["1"]["warnings"], ensure_ascii=False)
    assert [w["code"] for w in items["2"]["warnings"]] == ["LEGACY"] and items["2"]["schema_version"] == 1


def test_need_review_can_be_excluded_and_dedupe_can_be_disabled():
    s = Settings()
    s.recommend_include_need_review = False
    r = run([cand(1, status="NEED_REVIEW"), cand(2, elig(scholarship_name="다른"))], settings=s)
    assert [i["src_id"] for i in r["recommended"]] == ["2"]
    a = cand(1, elig(scholarship_name="같은 장학", foundation="F"), posted_date=date(2026, 9, 1))
    b = cand(2, elig(scholarship_name="같은 장학 안내", foundation="F"), posted_date=date(2026, 9, 20))
    assert run([a, b])["counts"]["after_dedupe"] == 1
    s2 = Settings()
    s2.recommend_dedupe = False
    assert run([a, b], settings=s2)["counts"]["after_dedupe"] == 2


def test_merged_notice_warning_mentions_deadline_change():
    a = cand(1, elig(scholarship_name="봄내 장학", foundation="F"), posted_date=date(2026, 9, 17), apply_end=date(2026, 10, 7))
    b = cand(2, elig(scholarship_name="봄내 장학 (기간연장)", foundation="F"), posted_date=date(2026, 10, 1), apply_end=date(2026, 10, 14))
    item = run([a, b])["recommended"][0]
    msg = [w["message"] for w in item["warnings"] if w["code"] == "MERGED"][0]
    assert item["src_id"] == "2" and "(1)" in msg and "2026-10-07 -> 2026-10-14" in msg


def test_variants_are_exposed_in_result():
    e = elig(scholarship_name="유형 장학", variants=[
        {"name": "성적우수", "amount": "100만원", "gpa": {"min": 4.0, "scale": 4.5, "basis": "LAST_SEMESTER"}},
        {"name": "저소득", "income": {"eligible_types": ["기초생활수급자"]}}])
    it = run([cand(1, e)], profile={"level": "UNIV", "gpa_last": 3.0, "is_basic_livelihood": True})["recommended"][0]
    assert [v["name"] for v in it["matched_variants"]] == ["저소득"] and it["matched_variants"][0]["verdict_label"] == "충족"
    assert it["unmatched_variants"][0]["name"] == "성적우수" and "성적 미달" in it["unmatched_variants"][0]["reason"]


def test_no_personal_identifiers_in_result_and_assumptions_reported():
    r = run([cand(1)], profile={"name": "홍길동", "학번": "20260001", "email": "a@b.c", "level": "UNIV", "gpa_last": 3.3})
    dumped = json.dumps(r, ensure_ascii=False)
    assert "홍길동" not in dumped and "20260001" not in dumped and "a@b.c" not in dumped
    assert r["profile_notes"]["dropped_sensitive_keys"] == ["email", "name", "학번"] and r["profile_notes"]["assumptions"]
    with pytest.raises(ProfileError):
        run([cand(1)], profile={"level": "외계인"})


def test_school_defaults_fill_missing_profile_values_only():
    e = elig(scholarship_name="경기 장학", region={"school_location": ["시흥시"]}, enrollment={"excluded_status": ["사이버대학"]})
    plain = run([cand(1, e)])["recommended"] + run([cand(1, e)])["conditional"]
    assert plain[0]["verdict"] == "CONDITIONAL"                                                       # 학교 정보가 없으면 확인 필요
    s = Settings()
    s.recommend_school_type, s.recommend_school_sido, s.recommend_school_sigungu = "GENERAL", "경기도", "시흥시"
    assert run([cand(1, e)], settings=s)["recommended"][0]["verdict"] == "MATCH"
    r = run([cand(1, e)], profile={"level": "UNIV", "school_sigungu": "천안시"}, settings=s)           # 프로필 값이 우선
    assert r["excluded"][0]["reasons"][0].startswith("학교 소재지 조건 미달")
    s.recommend_school_type = "엉뚱"
    with pytest.raises(ProfileError, match="환경설정"):
        run([cand(1, e)], settings=s)


def test_format_text_is_readable_and_truncates_long_confirm_lists():
    e = elig(scholarship_name="긴 장학", other_conditions=["조건{}".format(i) for i in range(10)])
    text = format_text(run([cand(1, e), cand(2, elig(scholarship_name="탈락", target_levels=["HIGH"]))]), show_excluded=True)
    assert "충족 0 / 조건부 1 / 미달 1" in text and "[조건부 (확인 필요)] 1건" in text
    assert "외 확인 항목 4개" in text and "[미달] 1건" in text and "탈락" in text and "원문: https://x/1" in text


# ================================================================== 후보 조회: Memory / Oracle / File
def _store(repo, sid, e, **kw):
    row = {"title": e.get("scholarship_name"), "origin_url": "https://x/" + sid, "posted_date": date(2026, 9, 20), "category_cd": "OUT",
           "apply_type_cd": "DIRECT", "apply_start": None, "apply_end": date(2026, 10, 18), "org_deadline": None,
           "summary": "요약", "status_cd": "EXTRACTED", "elig_json": json.dumps(e, ensure_ascii=False), "uncertain_json": None}
    row.update(kw)
    repo.upsert_notice("EXT01", sid, row)


def test_memory_repository_candidates_follow_the_same_rules():
    repo = MemoryRepository()
    _store(repo, "1", elig(scholarship_name="정상"))
    _store(repo, "2", elig(scholarship_name="수집만"), status_cd="COLLECTED")
    _store(repo, "3", elig(scholarship_name="교내"), category_cd="IN")
    _store(repo, "4", elig(scholarship_name="마감"), apply_end=date(2026, 10, 5))
    _store(repo, "5", elig(scholarship_name="오늘 마감"), apply_end=date(2026, 10, 6))
    _store(repo, "6", elig(scholarship_name="깨진 JSON"), elig_json="{not json")
    _store(repo, "7", elig(scholarship_name="비활성"))
    _store(repo, "8", elig(scholarship_name="검수대기"), status_cd="NEED_REVIEW")
    repo.set_use_yn("EXT01", ["7"], "N")
    got = repo.list_candidates(TODAY)
    assert [c["src_id"] for c in got] == ["5", "1", "3", "8"] or sorted(c["src_id"] for c in got) == ["1", "3", "5", "8"]
    assert got[0]["apply_end"] == date(2026, 10, 6) and isinstance(got[0]["elig"], dict) and got[0]["status"] in ("EXTRACTED", "NEED_REVIEW")


class FakeDescCursor:
    def __init__(self, conn):
        self.conn, self.description = conn, None

    def execute(self, sql, binds=None, **kw):
        self.conn.executed.append((sql, binds))
        self.description = [(c,) for c in self.conn.cols]

    def fetchall(self):
        return self.conn.rows

    def close(self):
        pass


class FakeDescConn:
    def __init__(self, cols, rows):
        self.cols, self.rows, self.executed = cols, rows, []

    def cursor(self):
        return FakeDescCursor(self)


def test_oracle_repository_candidates_use_sql_file_and_today_bind(tmp_path):
    e = json.dumps(elig(scholarship_name="DB 장학"), ensure_ascii=False)
    cols = ["SRC_CD", "SRC_ID", "TITLE", "ORIGIN_URL", "POSTED_DATE", "APPLY_END", "ELIG_JSON", "STATUS_CD", "UNCERTAIN_JSON"]
    rows = [("EXT01", 20, "나중 마감", "https://x", datetime(2026, 9, 1, 10, 0), datetime(2026, 10, 30, 0, 0), e, "EXTRACTED", '{"uncertain": []}'),
            ("EXT01", 10, "먼저 마감", None, None, datetime(2026, 10, 10, 0, 0), e, "NEED_REVIEW", None),
            ("EXT01", 30, "깨짐", None, None, datetime(2026, 10, 11, 0, 0), "{bad", "EXTRACTED", None)]
    conn = FakeDescConn(cols, rows)
    out = OracleRepository(conn).list_candidates(TODAY)                       # 기본 config/recommend_query.txt 사용
    sql, binds = conn.executed[0]
    assert "FROM      SCHOLARSHIP_NOTICE" in sql and ":today" in sql and "--" not in sql      # 주석 제거
    assert binds == {"today": TODAY}
    assert [c["src_id"] for c in out] == ["10", "20"]                          # 마감 빠른 순, 깨진 JSON 행은 제외
    assert out[1]["apply_end"] == date(2026, 10, 30) and out[1]["posted_date"] == date(2026, 9, 1) and out[0]["status"] == "NEED_REVIEW"
    custom = tmp_path / "q.txt"
    custom.write_text("SELECT SRC_CD, SRC_ID, TITLE, APPLY_END, ELIG_JSON FROM T", encoding="utf-8")   # :today 를 안 써도 됨
    conn2 = FakeDescConn(["SRC_CD", "SRC_ID", "TITLE", "APPLY_END", "ELIG_JSON"], [rows[1][:3] + (rows[1][5], rows[1][6])])
    assert len(OracleRepository(conn2, candidate_sql_file=custom).list_candidates(TODAY)) == 1 and conn2.executed[0][1] == {}


def test_oracle_candidate_query_validates_required_columns_and_binds(tmp_path):
    from scholarship.common import sql_file
    bad = tmp_path / "bad.txt"
    bad.write_text("SELECT SRC_ID, TITLE FROM T WHERE A = :today", encoding="utf-8")
    with pytest.raises(sql_file.SqlFileError, match="필수 컬럼"):
        OracleRepository(FakeDescConn(["SRC_ID", "TITLE"], []), candidate_sql_file=bad).list_candidates(TODAY)
    evil = tmp_path / "evil.txt"
    evil.write_text("SELECT * FROM T WHERE A = :hack", encoding="utf-8")
    with pytest.raises(sql_file.SqlFileError, match="사용할 수 없는 바인드"):
        OracleRepository(FakeDescConn([], []), candidate_sql_file=evil).list_candidates(TODAY)


def test_build_candidate_handles_bad_json():
    assert build_candidate({"SRC_ID": 1, "ELIG_JSON": "{x"}) is None and build_candidate({"SRC_ID": 1}) is None
    c = build_candidate({"SRC_CD": "EXT01", "SRC_ID": 1, "ELIG_JSON": "{}", "UNCERTAIN_JSON": "깨짐", "APPLY_END": datetime(2026, 10, 1)})
    assert c["src_id"] == "1" and c["uncertain"] == {} and c["apply_end"] == date(2026, 10, 1)


def _results_dir(tmp_path):
    for name, src in (("ext01_153381_ai_result.json", "ai_result_153381_v2.json"), ):
        shutil.copy(os.path.join(FIX, src), tmp_path / name)
    return tmp_path


def test_file_source_reads_dump_results(tmp_path):
    d = _results_dir(tmp_path)
    shutil.copy(os.path.join(FIX, "ai_result_153381_v1.json"), d / "ext01_999_ai_result.json")             # 구형식도 읽음
    (d / "ext01_888_ai_result.json").write_text("{not json", encoding="utf-8")
    (d / "memo.txt").write_text("x", encoding="utf-8")
    got = FileSource(d).list_candidates(TODAY)
    byid = {c["src_id"]: c for c in got}
    assert set(byid) == {"153381", "999"} and byid["153381"]["status"] == "NEED_REVIEW"                    # verify 항목 -> 검수 대기로 추정
    assert byid["153381"]["elig"]["schema_version"] == 2 and "schema_version" not in byid["999"]["elig"]
    assert byid["153381"]["apply_end"] == date(2026, 10, 7)
    assert FileSource(d).list_candidates(date(2026, 10, 8)) == []                                          # 마감 지난 공고 제외


# ================================================================== CLI
def _profile_file(tmp_path, name):
    shutil.copy(os.path.join(FIX, "profiles", name), tmp_path / name)
    return str(tmp_path / name)


def test_cli_with_results_dir_text_and_json(tmp_path, capsys):
    import run_recommend
    d = tmp_path / "out"
    d.mkdir()
    _results_dir(d)
    prof = _profile_file(tmp_path, "chuncheon_low_gpa_lowincome.json")
    assert run_recommend.main(["--profile", prof, "--results-dir", str(d), "--today", "2026-10-06", "--format", "list", "--env", str(tmp_path / "none.env")]) == 0
    text = capsys.readouterr().out
    assert "장학금 추천 결과 (기준일 2026-10-06)" in text and "애향-저소득(충족)" in text and "미달 유형:" in text

    assert run_recommend.main(["--profile", prof, "--results-dir", str(d), "--today", "2026-10-06", "--json", "--env", str(tmp_path / "none.env")]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["counts"]["conditional"] == 1 and data["conditional"][0]["src_id"] == "153381"
    assert data["conditional"][0]["matched_variants"][0]["name"] == "애향-저소득"

    (d / "ext01_777_ai_result.json").write_text(json.dumps({"elig": {"category": "OUT", "apply_end": "2026-10-30", "target_levels": ["UNIV"],
        "scholarship_name": "대전 장학", "region": {"residence": ["대전광역시"]}}, "review": {}}, ensure_ascii=False), encoding="utf-8")
    siheung = _profile_file(tmp_path, "siheung_mid_gpa.json")
    assert run_recommend.main(["--profile", siheung, "--results-dir", str(d), "--today", "2026-10-06", "--show-excluded", "--format", "list", "--env", str(tmp_path / "none.env")]) == 0
    text = capsys.readouterr().out
    assert "미달 1" in text and "대전광역시" in text and "대체 요건" in text                                # 대전은 탈락, 봄내(시민 또는 자녀)는 확인 필요


def test_cli_drops_sensitive_profile_keys(tmp_path, capsys):
    import run_recommend
    d = tmp_path / "out"
    d.mkdir()
    _results_dir(d)
    prof = _profile_file(tmp_path, "chuncheon_high_gpa.json")                                              # name/학번 포함
    assert run_recommend.main(["--profile", prof, "--results-dir", str(d), "--today", "2026-10-06", "--env", str(tmp_path / "none.env")]) == 0
    text = capsys.readouterr().out
    assert "받지 않은 항목: name, 학번" in text and "홍길동" not in text and "20260001" not in text


def test_cli_error_paths(tmp_path, capsys):
    import run_recommend
    env = str(tmp_path / "none.env")
    assert run_recommend.main(["--profile", str(tmp_path / "missing.json"), "--results-dir", str(tmp_path), "--env", env]) == 2
    assert "읽을 수 없습니다" in capsys.readouterr().err
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"level": "외계인"}), encoding="utf-8")
    assert run_recommend.main(["--profile", str(bad), "--results-dir", str(tmp_path), "--env", env]) == 2
    assert "프로필 오류" in capsys.readouterr().err
    ok = tmp_path / "ok.json"
    ok.write_text("{}", encoding="utf-8")
    assert run_recommend.main(["--profile", str(ok), "--results-dir", str(tmp_path), "--today", "2026/10/06", "--env", env]) == 2


def test_defer_keys_setting_via_service_and_env(monkeypatch):
    e = elig(scholarship_name="대전 장학", region={"residence": ["대전광역시"]})
    prof = {"level": "UNIV", "status": "ENROLLED", "residence_sido": "경기도", "residence_sigungu": "시흥시"}
    assert run([cand(1, e)], profile=prof)["counts"]["no_match"] == 1
    s = Settings()
    s.recommend_defer_keys = ("region",)
    r = run([cand(1, e)], profile=prof, settings=s)
    assert r["counts"]["conditional"] == 1 and any("AI 판단 예정" in m for m in r["conditional"][0]["to_confirm"])
    s.recommend_defer_keys = ("regoin",)                                                              # 오타는 조용히 무시하지 않고 알림
    with pytest.raises(ProfileError, match="RECOMMEND_DEFER_KEYS"):
        run([cand(1, e)], profile=prof, settings=s)
    monkeypatch.setenv("RECOMMEND_DEFER_KEYS", " Region , GPA ,")
    assert Settings.from_env().recommend_defer_keys == ("region", "gpa")
    monkeypatch.delenv("RECOMMEND_DEFER_KEYS")
    assert Settings.from_env().recommend_defer_keys == ()


@pytest.mark.parametrize("title, closed", [
    ("[마감]2026학년도 2학기 어학우수장학금 신청 안내", True), ("[마감] 장학금", True), ("【접수마감】 장학", True), ("(모집 마감) 장학", True),
    ("2026학년도 2학기 어학우수장학금 신청 안내", False), ("마감 임박! 장학금 신청 안내", False), ("장학금 신청 안내 [마감]", False),
    ("[마감일 연장] 장학금", False),
])
def test_notice_marked_closed_in_the_title_is_not_recommended(title, closed):
    from scholarship.recommend.service import is_marked_closed
    assert is_marked_closed({"title": title}) is closed


def test_marked_closed_notice_is_dropped_even_when_the_deadline_is_still_ahead():
    ok, gone = cand(1, elig(scholarship_name="열린 장학")), cand(2, elig(scholarship_name="닫힌 장학"), title="[마감]닫힌 장학")
    ids = [x["src_id"] for x in run([ok, gone])["conditional"] + run([ok, gone])["recommended"]]
    assert "1" in ids and "2" not in ids
