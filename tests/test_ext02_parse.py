# -*- coding: utf-8 -*-
"""드림스폰 화면 해석 (실제 목록/상세 HTML 샘플 기준. 샘플의 로그인 계정 이메일은 user@example.com 으로 치환됨)."""
import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from scholarship.common.text_clean import clean_html
from scholarship.sources import ext02_parse as P

FIX = Path(__file__).parent / "fixtures"
LIST_HTML = (FIX / "dreamspon_list.html").read_text(encoding="utf-8")
DETAIL_HTML = (FIX / "dreamspon_detail.html").read_text(encoding="utf-8")
TODAY = date(2026, 10, 7)


# ================================================================== 목록
def test_parse_real_list():
    rows = P.parse_list(LIST_HTML)
    assert len(rows) == 15 and P.parse_max_page(LIST_HTML) == 11
    first = rows[0]
    assert (first.idx, first.org, first.dday, first.state, first.views) == ("9209", "마포인재육성장학재단", 0, "마감임박", 272)
    assert first.title == "마포구 인재육성장학 (2026년 하반기)" and first.tags == ["장학금", "중학생", "고등학생"]
    assert first.kind == "장학금" and first.audiences == ["중학생", "고등학생"]
    r = {x.idx: x for x in rows}["9218"]
    assert (r.dday, r.tags, r.org) == (1, ["장학금", "대학생", "지역기준"], "윤영문화재단")
    assert Counter(x.kind for x in rows) == {"장학금": 12, "기타지원": 1, "대출지원": 1, "연수지원": 1}
    assert {x.state for x in rows} == {"마감임박", "모집중"} and rows[-1].dday == 7 and rows[-1].state == "모집중"
    assert [x.idx for x in rows if "대학생" in x.audiences and x.kind == "장학금"] == ["9210", "9211", "9212", "9213", "9217", "9218"]


@pytest.mark.parametrize("text,expected", [("D-0", 0), ("D-12", 12), (" D - 7 ", 7), ("D+3", -3), ("마감", None), ("", None), (None, None)])
def test_parse_dday(text, expected):
    assert P.parse_dday(text) == expected


def test_parse_list_ignores_rows_without_link_and_empty_pages():
    html = ("<table><tbody><tr><td>검색 결과가 없습니다</td></tr>"
            "<tr><td class='td_subject'><p class='title'><a href='/x.html'>링크에 idx 없음</a></p></td></tr></tbody></table>")
    assert P.parse_list(html) == [] and P.parse_list("") == [] and P.parse_max_page("<p>없음</p>") == 1


# ================================================================== 신청기간
@pytest.mark.parametrize("text,kind,start,end", [
    ("2026. 09. 28. ~ 2026. 10. 08.", "RANGE", date(2026, 9, 28), date(2026, 10, 8)),
    ("2026. 09. 28. (월) ~ 2026. 10. 08. (목)", "RANGE", date(2026, 9, 28), date(2026, 10, 8)),
    ("2026.9.28 ~ 2026.10.8", "RANGE", date(2026, 9, 28), date(2026, 10, 8)),
    ("2026-09-28 ~ 2026-10-08", "RANGE", date(2026, 9, 28), date(2026, 10, 8)),
    ("2026년 9월 28일 ~ 2026년 10월 8일", "RANGE", date(2026, 9, 28), date(2026, 10, 8)),
    ("~ 2026. 10. 08.", "END_ONLY", None, date(2026, 10, 8)),
    ("2026. 10. 08.까지", "END_ONLY", None, date(2026, 10, 8)),
])
def test_parse_period_usable(text, kind, start, end):
    p = P.parse_period(text)
    assert (p.kind, p.start, p.end, p.usable) == (kind, start, end, True) and p.warnings == []


@pytest.mark.parametrize("text,kind", [("상시 모집", "OPEN"), ("예산 소진 시까지", "OPEN"), ("추후 공지", "OPEN"), ("선착순 마감", "OPEN"),
                                       ("", "UNPARSED"), ("신청서 제출", "UNPARSED"), ("10. 08.까지", "UNPARSED")])
