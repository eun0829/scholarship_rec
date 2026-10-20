# -*- coding: utf-8 -*-
"""조건부 실현 가능성: 규칙 등급, AI 보조(가짜 클라이언트), 정렬, 혜택 필드, 표시."""
import time
from datetime import date
from types import SimpleNamespace

import pytest

from scholarship.common.config import Settings
from scholarship.recommend import feasibility as F
from scholarship.recommend import feasibility_rules as R
from scholarship.recommend import feasibility_ai as FAI
from scholarship.recommend.present import build_cards, format_cards
from scholarship.recommend.service import Recommender

from tests.test_matcher_v3 import v3cand
from tests.test_recommend_service import Stub

RULES = R.load_rules()
ENG = ("ENGINEERING",)


@pytest.mark.parametrize("msg,level", [
    ("확인 필요: 어학 요건 확인 필요 (어학 성적 정보 없음 (필요: TOEIC 700점 이상))", F.NORMAL),
    ("확인 필요: 어학 요건 확인 필요 (어학 성적 정보 없음 (필요: 독일어 B1 이상))", F.HARD),
    ("확인 필요: 어학 요건 확인 필요 (필요: TOEIC 700점 이상 / 독일어 B1 이상)", F.NORMAL),
    ("확인 필요: 기초생활수급자 여부 확인 필요", F.HARD),
    ("제외 조건 확인: 타 장학금 중복수혜 불가", F.NORMAL),
    ("확인 필요: 추천서 제출 가능 여부", F.NORMAL),
])
def test_rule_levels(msg, level):
    assert F.classify_text(msg, RULES, ENG)[0] == level


def test_unruled_text_is_left_for_ai_and_defaults_to_normal():
    assert F.classify_text("확인 필요: 해당 지역 출신 여부", RULES, ()) is None
    assert F.assess_texts(["확인 필요: 해당 지역 출신 여부"], RULES)[0] == F.NORMAL
    got = F.assess_texts(["확인 필요: 해당 지역 출신 여부"], RULES, (), {"해당 지역 출신 여부": (F.HARD, "지역 한정")})
    assert got == (F.HARD, "지역 한정", "ai")


def test_worst_level_wins_and_rule_beats_ai():
    lv, _, src = F.assess_texts(["확인 필요: 어학 요건 확인 필요 (필요: TOEIC 700점 이상)", "확인 필요: 기초생활수급자 여부"], RULES, ENG,
                                {"어학 요건 확인 필요 (필요: TOEIC 700점 이상)": (F.HARD, "x")})
    assert (lv, src) == (F.HARD, "rule")
    assert F.assess_texts(["확인 필요: 어학 요건 확인 필요 (필요: TOEIC 700점 이상)"], RULES, ENG, {"어학 요건 확인 필요 (필요: TOEIC 700점 이상)": (F.HARD, "x")})[0] == F.NORMAL


class FakeClient:
    def __init__(self, text='[{"n":1,"level":"HARD","reason":"지역 한정"}]', exc=None, delay=0):
        self.calls, self.prompts, self.text, self.exc, self.delay = 0, [], text, exc, delay

    def complete(self, system, user, images, max_tokens=None):
        self.calls += 1
        self.prompts.append(user)
        if self.delay:
            time.sleep(self.delay)
        if self.exc:
            raise self.exc
        return SimpleNamespace(text=self.text, input_tokens=1, output_tokens=1)


def test_ai_lookup_caches_and_sends_only_condition_text():
    c = FakeClient()
    ai = FAI.FeasibilityAI(c)
    assert ai.lookup(["해당 지역 출신 여부"]) == {"해당 지역 출신 여부": ("HARD", "지역 한정")}
    assert ai.lookup(["해당 지역 출신 여부"]) == {"해당 지역 출신 여부": ("HARD", "지역 한정")}
    assert c.calls == 1 and c.prompts == ["1. 해당 지역 출신 여부"]


@pytest.mark.parametrize("client", [FakeClient(exc=RuntimeError("boom")), FakeClient(text="not json"), FakeClient(delay=1.0)])
def test_ai_failures_fall_back_silently(client):
    ai = FAI.FeasibilityAI(client, timeout=0.2)
    assert ai.lookup(["해당 지역 출신 여부"]) == {}


def test_build_is_off_without_key_or_when_disabled():
    assert FAI.build(Settings()) is None
    s = Settings()
    s.feasibility_ai = False
    assert FAI.build(s, client=FakeClient()) is None


