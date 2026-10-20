# -*- coding: utf-8 -*-
"""선발 정보(평가 비율·추천자·진행 절차)와 중·고등학생 전용 유형 제거 (실제 공고 춘천 봄내장학 형태)."""
import json
import pathlib

from scholarship.common.ai import schema_v3 as S3
from scholarship.common.ai.prompt_v3 import build_system_prompt_v3
from scholarship.common.ai.schema import normalize
from scholarship.recommend import matcher_v3 as V
from scholarship.recommend import present, service
from scholarship.recommend.profile import build_profile

FIX = pathlib.Path(__file__).parent / "fixtures" / "elig_chuncheon.json"


def elig():
    return json.loads(FIX.read_text(encoding="utf-8"))


def prof(**kw):
    d = {"level": "UNIV", "gpa_scale": 4.5, "status": "ENROLLED", "grade": 3, "semesters_completed": 5, "gpa_last": 3.4, "credits_last": 18,
         "income_bracket": 5, "residence_sido": "강원특별자치도", "residence_sigungu": "춘천시"}
    d.update(kw)
    return build_profile(d).profile


def raw(variants, **top):
    base = {"category": "OUT", "scholarship_name": "봄내", "target_levels": ["HIGH", "UNIV"], "variants": variants}
    base.update(top)
    return base


def test_high_school_only_variant_dropped_before_saving():
    out, issues = normalize(raw([{"name": "성적우수", "target_levels": ["HIGH", "UNIV"]}, {"name": "성적향상", "target_levels": ["HIGH"]},
                                 {"name": "특기"}, {"name": "중등", "target_levels": ["MIDDLE", "HIGH"]}]), version=3)
    assert [v["name"] for v in out["variants"]] == ["성적우수", "특기"]
    assert out["dropped_variants"] == ["성적향상", "중등"]


def test_nothing_recorded_when_no_variant_dropped():
    out, _ = normalize(raw([{"name": "A", "target_levels": ["UNIV"]}, {"name": "B"}]), version=3)
    assert "dropped_variants" not in out


def test_single_remaining_variant_is_not_flagged():
    out, issues = normalize(raw([{"name": "A", "target_levels": ["UNIV"]}, {"name": "B", "target_levels": ["HIGH"]}]), version=3)
    assert [v["name"] for v in out["variants"]] == ["A"]
    assert not any("1개뿐" in i["reason"] for i in issues)


def test_v2_keeps_high_school_variant():
    out, _ = normalize(raw([{"name": "A", "target_levels": ["UNIV"]}, {"name": "B", "target_levels": ["HIGH"]}]), version=2)
    assert [v["name"] for v in out["variants"]] == ["A", "B"]


def test_old_saved_high_school_variant_not_judged():
    ev = V.evaluate_v3(elig(), prof())
    names = [v.name for v in ev.variants]
    assert "성적향상" not in names
    assert "성적우수" in names


def test_evaluation_sum_must_be_100():
    ok = S3.evaluation([{"item": "성적", "percent": 40}, {"item": "생활수준", "percent": 50}, {"item": "봉사", "percent": 10}])
    assert [(e["item"], e["percent"]) for e in ok] == [("성적", 40), ("생활수준", 50), ("봉사", 10)]
    seen = []
    assert S3.evaluation([{"item": "성적", "percent": 70}, {"item": "봉사", "percent": 10}], lambda f, r: seen.append(r)) == []
    assert seen
    assert S3.evaluation("성적 70%") == [] and S3.evaluation(None) == []


def test_evaluation_ignores_bad_rows():
    assert S3.evaluation([{"item": "", "percent": 100}, {"item": "봉사", "percent": 0}, {"item": "봉사", "percent": 100}, {"item": "봉사", "percent": 5}]) == [{"item": "봉사", "percent": 100}]


def test_procedure_cap_and_dedupe():
    steps = [{"name": "서류 접수", "when": "10/7까지"}, {"name": "서류 접수"}, {"name": "서류 심사"}, {"name": "면접", "when": "11월 중"}, {"name": "결과 발표"},
             {"name": "장학증서 수여"}, {"name": "추가1"}, {"name": "추가2"}]
    out = S3.procedure(steps)
    assert len(out) == S3.MAX_PROCEDURE == 6
    assert out[0] == {"name": "서류 접수", "when": "10/7까지"} and "when" not in out[1]
    assert S3.procedure(None) == [] and S3.procedure("접수") == []