def test_parse_period_not_usable(text, kind):
    p = P.parse_period(text)
    assert p.kind == kind and not p.usable and p.start is None and p.end is None


def test_parse_period_warnings():
    assert any("요일 불일치" in w for w in P.parse_period("2026. 09. 28. (화) ~ 2026. 10. 08. (목)").warnings)      # 9/28 은 월요일
    p = P.parse_period("2026. 10. 08.")
    assert p.kind == "END_ONLY" and any("종료일로 가정" in w for w in p.warnings)                                     # 날짜 하나뿐이고 표시가 없음
    p = P.parse_period("2026. 10. 08. ~ 2026. 09. 01.")
    assert p.kind == "UNPARSED" and not p.usable and any("시작일이 종료일보다 늦음" in w for w in p.warnings)
    p = P.parse_period("1차 2026.09.01~09.10 / 2차 2026.10.01 ~ 2026.10.20 / 3차 2026.11.05")
    assert p.kind == "MULTI" and p.start == date(2026, 9, 1) and p.end == date(2026, 11, 5) and any("여러 개" in w for w in p.warnings)
    p = P.parse_period("2026. 13. 40. ~ 2026. 10. 08.")                                                              # 잘못된 날짜는 건너뜀
    assert p.kind == "END_ONLY" and any("올바르지 않음" in w for w in p.warnings)
    assert P.parse_period("2026. 09. 28. ~ 2026. 10. 08.").to_dict("DETAIL") == {
        "raw": "2026. 09. 28. ~ 2026. 10. 08.", "start": "2026-09-28", "end": "2026-10-08", "kind": "RANGE", "source": "DETAIL", "warnings": []}


# ================================================================== 상세 (실제 샘플)
def test_parse_real_detail():
    d = P.parse_detail(DETAIL_HTML)
    assert (d.idx, d.title, d.org_name, d.dday, d.has_content) == ("9218", "윤영문화재단 장학 (2026년 2학기)", "윤영문화재단", 1, True)
    assert d.info == {"장학종류": "장학금", "선발인원": "총 30명 선발", "장학혜택": "장학금지급 : 최대 200만원", "선발대상": "대학생",
                      "신청기간": "2026. 09. 28. (월) ~ 2026. 10. 08. (목)"}
    assert [(a.name, a.filepath.endswith("(지원서).zip")) for a in d.attachments] == [("20261008 윤영문화재단 장학(지원서).zip", True)]
    assert d.attachments[0].filepath.startswith("/webData/") and d.attach_summary == "첨부파일 1개 (204.4KB)"
    assert d.original_url == "https://yoonyoung.org/bbs/board.php?bo_table=notice&wr_id=23"
    assert list(d.sections) == ["period", "headcount", "benefit", "method", "target", "eligibility", "contact"] and d.extra_sections == {}
    assert d.org == {"기관명": "윤영문화재단", "기관분류": "민간재단 , 기타", "전화번호": "031-712-3100",
                     "주소": "경기도 성남시 분당구 두밀로 123번길 57-5, 1층", "홈페이지": "https://yoonyoung.org/"}
    assert d.images == ["/data/scholarship/202609/1790727993mod슬라이드1.png", "/data/scholarship/202609/1790727993mod슬라이드2.png"]
    assert d.sections["period"] == ["2026. 09. 28. ~ 2026. 10. 08."]