def lang_cand(src_id, exam, amount=None):
    kw = {"language_requirements": [{"exam": exam[0], "min_score": exam[1], "level": exam[2]}]}
    if amount:
        kw["amount"] = amount
    return v3cand(src_id, **kw)


PROFILE = {"level": "UNIV", "gpa_scale": 4.5}


def test_conditional_sorted_easy_first_with_fields_and_ai_off():
    german = lang_cand(1, ("독일어", None, "B1 이상"))
    toeic = lang_cand(2, ("TOEIC", 700, None), amount="연 200만원")
    r = Recommender(Stub([german, toeic]), Settings(), feasibility_ai=None).recommend(PROFILE, today=date(2026, 10, 6))
    cond = r["conditional"]
    assert [x["src_id"] for x in cond] == ["2", "1"]
    assert [x["feasibility"]["level"] for x in cond] == ["NORMAL", "HARD"]
    assert cond[0]["benefit"]["amount"] == "연 200만원" and cond[0]["amount"] == "연 200만원"


def test_cards_show_benefit_section_and_feasibility_label():
    toeic = lang_cand(2, ("TOEIC", 700, None), amount="연 200만원")
    r = Recommender(Stub([toeic]), Settings(), feasibility_ai=None).recommend(PROFILE, today=date(2026, 10, 6))
    r["conditional"][0]["benefit"]["limits"] = ["등록금 전액(4학기)"]
    text = format_cards(r, width=100)
    assert "혜택" in text and "연 200만원" in text and "제한: 등록금 전액(4학기)" in text
    assert "실현성" not in text and "실현 가능성" not in text
    text.encode("cp949")
    card = build_cards(r)["items"][0]
    assert card["feasibility"] == "NORMAL" and card["benefit"][0] == "연 200만원"


AGRI = "확인 필요: 졸업 후 농업계에 종사하며 농업리더로 성장하겠다는 의지가 있는 학생"


def test_other_domain_notice_is_very_hard_for_an_engineering_school_but_not_for_that_student():
    assert F.classify_text(AGRI, RULES, ENG)[0] == F.VERY_HARD
    assert F.classify_text(AGRI, RULES, ("AGRICULTURE",)) is None                  # 농업계 학생이면 계열 불일치가 아니다 (AI 보조 대상)
    assert F.classify_text(AGRI, RULES, ()) is None                                 # 학교 계열이 비어 있으면 계열 규칙은 꺼짐
    assert F.classify_domain("농촌 지역 출신 우대", RULES, ENG) is None                     # 출신·우대 문구는 계열 대상이 아니다


def test_mere_mention_without_target_words_is_not_a_mismatch():
    assert F.classify_domain("농업 박람회 참가 경험", RULES, ENG) is None


def test_majors_priority_student_over_env_over_file():
    assert F.majors_for(RULES, "MEDICAL", ("NATURAL",)) == ("MEDICAL",)
    assert F.majors_for(RULES, None, ("NATURAL",)) == ("NATURAL",)
    assert F.majors_for(RULES, None, ()) == ("ENGINEERING",)


def test_conditional_item_in_other_domain_sorts_last_and_is_very_hard():
    agri = v3cand(3, unmodeled_conditions=[{"text": "졸업 후 농업계에 종사할 학생", "section": "자격", "source": "본문", "kind": "ELIGIBILITY"}])
    toeic = lang_cand(2, ("TOEIC", 700, None))
    r = Recommender(Stub([agri, toeic]), Settings(), feasibility_ai=None).recommend(PROFILE, today=date(2026, 10, 6))
    assert [x["src_id"] for x in r["conditional"]] == ["2", "3"]
    last = r["conditional"][1]["feasibility"]
    assert last["level"] == "VERY_HARD" and last["label"] == "매우 어려움"


def test_ai_prompt_includes_the_rules_file_and_ai_notes():
    brief = R.brief_for_ai(RULES)
    for needle in ("NORMAL=보통", "공학계", "희소 외국어", "농업계 대상 공고", "[추가 지침]", "VERY_HARD"):
        assert needle in brief
    c = FakeClient()
    ai = FAI.FeasibilityAI(c, school="공학계", salt=RULES.version, brief=brief)
    ai.lookup(["해당 지역 출신 여부"])
    assert c.prompts == ["1. 해당 지역 출신 여부"]
    assert ai.brief == brief and RULES.ai_notes


def test_changing_the_rules_changes_the_ai_cache_key():
    a = FAI.FeasibilityAI(FakeClient(), salt="v1")
    b = FAI.FeasibilityAI(FakeClient(), salt="v2")
    assert a._key("x") != b._key("x")
