# -*- coding: utf-8 -*-
"""표 출력(추천 결과·확인 도구): 요약/부가정보/원문 링크 표, 마크다운, 폭 제한, 원문 주소."""
import json

import pytest

import run_recommend
import run_v3_match
from scholarship.recommend import matcher as M
from scholarship.recommend.service import detail_table, evaluation_fields, format_table
from scholarship.recommend.table import display_width
from scholarship.sources.ext02_trial import dreamspon_view_url
from tests.test_4b1_support import RICH
from tests.test_ext02_trial import IDEAL, NATIONAL_LIKE
from tests.test_matcher_v3 import SAMPLE, notice, prof, reg, req, run3, v3cand
from tests.test_recommend_service import _profile_file, _results_dir, cand
from tests.test_run_v3_match import NOENV, write_trial

URL1 = "https://www.dreamspon.com/scholarship/view.html?idx=9096"
URL2 = "https://www.dreamspon.com/scholarship/view.html?idx=9212"


@pytest.fixture
def result():
    a = v3cand(1, **RICH)
    a["title"], a["origin_url"] = "거창군 지역출신 대학생 등록금 지원 (2026년 1학기)", URL1
    far = v3cand(2, **reg("ANY", req("RESIDENCE_SELF", "경상남도 거창군")))
    far["title"], far["origin_url"] = "문주장학재단 AI·ICT 인재장학 (2026년 하반기)", URL2
    return run3([a, far])


def lines_of(text):
    return text.split("\n")


def table_lines(text):
    """표의 줄만 (머리말·제목 줄은 제외)"""
    return [l for l in text.split("\n") if l.startswith("+") or l.startswith("|")]


# ================================================================== 추천 결과 표
def test_ascii_tables_have_summary_extra_excluded_and_link_tables(result):
    t = format_table(result, show_excluded=True, width=170)
    L = lines_of(t)
    assert L[0] == "=== 장학금 추천 결과 (기준일 2026-10-06) ===" and L[1].endswith("충족 1 / 조건부 0 / 미달 1")
    assert "[충족] 1건" in L and "[조건부 (확인 필요)] 0건" in L and "[미달] 1건" in L and "[원문 링크]" in L
    assert "| # | 글번호 | 장학금" in t and "| 마감" in t and "| 해당 가능 유형" in t and "| 확인 필요 |" in t
    assert "10-18 (D-12)" in t and "대학생(충족, ~10-07)" in t and "2차 접수(충족, ~09-23 마감 지남)" in t                     # 마감과 유형별 마감
    assert "  선행 조건 · 우대 조건 · 학교 사업 · 확인 사항 (판정에는 영향 없음)" in L
    assert "국가장학금 신청 (확인 필요)" in t and "기초생활수급자 (해당 없음)" in t and "국가장학금 참여대학 (본교 참여 여부 설정 없음)" in t
    assert "제외·중복수혜 제한: 타 장학금 중복 수혜 불가" in t and "| 미달 사유" in t and "지역 조건 미달 (필요: [본인 주민등록 경상남도 거창군])" in t
    tail = t[t.index("[원문 링크]"):]
    assert "| 판정" in tail and "| 글번호 | 원문" in tail and URL1 in tail and URL2 in tail and tail.index(URL1) < tail.index(URL2)    # 충족, 미달 순서
    assert max(display_width(l) for l in table_lines(t)) <= 170


def test_table_without_urls_has_no_link_table_and_empty_extra_table_is_omitted():
    res = run3([cand(5, origin_url=None)])                                                                  # v2 공고: 선행·우대·학교 사업·확인 사항이 없고 주소도 없다
    t = format_table(res, width=100)
    assert "[충족] 1건" in t and "원문 링크" not in t and "선행 조건 ·" not in t
    assert "| 마감" in t                                                                                    # 마감이 있으면 열이 있다
    pseudo = {"counts": {}, "profile_notes": {}, "excluded": [], "conditional": [],
              "recommended": [dict(evaluation_fields(M.evaluate(notice(), prof())), src_id="1", title="기한 없는 공고", url=None, apply_end=None, days_left=None, warnings=[])]}
    assert "| 마감" not in format_table(pseudo, header=["머리말"], width=100)                                   # 마감·경고가 없으면 열을 뺀다