def test_real_detail_structured_fields():
    d = P.parse_detail(DETAIL_HTML)
    st = P.structure_sections(d.sections)
    assert st["headcount"] == {"raw": "총 30명", "count": 30}
    assert st["benefit"]["headline"] == "[장학금지급] 최대 200 만원" and st["benefit"]["notes"] == ["※ 중복수혜 : 일부 가능"]
    assert "윤영문화재단 장학생에 선발된 자 선발 제외" in st["benefit"]["lines"]
    m = st["method"]
    assert m["headline"] == "학교 경유 접수" and m["apply_type_hint"] == "SCHOOL" and m["submit_docs"] == "공고문 안내 확인"
    assert m["mail_address"] == "(13544) 경기도 성남시 분당구 두밀로 123번길 57-5, 1층 (대장동) 재단법인 윤영문화재단"       # '제출서류' 줄이 섞이지 않음 (회귀)
    assert st["target"] == {"raw": "대학생", "levels": ["UNIV"], "notes": []}
    el = st["eligibility"]
    assert el["region"] == {"tag": "지역기준", "label": "학교 소재지", "lines": ["서울, 경기, 인천 관내 4년제 대학 재학생"]}
    assert el["gpa"]["lines"] == ["직전학기 12학점 이상 이수 & 평점 3.5/4.5 (3.3/4.3) 이상"] and el["gpa"]["label"] == "대학 학점"
    assert el["recommend"] == {"tag": "추천사항", "label": "지도교수", "lines": []}
    assert el["etc"]["lines"] == ["다양한 문화예술 분야의 인재로 성장할 가능성이 있는 대학생"] and el["etc"]["label"] == ""
    assert st["contact"]["org"] == "윤영문화재단 사무국" and st["contact"]["tel"] == "031-712-3100" and st["contact"]["email"] is None


def test_sections_unknown_heading_missing_and_duplicate():
    html = ("<div id='t_content'><div id='tab1s'><dl><dd>"
            "<p><span><strong>• 신청기간</strong></span></p><p>2026. 10. 01. ~ 2026. 10. 31.</p>"
            "<p><span><strong>• 유의사항</strong></span></p><p>- 허위 기재 시 취소</p>"
            "<p><span><strong>• 선발인원</strong></span></p><p>제한 없음</p>"
            "<p><span><strong>• 선발인원</strong></span></p><p>중복 제목은 무시</p>"
            "<p>제목 앞의 줄은 버려지지 않아야 함</p></dd></dl></div></div>")
    d = P.parse_detail(html)
    assert list(d.sections) == ["period", "headcount"] and d.extra_sections == {"유의사항": ["- 허위 기재 시 취소"]}
    assert d.sections["headcount"] == ["제한 없음"]
    assert P.structure_sections(d.sections)["headcount"] == {"raw": "제한 없음", "count": None}                      # 숫자가 없으면 count 없음
    assert "benefit" not in P.structure_sections(d.sections)                                                         # 없는 항목은 비워 둠


def test_logged_out_detail_has_no_content():
    d = P.parse_detail("<html><head><title>로그인, 드림스폰</title></head><body><form id='loginForm'><input name='mbr_id'></form></body></html>")
    assert d.has_content is False and d.sections == {} and d.attachments == []


@pytest.mark.parametrize("text,expected", [("대학생", ["UNIV"]), ("대학생, 대학원생", ["UNIV", "GRAD"]), ("대학원생", ["GRAD"]),
                                           ("전문대학생", ["UNIV"]), ("고등학생·대학생", ["HIGH", "UNIV"]), ("초등학생", ["ELEM"]),
                                           ("일반인", []), ("", [])])
def test_levels_from_text(text, expected):
    assert P.levels_from_text(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("학교 경유 접수 - 대학은 서류를 재단에 우편 제출", "SCHOOL"), ("학교장 추천 후 재단 제출", "SCHOOL"),
    ("온라인 접수 (재단 홈페이지)", "DIRECT"), ("이메일 제출", "DIRECT"), ("우편 접수", "DIRECT"), ("공고문 참조", None), ("", None)])
def test_classify_apply_type(text, expected):
    assert P.classify_apply_type(text) == expected


@pytest.mark.parametrize("text,count", [("총 30명", 30), ("1,200명 선발", 1200), ("제한 없음", None), ("약 10 명", 10),
                                        ("총 00명", None), ("총 0명 선발", None), ("선발인원 미정/미기재", None), ("", None)])
def test_parse_headcount(text, count):
    assert P.parse_headcount(text) == count


