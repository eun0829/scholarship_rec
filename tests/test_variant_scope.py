# -*- coding: utf-8 -*-
"""'유형이름: 조건' 문장을 해당 유형의 조건으로 옮기는 공용 규칙 (저장할 때와 판정할 때 같은 함수)."""
import glob
import json
import pathlib

import pytest

from scholarship.common.ai.schema import normalize
from scholarship.common.variant_scope import attach_prefixed_conditions as A
from scholarship.recommend import matcher as M
from scholarship.recommend.profile import build_profile

ROOT = pathlib.Path(__file__).parent.parent


def prof(**kw):
    d = {"level": "UNIV", "gpa_scale": 4.5, "status": "ENROLLED", "grade": 3, "semesters_completed": 5, "gpa_last": 3.4, "credits_last": 18, "income_bracket": 5}
    d.update(kw)
    return build_profile(d).profile


def elig(common, variants):
    return {"schema_version": 3, "target_levels": ["UNIV"], "other_conditions": common, "variants": variants}


def test_prefixed_sentence_moves_to_the_matching_variant_only():
    out = A(elig(["나비: 자격증 3개 이상 취득자", "공통으로 확인할 말"], [{"name": "나비 장학생"}, {"name": "특기 장학생"}]))
    assert out["other_conditions"] == ["공통으로 확인할 말"]
    assert out["variants"][0]["other_conditions"] == ["자격증 3개 이상 취득자"]      # 앞 이름은 떼고 옮긴다 (유형 이름이 이미 앞에 있다)
    assert not out["variants"][1].get("other_conditions")


def test_sentence_matching_every_variant_or_none_stays_common():
    e = elig(["참고: 서류는 별도 제출", "알수없음: 어떤 조건"], [{"name": "A유형"}, {"name": "B유형"}])
    assert A(e) is e


def test_generic_headings_are_never_treated_as_variant_names():
    e = elig(["대상: 대학생"], [{"name": "대상자 유형"}, {"name": "일반"}])
    assert A(e) is e


def test_one_sentence_goes_to_every_matching_variant_without_duplicates():
    e = elig(["애향: 거주 3년 이상"], [{"name": "애향-저소득", "other_conditions": ["거주 3년 이상"]}, {"name": "애향-시정유공"}, {"name": "성적우수"}])
    out = A(e)
    assert out["other_conditions"] == []
    assert out["variants"][0]["other_conditions"] == ["거주 3년 이상"]                 # 이미 있으면 중복하지 않는다
    assert out["variants"][1]["other_conditions"] == ["거주 3년 이상"]
    assert not out["variants"][2].get("other_conditions")


def test_rule_is_idempotent_and_does_not_modify_the_input():
    e = elig(["나비: 자격증 3개 이상"], [{"name": "나비"}, {"name": "특기"}])
    before = json.dumps(e, ensure_ascii=False, sort_keys=True)
    once = A(e)
    assert json.dumps(e, ensure_ascii=False, sort_keys=True) == before
    assert A(once) is once


def test_single_variant_notice_is_left_alone():
    e = elig(["나비: 자격증"], [{"name": "나비"}])
    assert A(e) is e


def test_saving_applies_the_rule_for_v3_only():
    raw = {"category": "OUT", "scholarship_name": "X", "target_levels": ["UNIV"], "other_conditions": ["나비: 자격증 3개 이상"],
           "variants": [{"name": "나비", "target_levels": ["UNIV"]}, {"name": "특기", "target_levels": ["UNIV"]}]}
    v3, _ = normalize(dict(raw), version=3)
    assert v3["other_conditions"] == [] and v3["variants"][0]["other_conditions"] == ["자격증 3개 이상"]
    v2, _ = normalize(dict(raw), version=2)
    assert v2["other_conditions"] == ["나비: 자격증 3개 이상"]


def test_judging_old_saved_rows_uses_the_same_rule():
    e = elig(["나비: 자격증 3개 이상 취득자"], [{"name": "나비", "target_levels": ["UNIV"]}, {"name": "특기", "target_levels": ["UNIV"]}])
    ev = M.evaluate(e, prof())
    by = {v.name: v for v in ev.variants}
    assert by["특기"].verdict == "MATCH"                                               # 나비 유형의 조건이 특기 유형을 조건부로 만들지 않는다
    assert by["나비"].verdict == "CONDITIONAL" and any("자격증" in c.message for c in by["나비"].checks)
    assert ev.verdict == "MATCH"


def test_moving_never_turns_a_fail_into_a_pass_in_the_matching_variant():
    e = elig(["나비: 자격증 3개 이상"], [{"name": "나비", "gpa": {"min": 4.4, "scale": 4.5, "basis": "LAST_SEMESTER"}}, {"name": "특기"}])
    by = {v.name: v for v in M.evaluate(e, prof(gpa_last=3.0)).variants}
    assert by["나비"].verdict == "NO_MATCH"


def _stored():
    files = sorted(set(glob.glob(str(ROOT / "tests/fixtures/**/*.json"), recursive=True) + glob.glob(str(ROOT / "data/v3_matrix/notices/*.json"))))
    for f in files:
        try:
            d = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
        except ValueError:
            continue
        e = d.get("elig") or d.get("data") or d
        if isinstance(e, dict) and isinstance(e.get("variants"), list):
            yield f, e


@pytest.mark.parametrize("path, e", list(_stored()), ids=lambda x: pathlib.Path(x).name if isinstance(x, str) else "")
def test_property_over_every_stored_notice(path, e):
    out = A(e)
    assert A(out) is out                                                                # 몇 번 적용해도 같다
    before = [t for t in e.get("other_conditions") or []]
    after = out.get("other_conditions") or []
    assert set(after) <= set(before)                                                    # 공통에는 줄기만 한다
    assert len(out["variants"]) == len(e["variants"])
    for v0, v1 in zip(e["variants"], out["variants"]):
        assert (v0.get("name") == v1.get("name"))
        assert set(v0.get("other_conditions") or []) <= set(v1.get("other_conditions") or [])    # 유형의 기존 조건은 사라지지 않는다
    moved = [t for t in before if t not in after]
    got = sum(len(v1.get("other_conditions") or []) - len(v0.get("other_conditions") or []) for v0, v1 in zip(e["variants"], out["variants"]))
    assert got >= len(moved) or not moved                                               # 옮긴 문장은 어딘가 유형에 들어갔다 (이미 있던 문장은 중복하지 않는다)
