# -*- coding: utf-8 -*-
"""조건 요약: 코드 대체 묶기, AI 응답 검증, 캐시, 개인정보, 서비스 연결, 화면."""
import json
import time
from datetime import date
from types import SimpleNamespace

import pytest

from scholarship.common.config import Settings
from scholarship.recommend import condition_summary as C
from scholarship.recommend import feasibility_rules as R
from scholarship.recommend import present as P
from scholarship.recommend.service import Recommender
from tests.test_matcher_v3 import v3cand
from tests.test_recommend_service import Stub

RULES = R.load_rules()
VARIANTS = ["애향-통리반장자녀", "애향-시정유공본인·자녀", "애향-환경공원주변자녀", "특기", "주소이전", "원격대학", "세인트존스대학교", "자원봉사 우수"]


def chuncheon_rows():
    rows = [{"cond": "강원도 춘천시 / 안내: 공고일 현재 본인 또는 부모가 춘천시 주민등록 1년 이상", "result": "confirm", "variant": ""},
            {"cond": "직전학기(26년 상반기) 본 재단 장학생 (저소득유형 예외)", "result": "exclude", "variant": ""},
            {"cond": "타 장학금·재단 장학금 간 중복신청 불가", "result": "exclude", "variant": ""},
            {"cond": "등록금 전액 면제자 제외", "result": "confirm", "variant": ""},
            {"cond": "2026년 하반기 타 장학금 수혜 중·고등학생", "result": "exclude", "variant": ""}]
    rows += [{"cond": "[{}] 요건 문장 {}".format(v, i), "result": "confirm", "variant": v} for i, v in enumerate(VARIANTS)]
    for r in rows:
        r["text"] = r["cond"].split("] ", 1)[-1] if r["variant"] else r["cond"]
    return rows


class FakeClient:
    def __init__(self, payload=None, exc=None, delay=0.0):
        self.calls, self.systems, self.users, self.payload, self.exc, self.delay = 0, [], [], payload, exc, delay

    def complete(self, system, user, images, max_tokens=None):
        self.calls += 1
        self.systems.append(system)
        self.users.append(user)
        if self.delay:
            time.sleep(self.delay)
        if self.exc:
            raise self.exc
        text = self.payload(user) if callable(self.payload) else json.dumps(self.payload, ensure_ascii=False)
        return SimpleNamespace(text=text, input_tokens=1, output_tokens=1)


def test_code_summary_folds_variants_and_exclusions_into_few_groups():
    rows = chuncheon_rows()
    groups = C._finish(C.code_summary(rows), rows, RULES)
    assert len(groups) <= C.MAX_GROUPS
    assert sorted(i for g in groups for i in g["rows"]) == list(range(len(rows)))          # 모든 행이 한 그룹에 정확히 한 번
    titles = [g["title"] for g in groups]
    assert any(t.startswith("유형별 요건 (8개)") for t in titles) and any(t.startswith("제외 조건 (3개)") for t in titles)
    assert [g for g in groups if g["title"].startswith("제외 조건")][0]["kind"] == "exclude"


def good_payload(rows):
    return [{"n": 1, "groups": [
        {"title": "지역", "summary": "춘천시 1년 이상 거주(본인 또는 부모)", "quote": "공고일 현재 본인 또는 부모가 춘천시 주민등록 1년 이상", "level": "NORMAL", "rows": [0]},
        {"title": "제외", "summary": "직전 수혜자·중복신청·타 장학 수혜자 제외", "quote": "타 장학금·재단 장학금 간 중복신청 불가", "level": "NORMAL", "rows": [1, 2, 3, 4]},
        {"title": "유형별 요건", "summary": "애향·특기·주소이전 등 8개 유형", "quote": "없는 문장입니다", "level": "HARD", "rows": list(range(5, 13))}]}]


def test_ai_summary_is_validated_and_cached_and_sends_no_student_state():
    rows = chuncheon_rows()
    client = FakeClient(good_payload(rows))
    s = C.ConditionSummarizer(client, RULES)
    out = s.summarize_many([("EXT01:9210", rows)])["EXT01:9210"]
    assert out["source"] == "ai" and len(out["groups"]) == 3
    assert out["groups"][2]["quote"] == rows[5]["text"][:60]                                # 원문에 없는 발췌는 그 행 문장으로 대체
    assert out["groups"][2]["level"] == "HARD" and out["groups"][1]["kind"] == "exclude"
    s.summarize_many([("EXT01:9210", rows)])
    assert client.calls == 1                                                                 # 같은 조건 문장이면 다시 묻지 않는다
    prompt = client.users[0] + client.systems[0]
    assert "내 상태" not in prompt and "직접 확인" not in prompt and "[애향-통리반장자녀]" in prompt and "[요약 지침]" in prompt