def test_normalize_keeps_selection_fields():
    out, _ = normalize(raw([{"name": "성적우수", "evaluation": [{"item": "성적", "percent": 70}, {"item": "생활수준", "percent": 20}, {"item": "봉사", "percent": 10}],
                             "recommender": "총장·학장", "documents": ["수상 증빙"]}, {"name": "특기"}],
                           procedure=[{"name": "접수", "when": "10/7까지"}, {"name": "발표"}], recommender="학교장"), version=3)
    v = out["variants"][0]
    assert v["recommender"] == "총장·학장" and v["evaluation"][0] == {"item": "성적", "percent": 70} and v["documents"] == ["수상 증빙"]
    assert out["procedure"][0]["name"] == "접수" and out["recommender"] == "학교장"
    assert "evaluation" not in out["variants"][1]


def test_prompt_mentions_selection_rules_and_template_keys():
    t = build_system_prompt_v3()
    assert "evaluation" in t and "procedure" in t and "recommender" in t
    assert "variants 에 적지 않습니다" in t
    for k in ("evaluation", "recommender", "procedure"):
        assert k in S3.TEMPLATE_V3


def _mv(name, ev=None, rc=None):
    d = {"name": name, "verdict": "CONDITIONAL", "verdict_label": "조건부"}
    if ev:
        d["evaluation"] = ev
    if rc:
        d["recommender"] = rc
    return d


E40 = [{"item": "성적", "percent": 40}, {"item": "생활수준", "percent": 50}, {"item": "봉사", "percent": 10}]
E70 = [{"item": "성적", "percent": 70}, {"item": "생활수준", "percent": 20}, {"item": "봉사", "percent": 10}]


def test_selection_rows_empty_when_nothing_collected():
    assert present.selection_rows({"matched_variants": [_mv("A")]}) == {}
    assert present.selection_rows({}) == {}


def test_selection_rows_merges_identical_variants_into_one_line():
    it = {"matched_variants": [_mv("A", E40, "읍면동장"), _mv("B", E40, "읍면동장")]}
    assert present.selection_rows(it) == {"method": ["성적 40% · 생활수준 50% · 봉사 10%  |  추천: 읍면동장"]}


def test_selection_rows_splits_different_variants():
    it = {"matched_variants": [_mv("애향", E40, "읍면동장"), _mv("성적우수", E70, "총장·학장"), _mv("특기")]}
    rows = present.selection_rows(it)
    assert rows["method"][0].startswith("애향: 성적 40%") and rows["method"][1].startswith("성적우수: 성적 70%")
    assert len(rows["method"]) == 2                                   # 값이 없는 유형은 줄을 만들지 않는다


def test_selection_rows_common_and_procedure():
    it = {"matched_variants": [], "selection_info": {"evaluation": E40, "procedure": [{"name": "접수", "when": "10/7까지"}, {"name": "서류 심사"}, {"name": "발표", "when": "11월 중"}]}}
    rows = present.selection_rows(it)
    assert rows["method"] == ["성적 40% · 생활수준 50% · 봉사 10%"]
    assert rows["procedure"] == "접수(10/7까지) → 서류 심사 → 발표(11월 중)"


def test_card_and_terminal_show_selection_only_when_present():
    from datetime import date
    from scholarship.common.config import Settings
    from scholarship.recommend.service import Recommender
    from tests.test_matcher_v3 import v3cand
    from tests.test_recommend_service import Stub
    profile = {"level": "UNIV", "gpa_scale": 4.5}
    plain = v3cand(1, unmodeled_conditions=[{"text": "봉사 경험", "section": "자격", "source": "본문", "kind": "ELIGIBILITY"}])
    rich = v3cand(2, unmodeled_conditions=[{"text": "봉사 경험", "section": "자격", "source": "본문", "kind": "ELIGIBILITY"}],
                  evaluation=E40, recommender="읍면동장", procedure=[{"name": "접수", "when": "10/7까지"}, {"name": "발표"}])
    r = Recommender(Stub([plain, rich]), Settings(), feasibility_ai=None).recommend(profile, today=date(2026, 10, 6))
    by = {x["src_id"]: x for x in r["conditional"]}
    assert "selection_info" not in by["1"] and by["2"]["selection_info"]["procedure"][0]["name"] == "접수"
    from scholarship.recommend import result_format as RF
    assert RF.validate(r, step=False) == []
    cards = {c["src_id"]: c for c in present.build_cards(r)["items"]}
    assert "selection" not in cards["1"]
    assert cards["2"]["selection"]["procedure"] == "접수(10/7까지) → 발표"
    out = present.format_cards(r, width=120)
    assert "선발 방식" in out and "진행 절차" in out and out.count("선발 방식") == 1
    out.encode("cp949")


