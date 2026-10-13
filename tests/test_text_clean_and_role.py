# -*- coding: utf-8 -*-
import pytest

from scholarship.common import file_role
from scholarship.common.text_clean import clean_html


def test_clean_text_type(fixture_dir):
    raw = (fixture_dir / "clob_text_type.html").read_text(encoding="utf-8")
    text, imgs = clean_html(raw)
    assert len(raw) > 40000 and len(text) < 3000          # 스타일 덩어리 제거
    assert "2026. 09. 04.(금) 16:00 ~ 2026. 10. 25.(일) 23:59" in text   # 신청기간이 끊기지 않음
    assert "소득분위 8분위 이내" in text and "[표]" in text
    assert imgs == []


def test_clean_image_only(fixture_dir):
    raw = (fixture_dir / "clob_image_only.html").read_text(encoding="utf-8")
    text, imgs = clean_html(raw)
    assert imgs == ["/CrossEditor/binary/images/000342/포스터.jpg"]
    assert "첨부파일" in text and len(text) < 100


def test_clean_entities_and_strike():
    text, _ = clean_html("<p>&quot;학자금&quot; <s>폐지</s></p>")
    assert '"학자금"' in text and "[취소선:폐지]" in text


@pytest.mark.parametrize("name,expected", [
    ("붙임2-1_26년_하반기_인천인희망드림_장학생_공고문_.hwp", file_role.GUIDE),
    ("2026년_상반기분_통영시_대학생_학자금_이자_지원_공고_안_.hwp", file_role.GUIDE),
    ("공고문 및 지원서.hwp", file_role.GUIDE),                 # 요강 우선
    ("_서식___仁松_일반장학생__선발_지원서_2026_2.hwp", file_role.FORM),
    ("학습계획서 양식.hwp", file_role.FORM),
    ("장학금 지원 신청서.hwp", file_role.FORM),
    ("개인정보 수집이용 동의서.pdf", file_role.FORM),
    ("합격자 명단.xlsx", file_role.EXCLUDED),
    ("선정자_발표.pdf", file_role.EXCLUDED),
    ("장학신청 매뉴얼.pdf", file_role.AUX),
    ("붙임1.hwp", file_role.UNKNOWN),
    ("첨부.pdf", file_role.UNKNOWN),
])
def test_file_role(name, expected):
    kw = file_role.load_keywords(None)
    assert file_role.classify(name, kw)[0] == expected
