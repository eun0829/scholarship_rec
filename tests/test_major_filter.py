# -*- coding: utf-8 -*-
"""공학과 무관한 계열(농업·예체능) 전용 공고를 AI 추출 전에 건너뛰는 판정."""
from pathlib import Path

import pytest

from scholarship.common import major_filter as M

CFG = Path(__file__).parent.parent / "config" / "major_exclude.json"
R = M.load_rules(CFG)


@pytest.mark.parametrize("title, body", [
    ("2026 농업계열 대학생 장학생 모집", ""),
    ("예체능 특기자 장학생", ""),
    ("체육 전공 학생 장학", ""),
    ("체육전공학생 장학", ""),
    ("OO재단 장학생", "대상: 음악 전공 학생에 한함"),
    ("2027년 대산장학생 선발", "졸업 후 농업계에 종사하며 농업리더(영농, 농업연구)로 성장하겠다는 학생 ※ 농학계열 재학생 우대"),   # 농업은 우대 표현이 있어도 제외
    ("수산 장학생", ""),
    ("OO재단 장학생", "농업 관련 분야 진출 희망 학생, 영농 후계자"),
])
def test_skips_agriculture_and_arts_only_notices(title, body):
    assert M.skip_reason(title, body, R)


@pytest.mark.parametrize("title, body", [
    ("이공계 새터민 장학", ""),
    ("디자인 예술 계열 장학", ""),                                     # 디자인 계열은 남긴다
    ("건축 디자인 전공 장학", ""),
    ("공학 및 농업 계열 장학", "대상 농업계열 학생"),                    # 공학도 해당
    ("농업 장학", "전공 무관"),
    ("농협재단 장학생", "한국농업협동조합 후원 대학생"),
    ("농어촌 학생 장학", "농어촌 거주 대학생"),                         # 지역·신분이지 계열이 아니다
    ("농업인 자녀 장학", "농업인 자녀 대학생 대상"),
    ("미술관 도슨트 장학", "대학생 대상"),
    ("OO재단 장학생", "전공 무관, 음악 전공 학생도 가능"),
    ("OO재단 장학생", ""),
])
def test_keeps_notices_that_engineering_students_can_apply_to(title, body):
    assert M.skip_reason(title, body, R) is None


def test_body_is_only_checked_near_the_top():
    body = "가" * 900 + " 대상: 음악 전공 학생에 한함"
    assert M.skip_reason("OO재단 장학생", body, R) is None


def test_config_file_loads_and_missing_file_falls_back_to_defaults():
    assert "농업" in R["categories"] and "예체능" in R["categories"] and "디자인" in R["keep"]
    assert M.load_rules(Path("no-such-file.json"))["categories"].keys() == M.DEFAULT_RULES["categories"].keys()


def test_prompt_has_title_condition_rule():
    from scholarship.common.ai.prompt_v3 import build_system_prompt_v3
    t = build_system_prompt_v3()
    assert "제목에 대상이나 자격 조건이 명시" in t and "source \"제목\"" in t and len(t) < 22000