def test_service_item_carries_selection_info():
    e = dict(elig(), evaluation=E40, recommender="재단", procedure=[{"name": "접수"}, {"name": "발표"}])
    assert service._selection_info(e) == {"evaluation": E40, "recommender": "재단", "procedure": [{"name": "접수"}, {"name": "발표"}]}
    assert service._selection_info(elig()) == {}


def test_sentence_prefix_matches_the_detail_part_of_a_grouped_variant_name():
    names = {"애향-통리반장자녀", "애향-시정유공", "애향-환경공원주변", "특기"}
    for pre in ("통리반장 자녀", "시정유공", "환경공원 주변자녀", "특기"):
        assert V._prefix_matches(pre, names), pre
    assert not V._prefix_matches("자원봉사 우수", names)
    assert not V._prefix_matches("A", {"AB"})


def test_failed_grouped_variant_sentence_leaves_the_common_confirms():
    e = elig()
    e["variants"] = [{"name": "애향-시정유공", "target_levels": ["UNIV"]}, {"name": "특기"}]
    e["other_conditions"] = ["시정유공: 시장 포상 후 1년 미경과한 본인 또는 자녀", "특기: 1년 이내 대회 입상자", "공통 문장"]
    out = V.drop_conditions_of_failed_variants(e, ["애향-시정유공"])
    assert out["other_conditions"] == ["특기: 1년 이내 대회 입상자", "공통 문장"]


def test_selection_rows_fold_many_different_variants_into_three_lines_plus_one():
    def ev(a):
        return [{"item": "성적", "percent": a}, {"item": "봉사", "percent": 100 - a}]
    it = {"matched_variants": [_mv("A1", ev(10)), _mv("A2", ev(10)), _mv("B", ev(20)), _mv("C", ev(30)), _mv("D", ev(40)), _mv("E", ev(50))]}
    rows = present.selection_rows(it)["method"]
    assert len(rows) == 4
    assert rows[0].startswith("A1, A2:") and rows[-1] == "그 밖의 2개 유형: 유형마다 다름 (공고문 확인)"


# ---------------------------------------------------------------- 유형별 평가 기준·선발 기준 (criteria)
def test_criteria_is_normalized_for_common_and_each_variant():
    out, _ = normalize(raw([{"name": "A", "criteria": ["면접 70점 이상"]}, {"name": "B"}], criteria=["성적 우수자 우선", "  ", "소득분위 낮은 순", "가" * 80]), version=3)
    assert out["criteria"][:2] == ["성적 우수자 우선", "소득분위 낮은 순"] and len(out["criteria"][2]) <= 40
    assert out["variants"][0]["criteria"] == ["면접 70점 이상"] and "criteria" not in out["variants"][1]
    assert "criteria" in S3.TEMPLATE_V3 and "criteria" in build_system_prompt_v3()


def test_variant_table_rows_carry_evaluation_and_criteria_and_selection_drops_duplicates():
    P = present
    it = {"matched_variants": [{"name": "A", "verdict": "MATCH", "verdict_label": "충족", "to_confirm": [], "evaluation": E40, "recommender": "총장", "criteria": ["성적 우수자 우선"]},
                               {"name": "B", "verdict": "CONDITIONAL", "verdict_label": "조건부", "to_confirm": ["x"]}],
          "unmatched_variants": [{"name": "C", "reason": "성적 미달", "criteria": ["면접 70점 이상"]}], "selection_info": {"criteria": ["서류 심사 후 면접"]}}
    rows = P.variant_rows(it)
    assert rows[0]["evaluation"] == "성적 40% · 생활수준 50% · 봉사 10%" and rows[0]["criteria"] == "추천: 총장 · 성적 우수자 우선"
    assert rows[1]["evaluation"] == "" and rows[2]["criteria"] == "면접 70점 이상"
    assert P.selection_rows(it, per_variant=False).get("method") is None and P.selection_rows(it, per_variant=False)["criteria"] == "서류 심사 후 면접"
    assert P.selection_rows(it)["method"]                                                         # 표가 없는 화면(터미널)은 기존처럼 유형 묶음 줄
