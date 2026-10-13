# -*- coding: utf-8 -*-
from datetime import date, datetime

import pytest

from scholarship.sources import ext01
from scholarship.sources.ext01 import Ext01Adapter, Ext01Config


def test_image_url_matches_confirmed_url():
    # 브라우저에서 실제로 열림을 확인한 주소
    url = ext01.resolve_image_url("https://tukorea.ac.kr", "/CrossEditor/binary/images/000342/포스터.jpg")
    assert url == "https://tukorea.ac.kr/CrossEditor/binary/images/000342/%ED%8F%AC%EC%8A%A4%ED%84%B0.jpg"


def test_image_url_blocks_foreign_hosts():
    with pytest.raises(ValueError):
        ext01.resolve_image_url("https://tukorea.ac.kr", "http://evil.example.com/a.jpg")
    with pytest.raises(ValueError):
        ext01.resolve_image_url("https://tukorea.ac.kr", "file:///etc/passwd")
    assert ext01.resolve_image_url("https://tukorea.ac.kr", "https://www.tukorea.ac.kr/a/b.png").endswith("/a/b.png")


def test_date_condition_by_type():
    assert "TO_CHAR" in ext01.date_condition("VARCHAR2")
    assert "TO_CHAR" not in ext01.date_condition("DATE")
    assert "TO_CHAR" not in ext01.date_condition("TIMESTAMP(6)")


@pytest.mark.parametrize("v,exp", [
    (datetime(2026, 10, 1, 9, 30), date(2026, 10, 1)),
    ("20261001", date(2026, 10, 1)),
    ("2026-10-01 09:00:00", date(2026, 10, 1)),
    ("26/10/01", None),                      # 연도 2자리 형식은 해석하지 않음 (오해석 방지)
    (None, None),
])
def test_parse_rgsde(v, exp):
    assert ext01.parse_rgsde(v) == exp


def test_assemble(settings):
    ad = Ext01Adapter(settings, Ext01Config(setup_seq="374"))
    notices = [{"BBS_ARTCL_SEQ": 153666, "SJ": "&quot;학자금&quot; 안내 ", "CN": "<p>본문</p>",
                "RGSDE": datetime(2026, 10, 1), "ANNYMTY_WRITER": "학생지원팀"},
               {"BBS_ARTCL_SEQ": 152953, "SJ": "인천 장학생", "CN": "<p>x</p>", "RGSDE": "20260904"}]   # 작성자 컬럼 없음
    atts = [{"BBS_ATCHMNFL_SEQ": 9002, "BBS_ARTCL_SEQ": 153666, "ORGINL_NM": "b.pdf", "SORT_NO": 2},
            {"BBS_ATCHMNFL_SEQ": 9001, "BBS_ARTCL_SEQ": 153666, "ORGINL_NM": "a.hwp", "SORT_NO": 1},
            {"BBS_ATCHMNFL_SEQ": 9100, "BBS_ARTCL_SEQ": 152953, "ORGINL_NM": "c.hwp"}]                   # SORT_NO 없음
    out = ad.assemble(notices, atts)
    assert out[0].src_id == "153666" and out[0].title == '"학자금" 안내'
    assert [a.org_file_nm for a in out[0].attachments] == ["a.hwp", "b.pdf"]       # sort_no 순
    assert out[0].attachments[0].download_url == "https://www.tukorea.ac.kr/bbs/tukorea/374/9001/download.do"
    assert out[0].origin_url == "https://www.tukorea.ac.kr/bbs/tukorea/374/153666/artclView.do"
    assert out[1].posted_date == date(2026, 9, 4) and out[1].dept_nm == "" and len(out[1].attachments) == 1