# ================================================================== compose (source_fields, hints)
def test_compose_real_detail():
    d = P.parse_detail(DETAIL_HTML)
    row = {r.idx: r for r in P.parse_list(LIST_HTML)}["9218"]
    fields, hints = P.compose(d, row, TODAY)
    assert hints == {"apply_start": "2026-09-28", "apply_end": "2026-10-08", "period_kind": "RANGE", "period_source": "DETAIL",
                     "period_warnings": [], "dday_end": "2026-10-08", "recruit_count": "30명", "amount": "[장학금지급] 최대 200 만원",
                     "apply_how": "학교 경유 접수 / 재단에서 추천 의뢰한 대학교에서 자체 선발 후 재단에 추천 / 대학은 서류를 재단에 우편 제출",
                     "apply_type": "SCHOOL", "target_levels": ["UNIV"]}
    assert fields["period"]["source"] == "DETAIL" and fields["original_url"].startswith("https://yoonyoung.org/")
    assert fields["list"] == {"tags": ["장학금", "대학생", "지역기준"], "dday": 1, "state": "마감임박", "seen_at": "2026-10-07"}
    assert fields["attachments"] == [{"name": "20261008 윤영문화재단 장학(지원서).zip"}]                              # 서버 내부 경로는 저장하지 않음
    dumped = json.dumps(fields, ensure_ascii=False)
    assert "/webData/" not in dumped and "user@example.com" not in dumped and fields["org"]["기관명"] == "윤영문화재단"


def test_compose_period_fallbacks_and_conflicts():
    base = ("<div id='t_content'><div id='tab1s'><dl><dd><p><span><strong>• 신청기간</strong></span></p>{sec}"
            "</dd></dl></div></div>")
    info = "<div class='basic-info'><ul><li class='col'>신청기간</li><li>{info}</li></ul></div>"
    # 본문이 상시모집이면 정보표의 기간을 사용
    d = P.parse_detail(info.format(info="2026. 09. 01. (화) ~ 2026. 12. 31. (목)") + base.format(sec="<p>상시 모집</p>"))
    f, h = P.compose(d, None, TODAY)
    assert (h["apply_start"], h["apply_end"], h["period_source"]) == ("2026-09-01", "2026-12-31", "INFO_TABLE")
    # 둘 다 있는데 다르면 본문 값 + 경고
    d = P.parse_detail(info.format(info="2026. 09. 01. ~ 2026. 12. 31.") + base.format(sec="<p>2026. 09. 01. ~ 2026. 11. 30.</p>"))
    f, h = P.compose(d, None, TODAY)
    assert h["apply_end"] == "2026-11-30" and h["period_source"] == "DETAIL" and any("두 표기" in w or "다름" in w for w in h["period_warnings"])
    # 기간을 읽을 수 없으면 코드 값 없음 + D-day 는 보조로만 전달
    d = P.parse_detail(base.format(sec="<p>상시 모집</p>"))
    row = P.ListRow(idx="1", title="t", dday=5, state="모집중")
    f, h = P.compose(d, row, TODAY)
    assert h["apply_end"] is None and h["period_kind"] == "OPEN" and h["dday_end"] == "2026-10-12"
    f, h = P.compose(d, P.ListRow(idx="1", title="t", dday=-2), TODAY)
    assert h["dday_end"] is None                                                                              # 이미 지난 D-day 는 쓰지 않음
    f, h = P.compose(P.parse_detail("<div id='t_content'></div>"), None, TODAY)
    assert h["apply_end"] is None and f["period"]["source"] == "NONE" and h["target_levels"] == []


def test_body_html_has_no_personal_data_ads_or_forms():
    d = P.parse_detail(DETAIL_HTML)
    html = P.build_body_html(d)
    text, imgs = clean_html(html)
    assert "user@example.com" not in html and "문의하기" not in text and "오류 제보" not in text and "책임지지 않습니다" not in text
    assert "신청자격" in text and "[지역기준] 학교 소재지" in text and "신청기간 | 2026. 09. 28. (월) ~ 2026. 10. 08. (목)" in text
    assert "기관명 | 윤영문화재단" in text and imgs == []                                                       # 요약 이미지는 기본적으로 넣지 않음
    _, imgs = clean_html(P.build_body_html(d, include_images=True))
    assert imgs == d.images
    assert "user@example.com" not in repr(d)                                                                  # 문의 양식의 이메일은 읽지 않음