def test_missing_rows_become_an_extra_group_and_bad_indexes_are_dropped():
    rows = chuncheon_rows()
    payload = [{"n": 1, "groups": [{"title": "지역", "summary": "s", "quote": "", "level": "NORMAL", "rows": [0, 0, 99, True]}]}]
    out = C.ConditionSummarizer(FakeClient(payload), RULES).summarize_many([("a", rows)])["a"]
    assert out["source"] == "ai" and out["groups"][0]["rows"] == [0]
    assert sorted(i for g in out["groups"] for i in g["rows"]) == list(range(len(rows)))
    assert out["groups"][-1]["title"].startswith("기타 조건")


@pytest.mark.parametrize("client", [None, FakeClient(exc=RuntimeError("x")), FakeClient(payload=lambda u: "not json"), FakeClient(payload={"n": 1}), FakeClient(delay=1.0)])
def test_ai_failures_fall_back_to_code_summary(client):
    rows = chuncheon_rows()
    out = C.ConditionSummarizer(client, RULES, timeout=0.2).summarize_many([("a", rows)])["a"]
    assert out["source"] == "rule" and 1 <= len(out["groups"]) <= C.MAX_GROUPS


def test_rule_level_overrides_ai_level_and_follows_student_major():
    rows = [{"cond": "졸업 후 농업계에 종사할 학생", "text": "졸업 후 농업계에 종사할 학생", "result": "confirm", "variant": ""}] * 4
    payload = [{"n": 1, "groups": [{"title": "농업", "summary": "농업계 종사", "quote": "농업계에 종사", "level": "NORMAL", "rows": [0, 1, 2, 3]}]}]
    s = C.ConditionSummarizer(FakeClient(payload), RULES)
    assert s.summarize_many([("a", rows)], ("ENGINEERING",))["a"]["groups"][0]["level"] == "VERY_HARD"
    assert s.summarize_many([("a", rows)], ("AGRICULTURE",))["a"]["groups"][0]["level"] == "NORMAL"    # 캐시를 쓰더라도 학생 계열에 맞게 다시 적용


def test_cache_is_new_when_rules_change(tmp_path):
    rows = chuncheon_rows()
    a = C.ConditionSummarizer(FakeClient(), RULES)
    d = json.loads(R.DEFAULT_FILE.read_text(encoding="utf-8"))
    d["요약_지침"].append("새 지침")
    p = tmp_path / "r.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    b = C.ConditionSummarizer(FakeClient(), R.load_rules(str(p)))
    assert a._key(rows) != b._key(rows) and b.rules.summary_notes[-1] == "새 지침"


def conds(n):
    return [{"text": "조건 문장 {} 학생".format(i), "section": "자격", "source": "본문", "kind": "ELIGIBILITY"} for i in range(n)]


PROFILE = {"level": "UNIV", "gpa_scale": 4.5}


def test_service_attaches_summary_only_when_rows_reach_the_threshold():
    many, few = v3cand(1, unmodeled_conditions=conds(5)), v3cand(2, unmodeled_conditions=conds(2))
    r = Recommender(Stub([many, few]), Settings(), feasibility_ai=None, condition_summarizer=C.ConditionSummarizer(None, RULES)).recommend(PROFILE, today=date(2026, 10, 6))
    by = {x["src_id"]: x for x in r["conditional"]}
    assert by["1"]["condition_summary"]["source"] == "rule" and "condition_summary" not in by["2"]
    from scholarship.recommend import result_format as RF
    assert RF.validate(r, step=False) == []


def test_cards_show_summary_with_quotes_and_fold_the_raw_table():
    many = v3cand(1, unmodeled_conditions=conds(5))
    r = Recommender(Stub([many]), Settings(), feasibility_ai=None, condition_summarizer=C.ConditionSummarizer(None, RULES)).recommend(PROFILE, today=date(2026, 10, 6))
    out = P.format_cards(r, width=120)
    assert "▷ " in out and "--show-raw" in out and "+--" not in out[out.index("  조건 "):out.index("  확인사항")]
    raw = P.format_cards(r, width=120, show_raw=True)
    assert "내 상태" in raw and "▷ " not in raw
    out.encode("cp949")
    card = P.build_cards(r)["items"][0]
    assert card["condition_groups"] and card["condition_groups"][0]["count"] >= 1 and card["condition_source"] == "rule"


def test_stale_summary_indexes_are_ignored_by_the_display():
    it = {"to_confirm": ["확인 필요: a"], "condition_summary": {"source": "ai", "groups": [{"title": "t", "summary": "s", "quote": "", "level": "NORMAL", "kind": "confirm", "rows": [7]}]}}
    assert P.summary_groups(it, P.condition_table(it, P.confirm_groups(it))["open"]) == []