def test_deadline_cell_shows_warnings_and_unmatched_variants_are_listed(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    _results_dir(d)
    from datetime import date
    from scholarship.common.config import Settings
    from scholarship.recommend.file_source import FileSource
    from scholarship.recommend.service import Recommender
    profile = json.loads(open(_profile_file(tmp_path, "chuncheon_low_gpa_lowincome.json"), encoding="utf-8").read())
    res = Recommender(FileSource(str(d)), Settings()).recommend(profile, today=date(2026, 10, 6))
    t = format_table(res, width=130)
    assert "10-07 (D-1)" in t and "마감 임박" in t and "추천서 필요" in t and "검수 대기" in t                      # 마감 열에 경고가 함께 보인다
    flat = " ".join(t.replace("|", " ").split())
    assert "애향-저소득(충족)" in flat and "미달 유형: 애향-통리반장" in flat                                          # 해당 가능 유형과 미달 유형이 같은 칸에 나뉘어 표시된다
    assert "[원문 링크]" in t and "/bbs/tukorea/374/" in t                                                   # 결과 폴더에서도 글번호로 만든 원문 주소가 나온다


def test_width_option_limits_every_line(result):
    for width in (90, 100, 120):                                                                           # 원문 주소 칸은 줄이지 않으므로 그 표가 들어가는 폭부터
        t = format_table(result, show_excluded=True, width=width)
        assert max(display_width(l) for l in table_lines(t)) <= width, width


def test_markdown_style_uses_one_table_per_section_with_link_titles(result):
    t = format_table(result, show_excluded=True, style="md")
    assert "| # | 장학금 | 마감 | 해당 가능 유형 | 확인 필요 | 선행 조건 | 우대 조건 | 학교 사업 | 확인 사항 |" in t
    assert "[거창군 지역출신 대학생 등록금 지원 (2026년 1학기)](" + URL1 + ")" in t and "[문주장학재단 AI·ICT 인재장학 (2026년 하반기)](" + URL2 + ")" in t
    assert "+---" not in t and "원문 링크" not in t and "<br>" in t and "| --- |" in t


def test_header_override_and_profile_notes():
    pseudo = {"counts": {}, "profile_notes": {"assumptions": ["평점 만점 4.5 가정"], "dropped_sensitive_keys": ["이름"]}, "excluded": [], "conditional": [], "recommended": []}
    L = lines_of(format_table(pseudo, header=["=== 내 머리말 ==="]))
    assert L[:3] == ["=== 내 머리말 ===", "※ 개인정보로 보여 받지 않은 항목: 이름", "※ 가정: 평점 만점 4.5 가정"]


# ================================================================== 공고 하나의 상세 표
def test_detail_table_lists_checks_variants_side_info_and_url_outside_the_table():
    n = notice(**dict(NATIONAL_LIKE, apply_end="2026-10-08"))
    ev = M.evaluate(n, prof(school_programs=["NATIONAL_SCHOLARSHIP"], semesters_completed=7, status="ENROLLED", special_complete=True, special_codes=[]))
    item = dict(evaluation_fields(ev), src_id="9195", title="국가장학금 (2026년 2학기 2차)", url=URL1)
    t = detail_table(item, ev, width=120)
    L = lines_of(t)
    assert L[0] == "[조건부] 9195 국가장학금 (2026년 2학기 2차)" and "| 구분" in L[2] and "| 항목" in L[2] and "| 상태" in L[2] and "| 내용" in L[2]
    assert any(l.startswith("| 공통") and "학교급" in l and "충족" in l for l in L)
    assert any("유형: 수급자·차상위" in l and "판정" in l and "미달" in l for l in L) and any("신분" in l and "미해당" in l for l in L)
    assert any(l.startswith("| 학교 사업") and "본교 참여" in l for l in L)
    assert L[-1] == "원문: " + URL1 and L[-2].startswith("+---")                                           # 주소는 표 밖에 한 줄로
    assert max(display_width(l) for l in table_lines(t)) <= 120
    md = detail_table(item, ev, style="md")
    assert "| 구분 | 항목 | 상태 | 내용 |" in md and md.endswith("원문: " + URL1)


def test_detail_table_without_url_has_no_url_line():
    ev = M.evaluate(notice(**IDEAL["9096"]), prof())
    t = detail_table(dict(evaluation_fields(ev), src_id="1", title="x", url=None), ev)
    assert "원문:" not in t and "선행 조건" in t


# ================================================================== 원문 주소
def test_dreamspon_view_url():
    assert dreamspon_view_url("9195") == "https://www.dreamspon.com/scholarship/view.html?idx=9195"
    assert dreamspon_view_url("9195", "http://127.0.0.1:8000/") == "http://127.0.0.1:8000/scholarship/view.html?idx=9195"


# ================================================================== 실행 스크립트
@pytest.fixture
def trial(tmp_path):
    d = tmp_path / "v3_trial"
    write_trial(d, {"9096": ("거창군 지역출신 대학생 등록금 지원 (2026년 1학기)", IDEAL["9096"]), "9212": ("문주장학재단 AI·ICT 인재장학", IDEAL["9212"]),
                    "9195": ("국가장학금 (2026년 2학기 2차)", dict(NATIONAL_LIKE, apply_end="2026-10-08"))})
    return d


@pytest.fixture
def student(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text(SAMPLE.replace("본인 주민등록: 경기도 수원시", "본인 주민등록: 경상남도 거창군"), encoding="utf-8")
    return str(f)


def run_match(capsys, trial, student, *extra):
    assert run_v3_match.main(["--profile-text", student, "--trial-dir", str(trial)] + list(extra) + NOENV) == 0
    return capsys.readouterr()


def test_run_v3_match_default_is_a_table_with_original_urls_built_from_the_post_number(capsys, trial, student):
    cap = run_match(capsys, trial, student)
    out = cap.out
    assert out.startswith("=== v3 공고 3건 판정: 충족 1 / 조건부 1 / 미달 1 ===") and "[충족] 1건" in out and "[조건부 (확인 필요)] 1건" in out and "[미달] 1건" in out
    assert "| 글번호 | 장학금" in out and "| 9096   | 거창군 지역출신 대학생 등록금 지원 (2026년 1학기)" in out and "| 마감" not in out
    tail = out[out.index("[원문 링크]"):]
    assert [u for u in ("9096", "9195", "9212") if "idx=" + u in tail] == ["9096", "9195", "9212"] and tail.count("https://www.dreamspon.com/scholarship/view.html?idx=") == 3
    assert "상세 판정 항목: --detail 글번호 (예: --detail 9096)" in out
    assert "※ 가정" not in out                                                                              # 가정은 표준 오류로


def test_run_v3_match_uses_a_saved_url_and_the_configured_base(capsys, trial, student, monkeypatch):
    f = trial / "9096_v3.json"
    rec = json.loads(f.read_text(encoding="utf-8"))
    rec["url"] = "https://example.org/notice/9096"
    f.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("EXT02_BASE_URL", "http://127.0.0.1:9000")
    tail = run_match(capsys, trial, student).out
    assert "https://example.org/notice/9096" in tail and "http://127.0.0.1:9000/scholarship/view.html?idx=9212" in tail and "dreamspon.com" not in tail


def test_run_v3_match_markdown_and_list_formats(capsys, trial, student):
    md = run_match(capsys, trial, student, "--format", "md").out
    assert "[거창군 지역출신 대학생 등록금 지원 (2026년 1학기)](https://www.dreamspon.com/scholarship/view.html?idx=9096)" in md and "+---" not in md
    old = run_match(capsys, trial, student, "--format", "list").out
    assert "[충족] 9096 거창군 지역출신 대학생 등록금 지원 (2026년 1학기)" in old and "+---" not in old and "원문 링크" not in old


def test_run_v3_match_detail_prints_a_table_and_the_url(capsys, trial, student):
    out = run_match(capsys, trial, student, "--detail", "9195").out
    section = out[out.index("[조건부] 9195"):]
    assert "| 구분" in section and "| 유형: 수급자·차상위" in section and "신분 조건 미해당" in section
    assert section.rstrip().endswith("원문: https://www.dreamspon.com/scholarship/view.html?idx=9195")
    assert "상세 판정 항목:" not in out                                                                       # 상세를 지정하면 안내 문구는 없다


def test_run_v3_match_width_option(capsys, trial, student):
    out = run_match(capsys, trial, student, "--width", "90").out
    assert max(display_width(l) for l in table_lines(out)) <= 90


def test_run_recommend_default_is_a_card_view_and_table_list_md_json_are_available(tmp_path, capsys):
    d = tmp_path / "out"
    d.mkdir()
    _results_dir(d)
    prof_file = _profile_file(tmp_path, "chuncheon_low_gpa_lowincome.json")
    base = ["--profile", prof_file, "--results-dir", str(d), "--today", "2026-10-06", "--env", str(tmp_path / "none.env")]
    assert run_recommend.main(base) == 0
    card = capsys.readouterr().out
    assert "=== 장학금 추천 결과 (기준일 2026-10-06) ===" in card and "[한눈에 보기]" in card and "▶ " in card and "조건부 1/1" in card   # 기본은 건별 카드
    assert "? " not in card and "\n    ! " not in card                                                              # 옛 기호(?, !)는 쓰지 않는다
    assert run_recommend.main(base + ["--format", "table"]) == 0
    table = capsys.readouterr().out
    assert "=== 장학금 추천 결과 (기준일 2026-10-06) ===" in table and "| 글번호 | 장학금" in table and "+====" in table and "해당 가능 유형:" not in table
    assert run_recommend.main(base + ["--format", "md"]) == 0
    assert "| --- |" in capsys.readouterr().out
    assert run_recommend.main(base + ["--format", "list"]) == 0
    assert "해당 가능 유형:" in capsys.readouterr().out
    assert run_recommend.main(base + ["--format", "table", "--width", "75"]) == 0
    out75 = capsys.readouterr().out
    assert max(display_width(l) for l in table_lines(out75[:out75.index("[원문 링크]")])) <= 75                   # 주소 표는 주소를 자르지 않으므로 제외
    assert run_recommend.main(base + ["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["conditional"] == 1


def test_original_urls_stay_whole_even_when_the_width_is_small(result):
    """주소가 표 안에서 줄바꿈되면 터미널에서 열 수 없다: 좁은 폭에서도 주소는 줄이거나 나누지 않는다."""
    t = format_table(result, show_excluded=True, width=70)                                                  # 6열 표의 머리글만으로도 61칸이 필요하므로 그보다 넉넉하게
    assert URL1 in t and URL2 in t
    assert all(display_width(l) <= 70 for l in table_lines(t[:t.index("[원문 링크]")]))                      # 다른 표는 폭을 지킨다
