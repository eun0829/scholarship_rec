# -*- coding: utf-8 -*-
"""실제 드림스폰 글 3건(9218, 9227, 9235)의 본문 덤프로 만든 회귀 테스트.
원본 HTML 이 아니라 '고정 7개 항목' 본문(out\\ext02_글번호_body.txt)을 같은 구조의 HTML 로 되돌려 실제 파서에 통과시킨다."""
import re
from datetime import date
from pathlib import Path

import pytest

from scholarship.sources import ext02_parse as P
from tests.fake_dreamspon import detail_html

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 7)


def load(idx, dday=3):
    lines = (FIX / "dreamspon_body_{}.txt".format(idx)).read_text(encoding="utf-8").split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("• 신청기간"))
    end = next(i for i, l in enumerate(lines) if l.startswith("기관 정보"))
    sections, cur = {}, None
    for l in lines[start:end]:
        if l.startswith("• "):
            cur = sections.setdefault(l[2:].strip(), [])
        elif l.strip() and cur is not None:
            cur.append(l)
    info = next(l for l in lines if l.startswith("신청기간 |")).split("|", 1)[1].strip()
    d = P.parse_detail(detail_html(idx, sections=sections, info_period=info))
    return d, P.compose(d, P.ListRow(idx=idx, title="t", dday=dday), TODAY)


def test_9218_unchanged():
    d, (f, h) = load("9218")
    assert (h["recruit_count"], h["amount"], h["apply_type"], h["target_levels"]) == ("30명", "[장학금지급] 최대 200 만원", "SCHOOL", ["UNIV"])
    assert (h["apply_start"], h["apply_end"], h["period_warnings"]) == ("2026-09-28", "2026-10-08", [])


def test_9227_placeholder_headcount_is_not_a_value_and_levels_are_split():
    d, (f, h) = load("9227")
    assert f["headcount"] == {"raw": "총 00명", "count": None} and h["recruit_count"] is None            # '총 00명' 은 빈칸 표기 (이전에는 '0명' 으로 저장됨)
    assert f["target"] == {"raw": "고등학생, 대학생", "levels": ["HIGH", "UNIV"], "notes": []} and h["target_levels"] == ["HIGH", "UNIV"]
    assert h["amount"] == "[장학금지급] 최대 350 만원" and h["apply_type"] == "DIRECT"                     # '방문 / 우편 접수'
    assert (h["apply_start"], h["apply_end"], h["period_warnings"]) == ("2026-09-01", "2026-10-23", [])
    el = f["eligibility"]
    assert set(el) == {"gpa", "income", "근로기준"} and el["근로기준"]["label"] == "근무기간, 특수직업"       # 모르는 이름표도 그대로 보존
    gpa_lines = el["gpa"]["lines"]
    assert "대학생 : 직전학기 성적 평균 2.0 이상" in gpa_lines and "유상교육대상자 : 성적무관" in gpa_lines  # 하위 항목('· ')의 표시는 제거
    assert el["income"]["lines"] == ["2025년도 선원월평균임금 이하"]
    assert any("승무경력" in x for x in el["근로기준"]["lines"])
    assert f["benefit"]["lines"][:2] == ["고등학생 : 120만원", "대학생 : 최대 350만원"] and f["method"]["headline"] == "방문 / 우편 접수"
    assert h["apply_how"].startswith("방문 / 우편 접수") and "부산광역시" not in h["apply_how"]                 # 주소 줄은 apply_how 에 넣지 않음


def test_9235_tag_only_benefit_and_conditions_in_target_section():
    d, (f, h) = load("9235")
    assert h["recruit_count"] is None and f["headcount"]["count"] is None                              # '인원 미정'
    assert f["benefit"]["headline"] == "[기타혜택]" and h["amount"] is None                            # 이름표만 있는 줄은 금액 힌트로 쓰지 않음
    assert "일회성 용돈 (475 유로), 여행경비 (1,735 유로) 지원" in f["benefit"]["lines"]
    assert f["target"]["raw"] == "대학생, 대학원생" and f["target"]["levels"] == ["UNIV", "GRAD"]
    assert f["target"]["notes"] == ["장학금 수혜 시점에 학사 2년 이상을 마쳐야 하며 대학에 등록되어 있어야 함"]    # 자격 조건은 notes 로 분리
    assert f["eligibility"]["어학능력"] == {"tag": "어학능력", "label": "독일어", "lines": ["유럽언어기준 B1 이상의 독일어 능력을 보유한 자"]}
    assert h["apply_type"] == "DIRECT" and h["apply_how"].startswith("온라인 접수 / 링크 : http://www.daad-korea.org")
    assert (h["apply_start"], h["apply_end"], h["period_kind"]) == ("2026-09-16", "2026-10-30", "RANGE") and h["period_warnings"] == []


@pytest.mark.parametrize("idx", ["9218", "9227", "9235"])
def test_real_samples_weekdays_match_and_source_fields_are_json_safe(idx):
    import json
    d, (f, h) = load(idx)
    assert f["period"]["source"] == "DETAIL" and f["period"]["warnings"] == []                          # 본문/정보표 요일(월~금)이 날짜와 모두 일치
    json.dumps(f, ensure_ascii=False)
    assert f["org"]["기관명"] and f["contact"]["tel"]
